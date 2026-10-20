from __future__ import annotations

import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import urllib.error
import urllib.parse
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "plugins/akbs-member-ops/lib"))
from akbs_member_ops.http_client import HttpClientFailure
from akbs_member_ops.knowledge.merge_confirmation import client
from akbs_member_ops.knowledge_search import cli
from akbs_member_ops.knowledge_search import config


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


@pytest.mark.parametrize("reason,assessment", [
    ("原异议理由\n保留第二行", ""),
    ("", "目标实现不符\n成员的完整评估"),
    ("原异议理由", "补充评估\n" + "不应省略的细节" * 100),
])
def test_dispute_sends_complete_user_content_in_the_single_reason(reason: str, assessment: str) -> None:
    refs = ["patch:asset-example", "verification:example\n补充证据说明"]
    with (
        mock.patch.object(client, "member_merge_confirmations_url", return_value="http://akbs.example/confirmation/dispute"),
        mock.patch.object(client, "member_request_headers", return_value={"X-AKBS-User": "lincong"}),
        mock.patch.object(client, "request_json", return_value={"accepted": True}) as request,
    ):
        client.post_merge_dispute(
            "confirmation-example", reason=reason, member_assessment=assessment,
            evidence_refs=refs, agent_notes={"submitted_by": "internal-only-marker"},
        )
    sent = request.call_args.args[0]
    assert sent.get_method() == "POST"
    payload = json.loads(sent.data.decode("utf-8"))
    assert set(payload) == {"reason"}
    assert payload["reason"].strip()
    for original in [reason, assessment, *refs]:
        if original:
            assert original in payload["reason"]
    assert "internal-only-marker" not in payload["reason"]


def test_reason_only_dispute_keeps_existing_reason_unchanged() -> None:
    reason = "已有理由\n第二行"
    with (
        mock.patch.object(client, "member_merge_confirmations_url", return_value="http://akbs.example/dispute"),
        mock.patch.object(client, "member_request_headers", return_value={"X-AKBS-User": "lincong"}),
        mock.patch.object(client, "request_json", return_value={"accepted": True}) as request,
    ):
        client.post_merge_dispute(
            "confirmation-example", reason=reason, member_assessment="", evidence_refs=[], agent_notes={},
        )
    assert json.loads(request.call_args.args[0].data) == {"reason": reason}


def test_internal_notes_and_evidence_alone_are_not_a_member_dispute() -> None:
    with mock.patch.object(client, "request_json") as request:
        with pytest.raises(ValueError, match="reason or member assessment"):
            client.post_merge_dispute(
                "confirmation-example", reason=" ", member_assessment="\n",
                evidence_refs=["evidence-only"], agent_notes={"reason": "not-member-content"},
            )
        request.assert_not_called()


@pytest.mark.parametrize("send", [False, True])
def test_assessment_only_cli_still_requires_explicit_send_and_sends_a_reason(send: bool, capsys) -> None:
    arguments = [
        "--merge-confirmation", "dispute", "--merge-confirmation-id", "confirmation-example",
        "--member-assessment", "成员的完整评估\n第二行", "--evidence-ref", "evidence-example", "--json",
    ]
    if send:
        arguments.append("--send-dispute")
    args = cli.build_parser().parse_args(arguments)
    with (
        mock.patch.object(client, "member_merge_confirmations_url", return_value="http://akbs.example/dispute"),
        mock.patch.object(client, "member_request_headers", return_value={"X-AKBS-User": "lincong"}),
        mock.patch.object(client, "request_json", return_value={"accepted": True}) as request,
    ):
        if send:
            assert cli.handle_merge_confirmation_command(args) == 0
            sent = json.loads(request.call_args.args[0].data)
            assert set(sent) == {"reason"}
            assert "成员的完整评估\n第二行" in sent["reason"]
            assert "evidence-example" in sent["reason"]
            assert json.loads(capsys.readouterr().out)["accepted"] is True
        else:
            with pytest.raises(SystemExit, match="--send-dispute"):
                cli.handle_merge_confirmation_command(args)
            request.assert_not_called()


