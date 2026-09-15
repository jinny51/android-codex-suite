from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


PUBLIC_CONTRACT_PATH = (
    Path(__file__).resolve().parents[3]
    / "internal"
    / "incoming-v2"
    / "references"
    / "incoming-public-contract-v2.json"
)
CONTRACT_SET_PATH = Path(__file__).resolve().parents[3] / "contracts" / "contract-set.json"

PATCH_PACKAGE_CONTRACT_SCHEMA = "akbs-patch-package-contract/v2"
LEGACY_PATCH_CONTRACT_ERROR_CODE = "legacy_patch_contract_not_supported"


@lru_cache(maxsize=1)
def public_contract() -> dict[str, Any]:
    contract_set = json.loads(CONTRACT_SET_PATH.read_text(encoding="utf-8"))
    current = (contract_set.get("incoming_package") or {}).get("current")
    if (
        contract_set.get("schema") != "akbs-contract-set/v1"
        or not isinstance(current, dict)
        or current.get("schema") != "knowledge-incoming-package"
        or current.get("schema_version") != "2"
        or current.get("write_policy") != "required_for_all_new_incoming_packages"
    ):
        raise RuntimeError("packaged AKBS contract set is invalid")
    payload = json.loads(PUBLIC_CONTRACT_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema") != "akbs-incoming-public-contract-v2":
        raise RuntimeError("packaged incoming public contract schema is invalid")
    families = payload.get("reason_code_families")
    success = payload.get("success_reason_codes")
    if not isinstance(families, dict) or not families or not isinstance(success, list) or not success:
        raise RuntimeError("packaged incoming public contract reason codes are missing")
    error_codes = [code for codes in families.values() if isinstance(codes, list) for code in codes]
    all_codes = [*error_codes, *success]
    if any(not isinstance(code, str) or not code for code in all_codes) or len(all_codes) != len(set(all_codes)):
        raise RuntimeError("packaged incoming public contract reason codes must be unique non-empty strings")
    completion = payload.get("patch_information_completion")
    if not isinstance(completion, dict) or completion.get("schema") != "akbs-patch-package-information-completion/v1":
        raise RuntimeError("packaged patch information completion contract is missing")
    fields = completion.get("fields")
    field_ids = [item.get("id") for item in fields or [] if isinstance(item, dict)]
    if (
        not isinstance(fields, list)
        or len(field_ids) != len(fields)
        or len(field_ids) != len(set(field_ids))
        or any(not isinstance(value, str) or not value for value in field_ids)
    ):
        raise RuntimeError("packaged patch information completion fields are invalid")
    attachment = completion.get("attachment")
    if (
        not isinstance(attachment, dict)
        or not isinstance(attachment.get("max_file_bytes"), int)
        or not isinstance(attachment.get("max_total_bytes"), int)
        or attachment.get("patch_assets_immutable") is not True
    ):
        raise RuntimeError("packaged patch information attachment boundary is invalid")
    _validate_patch_package_contract(payload.get("patch_package_contract"), families)
    return payload


def _validate_string_list(value: Any, *, label: str) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item for item in value)
        or len(value) != len(set(value))
    ):
        raise RuntimeError(f"packaged patch package contract {label} is invalid")
    return tuple(value)


