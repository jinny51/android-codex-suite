from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
CORE = ROOT / "plugins/android-engineering-ops"
JINNY = ROOT / "plugins/jinny-android-practices"
LIB = CORE / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from android_engineering_ops.orchestration import (  # noqa: E402
    ExtensionResolutionError,
    resolve_extension,
)


def install_jinny(
    tmp_path: Path,
    *,
    name: str = "jinny-android-practices",
    marketplace: str = "android-codex-suite",
) -> tuple[Path, dict[str, object]]:
    home = tmp_path / "home"
    version = json.loads((JINNY / ".codex-plugin/plugin.json").read_text())["version"]
    source = home / "marketplaces" / marketplace / "plugins" / name
    runtime = home / "plugins/cache" / marketplace / name / version
    for target in (source, runtime):
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(JINNY, target)
        if name != "jinny-android-practices":
            plugin_path = target / ".codex-plugin/plugin.json"
            plugin = json.loads(plugin_path.read_text())
            plugin["name"] = name
            plugin_path.write_text(json.dumps(plugin), encoding="utf-8")
            extension_path = target / "contracts/android-orchestration-extension/v1/extension.json"
            extension = json.loads(extension_path.read_text())
            extension["provider_id"] = name
            extension_path.write_text(json.dumps(extension), encoding="utf-8")
    row = {
        "pluginId": f"{name}@{marketplace}",
        "name": name,
        "marketplaceName": marketplace,
        "version": version,
        "installed": True,
        "enabled": True,
        "source": {"source": "local", "path": str(source)},
    }
    return home, row


def config(project: Path, text: str) -> None:
    path = project / ".codex/android-engineering.toml"
    path.parent.mkdir(parents=True)
    path.write_text(text, encoding="utf-8")


def test_missing_config_is_core_direct_and_does_not_read_inventory(tmp_path: Path) -> None:
    result = resolve_extension(
        tmp_path / "project",
        inventory={"definitely": "not an inventory"},
        codex_home=tmp_path / "home",
    )
    assert result.as_dict() == {
        "source": "core",
        "mode": "none",
        "config_path": None,
        "plugin_id": None,
        "plugin_name": None,
        "plugin_version": None,
        "plugin_root": None,
        "skill_id": None,
        "skill_path": None,
    }


def test_explicit_jinny_resolves_one_real_orchestrator_skill(tmp_path: Path) -> None:
    home, row = install_jinny(tmp_path)
    project = tmp_path / "project"
    config(project, '[extension]\nmode = "jinny"\n')
    result = resolve_extension(
        project, inventory={"installed": [row]}, codex_home=home
    )
    assert result.source == "extension"
    assert result.skill_id == "jinny-android-orchestrator"
    assert result.skill_path and result.skill_path.is_file()


def test_legacy_jinny_pins_are_accepted_but_not_part_of_resolution(tmp_path: Path) -> None:
    home, row = install_jinny(tmp_path)
    project = tmp_path / "project"
    config(
        project,
        '[extension]\nmode = "jinny"\nprovider_version = "2.0.1"\n'
        'provider_manifest_sha256 = "obsolete"\n',
    )
    result = resolve_extension(
        project, inventory={"installed": [row]}, codex_home=home
    )
    assert result.plugin_version == "3.0.1"
    assert "provider" not in result.as_dict()


def test_selected_extension_missing_or_ambiguous_fails_closed(tmp_path: Path) -> None:
    project = tmp_path / "project"
    config(project, '[extension]\nmode = "jinny"\n')
    with pytest.raises(ExtensionResolutionError, match="one active plugin"):
        resolve_extension(
            project, inventory={"installed": []}, codex_home=tmp_path / "home"
        )


def test_custom_mode_selects_any_plugin_implementing_the_same_contract(tmp_path: Path) -> None:
    name = "team-android-orchestrator"
    home, row = install_jinny(tmp_path, name=name, marketplace="team-market")
    project = tmp_path / "project"
    config(project, f'[extension]\nmode = "custom"\nplugin_name = "{name}"\n')
    result = resolve_extension(
        project, inventory={"installed": [row]}, codex_home=home
    )
    assert result.plugin_name == name
    assert result.skill_id == "jinny-android-orchestrator"


def test_selected_extension_source_runtime_substitution_is_rejected(tmp_path: Path) -> None:
    home, row = install_jinny(tmp_path)
    project = tmp_path / "project"
    config(project, '[extension]\nmode = "jinny"\n')
    runtime = home / "plugins/cache/android-codex-suite/jinny-android-practices/3.0.1"
    manifest = runtime / "contracts/android-orchestration-extension/v1/extension.json"
    manifest.write_text(manifest.read_text() + "\n", encoding="utf-8")
    with pytest.raises(ExtensionResolutionError, match="source/runtime extension manifest differs"):
        resolve_extension(project, inventory={"installed": [row]}, codex_home=home)
