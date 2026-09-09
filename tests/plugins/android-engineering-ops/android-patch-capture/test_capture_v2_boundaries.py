from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[4]
CAPTURE_PATH = (
    ROOT
    / "plugins/android-engineering-ops/skills/android-patch-capture/scripts/capture_android_patch.py"
)
LEGACY_READER_PATH = (
    ROOT
    / "plugins/android-engineering-ops/skills/android-patch-capture/scripts/read_legacy_capture.py"
)
PUSH_PATH = (
    ROOT
    / "plugins/android-engineering-ops/skills/android-remote-build-deploy/scripts/push_artifacts.py"
)


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def producer_delivery_receipt(*, dry_run: bool = False) -> dict:
    push = load(PUSH_PATH, "capture_build_delivery_producer")
    manifest = push.RemoteArtifactManifest(
        schema="android-remote-artifact-manifest-v1", version=1,
        remote_path="/build/android/out/system/lib64/libtest.so", module="libtest",
        profile="native", workspace_id="0123456789abcdef", command_id="build-001",
        build_started_ns=1, build_finished_ns=3, size=42, mtime_ns=2, sha256="a" * 64,
    )
    return push.delivery_evidence(
        argparse.Namespace(
            adb_serial="device-01", reboot=True, wait_boot=True, dry_run=dry_run,
            remote_build_host="builder", remote_source_root="/build/android",
            remote_build_command="m libtest", remote_build_profile="native",
            artifact_transfer="verified artifact bridge",
        ),
        [(Path("/artifacts/libtest.so"), "/system/lib64/libtest.so")],
        [manifest],
    )


