from __future__ import annotations

import io
import json
import os
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
PLUGIN = ROOT / "plugins/android-engineering-ops"
PLUGIN_VERSION = json.loads((PLUGIN / ".codex-plugin/plugin.json").read_text())["version"]
LIB = PLUGIN / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from android_engineering_ops.install_family import (  # noqa: E402
    InstallFamilyError,
    _installed_inventory,
    _read_active_inventory,
    assert_target_install_family,
)
from android_engineering_ops import install_family  # noqa: E402


def entry(name: str, root: Path = PLUGIN) -> dict[str, object]:
    marketplace = "android-codex-suite"
    return {
        "pluginId": f"{name}@{marketplace}",
        "name": name,
        "marketplaceName": marketplace,
        "version": json.loads((root / ".codex-plugin/plugin.json").read_text())["version"],
        "installed": True,
        "enabled": True,
        "source": {"source": "local", "path": str(root)},
    }


def installed_core(tmp_path: Path) -> tuple[Path, Path, Path, dict[str, object]]:
    home = tmp_path / "codex-home"
    marketplace = "android-codex-suite"
    source = home / ".tmp/marketplaces" / marketplace / "plugins/android-engineering-ops"
    runtime = home / "plugins/cache" / marketplace / "android-engineering-ops" / PLUGIN_VERSION
    for target in (source, runtime):
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(
            PLUGIN,
            target,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    return home, source, runtime, entry("android-engineering-ops", source)


def fake_codex_app_server(path: Path, rows: list[dict[str, object]]) -> None:
    marketplaces: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        source = row.get("source")
        marketplaces.setdefault(str(row["marketplaceName"]), []).append(
            {
                "id": row["pluginId"],
                "name": row["name"],
                "localVersion": row["version"],
                "installed": row["installed"],
                "enabled": row["enabled"],
                "source": (
                    {"type": source.get("source"), "path": source.get("path")}
                    if isinstance(source, dict)
                    else source
                ),
            }
        )
    result = {
        "marketplaces": [
            {"name": name, "plugins": plugins} for name, plugins in marketplaces.items()
        ],
        "marketplaceLoadErrors": [],
    }
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        f"result = {result!r}\n"
        "for line in sys.stdin:\n"
        "    request = json.loads(line)\n"
        "    if request.get('id') == 1:\n"
        "        print(json.dumps({'id': 1, 'result': {}}), flush=True)\n"
        "    elif request.get('id') == 2:\n"
        "        if request.get('params', {}).get('cwds') != [os.getcwd()]:\n"
        "            raise SystemExit(65)\n"
        "        print(json.dumps({'id': 2, 'result': result}), flush=True)\n",
        encoding="utf-8",
    )
    path.chmod(stat.S_IMODE(path.stat().st_mode) | stat.S_IXUSR)


def test_installed_rpc_preserves_cross_market_legacy_and_rejects_partial_or_ambiguous_data(
    tmp_path: Path,
) -> None:
    home, _source, runtime, target = installed_core(tmp_path)
    target_rpc = {
        "id": target["pluginId"],
        "name": target["name"],
        "localVersion": target["version"],
        "source": {"type": "local", "path": target["source"]["path"]},
        "installed": True,
        "enabled": True,
    }
    legacy_rpc = {
        "id": "android-framework-ops@other-market",
        "name": "android-framework-ops",
        "localVersion": "1.0.0",
        "source": {"type": "local", "path": "/legacy"},
        "installed": True,
        "enabled": True,
    }
    result = {
        "marketplaces": [
            {"name": "android-codex-suite", "plugins": [target_rpc]},
            {"name": "other-market", "plugins": [legacy_rpc]},
        ],
        "marketplaceLoadErrors": [],
    }
    inventory = _installed_inventory({"result": result})
    with pytest.raises(InstallFamilyError, match="co-installed"):
        assert_target_install_family(runtime, inventory=inventory, codex_home=home)
    for invalid in (
        {**result, "marketplaceLoadErrors": [{"name": "broken"}]},
        {**result, "marketplaces": result["marketplaces"] * 2},
        {**result, "marketplaces": [{"name": "android-codex-suite", "plugins": [target_rpc]}, {"name": "android-codex-suite", "plugins": [{**legacy_rpc, "id": "android-framework-ops@android-codex-suite"}]}]},
        {**result, "marketplaces": [{"name": "android-codex-suite", "plugins": [{**target_rpc, "enabled": None}]}]},
    ):
        with pytest.raises(InstallFamilyError):
            _installed_inventory({"result": invalid})


