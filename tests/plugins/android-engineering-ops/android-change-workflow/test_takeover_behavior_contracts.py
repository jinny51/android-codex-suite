from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
PLUGIN = ROOT / "plugins/android-engineering-ops"
WORKFLOW = PLUGIN / "skills/android-change-workflow/SKILL.md"
CAPTURE = PLUGIN / "skills/android-patch-capture/SKILL.md"


def test_change_workflow_keeps_ordered_engineering_gates() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    ordered = (
        "## Gate 0: Active Install Family",
        "## Optional Orchestration Extension",
        "## Gate 1: Requirement and Component",
        "## Gate 2: Knowledge and Source Authority",
        "## Gate 3: Policy and Change Plan",
        "## Gate 4: Implement and Verify by Component",
        "## Gate 5: Capture and Submission",
        "## Final Report",
    )
    positions = [text.index(item) for item in ordered]
    assert positions == sorted(positions)
    for skill in (
        "akbs-knowledge-search",
        "android-source-access",
        "android-remote-channel",
        "android-remote-build-deploy",
        "android-patch-capture",
        "akbs-patch-submit",
    ):
        assert skill in text
    assert "optional integration" in text
    assert "current user task runs final acceptance" in text


def test_every_canonical_skill_requires_target_install_family_before_effects() -> None:
    for name in (
        "android-change-workflow",
        "android-source-access",
        "android-remote-channel",
        "android-remote-build-deploy",
        "android-patch-capture",
    ):
        text = " ".join(
            (PLUGIN / "skills" / name / "SKILL.md")
            .read_text(encoding="utf-8")
            .split()
        )
        assert "install_family.py" in text, name
        assert "--plugin-root" in text, name
        assert "nonzero result" in text, name
        assert "target-only" in text, name

    workflow = WORKFLOW.read_text(encoding="utf-8")
    gate = workflow.split("## Gate 0: Active Install Family", 1)[1].split(
        "## Required Contracts", 1
    )[0]
    assert "local_project" in gate
    assert "adb" in gate
    assert "project/source data" in gate
    assert "subagent result cannot replace" in gate


def test_canonical_capture_and_submit_use_single_incoming_v2_android_change_contract() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    capture = CAPTURE.read_text(encoding="utf-8")
    assert "knowledge-incoming-package/2/android_change" in workflow
    assert "knowledge-incoming-package/2/android_change" in capture
    assert "Every `files.patches` path appears exactly once" in workflow
    assert "only `layer` and `patches`" in capture
    assert "Framework-only" not in capture


def test_legacy_skill_wrappers_are_absent_from_target_plugin() -> None:
    workflow_wrapper = PLUGIN / "skills/android-framework-change-workflow"
    capture_wrapper = PLUGIN / "skills/android-framework-patch-capture"
    assert not workflow_wrapper.exists()
    assert not capture_wrapper.exists()