@pytest.mark.parametrize("dry_run", [False, True])
def test_formal_delivery_receipt_is_auxiliary_and_never_feature_acceptance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dry_run: bool,
) -> None:
    capture = load(CAPTURE_PATH, "capture_build_delivery_consumer")
    receipt = producer_delivery_receipt(dry_run=dry_run)
    source = tmp_path / "latest-build-delivery.json"
    original = (json.dumps(receipt, ensure_ascii=False, indent=2) + "\n").encode()
    source.write_bytes(original)
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    generated_feature = b'{"result":"INFO","scope":"feature"}\n'
    (evidence_dir / "verification-result.json").write_bytes(generated_feature)
    rows = capture.collect_external_evidence(
        argparse.Namespace(
            evidence_dir=[], build_result=[str(source)], components=[{"id": "native"}],
            evidence_component=[],
        ),
        evidence_dir,
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == "build-delivery"
    assert row["kind"] == "deploy_result"
    payload = json.loads((tmp_path / row["path"]).read_text())
    assert payload["delivery_receipt"] == receipt
    assert payload["scope"] == "build_delivery"
    assert payload["requirement_acceptance"] == "unverified"
    assert payload["component_ids"] == ["native"]
    assert payload["result"] == ("INFO" if dry_run else "PASS")
    assert not capture.has_authoritative_requirement_result(payload, expected_result="PASS")
    assert source.read_bytes() == original
    assert (evidence_dir / "verification-result.json").read_bytes() == generated_feature

    monkeypatch.setattr(sys, "argv", [str(CAPTURE_PATH), "--platform", "rk14",
        "--change-id", "test-delivery", "--summary", "test delivery",
        "--workflow-contract", "manual_import", "--status", "validated"])
    args = capture.parse_args()
    feature = capture.verification_result(args)
    assert capture.validate_verification_for_status(args, feature)


def test_multiple_delivery_receipts_have_distinct_ids_and_explicit_component_scope(
    tmp_path: Path,
) -> None:
    capture = load(CAPTURE_PATH, "capture_multiple_delivery_receipts")
    receipt = producer_delivery_receipt()
    first, second = tmp_path / "first.json", tmp_path / "second.json"
    first.write_text(json.dumps(receipt))
    second.write_text(json.dumps({**receipt, "component_ids": ["settings"]}))
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    args = argparse.Namespace(
        evidence_dir=[], build_result=[str(first), str(second)],
        components=[{"id": "native"}, {"id": "settings"}],
        evidence_component=["build-delivery:native"],
    )
    rows = capture.collect_external_evidence(args, evidence_dir)
    assert len({row["id"] for row in rows}) == 2
    assert all(row["id"] != "verification-result" for row in rows)
    assert [row["component_ids"] for row in rows] == [["native"], ["settings"]]
    assert json.loads((tmp_path / rows[0]["path"]).read_text())["delivery_receipt"] == receipt


@pytest.mark.parametrize("mutation", [
    {"scope": "feature", "requirement_acceptance": "accepted"},
    {"requirement_acceptance": "accepted"},
    {"contract_version": "unknown"},
    {"steps": []},
    {"local_delivery": {}},
])
def test_delivery_import_rejects_fake_feature_or_incomplete_receipts(
    tmp_path: Path, mutation: dict,
) -> None:
    capture = load(CAPTURE_PATH, "capture_invalid_delivery_receipt")
    source = tmp_path / "receipt.json"
    source.write_text(json.dumps({**producer_delivery_receipt(), **mutation}))
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    args = argparse.Namespace(evidence_dir=[], build_result=[str(source)],
        components=[{"id": "native"}], evidence_component=[])
    with pytest.raises(SystemExit, match="build_delivery/unverified"):
        capture.collect_external_evidence(args, evidence_dir)
    assert list(evidence_dir.iterdir()) == []
    with pytest.raises(SystemExit, match="build_delivery/unverified"):
        capture.wrap_build_delivery_receipt({"kind": "verification_result", "result": "PASS"}, source)


def test_formal_capture_materialize_prepare_samples_preserve_producer_boundaries(tmp_path: Path) -> None:
    samples = load(ROOT / "scripts/android_change_v2_contract_samples.py", "formal_contract_samples_test")
    packages, env = samples.generate_packages(tmp_path / "formal", ROOT)
    assert set(packages) == {*samples.CASES, "platform-application"}
    assert str(tmp_path) in env["CODEX_HOME"]
    for name, package in packages.items():
        manifest = json.loads((package / "manifest.json").read_text())
        assert manifest["identity"]["member_alias"] == "wick"
        if name == "platform-application":
            assert {row["layer"] for row in manifest["components"]} == {"platform", "application"}
        snapshot = json.loads((package / "evidence/remote-source-snapshot.json").read_text())
        assert snapshot["schema"] == "android-remote-patch-snapshot-v1"
        assert "component_ids" not in snapshot
        delivery = json.loads((package / "evidence/build-delivery.json").read_text())
        assert delivery["kind"] == "deploy_result"
        assert delivery["delivery_receipt"]["kind"] == "verification_result"
        assert delivery["requirement_acceptance"] == "unverified"
        if name == "platform-resource-equivalent":
            feature = json.loads((package / "evidence/verification-result.json").read_text())
            assert feature["method"] == "equivalent"
            assert feature["steps"] == []
            assert feature["coverage"]


def test_component_model_supports_all_layers_and_orthogonal_overrides() -> None:
    capture = load(CAPTURE_PATH, "capture_v2_component_test")
    assert set(capture.COMPONENT_LAYERS) == {
        "application", "platform", "native", "hal", "kernel", "device", "build"
    }
    framework = capture.resolve_component(
        argparse.Namespace(
            change_domain="framework",
            component_layer="",
            component_type="",
            component_partition="",
            component_ownership="",
        )
    )
    assert framework == {
        "layer": "platform",
        "type": "framework",
        "partition": "unknown",
        "ownership": "unknown",
    }
    explicit = capture.resolve_component(
        argparse.Namespace(
            change_domain="",
            component_layer="hal",
            component_type="hidl_hal",
            component_partition="odm",
            component_ownership="soc_vendor",
        )
    )
    assert explicit == {
        "layer": "hal",
        "type": "hidl_hal",
        "partition": "odm",
        "ownership": "soc_vendor",
    }
    with pytest.raises(SystemExit, match="vendor is an ownership/partition facet"):
        capture.resolve_component(
            argparse.Namespace(
                change_domain="vendor",
                component_layer="",
                component_type="",
                component_partition="vendor",
                component_ownership="vendor",
            )
        )


def test_change_id_is_canonical_and_legacy_feature_conflict_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture = load(CAPTURE_PATH, "capture_v2_change_id_test")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(CAPTURE_PATH),
            "--platform", "rk14",
            "--change-id", "fix-policy",
            "--summary", "Fix policy behavior",
            "--workflow-contract", "manual_import",
        ],
    )
    canonical = capture.parse_args()
    assert canonical.change_id == "fix-policy"
    assert canonical.legacy_feature is None

    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(CAPTURE_PATH),
            "--platform", "rk14",
            "--change-id", "fix-policy",
            "--feature", "different-feature",
            "--summary", "Fix policy behavior",
            "--workflow-contract", "manual_import",
        ],
    )
    with pytest.raises(SystemExit):
        capture.parse_args()


