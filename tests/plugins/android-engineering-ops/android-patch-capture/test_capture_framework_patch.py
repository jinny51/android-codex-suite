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


def test_multi_component_input_and_repository_bindings_are_exact() -> None:
    capture = load_capture()
    args = argparse.Namespace(
        component_specs=[
            "platform:platform:framework:system:aosp",
            "settings:application:system_app:system_ext:product",
        ],
        change_domain="",
        component_layer="",
        component_type="",
        component_partition="",
        component_ownership="",
        primary_component_id="platform",
        component_qualifier=[],
    )
    components, primary = capture.resolve_components(args)
    assert primary == "platform"
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
    args.repo_component = ["frameworks/base=platform", "packages/apps/Settings=settings"]
    capture.bind_repository_components(args, repositories, components)
    assert [item.repository_id for item in repositories] == ["repo-001", "repo-002"]
    assert [item.component_ids for item in repositories] == [("platform",), ("settings",)]

    args.repo_component = ["frameworks/base=platform"]
    with pytest.raises(SystemExit, match="every captured repository"):
        capture.bind_repository_components(args, repositories, components)


def test_generated_evidence_scope_requires_explicit_cross_layer_binding() -> None:
    capture = load_capture()
    components = [{"id": "platform"}, {"id": "settings"}]
    evidence = [
        {"id": "changed-files", "component_ids": ["platform", "settings"]},
        {"id": "verification-result"},
    ]
    with pytest.raises(SystemExit, match="explicit --evidence-component"):
        capture.bind_generated_evidence_components(evidence, components, [])
    capture.bind_generated_evidence_components(
        evidence,
        components,
        ["verification-result:platform", "verification-result:settings"],
    )
    assert evidence[1]["component_ids"] == ["platform", "settings"]


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
    project, evidence = capture.infer_capture_project_for_change(
        argparse.Namespace(project="unknown", summary="policy", change_id="feature"),
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
    errors = capture.validate_change_scope(
        argparse.Namespace(summary=summary, change_id="batch-collection")
    )
    assert errors


def test_controlled_patch_filename_and_final_run_id_rules() -> None:
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
    assert capture.validate_run_id("20260910-120000-feature") == "20260910-120000-feature"
    with pytest.raises(SystemExit, match="YYYYMMDD"):
        capture.validate_run_id("draft-package")
