from __future__ import annotations

import datetime as dt
import io
import json
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
PLUGIN = ROOT / "plugins/akbs-member-ops"
for path in (PLUGIN / "lib", PLUGIN / "internal/incoming-v2/scripts"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from akbs_member_ops import doc_submit as doc


@pytest.fixture
def prepared(tmp_path):
    original = tmp_path / "独立操作说明.md"
    original.write_text("# 独立操作说明\n确认设备状态后执行操作。\n", encoding="utf-8")
    config = {"member_alias": "auditmember", "member_name": "测试成员", "role": "member",
              "out_dir": str(tmp_path / "artifacts"), "timezone": "Asia/Shanghai"}
    package = doc.prepare_document(source=original, title="独立操作说明", summary="设备状态核对方法",
        date=dt.date(2026, 9, 24), config=config, run_id="20261022-120000-doc")
    return original, package, config


def test_prepare_keeps_original_and_uses_only_minimal_doc_contract(prepared):
    original, package, _config = prepared
    manifest = doc.read_manifest(package)
    assert manifest == {"schema": "knowledge-incoming-package", "schema_version": "2",
        "package_kind": "technical_document", "member_alias": "auditmember", "member_name": "测试成员",
        "date": "2026-09-24", "run_id": "20261022-120000-doc", "tool": "akbs-doc-submit",
        "title": "独立操作说明", "summary": "设备状态核对方法",
        "files": {"document": "materials/document/独立操作说明.md"}}
    assert doc.document_file(package, manifest).read_bytes() == original.read_bytes()
    assert doc.validate_package(package)["status"] == "PASS"
    assert not (package / doc.RECEIPT).exists()


def test_validate_is_read_only_and_detects_original_drift(prepared):
    _source, package, _config = prepared
    before = {path.relative_to(package): path.read_bytes() for path in package.rglob("*") if path.is_file()}
    assert doc.validate_package(package)["status"] == "PASS"
    assert before == {path.relative_to(package): path.read_bytes() for path in package.rglob("*") if path.is_file()}
    doc.document_file(package, doc.read_manifest(package)).write_text("changed", encoding="utf-8")
    result = doc.validate_package(package)
    assert result["status"] == "FAIL"
    assert "changed after preparation" in ";".join(result["errors"])


@pytest.mark.parametrize("relative", ["../outside.md", "/etc/passwd", "materials/document/../outside.md", "materials\\document\\x.md"])
def test_document_path_cannot_escape(prepared, relative):
    _source, package, _config = prepared
    manifest = doc.read_manifest(package)
    manifest["files"]["document"] = relative
    (package / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert doc.validate_package(package)["status"] == "FAIL"


def test_prepare_revision_preserves_server_identity_and_actual_date(tmp_path):
    source = tmp_path / "method.md"
    source.write_text("new method content", encoding="utf-8")
    config = {"member_alias": "auditmember", "member_name": "测试成员", "out_dir": str(tmp_path / "artifacts")}
    package = doc.prepare_document(source=source, title="方法", summary="新增限制说明", date=dt.date(2026, 9, 25),
        config=config, run_id="20261022-120001-doc", document_id="doc-example", expected_revision=1)
    manifest = doc.read_manifest(package)
    assert manifest["document_id"] == "doc-example"
    assert manifest["expected_revision"] == 1
    assert manifest["date"] == "2026-09-25"


def test_invalid_revision_does_not_leave_partial_package(prepared, tmp_path):
    source, _package, config = prepared
    with pytest.raises(ValueError, match="expected_revision"):
        doc.prepare_document(source=source, title="方法", summary="变化", date=dt.date(2026, 9, 26),
            config=config, run_id="20261022-120002-doc", document_id="doc-example")
    assert not (Path(config["out_dir"]) / "pending/20260926/auditmember/20261022-120002-doc").exists()
    assert source.exists()


def test_exact_run_retry_does_not_fall_back(prepared):
    _source, package, config = prepared
    assert doc.latest_prepared(config, "20261022-120000-doc") == package
    with pytest.raises(ValueError, match="missing or ambiguous"):
        doc.latest_prepared(config, "20261022-120000-other")


def test_member_submit_uses_shared_transport_and_pins_receipt(prepared, monkeypatch):
    _source, package, config = prepared
    calls = []
    def request(request, **kwargs):
        calls.append(request)
        assert request.full_url.endswith("/akbs/api/member/auditmember/uploads/doc")
        assert request.get_header("X-akbs-user") == "auditmember"
        with tarfile.open(fileobj=io.BytesIO(request.data), mode="r:gz") as archive:
            assert json.load(archive.extractfile("manifest.json"))["package_kind"] == "technical_document"
            assert archive.extractfile("materials/document/独立操作说明.md").read().startswith(b"# ")
            assert set(archive.getnames()) == {"manifest.json", "materials/document/独立操作说明.md"}
        return {"document_id": "doc-example", "revision": 1, "package_key": "20260924/auditmember/20261022-120000-doc",
            "document_view": {"document_id": "doc-example", "revision": 1, **doc.validate_package(package)["original"]}}, {"request_id": "req_" + "a" * 32}
    monkeypatch.setattr(doc, "request_json_with_metadata", request)
    first = doc.submit_document(package, config)
    second = doc.submit_document(package, config)
    assert first["document_id"] == second["document_id"] == "doc-example"
    assert len(calls) == 2
    assert json.loads((package / doc.RECEIPT).read_text())["revision"] == 1


def test_member_submit_does_not_pin_a_receipt_for_different_original(prepared, monkeypatch):
    _source, package, config = prepared
    monkeypatch.setattr(doc, "request_json_with_metadata", lambda *a, **k: (
        {"document_id": "doc-example", "revision": 1, "package_key": "20260924/auditmember/20261022-120000-doc",
         "document_view": {"document_id": "doc-example", "revision": 1, "filename": "different.md", "size": 1, "sha256": "0" * 64}}, {}))
    from akbs_member_ops.http_client import HttpClientFailure
    with pytest.raises(HttpClientFailure):
        doc.submit_document(package, config)
    assert not (package / doc.RECEIPT).exists()


def test_administrator_submission_uses_management_not_member_route(prepared, monkeypatch):
    _source, package, config = prepared
    config["role"] = "admin"
    monkeypatch.setattr(doc, "request_json_with_metadata", lambda *a, **kw: pytest.fail("must not upload"))
    with pytest.raises(ValueError, match="management Skill"):
        doc.submit_document(package, config)


def test_wrong_profile_cannot_submit_another_author(prepared, monkeypatch):
    _source, package, config = prepared
    config["member_alias"] = "another"
    monkeypatch.setattr(doc, "request_json_with_metadata", lambda *a, **kw: pytest.fail("must not upload"))
    with pytest.raises(ValueError, match="author conflicts"):
        doc.submit_document(package, config)


def test_validate_cli_does_not_fetch_or_upload(prepared, monkeypatch, capsys):
    _source, package, _config = prepared
    monkeypatch.setattr(doc, "installed_plugin_family_status", lambda: {"blocking": False})
    monkeypatch.setattr(doc, "plugin_version_gate_check", lambda *a, **kw: pytest.fail("validation must not fetch"))
    monkeypatch.setattr(doc, "request_json_with_metadata", lambda *a, **kw: pytest.fail("validation must not upload"))
    assert doc.main(["--validate", str(package)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "PASS"


def test_help_does_not_read_profile_or_call_network(monkeypatch):
    monkeypatch.setattr(doc, "load_config", lambda *a, **kw: pytest.fail("help must not read profile"))
    with pytest.raises(SystemExit) as exc:
        doc.main(["--help"])
    assert exc.value.code == 0


def test_wsl_windows_file_uri_is_converted_with_system_wslpath(monkeypatch):
    monkeypatch.setattr(doc.platform, "release", lambda: "6.6.87.1-microsoft-standard-WSL2")
    calls = []
    class Result:
        stdout = "/mnt/c/Users/example/guide.md\n"
    def convert(command, **kwargs):
        calls.append(command)
        return Result()
    monkeypatch.setattr(doc.subprocess, "run", convert)
    assert str(doc.input_path("file:///C:/Users/example/guide.md")) == "/mnt/c/Users/example/guide.md"
    assert calls == [["wslpath", "-u", "C:/Users/example/guide.md"]]
