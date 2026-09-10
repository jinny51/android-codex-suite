"""Offline capture-2.1 to canonical Android-change-v2 materialization.

The materializer evaluates only the client qualification contract.  Its
outputs remain untrusted client input and never imply server qualification or
perform a network request.
"""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from akbs_member_ops.artifact_paths import require_safe_artifact_path
from akbs_member_ops.member_config import default_codex_home

from .capture_adapter import (
    open_materializable_capture_files,
    read_materializable_capture,
)
from .schema import SchemaError, load_json_bytes, validate_document
from .validation import (
    AndroidChangeV2Error,
    CONTRACT_SHA256,
    PROFILE_PATH,
    _load_contract,
    _required_groups,
    _validate_profile_registry,
    canonical_json_sha256,
    check_package,
    prepare_package,
    qualification_input_sha256,
    writer_status,
)


PLUGIN_ROOT = Path(__file__).resolve().parents[3]
QUALIFICATION_PACK_PATH = (
    PLUGIN_ROOT / "contracts" / "incoming" / "v2" / "qualification-contract-pack-v2.json"
)
ADAPTER_INPUT_SCHEMA_PATH = QUALIFICATION_PACK_PATH.with_name(
    "qualification-adapter-input-v2.schema.json"
)
ADAPTER_INPUTS_SCHEMA_PATH = QUALIFICATION_PACK_PATH.with_name(
    "qualification-adapter-inputs-v2.schema.json"
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
GIT_OID_RE = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
ALIAS_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")
TOKEN_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
SEARCH_DECISIONS = {"reuse", "adapt", "reference_only", "not_applicable", "not_found"}
# Released packs retain their identity and exact bytes on upload retry. Their
# runtime family is not retroactively reinterpreted as equivalent verification.
LEGACY_QUALIFICATION_PACK_SHA256 = "ac064f0c6215ff9471b3b7c6ab8f9dab9fcec066b112cadfbb334985cd09b1a4"
SEVEN_LAYER_QUALIFICATION_PACK_SHA256 = "f4d909382059f6db3a2007d1c6e9ab21ee08b2398a7de07c2292bf0826d4cb54"


def _fail(code: str, detail: str) -> None:
    raise AndroidChangeV2Error(f"{code}: {detail}")


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _write_bytes(root: Path, relative: str, raw: bytes) -> None:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)


def _load_qualification_contract() -> tuple[
    bytes,
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
]:
    try:
        raw = QUALIFICATION_PACK_PATH.read_bytes()
    except OSError as exc:
        _fail("qualification_contract_unavailable", str(exc))
    if hashlib.sha256(raw).hexdigest() != CONTRACT_SHA256["qualification-contract-pack-v2.json"]:
        _fail("qualification_contract_hash_mismatch", QUALIFICATION_PACK_PATH.name)
    try:
        pack = load_json_bytes(raw, label=str(QUALIFICATION_PACK_PATH))
        profiles = _load_contract(PROFILE_PATH)
        item_schema = _load_contract(ADAPTER_INPUT_SCHEMA_PATH)
        collection_schema = _load_contract(ADAPTER_INPUTS_SCHEMA_PATH)
    except SchemaError as exc:
        _fail("qualification_contract_invalid", str(exc))
    _validate_profile_registry(profiles)
    legacy_groups = profiles["evidence_group_registry"]["groups"]
    expected_pack_fields = {
        "schema",
        "contract_version",
        "applies_to",
        "evaluation_scope",
        "all_components_must_qualify",
        "authority",
        "capability",
        "input_binding",
        "shape_families",
        "not_applicable",
        "groups",
    }
    if (
        set(pack) != expected_pack_fields
        or pack.get("schema") != "akbs-qualification-contract-pack-v2"
        or pack.get("contract_version") != "2"
        or (pack.get("applies_to") or {}).get("legacy_profile_sha256")
        != CONTRACT_SHA256[PROFILE_PATH.name]
        or set(pack.get("groups") or {}) != set(legacy_groups)
        or len(legacy_groups) != 37
    ):
        _fail("qualification_contract_invalid", "taxonomy or legacy profile binding differs")
    if pack.get("authority") != {
        "client_adapter_outputs": "untrusted_client_input",
        "server_qualification_decision_owner": "akbs_server",
        "server_decision_contract": "akbs-server-qualification-decision-v1",
    }:
        _fail("qualification_contract_invalid", "qualification authority differs")
    input_binding = pack.get("input_binding") or {}
    expected_input_binding = {
        "item_schema": {
            "file": ADAPTER_INPUT_SCHEMA_PATH.name,
            "schema": "akbs-qualification-adapter-input-v2",
            "version": "2",
            "sha256": CONTRACT_SHA256[ADAPTER_INPUT_SCHEMA_PATH.name],
        },
        "collection_schema": {
            "file": ADAPTER_INPUTS_SCHEMA_PATH.name,
            "schema": "akbs-qualification-adapter-inputs-v2",
            "version": "2",
            "sha256": CONTRACT_SHA256[ADAPTER_INPUTS_SCHEMA_PATH.name],
        },
        "evidence_component_membership_required": True,
        "evidence_sha256_required": True,
        "capture_manifest_sha256_required": True,
        "capture_archive_inventory_sha256_required": True,
        "unknown_fields": "reject",
    }
    if input_binding != expected_input_binding:
        _fail("qualification_contract_invalid", "adapter input schema binding differs")
    if (
        item_schema.get("$ref") != "#/$defs/adapterInput"
        or item_schema.get("$defs") != collection_schema.get("$defs")
    ):
        _fail("qualification_contract_invalid", "adapter item schema copies differ")
    capability = pack.get("capability") or {}
    if (
        set(capability.get("taxonomy_layers") or {})
        != {"application", "platform", "native", "hal", "kernel", "device", "build"}
        or set(capability.get("executable_layers") or {})
        != {"application", "platform", "native", "hal", "kernel", "device", "build"}
        or capability.get("disabled_layers") != {}
    ):
        _fail("qualification_contract_invalid", "layer capability differs")
    families = pack.get("shape_families")
    if not isinstance(families, dict) or not families:
        _fail("qualification_contract_invalid", "shape family registry is missing")
    for group_id, legacy in legacy_groups.items():
        rule = (pack.get("groups") or {}).get(group_id)
        expected_keys = {
            "adapter_contract",
            "adapter_version",
            "claim",
            "input_contract",
            "shape_family",
            "accepted_source_kinds",
            "accepted_assertion_ids",
            "source_cardinality",
            "allowed_results",
            "not_applicable",
        }
        if (
            not isinstance(rule, dict)
            or set(rule) != expected_keys
            or rule["adapter_contract"] != legacy["adapter_contract"]
            or rule["adapter_version"] != legacy["adapter_version"]
            or rule["claim"] != legacy["claim"]
            or rule["allowed_results"] != legacy["allowed_adapter_results"]
            or rule["not_applicable"] is not legacy["not_applicable"]
            or rule["input_contract"]
            != {"schema": "akbs-qualification-adapter-input-v2", "version": "2"}
            or rule["shape_family"] not in families
            or rule["source_cardinality"] != "exactly_one"
            or not isinstance(rule["accepted_source_kinds"], list)
            or not rule["accepted_source_kinds"]
            or (
                rule["shape_family"] == "structured_assertion"
                and (
                    not isinstance(rule["accepted_assertion_ids"], list)
                    or not rule["accepted_assertion_ids"]
                )
            )
            or (
                rule["shape_family"] != "structured_assertion"
                and rule["accepted_assertion_ids"] != []
            )
        ):
            _fail("qualification_contract_invalid", f"closed group rule differs: {group_id}")
    return raw, pack, profiles, item_schema, collection_schema


