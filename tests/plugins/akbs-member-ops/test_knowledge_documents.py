from __future__ import annotations

import copy
import hashlib
import io
import json
from pathlib import Path
import sys
import urllib.error

import pytest

ROOT = Path(__file__).resolve().parents[3]
LIB = ROOT / "plugins/akbs-member-ops/lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from akbs_member_ops.http_client import HttpClientFailure
from akbs_member_ops.knowledge_search import api, cli, documents as docs
from akbs_member_ops.knowledge_search.formatting import format_markdown


ORIGINAL = "# 升级操作\n检查升级条件与回退限制。\n".encode()


def document_view(*, revision=2, url="/akbs/api/member/me/documents/doc-upgrade/original?revision=2"):
    return {"document_id": "doc-upgrade", "revision": revision, "title": "升级操作",
        "summary": "设备升级与回退限制", "document_date": "2026-09-24", "member_alias": "auditmember",
        "member_name": "测试成员", "filename": "升级操作.md", "sha256": hashlib.sha256(ORIGINAL).hexdigest(),
        "size": len(ORIGINAL), "original_url": url, "review_status": "published", "published_revision": 2}


def doc_row():
    view = document_view()
    return {"type": "doc", "document_id": view["document_id"], "revision": view["revision"],
        "knowledge_id": view["document_id"], "title": view["title"], "summary": view["summary"],
        "content": "先检查设备状态，再选择升级方式。", "applicability": "适用于指定设备升级操作；保留原限制。",
        "member_alias": view["member_alias"], "member_name": view["member_name"],
        "source_package_key": "20260924/auditmember/20261022-120000-doc", "score": 1.2,
        "reuse_grade": "reference_only", "original": {"filename": view["filename"], "sha256": view["sha256"],
        "size": view["size"], "url": view["original_url"]}}


def search_payload():
    return {"schema": api.SEARCH_RESPONSE_SCHEMA, "search_mode": api.SEARCH_MODE, "query": "升级操作",
        "result_type": "doc", "result_state": "matches", "completeness": "complete", "reason_code": "",
        "not_found": False, "target_environment": {"project": "", "platform": "", "android_version": ""},
        "filters": {"component": {"component_layer": []}},
        "pagination": {"offset": 0, "limit": 8, "total": 1, "has_more": False},
        "projection": {"ready": True, "complete": True, "reason_code": "", "generation": 1,
            "eligible_count": 1, "indexed_count": 1, "isolated_count": 0}, "results": [doc_row()]}


def test_doc_search_is_typed_without_patch_environment_or_bindings():
    payload = search_payload()
    api.validate_server_payload(payload)
    rows = api.normalize_server_results(payload)
    assert rows[0]["kind"] == "doc"
    assert cli.result_id(rows[0]) == "doc-upgrade"
    usage = cli.usage_result(rows[0])
    assert usage["revision"] == 2 and usage["original"]["sha256"] == hashlib.sha256(ORIGINAL).hexdigest()
    assert "case_id" not in rows[0] and "required_bindings" not in rows[0]
    rendered = format_markdown(None, "升级操作", rows, None, source="server_api", search_mode=api.SEARCH_MODE)
    assert "文档参考" in rendered and "升级操作.md" in rendered
    assert "SHA-256" in rendered and "doc-upgrade / 2" in rendered
    cli.validate_reuse_decision(decision="reference_only", targets=["doc-upgrade"], results=rows)
    with pytest.raises(SystemExit, match="implementation"):
        cli.validate_reuse_decision(decision="reuse", targets=["doc-upgrade"], results=rows)


@pytest.mark.parametrize("key,value", [("revision", True), ("revision", 0), ("knowledge_id", "other"),
    ("reuse_grade", "direct_reuse_candidate"), ("applicability", []), ("case_id", "case-unrelated")])
def test_doc_search_rejects_mixed_or_invalid_identity(key, value):
    payload = search_payload()
    payload["results"][0][key] = value
    with pytest.raises(HttpClientFailure):
        api.validate_server_payload(payload)


def test_doc_search_json_schema_keeps_patch_branch_and_covers_exact_doc_fields():
    schema = json.loads((ROOT / "plugins/akbs-member-ops/contracts/knowledge/member-knowledge-search-v2.schema.json").read_text())
    assert "required_bindings" in schema["$defs"]["patchKnowledgeResult"]["properties"]
    doc_schema = schema["$defs"]["docResult"]
    assert set(doc_schema["properties"]) == set(doc_schema["required"]) == set(doc_row())
    assert doc_schema["properties"]["type"]["const"] == "doc"
    assert doc_schema["properties"]["reuse_grade"]["const"] == "reference_only"
    assert {branch["$ref"] for branch in schema["properties"]["results"]["items"]["oneOf"]} == {
        "#/$defs/patchKnowledgeResult", "#/$defs/docResult"}


