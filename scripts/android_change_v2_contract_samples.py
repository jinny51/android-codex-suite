"""Synthetic source -> final v2 package -> member prepare contract samples.

This is a test helper, never a member upload tool. It writes only below the caller's
isolated root, runs no Android build/adb/network operation, and does not construct or
rewrite capture/canonical manifests. Android behavior facts below are explicitly
synthetic inputs to test the producer/consumer boundary, not production evidence.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# Facets, repository, changed file, qualifiers and producer assertion IDs only.
# No server response or upload result is fabricated here.
CASES = {
    "application": ("settings:application:system_app:system_ext:product", "packages/apps/Settings",
        "src/ContractFeature.java", ["installable"], ["permission_signing_compatibility", "install_upgrade_behavior"]),
    "platform": ("platform:platform:framework_api:system:aosp", "frameworks/base",
        "core/java/android/os/ContractFeature.java", [], ["api_resource_compatibility"]),
    "native": ("native:native:native_library:system:aosp", "frameworks/native",
        "libs/contract/contract.cpp", ["published_abi_or_api"], ["abi_api_linker_compatibility"]),
    "hal": ("hal:hal:aidl_hal:vendor:silicon_vendor", "hardware/interfaces",
        "light/aidl/default/Contract.cpp", [], ["interface_version_compatibility", "vintf_selinux_service_registration", "hardware_behavior"]),
    "kernel": ("kernel:kernel:driver:vendor_boot:silicon_vendor", "kernel",
        "drivers/video/backlight/contract.c", ["power_managed"], ["kconfig_build_graph", "probe_bind_dmesg", "hardware_behavior", "power_suspend_resume"]),
    "device": ("device:device:device_tree:vendor_boot:product", "device/example/board",
        "board.dts", [], ["partition_overlay_policy", "device_behavior"]),
    "build": ("build:build:soong:build_host:aosp", "build/soong",
        "Android.bp", ["reproducibility_relevant"], ["dependency_graph", "clean_incremental_build", "artifact_contract", "reproducibility"]),
    "platform-resource-equivalent": ("resource:platform:framework_resource:system:aosp", "frameworks/base",
        "core/res/res/values/config.xml", [], ["api_resource_compatibility"]),
}


def _source_text(path: str, value: int, *, changed: bool) -> str:
    date = datetime.datetime.now().strftime("%Y%m%d")
    if path.endswith(".xml"):
        return f'<resources>\n    <integer name="config_contract_value">{value}</integer>\n</resources>\n'
    if path.endswith(".dts"):
        body = f"contract-value = <{value}>;"
        prefix, suffix = "/dts-v1/;\n/ {\n", "};\n"
    elif path.endswith(".bp"):
        body = f'name: "contract_fixture_{value}",'
        prefix, suffix = "filegroup {\n", "}\n"
    elif path.endswith(".java"):
        body = f"static final int VALUE = {value};"
        prefix, suffix = "class ContractFeature {\n", "}\n"
    else:
        body = f"return {value};"
        prefix, suffix = "int contract_value(void) {\n", "}\n"
    if changed:
        body = f"//wick {date}@{{\n    {body}\n    //wick {date}@}}"
    return prefix + "    " + body + "\n" + suffix


def generate_packages(
    root: Path, suite_root: Path, *, case_names: tuple[str, ...] | None = None,
) -> tuple[dict[str, Path], dict[str, str]]:
    """Return prepared canonical packages plus their isolated member environment."""
    root, suite_root = root.resolve(), suite_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    gate = _load(suite_root / "scripts/validate_incoming_contract_gate.py", "formal_sample_gate")
    gate.REPO_ROOT = suite_root
    env, runtimes = gate.write_config(root)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    engineering = runtimes["android-engineering-ops"]
    sys.path.insert(0, str(engineering / "lib"))
    from android_engineering_ops.remote_patch_snapshot import create_remote_patch_snapshot
    push = _load(engineering / "skills/android-remote-build-deploy/scripts/push_artifacts.py", "formal_sample_push")
    capture_cli = engineering / "skills/android-patch-capture/scripts/capture_android_patch.py"
    member_cli = runtimes["akbs-member-ops"] / "skills/akbs-patch-submit/scripts/akbs_patch_submit.py"
    packages: dict[str, Path] = {}
    selected = case_names or (*CASES, "platform-application")
    for name in selected:
        layer_names = ("platform", "application") if name == "platform-application" else (name,)
        specs = [CASES[layer] for layer in layer_names]
        case_root = root / "samples" / name
        remote_root = case_root / "TVE8402M"
        inputs = case_root / "inputs"
        inputs.mkdir(parents=True)
        for spec, repo, path, qualifiers, assertion_ids in specs:
            source = remote_root / repo / path
            source.parent.mkdir(parents=True)
            source.write_text(_source_text(path, 1, changed=False))
            repo_root = remote_root / repo
            for command in (
                ["git", "init", "-q", "-b", "main"],
                ["git", "config", "user.name", "Synthetic Contract Test"],
                ["git", "config", "user.email", "contract@example.invalid"],
                ["git", "add", path],
                ["git", "commit", "-qm", "synthetic source baseline"],
            ):
                subprocess.run(command, cwd=repo_root, env=env, check=True,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            source.write_text(_source_text(path, 2, changed=True))
        workspace_id = hashlib.sha256(str(remote_root).encode()).hexdigest()[:16]
        command_id = "contract-snapshot-" + name
        snapshot = create_remote_patch_snapshot(remote_root=remote_root,
            workspace_id=workspace_id, command_id=command_id,
            repository_paths=[spec[1] for spec in specs])
        snapshot_path = inputs / "snapshot.json"
        snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False) + "\n")
        receipt = push.delivery_evidence(
            argparse.Namespace(adb_serial="synthetic-device", reboot=True, wait_boot=True,
                dry_run=False, remote_build_host="synthetic-builder", remote_source_root=str(remote_root),
                remote_build_command="synthetic build fixture", remote_build_profile="contract",
                artifact_transfer="synthetic verified bridge"),
            [(inputs / "contract.bin", "/system/etc/contract.bin")],
            [push.RemoteArtifactManifest(schema="android-remote-artifact-manifest-v1", version=1,
                remote_path=str(remote_root / "out/contract.bin"), module="contract", profile="contract",
                workspace_id=workspace_id, command_id="synthetic-build", build_started_ns=1,
                build_finished_ns=3, size=42, mtime_ns=2, sha256="a" * 64)],
        )
        receipt["summary"] = "SYNTHETIC delivery contract fixture; no adb or Android build was executed"
        receipt_path = inputs / "delivery.json"
        receipt_path.write_text(json.dumps(receipt) + "\n")
        assertions = [
            {"component_id": spec.split(":")[0], "assertion_id": assertion_id,
             "result": "PASS", "observations": [f"SYNTHETIC {assertion_id} contract fixture"]}
            for spec, repo, path, qualifiers, assertion_ids in specs for assertion_id in assertion_ids
        ]
        assertion_dir = inputs / "assertions"
        assertion_dir.mkdir()
        (assertion_dir / "component-assertion.json").write_text(json.dumps({
            "kind": "component_assertion", "result": "INFO",
            "component_ids": list(dict.fromkeys(row["component_id"] for row in assertions)),
            "assertions": assertions,
        }) + "\n")
        command = [sys.executable, str(capture_cli), "--profile", "wick",
            "--remote-snapshot", str(snapshot_path), "--snapshot-workspace-id", workspace_id,
            "--snapshot-command-id", command_id, "--snapshot-sha256", snapshot["snapshot_sha256"],
            "--remote-source-root", str(remote_root), "--platform", "rk14", "--project", "TVE8402M",
            "--change-id", "contract-" + name, "--summary", "Synthetic contract value adjustment",
            "--run-id", datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + "-contract-" + name,
            "--problem-summary", "Synthetic contract fixture requires a value adjustment",
            "--solution-summary", "Adjust the synthetic source value and preserve scoped evidence",
            "--workflow-contract", "current_codex_skill", "--implementation-origin", "codex",
            "--primary-component-id", specs[0][0].split(":")[0],
            "--verification", "SYNTHETIC compile result PASS",
            "--health-check", "SYNTHETIC baseline regression PASS",
            "--risk", "Synthetic isolated contract fixture only", "--rollback", "Restore the synthetic baseline",
            "--search-query", "synthetic contract value fixture", "--reuse-decision", "not_found",
            "--evidence-dir", str(assertion_dir), "--build-result", str(receipt_path)]
        if name == "platform-resource-equivalent":
            command += ["--verification-method", "equivalent", "--equivalent-type", "artifact_static_check",
                "--equivalent-reason", "Resource-only contract sample without runtime behavior changes",
                "--equivalent-coverage", "SYNTHETIC compiled resource value check",
                "--remaining-risk", "Synthetic fixture does not claim production device validation"]
        else:
            command += ["--device", "synthetic-device", "--device-verification", "SYNTHETIC scoped behavior PASS"]
        for spec, repo, path, qualifiers, assertion_ids in specs:
            component_id = spec.split(":")[0]
            command += ["--component", spec, "--repo-component", f"{repo}={component_id}"]
            for qualifier in qualifiers:
                command += ["--component-qualifier", f"{component_id}:{qualifier}"]
            for evidence_id in ("verification-result", "rollback-plan", "search-before-change", "build-delivery"):
                command += ["--evidence-component", f"{evidence_id}:{component_id}"]
        captured = gate.run_json(command, case_root, env)
        if captured["package_status"] != "validated":
            raise AssertionError(captured)
        prepared = gate.run_json([sys.executable, str(member_cli), "android-change-v2", "prepare",
            captured["package"]], case_root, env)
        packages[name] = Path(prepared["package"])
        if json.loads(receipt_path.read_text()) != receipt:
            raise AssertionError("capture changed the source delivery receipt")
    return packages, env