def _nonempty_list(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(
        isinstance(item, (str, dict)) and bool(item) for item in value
    )


def _component_changed(payload: dict[str, Any], component_id: str) -> bool:
    repositories = payload.get("repositories")
    if not isinstance(repositories, list):
        return False
    scoped_files: list[str] = []
    for row in repositories:
        if not isinstance(row, dict) or component_id not in (row.get("component_ids") or []):
            continue
        values = row.get("modified_files")
        if isinstance(values, list):
            scoped_files.extend(item for item in values if isinstance(item, str) and item)
    return bool(scoped_files) and _nonempty_list(payload.get("modified_files"))


def _component_assertion(
    payload: dict[str, Any],
    *,
    component_id: str,
    accepted_assertion_ids: set[str],
) -> tuple[str, dict[str, str] | None] | None:
    if (
        set(payload)
        - {"kind", "result", "component_ids", "assertions", "summary", "message"}
        or payload.get("kind") != "component_assertion"
        or payload.get("result") != "INFO"
    ):
        return None
    component_ids = payload.get("component_ids")
    assertions = payload.get("assertions")
    if (
        not isinstance(component_ids, list)
        or not component_ids
        or len(component_ids) != len(set(component_ids))
        or not isinstance(assertions, list)
        or not assertions
    ):
        return None
    observed_components: set[str] = set()
    observed_pairs: set[tuple[str, str]] = set()
    matches: list[dict[str, Any]] = []
    for row in assertions:
        if not isinstance(row, dict):
            return None
        row_component = row.get("component_id")
        assertion_id = row.get("assertion_id")
        result = row.get("result")
        if (
            row_component not in component_ids
            or not isinstance(assertion_id, str)
            or not TOKEN_RE.fullmatch(assertion_id)
            or result not in {"PASS", "FAIL", "INFO", "NOT_APPLICABLE"}
        ):
            return None
        pair = (row_component, assertion_id)
        if pair in observed_pairs:
            return None
        observed_pairs.add(pair)
        observed_components.add(row_component)
        if result == "NOT_APPLICABLE":
            if set(row) != {
                "component_id", "assertion_id", "result", "basis", "limits"
            } or not all(
                isinstance(row.get(key), str) and row[key].strip()
                for key in ("basis", "limits")
            ):
                return None
        else:
            observations = row.get("observations")
            if (
                set(row)
                != {"component_id", "assertion_id", "result", "observations"}
                or not isinstance(observations, list)
                or not observations
                or any(not isinstance(item, str) or not item.strip() for item in observations)
            ):
                return None
        if (
            row_component == component_id
            and assertion_id in accepted_assertion_ids
        ):
            matches.append(row)
    if observed_components != set(component_ids):
        return None
    if len(matches) != 1:
        return None
    row = matches[0]
    result = row.get("result")
    basis: dict[str, str] | None = None
    if result == "NOT_APPLICABLE":
        basis = {"basis": row["basis"], "limits": row["limits"]}
    return result, basis


def _evaluate_shape(
    family: str,
    payload: dict[str, Any],
    *,
    component_id: str,
    source_result: str,
    accepted_assertion_ids: set[str],
) -> tuple[str, dict[str, str] | None] | None:
    if family == "source_integrity":
        return (
            ("PASS", None)
            if source_result == "INFO" and _component_changed(payload, component_id)
            else None
        )
    if family == "diff_facts":
        return (
            ("INFO", None)
            if source_result == "INFO" and _nonempty_list(payload.get("modified_files"))
            else None
        )
    if family == "risk":
        return (
            ("INFO", None)
            if source_result == "INFO"
            and all(_nonempty_list(payload.get(field)) for field in ("risk_areas", "basis", "limits"))
            else None
        )
    if family == "policy":
        return (
            ("PASS", None)
            if source_result == "PASS"
            and payload.get("result") == "PASS"
            and payload.get("errors") == []
            else None
        )
    if family == "verification":
        return (
            ("PASS", None)
            if source_result == "PASS"
            and payload.get("result") == "PASS"
            and payload.get("contract_version") == "akbs-verification-evidence/v2"
            and payload.get("scope") == "feature"
            and payload.get("requirement_acceptance") == "accepted"
            else None
        )
    if family == "regression":
        return (
            ("PASS", None)
            if source_result == "PASS"
            and payload.get("result") == "PASS"
            and _nonempty_list(payload.get("health_checks"))
            else None
        )
    if family == "rollback":
        return (
            ("PASS", None)
            if source_result == "PASS"
            and payload.get("result") == "PASS"
            and payload.get("kind") == "rollback_plan"
            and isinstance(payload.get("plan"), str)
            and payload["plan"].strip()
            else None
        )
    if family == "search":
        if (
            source_result in {"PASS", "INFO"}
            and payload.get("result") == source_result
            and payload.get("searched") is True
            and payload.get("decision") in SEARCH_DECISIONS
        ):
            return source_result, None
        return None
    if family == "import_provenance":
        rows = payload.get("repository_artifacts")
        scoped = (
            [
                row
                for row in rows
                if isinstance(row, dict)
                and component_id in (row.get("component_ids") or [])
            ]
            if isinstance(rows, list)
            else []
        )
        valid = source_result == "PASS" and payload.get("result") == "PASS" and bool(scoped)
        if valid:
            for row in scoped:
                if (
                    not isinstance(row.get("patch_artifact_sha256"), str)
                    or not SHA256_RE.fullmatch(row["patch_artifact_sha256"])
                ):
                    valid = False
                    break
        return ("PASS", None) if valid else None
    if family == "build":
        return (
            ("PASS", None)
            if source_result == "PASS"
            and payload.get("result") == "PASS"
            and _nonempty_list(payload.get("build"))
            else None
        )
    if family == "runtime_or_equivalent":
        if source_result != "PASS" or payload.get("result") != "PASS":
            return None
        if _nonempty_list(payload.get("steps")):
            return "PASS", None
        if (
            payload.get("contract_version") == "akbs-verification-evidence/v2"
            and payload.get("scope") == "feature"
            and payload.get("requirement_acceptance") == "accepted"
            and payload.get("method") == "equivalent"
            and all(
                isinstance(payload.get(field), str) and payload[field].strip()
                for field in ("equivalent_type", "reason", "remaining_risk")
            )
            and _nonempty_list(payload.get("coverage"))
        ):
            return "PASS", None
        return None
    if family == "runtime":
        return (
            ("PASS", None)
            if source_result == "PASS"
            and payload.get("result") == "PASS"
            and _nonempty_list(payload.get("steps"))
            else None
        )
    if family == "structured_assertion":
        if source_result != "INFO":
            return None
        return _component_assertion(
            payload,
            component_id=component_id,
            accepted_assertion_ids=accepted_assertion_ids,
        )
    _fail("qualification_contract_invalid", f"unknown shape family: {family}")
    raise AssertionError("unreachable")


def _qualification_outputs(
    snapshot: dict[str, Any],
    pack: dict[str, Any],
    profiles: dict[str, Any],
    item_schema: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, list[str]]]:
    manifest = snapshot["manifest"]
    details = snapshot["details"]
    evidence = {row["id"]: row for row in details["evidence"]}
    payloads = details["evidence_payloads"]
    bindings = {
        row["component_id"]: row for row in details["qualification_bindings"]
    }
    executable = set(pack["capability"]["executable_layers"])
    components: list[dict[str, Any]] = []
    adapter_inputs: list[dict[str, Any]] = []
    derived_claims: dict[str, list[str]] = {}
    for component in details["components"]:
        component_id = component["id"]
        if component["layer"] not in executable:
            _fail(
                "layer_not_enabled",
                f"{component_id} uses frozen but non-executable layer {component['layer']}",
            )
        required = sorted(
            _required_groups(component, manifest["workflow_contract"], profiles)
        )
        available_ids = set(bindings[component_id]["evidence_ids"])
        outputs: list[dict[str, Any]] = []
        for group_id in required:
            rule = pack["groups"][group_id]
            family_contract = pack["shape_families"][rule["shape_family"]]
            candidates = [
                row
                for row in evidence.values()
                if row["id"] in available_ids
                and component_id in row["component_ids"]
                and row["kind"] in rule["accepted_source_kinds"]
            ]
            evaluated: list[tuple[dict[str, Any], str, dict[str, str] | None]] = []
            for row in candidates:
                payload = payloads[row["id"]]
                if any(
                    field not in payload
                    for field in family_contract["required_payload_fields"]
                ):
                    continue
                value = _evaluate_shape(
                    rule["shape_family"],
                    payload,
                    component_id=component_id,
                    source_result=row["result"],
                    accepted_assertion_ids=set(rule["accepted_assertion_ids"]),
                )
                if value is not None:
                    evaluated.append((row, value[0], value[1]))
            if len(evaluated) != 1:
                _fail(
                    "qualification_evidence_not_closed",
                    f"{component_id}.{group_id} has {len(evaluated)} qualifying evidence rows",
                )
            source, result, basis = evaluated[0]
            source_claims = derived_claims.setdefault(source["id"], [])
            if rule["claim"] not in source_claims:
                source_claims.append(rule["claim"])
            if result not in rule["allowed_results"]:
                _fail(
                    "qualification_result_not_allowed",
                    f"{component_id}.{group_id} derived {result}",
                )
            if result == "NOT_APPLICABLE":
                if not rule["not_applicable"] or group_id in pack["not_applicable"]["mandatory_groups"]:
                    _fail(
                        "not_applicable_forbidden",
                        f"{component_id}.{group_id} cannot be N/A",
                    )
            elif basis is not None:
                _fail("not_applicable_basis_mismatch", f"{component_id}.{group_id}")
            source_facts = snapshot["inventory"][source["path"]]
            adapter_input = {
                "schema": "akbs-qualification-adapter-input-v2",
                "contract_version": "2",
                "group_id": group_id,
                "component": component,
                "workflow_contract": manifest["workflow_contract"],
                "capture_binding": {
                    "manifest_sha256": snapshot["manifest_sha256"],
                    "archive_inventory_sha256": snapshot["archive_inventory_sha256"],
                },
                "evidence": {
                    "id": source["id"],
                    "kind": source["kind"],
                    "component_ids": source["component_ids"],
                    "contract": source["contract"],
                    "sha256": source_facts["sha256"],
                    "result": source["result"],
                    "payload": payloads[source["id"]],
                },
            }
            try:
                validate_document(adapter_input, item_schema)
            except SchemaError as exc:
                _fail("qualification_adapter_input_invalid", str(exc))
            adapter_inputs.append(adapter_input)
            output = {
                "schema": "akbs-client-adapter-output-v1",
                "component_id": component_id,
                "group_id": group_id,
                "source_evidence_id": source["id"],
                "source_evidence_sha256": source_facts["sha256"],
                "adapter_contract": rule["adapter_contract"],
                "adapter_version": rule["adapter_version"],
                "claim": rule["claim"],
                "adapter_result": result,
            }
            if basis is not None:
                output["not_applicable_basis"] = basis
            outputs.append(output)
        components.append({"component_id": component_id, "outputs": outputs})
    return components, adapter_inputs, derived_claims