def _validate_patch_package_contract(value: Any, reason_families: dict[str, Any]) -> None:
    if not isinstance(value, dict) or value.get("schema") != PATCH_PACKAGE_CONTRACT_SCHEMA:
        raise RuntimeError("packaged patch package contract is missing")

    identity = value.get("business_identity")
    if (
        not isinstance(identity, dict)
        or identity.get("visible_package_type") != "patch_package"
        or identity.get("identity_field") != "patch_package_id"
        or identity.get("canonical_identity") != "patch_packages.patch_package_id"
        or identity.get("queue_identity") != "patch_packages.patch_package_id"
        or identity.get("main_branch_identity") != "patch_packages.patch_package_id"
        or identity.get("source_identity_field") != "package_key"
        or identity.get("source_identity_authority") != "packages.package_key"
        or identity.get("source_identity_role") != "source_only"
        or identity.get("patch_assets_immutable") is not True
        or identity.get("non_patch_envelope_append_only") is not True
    ):
        raise RuntimeError("packaged patch package business identity is invalid")

    patch_set = value.get("patch_set")
    if (
        not isinstance(patch_set, dict)
        or patch_set.get("algorithm") != "sha256-canonical-json-v1"
        or patch_set.get("source") != "package_assets[asset_type=patch]"
        or patch_set.get("sort") != ["relative_path", "sha256"]
        or patch_set.get("fields") != ["relative_path", "sha256", "size_bytes"]
    ):
        raise RuntimeError("packaged patch-set identity contract is invalid")

    queue = value.get("queue")
    if not isinstance(queue, dict) or queue.get("branch") != "queue":
        raise RuntimeError("packaged patch queue contract is missing")
    states = _validate_string_list(queue.get("states"), label="queue states")
    expected_states = (
        "received",
        "under_review",
        "information_required",
        "information_review",
        "closed",
    )
    if states != expected_states:
        raise RuntimeError("packaged patch queue states drifted")
    terminal_states = _validate_string_list(queue.get("terminal_states"), label="terminal states")
    if terminal_states != ("closed",):
        raise RuntimeError("packaged patch queue terminal states drifted")
    if queue.get("admission_target") != {"branch": "main", "stage": "under_review"}:
        raise RuntimeError("packaged patch queue admission target drifted")
    if queue.get("rejection_target") != {
        "branch": "queue",
        "stage": "closed",
        "final_decision": "rejected",
    }:
        raise RuntimeError("packaged patch queue rejection target drifted")
    transitions = queue.get("transitions")
    if not isinstance(transitions, list) or not transitions:
        raise RuntimeError("packaged patch queue transitions are missing")
    normalized_transitions: list[tuple[str, str, str, str, str, str]] = []
    for transition in transitions:
        required_transition_fields = {"from_branch", "from", "action", "to_branch", "to"}
        allowed_transition_fields = {*required_transition_fields, "final_decision"}
        if (
            not isinstance(transition, dict)
            or not required_transition_fields.issubset(transition)
            or set(transition) - allowed_transition_fields
        ):
            raise RuntimeError("packaged patch queue transition is invalid")
        source_branch = transition.get("from_branch")
        source = transition.get("from")
        action = transition.get("action")
        target_branch = transition.get("to_branch")
        target = transition.get("to")
        final_decision = str(transition.get("final_decision") or "")
        if (
            source_branch != "queue"
            or source not in states
            or target_branch not in {"queue", "main"}
            or (target_branch == "queue" and target not in states)
            or (target_branch == "main" and target != "under_review")
            or not isinstance(action, str)
            or not action
            or (final_decision and target != "closed")
        ):
            raise RuntimeError("packaged patch queue transition references an invalid state or action")
        normalized_transitions.append(
            (str(source_branch), str(source), action, str(target_branch), str(target), final_decision)
        )
    if len(normalized_transitions) != len(set(normalized_transitions)):
        raise RuntimeError("packaged patch queue transitions must be unique")
    expected_transitions = [
        ("queue", "received", "review_started", "queue", "under_review", ""),
        ("queue", "received", "information_requested", "queue", "information_required", ""),
        ("queue", "under_review", "information_requested", "queue", "information_required", ""),
        ("queue", "information_review", "information_requested", "queue", "information_required", ""),
        ("queue", "information_required", "information_submitted", "queue", "information_review", ""),
        ("queue", "received", "admitted", "main", "under_review", ""),
        ("queue", "under_review", "admitted", "main", "under_review", ""),
        ("queue", "information_review", "admitted", "main", "under_review", ""),
        ("queue", "received", "rejected", "queue", "closed", "rejected"),
        ("queue", "under_review", "rejected", "queue", "closed", "rejected"),
        ("queue", "information_required", "rejected", "queue", "closed", "rejected"),
        ("queue", "information_review", "rejected", "queue", "closed", "rejected"),
        ("queue", "received", "withdrawn", "queue", "closed", "withdrawn"),
    ]
    if normalized_transitions != expected_transitions:
        raise RuntimeError("packaged patch queue transition graph drifted")
    if not isinstance(queue.get("idempotency"), str) or not queue["idempotency"]:
        raise RuntimeError("packaged patch queue idempotency contract is missing")

    patch_queue_codes = _validate_string_list(reason_families.get("patch_queue"), label="reason codes")
    required_queue_codes = {
        "information_request_not_found",
        "information_request_closed",
        "patch_asset_immutable",
        "patch_set_proof_mismatch",
        "queue_state_closed",
        "queue_state_not_reviewable",
    }
    if not required_queue_codes.issubset(patch_queue_codes):
        raise RuntimeError("packaged patch queue reason codes are incomplete")

    curation = value.get("curation")
    if not isinstance(curation, dict) or curation.get("branch") != "main":
        raise RuntimeError("packaged patch curation contract is missing")
    stages = _validate_string_list(curation.get("stages"), label="curation stages")
    if stages != ("under_review", "pending_merge_confirmation", "dispute_open", "closed"):
        raise RuntimeError("packaged patch curation stages drifted")
    allowed_decisions = _validate_string_list(curation.get("allowed_decisions"), label="curation decisions")
    forbidden_decisions = _validate_string_list(
        curation.get("forbidden_decisions"), label="forbidden curation decisions"
    )
    if set(allowed_decisions) & set(forbidden_decisions):
        raise RuntimeError("packaged patch curation decisions overlap")
    if allowed_decisions != ("new_case", "merge_existing"):
        raise RuntimeError("packaged patch curation decisions drifted")
    if forbidden_decisions != ("needs_evidence", "supplement_review"):
        raise RuntimeError("packaged patch forbidden curation decisions drifted")
    member_actions = _validate_string_list(curation.get("member_actions"), label="member actions")
    if member_actions != ("confirm_merge", "submit_merge_dispute"):
        raise RuntimeError("packaged patch member actions drifted")
    forbidden_actions = _validate_string_list(
        curation.get("forbidden_actions"), label="forbidden member actions"
    )
    if forbidden_actions != (
        "submit_supplement",
        "close_supplement_chain",
        "upload_replacement_original",
    ):
        raise RuntimeError("packaged patch forbidden member actions drifted")
    _validate_string_list(value.get("retired_business_fields"), label="retired fields")
    _validate_string_list(value.get("retired_business_values"), label="retired values")
    _validate_string_list(value.get("regenerate_when"), label="regeneration reasons")

    history = value.get("history")
    if (
        not isinstance(history, dict)
        or history.get("raw_rows") != "immutable_audit_only"
        or history.get("retired_business_vocabulary") != "history_audit_only"
        or history.get("missing_parent_projection") != "history_compatible_patch_package"
        or history.get("inferred_relation_requires_audit_marker") is not True
    ):
        raise RuntimeError("packaged patch package history contract is invalid")
    public_source_roles = _validate_string_list(
        history.get("public_source_roles"), label="public history source roles"
    )
    if "source" not in public_source_roles or {"root_original", "supplement", "replacement"} & set(
        public_source_roles
    ):
        raise RuntimeError("packaged patch package public source roles are invalid")