def test_multi_component_capture_requires_primary_and_exact_repo_mappings() -> None:
    capture = load(CAPTURE_PATH, "capture_v2_multi_component_test")
    args = argparse.Namespace(
        component_specs=[
            "platform-core:platform:framework:system:aosp",
            "settings-ui:application:system_app:system_ext:product",
        ],
        primary_component_id="platform-core",
        change_domain="",
        component_layer="",
        component_type="",
        component_partition="",
        component_ownership="",
        repo_component=[
            "frameworks/base=platform-core",
            "packages/apps/Settings=settings-ui,platform-core",
        ],
    )
    components, primary = capture.resolve_components(args)
    assert primary == "platform-core"
    captures = [
        capture.RepositoryCapture(
            source_root="/src/frameworks/base",
            repo_path="frameworks/base",
            git_info={},
            diff_text="diff --git a/a b/a\n",
            facts={"content_sha1": "1"},
            module="frameworks-base",
            patch_name="rk14-frameworks-base@feature.patch",
            patch_rel="patches/rk14-frameworks-base@feature.patch",
        ),
        capture.RepositoryCapture(
            source_root="/src/packages/apps/Settings",
            repo_path="packages/apps/Settings",
            git_info={},
            diff_text="diff --git a/b b/b\n",
            facts={"content_sha1": "2"},
            module="settings",
            patch_name="rk14-settings@feature.patch",
            patch_rel="patches/rk14-settings@feature.patch",
        ),
    ]
    capture.bind_repository_components(args, captures, components)
    assert [(item.repository_id, item.component_ids) for item in captures] == [
        ("repo-001", ("platform-core",)),
        ("repo-002", ("settings-ui", "platform-core")),
    ]
    missing = argparse.Namespace(**{**vars(args), "repo_component": args.repo_component[:1]})
    with pytest.raises(SystemExit, match="every captured repository"):
        capture.bind_repository_components(missing, captures, components)
    unused = argparse.Namespace(
        **{
            **vars(args),
            "repo_component": [
                "frameworks/base=platform-core",
                "packages/apps/Settings=platform-core",
            ],
        }
    )
    with pytest.raises(SystemExit, match="every declared component"):
        capture.bind_repository_components(unused, captures, components)


def test_multi_component_generated_evidence_requires_exact_explicit_scope() -> None:
    capture = load(CAPTURE_PATH, "capture_v2_evidence_scope_test")
    components = [{"id": "platform-core"}, {"id": "settings-ui"}]
    items = [
        {"id": "changed-files"},
        {"id": "verification-result"},
        {"id": "rollback-plan"},
        {"id": "search-before-change"},
        {"id": "build-result", "component_ids": ["settings-ui"]},
    ]
    with pytest.raises(SystemExit, match="explicit --evidence-component"):
        capture.bind_generated_evidence_components(items, components, [])

    scoped = copy.deepcopy(items)
    capture.bind_generated_evidence_components(
        scoped,
        components,
        [
            "verification-result:platform-core",
            "rollback-plan:settings-ui",
            "search-before-change:settings-ui",
        ],
    )
    by_id = {item["id"]: item["component_ids"] for item in scoped}
    assert by_id["changed-files"] == ["platform-core", "settings-ui"]
    assert by_id["verification-result"] == ["platform-core"]
    assert by_id["rollback-plan"] == ["settings-ui"]
    assert by_id["search-before-change"] == ["settings-ui"]
    assert by_id["build-result"] == ["settings-ui"]


def test_component_assertion_contract_is_producer_owned_and_closed(tmp_path: Path) -> None:
    capture = load(CAPTURE_PATH, "capture_v2_component_assertion_test")
    source = tmp_path / "component-assertion.json"
    valid = {
        "kind": "component_assertion",
        "result": "INFO",
        "component_ids": ["platform-core", "settings-ui"],
        "assertions": [
            {
                "component_id": "platform-core",
                "assertion_id": "api_resource_compatibility",
                "result": "INFO",
                "observations": ["API surface inspected"],
            },
            {
                "component_id": "settings-ui",
                "assertion_id": "permission_signing_compatibility",
                "result": "FAIL",
                "observations": ["signature differs"],
            },
        ],
    }
    capture.validate_component_assertion_payload(
        valid,
        valid["component_ids"],
        source,
    )

    consumer_group = copy.deepcopy(valid)
    consumer_group["assertions"][0]["group_id"] = "api_or_resource_compatibility"
    with pytest.raises(SystemExit, match="observations"):
        capture.validate_component_assertion_payload(
            consumer_group,
            consumer_group["component_ids"],
            source,
        )
    naked_pass = copy.deepcopy(valid)
    naked_pass["assertions"][0].update(result="PASS")
    naked_pass["assertions"][0].pop("observations")
    with pytest.raises(SystemExit, match="observations"):
        capture.validate_component_assertion_payload(
            naked_pass,
            naked_pass["component_ids"],
            source,
        )
    outer_pass = copy.deepcopy(valid)
    outer_pass["result"] = "PASS"
    with pytest.raises(SystemExit, match="result=INFO"):
        capture.validate_component_assertion_payload(
            outer_pass,
            outer_pass["component_ids"],
            source,
        )
    incomplete_union = copy.deepcopy(valid)
    incomplete_union["assertions"] = incomplete_union["assertions"][:1]
    with pytest.raises(SystemExit, match="精确覆盖"):
        capture.validate_component_assertion_payload(
            incomplete_union,
            incomplete_union["component_ids"],
            source,
        )


