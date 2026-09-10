from __future__ import annotations

import importlib.util
import json
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SAMPLES = ROOT / "scripts" / "android_change_v2_contract_samples.py"
LAYERS = {"application", "platform", "native", "hal", "kernel", "device", "build"}


def _load_samples():
    spec = importlib.util.spec_from_file_location("direct_v2_samples", SAMPLES)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_direct_capture_and_member_check_cover_all_seven_layers() -> None:
    samples = _load_samples()
    with tempfile.TemporaryDirectory() as temporary:
        packages, _environment = samples.generate_packages(
            Path(temporary), ROOT, case_names=tuple(sorted(LAYERS))
        )
        assert set(packages) == LAYERS
        observed = set()
        for package in packages.values():
            manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
            assert manifest["schema"] == "akbs-android-change-package-v2"
            assert manifest["package_status"] == "validated"
            assert manifest["subject"]["target"]["platform"] == "rk"
            assert "platform_token" not in json.dumps(manifest)
            assert not ({"qualification", "files", "changes"} & set(manifest))
            observed.update(component["layer"] for component in manifest["components"])
        assert observed == LAYERS


def test_direct_capture_preserves_cross_layer_component_bindings() -> None:
    samples = _load_samples()
    with tempfile.TemporaryDirectory() as temporary:
        packages, _environment = samples.generate_packages(
            Path(temporary), ROOT, case_names=("platform-application",)
        )
        manifest = json.loads(
            (packages["platform-application"] / "manifest.json").read_text(encoding="utf-8")
        )
        assert {item["layer"] for item in manifest["components"]} == {
            "platform", "application"
        }
        assert {item["source_id"] for item in manifest["patches"]} == {
            item["id"] for item in manifest["sources"]
        }
