"""Resolve one explicitly selected Android orchestration extension.

The core stays fully usable without an extension.  Resolution only identifies the
selected orchestrator Skill; it does not classify work, choose a model, create an
agent, or participate in the Android engineering workflow.
"""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from android_engineering_ops.configuration import (
    EngineeringConfigError,
    parse_engineering_config,
)
from android_engineering_ops.json_contract import (
    ContractValidationError,
    validate_document,
)


PROJECT_CONFIG = Path(".codex/android-engineering.toml")
USER_CONFIG = "android-engineering-ops.toml"
EXTENSION_MANIFEST = Path("contracts/android-orchestration-extension/v1/extension.json")
CORE_CONTRACT = "android-engineering-orchestration-v1"
OFFICIAL_MARKETPLACE = "android-codex-suite"
JINNY_PLUGIN = "jinny-android-practices"
SCHEMA_PATH = (
    Path(__file__).resolve().parents[3]
    / "contracts/android-orchestration-extension/v1/extension.schema.json"
)
SEMVER_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
MARKETPLACE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
SKILL_NAME_RE = re.compile(r"^name:\s*([a-z0-9][a-z0-9-]*)\s*$", re.MULTILINE)


class ExtensionResolutionError(ValueError):
    """The selected extension is ambiguous, unavailable, or malformed."""


@dataclass(frozen=True)
class ExtensionResolution:
    source: str
    mode: str
    config_path: Path | None = None
    plugin_id: str | None = None
    plugin_name: str | None = None
    plugin_version: str | None = None
    plugin_root: Path | None = None
    skill_id: str | None = None
    skill_path: Path | None = None

    def as_dict(self) -> dict[str, str | None]:
        return {
            "source": self.source,
            "mode": self.mode,
            "config_path": str(self.config_path) if self.config_path else None,
            "plugin_id": self.plugin_id,
            "plugin_name": self.plugin_name,
            "plugin_version": self.plugin_version,
            "plugin_root": str(self.plugin_root) if self.plugin_root else None,
            "skill_id": self.skill_id,
            "skill_path": str(self.skill_path) if self.skill_path else None,
        }


def _stable_bytes(path: Path, *, label: str) -> bytes:
    absolute = Path(os.path.abspath(os.fspath(path.expanduser())))
    current = Path(absolute.anchor)
    try:
        for part in absolute.parts[1:]:
            current /= part
            info = current.lstat()
            if stat.S_ISLNK(info.st_mode):
                raise ExtensionResolutionError(f"{label} contains a symlink: {current}")
        before = absolute.stat()
        if not stat.S_ISREG(before.st_mode):
            raise ExtensionResolutionError(f"{label} is not a regular file: {absolute}")
        raw = absolute.read_bytes()
        after = absolute.stat()
    except OSError as exc:
        raise ExtensionResolutionError(f"cannot read {label}: {absolute}: {exc}") from exc
    identity = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns)
    if identity(before) != identity(after):
        raise ExtensionResolutionError(f"{label} changed while being read: {absolute}")
    return raw


