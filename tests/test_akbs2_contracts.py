from __future__ import annotations

import copy
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
