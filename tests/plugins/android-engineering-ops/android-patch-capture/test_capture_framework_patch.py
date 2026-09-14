from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[4]
PLUGIN = ROOT / "plugins/android-engineering-ops"
CAPTURE = PLUGIN / "skills/android-patch-capture/scripts/capture_android_patch.py"
if str(PLUGIN / "lib") not in sys.path:
    sys.path.insert(0, str(PLUGIN / "lib"))


def load_capture():
    spec = importlib.util.spec_from_file_location("capture_framework_focused", CAPTURE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_diff_facts_extract_changed_android_facts() -> None:
    capture = load_capture()
    diff = """diff --git a/core/res/res/values/config.xml b/core/res/res/values/config.xml
--- a/core/res/res/values/config.xml
+++ b/core/res/res/values/config.xml
@@ -1,2 +1,2 @@
-<string name=\"removed_key\">old</string>
+<string name=\"added_key\">new</string>
 <string name=\"context_key\">same</string>
"""
    facts = capture.facts_from_diff(diff)
    assert facts["resource_keys"] == ["added_key", "removed_key"]
    assert "context_key" not in facts["resource_keys"]


def test_repository_layer_bindings_are_exact() -> None:
    capture = load_capture()
    repositories = [
        capture.RepositoryCapture(
            source_root="/source/frameworks/base",
            repo_path="frameworks/base",
            git_info={},
            diff_text="diff",
            facts={},
            module="frameworks-base",
            patch_name="rk14-frameworks-base@feature.patch",
            patch_rel="patches/rk14-frameworks-base@feature.patch",
        ),
        capture.RepositoryCapture(
            source_root="/source/packages/apps/Settings",
            repo_path="packages/apps/Settings",
            git_info={},
            diff_text="diff",
            facts={},
            module="settings",
            patch_name="rk14-settings@feature.patch",
            patch_rel="patches/rk14-settings@feature.patch",
        ),
    ]
    layers = capture.capture_layers(
        repositories,
        default_layer="platform",
        repository_layers={"packages/apps/Settings": "application"},
    )
    assert layers == {
        "patches/rk14-frameworks-base@feature.patch": "platform",
        "patches/rk14-settings@feature.patch": "application",
    }
    assert capture.component_rows(layers) == [
        {"layer": "application", "patches": ["patches/rk14-settings@feature.patch"]},
        {"layer": "platform", "patches": ["patches/rk14-frameworks-base@feature.patch"]},
    ]
    with pytest.raises(SystemExit, match="不存在的 repo_path"):
        capture.capture_layers(
            repositories,
            default_layer="platform",
            repository_layers={"unknown/repo": "native"},
        )


def test_component_rows_group_every_patch_once() -> None:
    capture = load_capture()
    rows = capture.component_rows(
        {"patches/a.patch": "native", "patches/b.patch": "native", "patches/c.patch": "build"}
    )
    assert rows == [
        {"layer": "native", "patches": ["patches/a.patch", "patches/b.patch"]},
        {"layer": "build", "patches": ["patches/c.patch"]},
    ]


def test_project_inference_keeps_conflicts_out_of_formal_target() -> None:
    capture = load_capture()
    change = capture.RepositoryCapture(
        source_root="/source/TVE8402M/frameworks/base",
        repo_path="frameworks/base",
        git_info={"branch": "feature/TVA10A2R-policy", "remote": "", "remotes": ""},
        diff_text="diff --git a/A b/A\n",
        facts={"modified_files": ["A"], "modules": [], "symbols": []},
        module="frameworks-base",
        patch_name="rk14-frameworks-base@feature.patch",
        patch_rel="patches/rk14-frameworks-base@feature.patch",
    )
    project, evidence = capture.infer_capture_project_for_feature(
        argparse.Namespace(project="unknown", summary="policy", feature="feature"),
        [change],
        trusted_platform="rk",
    )
    assert project == "unknown"
    assert set(evidence["candidates"]) == {"TVA10A2R", "TVE8402M"}


@pytest.mark.parametrize(
    "summary",
    ["今日补丁合集", "多个独立功能合集"],
)
def test_non_coherent_package_scope_is_rejected(summary: str) -> None:
    capture = load_capture()
    errors = capture.validate_feature_scope(
        argparse.Namespace(summary=summary, feature="batch-collection")
    )
    assert errors


def test_controlled_patch_filename_rules() -> None:
    capture = load_capture()
    good = capture.RepositoryCapture(
        source_root="/source",
        repo_path="frameworks/base",
        git_info={},
        diff_text="diff",
        facts={},
        module="frameworks-base",
        patch_name="rk14-frameworks-base@feature.patch",
        patch_rel="patches/rk14-frameworks-base@feature.patch",
    )
    assert capture.validate_patch_asset_names([good]) == []


def test_historical_import_rejects_android_root_combined_patch(tmp_path: Path) -> None:
    capture = load_capture()
    patch = tmp_path / "combined.patch"
    patch.write_text(
        "diff --git a/frameworks/base/core/Test.java b/frameworks/base/core/Test.java\n"
        "--- a/frameworks/base/core/Test.java\n"
        "+++ b/frameworks/base/core/Test.java\n"
        "@@ -1 +1 @@\n-old\n+new\n"
        "diff --git a/device/vendor/product/config.mk b/device/vendor/product/config.mk\n"
        "--- a/device/vendor/product/config.mk\n"
        "+++ b/device/vendor/product/config.mk\n"
        "@@ -1 +1 @@\n-old\n+new\n",
        encoding="utf-8",
    )
    args = argparse.Namespace(
        patch_artifact=[str(patch)],
        patch_repo_path=["."],
        module=None,
        remote_source_root="",
    )
    with pytest.raises(SystemExit, match="每个仓库分别提供"):
        capture.collect_patch_artifact_captures(args, "rk14", "feature")


def test_historical_import_rejects_workspace_relative_paths_for_declared_repo(
    tmp_path: Path,
) -> None:
    capture = load_capture()
    patch = tmp_path / "workspace-relative.patch"
    patch.write_text(
        "diff --git a/frameworks/base/core/Test.java b/frameworks/base/core/Test.java\n"
        "--- a/frameworks/base/core/Test.java\n"
        "+++ b/frameworks/base/core/Test.java\n"
        "@@ -1 +1 @@\n-old\n+new\n",
        encoding="utf-8",
    )
    args = argparse.Namespace(
        patch_artifact=[str(patch)],
        patch_repo_path=["frameworks/base"],
        module=None,
        remote_source_root="",
    )
    with pytest.raises(SystemExit, match="相对于 Android 源码顶层"):
        capture.collect_patch_artifact_captures(args, "rk14", "feature")