def _safe_remote(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if not text or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://[^/]*@", text):
        return ""
    return text


def _canonical_sources(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    for repository in manifest["git_repositories"]:
        git = repository["git"]
        head = str(git.get("head") or "")
        base = str(git.get("base") or head)
        source: dict[str, Any] = {
            "id": repository["id"],
            "repo_path": repository["repo_path"],
        }
        if GIT_OID_RE.fullmatch(head) and GIT_OID_RE.fullmatch(base):
            source.update(kind="git", base_revision=base, head_revision=head)
            branch = str(git.get("branch") or "").strip()
            remote = _safe_remote(git.get("remote"))
            if branch:
                source["branch"] = branch
            if remote:
                source["remote_url"] = remote
        else:
            digest = str(git.get("patch_artifact_sha256") or "")
            if not SHA256_RE.fullmatch(digest):
                _fail(
                    "source_revision_unresolved",
                    f"{repository['id']} has neither a full Git OID nor a hash-bound patch artifact",
                )
            source.update(
                kind="external",
                external_reference=f"sha256:{digest}",
            )
        sources.append(source)
    return sources


def _copy_capture_file(
    snapshot: dict[str, Any],
    copy_file: Any,
    target_root: Path,
    relative: str,
) -> dict[str, Any]:
    normalized = Path(relative)
    if normalized.is_absolute() or ".." in normalized.parts:
        _fail("capture_path_unsafe", relative)
    facts = snapshot["inventory"].get(relative)
    if not isinstance(facts, dict):
        _fail("capture_file_missing", relative)
    target = target_root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    copied = copy_file(relative, target)
    if copied != {"sha256": facts["sha256"], "size_bytes": facts["size_bytes"]}:
        _fail("capture_file_changed", relative)
    return copied


def _artifact_id(kind: str, source_id: str) -> str:
    """Separate capture labels from the shorter canonical identifier namespace."""
    return f"{kind}-{hashlib.sha256(source_id.encode('utf-8')).hexdigest()}"


def _capture_artifact_ids(capture: dict[str, Any]) -> dict[str, Any]:
    return {
        "algorithm": "type-prefixed-sha256-utf8-source-id",
        "patches": [
            {
                "source_id": patch["id"],
                "change_id": _artifact_id("patch", patch["id"]),
                "file_id": _artifact_id("patch-file", patch["id"]),
                "path": patch["path"],
            }
            for patch in capture["patches"]
        ],
        "evidence": [
            {
                "source_id": item["id"],
                "evidence_id": item["id"],
                "file_id": _artifact_id("evidence-file", item["id"]),
                "path": item["path"],
            }
            for item in capture["evidence"]
        ],
    }


def _copy_bound_capture_files(
    snapshot: dict[str, Any],
    copy_file: Any,
    package_root: Path,
    derived_claims: dict[str, list[str]],
) -> tuple[
    list[dict[str, Any]],
    dict[str, str],
    dict[str, str],
    list[dict[str, Any]],
]:
    capture = snapshot["manifest"]
    files: list[dict[str, Any]] = []
    readme_facts = _copy_capture_file(
        snapshot, copy_file, package_root, capture["readme"]
    )
    files.append(
        {
            "id": "readme",
            "role": "readme",
            "path": capture["readme"],
            "sha256": readme_facts["sha256"],
            "size_bytes": readme_facts["size_bytes"],
            "media_type": "text/markdown",
        }
    )

    patch_file_ids: dict[str, str] = {}
    for patch in capture["patches"]:
        facts = _copy_capture_file(
            snapshot, copy_file, package_root, patch["path"]
        )
        file_id = _artifact_id("patch-file", patch["id"])
        patch_file_ids[patch["id"]] = file_id
        files.append(
            {
                "id": file_id,
                "role": "patch",
                "path": patch["path"],
                "sha256": facts["sha256"],
                "size_bytes": facts["size_bytes"],
                "media_type": "text/x-diff",
            }
        )

    evidence_file_ids: dict[str, str] = {}
    canonical_evidence: list[dict[str, Any]] = []
    for item in capture["evidence"]:
        facts = _copy_capture_file(
            snapshot, copy_file, package_root, item["path"]
        )
        file_id = _artifact_id("evidence-file", item["id"])
        evidence_file_ids[item["id"]] = file_id
        files.append(
            {
                "id": file_id,
                "role": "evidence",
                "path": item["path"],
                "sha256": facts["sha256"],
                "size_bytes": facts["size_bytes"],
                "media_type": "application/json",
            }
        )
        result = {"SKIPPED": "NOT_RUN"}.get(item["result"], item["result"])
        row: dict[str, Any] = {
            "id": item["id"],
            "kind": item["kind"],
            "component_ids": item["component_ids"],
            "file_id": file_id,
            "scope": {
                "component": "component",
                "package": "package",
                "feature": "feature",
                "change": "feature",
            }.get(item.get("scope"), "feature"),
            "result": result,
            "contract": item["contract"],
            "declared_claims": list(
                dict.fromkeys(
                    [*item["declared_claims"], *derived_claims.get(item["id"], [])]
                )
            ),
            "summary": item["summary"],
        }
        if "not_applicable_basis" in item:
            row["not_applicable_basis"] = item["not_applicable_basis"]
        canonical_evidence.append(row)
    return files, patch_file_ids, evidence_file_ids, canonical_evidence


def _run_id(snapshot: dict[str, Any]) -> str:
    raw = str(snapshot["manifest"]["created_at"])
    try:
        created = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        _fail("capture_created_at_invalid", raw)
    return (
        created.strftime("%Y%m%d-%H%M%S")
        + "-"
        + snapshot["archive_inventory_sha256"][:12]
    )


def _canonical_created_at(value: Any) -> str:
    raw = str(value)
    try:
        created = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        _fail("capture_created_at_invalid", raw)
    if created.tzinfo is None:
        created = created.astimezone()
    return created.isoformat(timespec="seconds")


def _target_adapted_root() -> Path:
    return require_safe_artifact_path(
        Path(default_codex_home())
        / "artifacts"
        / "akbs-member-ops"
        / "android-change-v2"
        / "adapted",
        purpose="Android change v2 adapted root",
    )


def _validate_existing_capture_bindings(
    package: dict[str, Any],
    snapshot: dict[str, Any],
    derived_claims: dict[str, list[str]],
) -> bool:
    """Bind an existing package to the capture without requiring current opaque IDs."""
    capture = snapshot["manifest"]
    files = {row["id"]: row for row in package["files"]}
    files_by_path = {row["path"]: row for row in package["files"]}
    expected_roles = {
        capture["readme"]: "readme",
        **{row["path"]: "patch" for row in capture["patches"]},
        **{row["path"]: "evidence" for row in capture["evidence"]},
        "metadata/qualification-adapter-inputs.json": "metadata",
        "metadata/client-adapter-outputs.json": "metadata",
    }
    if {path: row["role"] for path, row in files_by_path.items()} != expected_roles:
        _fail("idempotency_conflict", "existing capture file paths or roles differ")
    for path, role in expected_roles.items():
        if role == "metadata":
            continue
        expected = snapshot["inventory"][path]
        if any(files_by_path[path][key] != expected[key] for key in ("sha256", "size_bytes")):
            _fail("idempotency_conflict", f"existing capture file bytes differ: {path}")
    changes = [
        {
            "path": files[row["file_id"]]["path"],
            "component_ids": row["component_ids"],
            "repository_id": row["source_id"],
            "format": row["format"],
        }
        for row in package["changes"]
    ]
    expected_changes = [
        {
            "path": row["path"],
            "component_ids": row["component_ids"],
            "repository_id": row["repository_id"],
            "format": "git_diff",
        }
        for row in capture["patches"]
    ]
    evidence = [
        {
            **{key: value for key, value in row.items() if key != "file_id"},
            "path": files[row["file_id"]]["path"],
        }
        for row in package["evidence"]
    ]
    expected_evidence = []
    for item in capture["evidence"]:
        row = {
            "id": item["id"],
            "kind": item["kind"],
            "component_ids": item["component_ids"],
            "path": item["path"],
            "scope": {
                "component": "component", "package": "package",
                "feature": "feature", "change": "feature",
            }.get(item.get("scope"), "feature"),
            "result": {"SKIPPED": "NOT_RUN"}.get(item["result"], item["result"]),
            "contract": item["contract"],
            "declared_claims": list(dict.fromkeys(
                [*item["declared_claims"], *derived_claims.get(item["id"], [])]
            )),
            "summary": item["summary"],
        }
        if "not_applicable_basis" in item:
            row["not_applicable_basis"] = item["not_applicable_basis"]
        expected_evidence.append(row)
    expected_workflow = {
        "contract": capture["workflow_contract"],
        "implementation_origins": [capture["implementation_origin"]],
        "capture_tool": {"id": "android-patch-capture", "version": "2.1"},
    }
    if capture["workflow_contract"] in {"manual_import", "historical_import"}:
        imports = [row for row in capture["evidence"] if row["kind"] == "import_provenance"]
        if len(imports) != 1:
            _fail("idempotency_conflict", "existing import provenance is not unique")
        expected_workflow["import_provenance_file_id"] = files_by_path[imports[0]["path"]]["id"]
    expected_subject = {
        "title": capture["change_id"],
        "summary": capture["summary"],
        "feature_key": capture["change_id"],
        "primary_component_id": capture["primary_component_id"],
        "target": {
            "project": str(capture["project"]).lower().replace("_", "-"),
            "platform": capture["platform"],
            "android_version": capture["android_version"],
        },
    }
    legacy_subject = {
        **expected_subject,
        "target": {
            **expected_subject["target"],
            "platform": capture["platform_token"],
        },
    }
    legacy_platform_token = package["subject"] == legacy_subject
    if (
        package["components"] != capture["components"]
        or package["sources"] != _canonical_sources(capture)
        or changes != expected_changes
        or evidence != expected_evidence
        or package["identity"]["created_at"] != _canonical_created_at(capture["created_at"])
        or package["subject"] not in (expected_subject, legacy_subject)
        or package["workflow"] != expected_workflow
        or package["qualification"]["component_evidence_bindings"] != [
            {"component_id": row["component_id"], "evidence_ids": row["evidence_ids"]}
            for row in capture["qualification_bindings"]
        ]
    ):
        _fail("idempotency_conflict", "existing package facts differ from the capture")
    return legacy_platform_token


def _existing_result(
    destination: Path,
    *,
    root: Path,
    member_alias: str,
    run_id: str,
    snapshot: dict[str, Any],
    contract_sha256: str,
    expected_adapter_inputs_raw: bytes,
    expected_component_outputs: list[dict[str, Any]],
    derived_claims: dict[str, list[str]],
) -> dict[str, Any] | None:
    member_root = root / member_alias
    for label, path in (
        ("adapted root", root),
        ("member root", member_root),
        ("destination", destination),
    ):
        if path.is_symlink():
            _fail("idempotency_path_unsafe", f"{label} is a symbolic link: {path}")
    if not destination.exists() and not destination.is_symlink():
        return None
    try:
        checked = check_package(destination)
        manifest_raw = (destination / "manifest.json").read_bytes()
        package = load_json_bytes(manifest_raw, label=str(destination / "manifest.json"))
    except (AndroidChangeV2Error, OSError, SchemaError) as exc:
        _fail("idempotency_conflict", str(exc))
    if hashlib.sha256(manifest_raw).hexdigest() != checked["manifest_sha256"]:
        _fail("idempotency_conflict", f"manifest changed after validation: {destination}")
    extension = (package.get("extensions") or {}).get("akbs.android/capture") or {}
    qualification = (package.get("extensions") or {}).get("akbs.android/qualification") or {}
    if not isinstance(extension, dict) or not isinstance(qualification, dict):
        _fail("idempotency_conflict", "existing capture or qualification extension differs")
    existing_contract_sha256 = qualification.get("contract_sha256")
    if existing_contract_sha256 in {
        LEGACY_QUALIFICATION_PACK_SHA256, SEVEN_LAYER_QUALIFICATION_PACK_SHA256,
    }:
        if existing_contract_sha256 == LEGACY_QUALIFICATION_PACK_SHA256 and any(
            component["layer"] not in {"application", "platform"}
            for component in snapshot["details"]["components"]
        ):
            _fail("idempotency_conflict", "legacy qualification does not cover this component")
        # Old runtime contracts required actual steps. Do not validate an old
        # package under today's broader equivalent-verification semantics.
        old_pack = json.loads(QUALIFICATION_PACK_PATH.read_bytes())
        for rule in old_pack["groups"].values():
            if rule["shape_family"] == "runtime_or_equivalent":
                rule["shape_family"] = "runtime"
        _, _, old_profiles, old_item_schema, _ = _load_qualification_contract()
        old_outputs, _, old_claims = _qualification_outputs(
            snapshot, old_pack, old_profiles, old_item_schema,
        )
        if old_outputs != expected_component_outputs or old_claims != derived_claims:
            _fail("idempotency_conflict", "existing qualification semantics differ")
        expected_inputs = load_json_bytes(
            expected_adapter_inputs_raw, label="expected qualification adapter inputs"
        )
        expected_inputs["qualification_contract_sha256"] = existing_contract_sha256
        expected_adapter_inputs_raw = _json_bytes(expected_inputs)
    elif existing_contract_sha256 != contract_sha256:
        _fail("idempotency_conflict", "existing qualification contract is not supported")
    legacy_platform_token = _validate_existing_capture_bindings(
        package, snapshot, derived_claims
    )
    if legacy_platform_token != (
        checked["platform_compatibility"]["mode"] == "legacy_versioned_platform_token"
    ):
        _fail("idempotency_conflict", "existing platform compatibility differs")
    output_file = next(
        row for row in package["files"]
        if row["id"] == package["qualification"]["client_adapter_outputs_file_id"]
    )
    try:
        output_raw = (destination / output_file["path"]).read_bytes()
        outputs = load_json_bytes(output_raw, label="existing client adapter outputs")
    except (OSError, SchemaError) as exc:
        _fail("idempotency_conflict", str(exc))
    if (
        output_file["path"] != "metadata/client-adapter-outputs.json"
        or hashlib.sha256(output_raw).hexdigest() != output_file["sha256"]
        or len(output_raw) != output_file["size_bytes"]
        or outputs.get("components") != expected_component_outputs
    ):
        _fail("idempotency_conflict", "existing client adapter outputs differ from the capture")
    artifact_ids = (package.get("extensions") or {}).get("akbs.android/capture-artifact-ids")
    expected_source_key = f"{run_id[:8]}/{member_alias}/{run_id}"
    input_rows = [
        row
        for row in package.get("files", [])
        if isinstance(row, dict) and row.get("id") == "qualification-adapter-inputs"
    ]
    input_raw = b""
    if len(input_rows) == 1:
        input_path = destination / str(input_rows[0].get("path") or "")
        if not input_path.is_symlink() and input_path.is_file():
            try:
                input_raw = input_path.read_bytes()
            except OSError as exc:
                _fail("idempotency_conflict", str(exc))
    if (
        (package.get("identity") or {}).get("member_alias") != member_alias
        or (package.get("identity") or {}).get("run_id") != run_id
        or checked.get("source_package_key") != expected_source_key
        or extension
        != {
            "contract": "android-patch-capture-package-v2/2.1",
            "manifest_sha256": snapshot["manifest_sha256"],
            "archive_inventory_sha256": snapshot["archive_inventory_sha256"],
        }
        or qualification
        != {
            "contract": "akbs-qualification-contract-pack-v2/2",
            "contract_sha256": existing_contract_sha256,
            "adapter_inputs_file_id": "qualification-adapter-inputs",
            "server_qualified": False,
        }
        # Already materialized valid packages keep their bytes and server retry
        # identity. New packages additionally preserve the exact source-ID map.
        or (artifact_ids is not None and artifact_ids != _capture_artifact_ids(snapshot["manifest"]))
        or len(input_rows) != 1
        or input_rows[0].get("path") != "metadata/qualification-adapter-inputs.json"
        or hashlib.sha256(input_raw).hexdigest() != input_rows[0].get("sha256")
        or len(input_raw) != input_rows[0].get("size_bytes")
        or input_raw != expected_adapter_inputs_raw
    ):
        _fail("idempotency_conflict", str(destination))
    return {
        "status": "PASS",
        "operation": "adapt-capture",
        "package": str(destination),
        "manifest_sha256": checked["manifest_sha256"],
        "archive_inventory_sha256": checked["archive_inventory_sha256"],
        "source_package_key": checked["source_package_key"],
        "platform_compatibility": checked["platform_compatibility"],
        "capture_manifest_sha256": snapshot["manifest_sha256"],
        "capture_archive_inventory_sha256": snapshot["archive_inventory_sha256"],
        "qualification_contract_sha256": existing_contract_sha256,
        "qualification_input_sha256": checked["coherence"]["qualification_input_sha256"],
        "idempotent_reuse": True,
        "source_capture_rewritten": False,
        "server_qualified": False,
        "v1_fallback": False,
        "writer": writer_status(),
    }


def materialize_capture(
    value: Path,
    *,
    member_alias: str,
    output_root: Path | None = None,
) -> dict[str, Any]:
    """Create a deterministic, local canonical v2 package from capture 2.1."""
    if not ALIAS_RE.fullmatch(member_alias):
        _fail("member_alias_invalid", member_alias)
    snapshot = read_materializable_capture(value)
    contract_raw, pack, profiles, item_schema, collection_schema = (
        _load_qualification_contract()
    )
    contract_sha256 = hashlib.sha256(contract_raw).hexdigest()
    profile_raw = PROFILE_PATH.read_bytes()
    profile_sha256 = hashlib.sha256(profile_raw).hexdigest()
    run_id = _run_id(snapshot)
    root = output_root.expanduser() if output_root is not None else _target_adapted_root()
    destination = root / member_alias / run_id
    component_outputs, adapter_inputs, derived_claims = _qualification_outputs(
        snapshot,
        pack,
        profiles,
        item_schema,
    )
    adapter_inputs_document = {
        "schema": "akbs-qualification-adapter-inputs-v2",
        "contract_version": "2",
        "item_contract": {
            "schema": "akbs-qualification-adapter-input-v2",
            "version": "2",
            "sha256": CONTRACT_SHA256[ADAPTER_INPUT_SCHEMA_PATH.name],
        },
        "capture_manifest_sha256": snapshot["manifest_sha256"],
        "capture_archive_inventory_sha256": snapshot["archive_inventory_sha256"],
        "qualification_contract_sha256": contract_sha256,
        "inputs": adapter_inputs,
    }
    try:
        validate_document(adapter_inputs_document, collection_schema)
    except SchemaError as exc:
        _fail("qualification_adapter_inputs_invalid", str(exc))
    adapter_inputs_raw = _json_bytes(adapter_inputs_document)
    canonical_json_sha256(adapter_inputs_document)
    existing = _existing_result(
        destination,
        root=root,
        member_alias=member_alias,
        run_id=run_id,
        snapshot=snapshot,
        contract_sha256=contract_sha256,
        expected_adapter_inputs_raw=adapter_inputs_raw,
        expected_component_outputs=component_outputs,
        derived_claims=derived_claims,
    )
    if existing is not None:
        return existing
    source_root = snapshot["root"].resolve()
    destination_resolved = destination.resolve(strict=False)
    if source_root == destination_resolved or source_root in destination_resolved.parents:
        _fail("output_overlaps_capture", str(destination))
    capture = snapshot["manifest"]
    with tempfile.TemporaryDirectory(prefix="akbs-capture-materializer-") as temporary:
        package_root = Path(temporary) / "package"
        package_root.mkdir()
        with open_materializable_capture_files(snapshot) as copy_file:
            (
                files,
                patch_file_ids,
                evidence_file_ids,
                canonical_evidence,
            ) = _copy_bound_capture_files(
                snapshot,
                copy_file,
                package_root,
                derived_claims,
            )

        adapter_inputs_path = "metadata/qualification-adapter-inputs.json"
        _write_bytes(package_root, adapter_inputs_path, adapter_inputs_raw)
        files.append(
            {
                "id": "qualification-adapter-inputs",
                "role": "metadata",
                "path": adapter_inputs_path,
                "sha256": hashlib.sha256(adapter_inputs_raw).hexdigest(),
                "size_bytes": len(adapter_inputs_raw),
                "media_type": "application/json",
            }
        )

        output_row = {
            "id": "qualification-client-output",
            "role": "metadata",
            "path": "metadata/client-adapter-outputs.json",
            "sha256": "0" * 64,
            "size_bytes": 0,
            "media_type": "application/json",
        }
        files.append(output_row)
        sources = _canonical_sources(capture)
        source_by_repository = {row["id"]: row["id"] for row in sources}
        package: dict[str, Any] = {
            "schema": "akbs-android-change-package-v2",
            "schema_version": "2",
            "package_kind": "android_change",
            "package_status": "validated",
            "identity": {
                "member_alias": member_alias,
                "run_id": run_id,
                "created_at": _canonical_created_at(capture["created_at"]),
            },
            "subject": {
                "title": capture["change_id"],
                "summary": capture["summary"],
                "feature_key": capture["change_id"],
                "primary_component_id": capture["primary_component_id"],
                "target": {
                    "project": str(capture["project"]).lower().replace("_", "-"),
                    "platform": capture["platform"],
                    "android_version": capture["android_version"],
                },
            },
            "workflow": {
                "contract": capture["workflow_contract"],
                "implementation_origins": [capture["implementation_origin"]],
                "capture_tool": {"id": "android-patch-capture", "version": "2.1"},
            },
            "components": capture["components"],
            "sources": sources,
            "files": files,
            "changes": [
                {
                    "id": _artifact_id("patch", patch["id"]),
                    "component_ids": patch["component_ids"],
                    "source_id": source_by_repository[patch["repository_id"]],
                    "file_id": patch_file_ids[patch["id"]],
                    "format": "git_diff",
                }
                for patch in capture["patches"]
            ],
            "evidence": canonical_evidence,
            "qualification": {
                "profile_id": profiles["schema"],
                "profile_artifact_sha256": profile_sha256,
                "client_adapter_outputs_file_id": output_row["id"],
                "component_evidence_bindings": [
                    {
                        "component_id": binding["component_id"],
                        "evidence_ids": binding["evidence_ids"],
                    }
                    for binding in capture["qualification_bindings"]
                ],
            },
            "extensions": {
                "akbs.android/capture": {
                    "contract": "android-patch-capture-package-v2/2.1",
                    "manifest_sha256": snapshot["manifest_sha256"],
                    "archive_inventory_sha256": snapshot["archive_inventory_sha256"],
                },
                "akbs.android/capture-artifact-ids": _capture_artifact_ids(capture),
                "akbs.android/qualification": {
                    "contract": "akbs-qualification-contract-pack-v2/2",
                    "contract_sha256": contract_sha256,
                    "adapter_inputs_file_id": "qualification-adapter-inputs",
                    "server_qualified": False,
                },
            },
        }
        if capture["workflow_contract"] in {"manual_import", "historical_import"}:
            imports = [
                row
                for row in canonical_evidence
                if row["kind"] == "import_provenance"
            ]
            if len(imports) != 1:
                _fail("import_provenance_missing", capture["workflow_contract"])
            package["workflow"]["import_provenance_file_id"] = imports[0]["file_id"]

        outputs = {
            "schema": "akbs-client-adapter-outputs-v1",
            "authority": "untrusted_client_input",
            "source_package_key": f"{run_id[:8]}/{member_alias}/{run_id}",
            "qualification_input_sha256": qualification_input_sha256(package),
            "profile_id": profiles["schema"],
            "profile_artifact_sha256": profile_sha256,
            "declared_package_status": "validated",
            "components": component_outputs,
        }
        output_raw = _json_bytes(outputs)
        _write_bytes(package_root, output_row["path"], output_raw)
        output_row["sha256"] = hashlib.sha256(output_raw).hexdigest()
        output_row["size_bytes"] = len(output_raw)
        _write_bytes(package_root, "manifest.json", _json_bytes(package))
        checked = check_package(package_root)

        rebound = read_materializable_capture(snapshot["root"])
        if (
            rebound["manifest_sha256"] != snapshot["manifest_sha256"]
            or rebound["archive_inventory_sha256"] != snapshot["archive_inventory_sha256"]
        ):
            _fail("capture_changed_during_materialization", str(snapshot["root"]))
        prepared = prepare_package(package_root, pending_root=root)
        return {
            "status": "PASS",
            "operation": "adapt-capture",
            "package": prepared["package"],
            "manifest_sha256": checked["manifest_sha256"],
            "archive_inventory_sha256": checked["archive_inventory_sha256"],
            "source_package_key": checked["source_package_key"],
            "platform_compatibility": checked["platform_compatibility"],
            "capture_manifest_sha256": snapshot["manifest_sha256"],
            "capture_archive_inventory_sha256": snapshot["archive_inventory_sha256"],
            "qualification_contract_sha256": contract_sha256,
            "qualification_input_sha256": checked["coherence"]["qualification_input_sha256"],
            "idempotent_reuse": False,
            "source_capture_rewritten": False,
            "server_qualified": False,
            "v1_fallback": False,
            "writer": writer_status(),
        }
