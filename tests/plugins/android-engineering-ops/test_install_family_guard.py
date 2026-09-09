from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
PLUGIN = ROOT / "plugins/android-engineering-ops"
LIB = PLUGIN / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from android_engineering_ops.install_family import (  # noqa: E402
    InstallFamilyError,
    assert_target_install_family,
)


def entry(name: str, root: Path = PLUGIN) -> dict[str, object]:
    marketplace = "android-codex-suite"
    return {
        "pluginId": f"{name}@{marketplace}",
        "name": name,
        "marketplaceName": marketplace,
        "version": "2.0.2" if name == "android-engineering-ops" else "2.0.1",
        "installed": True,
        "enabled": True,
        "source": {"source": "local", "path": str(root)},
    }


def installed_core(tmp_path: Path) -> tuple[Path, Path, Path, dict[str, object]]:
    home = tmp_path / "codex-home"
    marketplace = "android-codex-suite"
    source = home / ".tmp/marketplaces" / marketplace / "plugins/android-engineering-ops"
    runtime = home / "plugins/cache" / marketplace / "android-engineering-ops/2.0.2"
    for target in (source, runtime):
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(
            PLUGIN,
            target,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    return home, source, runtime, entry("android-engineering-ops", source)


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


def test_optional_jinny_must_match_core_generation_and_provider_contract(
    tmp_path: Path,
) -> None:
    jinny = ROOT / "plugins/jinny-android-practices"
    home, _core_source, core_runtime, core_row = installed_core(tmp_path)
    marketplace = "android-codex-suite"
    jinny_source = home / ".tmp/marketplaces" / marketplace / "plugins/jinny-android-practices"
    jinny_cache = home / "plugins/cache" / marketplace / "jinny-android-practices/2.0.1"
    for target in (jinny_source, jinny_cache):
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(
            jinny, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
        )
    valid = entry("jinny-android-practices", jinny_source)
    assert_target_install_family(
        core_runtime,
        inventory={"installed": [core_row, valid]},
        codex_home=home,
    )

    old = home / ".tmp/marketplaces" / marketplace / "plugins/old-jinny"
    (old / ".codex-plugin").mkdir(parents=True)
    (old / ".codex-plugin/plugin.json").write_text(
        json.dumps(
            {
                "name": "jinny-android-practices",
                "version": "1.0.3",
                "interface": {"capabilities": ["Interactive", "Read"]},
            }
        ),
        encoding="utf-8",
    )
    old_row = {
        **entry("jinny-android-practices", old),
        "version": "1.0.3",
    }
    old_cache = home / "plugins/cache" / marketplace / "jinny-android-practices/1.0.3"
    old_cache.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(old, old_cache)
    with pytest.raises(InstallFamilyError, match="generation differs"):
        assert_target_install_family(
            core_runtime,
            inventory={"installed": [core_row, old_row]},
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
    runtime = home / "plugins/cache" / marketplace / "android-engineering-ops/2.0.2"
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


def test_real_inventory_source_and_runtime_executable_mode_are_hash_bound(
    tmp_path: Path,
) -> None:
    home = tmp_path / "codex-home"
    marketplace = "android-codex-suite"
    source = home / ".tmp/marketplaces" / marketplace / "plugins/android-engineering-ops"
    runtime = home / "plugins/cache" / marketplace / "android-engineering-ops/2.0.2"
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
    payload = {
        "installed": [
            entry("android-engineering-ops"),
            entry("android-framework-ops"),
        ]
    }
    codex.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        f"print(json.dumps({payload!r}, sort_keys=True))\n",
        encoding="utf-8",
    )
    codex.chmod(stat.S_IMODE(codex.stat().st_mode) | stat.S_IXUSR)
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
    payload = {"installed": rows}
    codex.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        f"print(json.dumps({payload!r}, sort_keys=True))\n",
        encoding="utf-8",
    )
    codex.chmod(stat.S_IMODE(codex.stat().st_mode) | stat.S_IXUSR)
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