@pytest.mark.parametrize("mode", ["success", "timeout", "bad-json", "bad-init"])
def test_one_shot_app_server_always_exits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str,
) -> None:
    codex = tmp_path / "codex"
    pid_file = tmp_path / "child.pid"
    codex.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys, time\n"
        f"open({str(pid_file)!r}, 'w').write(str(os.getpid()))\n"
        f"mode = {mode!r}\n"
        "for line in sys.stdin:\n"
        "    request = json.loads(line)\n"
        "    if request.get('id') == 1:\n"
        "        print(json.dumps({'id': 1, 'result': None if mode == 'bad-init' else {}}), flush=True)\n"
        "    elif request.get('id') == 2:\n"
        "        if request.get('params', {}).get('cwds') != [os.getcwd()]:\n"
        "            raise SystemExit(65)\n"
        "        if mode == 'timeout':\n"
        "            time.sleep(30)\n"
        "        elif mode == 'bad-json':\n"
        "            print('{\"id\":2,\"result\":{},\"result\":{}}', flush=True)\n"
        "        else:\n"
        "            print(json.dumps({'id': 2, 'result': {'marketplaces': [], 'marketplaceLoadErrors': []}}), flush=True)\n",
        encoding="utf-8",
    )
    codex.chmod(stat.S_IMODE(codex.stat().st_mode) | stat.S_IXUSR)
    if mode == "timeout":
        monkeypatch.setattr(install_family, "INVENTORY_TIMEOUT_SECONDS", 0.2)
    started = time.monotonic()
    if mode == "success":
        assert _read_active_inventory(str(codex)) == {"installed": []}
    else:
        with pytest.raises(InstallFamilyError):
            _read_active_inventory(str(codex))
    assert time.monotonic() - started < 3
    pid = int(pid_file.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_one_shot_cleanup_tolerates_child_exit_after_poll(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ExitingProcess:
        stdin = io.BytesIO()
        stdout = io.BytesIO()
        waited = False

        def poll(self) -> None:
            return None

        def terminate(self) -> None:
            raise ProcessLookupError

        def wait(self, timeout: int) -> int:
            self.waited = True
            return 0

    process = ExitingProcess()
    responses = iter((
        {"result": {}},
        {"result": {"marketplaces": [], "marketplaceLoadErrors": []}},
    ))
    monkeypatch.setattr(install_family.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(install_family, "_rpc_response", lambda *args, **kwargs: next(responses))
    assert _read_active_inventory() == {"installed": []}
    assert process.waited


@pytest.mark.skipif(shutil.which("codex") is None, reason="Codex CLI is unavailable")
def test_real_project_plugin_enablement_changes_installed_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "codex-home"
    project = tmp_path / "project"
    neutral = tmp_path / "neutral"
    neutral.mkdir()
    (project / ".git").mkdir(parents=True)
    (project / ".agents/plugins").mkdir(parents=True)
    (project / ".codex").mkdir()
    names = ("android-framework-ops", "android-engineering-ops")
    (project / ".agents/plugins/marketplace.json").write_text(
        json.dumps(
            {
                "name": "project-market",
                "plugins": [
                    {"name": name, "source": {"source": "local", "path": f"./plugins/{name}"}}
                    for name in names
                ],
            }
        ),
        encoding="utf-8",
    )
    for name in names:
        for plugin_root in (
            project / "plugins" / name,
            home / "plugins/cache/project-market" / name / "1.0.0",
        ):
            manifest = plugin_root / ".codex-plugin/plugin.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(json.dumps({"name": name, "version": "1.0.0"}), encoding="utf-8")
    home.mkdir(exist_ok=True)
    (home / "config.toml").write_text(
        '[features]\nplugins = true\nremote_plugin = false\n'
        f'[projects."{project}"]\ntrust_level = "trusted"\n',
        encoding="utf-8",
    )
    project_config = project / ".codex/config.toml"
    project_config.write_text(
        '[plugins."android-framework-ops@project-market"]\nenabled = true\n'
        '[plugins."android-engineering-ops@project-market"]\nenabled = false\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(home))
    # The engineering test fixture shadows `codex` with a protocol fake;
    # this integration check intentionally exercises the real local CLI.
    real_codex = shutil.which(
        "codex", path=os.pathsep.join(os.environ["PATH"].split(os.pathsep)[1:])
    )
    assert real_codex is not None
    baseline = subprocess.run(
        [real_codex, "plugin", "list", "--marketplace", "project-market", "--json"],
        cwd=project,
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert baseline.returncode == 0, baseline.stderr
    in_project = _read_active_inventory(real_codex, cwd=project)
    outside = _read_active_inventory(real_codex, cwd=neutral)
    observed = {row["name"]: row for row in in_project["installed"]}
    assert observed["android-framework-ops"]["enabled"] is True
    assert observed["android-engineering-ops"]["enabled"] is False
    assert observed["android-engineering-ops"]["source"]["path"] == str(
        project / "plugins/android-engineering-ops"
    )
    assert not ({"android-framework-ops", "android-engineering-ops"} & {
        row["name"] for row in outside["installed"]
    })
    project_config.write_text(
        '[plugins."android-framework-ops@project-market"]\nenabled = false\n'
        '[plugins."android-engineering-ops@project-market"]\nenabled = true\n',
        encoding="utf-8",
    )
    swapped = {row["name"]: row for row in _read_active_inventory(real_codex, cwd=project)["installed"]}
    assert swapped["android-framework-ops"]["enabled"] is False
    assert swapped["android-engineering-ops"]["enabled"] is True


def test_target_only_inventory_is_required_and_legacy_coinstall_fails_closed(
    tmp_path: Path,
) -> None:
    home, _source, runtime, target = installed_core(tmp_path)
    assert_target_install_family(
        runtime, inventory={"installed": [target]}, codex_home=home
    )
    for legacy in ("android-framework-ops", "android-wsl-ops", "android-mac-ops"):
        with pytest.raises(InstallFamilyError, match="co-installed"):
            assert_target_install_family(
                runtime,
                inventory={"installed": [target, entry(legacy)]},
                codex_home=home,
            )
    with pytest.raises(InstallFamilyError, match="exactly one active"):
        assert_target_install_family(runtime, inventory={"installed": []}, codex_home=home)
    with pytest.raises(InstallFamilyError, match="inventory-bound runtime cache"):
        assert_target_install_family(
            _source,
            inventory={"installed": [target]},
            codex_home=home,
        )


@pytest.mark.parametrize(
    ("override", "match"),
    [
        ({"pluginId": "wrong@android-codex-suite"}, "pluginId"),
        ({"marketplaceName": "other"}, "marketplaceName must be"),
        ({"version": "2.0.1"}, "inventory identity differs"),
        ({"version": "not-a-version"}, "version is missing or malformed"),
    ],
)
def test_inventory_id_marketplace_and_version_bind_exact_plugin_manifest(
    tmp_path: Path, override: dict[str, object], match: str,
) -> None:
    home, _source, runtime, target = installed_core(tmp_path)
    row = {**target, **override}
    with pytest.raises(InstallFamilyError, match=match):
        assert_target_install_family(
            runtime, inventory={"installed": [row]}, codex_home=home
        )


def test_core_install_family_ignores_unselected_optional_extensions(tmp_path: Path) -> None:
    home, _source, runtime, core = installed_core(tmp_path)
    broken_optional = {
        "pluginId": "jinny-android-practices@somewhere-else",
        "name": "jinny-android-practices",
        "marketplaceName": "somewhere-else",
        "version": "not-even-a-version",
        "installed": True,
        "enabled": True,
        "source": {"source": "local", "path": "/missing"},
    }
    assert_target_install_family(
        runtime,
        inventory={"installed": [core, broken_optional]},
        codex_home=home,
    )


def test_inventory_source_cannot_impersonate_versioned_runtime_cache(
    tmp_path: Path,
) -> None:
    home, source, runtime, row = installed_core(tmp_path)
    with pytest.raises(InstallFamilyError, match="inventory-bound runtime cache"):
        assert_target_install_family(
            source, inventory={"installed": [row]}, codex_home=home
        )

    borrowed = dict(row)
    borrowed["source"] = {"source": "local", "path": str(runtime)}
    with pytest.raises(InstallFamilyError, match="must be different roots"):
        assert_target_install_family(
            runtime, inventory={"installed": [borrowed]}, codex_home=home
        )


def test_real_inventory_source_and_runtime_cache_are_both_hash_bound(
    tmp_path: Path,
) -> None:
    home = tmp_path / "codex-home"
    marketplace = "android-codex-suite"
    source = home / ".tmp/marketplaces" / marketplace / "plugins/android-engineering-ops"
    runtime = home / "plugins/cache" / marketplace / "android-engineering-ops" / PLUGIN_VERSION
    for target in (source, runtime):
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(
            PLUGIN,
            target,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    row = entry("android-engineering-ops", source)
    assert_target_install_family(
        runtime,
        inventory={"installed": [row]},
        codex_home=home,
    )
    (source / "README.md").write_bytes((source / "README.md").read_bytes() + b"\nchanged\n")
    with pytest.raises(InstallFamilyError, match="content hashes differ"):
        assert_target_install_family(
            runtime,
            inventory={"installed": [row]},
            codex_home=home,
        )


def test_project_marketplace_source_must_match_installed_runtime(
    tmp_path: Path,
) -> None:
    home, source, runtime, row = installed_core(tmp_path)
    project_source = tmp_path / "project/plugins/android-engineering-ops"
    project_source.parent.mkdir(parents=True)
    shutil.copytree(source, project_source)
    (project_source / "README.md").write_bytes(
        (project_source / "README.md").read_bytes() + b"\nproject-only change\n"
    )
    project_row = {**row, "source": {"source": "local", "path": str(project_source)}}
    with pytest.raises(InstallFamilyError, match="content hashes differ"):
        assert_target_install_family(
            runtime, inventory={"installed": [project_row]}, codex_home=home,
        )


def test_real_inventory_source_and_runtime_executable_mode_are_hash_bound(
    tmp_path: Path,
) -> None:
    home = tmp_path / "codex-home"
    marketplace = "android-codex-suite"
    source = home / ".tmp/marketplaces" / marketplace / "plugins/android-engineering-ops"
    runtime = home / "plugins/cache" / marketplace / "android-engineering-ops" / PLUGIN_VERSION
    for target in (source, runtime):
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(
            PLUGIN,
            target,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    row = entry("android-engineering-ops", source)
    script = source / "skills/android-source-access/scripts/android_source_access.py"
    executable_mask = stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
    current_mode = stat.S_IMODE(script.stat().st_mode)
    script.chmod(
        current_mode & ~executable_mask
        if current_mode & executable_mask
        else current_mode | stat.S_IXUSR
    )
    with pytest.raises(InstallFamilyError, match="content hashes differ"):
        assert_target_install_family(
            runtime,
            inventory={"installed": [row]},
            codex_home=home,
        )


def test_real_source_access_action_stops_before_adapter_when_family_is_mixed(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "mixed-bin"
    fake_bin.mkdir()
    codex = fake_bin / "codex"
    fake_codex_app_server(codex, [entry("android-engineering-ops"), entry("android-framework-ops")])
    script = PLUGIN / "skills/android-source-access/scripts/android_source_access.py"
    result = subprocess.run(
        [sys.executable, str(script), "run", "inspect-android-sdk.sh"],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}"},
    )
    assert result.returncode == 78
    assert "ANDROID_ENGINEERING_INSTALL_FAMILY_INVALID" in result.stderr
    assert "co-installed" in result.stderr


@pytest.mark.parametrize(
    ("installed", "detail"),
    [
        ("mixed", "co-installed"),
        ("missing", "exactly one active"),
    ],
)
def test_video_frame_writer_stops_before_side_effects_when_family_is_invalid(
    tmp_path: Path, installed: str, detail: str,
) -> None:
    home, _source, runtime, target = installed_core(tmp_path)
    fake_bin = tmp_path / "frame-bin"
    fake_bin.mkdir()
    rows = (
        [target, entry("android-framework-ops")]
        if installed == "mixed"
        else []
    )
    codex = fake_bin / "codex"
    fake_codex_app_server(codex, rows)
    ffmpeg_marker = tmp_path / "ffmpeg-called"
    ffmpeg = fake_bin / "ffmpeg"
    ffmpeg.write_text(
        f"#!/bin/sh\ntouch {ffmpeg_marker}\nexit 0\n", encoding="utf-8"
    )
    ffmpeg.chmod(stat.S_IMODE(ffmpeg.stat().st_mode) | stat.S_IXUSR)
    output = tmp_path / "frames"
    script = runtime / "skills/android-change-workflow/scripts/extract_video_frames.py"

    result = subprocess.run(
        [sys.executable, str(script), str(tmp_path / "missing.mp4"), "--out", str(output)],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={
            **os.environ,
            "CODEX_HOME": str(home),
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        },
    )

    assert result.returncode != 0
    assert "ANDROID_ENGINEERING_INSTALL_FAMILY_INVALID" in result.stderr
    assert detail in result.stderr
    assert not ffmpeg_marker.exists()
    assert not output.exists()


def test_help_and_pure_host_detection_do_not_require_inventory() -> None:
    script = PLUGIN / "skills/android-source-access/scripts/android_source_access.py"
    env = {**os.environ, "PATH": "/nonexistent"}
    help_result = subprocess.run(
        [sys.executable, str(script), "--help"],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    assert help_result.returncode == 0
    detect_result = subprocess.run(
        [sys.executable, str(script), "detect", "--print-field", "host"],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    assert detect_result.returncode == 0
    assert detect_result.stdout.strip() == "wsl"