def test_doc_detail_uses_profile_headers_and_exact_revision(monkeypatch):
    seen = []
    monkeypatch.setattr(docs, "member_api_base_url", lambda: ("http://akbs.test:8088", "fixture"))
    monkeypatch.setattr(docs, "member_request_headers", lambda: {"X-AKBS-User": "auditmember"})
    def fetch(request, **kwargs):
        seen.append(request)
        return {"document_view": document_view(), "knowledge": {"title": "升级操作", "summary": "限制",
            "content": "正式知识全文", "applicability": "已审条件", "revision": 2}}
    monkeypatch.setattr(docs, "request_json", fetch)
    result = docs.fetch_document("doc-upgrade", revision=2)
    assert seen[0].full_url == "http://akbs.test:8088/akbs/api/member/me/documents/doc-upgrade?revision=2"
    assert seen[0].get_header("X-akbs-user") == "auditmember"
    assert "正式知识全文" in docs.format_document_detail(result)
    with pytest.raises(HttpClientFailure):
        docs.fetch_document("doc-upgrade", revision=1)


def test_doc_detail_refuses_mismatched_reviewed_knowledge(monkeypatch):
    monkeypatch.setattr(docs, "member_api_base_url", lambda: ("http://akbs.test", "fixture"))
    monkeypatch.setattr(docs, "member_request_headers", lambda: {})
    monkeypatch.setattr(docs, "request_json", lambda *a, **k: {"document_view": document_view(),
        "knowledge": {"title": "old", "summary": "old", "content": "old", "applicability": "old", "revision": 1}})
    with pytest.raises(HttpClientFailure):
        docs.fetch_document("doc-upgrade", revision=2)


@pytest.fixture
def download_fixture(monkeypatch):
    monkeypatch.setattr(docs, "member_api_base_url", lambda: ("http://akbs.test:8088", "fixture"))
    monkeypatch.setattr(docs, "member_request_headers", lambda: {"X-AKBS-User": "auditmember"})
    monkeypatch.setattr(docs, "require_safe_artifact_path", lambda path, **kwargs: path)
    payload = {"document_view": document_view()}
    monkeypatch.setattr(docs, "fetch_document", lambda *a, **k: copy.deepcopy(payload))
    requests = []
    def open_original(request, timeout):
        requests.append(request)
        return io.BytesIO(ORIGINAL)
    monkeypatch.setattr(docs, "_open_original", open_original)
    return payload, requests


def test_doc_download_verifies_exact_bytes_and_does_not_claim_reuse(download_fixture, tmp_path):
    _payload, requests = download_fixture
    target = tmp_path / "document.md"
    result = docs.download_document("doc-upgrade", target, revision=2)
    assert target.read_bytes() == ORIGINAL
    assert requests[0].get_header("X-akbs-user") == "auditmember"
    assert result["verified_original"] is True and result["reuse_outcome"] == "not_started"
    with pytest.raises(ValueError, match="already exists"):
        docs.download_document("doc-upgrade", target, revision=2)
    assert len(requests) == 1


@pytest.mark.parametrize("url", ["https://other.test/doc.md", "//other.test/doc.md", "http://user:pass@akbs.test:8088/doc.md"])
def test_doc_download_does_not_send_identity_cross_origin(download_fixture, tmp_path, url):
    payload, requests = download_fixture
    payload["document_view"]["original_url"] = url
    with pytest.raises(HttpClientFailure):
        docs.download_document("doc-upgrade", tmp_path / "document.md", revision=2)
    assert not requests


@pytest.mark.parametrize("bad_field,value", [("sha256", "0" * 64), ("size", len(ORIGINAL) - 1)])
def test_doc_download_bad_metadata_leaves_no_output(download_fixture, tmp_path, bad_field, value):
    payload, _requests = download_fixture
    payload["document_view"][bad_field] = value
    target = tmp_path / "document.md"
    with pytest.raises(HttpClientFailure):
        docs.download_document("doc-upgrade", target, revision=2)
    assert not target.exists()


def test_doc_redirect_is_not_followed():
    request = type("Request", (), {})()
    assert docs._NoRedirect().redirect_request(request, None, 302, "Found", {}, "https://other.test") is None


def test_doc_cli_detail_is_read_only_exact_version(monkeypatch, capsys):
    monkeypatch.setattr(cli, "fetch_document", lambda doc_id, **kw: {"document_view": document_view(), "received_revision": kw["revision"]})
    monkeypatch.setattr(cli, "fetch_server_results", lambda *a, **k: pytest.fail("no search or usage write"))
    assert cli.main(["--document-detail", "doc-upgrade", "--document-revision", "2", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["received_revision"] == 2
    with pytest.raises(SystemExit):
        cli.main(["--document-detail", "doc-upgrade", "--download-document"])
    with pytest.raises(SystemExit):
        cli.main(["--document-revision", "2"])
