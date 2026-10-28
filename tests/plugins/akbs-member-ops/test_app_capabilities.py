from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
PLUGIN = ROOT / "plugins/akbs-member-ops"
if str(PLUGIN / "lib") not in sys.path:
    sys.path.insert(0, str(PLUGIN / "lib"))

from akbs_member_ops.http_client import HttpClientFailure
from akbs_member_ops.knowledge_search import api, apps, cli
from akbs_member_ops.knowledge_search.formatting import format_server_result


def sources():
    return [{"repository_url": "https://git.lincos.ai/liu_gui/AiFaceUnlock",
        "commit": "a" * 40, "branch": "main", "paths": ["app/src/main"]}]


def row():
    return {"type": "app", "app_id": "app-face", "revision": 1, "knowledge_id": "app-face",
        "title": "人脸解锁", "summary": "人脸识别后解锁", "content": "现有识别与解锁入口",
        "applicability": "使用原有识别服务", "usage": "按原README配置服务", "limitations": "未验证其他SDK",
        "member_alias": "liugui", "member_name": "桂柳", "sources": sources(), "version_label": "",
        "source_url": "/source", "score": 1.0, "reuse_grade": "reference_only"}


def test_app_has_no_member_submission_skill_or_module():
    assert not (PLUGIN / "skills/akbs-app-submit").exists()
    assert not (PLUGIN / "lib/akbs_member_ops/app_submit.py").exists()
    manifest = (ROOT / "manifests/akbs-member-ops.toml").read_text()
    assert "akbs-app-submit" not in manifest
    skill = (PLUGIN / "skills/akbs-knowledge-search/SKILL.md").read_text()
    assert "without a separate App submission" in skill


@pytest.mark.parametrize("changes", [{"commit": "main"},
    {"repository_url": "https://user:secret@git.lincos.ai/a/b"},
    {"repository_url": "https://other.invalid/a/b"}, {"paths": ["../outside"]}, {"paths": "/source"}])
def test_sources_pin_commit_and_reject_credential_or_path_escape(changes):
    value = sources()
    value[0].update(changes)
    with pytest.raises(ValueError):
        apps.validate_sources(value)


def test_app_search_preserves_exact_sources_without_patch_gate():
    item = row()
    api.validate_app_result(item)
    schema = json.loads((PLUGIN / "contracts/knowledge/member-knowledge-search-v2.schema.json").read_text())
    assert set(schema["$defs"]["appResult"]["properties"]) == set(item)
    assert cli.result_id(item) == "app-face"
    usage = cli.usage_result(item)
    assert usage["sources"][0]["commit"] == "a" * 40
    assert "App 能力" in format_server_result(item, 1)
    with pytest.raises(HttpClientFailure):
        api.validate_app_result({**item, "case_id": "case-other"})
    with pytest.raises(HttpClientFailure):
        api.validate_app_result({**item, "reuse_grade": "direct_reuse_candidate"})


def test_exact_source_read_checks_reviewed_commit_without_fetch_or_feedback(monkeypatch):
    seen = []
    monkeypatch.setattr(apps, "member_api_base_url", lambda: ("http://fixture.test", "fixture"))
    monkeypatch.setattr(apps, "member_request_headers", lambda: {"X-AKBS-User": "auditmember"})

    def get(request, **kwargs):
        seen.append(request)
        if request.full_url.endswith("/source"):
            return {"app_id": "app-face", "revision": 1, "sources": [{**sources()[0],
                "commit_url": "https://git.lincos.ai/liu_gui/AiFaceUnlock/commit/" + "a" * 40}]}
        return {"app_view": {"app_id": "app-face", "revision": 1, "sources": sources(),
            "contributors": [{"member_alias": "liugui", "member_name": "桂柳"}]}, "knowledge": {"revision": 1}}

    monkeypatch.setattr(apps, "request_json", get)
    result = apps.fetch_app_source("app-face", revision=1)
    assert result["reuse_outcome"] == "not_started"
    assert all(request.get_method() == "GET" and request.full_url.startswith("http://fixture.test/") for request in seen)
    assert seen[0].full_url.endswith("/apps/app-face?revision=1")
    assert seen[1].full_url.endswith("/apps/app-face/revisions/1/source")
    with pytest.raises(ValueError):
        apps.fetch_app("app-face", revision=None)