def error_reason_codes() -> frozenset[str]:
    families = public_contract()["reason_code_families"]
    return frozenset(code for codes in families.values() for code in codes)


def success_reason_codes() -> tuple[str, ...]:
    return tuple(public_contract()["success_reason_codes"])


def patch_queue_reason_codes() -> frozenset[str]:
    return frozenset(public_contract()["reason_code_families"]["patch_queue"])


def patch_queue_states() -> tuple[str, ...]:
    return tuple(public_contract()["patch_package_contract"]["queue"]["states"])


def patch_queue_terminal_states() -> frozenset[str]:
    return frozenset(public_contract()["patch_package_contract"]["queue"]["terminal_states"])


def patch_information_completion_fields() -> frozenset[str]:
    return frozenset(
        str(item["id"]) for item in public_contract()["patch_information_completion"]["fields"]
    )


def patch_information_attachment_limits() -> tuple[int, int]:
    attachment = public_contract()["patch_information_completion"]["attachment"]
    return int(attachment["max_file_bytes"]), int(attachment["max_total_bytes"])


def retired_patch_business_fields() -> frozenset[str]:
    return frozenset(public_contract()["patch_package_contract"]["retired_business_fields"])


def retired_patch_business_values() -> frozenset[str]:
    return frozenset(public_contract()["patch_package_contract"]["retired_business_values"])


