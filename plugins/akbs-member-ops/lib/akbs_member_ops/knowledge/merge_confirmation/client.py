"""HTTP client for member merge-confirmation reads and explicit disputes."""

from __future__ import annotations

import json
import hashlib
import re
import urllib.request
from typing import Any

from akbs_member_ops.knowledge.member import require_member_alias
from akbs_member_ops.http_client import (
    failure_result,
    invalid_success_response,
    request_json,
)
from akbs_member_ops.knowledge_search.config import member_merge_confirmations_url


def member_request_headers() -> dict[str, str]:
    return {
        "Accept": "application/json",
        "X-AKBS-User": require_member_alias(),
    }


def merge_api_error(exc: BaseException) -> str:
    return failure_result(exc).safe_summary("merge confirmation API unavailable")


def _validate_typed_confirmation(
    payload: dict[str, Any], *, confirmation_id: str, member_alias: str
) -> None:
    context = payload.get("member_agent_context")
    context = context if isinstance(context, dict) else {}
    typed_actions = {"add_implementation", "attach_evidence"}
    identity = context.get("confirmation_identity")
    proposal = context.get("proposal")
    identity_action = identity.get("action") if isinstance(identity, dict) else None
    proposal_action = proposal.get("decision") if isinstance(proposal, dict) else None
    if (
        context.get("schema") != "akbs-member-curation-guidance/v2"
        and payload.get("curation_action") not in typed_actions
        and context.get("action") not in typed_actions
        and identity_action not in typed_actions
        and proposal_action not in typed_actions
    ):
        return  # Closed historical merge reads retain their existing shape.

    def reject() -> None:
        raise invalid_success_response("typed merge confirmation identity is inconsistent")

    target = payload.get("target_knowledge") or context.get("target_case")
    if (
        context.get("schema") != "akbs-member-curation-guidance/v2"
        or not isinstance(identity, dict)
        or not isinstance(proposal, dict)
        or not isinstance(target, dict)
    ):
        reject()
    action = context.get("action")
    returned_id = identity.get("confirmation_id")
    if (
        action not in typed_actions
        or identity.get("action") != action
        or proposal.get("decision") != action
        or payload.get("curation_action", action) != action
        or not isinstance(returned_id, str) or not returned_id
        or (confirmation_id and returned_id != confirmation_id)
        or payload.get("confirmation_id", returned_id) != returned_id
        or identity.get("member_alias") != member_alias
        or ("package_key" in payload and payload["package_key"] != identity.get("package_key"))
        or ("patch_package_id" in payload and payload["patch_package_id"] != proposal.get("patch_package_id"))
    ):
        reject()
    artifact_id = context.get("decision_artifact_id")
    decision_hash = context.get("decision_sha256")
    if (
        not isinstance(artifact_id, str) or not artifact_id.strip()
        or not isinstance(decision_hash, str)
        or not re.fullmatch(r"[0-9a-f]{64}", decision_hash)
    ):
        reject()
    actual_decision_hash = hashlib.sha256(json.dumps(
        proposal, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()
    if actual_decision_hash != decision_hash:
        reject()
    case_id = identity.get("target_case_id")
    case_hash = target.get("content_hash")
    expected_case_hash = proposal.get("target_case_content_hash")
    if (
        not isinstance(case_id, str) or not case_id
        or target.get("case_id") != case_id
        or proposal.get("target_case_id") != case_id
        or not isinstance(case_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", case_hash)
        or not isinstance(expected_case_hash, str)
        or not re.fullmatch(r"[0-9a-f]{64}", expected_case_hash)
    ):
        reject()
    context_target = context.get("target_case")
    if isinstance(context_target, dict) and any(
        context_target.get(key) != target.get(key) for key in ("case_id", "content_hash")
    ):
        reject()
    if isinstance(context_target, dict):
        context_impl = context_target.get("implementation")
        target_impl = target.get("implementation")
        if bool(context_impl) != bool(target_impl):
            reject()
        if context_impl and (
            not isinstance(context_impl, dict) or not isinstance(target_impl, dict)
            or any(context_impl.get(key) != target_impl.get(key)
                   for key in ("implementation_id", "case_id", "content_hash"))
        ):
            reject()
    # Expected and current hashes remain separate. A stale target is meaningful
    # readonly review context, not permission to silently switch the proposal.
    implementation_id = identity.get("target_implementation_id")
    if action == "attach_evidence":
        implementation = target.get("implementation")
        expected_hash = proposal.get("target_implementation_content_hash")
        if (
            not isinstance(implementation_id, str) or not implementation_id
            or proposal.get("target_implementation_id") != implementation_id
            or not isinstance(implementation, dict)
            or implementation.get("implementation_id") != implementation_id
            or implementation.get("case_id") != case_id
            or not isinstance(implementation.get("content_hash"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", implementation["content_hash"])
            or not isinstance(expected_hash, str)
            or not re.fullmatch(r"[0-9a-f]{64}", expected_hash)
        ):
            reject()
    elif implementation_id:
        reject()  # add_implementation proposes a new implementation, not an old target.
    agent_context = payload.get("agent_context")
    record = agent_context.get("confirmation_record") if isinstance(agent_context, dict) else None
    if isinstance(record, dict) and (
        record.get("confirmation_id") != returned_id
        or record.get("decision_artifact_id") != artifact_id
        or record.get("decision_sha256") != decision_hash
    ):
        reject()


def fetch_merge_confirmation_payload(
    confirmation_id: str = "",
    action: str = "",
    *,
    timeout: float = 3.0,
) -> dict[str, Any]:
    headers = member_request_headers()
    request = urllib.request.Request(
        member_merge_confirmations_url(confirmation_id, action),
        headers=headers,
        method="GET",
    )
    payload = request_json(request, timeout=timeout)
    returned_id = str(payload.get("confirmation_id") or "").strip()
    if confirmation_id and returned_id and returned_id != confirmation_id:
        raise invalid_success_response("merge confirmation response identity mismatch")
    items = payload.get("items")
    if not confirmation_id and isinstance(items, list):
        for item in items:
            if isinstance(item, dict):
                _validate_typed_confirmation(
                    item, confirmation_id="", member_alias=headers["X-AKBS-User"]
                )
    else:
        _validate_typed_confirmation(
            payload, confirmation_id=confirmation_id, member_alias=headers["X-AKBS-User"]
        )
    return payload


def post_merge_dispute(
    confirmation_id: str,
    *,
    reason: str,
    member_assessment: str,
    evidence_refs: list[str],
    agent_notes: dict[str, Any],
    timeout: float = 3.0,
) -> dict[str, Any]:
    payload = {
        "reason": reason,
        "member_assessment": member_assessment,
        "evidence_refs": evidence_refs,
        "agent_notes": agent_notes,
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = member_request_headers()
    headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        member_merge_confirmations_url(confirmation_id, "dispute"),
        data=data,
        headers=headers,
        method="POST",
    )
    result = request_json(request, timeout=timeout)
    returned_id = str(result.get("confirmation_id") or "").strip()
    if returned_id and returned_id != confirmation_id:
        raise invalid_success_response("merge dispute response confirmation identity mismatch")
    return result