def test_source_locator_cannot_silently_switch_commit(monkeypatch):
    monkeypatch.setattr(apps, "fetch_app", lambda *a, **k: {"app_view": {"sources": sources()}})
    monkeypatch.setattr(apps, "member_api_base_url", lambda: ("http://fixture.test", "fixture"))
    monkeypatch.setattr(apps, "member_request_headers", lambda: {})
    wrong = copy.deepcopy(sources())
    wrong[0]["commit"] = "b" * 40
    monkeypatch.setattr(apps, "request_json", lambda *a, **k: {"app_id": "app-face", "revision": 1, "sources": wrong})
    with pytest.raises(HttpClientFailure):
        apps.fetch_app_source("app-face", revision=1)


def test_feedback_is_explicit_exact_and_not_synthesized_from_reads(monkeypatch):
    monkeypatch.setattr(apps, "member_api_base_url", lambda: ("http://fixture.test", "fixture"))
    monkeypatch.setattr(apps, "member_request_headers", lambda: {"X-AKBS-User": "auditmember"})
    sent = []
    monkeypatch.setattr(apps, "request_json", lambda request, **k: sent.append(request) or {"status": "recorded"})
    value = {"revision": 1, "outcome": "not_executed", "content": "只阅读还未执行", "request_id": "use-1"}
    assert apps.submit_app_feedback("app-face", value)["status"] == "recorded"
    assert json.loads(sent[0].data) == value
    assert sent[0].full_url.endswith("/apps/app-face/feedback")
    assert sent[0].get_method() == "POST"
    with pytest.raises(ValueError):
        apps.submit_app_feedback("app-face", {**value, "outcome": "downloaded_success"})
    assert len(sent) == 1


def test_app_cli_reads_exact_detail_without_search_or_feedback(monkeypatch, capsys):
    monkeypatch.setattr(cli, "fetch_app", lambda app_id, **kw: {"app_view": {"app_id": app_id},
        "received_revision": kw["revision"]})
    monkeypatch.setattr(cli, "fetch_server_results", lambda *a, **k: pytest.fail("no search or usage write"))
    monkeypatch.setattr(cli, "submit_app_feedback", lambda *a, **k: pytest.fail("no implicit feedback"))
    assert cli.main(["--app-detail", "app-face", "--app-revision", "1", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["received_revision"] == 1
    for arguments in (["--app-revision", "1"], ["--app-detail", "app-face", "--document-revision", "2"],
            ["--app-detail", "app-face", "--app-source", "--app-feedback", "unused.json"]):
        with pytest.raises(SystemExit):
            cli.main(arguments)


def test_app_cli_feedback_requires_explicit_action_and_matching_revision(monkeypatch, tmp_path, capsys):
    sent = []
    monkeypatch.setattr(cli, "submit_app_feedback", lambda app_id, payload, **kw:
        sent.append((app_id, payload)) or {"status": "recorded"})
    feedback = tmp_path / "feedback.json"
    payload = {"revision": 1, "outcome": "used", "content": "按已说明条件运行成功", "request_id": "use-real-1"}
    feedback.write_text(json.dumps(payload), encoding="utf-8")
    args = ["--app-detail", "app-face", "--app-revision", "1", "--app-feedback", str(feedback)]
    assert cli.main(args) == 0
    assert sent == [("app-face", payload)]
    assert json.loads(capsys.readouterr().out)["status"] == "recorded"
    with pytest.raises(SystemExit, match="feedback revision"):
        cli.main(["--app-detail", "app-face", "--app-revision", "2", "--app-feedback", str(feedback)])
    assert len(sent) == 1
