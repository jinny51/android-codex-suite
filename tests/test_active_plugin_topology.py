from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "scripts/validate_active_plugin_topology.py"
CORE_LIB = ROOT / "plugins/android-engineering-ops/lib"
if str(CORE_LIB) not in sys.path:
    sys.path.insert(0, str(CORE_LIB))

from android_engineering_ops.json_contract import (  # noqa: E402
    ContractValidationError,
    validate_instance,
)


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_active_topology_validator_passes() -> None:
    result = subprocess.run(
        [sys.executable, str(VALIDATOR)], text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.fixture
def private_catalog(tmp_path: Path):
    spec = importlib.util.spec_from_file_location("private_topology_validator", VALIDATOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ROOT = tmp_path
    marketplace = load(ROOT / ".agents/plugins/marketplace.json")
    catalog = tmp_path / ".agents/plugins/marketplace.json"
    catalog.parent.mkdir(parents=True)
    catalog.write_text(json.dumps(marketplace), encoding="utf-8")
    for plugin_id, expected in module.EXPECTED.items():
        plugin_root = tmp_path / "plugins" / plugin_id
        metadata = plugin_root / ".codex-plugin/plugin.json"
        metadata.parent.mkdir(parents=True)
        metadata.write_text(json.dumps(load(ROOT / "plugins" / plugin_id / ".codex-plugin/plugin.json")), encoding="utf-8")
        manifest = tmp_path / "manifests" / f"{plugin_id}.toml"
        manifest.parent.mkdir(exist_ok=True)
        manifest.write_text("\n".join(f'name = "{name}"' for name in expected["skills"]), encoding="utf-8")
        for name in expected["skills"]:
            skill_root = plugin_root / "skills" / name
            (skill_root / "agents").mkdir(parents=True)
            (skill_root / "SKILL.md").write_text(f"---\nname: {name}\n---\n", encoding="utf-8")
            (skill_root / "agents/openai.yaml").write_text(f"default_prompt: ${name}\n", encoding="utf-8")
            doc = tmp_path / "docs/skills" / plugin_id / name / "README.md"
            doc.parent.mkdir(parents=True)
            doc.write_text("fixture", encoding="utf-8")
    return module, tmp_path


def _mutate_json(path: Path, mutate) -> None:
    payload = load(path)
    mutate(payload)
    path.write_text(json.dumps(payload), encoding="utf-8")


@pytest.mark.parametrize("field,value", [
    ("description", None), ("description", "   "), ("author", "publisher"),
    ("author", {}), ("author", {"name": []}), ("interface", []),
    ("keywords", "android"), ("keywords", [1]), ("homepage", "http://example.com"),
    ("homepage", "https://user:password@example.com"),
    ("homepage", "https://@example.com"), ("homepage", "https://example.com:invalid"),
])
def test_declared_plugin_metadata_is_validated(private_catalog, field, value) -> None:
    module, root = private_catalog
    path = root / "plugins/akbs-member-ops/.codex-plugin/plugin.json"
    _mutate_json(path, lambda payload: payload.update({field: value}))
    with pytest.raises(module.TopologyError):
        module.validate_plugins()


@pytest.mark.parametrize("field,value", [
    ("displayName", []), ("shortDescription", False), ("capabilities", "Read"),
    ("capabilities", [None]), ("defaultPrompt", [3]), ("brandColor", "blue"),
    ("screenshots", "./assets/screenshot.png"),
    ("logo", "../outside.png"), ("logo", "/tmp/outside.png"),
    ("logo", "./assets/missing.png"),
])
def test_declared_interface_metadata_is_validated(private_catalog, field, value) -> None:
    module, root = private_catalog
    path = root / "plugins/akbs-member-ops/.codex-plugin/plugin.json"
    _mutate_json(path, lambda payload: payload["interface"].update({field: value}))
    with pytest.raises(module.TopologyError):
        module.validate_plugins()


@pytest.mark.parametrize("mutation", [
    "duplicate", "wrong_target", "parent", "absolute", "missing", "source_type", "policy_type",
])
def test_marketplace_entries_are_unique_and_root_bound(private_catalog, mutation) -> None:
    module, root = private_catalog
    catalog = root / ".agents/plugins/marketplace.json"
    payload = load(catalog)
    entry = payload["plugins"][0]
    if mutation == "duplicate":
        payload["plugins"].append(copy.deepcopy(entry))
    elif mutation == "source_type":
        entry["source"] = []
    elif mutation == "policy_type":
        entry["policy"] = "AVAILABLE"
    else:
        entry["source"]["path"] = {
            "wrong_target": "./plugins/android-engineering-ops", "parent": "./../outside",
            "absolute": str(root / "plugins/akbs-member-ops"), "missing": "./plugins/missing",
        }[mutation]
    catalog.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(module.TopologyError):
        module.validate_plugins()


def test_private_suite_does_not_require_public_submission_metadata(private_catalog) -> None:
    module, root = private_catalog
    path = root / "plugins/akbs-member-ops/.codex-plugin/plugin.json"
    _mutate_json(path, lambda payload: payload.update({"interface": {
        "displayName": "Private suite", "shortDescription": "A" * 80,
        "capabilities": [], "defaultPrompt": "Use this private suite",
    }}))
    module.validate_plugins()


def test_declared_assets_must_exist_within_the_plugin(private_catalog) -> None:
    module, root = private_catalog
    plugin = root / "plugins/akbs-member-ops"
    asset = plugin / "assets/logo.svg"
    asset.parent.mkdir()
    asset.write_text("<svg/>", encoding="utf-8")
    path = plugin / ".codex-plugin/plugin.json"
    _mutate_json(path, lambda payload: payload["interface"].update({"logo": "./assets/logo.svg"}))
    module.validate_plugins()


def test_declared_asset_symlink_cannot_escape_the_plugin(private_catalog) -> None:
    module, root = private_catalog
    outside = root / "outside.svg"
    outside.write_text("<svg/>", encoding="utf-8")
    plugin = root / "plugins/akbs-member-ops"
    assets = plugin / "assets"
    assets.mkdir()
    (assets / "logo.svg").symlink_to(outside)
    _mutate_json(plugin / ".codex-plugin/plugin.json",
                 lambda payload: payload["interface"].update({"logo": "./assets/logo.svg"}))
    with pytest.raises(module.TopologyError, match="escapes"):
        module.validate_plugins()


def test_marketplace_can_use_the_documented_local_string_source(private_catalog) -> None:
    module, root = private_catalog
    path = root / ".agents/plugins/marketplace.json"
    _mutate_json(path, lambda payload: payload["plugins"][0].update({"source": "./plugins/akbs-member-ops"}))
    module.validate_plugins()


def test_duplicate_json_keys_are_rejected(private_catalog) -> None:
    module, root = private_catalog
    path = root / "plugins/akbs-member-ops/.codex-plugin/plugin.json"
    path.write_text('{"name":"akbs-member-ops","name":"akbs-member-ops"}', encoding="utf-8")
    with pytest.raises(module.TopologyError, match="duplicate JSON key"):
        module.validate_plugins()


def test_extension_schema_is_deliberately_minimal() -> None:
    schema = load(ROOT / "contracts/android-orchestration-extension/v1/extension.schema.json")
    manifest = load(
        ROOT / "plugins/jinny-android-practices/contracts/android-orchestration-extension/v1/extension.json"
    )
    validate_instance(manifest, schema, schema)
    assert set(manifest) == {
        "schema", "provider_id", "provider_version",
        "compatible_core_contracts", "orchestrator",
    }
    for obsolete in ("roles", "models", "capabilities", "decision_entrypoint", "worker_profiles"):
        invalid = copy.deepcopy(manifest)
        invalid[obsolete] = {}
        with pytest.raises(ContractValidationError):
            validate_instance(invalid, schema, schema)


def test_compatibility_matrix_explains_every_changed_surface() -> None:
    matrix = load(ROOT / "contracts/plugin-topology/v3/compatibility-matrix.json")
    required = set(matrix["required_behavior_keys"])
    rows = {row["surface_id"]: row for row in matrix["rows"]}
    assert {
        "plugin.android-engineering-ops",
        "skill.android-change-policy",
        "plugin.jinny-android-practices",
        "contract.android-orchestration-extension",
        "config.android-engineering.extension",
        "runtime.controller-worker-protocol",
        "contract.knowledge-incoming-package-v2",
    } == set(rows)
    assert all(required.issubset(row) for row in rows.values())


def test_removed_runtime_is_physically_absent() -> None:
    assert not (ROOT / "contracts/android-practices-provider").exists()
    assert not (ROOT / "contracts/android-change-workflow").exists()
    assert (ROOT / "contracts/incoming/v2/knowledge-incoming-package.schema.json").is_file()
    assert not (ROOT / "contracts/incoming/v2/akbs-android-change-package.schema.json").exists()
    assert not (
        ROOT / "plugins/android-engineering-ops/lib/android_engineering_ops/workflow"
    ).exists()
    assert not (
        ROOT / "plugins/android-engineering-ops/skills/android-change-policy"
    ).exists()
    assert (
        ROOT / "plugins/android-engineering-ops/contracts/android-change-policy/v1/policy.json"
    ).is_file()
    assert (
        ROOT / "plugins/android-engineering-ops/lib/android_engineering_ops/policy/patch_markers.py"
    ).is_file()
    assert not (
        ROOT / "plugins/jinny-android-practices/skills/jinny-android-execution-policy"
    ).exists()
