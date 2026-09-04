"""Resolve one explicitly selected Android practices provider without scanning.

The resolver is deliberately self-contained so ``android-engineering-ops`` works when
it is the only installed plugin.  Provider Skills return decisions; this module never
spawns a worker, grants authority, writes source, or accepts a workflow gate.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

try:  # Python 3.11+
    import tomllib as _stdlib_tomllib
except ModuleNotFoundError:  # Python 3.10 and older Apple system Python
    _stdlib_tomllib = None

from .schema import ContractValidationError, validate_document
from android_engineering_ops.configuration import (
    EngineeringConfigError,
    parse_engineering_config,
)


CONFIG_NAME = "android-engineering.toml"
LOCAL_CONFIG_NAME = "android-engineering-ops.toml"
PROVIDER_RELATIVE_PATH = Path("contracts/android-practices-provider/v1/provider.json")
PLUGIN_ROOT = Path(__file__).resolve().parents[3]
CONTRACT_ROOT = PLUGIN_ROOT / "contracts" / "android-practices-provider" / "v1"
PROVIDER_SCHEMA = CONTRACT_ROOT / "provider.schema.json"
CODING_DECISION_SCHEMA = CONTRACT_ROOT / "coding-policy-decision.schema.json"
EXECUTION_DECISION_SCHEMA = CONTRACT_ROOT / "execution-policy-decision.schema.json"
CORE_CONTRACT = "android-engineering-ops-v1"
OFFICIAL_MARKETPLACE = "android-codex-suite"
MODES = {"none", "jinny", "custom"}
CAPABILITIES = {"coding", "execution"}
LAYERS = {"application", "platform", "native", "hal", "kernel", "device", "build"}
TASK_CLASSES = {
    "analysis", "diagnosis", "implementation", "review", "verification",
    "bounded_operation",
}
EFFECTS = {"read_only", "workspace_mutation", "controlled_operation"}
SEMVER_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")
PROVIDER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*-android-practices$")
SKILL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SKILL_NAME_RE = re.compile(r"^name:\s*([a-z0-9][a-z0-9-]*)\s*$", re.MULTILINE)
FROZEN_TOML_ASSIGNMENT_RE = re.compile(
    r'^([A-Za-z_][A-Za-z0-9_-]*)\s*=\s*"([^"\\\x00-\x1f]*)"\s*$'
)

EXPECTED_FALLBACK = {
    "capability_absent": "core",
    "applicability_miss": "core",
    "provider_missing_or_invalid": "fail_closed",
    "declared_capability_broken": "fail_closed",
    "invalid_decision": "fail_closed",
}
EXPECTED_AUTHORITY = {
    "decision_only": True,
    "can_spawn": False,
    "can_write_source": False,
    "can_acquire_lock": False,
    "can_execute_side_effects": False,
    "can_upload": False,
    "can_accept_gate": False,
    "can_final_accept": False,
}


class ExtensionResolutionError(ValueError):
    """The selected extension config is contradictory or cannot be read safely."""


class ProviderValidationError(ExtensionResolutionError):
    """An explicitly selected provider is missing, invalid, or broken."""


@dataclass(frozen=True)
class ProviderSkill:
    capability: str
    skill_id: str
    skill_version: str
    skill_path: Path
    skill_sha256: str
    agent_metadata_path: Path
    agent_metadata_sha256: str
    decision_entrypoint_path: Path
    decision_entrypoint_sha256: str
    manifest_value: Mapping[str, Any]


@dataclass(frozen=True)
class ActivePlugin:
    plugin_id: str
    name: str
    version: str
    marketplace: str
    root: Path
    inventory_source_root: Path
    source_plugin_manifest_sha256: str
    execution_plugin_manifest_sha256: str
    source_provider_manifest_sha256: str
    execution_provider_manifest_sha256: str


@dataclass(frozen=True)
class CapabilityBinding:
    source: str
    reason: str
    provider_id: str | None = None
    provider_version: str | None = None
    provider_manifest_sha256: str | None = None
    skill_id: str | None = None
    skill_version: str | None = None
    skill_path: Path | None = None
    skill_sha256: str | None = None
    agent_metadata_sha256: str | None = None
    decision_entrypoint_sha256: str | None = None

    def snapshot(self) -> dict[str, str]:
        """Return the closed capability shape accepted by stage-snapshot-v1."""
        return {"source": self.source, "reason": self.reason}

    def evidence(self) -> dict[str, str]:
        value = self.snapshot()
        for key in (
            "provider_id", "provider_version", "provider_manifest_sha256",
            "skill_id", "skill_version", "skill_sha256", "agent_metadata_sha256",
            "decision_entrypoint_sha256",
        ):
            item = getattr(self, key)
            if item:
                value[key] = item
        if self.skill_path:
            value["skill_path"] = str(self.skill_path)
        return value


@dataclass(frozen=True)
class ExtensionResolution:
    mode: str
    config_path: Path | None
    config_sha256: str
    provider_manifest_path: Path | None
    provider_manifest_sha256: str | None
    provider: Mapping[str, Any] | None
    skills: Mapping[str, ProviderSkill]
    active_plugin_id: str | None = None
    active_plugin_root: Path | None = None
    active_plugin_source_root: Path | None = None
    source_plugin_manifest_sha256: str | None = None
    execution_plugin_manifest_sha256: str | None = None
    source_provider_manifest_sha256: str | None = None
    execution_provider_manifest_sha256: str | None = None

    def capability(
        self,
        capability: str,
        *,
        workflow_action: str,
        component_layer: str,
    ) -> CapabilityBinding:
        if capability not in CAPABILITIES:
            raise ExtensionResolutionError(f"unknown provider capability: {capability}")
        if component_layer not in LAYERS:
            raise ExtensionResolutionError(f"unknown Android component layer: {component_layer}")
        if self.mode == "none":
            return CapabilityBinding(source="core", reason="mode_none")
        skill = self.skills.get(capability)
        if skill is None:
            return CapabilityBinding(source="core", reason="capability_absent")
        applicability = skill.manifest_value["applicability"]
        if (
            workflow_action not in applicability["workflow_actions"]
            or component_layer not in applicability["component_layers"]
        ):
            return CapabilityBinding(source="core", reason="applicability_miss")
        assert self.provider is not None
        return CapabilityBinding(
            source="provider",
            reason="provider_capability",
            provider_id=str(self.provider["provider_id"]),
            provider_version=str(self.provider["provider_version"]),
            provider_manifest_sha256=self.provider_manifest_sha256,
            skill_id=skill.skill_id,
            skill_version=skill.skill_version,
            skill_path=skill.skill_path,
            skill_sha256=skill.skill_sha256,
            agent_metadata_sha256=skill.agent_metadata_sha256,
            decision_entrypoint_sha256=skill.decision_entrypoint_sha256,
        )

    def stage_snapshot_resolution(
        self, *, workflow_action: str, component_layer: str,
    ) -> dict[str, Any]:
        value: dict[str, Any] = {
            "selection_mode": self.mode,
            "coding": self.capability(
                "coding", workflow_action=workflow_action, component_layer=component_layer,
            ).snapshot(),
            "execution": self.capability(
                "execution", workflow_action=workflow_action, component_layer=component_layer,
            ).snapshot(),
        }
        if self.provider is not None:
            value.update(
                {
                    "provider_id": self.provider["provider_id"],
                    "provider_version": self.provider["provider_version"],
                    "provider_manifest_sha256": self.provider_manifest_sha256,
                }
            )
        return value

    def evidence(self, *, workflow_action: str, component_layer: str) -> dict[str, Any]:
        return {
            "schema": "android-engineering-extension-resolution-v1",
            "config_path": str(self.config_path) if self.config_path else None,
            "config_sha256": self.config_sha256,
            "provider_manifest_path": (
                str(self.provider_manifest_path) if self.provider_manifest_path else None
            ),
            "active_plugin_id": self.active_plugin_id,
            "active_plugin_root": (
                str(self.active_plugin_root) if self.active_plugin_root else None
            ),
            "active_plugin_source_root": (
                str(self.active_plugin_source_root)
                if self.active_plugin_source_root else None
            ),
            "source_plugin_manifest_sha256": self.source_plugin_manifest_sha256,
            "execution_plugin_manifest_sha256": self.execution_plugin_manifest_sha256,
            "source_provider_manifest_sha256": self.source_provider_manifest_sha256,
            "execution_provider_manifest_sha256": self.execution_provider_manifest_sha256,
            "provider_resolution": self.stage_snapshot_resolution(
                workflow_action=workflow_action, component_layer=component_layer,
            ),
            "capabilities": {
                name: self.capability(
                    name,
                    workflow_action=workflow_action,
                    component_layer=component_layer,
                ).evidence()
                for name in sorted(CAPABILITIES)
            },
        }


def _strict_json(raw: bytes, *, label: str) -> dict[str, Any]:
    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ProviderValidationError(f"duplicate provider manifest key: {label}: {key}")
            value[key] = item
        return value

    def reject_constant(value: str) -> None:
        raise ProviderValidationError(f"non-finite provider manifest number: {value}")

    try:
        value = json.loads(
            raw.decode("utf-8"), object_pairs_hook=object_pairs, parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProviderValidationError(f"provider manifest is not strict UTF-8 JSON: {label}") from exc
    if not isinstance(value, dict):
        raise ProviderValidationError("provider manifest must be an object")
    return value


def _absolute_without_symlinks(path: Path, *, kind: str) -> Path:
    """Return an absolute path after rejecting symlinks in every existing component."""
    absolute = Path(os.path.abspath(os.fspath(path.expanduser())))
    current = Path(absolute.anchor)
    try:
        for part in absolute.parts[1:]:
            current = current / part
            info = current.lstat()
            if stat.S_ISLNK(info.st_mode):
                raise ProviderValidationError(f"{kind} contains a symlink: {current}")
    except OSError as exc:
        raise ProviderValidationError(f"cannot inspect {kind}: {current}: {exc}") from exc
    return absolute


def _stable_bytes(path: Path, *, kind: str) -> bytes:
    path = _absolute_without_symlinks(path, kind=kind)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    try:
        descriptor = os.open(path, flags)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ProviderValidationError(f"{kind} is not a regular file: {path}")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
    except OSError as exc:
        raise ProviderValidationError(f"cannot read {kind}: {path}: {exc}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    identity = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns)
    if identity(before) != identity(after):
        raise ProviderValidationError(f"{kind} changed while it was read: {path}")
    return b"".join(chunks)


def _string_list(value: Any, *, field: str, allow_empty: bool = True) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise ProviderValidationError(f"{field} must be a string array")
    if not allow_empty and not value:
        raise ProviderValidationError(f"{field} must not be empty")
    if len(value) != len(set(value)):
        raise ProviderValidationError(f"{field} must contain unique values")
    return value


def _validate_applicability(value: Any, *, capability: str) -> None:
    if not isinstance(value, dict) or set(value) != {"workflow_actions", "component_layers"}:
        raise ProviderValidationError(f"{capability} applicability shape is invalid")
    _string_list(value["workflow_actions"], field=f"{capability}.workflow_actions")
    layers = _string_list(value["component_layers"], field=f"{capability}.component_layers")
    if not set(layers).issubset(LAYERS):
        raise ProviderValidationError(f"{capability} declares an unknown component layer")


def _validate_capability(name: str, value: Any) -> None:
    base = {
        "contract", "skill_id", "skill_version", "skill_sha256",
        "agent_metadata_sha256", "decision_entrypoint_path",
        "decision_entrypoint_sha256", "decision_schema", "applicability",
    }
    expected_keys = base | ({"worker_profiles"} if name == "execution" else set())
    if not isinstance(value, dict) or set(value) != expected_keys:
        raise ProviderValidationError(f"{name} capability shape is invalid")
    expected_contract = {
        "coding": "android-coding-policy-provider-v1",
        "execution": "android-execution-policy-provider-v1",
    }[name]
    expected_decision = {
        "coding": "coding-policy-decision-v1",
        "execution": "execution-policy-decision-v1",
    }[name]
    if value["contract"] != expected_contract or value["decision_schema"] != expected_decision:
        raise ProviderValidationError(f"{name} capability contract differs")
    if not isinstance(value["skill_id"], str) or not SKILL_ID_RE.fullmatch(value["skill_id"]):
        raise ProviderValidationError(f"{name} capability skill ID is invalid")
    if not isinstance(value["skill_version"], str) or not SEMVER_RE.fullmatch(value["skill_version"]):
        raise ProviderValidationError(f"{name} capability skill version is invalid")
    for field in ("skill_sha256", "agent_metadata_sha256", "decision_entrypoint_sha256"):
        if not isinstance(value[field], str) or not SHA256_RE.fullmatch(value[field]):
            raise ProviderValidationError(f"{name} capability {field} is invalid")
    entrypoint = value["decision_entrypoint_path"]
    if (
        not isinstance(entrypoint, str)
        or Path(entrypoint).is_absolute()
        or ".." in Path(entrypoint).parts
        or Path(entrypoint).parts[:2] != ("skills", value["skill_id"])
        or Path(entrypoint).parts[2:3] != ("scripts",)
    ):
        raise ProviderValidationError(f"{name} decision entrypoint path is invalid")
    _validate_applicability(value["applicability"], capability=name)
    if name != "execution":
        return
    profiles = value["worker_profiles"]
    if not isinstance(profiles, dict) or not profiles:
        raise ProviderValidationError("execution capability requires worker profiles")
    for profile_id, profile in profiles.items():
        if not isinstance(profile_id, str) or not re.fullmatch(r"^[a-z0-9][a-z0-9._-]*$", profile_id):
            raise ProviderValidationError("execution worker profile ID is invalid")
        if not isinstance(profile, dict) or set(profile) != {"dispatch", "task_classes", "effect_ceiling"}:
            raise ProviderValidationError(f"execution worker profile is invalid: {profile_id}")
        dispatch = profile["dispatch"]
        if (
            not isinstance(dispatch, dict)
            or set(dispatch) != {"model_id", "reasoning_effort"}
            or any(not isinstance(item, str) or not item for item in dispatch.values())
        ):
            raise ProviderValidationError(f"execution dispatch is invalid: {profile_id}")
        classes = _string_list(
            profile["task_classes"], field=f"{profile_id}.task_classes", allow_empty=False,
        )
        if not set(classes).issubset(TASK_CLASSES) or profile["effect_ceiling"] not in EFFECTS:
            raise ProviderValidationError(f"execution profile boundary is invalid: {profile_id}")


def _validate_provider_document(value: dict[str, Any]) -> None:
    try:
        validate_document(value, PROVIDER_SCHEMA)
    except (ContractValidationError, OSError) as exc:
        raise ProviderValidationError(f"provider manifest violates packaged schema: {exc}") from exc
    expected_keys = {
        "schema", "provider_id", "provider_version", "compatible_core_contracts",
        "capabilities", "fallback", "authority",
    }
    if set(value) != expected_keys or value.get("schema") != "android-practices-provider-v1":
        raise ProviderValidationError("provider manifest root shape is invalid")
    provider_id = value.get("provider_id")
    version = value.get("provider_version")
    if not isinstance(provider_id, str) or not PROVIDER_ID_RE.fullmatch(provider_id):
        raise ProviderValidationError("provider ID is invalid")
    if not isinstance(version, str) or not SEMVER_RE.fullmatch(version):
        raise ProviderValidationError("provider version is invalid")
    compatible = _string_list(
        value.get("compatible_core_contracts"),
        field="compatible_core_contracts",
        allow_empty=False,
    )
    if CORE_CONTRACT not in compatible:
        raise ProviderValidationError("provider is not compatible with this core contract")
    if value.get("fallback") != EXPECTED_FALLBACK:
        raise ProviderValidationError("provider fallback is not fail-closed")
    authority = value.get("authority")
    if authority != EXPECTED_AUTHORITY or any(type(authority[key]) is not bool for key in authority):
        raise ProviderValidationError("provider authority is not decision-only")
    capabilities = value.get("capabilities")
    if not isinstance(capabilities, dict) or not capabilities or not set(capabilities).issubset(CAPABILITIES):
        raise ProviderValidationError("provider capabilities are invalid")
    for name, capability in capabilities.items():
        _validate_capability(name, capability)


def _provider_skills(
    active: ActivePlugin, manifest_path: Path, provider: Mapping[str, Any],
) -> dict[str, ProviderSkill]:
    plugin_root = active.root
    try:
        relative = manifest_path.relative_to(plugin_root)
    except ValueError as exc:  # pragma: no cover - structural guard
        raise ProviderValidationError("provider manifest has no plugin root") from exc
    if relative != PROVIDER_RELATIVE_PATH:
        raise ProviderValidationError(
            f"provider manifest must use {PROVIDER_RELATIVE_PATH.as_posix()}"
        )
    plugin_manifest_path = plugin_root / ".codex-plugin/plugin.json"
    plugin_manifest = _strict_json(
        _stable_bytes(plugin_manifest_path, kind="provider plugin manifest"),
        label=str(plugin_manifest_path),
    )
    if (
        plugin_manifest.get("name") != active.name
        or plugin_manifest.get("version") != provider["provider_version"]
    ):
        raise ProviderValidationError("provider manifest identity differs from plugin identity")
    interface = plugin_manifest.get("interface")
    declared_interface_capabilities = (
        interface.get("capabilities") if isinstance(interface, dict) else None
    )
    if (
        not isinstance(declared_interface_capabilities, list)
        or any(not isinstance(item, str) for item in declared_interface_capabilities)
        or "Write" in declared_interface_capabilities
    ):
        raise ProviderValidationError(
            "provider plugin interface must be declared and must not include Write"
        )
    skills: dict[str, ProviderSkill] = {}
    for capability, value in provider["capabilities"].items():
        skill_id = value["skill_id"]
        skill_path = plugin_root / "skills" / skill_id / "SKILL.md"
        raw = _stable_bytes(skill_path, kind=f"declared {capability} Skill")
        try:
            skill_text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProviderValidationError(f"declared Skill is not UTF-8: {skill_path}") from exc
        name_match = SKILL_NAME_RE.search(skill_text)
        if name_match is None or name_match.group(1) != skill_id:
            raise ProviderValidationError(f"declared Skill identity differs: {skill_path}")
        skill_digest = hashlib.sha256(raw).hexdigest()
        if skill_digest != value["skill_sha256"]:
            raise ProviderValidationError(f"declared {capability} Skill SHA-256 differs")
        metadata = skill_path.parent / "agents/openai.yaml"
        metadata_raw = _stable_bytes(metadata, kind=f"declared {capability} Skill metadata")
        metadata_digest = hashlib.sha256(metadata_raw).hexdigest()
        if metadata_digest != value["agent_metadata_sha256"]:
            raise ProviderValidationError(
                f"declared {capability} agent metadata SHA-256 differs"
            )
        entrypoint_relative = Path(value["decision_entrypoint_path"])
        entrypoint = plugin_root / entrypoint_relative
        try:
            entrypoint.relative_to(skill_path.parent / "scripts")
        except ValueError as exc:
            raise ProviderValidationError(
                f"declared {capability} decision entrypoint escapes its Skill"
            ) from exc
        entrypoint_raw = _stable_bytes(
            entrypoint, kind=f"declared {capability} decision entrypoint"
        )
        entrypoint_digest = hashlib.sha256(entrypoint_raw).hexdigest()
        if entrypoint_digest != value["decision_entrypoint_sha256"]:
            raise ProviderValidationError(
                f"declared {capability} decision entrypoint SHA-256 differs"
            )
        skills[capability] = ProviderSkill(
            capability=capability,
            skill_id=skill_id,
            skill_version=value["skill_version"],
            skill_path=skill_path,
            skill_sha256=skill_digest,
            agent_metadata_path=metadata,
            agent_metadata_sha256=metadata_digest,
            decision_entrypoint_path=entrypoint,
            decision_entrypoint_sha256=entrypoint_digest,
            manifest_value=value,
        )
    return skills


def _config_path(project_root: Path, codex_home: Path) -> Path | None:
    project = Path(os.path.abspath(os.fspath(project_root.expanduser()))) / ".codex" / CONFIG_NAME
    if project.exists() or project.is_symlink():
        return project
    local = Path(os.path.abspath(os.fspath(codex_home.expanduser()))) / LOCAL_CONFIG_NAME
    return local if local.exists() or local.is_symlink() else None


def _parse_frozen_extension_toml(
    raw: bytes,
    *,
    allow_identity: bool = False,
) -> dict[str, dict[str, str]]:
    """Parse the dependency-free public config subset identically on every host.

    The extension contract deliberately does not expose paths, arrays, numbers,
    interpolation, nested tables, or TOML escapes.  Accepting those forms on one
    Python version and silently ignoring them on another would weaken the closed
    config binding, so even the stdlib parser is preceded by this lexical gate.
    """
    try:
        return parse_engineering_config(
            raw,
            allow_identity=allow_identity,
            require_extension=not allow_identity,
        )
    except EngineeringConfigError as exc:
        raise ExtensionResolutionError(str(exc)) from exc


def _parse_extension_config(
    raw: bytes,
    *,
    allow_identity: bool = False,
) -> dict[str, dict[str, str]]:
    frozen = _parse_frozen_extension_toml(raw, allow_identity=allow_identity)
    if _stdlib_tomllib is None:
        return frozen
    try:
        parsed = _stdlib_tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, _stdlib_tomllib.TOMLDecodeError) as exc:
        raise ExtensionResolutionError(f"cannot parse extension config: {exc}") from exc
    if parsed != frozen:
        raise ExtensionResolutionError("extension config differs from the frozen TOML subset")
    return frozen


def _load_config(
    path: Path | None,
    *,
    allow_identity: bool = False,
) -> tuple[dict[str, str], str]:
    if path is None:
        return {"mode": "none"}, hashlib.sha256(b"").hexdigest()
    try:
        raw = _stable_bytes(path, kind="extension config")
        payload = _parse_extension_config(raw, allow_identity=allow_identity)
    except ProviderValidationError as exc:
        raise ExtensionResolutionError(str(exc)) from exc
    except OSError as exc:
        raise ExtensionResolutionError(f"cannot parse extension config {path}: {exc}") from exc
    allowed_tables = {"extension", *( ["identity"] if allow_identity else [])}
    if not isinstance(payload, dict) or not set(payload).issubset(allowed_tables):
        raise ExtensionResolutionError("extension config contains an unsupported table")
    value = payload.get("extension", {"mode": "none"})
    allowed = {
        "mode", "plugin_name", "provider_id", "provider_version", "provider_manifest_sha256",
    }
    if not isinstance(value, dict) or not set(value).issubset(allowed) or "mode" not in value:
        raise ExtensionResolutionError("[extension] shape is invalid")
    if any(not isinstance(item, str) for item in value.values()):
        raise ExtensionResolutionError("[extension] values must be strings")
    expected_fields = {
        "none": {"mode"},
        "jinny": {"mode", "provider_version", "provider_manifest_sha256"},
        "custom": {
            "mode", "plugin_name", "provider_id", "provider_version",
            "provider_manifest_sha256",
        },
    }
    mode = value["mode"].strip()
    if mode not in expected_fields:
        raise ExtensionResolutionError(f"extension mode must be one of {sorted(expected_fields)}")
    if set(value) != expected_fields[mode]:
        raise ExtensionResolutionError(
            f"mode {mode} requires exactly {sorted(expected_fields[mode])}"
        )
    return dict(value), hashlib.sha256(raw).hexdigest()


def _strict_inventory(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, dict) or not isinstance(value.get("installed"), list):
        raise ProviderValidationError("Codex plugin inventory has no installed list")
    entries = value["installed"]
    if any(not isinstance(item, dict) for item in entries):
        raise ProviderValidationError("Codex plugin inventory contains a non-object entry")
    return entries


def _codex_inventory(codex_executable: str) -> Mapping[str, Any]:
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
        raise ProviderValidationError(f"Codex active plugin inventory is unavailable: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise ProviderValidationError(
            "Codex active plugin inventory failed"
            + (f": {detail[:500]}" if detail else "")
        )
    value = _strict_json(completed.stdout, label="codex plugin list --json")
    _strict_inventory(value)
    return value


def _active_plugin(
    *,
    plugin_name: str,
    provider_version: str,
    inventory: Mapping[str, Any],
    codex_home: Path,
    expected_marketplace: str | None,
) -> ActivePlugin:
    candidates: list[Mapping[str, Any]] = []
    for entry in _strict_inventory(inventory):
        if entry.get("installed") is not True or entry.get("enabled") is not True:
            continue
        if entry.get("name") != plugin_name or entry.get("version") != provider_version:
            continue
        candidates.append(entry)
    if len(candidates) != 1:
        if not candidates:
            raise ProviderValidationError(
                f"selected provider plugin is not active at the pinned version: {plugin_name} {provider_version}"
            )
        raise ProviderValidationError(
            f"selected provider plugin is ambiguous in the active inventory: {plugin_name}"
        )
    entry = candidates[0]
    actual_plugin_id = entry.get("pluginId")
    marketplace = entry.get("marketplaceName")
    source = entry.get("source")
    if (
        not isinstance(actual_plugin_id, str)
        or not actual_plugin_id
        or not isinstance(marketplace, str)
        or not marketplace
        or actual_plugin_id != f"{plugin_name}@{marketplace}"
        or not isinstance(source, dict)
        or source.get("source") != "local"
        or not isinstance(source.get("path"), str)
        or not source["path"]
    ):
        raise ProviderValidationError("selected provider inventory entry has no supported installed source")
    if expected_marketplace is not None and marketplace != expected_marketplace:
        raise ProviderValidationError(
            f"selected provider marketplace must be {expected_marketplace}"
        )
    raw_source_root = Path(source["path"]).expanduser()
    if not raw_source_root.is_absolute():
        raise ProviderValidationError("selected provider inventory root must be absolute")
    source_root = _absolute_without_symlinks(
        raw_source_root, kind="active provider inventory source root"
    )
    try:
        if not source_root.is_dir():
            raise ProviderValidationError(
                f"active provider inventory source root is not a directory: {source_root}"
            )
    except OSError as exc:
        raise ProviderValidationError(
            f"cannot inspect active provider inventory source root: {source_root}: {exc}"
        ) from exc

    source_plugin_path = source_root / ".codex-plugin/plugin.json"
    source_plugin_raw = _stable_bytes(
        source_plugin_path, kind="provider inventory source plugin manifest"
    )
    source_plugin = _strict_json(source_plugin_raw, label=str(source_plugin_path))
    if (
        source_plugin.get("name") != plugin_name
        or source_plugin.get("version") != provider_version
    ):
        raise ProviderValidationError(
            "provider inventory identity differs from its source plugin manifest"
        )

    cache_candidate = (
        codex_home.expanduser()
        / "plugins" / "cache" / marketplace / plugin_name / provider_version
    )
    if cache_candidate.exists():
        root = _absolute_without_symlinks(
            cache_candidate, kind="active provider runtime cache root"
        )
        if not root.is_dir():
            raise ProviderValidationError(
                f"active provider runtime cache root is not a directory: {root}"
            )
    else:
        raise ProviderValidationError(
            "selected provider runtime cache is missing for its active inventory source"
        )

    execution_plugin_path = root / ".codex-plugin/plugin.json"
    execution_plugin_raw = _stable_bytes(
        execution_plugin_path, kind="provider runtime plugin manifest"
    )
    if execution_plugin_raw != source_plugin_raw:
        raise ProviderValidationError(
            "provider inventory source and runtime plugin manifest bytes differ"
        )
    execution_plugin = _strict_json(execution_plugin_raw, label=str(execution_plugin_path))
    if execution_plugin != source_plugin:
        raise ProviderValidationError(
            "provider inventory source and runtime plugin manifest differ"
        )
    source_provider_path = source_root / PROVIDER_RELATIVE_PATH
    execution_provider_path = root / PROVIDER_RELATIVE_PATH
    source_provider_raw = _stable_bytes(
        source_provider_path, kind="provider inventory source manifest"
    )
    execution_provider_raw = _stable_bytes(
        execution_provider_path, kind="provider runtime manifest"
    )
    if source_provider_raw != execution_provider_raw:
        raise ProviderValidationError(
            "provider inventory source and runtime provider manifest bytes differ"
        )
    return ActivePlugin(
        plugin_id=actual_plugin_id,
        name=plugin_name,
        version=provider_version,
        marketplace=marketplace,
        root=root,
        inventory_source_root=source_root,
        source_plugin_manifest_sha256=hashlib.sha256(source_plugin_raw).hexdigest(),
        execution_plugin_manifest_sha256=hashlib.sha256(execution_plugin_raw).hexdigest(),
        source_provider_manifest_sha256=hashlib.sha256(source_provider_raw).hexdigest(),
        execution_provider_manifest_sha256=hashlib.sha256(execution_provider_raw).hexdigest(),
    )


def _expected_binding(binding: CapabilityBinding) -> dict[str, str]:
    if binding.source != "provider":
        raise ProviderValidationError("a provider decision cannot bind a core capability")
    keys = (
        "provider_id", "provider_version", "provider_manifest_sha256",
        "skill_id", "skill_version", "skill_sha256", "agent_metadata_sha256",
        "decision_entrypoint_sha256",
    )
    value = {key: getattr(binding, key) for key in keys}
    if any(not isinstance(item, str) or not item for item in value.values()):
        raise ProviderValidationError("provider capability binding is incomplete")
    return value  # type: ignore[return-value]


def _validate_decision_binding(value: Mapping[str, Any], binding: CapabilityBinding) -> None:
    if value.get("provider") != _expected_binding(binding):
        raise ProviderValidationError("provider decision binding differs from the resolved provider")


def _validate_decision_context(
    value: Mapping[str, Any],
    *,
    expected_decision_id: str,
    expected_run_id: str,
    expected_stage_id: str,
    expected_context_sha256: str,
) -> None:
    expected = {
        "decision_id": expected_decision_id,
        "run_id": expected_run_id,
        "stage_id": expected_stage_id,
        "context_sha256": expected_context_sha256,
    }
    for field, item in expected.items():
        if not isinstance(item, str) or not item:
            raise ProviderValidationError(f"controller expected {field} is invalid")
        if value.get(field) != item:
            raise ProviderValidationError(
                f"provider decision does not bind controller expected {field}"
            )


def validate_coding_decision(
    value: Mapping[str, Any],
    *,
    binding: CapabilityBinding,
    core_policy_sha256: str,
    expected_decision_id: str,
    expected_run_id: str,
    expected_stage_id: str,
    expected_context_sha256: str,
) -> None:
    """Validate one provider coding decision against packaged contracts and pins."""
    try:
        validate_document(value, CODING_DECISION_SCHEMA)
    except (ContractValidationError, OSError) as exc:
        raise ProviderValidationError(f"coding provider decision is invalid: {exc}") from exc
    _validate_decision_context(
        value,
        expected_decision_id=expected_decision_id,
        expected_run_id=expected_run_id,
        expected_stage_id=expected_stage_id,
        expected_context_sha256=expected_context_sha256,
    )
    _validate_decision_binding(value, binding)
    if value.get("core_policy_sha256") != core_policy_sha256:
        raise ProviderValidationError("coding provider decision is bound to a different core policy")


_EFFECT_ORDER = {
    "read_only": 0,
    "workspace_mutation": 1,
    "controlled_operation": 2,
}


def validate_execution_decision(
    value: Mapping[str, Any],
    *,
    binding: CapabilityBinding,
    rollout_effect_ceiling: str,
    expected_decision_id: str,
    expected_run_id: str,
    expected_stage_id: str,
    expected_context_sha256: str,
) -> None:
    """Validate a decision; authority to dispatch or accept remains with the controller."""
    if rollout_effect_ceiling not in _EFFECT_ORDER:
        raise ProviderValidationError("unknown rollout effect ceiling")
    try:
        validate_document(value, EXECUTION_DECISION_SCHEMA)
    except (ContractValidationError, OSError) as exc:
        raise ProviderValidationError(f"execution provider decision is invalid: {exc}") from exc
    _validate_decision_context(
        value,
        expected_decision_id=expected_decision_id,
        expected_run_id=expected_run_id,
        expected_stage_id=expected_stage_id,
        expected_context_sha256=expected_context_sha256,
    )
    _validate_decision_binding(value, binding)
    outcome = value.get("outcome")
    if not isinstance(outcome, dict) or outcome.get("type") != "delegate":
        return
    if binding.source != "provider" or not isinstance(binding.skill_id, str):
        raise ProviderValidationError("execution provider binding is incomplete")
    # The caller resolves the exact provider manifest first.  The selected Skill's
    # manifest fragment is available on the CapabilityBinding only indirectly, so
    # profile validation is performed by validate_execution_decision_for_resolution.
    effect = outcome.get("requested_effect")
    if effect not in _EFFECT_ORDER or _EFFECT_ORDER[effect] > _EFFECT_ORDER[rollout_effect_ceiling]:
        raise ProviderValidationError("execution provider decision exceeds the active rollout ceiling")


def validate_execution_decision_for_resolution(
    value: Mapping[str, Any],
    *,
    resolution: ExtensionResolution,
    binding: CapabilityBinding,
    rollout_effect_ceiling: str,
    expected_decision_id: str,
    expected_run_id: str,
    expected_stage_id: str,
    expected_context_sha256: str,
) -> None:
    """Additionally bind a delegate decision to its declared worker profile."""
    validate_execution_decision(
        value,
        binding=binding,
        rollout_effect_ceiling=rollout_effect_ceiling,
        expected_decision_id=expected_decision_id,
        expected_run_id=expected_run_id,
        expected_stage_id=expected_stage_id,
        expected_context_sha256=expected_context_sha256,
    )
    outcome = value.get("outcome")
    if not isinstance(outcome, dict) or outcome.get("type") != "delegate":
        return
    capability = resolution.provider and resolution.provider.get("capabilities", {}).get("execution")
    profiles = capability.get("worker_profiles") if isinstance(capability, dict) else None
    profile = profiles.get(outcome.get("worker_profile_id")) if isinstance(profiles, dict) else None
    if not isinstance(profile, dict):
        raise ProviderValidationError("execution decision selected an undeclared worker profile")
    task_class = outcome.get("task_class")
    effect = outcome.get("requested_effect")
    if task_class not in profile.get("task_classes", []):
        raise ProviderValidationError("execution decision task class exceeds its worker profile")
    profile_ceiling = profile.get("effect_ceiling")
    if (
        effect not in _EFFECT_ORDER
        or profile_ceiling not in _EFFECT_ORDER
        or _EFFECT_ORDER[effect] > _EFFECT_ORDER[profile_ceiling]
    ):
        raise ProviderValidationError("execution decision effect exceeds its worker profile")


def resolve_extension(
    *,
    project_root: Path | str | None = None,
    codex_home: Path | str | None = None,
    inventory: Mapping[str, Any] | None = None,
    codex_executable: str = "codex",
) -> ExtensionResolution:
    """Resolve project config first, then bind to one active installed provider."""
    project = Path(project_root) if project_root is not None else Path.cwd()
    home = (
        Path(codex_home)
        if codex_home is not None
        else Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    )
    path = _config_path(project, home)
    local_config = Path(os.path.abspath(os.fspath(home.expanduser()))) / LOCAL_CONFIG_NAME
    config, config_sha256 = _load_config(
        path,
        allow_identity=bool(path is not None and path == local_config),
    )
    mode = config["mode"].strip()
    if mode not in MODES:
        raise ExtensionResolutionError(f"extension mode must be one of {sorted(MODES)}")
    binding_keys = {
        "plugin_name", "provider_id", "provider_version", "provider_manifest_sha256",
    }
    bindings = {key: config.get(key, "").strip() for key in binding_keys}
    if mode == "none":
        return ExtensionResolution(
            mode=mode,
            config_path=path.resolve() if path else None,
            config_sha256=config_sha256,
            provider_manifest_path=None,
            provider_manifest_sha256=None,
            provider=None,
            skills={},
            active_plugin_id=None,
            active_plugin_root=None,
            active_plugin_source_root=None,
            source_plugin_manifest_sha256=None,
            execution_plugin_manifest_sha256=None,
            source_provider_manifest_sha256=None,
            execution_provider_manifest_sha256=None,
        )
    provider_id = bindings["provider_id"]
    if mode == "jinny":
        plugin_name = "jinny-android-practices"
        provider_id = "jinny-android-practices"
    elif not provider_id or provider_id == "jinny-android-practices":
        raise ExtensionResolutionError("custom mode requires a non-Jinny provider ID")
    else:
        plugin_name = bindings["plugin_name"]
        if not plugin_name:
            raise ExtensionResolutionError("custom mode requires plugin_name")
    if not bindings["provider_version"] or not bindings["provider_manifest_sha256"]:
        raise ExtensionResolutionError(
            "selected provider requires exact version and provider manifest SHA-256"
        )
    if not SEMVER_RE.fullmatch(bindings["provider_version"]):
        raise ExtensionResolutionError("provider version pin is invalid")
    if not SHA256_RE.fullmatch(bindings["provider_manifest_sha256"]):
        raise ExtensionResolutionError("provider manifest SHA-256 is invalid")
    active = _active_plugin(
        plugin_name=plugin_name,
        provider_version=bindings["provider_version"],
        inventory=inventory if inventory is not None else _codex_inventory(codex_executable),
        codex_home=home,
        expected_marketplace=OFFICIAL_MARKETPLACE if mode == "jinny" else None,
    )
    provider_path = active.root / PROVIDER_RELATIVE_PATH
    raw = _stable_bytes(provider_path, kind="selected provider manifest")
    digest = hashlib.sha256(raw).hexdigest()
    if digest != bindings["provider_manifest_sha256"]:
        raise ProviderValidationError("selected provider manifest SHA-256 differs")
    provider = _strict_json(raw, label=str(provider_path))
    _validate_provider_document(provider)
    if (
        provider["provider_id"] != provider_id
        or provider["provider_version"] != bindings["provider_version"]
    ):
        raise ProviderValidationError("selected provider identity or version differs")
    skills = _provider_skills(active, provider_path, provider)
    if _stable_bytes(provider_path, kind="selected provider manifest") != raw:
        raise ProviderValidationError("selected provider manifest changed during validation")
    return ExtensionResolution(
        mode=mode,
        config_path=path.resolve() if path else None,
        config_sha256=config_sha256,
        provider_manifest_path=provider_path,
        provider_manifest_sha256=digest,
        provider=provider,
        skills=skills,
        active_plugin_id=active.plugin_id,
        active_plugin_root=active.root,
        active_plugin_source_root=active.inventory_source_root,
        source_plugin_manifest_sha256=active.source_plugin_manifest_sha256,
        execution_plugin_manifest_sha256=active.execution_plugin_manifest_sha256,
        source_provider_manifest_sha256=active.source_provider_manifest_sha256,
        execution_provider_manifest_sha256=active.execution_provider_manifest_sha256,
    )
