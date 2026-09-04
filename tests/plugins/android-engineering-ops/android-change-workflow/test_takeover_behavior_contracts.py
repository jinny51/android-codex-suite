from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
PLUGIN = ROOT / "plugins/android-engineering-ops"
WORKFLOW = PLUGIN / "skills/android-change-workflow/SKILL.md"
CAPTURE = PLUGIN / "skills/android-patch-capture/SKILL.md"


def test_change_workflow_keeps_ordered_controller_gates() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    ordered = (
        "## Gate 0: Active Install Family",
        "## Gate 1: Requirement and Component",
        "## Gate 2: Knowledge and Source Authority",
        "## Optional Practices Resolution",
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
    assert "controller runs final acceptance" in text


def test_every_canonical_skill_requires_target_install_family_before_effects() -> None:
    for name in (
        "android-change-policy",
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
    assert "worker result cannot replace" in gate


def test_canonical_capture_and_submit_never_fall_back_to_v1() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    capture = CAPTURE.read_text(encoding="utf-8")
    assert "validated package from any supported layer" in workflow
    assert "validated canonical component" in capture
    assert "strict v2 local validation and byte-preserving prepare" in workflow
    assert "zero side effects" in workflow
    assert "fall back to v1" in workflow
    assert "Framework-only" not in capture


def test_legacy_skill_wrappers_are_absent_from_target_plugin() -> None:
    workflow_wrapper = PLUGIN / "skills/android-framework-change-workflow"
    capture_wrapper = PLUGIN / "skills/android-framework-patch-capture"
    assert not workflow_wrapper.exists()
    assert not capture_wrapper.exists()