def _strict_json(raw: bytes, *, label: str) -> dict[str, Any]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ExtensionResolutionError(f"{label} repeats key: {key}")
            result[key] = value
        return result

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExtensionResolutionError(f"{label} is not strict UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ExtensionResolutionError(f"{label} must be a JSON object")
    return value


def _inventory(codex_executable: str) -> Mapping[str, Any]:
    try:
        completed = subprocess.run(
            [codex_executable, "plugin", "list", "--json"],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ExtensionResolutionError(f"Codex plugin inventory is unavailable: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise ExtensionResolutionError(
            "Codex plugin inventory failed" + (f": {detail[:300]}" if detail else "")
        )
    return _strict_json(completed.stdout, label="Codex plugin inventory")


def _read_selection(project_root: Path, codex_home: Path) -> tuple[str, str | None, Path | None]:
    project = project_root / PROJECT_CONFIG
    user = codex_home / USER_CONFIG
    selected = project if project.exists() else user if user.exists() else None
    if selected is None:
        return "none", None, None
    try:
        parsed = parse_engineering_config(
            _stable_bytes(selected, label="Android engineering config"),
            allow_identity=selected == user,
            require_extension=selected == project,
        )
    except EngineeringConfigError as exc:
        raise ExtensionResolutionError(str(exc)) from exc
    extension = parsed.get("extension")
    if extension is None:
        return "none", None, selected
    mode = extension.get("mode")
    if mode not in {"none", "jinny", "custom"}:
        raise ExtensionResolutionError("[extension].mode must be none, jinny, or custom")
    legacy = {"provider_id", "provider_version", "provider_manifest_sha256"}
    allowed = {"mode"} if mode == "none" else {"mode", *legacy}
    if mode == "custom":
        allowed.add("plugin_name")
    unexpected = set(extension) - allowed
    if unexpected:
        raise ExtensionResolutionError(
            f"[extension] contains unsupported fields for mode={mode}: {sorted(unexpected)}"
        )
    if mode == "none" and set(extension) != {"mode"}:
        raise ExtensionResolutionError("mode=none accepts only the mode field")
    if mode == "jinny":
        return mode, JINNY_PLUGIN, selected
    if mode == "custom":
        plugin_name = extension.get("plugin_name")
        if not plugin_name or not NAME_RE.fullmatch(plugin_name):
            raise ExtensionResolutionError("mode=custom requires a valid plugin_name")
        return mode, plugin_name, selected
    return "none", None, selected


def _validate_selected(
    plugin_name: str,
    *,
    mode: str,
    payload: Mapping[str, Any],
    codex_home: Path,
) -> tuple[str, str, Path, str, Path]:
    entries = payload.get("installed")
    if not isinstance(entries, list) or any(not isinstance(item, Mapping) for item in entries):
        raise ExtensionResolutionError("Codex plugin inventory has no valid installed list")
    matches = [
        item for item in entries
        if item.get("name") == plugin_name
        and item.get("installed") is True
        and item.get("enabled") is True
    ]
    if len(matches) != 1:
        raise ExtensionResolutionError(
            f"selected orchestration extension must have one active plugin: {plugin_name}"
        )
    row = matches[0]
    version = row.get("version")
    marketplace = row.get("marketplaceName")
    plugin_id = row.get("pluginId")
    source = row.get("source")
    if not isinstance(version, str) or not SEMVER_RE.fullmatch(version):
        raise ExtensionResolutionError("selected extension version is missing or malformed")
    if (
        not isinstance(marketplace, str)
        or not MARKETPLACE_RE.fullmatch(marketplace)
        or plugin_id != f"{plugin_name}@{marketplace}"
    ):
        raise ExtensionResolutionError("selected extension plugin identity is malformed")
    if mode == "jinny" and marketplace != OFFICIAL_MARKETPLACE:
        raise ExtensionResolutionError("Jinny extension must come from android-codex-suite")
    if (
        not isinstance(source, Mapping)
        or source.get("source") != "local"
        or not isinstance(source.get("path"), str)
        or not Path(source["path"]).is_absolute()
    ):
        raise ExtensionResolutionError("selected extension has no absolute local source root")
    source_root = Path(source["path"])
    runtime_root = codex_home / "plugins/cache" / marketplace / plugin_name / version
    pairs = {
        "plugin manifest": Path(".codex-plugin/plugin.json"),
        "extension manifest": EXTENSION_MANIFEST,
    }
    values: dict[str, dict[str, Any]] = {}
    for label, relative in pairs.items():
        source_raw = _stable_bytes(source_root / relative, label=f"source {label}")
        runtime_raw = _stable_bytes(runtime_root / relative, label=f"runtime {label}")
        if source_raw != runtime_raw:
            raise ExtensionResolutionError(f"selected extension source/runtime {label} differs")
        values[label] = _strict_json(runtime_raw, label=label)
    plugin = values["plugin manifest"]
    manifest = values["extension manifest"]
    if plugin.get("name") != plugin_name or plugin.get("version") != version:
        raise ExtensionResolutionError("selected extension inventory and plugin manifest differ")
    try:
        validate_document(manifest, SCHEMA_PATH)
    except (ContractValidationError, OSError) as exc:
        raise ExtensionResolutionError(f"selected extension manifest is invalid: {exc}") from exc
    if (
        manifest.get("provider_id") != plugin_name
        or manifest.get("provider_version") != version
        or CORE_CONTRACT not in manifest.get("compatible_core_contracts", [])
    ):
        raise ExtensionResolutionError("selected extension is incompatible with this core")
    orchestrator = manifest["orchestrator"]
    skill_id = orchestrator["skill_id"]
    skill_relative = Path("skills") / skill_id / "SKILL.md"
    agents_relative = Path("skills") / skill_id / "agents/openai.yaml"
    skill_raw = _stable_bytes(runtime_root / skill_relative, label="orchestrator Skill")
    agents_raw = _stable_bytes(runtime_root / agents_relative, label="orchestrator metadata")
    if skill_raw != _stable_bytes(source_root / skill_relative, label="source orchestrator Skill"):
        raise ExtensionResolutionError("selected extension source/runtime Skill differs")
    if agents_raw != _stable_bytes(source_root / agents_relative, label="source orchestrator metadata"):
        raise ExtensionResolutionError("selected extension source/runtime metadata differs")
    try:
        skill_text = skill_raw.decode("utf-8")
        agents_text = agents_raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ExtensionResolutionError("selected extension Skill metadata must be UTF-8") from exc
    skill_match = SKILL_NAME_RE.search(skill_text)
    if skill_match is None or skill_match.group(1) != skill_id:
        raise ExtensionResolutionError("selected extension Skill identity differs")
    if re.search(
        rf'^\s*default_prompt:\s*["\'][^"\']*\${re.escape(skill_id)}[^"\']*["\']\s*$',
        agents_text,
        re.MULTILINE,
    ) is None:
        raise ExtensionResolutionError("selected extension default prompt does not name its Skill")
    return plugin_id, version, runtime_root, skill_id, runtime_root / skill_relative


def resolve_extension(
    project_root: Path,
    *,
    inventory: Mapping[str, Any] | None = None,
    codex_home: Path | None = None,
    codex_executable: str = "codex",
) -> ExtensionResolution:
    """Return core-direct or one validated selected orchestration Skill."""
    home = (codex_home or Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")).expanduser()
    mode, plugin_name, config_path = _read_selection(project_root, home)
    if mode == "none":
        return ExtensionResolution(source="core", mode=mode, config_path=config_path)
    assert plugin_name is not None
    payload = inventory if inventory is not None else _inventory(codex_executable)
    plugin_id, version, root, skill_id, skill_path = _validate_selected(
        plugin_name, mode=mode, payload=payload, codex_home=home
    )
    return ExtensionResolution(
        source="extension",
        mode=mode,
        config_path=config_path,
        plugin_id=plugin_id,
        plugin_name=plugin_name,
        plugin_version=version,
        plugin_root=root,
        skill_id=skill_id,
        skill_path=skill_path,
    )
