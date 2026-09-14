#!/usr/bin/env python3
"""Validate the compact three-plugin topology and orchestration extension contract."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
MEMBER = ROOT / "plugins/akbs-member-ops"
CORE = ROOT / "plugins/android-engineering-ops"
JINNY = ROOT / "plugins/jinny-android-practices"
TOPOLOGY = ROOT / "contracts/plugin-topology/v3/active-topology.json"
MATRIX = ROOT / "contracts/plugin-topology/v3/compatibility-matrix.json"
EXTENSION_SCHEMA = ROOT / "contracts/android-orchestration-extension/v1/extension.schema.json"
PACKAGE_SCHEMA = ROOT / "contracts/incoming/v1/knowledge-incoming-package.schema.json"
PACKAGE_FIXTURE = ROOT / "contracts/incoming/v1/fixtures/patch.manifest.json"
EXPECTED = {
    "akbs-member-ops": {
        "version": "2.1.2",
        "skills": {
            "akbs-member-setup", "akbs-knowledge-search",
            "akbs-knowledge-merge-review", "akbs-daily-report",
            "akbs-weekly-report", "akbs-patch-submit",
        },
    },
    "android-engineering-ops": {
        "version": "3.0.0",
        "skills": {
            "android-change-policy", "android-change-workflow",
            "android-source-access", "android-remote-channel",
            "android-remote-build-deploy", "android-patch-capture",
        },
    },
    "jinny-android-practices": {
        "version": "3.0.0",
        "skills": {"jinny-android-orchestrator", "jinny-android-coding-practices"},
    },
}
REQUIRED_BEHAVIOR = {
    "read", "write", "default", "fallback", "activation",
    "deprecation", "removal", "test",
}
COMPONENT_LAYERS = {
    "application", "platform", "native", "hal", "kernel", "device", "build",
}


class TopologyError(ValueError):
    pass


def load(path: Path) -> dict[str, Any]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise TopologyError(f"duplicate JSON key {key}: {path}")
            value[key] = item
        return value

    try:
        result = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TopologyError(f"invalid JSON: {path}: {exc}") from exc
    if not isinstance(result, dict):
        raise TopologyError(f"JSON root must be an object: {path}")
    return result


def skill_name(skill_file: Path) -> str:
    text = skill_file.read_text(encoding="utf-8")
    match = re.search(r"^name:\s*([a-z0-9][a-z0-9-]*)\s*$", text, re.MULTILINE)
    if match is None:
        raise TopologyError(f"Skill has no valid name: {skill_file}")
    return match.group(1)


def manifest_skills(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8")
    return set(re.findall(r'^name = "([a-z0-9][a-z0-9-]*)"$', text, re.MULTILINE))


def validate_plugins() -> None:
    marketplace = load(ROOT / ".agents/plugins/marketplace.json")
    listed = {row.get("name") for row in marketplace.get("plugins", [])}
    if listed != set(EXPECTED):
        raise TopologyError(f"marketplace plugin set differs: {sorted(listed)}")
    for plugin_id, expected in EXPECTED.items():
        root = ROOT / "plugins" / plugin_id
        plugin = load(root / ".codex-plugin/plugin.json")
        if plugin.get("name") != plugin_id or plugin.get("version") != expected["version"]:
            raise TopologyError(f"plugin identity/version differs: {plugin_id}")
        if plugin.get("skills") != "./skills/":
            raise TopologyError(f"plugin skills root differs: {plugin_id}")
        actual = {path.parent.name for path in (root / "skills").glob("*/SKILL.md")}
        if actual != expected["skills"]:
            raise TopologyError(f"plugin Skill set differs: {plugin_id}: {sorted(actual)}")
        if manifest_skills(ROOT / "manifests" / f"{plugin_id}.toml") != actual:
            raise TopologyError(f"Skill manifest differs: {plugin_id}")
        for name in actual:
            skill_root = root / "skills" / name
            if skill_name(skill_root / "SKILL.md") != name:
                raise TopologyError(f"Skill directory/frontmatter differs: {plugin_id}/{name}")
            agents = skill_root / "agents/openai.yaml"
            if not agents.is_file() or f"${name}" not in agents.read_text(encoding="utf-8"):
                raise TopologyError(f"Skill metadata is missing its default prompt: {plugin_id}/{name}")
            if not (ROOT / "docs/skills" / plugin_id / name / "README.md").is_file():
                raise TopologyError(f"Skill docs missing: {plugin_id}/{name}")
            if (skill_root / "README.md").exists():
                raise TopologyError(f"runtime Skill README is forbidden: {plugin_id}/{name}")


def validate_topology() -> None:
    topology = load(TOPOLOGY)
    if topology.get("schema") != "android-plugin-topology-v3":
        raise TopologyError("active topology schema differs")
    rows = {row.get("id"): row for row in topology.get("plugins", [])}
    if set(rows) != set(EXPECTED):
        raise TopologyError("active topology plugin set differs")
    for plugin_id, expected in EXPECTED.items():
        row = rows[plugin_id]
        if row.get("version") != expected["version"] or set(row.get("skills", [])) != expected["skills"]:
            raise TopologyError(f"active topology row differs: {plugin_id}")
    extension = topology.get("orchestration_extension") or {}
    if (
        extension.get("default_mode") != "none"
        or extension.get("modes") != ["none", "jinny", "custom"]
        or extension.get("selected_skill_runs_in") != "current_user_task"
    ):
        raise TopologyError("orchestration extension defaults differ")

    matrix = load(MATRIX)
    if matrix.get("schema") != "android-plugin-compatibility-matrix-v3":
        raise TopologyError("compatibility matrix schema differs")
    if set(matrix.get("required_behavior_keys", [])) != REQUIRED_BEHAVIOR:
        raise TopologyError("compatibility matrix required keys differ")
    surface_ids: set[str] = set()
    for row in matrix.get("rows", []):
        surface = row.get("surface_id")
        if not isinstance(surface, str) or surface in surface_ids:
            raise TopologyError("compatibility matrix surface identity is invalid")
        surface_ids.add(surface)
        missing = REQUIRED_BEHAVIOR - set(row)
        if missing:
            raise TopologyError(f"compatibility row {surface} misses {sorted(missing)}")


def validate_extension() -> None:
    core_schema = CORE / "contracts/android-orchestration-extension/v1/extension.schema.json"
    if EXTENSION_SCHEMA.read_bytes() != core_schema.read_bytes():
        raise TopologyError("root/core orchestration schemas differ")
    sys.path.insert(0, str(CORE / "lib"))
    from android_engineering_ops.json_contract import validate_document

    extension_path = JINNY / "contracts/android-orchestration-extension/v1/extension.json"
    extension = load(extension_path)
    validate_document(extension, EXTENSION_SCHEMA)
    if extension != {
        "schema": "android-orchestration-extension-v1",
        "provider_id": "jinny-android-practices",
        "provider_version": "3.0.0",
        "compatible_core_contracts": ["android-engineering-orchestration-v1"],
        "orchestrator": {"skill_id": "jinny-android-orchestrator"},
    }:
        raise TopologyError("Jinny extension manifest is not minimal or differs")


def validate_preserved_contracts() -> None:
    member_schema = MEMBER / "internal/incoming-v1/references/knowledge-incoming-package.schema.json"
    if PACKAGE_SCHEMA.read_bytes() != member_schema.read_bytes():
        raise TopologyError("stable v1 package schema copies differ")

    sys.path.insert(0, str(CORE / "lib"))
    from android_engineering_ops.json_contract import validate_document
    from android_engineering_ops.knowledge_rules import VALID_FRAMEWORK_PLATFORMS

    package = load(PACKAGE_FIXTURE)
    validate_document(package, PACKAGE_SCHEMA)
    if package.get("package_kind") != "android_change" or package.get("schema_version") != "1":
        raise TopologyError("Android changes no longer use the stable v1 package")
    components = package.get("components")
    if not isinstance(components, list) or not components:
        raise TopologyError("Android change components are missing")
    patch_paths: list[str] = []
    for row in components:
        if set(row) != {"layer", "patches"} or row["layer"] not in COMPONENT_LAYERS:
            raise TopologyError("Android change component row differs")
        patch_paths.extend(row["patches"])
    if sorted(patch_paths) != sorted(package["files"]["patches"]):
        raise TopologyError("Android change patches are not mapped exactly once")
    if set(VALID_FRAMEWORK_PLATFORMS) != {"mtk", "rk", "unisoc"}:
        raise TopologyError("Android platform tokens changed")


def validate_removed_runtime() -> None:
    forbidden = [
        ROOT / "contracts/android-practices-provider",
        ROOT / "contracts/android-change-workflow",
        ROOT / "contracts/incoming/v2",
        CORE / "contracts/android-practices-provider",
        CORE / "contracts/android-change-workflow",
        CORE / "contracts/incoming/v2",
        CORE / "lib/android_engineering_ops/practices",
        CORE / "lib/android_engineering_ops/workflow",
        CORE / "skills/android-change-workflow/scripts/android_change_controller.py",
        CORE / "skills/android-change-workflow/scripts/resolve_android_practices.py",
        JINNY / "contracts/android-practices-provider",
        JINNY / "lib/jinny_android_practices",
        JINNY / "skills/jinny-android-execution-policy",
        JINNY / "skills/jinny-android-coding-practices/scripts/jinny_coding_policy.py",
    ]
    leftovers = [str(path.relative_to(ROOT)) for path in forbidden if path.exists()]
    if leftovers:
        raise TopologyError(f"retired runtime artifacts remain: {leftovers}")
    stale_tokens = (
        "android-practices-provider-v1", "coding-policy-decision-v1",
        "execution-policy-decision-v1", "jinny-android-execution-policy",
        "resolve_android_practices.py", "android_change_controller.py",
        "Android change v2", "android-change-package-v2",
    )
    active_files = [ROOT / "README.md"]
    for base in (
        CORE, JINNY, ROOT / "docs/skills/android-engineering-ops",
        ROOT / "docs/skills/jinny-android-practices",
    ):
        active_files.extend(path for path in base.rglob("*") if path.is_file())
    for path in active_files:
        if "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        text = path.read_text(encoding="utf-8")
        found = [token for token in stale_tokens if token in text]
        if found:
            raise TopologyError(f"retired runtime text remains in {path.relative_to(ROOT)}: {found}")


def main() -> int:
    try:
        validate_plugins()
        validate_topology()
        validate_extension()
        validate_preserved_contracts()
        validate_removed_runtime()
    except (TopologyError, KeyError, OSError, ValueError) as exc:
        print(f"PLUGIN_TOPOLOGY_INVALID: {exc}", file=sys.stderr)
        return 1
    print("Active plugin topology valid: core-direct plus explicit optional orchestration")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