@pytest.mark.parametrize(
    ("legacy", "layer", "component_type"),
    [
        ("system_app", "application", "system_app"),
        ("app", "application", "app"),
        ("hal", "hal", "hal"),
        ("native", "native", "native"),
        ("kernel", "kernel", "kernel"),
        ("driver", "kernel", "driver"),
        ("device", "device", "device"),
        ("build", "build", "build"),
    ],
)
def test_legacy_routes_never_invent_partition_or_ownership(
    legacy: str, layer: str, component_type: str,
) -> None:
    capture = load(CAPTURE_PATH, f"capture_legacy_hint_{legacy}")
    result = capture.resolve_component(
        argparse.Namespace(
            change_domain=legacy,
            component_layer="",
            component_type="",
            component_partition="",
            component_ownership="",
        )
    )
    assert result == {
        "layer": layer,
        "type": component_type,
        "partition": "unknown",
        "ownership": "unknown",
    }
    assert result["partition"] not in {"system", "system_ext", "data", "vendor", "boot"}
    assert result["ownership"] not in {"aosp", "product", "vendor"}


@pytest.mark.parametrize("status", ["draft", "candidate", "failed", "blocked"])
def test_capture_never_promotes_declared_status(status: str) -> None:
    capture = load(CAPTURE_PATH, f"capture_v2_status_{status}")
    assert capture.effective_capture_status(status, []) == status
    assert capture.effective_capture_status(status, ["qualification failed"]) == status
    assert capture.effective_capture_status("validated", ["qualification failed"]) == "candidate"


def test_new_writes_are_confined_to_canonical_root(tmp_path: Path, monkeypatch) -> None:
    capture = load(CAPTURE_PATH, "capture_v2_root_test")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    root = tmp_path / "codex/artifacts/android-patch-capture/packages"
    assert capture.require_canonical_package_root(root / "nested") == root / "nested"
    with pytest.raises(SystemExit, match="android-patch-capture/packages"):
        capture.require_canonical_package_root(tmp_path / "elsewhere")
    assert capture.validate_run_id("20260903-android_feature.patch") == (
        "20260903-android_feature.patch"
    )
    for unsafe in ("../escaped", "nested/package", "/absolute", ".", "", "x" * 129):
        with pytest.raises(SystemExit, match="safe 1..128 character token"):
            capture.validate_run_id(unsafe)


def test_legacy_import_is_read_only_and_never_pretends_v2_write(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    package = (
        tmp_path
        / "codex/artifacts/android-framework-patch-capture/packages/legacy-1"
    )
    package.mkdir(parents=True)
    (package / "manifest.json").write_text(
        json.dumps({"schema_version": "1.0", "status": "candidate"}) + "\n",
        encoding="utf-8",
    )
    (package / "README.md").write_text("legacy facts\n", encoding="utf-8")
    before = {
        path.relative_to(package).as_posix(): (path.read_bytes(), path.stat().st_mode)
        for path in package.rglob("*")
        if path.is_file()
    }
    reader = load(LEGACY_READER_PATH, "capture_legacy_reader_test")
    result = reader.inspect_legacy_package(package)
    after = {
        path.relative_to(package).as_posix(): (path.read_bytes(), path.stat().st_mode)
        for path in package.rglob("*")
        if path.is_file()
    }
    assert before == after
    assert result["normalized_component"]["layer"] == "platform"
    assert result["normalized_component"]["type"] == "framework"
    assert result["normalized_component"]["partition"] is None
    assert result["normalized_component"]["ownership"] is None
    assert result["read_only"] is True
    assert result["history_rewritten"] is False
    assert result["copied_to_new_root"] is False
    assert result["server_v2_writer"] == "disabled"
    assert not (tmp_path / "codex/artifacts/android-patch-capture").exists()
