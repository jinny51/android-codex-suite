"""Read, check, and byte-preserve Android change v2 packages.

This module validates untrusted client coherence only. The hash-pinned evidence
profile is not a live server switch; nothing here issues an upload or claims
server qualification.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import secrets
import stat
from pathlib import Path, PurePosixPath
from typing import Any

from akbs_member_ops.artifact_paths import require_safe_artifact_path
from akbs_member_ops.member_config import default_codex_home

from .schema import SchemaError, load_json_bytes, validate_document


PLUGIN_ROOT = Path(__file__).resolve().parents[3]
CONTRACT_ROOT = PLUGIN_ROOT / "contracts" / "incoming" / "v2"
PACKAGE_SCHEMA_PATH = CONTRACT_ROOT / "akbs-android-change-package.schema.json"
CLIENT_OUTPUT_SCHEMA_PATH = CONTRACT_ROOT / "client-adapter-outputs.schema.json"
PROFILE_PATH = CONTRACT_ROOT / "component-evidence-profiles.json"
PACKAGE_IDENTITY = ("akbs-android-change-package-v2", "2", "android_change")
CONTRACT_SHA256 = {
    "akbs-android-change-package.schema.json": "34410386b8af3e88686d109cc482a74117f68de9ba3ad9208fbd93977c502e23",
    "client-adapter-outputs.schema.json": "953c84592416d569e3b642f09835aaf8ee668c7619d0bb52a79a4b3ddb7be7a5",
    "component-evidence-profiles.json": "5e2f0eb8341d3b6ef58084adcaeebfb6627c83807776b13f1e709fbec847dc4c",
    "capture-package.schema.json": "df925aab64a7c3854095c294f19164c7631befccd87eae6fed7614a05be665c3",
    "capture-package-v2.1.schema.json": "e79a65a2a9e9cbde9d3bf1d605d2f5fab05797969b75fd75c9dc7a620d148901",
    "qualification-contract-pack-v2.json": "f4d909382059f6db3a2007d1c6e9ab21ee08b2398a7de07c2292bf0826d4cb54",
    "qualification-adapter-input-v2.schema.json": "612d432792d20c1aa1255e368068f7d16ac165c40600e4467ff1ea54f5c59e37",
    "qualification-adapter-inputs-v2.schema.json": "c098833d00899063f35a3874bf2390626a448d83d62c36e6900ba7d121d55a96",
}


class AndroidChangeV2Error(ValueError):
    """A v2 package is unsafe, incoherent, or outside the frozen contract."""


def _raise_schema(exc: SchemaError) -> None:
    raise AndroidChangeV2Error(str(exc)) from exc


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _manifest_path(value: Path) -> Path:
    path = value.expanduser()
    if path.is_dir():
        path = path / "manifest.json"
    if path.name != "manifest.json":
        raise AndroidChangeV2Error("Android change v2 input must be a package directory or manifest.json")
    if path.is_symlink() or not path.is_file():
        raise AndroidChangeV2Error(f"Android change v2 manifest is not a regular file: {path}")
    return path


def _load_contract(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        expected = CONTRACT_SHA256.get(path.name)
        if not expected or _sha256(raw) != expected:
            raise AndroidChangeV2Error(f"bundled Android change v2 contract hash differs: {path.name}")
        return load_json_bytes(raw, label=str(path))
    except OSError as exc:
        raise AndroidChangeV2Error(f"cannot read bundled Android change v2 contract: {path}: {exc}") from exc
    except SchemaError as exc:
        _raise_schema(exc)
    raise AssertionError("unreachable")


def _load_manifest(value: Path, *, validate_schema: bool = True) -> tuple[Path, bytes, dict[str, Any]]:
    path = _manifest_path(value)
    try:
        raw = path.read_bytes()
        package = load_json_bytes(raw, label=str(path))
    except (OSError, SchemaError) as exc:
        if isinstance(exc, SchemaError):
            _raise_schema(exc)
        raise AndroidChangeV2Error(f"cannot read Android change v2 manifest: {path}: {exc}") from exc
    identity = (
        package.get("schema"),
        package.get("schema_version"),
        package.get("package_kind"),
    )
    if identity != PACKAGE_IDENTITY:
        raise AndroidChangeV2Error(
            "package identity is not akbs-android-change-package-v2/2/android_change; v1 fallback is forbidden"
        )
    if validate_schema:
        try:
            validate_document(package, _load_contract(PACKAGE_SCHEMA_PATH))
        except SchemaError as exc:
            _raise_schema(exc)
    return path, raw, package


def _normalized_archive_path(value: object) -> str:
    if not isinstance(value, str) or not value or value.startswith("/") or "\\" in value:
        raise AndroidChangeV2Error("Android change v2 archive path is unsafe")
    path = PurePosixPath(value)
    normalized = path.as_posix()
    if value != normalized or value in {".", ".."} or any(part in {"", ".", ".."} for part in path.parts):
        raise AndroidChangeV2Error("Android change v2 archive path is not canonical")
    return normalized


def _require_canonical_json_domain(value: Any, *, path: str = "$") -> None:
    if value is None or isinstance(value, (bool, str, int)):
        return
    if isinstance(value, float):
        raise AndroidChangeV2Error(f"AKBS canonical JSON v1 forbids floating-point numbers: {path}")
    if isinstance(value, list):
        for index, item in enumerate(value):
            _require_canonical_json_domain(item, path=f"{path}/{index}")
        return
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise AndroidChangeV2Error(f"AKBS canonical JSON v1 requires text object keys: {path}")
        for key, item in value.items():
            _require_canonical_json_domain(item, path=f"{path}/{key}")
        return
    raise AndroidChangeV2Error(f"AKBS canonical JSON v1 unsupported value: {path}")


def canonical_json_sha256(value: Any) -> str:
    _require_canonical_json_domain(value)
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return _sha256(raw)


def qualification_input_sha256(package: dict[str, Any]) -> str:
    candidate = copy.deepcopy(package)
    qualification = candidate.get("qualification") or {}
    output_file_id = qualification.get("client_adapter_outputs_file_id")
    files = candidate.get("files")
    if not isinstance(output_file_id, str) or not isinstance(files, list):
        raise AndroidChangeV2Error("Android change v2 client output binding is missing")
    retained = [row for row in files if isinstance(row, dict) and row.get("id") != output_file_id]
    if len(retained) != len(files) - 1:
        raise AndroidChangeV2Error("Android change v2 client output file must resolve exactly once")
    candidate["files"] = retained
    return canonical_json_sha256(candidate)


def _predicate_matches(predicate: dict[str, Any], component: dict[str, Any]) -> bool:
    if predicate.get("always") is True:
        return True
    if "type_in" in predicate:
        return component.get("type") in set(predicate["type_in"])
    if "qualifier_contains" in predicate:
        return predicate["qualifier_contains"] in set(component.get("qualifiers") or [])
    raise AndroidChangeV2Error("unknown evidence conditional predicate")


def _validate_profile_registry(profiles: dict[str, Any]) -> None:
    if profiles.get("schema") != "akbs-component-evidence-profiles-v1":
        raise AndroidChangeV2Error("Android change v2 evidence profile identity differs")
    if (profiles.get("writer_activation") or {}).get("phase1_state") != "blocked":
        raise AndroidChangeV2Error("bundled Android change v2 writer state is not the frozen blocked state")
    registry = profiles.get("evidence_group_registry") or {}
    groups = registry.get("groups") or {}
    referenced = set(profiles.get("common_required_groups") or [])
    for values in (profiles.get("workflow_requirements") or {}).values():
        referenced.update(values)
    for layer in (profiles.get("layers") or {}).values():
        referenced.update(layer.get("required_groups") or [])
        for values in (layer.get("conditional_groups") or {}).values():
            referenced.update(values)
    if not isinstance(groups, dict) or set(groups) != referenced:
        raise AndroidChangeV2Error("evidence group registry does not exactly cover profile groups")
    claims: set[str] = set()
    for group_id, group in groups.items():
        expected = {
            "adapter_contract", "adapter_version", "claim",
            "allowed_adapter_results", "not_applicable",
        }
        if not isinstance(group, dict) or set(group) != expected:
            raise AndroidChangeV2Error(f"evidence group adapter contract differs: {group_id}")
        allowed = group.get("allowed_adapter_results")
        if not isinstance(allowed, list) or not allowed or len(allowed) != len(set(allowed)):
            raise AndroidChangeV2Error(f"evidence group adapter results differ: {group_id}")
        if not set(allowed).issubset({"PASS", "INFO", "NOT_APPLICABLE"}):
            raise AndroidChangeV2Error(f"evidence group adapter results differ: {group_id}")
        claim = group.get("claim")
        if not isinstance(claim, str) or not claim or claim in claims:
            raise AndroidChangeV2Error("evidence group client claims must be unique")
        claims.add(claim)
        if ("NOT_APPLICABLE" in allowed) != (group.get("not_applicable") is True):
            raise AndroidChangeV2Error(f"evidence group N/A contract differs: {group_id}")
    predicate_ids = {
        f"{layer_id}.{predicate_id}"
        for layer_id, layer in (profiles.get("layers") or {}).items()
        for predicate_id in (layer.get("conditional_groups") or {})
    }
    if set(profiles.get("conditional_predicates") or {}) != predicate_ids:
        raise AndroidChangeV2Error("evidence conditional predicates do not cover profile conditions")


def _required_groups(component: dict[str, Any], workflow: str, profiles: dict[str, Any]) -> set[str]:
    layer_id = component.get("layer")
    layer = (profiles.get("layers") or {}).get(layer_id)
    if not isinstance(layer, dict):
        raise AndroidChangeV2Error("component layer has no evidence profile")
    groups = set(profiles.get("common_required_groups") or [])
    groups.update((profiles.get("workflow_requirements") or {}).get(workflow) or [])
    groups.update(layer.get("required_groups") or [])
    predicates = profiles.get("conditional_predicates") or {}
    for predicate_id, values in (layer.get("conditional_groups") or {}).items():
        predicate = predicates.get(f"{layer_id}.{predicate_id}")
        if not isinstance(predicate, dict):
            raise AndroidChangeV2Error("component evidence predicate is missing")
        if _predicate_matches(predicate, component):
            groups.update(values)
    return groups


def source_package_key(package: dict[str, Any]) -> str:
    identity = package.get("identity") or {}
    member_alias = identity.get("member_alias")
    run_id = identity.get("run_id")
    if (
        not isinstance(member_alias, str)
        or not isinstance(run_id, str)
        or not re.fullmatch(r"[0-9]{8}-[0-9]{6}(?:-[A-Za-z0-9_.-]+)?", run_id)
    ):
        raise AndroidChangeV2Error("Android change v2 source package identity differs")
    return f"{run_id[:8]}/{member_alias}/{run_id}"


def _stream_regular_file(
    path: Path,
    *,
    destination: Path | None = None,
) -> tuple[str, int]:
    """Hash, and optionally copy, one regular file with bounded memory."""
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        source_fd = os.open(path, flags)
    except OSError as exc:
        raise AndroidChangeV2Error(
            f"cannot securely open Android change v2 archive entry: {path}: {exc}"
        ) from exc
    try:
        before = os.fstat(source_fd)
        if not stat.S_ISREG(before.st_mode):
            raise AndroidChangeV2Error(
                f"Android change v2 archive entry is not a regular file: {path}"
            )
        digest = hashlib.sha256()
        size = 0
        output = None
        try:
            if destination is not None:
                destination.parent.mkdir(parents=True, exist_ok=True)
                output = destination.open("xb")
            while True:
                chunk = os.read(source_fd, 1024 * 1024)
                if not chunk:
                    break
                if output is not None:
                    output.write(chunk)
                digest.update(chunk)
                size += len(chunk)
        except OSError as exc:
            raise AndroidChangeV2Error(
                f"cannot stream Android change v2 archive entry: {path}: {exc}"
            ) from exc
        finally:
            if output is not None:
                output.close()
        after = os.fstat(source_fd)
        try:
            rebound = os.stat(path, follow_symlinks=False)
        except OSError as exc:
            raise AndroidChangeV2Error(
                f"Android change v2 archive entry changed while reading: {path}: {exc}"
            ) from exc
        if (
            not os.path.samestat(before, after)
            or not os.path.samestat(before, rebound)
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or before.st_ctime_ns != after.st_ctime_ns
            or size != before.st_size
        ):
            raise AndroidChangeV2Error(
                f"Android change v2 archive entry changed while reading: {path}"
            )
        return digest.hexdigest(), size
    finally:
        os.close(source_fd)


def _archive_inventory(package_dir: Path) -> dict[str, tuple[str, int]]:
    inventory: dict[str, tuple[str, int]] = {}
    try:
        candidates = list(package_dir.rglob("*"))
    except OSError as exc:
        raise AndroidChangeV2Error(f"cannot enumerate Android change v2 package: {exc}") from exc
    for path in candidates:
        relative = path.relative_to(package_dir).as_posix()
        if path.is_symlink():
            raise AndroidChangeV2Error(f"Android change v2 archive contains a symlink: {relative}")
        try:
            mode = path.stat(follow_symlinks=False).st_mode
        except OSError as exc:
            raise AndroidChangeV2Error(f"cannot stat Android change v2 archive entry: {relative}: {exc}") from exc
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode):
            raise AndroidChangeV2Error(f"Android change v2 archive contains a special entry: {relative}")
        normalized = _normalized_archive_path(relative)
        if normalized in inventory:
            raise AndroidChangeV2Error(f"duplicate Android change v2 archive path: {normalized}")
        inventory[normalized] = _stream_regular_file(path)
    return inventory


def _validate_semantics(
    manifest_bytes: bytes,
    package: dict[str, Any],
    profile_bytes: bytes,
    profiles: dict[str, Any],
    output_bytes: bytes,
    outputs: dict[str, Any],
    inventory: dict[str, tuple[str, int]],
) -> dict[str, Any]:
    _validate_profile_registry(profiles)
    profile_sha = _sha256(profile_bytes)
    output_sha = _sha256(output_bytes)
    collections: dict[str, list[dict[str, Any]]] = {}
    for name in ("components", "sources", "files", "changes", "evidence"):
        rows = package.get(name)
        if not isinstance(rows, list) or not rows or any(not isinstance(row, dict) for row in rows):
            raise AndroidChangeV2Error(f"Android change v2 {name} must be non-empty objects")
        identifiers = [row.get("id") for row in rows]
        if any(not isinstance(item, str) for item in identifiers) or len(identifiers) != len(set(identifiers)):
            raise AndroidChangeV2Error(f"Android change v2 {name} IDs must be unique")
        collections[name] = rows
    components = {row["id"]: row for row in collections["components"]}
    sources = {row["id"]: row for row in collections["sources"]}
    files = {row["id"]: row for row in collections["files"]}
    evidence = {row["id"]: row for row in collections["evidence"]}
    if (package.get("subject") or {}).get("primary_component_id") not in components:
        raise AndroidChangeV2Error("Android change v2 primary component is unresolved")
    for source in sources.values():
        repo_path = source.get("repo_path")
        if repo_path != "." and (
            not isinstance(repo_path, str)
            or repo_path.startswith("/")
            or "\\" in repo_path
            or ".." in Path(repo_path).parts
        ):
            raise AndroidChangeV2Error("Android change v2 source path is unsafe")
    declared_paths: set[str] = set()
    for row in files.values():
        path = _normalized_archive_path(row.get("path"))
        if path == "manifest.json" or path in declared_paths:
            raise AndroidChangeV2Error("Android change v2 file path is unsafe or duplicated")
        declared_paths.add(path)
    changed_components: set[str] = set()
    for change in collections["changes"]:
        component_ids = set(change.get("component_ids") or [])
        if (
            not component_ids
            or not component_ids.issubset(components)
            or change.get("source_id") not in sources
            or change.get("file_id") not in files
            or files[change["file_id"]].get("role") != "patch"
        ):
            raise AndroidChangeV2Error("Android change v2 change references differ")
        changed_components.update(component_ids)
    for item in evidence.values():
        component_ids = set(item.get("component_ids") or [])
        if (
            not component_ids
            or not component_ids.issubset(components)
            or item.get("file_id") not in files
            or files[item["file_id"]].get("role") != "evidence"
        ):
            raise AndroidChangeV2Error("Android change v2 evidence references differ")
    qualification = package.get("qualification") or {}
    if qualification.get("profile_id") != profiles.get("schema") or qualification.get("profile_artifact_sha256") != profile_sha:
        raise AndroidChangeV2Error("Android change v2 qualification profile differs")
    output_file_id = qualification.get("client_adapter_outputs_file_id")
    output_file = files.get(output_file_id)
    if (
        not isinstance(output_file, dict)
        or output_file.get("role") != "metadata"
        or output_file.get("media_type") != "application/json"
        or output_file.get("sha256") != output_sha
        or output_file.get("size_bytes") != len(output_bytes)
    ):
        raise AndroidChangeV2Error("Android change v2 client adapter output file binding differs")
    bindings = qualification.get("component_evidence_bindings")
    if not isinstance(bindings, list) or not bindings:
        raise AndroidChangeV2Error("Android change v2 qualification bindings are missing")
    bound_components: set[str] = set()
    binding_evidence: dict[str, set[str]] = {}
    for binding in bindings:
        if not isinstance(binding, dict):
            raise AndroidChangeV2Error("Android change v2 qualification binding differs")
        component_id = binding.get("component_id")
        evidence_ids = set(binding.get("evidence_ids") or [])
        if (
            component_id not in components
            or component_id in bound_components
            or not evidence_ids
            or not evidence_ids.issubset(evidence)
            or any(component_id not in evidence[item].get("component_ids", []) for item in evidence_ids)
        ):
            raise AndroidChangeV2Error("Android change v2 qualification references differ")
        bound_components.add(component_id)
        binding_evidence[component_id] = evidence_ids
    if changed_components != set(components) or bound_components != set(components):
        raise AndroidChangeV2Error("every Android change v2 component must be changed and qualified")

    expected_document_fields = {
        "schema", "authority", "source_package_key", "qualification_input_sha256",
        "profile_id", "profile_artifact_sha256", "declared_package_status", "components",
    }
    document_components = outputs.get("components")
    if (
        set(outputs) != expected_document_fields
        or outputs.get("schema") != "akbs-client-adapter-outputs-v1"
        or outputs.get("authority") != "untrusted_client_input"
        or outputs.get("source_package_key") != source_package_key(package)
        or outputs.get("qualification_input_sha256") != qualification_input_sha256(package)
        or outputs.get("profile_id") != profiles.get("schema")
        or outputs.get("profile_artifact_sha256") != profile_sha
        or outputs.get("declared_package_status") != package.get("package_status")
        or not isinstance(document_components, list)
        or not document_components
    ):
        raise AndroidChangeV2Error("Android change v2 client adapter output document differs")
    outputs_by_component: dict[str, list[dict[str, Any]]] = {}
    for item in document_components:
        if (
            not isinstance(item, dict)
            or set(item) != {"component_id", "outputs"}
            or not isinstance(item.get("component_id"), str)
            or item.get("component_id") in outputs_by_component
            or not isinstance(item.get("outputs"), list)
            or not item["outputs"]
            or any(not isinstance(output, dict) for output in item["outputs"])
        ):
            raise AndroidChangeV2Error("Android change v2 client component outputs differ")
        outputs_by_component[item["component_id"]] = item["outputs"]
    if set(outputs_by_component) != set(components):
        raise AndroidChangeV2Error("client adapter outputs must cover every component exactly")
    registry = profiles["evidence_group_registry"]["groups"]
    output_contract = profiles["client_adapter_output_contract"]
    required_fields = set(output_contract["required_fields"])
    allowed_fields = required_fields | set(output_contract.get("additional_fields") or [])
    workflow = (package.get("workflow") or {}).get("contract")
    for component_id, component in components.items():
        component_outputs = outputs_by_component[component_id]
        group_ids = [item.get("group_id") for item in component_outputs]
        if len(group_ids) != len(set(group_ids)) or set(group_ids) != _required_groups(component, workflow, profiles):
            raise AndroidChangeV2Error("client adapter output groups do not satisfy the component profile")
        for output in component_outputs:
            if set(output) - allowed_fields or not required_fields.issubset(output):
                raise AndroidChangeV2Error("client adapter output fields differ")
            if (
                output.get("schema") != output_contract["schema"]
                or output.get("component_id") != component_id
                or output.get("source_evidence_id") not in binding_evidence[component_id]
            ):
                raise AndroidChangeV2Error("client adapter output binding differs")
            source = evidence[output["source_evidence_id"]]
            source_file = files[source["file_id"]]
            group = registry.get(output.get("group_id"))
            if not isinstance(group, dict) or (
                output.get("source_evidence_sha256") != source_file.get("sha256")
                or output.get("claim") not in set(source.get("declared_claims") or [])
                or output.get("adapter_contract") != group["adapter_contract"]
                or output.get("adapter_version") != group["adapter_version"]
                or output.get("claim") != group["claim"]
                or output.get("adapter_result") not in group["allowed_adapter_results"]
            ):
                raise AndroidChangeV2Error("client evidence adapter output differs")
            is_na = output.get("adapter_result") == "NOT_APPLICABLE"
            basis = output.get("not_applicable_basis")
            valid_basis = (
                isinstance(basis, dict)
                and set(basis) == {"basis", "limits"}
                and all(isinstance(basis[key], str) and basis[key].strip() for key in basis)
            )
            if is_na != valid_basis or (is_na and not group["not_applicable"]):
                raise AndroidChangeV2Error("client evidence N/A output differs")

    expected = {
        "manifest.json": (_sha256(manifest_bytes), len(manifest_bytes)),
        **{row["path"]: (row.get("sha256"), row.get("size_bytes")) for row in files.values()},
    }
    if inventory != expected:
        raise AndroidChangeV2Error("Android change v2 archive inventory or file integrity differs")
    return {
        "schema": "akbs-client-package-coherence-v1",
        "authority": "untrusted_client_input",
        "client_semantic_coherence_valid": True,
        "schema_validation_complete": True,
        "archive_inventory_binding_valid": True,
        "archive_extractor_validation_required": True,
        "server_qualified": False,
        "server_decision_required": "akbs-server-qualification-decision-v1",
        "profile_artifact_sha256": profile_sha,
        "client_adapter_outputs_file_sha256": output_sha,
        "qualification_input_sha256": qualification_input_sha256(package),
    }


def read_package(value: Path) -> dict[str, Any]:
    """Read and schema-check a v2 manifest without inspecting or writing payload files."""
    path, raw, package = _load_manifest(value)
    return {
        "status": "PASS",
        "operation": "read",
        "contract": "akbs-android-change-package-v2/2/android_change",
        "manifest": str(path),
        "manifest_sha256": _sha256(raw),
        "source_package_key": source_package_key(package),
        "package_status": package["package_status"],
        "primary_component_id": package["subject"]["primary_component_id"],
        "component_layers": sorted({item["layer"] for item in package["components"]}),
        "server_qualified": False,
    }


def check_package(value: Path) -> dict[str, Any]:
    """Strictly validate package schema, bindings, bytes, and archive inventory."""
    manifest_path, manifest_bytes, package = _load_manifest(value)
    package_dir = manifest_path.parent
    try:
        profile_bytes = PROFILE_PATH.read_bytes()
    except OSError as exc:
        raise AndroidChangeV2Error(f"cannot read bundled Android change v2 profile: {exc}") from exc
    if _sha256(profile_bytes) != CONTRACT_SHA256[PROFILE_PATH.name]:
        raise AndroidChangeV2Error("bundled Android change v2 contract hash differs: component-evidence-profiles.json")
    try:
        profiles = load_json_bytes(profile_bytes, label=str(PROFILE_PATH))
    except SchemaError as exc:
        _raise_schema(exc)
    output_id = (package.get("qualification") or {}).get("client_adapter_outputs_file_id")
    output_rows = [row for row in package.get("files", []) if isinstance(row, dict) and row.get("id") == output_id]
    if len(output_rows) != 1:
        raise AndroidChangeV2Error("Android change v2 client output file must resolve exactly once")
    output_relative = _normalized_archive_path(output_rows[0].get("path"))
    output_path = package_dir / output_relative
    if output_path.is_symlink() or not output_path.is_file():
        raise AndroidChangeV2Error("Android change v2 client adapter outputs file is missing or unsafe")
    try:
        output_bytes = output_path.read_bytes()
        outputs = load_json_bytes(output_bytes, label=str(output_path))
        validate_document(outputs, _load_contract(CLIENT_OUTPUT_SCHEMA_PATH))
    except (OSError, SchemaError) as exc:
        if isinstance(exc, SchemaError):
            _raise_schema(exc)
        raise AndroidChangeV2Error(f"cannot read client adapter outputs: {exc}") from exc
    # JSON evidence is parsed strictly before semantic evaluation.  It remains
    # evidence, not trusted adapter output or server qualification.
    evidence_file_ids = {
        item.get("file_id")
        for item in package.get("evidence", [])
        if isinstance(item, dict)
    }
    for row in package.get("files", []):
        if not isinstance(row, dict) or row.get("id") not in evidence_file_ids:
            continue
        relative = _normalized_archive_path(row.get("path"))
        if relative.endswith(".json") or row.get("media_type") == "application/json":
            evidence_path = package_dir / relative
            if evidence_path.is_symlink() or not evidence_path.is_file():
                raise AndroidChangeV2Error(f"declared JSON evidence is missing or unsafe: {relative}")
            try:
                load_json_bytes(evidence_path.read_bytes(), label=str(evidence_path))
            except (OSError, SchemaError) as exc:
                if isinstance(exc, SchemaError):
                    _raise_schema(exc)
                raise AndroidChangeV2Error(f"cannot read declared JSON evidence: {relative}: {exc}") from exc
    inventory = _archive_inventory(package_dir)
    coherence = _validate_semantics(
        manifest_bytes,
        package,
        profile_bytes,
        profiles,
        output_bytes,
        outputs,
        inventory,
    )
    return {
        "status": "PASS",
        "operation": "check",
        "contract": "akbs-android-change-package-v2/2/android_change",
        "package": str(package_dir),
        "manifest_sha256": _sha256(manifest_bytes),
        "archive_inventory_sha256": canonical_json_sha256(
            [[path, digest, size] for path, (digest, size) in sorted(inventory.items())]
        ),
        "source_package_key": source_package_key(package),
        "coherence": coherence,
    }


def _target_pending_root() -> Path:
    return require_safe_artifact_path(
        Path(default_codex_home()) / "artifacts" / "akbs-member-ops" / "android-change-v2" / "pending",
        purpose="Android change v2 pending root",
    )


def _secure_directory_flags() -> int:
    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    return flags


def _require_secure_dirfd_support() -> None:
    required = (os.open, os.mkdir, os.rename, os.stat)
    if (
        not hasattr(os, "O_DIRECTORY")
        or not hasattr(os, "O_NOFOLLOW")
        or any(function not in os.supports_dir_fd for function in required)
    ):
        raise AndroidChangeV2Error(
            "Android change v2 prepare requires no-follow directory descriptor support"
        )


def _open_real_child_directory(parent_fd: int, name: str) -> int:
    """Open or create one directory entry without following a symbolic link."""
    try:
        os.mkdir(name, mode=0o700, dir_fd=parent_fd)
    except FileExistsError:
        pass
    except OSError as exc:
        raise AndroidChangeV2Error(
            f"cannot create Android change v2 pending directory {name}: {exc}"
        ) from exc
    try:
        child_fd = os.open(name, _secure_directory_flags(), dir_fd=parent_fd)
    except OSError as exc:
        raise AndroidChangeV2Error(
            f"Android change v2 pending path is a symlink or not a real directory: {name}"
        ) from exc
    try:
        entry = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        opened = os.fstat(child_fd)
        if not stat.S_ISDIR(entry.st_mode) or not os.path.samestat(entry, opened):
            raise AndroidChangeV2Error(
                f"Android change v2 pending path is a symlink or not a real directory: {name}"
            )
    except BaseException:
        os.close(child_fd)
        raise
    return child_fd


def _open_directory_view(directory_fd: int) -> Path:
    """Return a descriptor-anchored path or fail closed before package writes."""
    for base in (Path("/proc/self/fd"), Path("/dev/fd")):
        candidate = base / str(directory_fd)
        if candidate.is_dir():
            return candidate
    raise AndroidChangeV2Error(
        "Android change v2 prepare cannot expose a descriptor-anchored pending directory"
    )


def _entry_matches_open_directory(parent_fd: int, name: str, child_fd: int) -> bool:
    try:
        entry = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except (FileNotFoundError, OSError):
        return False
    return stat.S_ISDIR(entry.st_mode) and os.path.samestat(entry, os.fstat(child_fd))


def _entry_matches_stat(parent_fd: int, name: str, expected: os.stat_result) -> bool:
    try:
        entry = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except (FileNotFoundError, OSError):
        return False
    return stat.S_ISDIR(entry.st_mode) and os.path.samestat(entry, expected)


def _entry_exists(parent_fd: int, name: str) -> bool:
    try:
        os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return True


def prepare_package(value: Path, *, pending_root: Path | None = None) -> dict[str, Any]:
    """Validate and byte-preserve a caller-produced v2 package in the target root."""
    check = check_package(value)
    source = _manifest_path(value).parent
    _manifest, manifest_bytes, package = _load_manifest(source)
    if _sha256(manifest_bytes) != check["manifest_sha256"] or source_package_key(package) != check["source_package_key"]:
        raise AndroidChangeV2Error("Android change v2 source changed after validation")
    source_inventory = _archive_inventory(source)
    source_inventory_sha256 = canonical_json_sha256(
        [[path, digest, size] for path, (digest, size) in sorted(source_inventory.items())]
    )
    if source_inventory_sha256 != check["archive_inventory_sha256"]:
        raise AndroidChangeV2Error("Android change v2 source inventory changed after validation")
    identity = package["identity"]
    _require_secure_dirfd_support()
    root_input = pending_root.expanduser() if pending_root is not None else _target_pending_root()
    if root_input.is_symlink():
        raise AndroidChangeV2Error("Android change v2 pending root cannot be a symbolic link")
    try:
        root_input.mkdir(parents=True, mode=0o700, exist_ok=True)
    except OSError as exc:
        raise AndroidChangeV2Error(f"cannot create Android change v2 pending root: {exc}") from exc
    if root_input.is_symlink() or not root_input.is_dir():
        raise AndroidChangeV2Error("Android change v2 pending root is not a real directory")
    root = root_input.resolve(strict=True)
    destination = root / identity["member_alias"] / identity["run_id"]
    source_resolved = source.resolve()
    try:
        root.relative_to(source_resolved)
    except ValueError:
        pass
    else:
        raise AndroidChangeV2Error("Android change v2 pending root cannot be inside the source package")
    try:
        source_resolved.relative_to(root)
    except ValueError:
        pass
    else:
        raise AndroidChangeV2Error("Android change v2 source package cannot be inside its pending root")
    root_fd = os.open(root, _secure_directory_flags())
    member_fd = -1
    temporary_fd = -1
    temporary_name = f".{identity['run_id']}.{secrets.token_hex(16)}"
    temporary: Path | None = None
    try:
        member_fd = _open_real_child_directory(root_fd, identity["member_alias"])
        if not _entry_matches_open_directory(root_fd, identity["member_alias"], member_fd):
            raise AndroidChangeV2Error("Android change v2 member directory changed during prepare")
        if _entry_exists(member_fd, identity["run_id"]):
            raise AndroidChangeV2Error(f"Android change v2 pending package already exists: {destination}")
        os.mkdir(temporary_name, mode=0o700, dir_fd=member_fd)
        temporary_fd = os.open(temporary_name, _secure_directory_flags(), dir_fd=member_fd)
        temporary = _open_directory_view(temporary_fd)
        for relative_text, (expected_sha, expected_size) in sorted(source_inventory.items()):
            relative = Path(relative_text)
            path = source / relative
            target = temporary / relative
            observed_sha, observed_size = _stream_regular_file(
                path,
                destination=target,
            )
            if observed_size != expected_size or observed_sha != expected_sha:
                raise AndroidChangeV2Error(f"Android change v2 source changed while copying: {relative_text}")
        if _archive_inventory(source) != source_inventory:
            raise AndroidChangeV2Error("Android change v2 source changed while copying")
        # Validate copied bytes before making the stable pending path visible.
        copied = check_package(temporary)
        if (
            copied["manifest_sha256"] != check["manifest_sha256"]
            or copied["archive_inventory_sha256"] != check["archive_inventory_sha256"]
            or copied["source_package_key"] != check["source_package_key"]
        ):
            raise AndroidChangeV2Error("Android change v2 copied package identity differs")
        if not _entry_matches_open_directory(root_fd, identity["member_alias"], member_fd):
            raise AndroidChangeV2Error("Android change v2 member directory changed during prepare")
        if _entry_exists(member_fd, identity["run_id"]):
            raise AndroidChangeV2Error(f"Android change v2 pending package already exists: {destination}")
        temporary_stat = os.fstat(temporary_fd)
        os.close(temporary_fd)
        temporary_fd = -1
        if not _entry_matches_stat(member_fd, temporary_name, temporary_stat):
            raise AndroidChangeV2Error("Android change v2 temporary directory changed during prepare")
        # Both names are resolved relative to the same no-follow directory
        # descriptor.  A swapped member_alias symlink therefore cannot redirect
        # publication outside the canonical pending root.
        os.rename(
            temporary_name,
            identity["run_id"],
            src_dir_fd=member_fd,
            dst_dir_fd=member_fd,
        )
        if (
            not _entry_matches_open_directory(root_fd, identity["member_alias"], member_fd)
            or not _entry_matches_stat(member_fd, identity["run_id"], temporary_stat)
        ):
            raise AndroidChangeV2Error("Android change v2 pending path changed during publication")
        published_copy = check_package(destination)
        if (
            published_copy["manifest_sha256"] != check["manifest_sha256"]
            or published_copy["archive_inventory_sha256"] != check["archive_inventory_sha256"]
            or published_copy["source_package_key"] != check["source_package_key"]
        ):
            raise AndroidChangeV2Error("Android change v2 published package identity differs")
        copied = published_copy
    except BaseException:
        # Never recursively delete a pathname after a prepare failure.  A
        # same-account process may have exchanged that entry after our last
        # identity check; preserving an inert private residue is safer than
        # deleting an unbound replacement.  Subsequent runs use a fresh random
        # staging name and the stable run_id remains fail-closed if occupied.
        raise
    finally:
        if temporary_fd >= 0:
            os.close(temporary_fd)
        if member_fd >= 0:
            os.close(member_fd)
        os.close(root_fd)
    return {
        "status": "PASS",
        "operation": "prepare",
        "contract": "akbs-android-change-package-v2/2/android_change",
        "package": str(destination),
        "source_package_key": check["source_package_key"],
        "bytes_preserved": True,
        "server_qualified": False,
        "writer": {**writer_status(), "scope": "submission_only"},
        "coherence": copied["coherence"],
    }


def writer_status() -> dict[str, Any]:
    """Local handling has not observed the server's runtime writer state."""
    return {
        "state": "server-controlled",
        "reason_code": "android_change_v2_server_state_unchecked",
        "message": "Local validation does not query or enable the server writer; submit is a separate operation.",
        "server_qualified": False,
        "v1_fallback": False,
        "network_requests": 0,
        "files_written": 0,
    }
