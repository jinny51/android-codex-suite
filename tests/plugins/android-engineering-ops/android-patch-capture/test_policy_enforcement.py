from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]
PLUGIN_ROOT = REPO_ROOT / "plugins/android-engineering-ops"
PLUGIN_LIB = PLUGIN_ROOT / "lib"
CAPTURE_SCRIPT = (
    PLUGIN_ROOT
    / "skills/android-patch-capture/scripts/capture_android_patch.py"
)
if str(PLUGIN_LIB) not in sys.path:
    sys.path.insert(0, str(PLUGIN_LIB))


def load_capture_module():
    spec = importlib.util.spec_from_file_location(
        "capture_android_patch_policy_test", CAPTURE_SCRIPT
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def diff_with(*added: str, removed: tuple[str, ...] = ()) -> str:
    lines = [
        "diff --git a/X.java b/X.java",
        "--- a/X.java",
        "+++ b/X.java",
        "@@ -1 +1 @@",
    ]
    lines.extend(f"-{line}" for line in removed)
    lines.extend(f"+{line}" for line in added)
    return "\n".join(lines) + "\n"


def capture_for(module, diff_text: str):
    return module.RepositoryCapture(
        source_root="/source",
        repo_path="frameworks/base",
        git_info={},
        diff_text=diff_text,
        facts=module.facts_from_diff(diff_text),
        module="frameworks-base",
        patch_name="rk14-frameworks-base@policy.patch",
        patch_rel="patches/rk14-frameworks-base@policy.patch",
        repository_id="repo-001",
        component_ids=("framework-core",),
    )


def arguments(**overrides: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "workflow_contract": "current_codex_skill",
        "implementation_origin": "codex",
        "policy_member_alias": "member01",
        "policy_profile_name": "member01",
        "change_domain": "framework",
        "component": {
            "id": "framework-core",
            "layer": "platform",
            "type": "framework",
            "partition": "system",
            "ownership": "aosp",
        },
        "components": [
            {
                "id": "framework-core",
                "layer": "platform",
                "type": "framework",
                "partition": "system",
                "ownership": "aosp",
            }
        ],
        "primary_component_id": "framework-core",
        "allow_banned_logs": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_current_codex_capture_records_versioned_matching_policy_evidence() -> None:
    module = load_capture_module()
    patch = diff_with(
        "//member01 20260828@{",
        "customLogic();",
        "//member01 20260828@}",
    )
    result = module.coding_standard_check(arguments(), [capture_for(module, patch)])
    assert result["result"] == "PASS"
    assert result["policy_id"] == "android-change-policy"
    assert result["policy_version"] == "1.0.0"
    assert result["member_profile"] == "member01"
    repository = result["repositories"][0]
    assert repository["marker_contract"] == "paired-current"
    assert repository["marker_pair_count"] == 1
    assert repository["marker_aliases"] == ["member01"]


def test_current_codex_capture_rejects_legacy_wrong_or_removed_only_markers() -> None:
    module = load_capture_module()
    cases = (
        diff_with("//member01 20260828@", "customLogic();"),
        diff_with(
            "//someone_else 20260828@{",
            "customLogic();",
            "//someone_else 20260828@}",
        ),
        diff_with("customLogic();", removed=("//member01 20260828@ historical",)),
    )
    for patch in cases:
        result = module.coding_standard_check(arguments(), [capture_for(module, patch)])
        assert result["result"] == "FAIL"
        assert result["repositories"][0]["marker_errors"]


def test_historical_import_keeps_original_legacy_author() -> None:
    module = load_capture_module()
    patch = diff_with("//legacy_author 20251016@")
    result = module.coding_standard_check(
        arguments(
            workflow_contract="historical_import",
            implementation_origin="historical",
            policy_member_alias="",
            policy_profile_name="",
        ),
        [capture_for(module, patch)],
    )
    assert result["result"] == "PASS"
    repository = result["repositories"][0]
    assert repository["marker_contract"] == "legacy-compatible"
    assert repository["marker_aliases"] == ["legacy_author"]


def test_each_slash_comment_file_requires_its_own_marker() -> None:
    module = load_capture_module()
    patch = diff_with(
        "//member01 20260828@{",
        "firstChange();",
        "//member01 20260828@}",
    ) + "\n".join(
        (
            "diff --git a/Y.cpp b/Y.cpp",
            "--- a/Y.cpp",
            "+++ b/Y.cpp",
            "@@ -1 +1 @@",
            "+secondChange();",
            "",
        )
    )
    result = module.coding_standard_check(arguments(), [capture_for(module, patch)])
    assert result["result"] == "FAIL"
    repository = result["repositories"][0]
    assert len(repository["marker_files"]) == 2
    assert any("Y.cpp" in error for error in repository["marker_errors"])


def test_file_without_slash_comment_syntax_is_not_broken_by_policy() -> None:
    module = load_capture_module()
    patch = "\n".join(
        (
            "diff --git a/res/values/strings.xml b/res/values/strings.xml",
            "--- a/res/values/strings.xml",
            "+++ b/res/values/strings.xml",
            "@@ -1 +1 @@",
            "+<string name=\"policy\">Policy</string>",
            "",
        )
    )
    result = module.coding_standard_check(arguments(), [capture_for(module, patch)])
    assert result["result"] == "PASS"
    marker_file = result["repositories"][0]["marker_files"][0]
    assert marker_file["result"] == "NOT_APPLICABLE"
    assert marker_file["comment_adapter"] == "NOT_APPLICABLE_NO_ADAPTER"


def test_mixed_change_accepts_historical_marker_and_requires_current_pair() -> None:
    module = load_capture_module()
    patch = diff_with(
        "//legacy_author 20251016@",
        "//member01 20260828@{",
        "currentChange();",
        "//member01 20260828@}",
    )
    result = module.coding_standard_check(
        arguments(implementation_origin="mixed"),
        [capture_for(module, patch)],
    )
    assert result["result"] == "PASS"
    assert result["repositories"][0]["marker_aliases"] == [
        "legacy_author",
        "member01",
    ]


def test_final_historical_import_rejects_missing_marker() -> None:
    module = load_capture_module()
    result = module.coding_standard_check(
        arguments(
            workflow_contract="historical_import",
            implementation_origin="historical",
            policy_member_alias="",
            policy_profile_name="",
        ),
        [capture_for(module, diff_with("historicalChange();"))],
    )
    assert result["result"] == "FAIL"
    repository = result["repositories"][0]
    assert repository["marker_exception"] is None
    assert repository["marker_files"][0]["result"] == "FAIL"


def test_capture_rejects_string_forgery_empty_pair_and_unwrapped_added_code() -> None:
    module = load_capture_module()
    cases = (
        diff_with(
            'String a = "//member01 20260828@{";',
            'String b = "//member01 20260828@}";',
        ),
        diff_with(
            "//member01 20260828@{",
            "//member01 20260828@}",
            "unwrappedChange();",
        ),
    )
    for patch in cases:
        result = module.coding_standard_check(arguments(), [capture_for(module, patch)])
        assert result["result"] == "FAIL"
        assert result["repositories"][0]["marker_errors"]


def test_capture_rejects_marker_pairs_split_across_hunks() -> None:
    module = load_capture_module()
    patch = "\n".join(
        (
            "diff --git a/X.java b/X.java",
            "--- a/X.java",
            "+++ b/X.java",
            "@@ -1 +1,2 @@",
            "+//member01 20260828@{",
            "+firstChange();",
            "@@ -20 +21,2 @@",
            "+secondChange();",
            "+//member01 20260828@}",
            "",
        )
    )
    result = module.coding_standard_check(arguments(), [capture_for(module, patch)])
    assert result["result"] == "FAIL"
    assert any(
        "hunk" in error for error in result["repositories"][0]["marker_errors"]
    )
