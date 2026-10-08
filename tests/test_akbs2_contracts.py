from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MEMBER = ROOT / "plugins/akbs-member-ops"
CORE = ROOT / "plugins/android-engineering-ops"
LIB = CORE / "lib"
MEMBER_LIB = MEMBER / "lib"
MEMBER_SCRIPTS = MEMBER / "internal/incoming-v2/scripts"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))
for path in (MEMBER_LIB, MEMBER_SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from android_engineering_ops.json_contract import (  # noqa: E402
    ContractValidationError,
    validate_document,
)
from android_engineering_ops.knowledge_rules import VALID_FRAMEWORK_PLATFORMS, classify_pre_change_search  # noqa: E402
from akbs_intake.patch.assets import validate_patch_readme  # noqa: E402
from akbs_intake.patch.capture_import import copy_patch_capture_packages  # noqa: E402
from akbs_intake.patch.evidence import (  # noqa: E402
    search_receipt_from_capture,
    select_search_before_change_payload,
)
from akbs_intake.patch import builder  # noqa: E402
from akbs_intake.patch.validation import validate_patch_pre_change_search  # noqa: E402
from akbs_intake.search_usage import (  # noqa: E402
    search_payload_missing_required_pre_change_search,
    search_payload_needs_closed_decision,
    workflow_contract_requires_pre_change_search,
)


PACKAGE_SCHEMA = ROOT / "contracts/incoming/v2/knowledge-incoming-package.schema.json"
PACKAGE_FIXTURE = ROOT / "contracts/incoming/v2/fixtures/patch.manifest.json"


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_android_change_uses_one_current_incoming_v2_schema() -> None:
    copies = [
        PACKAGE_SCHEMA,
        MEMBER / "internal/incoming-v2/references/knowledge-incoming-package.schema.json",
    ]
    assert len({path.read_bytes() for path in copies}) == 1
    package = load(PACKAGE_FIXTURE)
    validate_document(package, PACKAGE_SCHEMA)
    assert package["schema"] == "knowledge-incoming-package"
    assert package["schema_version"] == "2"
    assert package["package_kind"] == "android_change"
    assert package["components"] == [
        {"layer": "platform", "patches": ["patches/frameworks-base.patch"]}
    ]


def test_component_rows_require_patch_to_layer_mapping() -> None:
    invalid = copy.deepcopy(load(PACKAGE_FIXTURE))
    invalid["components"] = ["platform"]
    with pytest.raises(ContractValidationError):
        validate_document(invalid, PACKAGE_SCHEMA)

def test_readme_explicit_contract_uri_must_match_manifest(tmp_path: Path) -> None:
    readme = tmp_path / "README.md"
    readme.write_text(
        "\n".join(
            (
                "# sample",
                "## 功能描述",
                "knowledge-incoming-package/1/android_change",
                "## 修改点",
                "- sample",
                "## 日志控制",
                "无",
                "## SystemProperties",
                "无",
                "## 字符串国际化",
                "无",
                "## 可回滚性",
                "可回滚",
            )
        ),
        encoding="utf-8",
    )
    errors = validate_patch_readme(
        readme,
        expected_version="2",
        expected_kind="android_change",
    )
    assert any("knowledge-incoming-package/2/android_change" in error for error in errors)


def test_controlled_platform_tokens_are_unchanged() -> None:
    assert set(VALID_FRAMEWORK_PLATFORMS) == {"mtk", "rk", "unisoc"}


def test_new_extension_contract_replaces_only_the_retired_control_protocol() -> None:
    schema = ROOT / "contracts/android-orchestration-extension/v1/extension.schema.json"
    packaged = CORE / "contracts/android-orchestration-extension/v1/extension.schema.json"
    manifest = ROOT / "plugins/jinny-android-practices/contracts/android-orchestration-extension/v1/extension.json"
    assert schema.read_bytes() == packaged.read_bytes()
    validate_document(load(manifest), schema)
    assert not (ROOT / "contracts/android-practices-provider").exists()
    assert not (ROOT / "contracts/android-change-workflow").exists()
    assert (ROOT / "contracts/incoming/v2/knowledge-incoming-package.schema.json").is_file()
    assert not (ROOT / "contracts/incoming/v2/akbs-android-change-package.schema.json").exists()


def _capture_with_search_receipt(tmp_path: Path, evidence: dict) -> tuple[Path, bytes]:
    capture = tmp_path / "capture"
    capture.mkdir()
    (capture / "README.md").write_text("# product change\n", encoding="utf-8")
    (capture / "patches").mkdir()
    raw_patch = b"diff --git a/services/Example.java b/services/Example.java\n"
    (capture / "patches/product.patch").write_bytes(raw_patch)
    (capture / "evidence").mkdir()
    (capture / "evidence/search.json").write_bytes(b'{"schema": "android-knowledge-search-usage", "schema_version": "1", "reuse": {"decision": "unknown"}}\r\n')
    manifest = {
        "package_type": "android_feature_patch", "readme": "README.md",
        "components": [{"layer": "platform", "patches": ["patches/product.patch"]}],
        "patches": [{"path": "patches/product.patch", "repo_path": "frameworks/base"}],
        "evidence": [evidence],
    }
    (capture / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return capture, raw_patch


@pytest.mark.parametrize("marker", ["", None, False, 0, [], {}, "not-a-hash"])
def test_present_bad_search_receipt_marker_never_becomes_legacy(tmp_path: Path, marker) -> None:
    item = {"kind": "search_before_change", "path": "evidence/search.json",
            "source_receipt_sha256": marker}
    capture, _ = _capture_with_search_receipt(tmp_path, item)
    with pytest.raises(SystemExit, match="标记无效"):
        copy_patch_capture_packages(tmp_path / "incoming", [str(capture)], "TVI2343R", "validated")
    # A caller cannot avoid validation by feeding a malformed imported marker
    # directly to receipt selection. It must not return None/aggregate fallback.
    with pytest.raises(SystemExit, match="标记无效"):
        search_receipt_from_capture(capture, [item])


@pytest.mark.parametrize("changes", [{"kind": "source"}, {"path": ""}, {"path": None}])
def test_exact_search_receipt_marker_requires_search_kind_and_path(tmp_path: Path, changes: dict) -> None:
    item = {"kind": "search_before_change", "path": "evidence/search.json",
            "source_receipt_sha256": "a" * 64, **changes}
    capture, _ = _capture_with_search_receipt(tmp_path, item)
    with pytest.raises(SystemExit, match="标记无效"):
        copy_patch_capture_packages(tmp_path / "incoming", [str(capture)], "TVI2343R", "validated")
    with pytest.raises(SystemExit, match="标记无效"):
        search_receipt_from_capture(capture, [item])


def test_exact_receipt_copy_preserves_crlf_bytes_and_unknown_priority(tmp_path: Path) -> None:
    item = {"kind": "search_before_change", "path": "evidence/search.json"}
    capture, raw_patch = _capture_with_search_receipt(tmp_path, item)
    raw = (capture / item["path"]).read_bytes()
    marker = hashlib.sha256(raw).hexdigest()
    manifest = load(capture / "manifest.json")
    manifest["evidence"][0]["source_receipt_sha256"] = marker
    (capture / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    incoming = tmp_path / "incoming"
    patches, entries, *_ = copy_patch_capture_packages(incoming, [str(capture)], "TVI2343R", "validated")
    assert (incoming / patches[0]["path"]).read_bytes() == raw_patch
    assert (incoming / entries[0]["path"]).read_bytes() == raw
    exact = search_receipt_from_capture(incoming, entries)
    assert exact is not None
    assert exact["source_receipt_json"].encode("utf-8") == raw
    assert exact["source_receipt_sha256"] == marker
    assert select_search_before_change_payload(
        capture_search_payload=exact["payload"],
        member_search_payload={"reuse": {"decision": "not_found"}},
        capture_has_member_decision=False,
        capture_is_exact_receipt=True,
    ) == exact["payload"]


def test_absent_search_receipt_marker_retains_legacy_selection(tmp_path: Path) -> None:
    item = {"kind": "search_before_change", "path": "evidence/search.json"}
    capture, raw_patch = _capture_with_search_receipt(tmp_path, item)
    incoming = tmp_path / "incoming"
    patches, entries, *_ = copy_patch_capture_packages(incoming, [str(capture)], "TVI2343R", "validated")
    assert (incoming / patches[0]["path"]).read_bytes() == raw_patch
    assert "source_receipt_sha256" not in entries[0]
    assert search_receipt_from_capture(incoming, entries) is None
    member_payload = {"reuse": {"decision": "reference_only"}}
    assert select_search_before_change_payload(
        capture_search_payload={}, member_search_payload=member_payload,
        capture_has_member_decision=False,
    ) == member_payload


def _unknown_search_payload() -> dict:
    return {
        "schema": "android-knowledge-search-usage", "schema_version": "1",
        "member_alias": "member-test", "searched": True,
        "queries": ["display policy"], "results": [{"id": "unclassified-case"}],
        "target_environment": {"project": "TVI2343R", "platform": "rk", "android_version": "14"},
        "source": "local_jsonl_fallback", "decision": "unknown", "reuse_decision": "unknown",
        "targets": [], "reason": "Search health and applicability are not established.",
    }


@pytest.mark.parametrize("receipt_mode", ["exact", "legacy", "missing"])
def test_current_builder_never_borrows_another_same_day_search(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, receipt_mode: str,
) -> None:
    item = {"kind": "search_before_change", "path": "evidence/search.json"}
    capture, _ = _capture_with_search_receipt(tmp_path, item)
    payload = _unknown_search_payload()
    if receipt_mode == "legacy":
        payload.pop("schema")
        payload.pop("schema_version")
    raw = (json.dumps(payload) + "\r\n").encode("utf-8")
    (capture / item["path"]).write_bytes(raw)
    manifest = load(capture / "manifest.json")
    manifest["workflow_contract"] = "current_codex_skill"
    if receipt_mode == "exact":
        manifest["evidence"][0]["source_receipt_sha256"] = hashlib.sha256(raw).hexdigest()
    elif receipt_mode == "missing":
        manifest["evidence"] = []
    (capture / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    def wrong_same_day_search(*args, **kwargs):
        pytest.fail("current capture must not consult same-day search from TVE9999U/unisoc13")

    monkeypatch.setattr(builder, "search_usage_payload", wrong_same_day_search)
    package = _build_search_package(tmp_path, capture, monkeypatch)
    evidence = load(package / "materials/evidence/search_before_change.json")
    if receipt_mode == "missing":
        assert evidence["payload"]["searched"] is False
        assert evidence["payload"]["results"] == []
        assert load(package / "manifest.json")["package_status"] == "candidate"
    else:
        assert evidence["payload"] == payload
        assert evidence["payload"]["reuse_decision"] == "unknown"
    if receipt_mode == "exact":
        assert evidence["source_receipt_json"].encode("utf-8") == raw
        assert evidence["source_receipt_sha256"] == hashlib.sha256(raw).hexdigest()
    else:
        assert "source_receipt_json" not in evidence
        assert "source_receipt_sha256" not in evidence


def _build_search_package(tmp_path: Path, capture: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(builder, "same_day_daily_report_run_ids", lambda *args: [])
    monkeypatch.setattr(builder, "related_report_project_clues", lambda *args, **kwargs: [])
    return builder.build_patch_package(
        dt.date(2026, 10, 8),
        {"member_alias": "member-test", "member_name": "Member Test", "out_dir": str(tmp_path / "incoming")},
        run_id="display-policy", patch_package_paths=[str(capture)],
        project="TVI2343R", summary="Display policy", status="candidate",
        platform_override="rk", android_version_override="14",
        incoming_schema_version="2", framework_optional_evidence_kinds=set(),
        validate_package_fn=lambda package: {"ok": True},
        write_package_source_fn=lambda *args: {}, plugin_install_metadata_fn=lambda: {},
    )


@pytest.mark.parametrize("workflow", ["manual_import", "historical_import"])
def test_import_builder_retains_existing_search_reader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, workflow: str,
) -> None:
    capture, _ = _capture_with_search_receipt(
        tmp_path, {"kind": "search_before_change", "path": "evidence/search.json"},
    )
    manifest = load(capture / "manifest.json")
    manifest["workflow_contract"] = workflow
    (capture / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    recorded = {"searched": True, "decision": "reference_only", "reuse_decision": "reference_only"}
    calls = []
    monkeypatch.setattr(builder, "search_usage_payload", lambda *args, **kwargs: calls.append(args) or recorded)
    package = _build_search_package(tmp_path, capture, monkeypatch)
    assert len(calls) == 1
    assert load(package / "materials/evidence/search_before_change.json")["payload"] == recorded


@pytest.mark.parametrize("package_status,mutation", [
    ("validated", "none"), ("candidate", "none"), ("candidate", "hash"),
    ("candidate", "environment"), ("validated", "not_searched"),
])
def test_unknown_preserves_candidate_facts_and_validated_gate(package_status: str, mutation: str) -> None:
    payload = _unknown_search_payload()
    if mutation == "environment":
        payload["target_environment"]["project"] = "TVE9999U"
    elif mutation == "not_searched":
        payload["searched"] = False
    raw = json.dumps(payload)
    evidence = {
        "payload": payload, "source_receipt_json": raw,
        "source_receipt_sha256": "a" * 64 if mutation == "hash" else hashlib.sha256(raw.encode()).hexdigest(),
    }
    errors, warnings = [], []
    validate_patch_pre_change_search(
        manifest={"workflow_contract": "current_codex_skill", "member_alias": "member-test",
                  "project": "TVI2343R", "platform": "rk", "android_version": "14"},
        evidence_by_kind={"search_before_change": evidence}, package_status=package_status,
        workflow_contract_requires_pre_change_search=workflow_contract_requires_pre_change_search,
        search_payload_missing_required_pre_change_search=search_payload_missing_required_pre_change_search,
        search_payload_needs_closed_decision=search_payload_needs_closed_decision,
        errors=errors, warnings=warnings,
    )
    assert bool(errors) is (package_status == "validated" or mutation != "none")
    expected_error = {"hash": "hash", "environment": "目标环境", "not_searched": "未发生"}.get(mutation)
    if package_status == "validated" and mutation == "none":
        expected_error = "闭合搜索使用决策"
    if expected_error:
        assert any(expected_error in error for error in errors)
    assert payload["decision"] == payload["reuse_decision"] == "unknown"
    assert payload["targets"] == []
    assert classify_pre_change_search(
        payload, workflow_contract="current_codex_skill", package_status=package_status,
    )["validity_score_effect"] == "no_search_loop_score"
