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
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from android_engineering_ops.json_contract import (  # noqa: E402
    ContractValidationError,
    validate_document,
)
from android_engineering_ops.knowledge_rules import VALID_FRAMEWORK_PLATFORMS  # noqa: E402


PACKAGE_SCHEMA = ROOT / "contracts/incoming/v2/knowledge-incoming-package.schema.json"
PACKAGE_FIXTURE = ROOT / "contracts/incoming/v2/fixtures/patch.manifest.json"


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_android_change_uses_one_stable_v1_schema() -> None:
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


def test_v1_component_rows_reject_the_retired_flat_shape() -> None:
    invalid = copy.deepcopy(load(PACKAGE_FIXTURE))
    invalid["components"] = ["platform"]
    with pytest.raises(ContractValidationError):
        validate_document(invalid, PACKAGE_SCHEMA)


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
