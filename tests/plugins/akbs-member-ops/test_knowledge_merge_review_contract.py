from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "plugins/akbs-member-ops/lib"))
from akbs_member_ops.http_client import HttpClientFailure
from akbs_member_ops.knowledge.merge_confirmation import client


def typed_payload(action: str = "attach_evidence") -> dict:
    identity = {
        "confirmation_id": "confirmation-example", "member_alias": "lincong",
        "target_case_id": "case-example", "action": action,
        "package_key": "source-package-example",
        "target_implementation_id": "implementation-example" if action == "attach_evidence" else "",
    }
    target = {"case_id": "case-example", "content_hash": "a" * 64}
    proposal = {
        "decision": action, "target_case_id": "case-example",
        "target_case_content_hash": "a" * 64,
        "patch_package_id": "patch-package-example",
    }
    if action == "attach_evidence":
        target["implementation"] = {
            "case_id": "case-example", "implementation_id": "implementation-example",
            "content_hash": "b" * 64,
        }
        proposal.update(target_implementation_id="implementation-example",
                        target_implementation_content_hash="b" * 64)
    return {
        "confirmation_id": "confirmation-example", "curation_action": action,
        "package_key": "source-package-example", "patch_package_id": "patch-package-example",
        "target_knowledge": target,
        "member_agent_context": {
            "schema": "akbs-member-curation-guidance/v2", "action": action,
            "confirmation_identity": identity, "proposal": proposal, "target_case": copy.deepcopy(target),
            "decision_artifact_id": "artifact-example",
            "decision_sha256": hashlib.sha256(json.dumps(
                proposal, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")).hexdigest(),
        },
    }


def fetch(payload: dict, action: str = "") -> dict:
    with (
        mock.patch.object(client, "member_merge_confirmations_url", return_value="http://akbs.example/confirmation"),
        mock.patch.object(client, "member_request_headers", return_value={"X-AKBS-User": "lincong"}),
        mock.patch.object(client, "request_json", return_value=payload) as request,
        mock.patch.object(client, "post_merge_dispute") as dispute,
    ):
        result = client.fetch_merge_confirmation_payload("confirmation-example", action)
        assert request.call_args.args[0].get_method() == "GET"
        dispute.assert_not_called()
        return result


@pytest.mark.parametrize("action_type", ["add_implementation", "attach_evidence"])
@pytest.mark.parametrize("read_action", ["", "target", "compare"])
def test_typed_read_preserves_action_target_and_decision_identity(action_type: str, read_action: str) -> None:
    payload = typed_payload(action_type)
    if read_action:
        payload.pop("confirmation_id")  # Existing target/compare identity lives in context.
        payload.pop("curation_action")
    assert fetch(payload, read_action) == payload


@pytest.mark.parametrize("path,value", [
    (("member_agent_context", "confirmation_identity", "confirmation_id"), "wrong"),
    (("member_agent_context", "confirmation_identity", "member_alias"), "other"),
    (("member_agent_context", "proposal", "decision"), "add_implementation"),
    (("member_agent_context", "proposal", "target_case_id"), "another-case"),
    (("member_agent_context", "proposal", "target_case_content_hash"), ""),
    (("member_agent_context", "decision_artifact_id"), ""),
    (("member_agent_context", "decision_sha256"), "bad-hash"),
    (("target_knowledge", "implementation", "case_id"), "another-case"),
    (("target_knowledge", "implementation", "implementation_id"), "another-implementation"),
    (("member_agent_context", "proposal", "reason"), "changed content without a new hash"),
    (("member_agent_context", "schema"), "akbs-member-merge-guidance/v1"),
    (("member_agent_context", "target_case", "content_hash"), "f" * 64),
    (("member_agent_context", "target_case", "implementation", "content_hash"), "f" * 64),
    (("package_key",), "another-source"),
    (("patch_package_id",), "another-subject"),
])
def test_typed_read_rejects_substituted_or_incomplete_identity(path: tuple, value) -> None:
    payload = typed_payload()
    current = payload
    for part in path[:-1]:
        current = current[part]
    current[path[-1]] = value
    with pytest.raises(HttpClientFailure):
        fetch(payload)


def test_stale_target_hash_is_preserved_for_readonly_review_not_replaced() -> None:
    payload = typed_payload()
    payload["target_knowledge"]["content_hash"] = "d" * 64
    payload["target_knowledge"]["implementation"]["content_hash"] = "e" * 64
    payload["member_agent_context"]["target_case"] = copy.deepcopy(payload["target_knowledge"])
    payload["member_agent_context"]["review_state"] = "recheck_required"
    result = fetch(payload)
    assert result["member_agent_context"]["proposal"]["target_case_content_hash"] == "a" * 64
    assert result["target_knowledge"]["content_hash"] == "d" * 64


def test_target_read_cannot_downgrade_a_typed_context_to_history() -> None:
    payload = typed_payload()
    payload.pop("curation_action")
    payload["member_agent_context"].pop("schema")
    with pytest.raises(HttpClientFailure):
        fetch(payload, "target")


def test_typed_detail_rejects_inconsistent_confirmation_record() -> None:
    payload = typed_payload()
    payload["agent_context"] = {"confirmation_record": {
        "confirmation_id": "confirmation-example", "decision_artifact_id": "artifact-example",
        "decision_sha256": "f" * 64,
    }}
    with pytest.raises(HttpClientFailure):
        fetch(payload)


def test_typed_list_validates_each_member_confirmation_without_writes() -> None:
    payload = {"items": [typed_payload("add_implementation"), typed_payload()]}
    with (
        mock.patch.object(client, "member_merge_confirmations_url", return_value="http://akbs.example/confirmations"),
        mock.patch.object(client, "member_request_headers", return_value={"X-AKBS-User": "lincong"}),
        mock.patch.object(client, "request_json", return_value=payload),
        mock.patch.object(client, "post_merge_dispute") as dispute,
    ):
        assert client.fetch_merge_confirmation_payload() == payload
        dispute.assert_not_called()
        payload["items"][1]["member_agent_context"]["confirmation_identity"]["member_alias"] = "other"
        with pytest.raises(HttpClientFailure):
            client.fetch_merge_confirmation_payload()


def test_closed_historical_read_does_not_invent_typed_fields() -> None:
    payload = {"confirmation_id": "confirmation-example",
               "member_agent_context": {"schema": "akbs-member-merge-guidance/v1"}}
    assert fetch(payload) == payload
