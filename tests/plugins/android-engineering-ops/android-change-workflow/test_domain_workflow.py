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


def test_canonical_component_model_has_only_seven_layers_and_orthogonal_facets() -> None:
    contract = component_contract()
    assert contract["canonical_selector"] == "component.layer"
    assert contract["component_model"]["layer"] == [
        "application", "platform", "native", "hal", "kernel", "device", "build"
    ]
    assert contract["component_model"]["orthogonal_facets"] == [
        "type", "partition", "ownership"
    ]
    assert "vendor" not in contract["component_model"]["layer"]
    assert "domains" not in contract


def test_legacy_routes_only_hint_layer_and_type_without_fabricating_facets() -> None:
    legacy = component_contract()["legacy_change_domain"]
    assert legacy["status"] == "compatibility_input_only"
    for name, hint in legacy["partial_hints"].items():
        assert set(hint) == {"layer", "type"}, name
        assert "partition" not in hint
        assert "ownership" not in hint
    assert set(legacy["ambiguous_inputs"]) == {"vendor"}
    assert legacy["ambiguous_inputs"]["vendor"]["requires_explicit"] == [
        "layer", "type", "partition", "ownership"
    ]


def test_submission_boundary_is_general_v2_prepare_with_legacy_v1_compatibility() -> None:
    contract = component_contract()["submission"]
    assert contract["canonical_package_type"] == "android_change_capture"
    assert contract["v2_local_prepare_owner"] == "akbs-patch-submit"
    assert contract["writer_off_behavior"] == "capability_gated_zero_network_side_effects"
    assert contract["fallback_to_framework_v1"] is False
    workflow = (WORKFLOW / "SKILL.md").read_text(encoding="utf-8")
    capture = (CAPTURE / "SKILL.md").read_text(encoding="utf-8")
    capture_contract = (
        PLUGIN / "contracts/android-patch-capture/v2/README.md"
    ).read_text(encoding="utf-8")
    for text in (workflow, capture):
        assert "any supported layer" in text.lower()
        assert "byte-preserving prepare" in text
        assert "zero side effects" in text
        assert "never" in text.lower() and "fall back" in text.lower()
    for text in (capture, capture_contract):
        assert "layer_not_enabled" not in text
        assert "any supported layer" in text.lower()


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
