from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[4]
PLUGIN = ROOT / "plugins/android-engineering-ops"
CAPTURE_PATH = PLUGIN / "skills/android-patch-capture/scripts/capture_android_patch.py"
LEGACY_READER_PATH = PLUGIN / "skills/android-patch-capture/scripts/read_legacy_capture.py"
PUSH_PATH = PLUGIN / "skills/android-remote-build-deploy/scripts/push_artifacts.py"
if str(PLUGIN / "lib") not in sys.path:
    sys.path.insert(0, str(PLUGIN / "lib"))

from android_engineering_ops.knowledge_rules import parse_platform_input  # noqa: E402


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def producer_delivery_receipt() -> dict:
    push = load(PUSH_PATH, "capture_build_delivery_producer")
    manifest = push.RemoteArtifactManifest(
        schema="android-remote-artifact-manifest-v1",
        version=1,
        remote_path="/build/android/out/system/lib64/libtest.so",
        module="libtest",
        profile="native",
        workspace_id="0123456789abcdef",
        command_id="build-001",
        build_started_ns=1,
        build_finished_ns=3,
        size=42,
        mtime_ns=2,
        sha256="a" * 64,
    )
    return push.delivery_evidence(
        argparse.Namespace(
            adb_serial="device-01",
            reboot=True,
            wait_boot=True,
            dry_run=False,
            remote_build_host="builder",
            remote_source_root="/build/android",
            remote_build_command="m libtest",
            remote_build_profile="native",
            artifact_transfer="verified artifact bridge",
        ),
        [(Path("/artifacts/libtest.so"), "/system/lib64/libtest.so")],
        [manifest],
    )


def test_delivery_receipt_remains_auxiliary_evidence(tmp_path: Path) -> None:
    capture = load(CAPTURE_PATH, "capture_build_delivery_consumer")
    receipt = producer_delivery_receipt()
    source = tmp_path / "delivery.json"
    source.write_text(json.dumps(receipt), encoding="utf-8")
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    rows = capture.collect_external_evidence(
        argparse.Namespace(
            evidence_dir=[],
            build_result=[str(source)],
            components=[{"id": "native"}],
            evidence_component=[],
        ),
        evidence_dir,
    )
    assert len(rows) == 1
    assert rows[0]["kind"] == "deploy_result"
    assert rows[0]["scope"] == "feature"
    assert "contract" not in rows[0]
    assert "declared_claims" not in rows[0]
    payload = json.loads((tmp_path / rows[0]["path"]).read_text(encoding="utf-8"))
    assert payload["delivery_receipt"] == receipt
    assert payload["requirement_acceptance"] == "unverified"


def test_component_model_has_exactly_seven_layers_and_orthogonal_facets() -> None:
    capture = load(CAPTURE_PATH, "capture_component_model")
    assert set(capture.COMPONENT_LAYERS) == {
        "application", "platform", "native", "hal", "kernel", "device", "build"
    }
    explicit = capture.resolve_component(
        argparse.Namespace(
            change_domain="",
            component_layer="hal",
            component_type="aidl_hal",
            component_partition="vendor",
            component_ownership="silicon_vendor",
        )
    )
    assert explicit == {
        "layer": "hal",
        "type": "aidl_hal",
        "partition": "vendor",
        "ownership": "silicon_vendor",
    }
    with pytest.raises(SystemExit, match="vendor is an ownership/partition facet"):
        capture.resolve_component(
            argparse.Namespace(
                change_domain="vendor",
                component_layer="",
                component_type="",
                component_partition="",
                component_ownership="",
            )
        )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("mtk16", ("mtk16", "mtk", "16")),
        ("rk14", ("rk14", "rk", "14")),
        ("unisoc13", ("unisoc13", "unisoc", "13")),
    ],
)
def test_platform_input_is_transiently_split(value: str, expected: tuple[str, str, str]) -> None:
    assert parse_platform_input(value) == expected


@pytest.mark.parametrize("value", ["mtk", "android16", "qcom16", "rk-14", "sprd13", "u13"])
def test_platform_input_rejects_non_versioned_or_unsupported_values(value: str) -> None:
    assert parse_platform_input(value) == ("", "", "")


def test_new_v2_runtime_has_no_capture_materializer_or_status_machine() -> None:
    script = CAPTURE_PATH.read_text(encoding="utf-8")
    assert "android-patch-capture-package-v2" not in script
    assert "effective_status" not in script
    assert "declared_status" not in script
    assert "platform_token" not in script
    assert not (PLUGIN / "contracts/android-patch-capture/v2/capture-package-v2.1.schema.json").exists()


def test_legacy_framework_capture_reader_remains_read_only() -> None:
    reader = LEGACY_READER_PATH.read_text(encoding="utf-8")
    assert "read" in reader.lower()
    assert "write_json" not in reader
    assert "publish_tree_atomic" not in reader