_PATCH_BUSINESS_SEMANTIC_FIELDS = frozenset(
    {
        "schema",
        "package_type",
        "physical_package_type",
        "material_unit_type",
        "unit_type",
        "business_state",
        "branch",
        "stage",
        "state",
        "status",
        "queue_state",
        "gate_status",
        "event_type",
        "action",
        "next_action",
        "notification_type",
    }
)


def legacy_patch_contract_error(payload: dict[str, Any]) -> str:
    """Reject retired business protocol only at this centralized boundary.

    The check is intentionally structural: ordinary prose may still use words
    such as "supplementary" for daily or weekly evidence without becoming a
    retired patch-package workflow.
    """
    fields: set[str] = set()
    values: set[str] = set()

    def visit(value: object) -> None:
        if isinstance(value, dict):
            for raw_key, child in value.items():
                key = str(raw_key)
                folded_key = key.casefold()
                if folded_key in retired_patch_business_fields():
                    fields.add(key)
                    continue
                if (
                    folded_key in _PATCH_BUSINESS_SEMANTIC_FIELDS
                    and isinstance(child, str)
                    and child.casefold() in retired_patch_business_values()
                ):
                    values.add(child)
                    continue
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    if payload.get("package_kind") == "android_change" or set(payload) & retired_patch_business_fields():
        visit(payload)
    fields = sorted(fields)
    values = sorted(values)
    if not fields and not values:
        return ""
    parts: list[str] = []
    if fields:
        parts.append("fields=" + ",".join(fields))
    if values:
        parts.append("values=" + ",".join(values))
    return (
        f"[{LEGACY_PATCH_CONTRACT_ERROR_CODE}] 当前补丁包只接受 patch_package_id 业务主体合同；"
        "旧版补丁链字段或值仅保留历史审计，不能提交：" + "; ".join(parts)
    )


def require_current_patch_contract(payload: dict[str, Any]) -> None:
    error = legacy_patch_contract_error(payload)
    if error:
        raise SystemExit(error)


def patch_package_id_from_upload_response(payload: dict[str, Any]) -> str:
    package = payload.get("package")
    if not isinstance(package, dict):
        raise RuntimeError("server patch upload response is missing package identity")
    patch_package_id = str(package.get("patch_package_id") or "").strip()
    if not patch_package_id:
        raise RuntimeError("server patch upload response is missing patch_package_id")
    source_package_key = str(package.get("package_key") or "").strip()
    if not source_package_key:
        raise RuntimeError("server patch upload response is missing source package_key")
    if source_package_key == patch_package_id:
        raise RuntimeError("server patch upload response conflates subject and source identity")
    return patch_package_id


def validate_success_response(payload: dict[str, Any]) -> None:
    incoming = payload.get("agent_context", {}).get("incoming_contract")
    if not isinstance(incoming, dict):
        raise RuntimeError("server success response is missing incoming contract evidence")
    if str(incoming.get("version") or "") != str(public_contract()["schema_version"]):
        raise RuntimeError("server success response incoming contract version drifted")
    if incoming.get("authority") != "akbs-server":
        raise RuntimeError("server success response incoming contract authority is invalid")
    actual = incoming.get("reason_codes")
    if actual != list(success_reason_codes()):
        raise RuntimeError("server success response reason codes drifted from the public contract")
