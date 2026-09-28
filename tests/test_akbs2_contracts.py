from __future__ import annotations

import copy
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
from android_engineering_ops.knowledge_rules import VALID_FRAMEWORK_PLATFORMS  # noqa: E402
from akbs_intake.patch.assets import validate_patch_readme  # noqa: E402
from akbs_intake.patch.capture_import import copy_patch_capture_packages  # noqa: E402
from akbs_intake.patch.evidence import (  # noqa: E402
    search_receipt_from_capture,
    select_search_before_change_payload,
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
