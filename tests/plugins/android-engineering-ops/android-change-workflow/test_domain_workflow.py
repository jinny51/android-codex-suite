from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
PLUGIN = ROOT / "plugins/android-engineering-ops"
WORKFLOW = PLUGIN / "skills/android-change-workflow"
CAPTURE = PLUGIN / "skills/android-patch-capture"
BUILD_DEPLOY = PLUGIN / "skills/android-remote-build-deploy"


def component_contract() -> dict:
    return json.loads(
        (PLUGIN / "contracts/change-domain/v1/domain-profiles.json").read_text(
            encoding="utf-8"
        )
    )


def test_canonical_component_model_has_only_seven_layers() -> None:
    contract = component_contract()
    assert contract["canonical_selector"] == "component.layer"
    assert contract["component_model"]["layer"] == [
        "application", "platform", "native", "hal", "kernel", "device", "build"
    ]
    assert "vendor" not in contract["component_model"]["layer"]
    assert "domains" not in contract


def test_legacy_routes_map_only_to_canonical_layers() -> None:
    legacy = component_contract()["legacy_change_domain"]
    assert legacy["status"] == "compatibility_input_only"
    assert legacy["mapping"] == {
        "framework": "platform",
        "system_app": "application",
        "app": "application",
        "hal": "hal",
        "native": "native",
        "kernel": "kernel",
        "driver": "kernel",
        "device": "device",
        "build": "build",
    }
    assert set(legacy["ambiguous_inputs"]) == {"vendor"}
    assert legacy["ambiguous_inputs"]["vendor"]["requires_explicit"] == ["layer"]


def test_submission_boundary_is_single_v1_android_change_lifecycle() -> None:
    contract = component_contract()["submission"]
    assert contract["canonical_package_type"] == "knowledge-incoming-package/2/android_change"
    assert contract["final_package_owner"] == "android-patch-capture"
    assert contract["local_prepare_owner"] == "akbs-patch-submit"
    assert contract["upload_lifecycle"] == "common_patch_upload"
    workflow = (WORKFLOW / "SKILL.md").read_text(encoding="utf-8")
    capture = (CAPTURE / "SKILL.md").read_text(encoding="utf-8")
    capture_contract = (CAPTURE / "references/package-contract.md").read_text(encoding="utf-8")
    for text in (workflow, capture):
        assert "application" in text.lower()
        assert "android_change" in text.lower()
    for text in (capture, capture_contract):
        assert "layer_not_enabled" not in text
        assert "android_change" in text.lower()


def test_source_authority_and_build_routes_cover_remote_and_local_projects() -> None:
    workflow = (WORKFLOW / "SKILL.md").read_text(encoding="utf-8")
    routing = (WORKFLOW / "references/domain-routing.md").read_text(encoding="utf-8")
    build_deploy = (BUILD_DEPLOY / "SKILL.md").read_text(encoding="utf-8")
    for authority in ("registered_remote_tree", "local_project"):
        assert authority in workflow
        assert authority in routing
    for route in ("remote_profile", "remote_project_command", "local_project_command"):
        assert route in workflow
        assert route in routing
    assert "Never reclassify SMB/CIFS-mounted Android source" in workflow
    assert "not a generic Gradle" in build_deploy


def test_manifest_publishes_six_canonical_skills() -> None:
    manifest = (ROOT / "manifests/android-engineering-ops.toml").read_text(
        encoding="utf-8"
    )
    assert [line for line in manifest.splitlines() if line.startswith("name = ")] == [
        'name = "android-change-policy"',
        'name = "android-change-workflow"',
        'name = "android-source-access"',
        'name = "android-remote-channel"',
        'name = "android-remote-build-deploy"',
        'name = "android-patch-capture"',
    ]