def analysis_payload(action: str) -> dict:
    payload = typed_payload(action)
    payload["target_knowledge"]["title"] = "同一功能"
    if action == "attach_evidence":
        payload["target_knowledge"]["implementation"]["implementation_summary"] = "已有方案的具体做法"
        payload["target_knowledge"]["implementation"]["approach"] = "保留已有服务实现路径"
    context = payload["member_agent_context"]
    context["target_case"] = copy.deepcopy(payload["target_knowledge"])
    context.update(
        supporting_evidence=["审核给出的依据"], counter_evidence=[],
        different_points=["新增一份技术实现" if action == "add_implementation" else "为现有实现补充证据"],
        effect_if_confirmed="只按本次提议新增内容，不改写已有实现。",
    )
    if action == "add_implementation":
        context["proposal"]["implementation"] = {
            "implementation_summary": "本次提议的不同方案", "approach": "基于现有服务调整关键检查",
        }
    context["proposal"]["applicability"] = [{"project": "project-example", "platform": "mtk", "android_version": "14"}]
    context["proposal"]["evidence_bindings"] = [{"patch_asset_id": "asset-example", "binding_role": "verification_only"}]
    context["decision_sha256"] = hashlib.sha256(json.dumps(
        context["proposal"], ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()
    payload.update(
        material_display_title="成员材料", confirmation_status="pending",
        merge_basis=context["supporting_evidence"], counter_evidence=context["different_points"],
        actions={"can_submit_dispute": True},
    )
    return payload


def test_authoritative_empty_counter_evidence_is_not_replaced_by_a_display_difference() -> None:
    detail = analysis_payload("attach_evidence")
    detail["counter_evidence"] = ["旧详情的差异文案"]
    assert cli.build_merge_analysis(detail)["counter_evidence"] == []
    assert detail["counter_evidence"] == ["旧详情的差异文案"]


@pytest.mark.parametrize("action", ["add_implementation", "attach_evidence"])
def test_typed_analysis_does_not_treat_the_proposed_action_as_counter_evidence(action: str) -> None:
    detail = analysis_payload(action)
    original = copy.deepcopy(detail)
    result = cli.build_merge_analysis(detail)
    assert result["counter_evidence"] == []
    assert "当前未看到明确反向证据" in result["recommendation"]
    assert "反向证据：" not in result["dispute_reason_draft"]
    assert result["member_agent_context"] == original["member_agent_context"]
    assert detail == original
    assert result["curation_action"] == action
    if action == "add_implementation":
        assert "新增这份技术实现" in result["dispute_reason_draft"]
    else:
        assert "implementation-example" in result["dispute_reason_draft"]
        assert "补充这份证据" in result["dispute_reason_draft"]


def test_typed_analysis_preserves_real_counter_evidence_and_readonly_state() -> None:
    detail = analysis_payload("attach_evidence")
    detail["member_agent_context"]["counter_evidence"] = ["确切实现没有覆盖这个验证环境"]
    detail["actions"]["can_submit_dispute"] = False
    result = cli.build_merge_analysis(detail)
    assert result["counter_evidence"] == ["确切实现没有覆盖这个验证环境"]
    assert result["dispute_reason_draft"] == ""
    assert "只能作为只读分析材料" in result["recommendation"]


@pytest.mark.parametrize("action", ["add_implementation", "attach_evidence"])
def test_default_typed_output_explains_the_exact_proposal_and_effect(action: str) -> None:
    detail = analysis_payload(action)
    proposal = detail["member_agent_context"]["proposal"]
    proposal["evidence_bindings"].extend(
        {"patch_asset_id": f"asset-more-{index}", "binding_role": "verification_only"}
        for index in range(3)
    )
    result = cli.build_merge_analysis(detail)
    assert "asset-more-2" in json.dumps(result, ensure_ascii=False)
    for output in (
        cli.format_merge_analysis(result),
        cli.format_merge_payload("知识沉淀确认", detail),
    ):
        assert action in output
        assert "- 反向证据: 新增一份技术实现" not in output
        assert "- 反向证据: 为现有实现补充证据" not in output
        assert "只按本次提议新增内容，不改写已有实现。" in output
        assert "project-example" in output
        assert "平台 mtk / Android 14" in output
        assert "asset-example" in output
        assert "提议证据绑定: 4 条" in output
        assert "asset-more-2" not in output
        assert "完整内容见 --json" in output
        assert "```" not in output
        if action == "attach_evidence":
            assert "implementation-example" in output
            assert "已有方案的具体做法" in output
            assert "保留已有服务实现路径" in output
        else:
            assert "本次提议的不同方案" in output
            assert "基于现有服务调整关键检查" in output
            assert "目标具体实现:" not in output


def test_typed_analyze_only_reads_the_confirmation_and_never_submits(capsys) -> None:
    detail = analysis_payload("attach_evidence")
    args = cli.build_parser().parse_args([
        "--merge-confirmation", "analyze", "--merge-confirmation-id", "confirmation-example",
    ])
    with (
        mock.patch.object(cli, "fetch_merge_confirmation_payload", return_value=detail) as read,
        mock.patch.object(cli, "post_merge_dispute") as dispute,
    ):
        assert cli.handle_merge_confirmation_command(args) == 0
        assert read.call_args_list == [
            mock.call("confirmation-example", timeout=args.server_timeout),
        ]
        dispute.assert_not_called()
    assert "implementation-example" in capsys.readouterr().out


@pytest.mark.parametrize("state,review_state", [
    ("pending", "recheck_required"),
    ("pending", "waiting_for_prior"),
    ("confirmed", "not_pending"),
])
def test_analyze_uses_one_authoritative_state_and_capability_snapshot(state: str, review_state: str, capsys) -> None:
    detail = analysis_payload("attach_evidence")
    detail["confirmation_status"] = state
    detail["member_agent_context"].update(confirmation_status=state, review_state=review_state)
    detail["actions"]["can_submit_dispute"] = False
    args = cli.build_parser().parse_args([
        "--merge-confirmation", "analyze", "--merge-confirmation-id", "confirmation-example", "--json",
    ])
    with mock.patch.object(cli, "fetch_merge_confirmation_payload", return_value=detail) as read:
        assert cli.handle_merge_confirmation_command(args) == 0
        read.assert_called_once_with("confirmation-example", timeout=args.server_timeout)
    result = json.loads(capsys.readouterr().out)
    assert result["confirmation_status"] == state
    assert result["member_agent_context"]["review_state"] == review_state
    assert result["can_submit_dispute"] is False
    assert result["dispute_reason_draft"] == ""


def review_entrypoint():
    path = ROOT / "plugins/akbs-member-ops/skills/akbs-knowledge-merge-review/scripts/akbs_knowledge_merge_review.py"
    spec = importlib.util.spec_from_file_location("review_entrypoint_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_public_review_timeout_matches_core_and_preserves_explicit_budget() -> None:
    entry = review_entrypoint()
    args = entry.build_parser().parse_args(["analyze", "--confirmation-id", "confirmation-example"])
    forwarded = cli.build_parser().parse_args(entry.search_arguments(args))
    assert args.server_timeout == forwarded.server_timeout == cli.build_parser().parse_args([]).server_timeout == 15.0
    explicit = entry.build_parser().parse_args(["list", "--server-timeout", "0.25"])
    assert cli.build_parser().parse_args(entry.search_arguments(explicit)).server_timeout == 0.25


@pytest.mark.parametrize("action", ["target", "compare"])
def test_independent_target_and_compare_remain_readonly_and_show_nested_identity(action: str, capsys) -> None:
    detail = analysis_payload("attach_evidence")
    # This is the real target/compare endpoint shape: identity is nested.
    payload = {
        "review_id": "review-example", "package_key": detail["package_key"],
        "target_knowledge": detail["target_knowledge"], "member_agent_context": detail["member_agent_context"],
    }
    args = cli.build_parser().parse_args([
        "--merge-confirmation", action, "--merge-confirmation-id", "confirmation-example",
    ])
    with (
        mock.patch.object(cli, "fetch_merge_confirmation_payload", return_value=payload) as read,
        mock.patch.object(cli, "post_merge_dispute") as dispute,
    ):
        assert cli.handle_merge_confirmation_command(args) == 0
        read.assert_called_once_with("confirmation-example", action, timeout=args.server_timeout)
        dispute.assert_not_called()
    output = capsys.readouterr().out
    assert "confirmation_id: confirmation-example" in output
    assert "patch_package_id: patch-package-example" in output
    assert "source package_key: source-package-example" in output
    assert "implementation-example" in output


@pytest.mark.parametrize("code,hint", [
    ("confirmation_not_found", "最新确认单编号"),
    ("confirmation_member_mismatch", "不属于当前成员"),
    ("confirmation_not_pending", "已处理或替换"),
    ("dispute_reason_required", "理由或成员评估"),
    ("dispute_idempotency_conflict", "已提交过不同内容"),
    ("target_case_hash_conflict", "管理员重新核对"),
    ("target_implementation_hash_conflict", "管理员重新核对"),
    ("confirmation_not_actionable", "等待前一条完成或管理员复核"),
    ("merge_comparison_unavailable", "比较依据不完整"),
    ("curation_confirmation_rejected", "未通过校验"),
    ("unexpected_server_code", "kind=unknown"),
])
def test_merge_business_errors_have_fixed_safe_hints_and_keep_the_request_id(code: str, hint: str) -> None:
    request_id = "req_" + "1" * 32
    secret = "fixture-secret-must-not-be-echoed"
    envelope = {
        "schema": "akbs-error-envelope-v1", "code": code, "request_id": request_id,
        "message": secret, "detail": secret,
    }
    error = urllib.error.HTTPError(
        "http://akbs.example/dispute", 409, "Conflict", {"X-Request-ID": request_id},
        io.BytesIO(json.dumps(envelope).encode("utf-8")),
    )
    output = client.merge_api_error(error)
    assert hint in output
    assert f"code={code}" in output
    assert request_id in output
    assert secret not in output


def test_public_list_forwards_page_two_to_the_existing_api(capsys) -> None:
    entry = review_entrypoint()
    response = {"page": 2, "page_size": 50, "total": 101, "items": [typed_payload("add_implementation")]}
    with (
        mock.patch.object(entry, "installed_plugin_family_status", return_value={"blocking": False}),
        mock.patch.object(config, "member_api_base_url", return_value=("http://akbs.example", "fixture")),
        mock.patch.object(client, "member_request_headers", return_value={"X-AKBS-User": "lincong"}),
        mock.patch.object(client, "request_json", return_value=response) as request,
        mock.patch.object(client, "post_merge_dispute") as dispute,
    ):
        assert entry.main(["list", "--page", "2", "--page-size", "50", "--json"]) == 0
        sent = request.call_args.args[0]
        assert sent.get_method() == "GET"
        assert urllib.parse.parse_qs(urllib.parse.urlsplit(sent.full_url).query) == {"page": ["2"], "page_size": ["50"]}
        assert request.call_args.kwargs["timeout"] == 15.0
        dispute.assert_not_called()
    assert json.loads(capsys.readouterr().out) == response
    output = cli.format_merge_list(response)
    assert "total: 101" in output
    assert "当前第 2 页，每页 50 条，本页 1 条" in output
    assert "list --page 3 --page-size 50" in output


@pytest.mark.parametrize("arguments", [
    ["--page", "0"], ["--page-size", "0"], ["--page-size", "101"],
])
def test_invalid_list_paging_is_rejected_before_any_server_read(arguments: list[str]) -> None:
    args = cli.build_parser().parse_args(["--merge-confirmation", "list", *arguments])
    with mock.patch.object(cli, "fetch_merge_confirmation_payload") as read:
        with pytest.raises(SystemExit, match="--page"):
            cli.handle_merge_confirmation_command(args)
        read.assert_not_called()


def test_empty_later_page_does_not_claim_the_member_has_no_confirmations() -> None:
    output = cli.format_merge_list({"page": 4, "page_size": 50, "total": 101, "items": []})
    assert "total: 101" in output
    assert "本页没有确认项" in output
    assert "暂无需要 Codex 分析" not in output
    assert "下一页" not in output
