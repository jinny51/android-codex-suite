from __future__ import annotations

import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
PLUGIN = ROOT / "plugins" / "akbs-member-ops"
LIB = PLUGIN / "lib"
SCRIPT = PLUGIN / "skills" / "akbs-patch-submit" / "scripts" / "akbs_patch_submit.py"
sys.path.insert(0, str(LIB))
sys.path.insert(0, str(PLUGIN / "internal" / "incoming-v1" / "scripts"))

from akbs_member_ops.incoming_v2.capture_adapter import preflight_capture  # noqa: E402
from akbs_member_ops.incoming_v2 import capture_adapter  # noqa: E402
from akbs_member_ops.incoming_v2 import validation  # noqa: E402
from akbs_member_ops.incoming_v2 import materializer  # noqa: E402
from akbs_member_ops.incoming_v2.schema import (  # noqa: E402
    SchemaError,
    validate_document,
)
from akbs_member_ops.incoming_v2.validation import AndroidChangeV2Error  # noqa: E402
from akbs_member_ops.incoming_v2.materializer import materialize_capture  # noqa: E402


def write_json(path: Path, value: object) -> bytes:
    raw = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return raw


def refresh_inventory(package: Path, manifest: dict[str, object]) -> None:
    files = []
    for path in sorted(package.rglob("*")):
        if not path.is_file() or path.is_symlink() or path.name == "manifest.json":
            continue
        raw = path.read_bytes()
        files.append(
            {
                "path": path.relative_to(package).as_posix(),
                "size_bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        )
    manifest["file_inventory"] = {
        "algorithm": "sha256",
        "scope": "all_regular_package_files_except_manifest.json",
        "manifest_self_hash_excluded": True,
        "files": files,
    }
    write_json(package / "manifest.json", manifest)


def build_capture(package: Path) -> Path:
    package.mkdir(parents=True)
    (package / "README.md").write_text("# Feature\n", encoding="utf-8")
    patches = {
        "platform-patch": b"diff --git a/core.java b/core.java\n",
        "settings-patch": b"diff --git a/Settings.java b/Settings.java\n",
    }
    for patch_id, raw in patches.items():
        path = package / "patches" / f"{patch_id}.patch"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    package_check = {
        "status": "PASS",
        "errors": [],
        "warnings": [],
        "declared_package_status": "validated",
        "effective_package_status": "validated",
        "status_was_upgraded": False,
    }
    write_json(package / "evidence" / "package-check.json", package_check)
    write_json(package / "evidence" / "coding-standard-check.json", {"result": "PASS"})
    components = [
        {
            "id": "platform-core",
            "layer": "platform",
            "type": "framework",
            "partition": "system",
            "ownership": "aosp",
        },
        {
            "id": "settings-ui",
            "layer": "application",
            "type": "system_app",
            "partition": "system_ext",
            "ownership": "product",
        },
    ]
    repositories = [
        {
            "id": "repo-001",
            "repo_path": "frameworks/base",
            "root": "/source/frameworks/base",
            "component_ids": ["platform-core"],
            "git": {"head": "a" * 40, "branch": "feature"},
        },
        {
            "id": "repo-002",
            "repo_path": "packages/apps/Settings",
            "root": "/source/packages/apps/Settings",
            "component_ids": ["settings-ui"],
            "git": {"head": "b" * 40, "branch": "feature"},
        },
    ]
    patch_rows = []
    for patch_id, repository, component_id in (
        ("platform-patch", repositories[0], "platform-core"),
        ("settings-patch", repositories[1], "settings-ui"),
    ):
        raw = patches[patch_id]
        patch_rows.append(
            {
                "id": patch_id,
                "path": f"patches/{patch_id}.patch",
                "repository_id": repository["id"],
                "repo_path": repository["repo_path"],
                "component_ids": [component_id],
                "source_root": repository["root"],
                "content_sha1": hashlib.sha1(raw).hexdigest(),
                "status": "validated",
                "reuse_hint": True,
                "implementation_origin": "codex",
                "workflow_contract": "current_codex_skill",
                "captured_by": "codex",
                "project": "TVE8402M",
                "platform_token": "rk14",
                "platform": "rockchip",
                "android_version": "14",
                "facts": {"content_sha1": hashlib.sha1(raw).hexdigest()},
            }
        )
    evidence = [
        {
            "id": "package-check",
            "kind": "package_check",
            "path": "evidence/package-check.json",
            "result": "PASS",
            "scope": "feature",
            "summary": "local package checks",
            "component_ids": ["platform-core", "settings-ui"],
            "contract": "android-patch-capture-evidence-v1",
            "declared_claims": ["local_package_check_recorded"],
        },
        {
            "id": "coding-standard-check",
            "kind": "coding_standard_check",
            "path": "evidence/coding-standard-check.json",
            "result": "PASS",
            "scope": "feature",
            "summary": "local coding check",
            "component_ids": ["platform-core", "settings-ui"],
            "contract": "android-patch-capture-evidence-v1",
            "declared_claims": ["local_policy_check_recorded"],
        },
    ]
    manifest: dict[str, object] = {
        "schema": "android-patch-capture-package-v2",
        "schema_version": "2.0",
        "package_type": "android_change_capture",
        "components": components,
        "primary_component_id": "platform-core",
        "change_id": "cross-component-feature",
        "readme": "README.md",
        "project": "TVE8402M",
        "platform_token": "rk14",
        "platform": "rockchip",
        "android_version": "14",
        "summary": "cross component change",
        "status": "validated",
        "declared_status": "validated",
        "effective_status": "validated",
        "status_was_upgraded": False,
        "implementation_origin": "codex",
        "workflow_contract": "current_codex_skill",
        "captured_by": "codex",
        "authority": {
            "owner": "android-patch-capture",
            "local_capture_only": True,
            "can_confirm_or_downgrade_status_only": True,
            "can_upload": False,
            "can_allocate_server_package_id": False,
            "can_materialize_knowledge": False,
        },
        "server_submission": {
            "v2_writer": "disabled",
            "v2_submission_allowed": False,
            "server_qualified": False,
            "note": "writer off",
        },
        "coding_standard_check": {
            "required": True,
            "mode": "capture_gate",
            "path": "evidence/coding-standard-check.json",
            "result": "PASS",
        },
        "created_at": "2026-09-03T01:00:00",
        "related_report_run_ids": [],
        "source_roots": [item["root"] for item in repositories],
        "git_repositories": repositories,
        "project_inference": {"project": "TVE8402M", "basis": ["test fixture"]},
        "verification_chain": {
            "remote_build": True,
            "local_delivery": True,
            "device_verification": True,
        },
        "patches": patch_rows,
        "evidence": evidence,
        "qualification_bindings": [
            {
                "component_id": component_id,
                "repository_ids": [repository_id],
                "patch_ids": [patch_id],
                "evidence_ids": ["package-check", "coding-standard-check"],
                "contract": "android-patch-capture-local-qualification-v1",
                "declared_claims": [
                    "patch_bytes_captured",
                    "repository_component_mapping_declared",
                    "local_checks_recorded",
                ],
            }
            for component_id, repository_id, patch_id in (
                ("platform-core", "repo-001", "platform-patch"),
                ("settings-ui", "repo-002", "settings-patch"),
            )
        ],
    }
    refresh_inventory(package, manifest)
    return package


def build_capture_v21(package: Path, *, layers: tuple[str, ...] | None = None) -> Path:
    package = build_capture(package)
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    manifest["schema_version"] = "2.1"
    payloads = {
        "changed-files": {
            "kind": "changed_files",
            "repositories": [
                {
                    "repository_id": "repo-001",
                    "component_ids": ["platform-core"],
                    "modified_files": ["frameworks/base/core.java"],
                },
                {
                    "repository_id": "repo-002",
                    "component_ids": ["settings-ui"],
                    "modified_files": ["packages/apps/Settings/Settings.java"],
                },
            ],
            "modified_files": [
                "frameworks/base/core.java",
                "packages/apps/Settings/Settings.java",
            ],
        },
        "patch-diff-facts": {
            "kind": "patch_diff_facts",
            "modified_files": [
                "frameworks/base/core.java",
                "packages/apps/Settings/Settings.java",
            ],
        },
        "risk-surface": {
            "kind": "risk_surface",
            "risk_areas": ["platform API", "settings integration"],
            "basis": ["two component patches"],
            "limits": ["one product variant"],
        },
        "coding-standard-check": {"result": "PASS", "errors": [], "warnings": []},
        "verification-result": {
            "kind": "verification_result",
            "result": "PASS",
            "contract_version": "akbs-verification-evidence/v2",
            "scope": "feature",
            "requirement_acceptance": "accepted",
            "method": "device",
            "build": ["m framework-minus-apex Settings"],
            "steps": ["feature behavior passed"],
            "health_checks": ["system_server and Settings healthy"],
        },
        "rollback-plan": {
            "kind": "rollback_plan",
            "result": "PASS",
            "plan": "Reverse both captured patches and restore the previous artifacts.",
        },
        "search-before-change": {
            "kind": "search_before_change",
            "result": "PASS",
            "searched": True,
            "decision": "not_found",
        },
        "component-assertion": {
            "kind": "component_assertion",
            "result": "INFO",
            "component_ids": ["settings-ui"],
            "assertions": [
                {
                    "component_id": "settings-ui",
                    "assertion_id": "permission_signing_compatibility",
                    "result": "PASS",
                    "observations": ["existing platform signing path retained"],
                }
            ],
        },
        "package-check": {
            "status": "PASS",
            "errors": [],
            "warnings": [],
            "declared_package_status": "validated",
            "effective_package_status": "validated",
            "status_was_upgraded": False,
        },
    }
    for evidence_id, payload in payloads.items():
        write_json(package / "evidence" / f"{evidence_id}.json", payload)
    all_components = ["platform-core", "settings-ui"]
    rows = [
        {
            "id": "changed-files",
            "kind": "changed_files",
            "result": "INFO",
            "claims": ["repository_change_inventory"],
        },
        {
            "id": "patch-diff-facts",
            "kind": "patch_diff_facts",
            "result": "INFO",
            "claims": ["patch_bytes_parsed"],
        },
        {
            "id": "risk-surface",
            "kind": "risk_surface",
            "result": "INFO",
            "claims": ["risk_surface_recorded"],
        },
        {
            "id": "coding-standard-check",
            "kind": "coding_standard_check",
            "result": "PASS",
            "claims": ["local_policy_check_recorded"],
        },
        {
            "id": "verification-result",
            "kind": "verification_result",
            "result": "PASS",
            "claims": ["verification_recorded_not_server_accepted"],
        },
        {
            "id": "rollback-plan",
            "kind": "rollback_plan",
            "result": "PASS",
            "claims": ["rollback_plan_recorded"],
        },
        {
            "id": "search-before-change",
            "kind": "search_before_change",
            "result": "PASS",
            "claims": ["optional_search_decision_recorded"],
        },
        {
            "id": "package-check",
            "kind": "package_check",
            "result": "PASS",
            "claims": ["local_package_check_recorded"],
        },
        {
            "id": "component-assertion",
            "kind": "component_assertion",
            "result": "INFO",
            "claims": ["component_assertions_recorded"],
            "component_ids": ["settings-ui"],
            "contract_id": "android-patch-capture-component-assertion",
        },
    ]
    for payload_id, payload in payloads.items():
        if payload_id != "component-assertion":
            payload["component_ids"] = all_components
            write_json(package / "evidence" / f"{payload_id}.json", payload)
    manifest["evidence"] = [
        {
            "id": row["id"],
            "kind": row["kind"],
            "path": f"evidence/{row['id']}.json",
            "result": row["result"],
            "scope": "change",
            "summary": f"{row['id']} fixture",
            "component_ids": row.get("component_ids", all_components),
            "contract": {
                "id": row.get("contract_id", "android-patch-capture-evidence"),
                "version": "2.1",
            },
            "declared_claims": row["claims"],
        }
        for row in rows
    ]
    evidence_by_component = {
        component_id: [
            row["id"]
            for row in manifest["evidence"]
            if component_id in row["component_ids"]
        ]
        for component_id in all_components
    }
    for binding in manifest["qualification_bindings"]:
        component_id = binding["component_id"]
        binding["evidence_ids"] = evidence_by_component[component_id]
        binding["contract"] = "android-patch-capture-local-qualification-v2"
        binding["declared_claims"] = list(
            dict.fromkeys(
                claim
                for row in manifest["evidence"]
                if component_id in row["component_ids"]
                for claim in row["declared_claims"]
            )
        )
    refresh_inventory(package, manifest)
    if layers is not None:
        configure_capture_layers(package, layers)
    return package


LAYER_CAPTURE_FIXTURES = {
    "application": {
        "facets": ("settings-ui", "system_app", "system_ext", "product"),
        "qualifiers": ["installable"],
        "repo": "packages/apps/Settings",
        "file": "src/DisplaySettings.java",
        "change": ("return requested;", "return Math.min(requested, 100);"),
        "build": "m Settings completed with exit 0",
        "runtime": "Installed Settings, opened display settings, and confirmed brightness is capped at 100.",
        "assertions": [
            ("permission_and_signing", "permission_signing_compatibility", "Platform certificate and privileged permission grants match the baseline."),
            ("install_or_upgrade", "install_upgrade_behavior", "Updated Settings without clearing data; the saved brightness preference survived."),
        ],
    },
    "platform": {
        "facets": ("platform-core", "framework_api", "system", "aosp"),
        "qualifiers": [],
        "repo": "frameworks/base",
        "file": "core/java/android/os/DisplayBrightness.java",
        "change": ("return requested;", "return Math.min(requested, 100);"),
        "build": "m framework-minus-apex completed with exit 0",
        "runtime": "Called the brightness API with 101 and observed 100 through the native service.",
        "assertions": [
            ("api_or_resource_compatibility", "api_resource_compatibility", "The public method signature is unchanged and the API compatibility check passed."),
        ],
    },
    "native": {
        "facets": ("native-brightness", "native_library", "system", "aosp"),
        "qualifiers": ["published_abi_or_api"],
        "repo": "frameworks/native",
        "file": "libs/brightness/brightness.cpp",
        "change": ("return requested;", "return std::min(requested, 100);"),
        "build": "m libbrightness completed with exit 0",
        "runtime": "Loaded libbrightness and exercised inputs 0, 100, and 101; observed 0, 100, and 100 with no linker errors.",
        "assertions": [
            ("abi_api_or_linker_compatibility", "abi_api_linker_compatibility", "Exported symbols and SONAME match the baseline; the platform caller resolves the library."),
        ],
    },
    "hal": {
        "facets": ("hal-lights", "aidl_hal", "vendor", "silicon_vendor"),
        "qualifiers": [],
        "repo": "hardware/interfaces",
        "file": "light/aidl/default/Lights.cpp",
        "change": ("return level;", "return std::min(level, 100);"),
        "build": "m android.hardware.light-service.example completed with exit 0",
        "runtime": "Started the light HAL and confirmed the device LED follows the requested bounded level.",
        "assertions": [
            ("interface_version", "interface_version_compatibility", "The frozen AIDL version and interface hash match the registered client."),
            ("vintf_selinux_service_registration", "vintf_selinux_service_registration", "VINTF check passed, the service is registered, and the LED exercise produced no AVC denial."),
            ("hardware_behavior", "hardware_behavior", "Measured the device LED at low and maximum brightness after invoking the HAL."),
        ],
    },
    "kernel": {
        "facets": ("kernel-backlight", "driver", "vendor_boot", "silicon_vendor"),
        "qualifiers": ["power_managed"],
        "repo": "kernel",
        "file": "drivers/video/backlight/panel_bl.c",
        "change": ("return level;", "return min_t(u32, level, 100);"),
        "build": "make Image modules completed with exit 0 using the product defconfig",
        "runtime": "Booted the captured kernel, reached sys.boot_completed=1, and exercised backlight control.",
        "assertions": [
            ("kconfig_and_build_graph", "kconfig_build_graph", "CONFIG_BACKLIGHT_PANEL is enabled and panel_bl.o is linked into the product kernel."),
            ("probe_bind_or_dmesg", "probe_bind_dmesg", "The panel driver bound successfully; dmesg has no probe failure or kernel oops."),
            ("hardware_behavior", "hardware_behavior", "Measured panel brightness while writing 0 and 100 through sysfs."),
            ("power_and_suspend_resume", "power_suspend_resume", "Completed three suspend/resume cycles and restored the previous panel brightness."),
        ],
    },
    "device": {
        "facets": ("device-board", "device_tree", "vendor_boot", "product"),
        "qualifiers": [],
        "repo": "device/example/board",
        "file": "board.dts",
        "change": ('status = "disabled";', 'status = "okay";'),
        "build": "m vendorbootimage dtboimage completed with exit 0",
        "runtime": "Booted the updated board images and observed the backlight node bound to the expected driver.",
        "assertions": [
            ("partition_overlay_or_policy", "partition_overlay_policy", "The board DTBO is present in the expected image and selects the correct backlight node."),
            ("device_behavior", "device_behavior", "The display and backlight both operate on the target board after a cold boot."),
        ],
    },
    "build": {
        "facets": ("build-soong", "soong", "build_host", "aosp"),
        "qualifiers": ["reproducibility_relevant"],
        "repo": "build/soong",
        "file": "Android.bp",
        "change": ('cflags: ["-Wall"],', 'cflags: ["-Wall", "-Werror"],'),
        "build": "Clean and incremental m libbrightness completed with exit 0",
        "runtime": "Compared generated artifacts and exercised the library packaged by the updated build graph.",
        "assertions": [
            ("dependency_graph", "dependency_graph", "Ninja graph resolves libbrightness and its generated header with no missing or cyclic edge."),
            ("clean_and_incremental", "clean_incremental_build", "Both a clean output directory and an incremental rebuild after a header edit succeeded."),
            ("artifact_contract", "artifact_contract", "The output remains a shared ELF library with the expected install path and exported symbols."),
            ("reproducibility", "reproducibility", "Two clean builds from the same inputs produced equal artifact SHA-256 values."),
        ],
    },
}

COMMON_CAPTURE_GROUPS = {
    "source_integrity", "change_diff_facts", "risk_surface", "android_change_policy",
    "feature_acceptance", "regression", "rollback", "pre_change_search",
}
LAYER_VERIFICATION_GROUPS = {
    "application": {"application_build", "application_runtime_or_integration"},
    "platform": {"platform_build", "platform_runtime"},
    "native": {"native_build", "native_runtime"},
    "hal": {"hal_build"},
    "kernel": {"kernel_or_module_build", "boot"},
    "device": {"board_build", "boot_integration"},
    "build": {"build_result"},
}


def configure_capture_layers(package: Path, layers: tuple[str, ...]) -> None:
    """Replace the default topology with scoped, layer-specific source and evidence."""
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    patch_template = copy.deepcopy(manifest["patches"][0])
    verification_template = json.loads(
        (package / "evidence/verification-result.json").read_text(encoding="utf-8")
    )
    for patch in manifest["patches"]:
        (package / patch["path"]).unlink()
    for row in manifest["evidence"]:
        if row["kind"] in {"verification_result", "component_assertion"}:
            (package / row["path"]).unlink()
    manifest["evidence"] = [
        row for row in manifest["evidence"]
        if row["kind"] not in {"verification_result", "component_assertion"}
    ]
    manifest["components"] = []
    manifest["git_repositories"] = []
    manifest["patches"] = []
    changed_repositories = []
    for index, layer in enumerate(layers, 1):
        fixture = LAYER_CAPTURE_FIXTURES[layer]
        component_id, component_type, partition, ownership = fixture["facets"]
        component = {
            "id": component_id, "layer": layer, "type": component_type,
            "partition": partition, "ownership": ownership,
            "qualifiers": fixture["qualifiers"],
        }
        manifest["components"].append(component)
        repository = {
            "id": f"repo-{index:03d}", "repo_path": fixture["repo"],
            "root": f"/source/{fixture['repo']}", "component_ids": [component_id],
            "git": {"head": f"{index:040x}", "branch": "brightness-fix"},
        }
        manifest["git_repositories"].append(repository)
        before, after = fixture["change"]
        source_file = fixture["file"]
        patch_raw = (
            f"diff --git a/{source_file} b/{source_file}\n"
            f"--- a/{source_file}\n+++ b/{source_file}\n"
            f"@@ -1 +1 @@\n-{before}\n+{after}\n"
        ).encode("utf-8")
        patch = copy.deepcopy(patch_template)
        patch.update(
            id=f"{component_id}-patch", path=f"patches/{component_id}.patch",
            repository_id=repository["id"], repo_path=repository["repo_path"],
            component_ids=[component_id], source_root=repository["root"],
            content_sha1=hashlib.sha1(patch_raw).hexdigest(),
            facts={"content_sha1": hashlib.sha1(patch_raw).hexdigest()},
        )
        (package / patch["path"]).write_bytes(patch_raw)
        manifest["patches"].append(patch)
        changed_repositories.append({
            "repository_id": repository["id"], "component_ids": [component_id],
            "modified_files": [f"{fixture['repo']}/{source_file}"],
        })

    all_components = [component["id"] for component in manifest["components"]]
    modified_files = [path for row in changed_repositories for path in row["modified_files"]]
    for row in manifest["evidence"]:
        row["component_ids"] = all_components
        payload_path = package / row["path"]
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
        payload["component_ids"] = all_components
        if row["kind"] == "changed_files":
            payload.update(repositories=changed_repositories, modified_files=modified_files)
        elif row["kind"] == "patch_diff_facts":
            payload["modified_files"] = modified_files
        elif row["kind"] == "risk_surface":
            payload.update(
                risk_areas=[f"{layer} brightness integration" for layer in layers],
                basis=modified_files, limits=["synthetic fixture for one product variant"],
            )
        elif row["kind"] == "rollback_plan":
            payload["plan"] = "Reverse all captured patches and restore the corresponding baseline artifacts."
        write_json(payload_path, payload)

    for layer in layers:
        fixture = LAYER_CAPTURE_FIXTURES[layer]
        component_id = fixture["facets"][0]
        verification = copy.deepcopy(verification_template)
        verification.update(
            component_ids=[component_id], build=[fixture["build"]],
            steps=[fixture["runtime"]], health_checks=[f"No crash or error during {component_id} verification."],
        )
        assertion = {
            "kind": "component_assertion", "result": "INFO", "component_ids": [component_id],
            "assertions": [
                {"component_id": component_id, "assertion_id": assertion_id,
                 "result": "PASS", "observations": [observation]}
                for _group, assertion_id, observation in fixture["assertions"]
            ],
        }
        for prefix, payload, claim in (
            ("verification", verification, "verification_recorded_not_server_accepted"),
            ("assertion", assertion, "component_assertions_recorded"),
        ):
            evidence_id = f"{prefix}-{component_id}"
            path = f"evidence/{evidence_id}.json"
            write_json(package / path, payload)
            manifest["evidence"].append({
                "id": evidence_id, "kind": payload["kind"], "path": path,
                "result": payload["result"], "scope": "component",
                "summary": f"{component_id} {prefix} fixture", "component_ids": [component_id],
                "contract": {
                    "id": "android-patch-capture-component-assertion" if prefix == "assertion" else "android-patch-capture-evidence",
                    "version": "2.1",
                },
                "declared_claims": [claim],
            })
    manifest["primary_component_id"] = all_components[0]
    manifest["source_roots"] = [row["root"] for row in manifest["git_repositories"]]
    manifest["qualification_bindings"] = [
        {
            "component_id": component_id, "repository_ids": [repository["id"]],
            "patch_ids": [patch["id"]],
            "evidence_ids": [row["id"] for row in manifest["evidence"] if component_id in row["component_ids"]],
            "contract": "android-patch-capture-local-qualification-v2",
            "declared_claims": ["patch_bytes_captured", "local_checks_recorded"],
        }
        for component_id, repository, patch in zip(all_components, manifest["git_repositories"], manifest["patches"])
    ]
    refresh_inventory(package, manifest)


def legacy_qualification_contract() -> tuple:
    _raw, pack, profiles, item_schema, collection_schema = materializer._load_qualification_contract()
    legacy_pack = copy.deepcopy(pack)
    legacy_pack["capability"]["executable_layers"] = ["application", "platform"]
    legacy_pack["capability"]["disabled_layers"] = {
        layer: "layer_not_enabled" for layer in ("native", "hal", "kernel", "device", "build")
    }
    legacy_raw = (json.dumps(legacy_pack, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    assert hashlib.sha256(legacy_raw).hexdigest() == "ac064f0c6215ff9471b3b7c6ab8f9dab9fcec066b112cadfbb334985cd09b1a4"
    return legacy_raw, legacy_pack, profiles, item_schema, collection_schema


def resign_canonical_package(package: Path, manifest: dict) -> None:
    """Rebind the fixture's real bytes and client-output hash after tampering."""
    output_id = manifest["qualification"]["client_adapter_outputs_file_id"]
    output_row = next(row for row in manifest["files"] if row["id"] == output_id)
    for row in manifest["files"]:
        if row["id"] != output_id:
            raw = (package / row["path"]).read_bytes()
            row.update(sha256=hashlib.sha256(raw).hexdigest(), size_bytes=len(raw))
    outputs = json.loads((package / output_row["path"]).read_text(encoding="utf-8"))
    outputs["qualification_input_sha256"] = validation.qualification_input_sha256(manifest)
    raw = write_json(package / output_row["path"], outputs)
    output_row.update(sha256=hashlib.sha256(raw).hexdigest(), size_bytes=len(raw))
    write_json(package / "manifest.json", manifest)


class CaptureAdapterPreflightTest(unittest.TestCase):
    def test_capture_schema_is_byte_identical_and_hash_pinned(self) -> None:
        engineering_schema = (
            ROOT
            / "plugins"
            / "android-engineering-ops"
            / "contracts"
            / "android-patch-capture"
            / "v2"
            / "capture-package.schema.json"
        )
        member_schema = capture_adapter.CAPTURE_SCHEMA_PATH
        raw = member_schema.read_bytes()
        self.assertEqual(raw, engineering_schema.read_bytes())
        self.assertEqual(
            hashlib.sha256(raw).hexdigest(),
            validation.CONTRACT_SHA256["capture-package.schema.json"],
        )
        self.assertEqual(
            json.loads(raw)["$schema"],
            capture_adapter.DRAFT_2020_12_SCHEMA,
        )

    def test_phase4_contracts_are_exact_copies_and_freeze_37_groups(self) -> None:
        engineering = (
            ROOT
            / "plugins/android-engineering-ops/contracts/android-patch-capture/v2"
            / "capture-package-v2.1.schema.json"
        )
        member = capture_adapter.CAPTURE_SCHEMA_V21_PATH
        self.assertEqual(engineering.read_bytes(), member.read_bytes())
        self.assertEqual(
            hashlib.sha256(member.read_bytes()).hexdigest(),
            validation.CONTRACT_SHA256["capture-package-v2.1.schema.json"],
        )
        root_pack = ROOT / "contracts/incoming/v2/qualification-contract-pack-v2.json"
        member_pack = (
            PLUGIN / "contracts/incoming/v2/qualification-contract-pack-v2.json"
        )
        self.assertEqual(root_pack.read_bytes(), member_pack.read_bytes())
        self.assertEqual(
            hashlib.sha256(root_pack.read_bytes()).hexdigest(),
            validation.CONTRACT_SHA256["qualification-contract-pack-v2.json"],
        )
        pack = json.loads(root_pack.read_text(encoding="utf-8"))
        self.assertNotIn("server_qualified", pack)
        self.assertEqual(
            pack["authority"],
            {
                "client_adapter_outputs": "untrusted_client_input",
                "server_qualification_decision_owner": "akbs_server",
                "server_decision_contract": "akbs-server-qualification-decision-v1",
            },
        )
        self.assertEqual(len(pack["groups"]), 37)
        self.assertEqual(
            set(pack["capability"]["executable_layers"]),
            set(LAYER_CAPTURE_FIXTURES),
        )
        self.assertEqual(pack["capability"]["disabled_layers"], {})
        schemas = (
            "qualification-adapter-input-v2.schema.json",
            "qualification-adapter-inputs-v2.schema.json",
        )
        loaded = {}
        for name in schemas:
            root_schema = ROOT / "contracts/incoming/v2" / name
            member_schema = PLUGIN / "contracts/incoming/v2" / name
            self.assertEqual(root_schema.read_bytes(), member_schema.read_bytes())
            self.assertEqual(
                hashlib.sha256(root_schema.read_bytes()).hexdigest(),
                validation.CONTRACT_SHA256[name],
            )
            loaded[name] = json.loads(root_schema.read_text(encoding="utf-8"))
        self.assertEqual(
            loaded["qualification-adapter-input-v2.schema.json"]["$defs"],
            loaded["qualification-adapter-inputs-v2.schema.json"]["$defs"],
        )
        self.assertEqual(
            pack["input_binding"]["item_schema"]["sha256"],
            validation.CONTRACT_SHA256[
                "qualification-adapter-input-v2.schema.json"
            ],
        )
        self.assertEqual(
            pack["input_binding"]["collection_schema"]["sha256"],
            validation.CONTRACT_SHA256[
                "qualification-adapter-inputs-v2.schema.json"
            ],
        )

    def test_engineering_capture_does_not_embed_akbs_qualification_contracts(self) -> None:
        engineering = ROOT / "plugins/android-engineering-ops"
        self.assertEqual(
            list(engineering.rglob("qualification-contract-pack-v2.json")),
            [],
        )
        surfaces = (
            engineering
            / "skills/android-patch-capture/scripts/capture_android_patch.py",
            engineering
            / "contracts/android-patch-capture/v2/capture-package-v2.1.schema.json",
            engineering / "skills/android-patch-capture/SKILL.md",
        )
        for surface in surfaces:
            with self.subTest(surface=surface):
                self.assertNotIn(
                    "akbs-qualification-",
                    surface.read_text(encoding="utf-8"),
                )

    def test_draft_2020_schema_validation_runs_before_semantic_checks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            capture = build_capture(Path(temporary) / "capture")
            manifest = json.loads((capture / "manifest.json").read_text(encoding="utf-8"))
            manifest["unknown_field"] = "must fail structurally"
            write_json(capture / "manifest.json", manifest)
            with mock.patch.object(
                capture_adapter, "_validate_identity_status_authority"
            ) as semantic:
                with self.assertRaisesRegex(AndroidChangeV2Error, "additional properties"):
                    preflight_capture(capture)
            semantic.assert_not_called()

        with tempfile.TemporaryDirectory() as temporary:
            capture = build_capture(Path(temporary) / "capture")
            schema = validation._load_contract(capture_adapter.CAPTURE_SCHEMA_PATH)
            schema["$schema"] = "https://json-schema.org/draft/2019-09/schema"
            with mock.patch.object(capture_adapter, "_load_contract", return_value=schema), mock.patch.object(
                capture_adapter, "_validate_identity_status_authority"
            ) as semantic:
                with self.assertRaisesRegex(AndroidChangeV2Error, "does not declare Draft 2020-12"):
                    preflight_capture(capture)
            semantic.assert_not_called()

    def test_capture_schema_rejects_unknown_authority_shaped_fields(self) -> None:
        mutations = (
            ("top-level", lambda manifest: manifest.__setitem__("server_package_id", "fake")),
            (
                "repository",
                lambda manifest: manifest["git_repositories"][0].__setitem__(
                    "server_repository_id", "fake"
                ),
            ),
            (
                "patch",
                lambda manifest: manifest["patches"][0].__setitem__(
                    "server_patch_id", "fake"
                ),
            ),
            (
                "evidence",
                lambda manifest: manifest["evidence"][0].__setitem__(
                    "server_qualified", True
                ),
            ),
            (
                "qualification",
                lambda manifest: manifest["qualification_bindings"][0].__setitem__(
                    "server_qualification_id", "fake"
                ),
            ),
        )
        for label, mutate in mutations:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                capture = build_capture(Path(temporary) / "capture")
                manifest = json.loads((capture / "manifest.json").read_text(encoding="utf-8"))
                mutate(manifest)
                write_json(capture / "manifest.json", manifest)
                with self.assertRaisesRegex(AndroidChangeV2Error, "additional properties"):
                    preflight_capture(capture)

    def test_valid_multi_component_capture_reports_only_blocked_gap_without_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            capture = build_capture(workspace / "capture")
            before = {
                path.relative_to(workspace).as_posix(): path.read_bytes()
                for path in workspace.rglob("*")
                if path.is_file()
            }
            result = preflight_capture(capture)
            after = {
                path.relative_to(workspace).as_posix(): path.read_bytes()
                for path in workspace.rglob("*")
                if path.is_file()
            }
            self.assertEqual(after, before)
            self.assertEqual(result["status"], "BLOCKED")
            self.assertEqual(
                result["reason_code"], "android_change_v2_adapter_contracts_unavailable"
            )
            self.assertEqual(result["capture"]["change_id"], "cross-component-feature")
            self.assertEqual(result["capture"]["package_type"], "android_change_capture")
            self.assertEqual(result["capture"]["schema"], "android-patch-capture-package-v2")
            self.assertEqual(result["capture"]["schema_version"], "2.0")
            self.assertEqual(
                result["preflight"]["schema_dialect"],
                capture_adapter.DRAFT_2020_12_SCHEMA,
            )
            self.assertEqual(result["capture"]["primary_component_id"], "platform-core")
            self.assertEqual(
                [item["id"] for item in result["capture"]["components"]],
                ["platform-core", "settings-ui"],
            )
            self.assertEqual(
                result["capture"]["patches"][1]["component_ids"], ["settings-ui"]
            )
            self.assertFalse(result["adapter"]["canonical_package_created"])
            self.assertFalse(result["adapter"]["client_adapter_outputs_created"])
            self.assertFalse(result["writer"]["v1_fallback"])
            self.assertEqual(result["writer"]["network_requests"], 0)
            self.assertEqual(result["writer"]["files_written"], 0)
            gap = result["gaps"][0]
            self.assertEqual(
                gap["code"], "versioned_evidence_group_adapter_input_contracts_missing"
            )
            self.assertTrue(gap["groups"])

    def test_schema_status_authority_and_component_bindings_fail_closed(self) -> None:
        mutations = (
            ("schema", lambda manifest: manifest.__setitem__("schema_version", "1.0")),
            ("status", lambda manifest: manifest.__setitem__("effective_status", "candidate")),
            (
                "authority",
                lambda manifest: manifest["authority"].__setitem__("can_upload", True),
            ),
            (
                "component",
                lambda manifest: manifest["patches"][1].__setitem__(
                    "component_ids", ["platform-core"]
                ),
            ),
            ("top-change-domain", lambda manifest: manifest.__setitem__("change_domain", "framework")),
            (
                "patch-change-domain",
                lambda manifest: manifest["patches"][0].__setitem__("change_domain", "framework"),
            ),
        )
        for label, mutate in mutations:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                capture = build_capture(Path(temporary) / "capture")
                manifest = json.loads((capture / "manifest.json").read_text(encoding="utf-8"))
                mutate(manifest)
                write_json(capture / "manifest.json", manifest)
                with self.assertRaises(AndroidChangeV2Error):
                    preflight_capture(capture)

    def test_full_inventory_patch_hash_and_symlink_are_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            capture = build_capture(Path(temporary) / "capture")
            (capture / "unexpected.txt").write_text("extra", encoding="utf-8")
            with self.assertRaisesRegex(AndroidChangeV2Error, "file_inventory"):
                preflight_capture(capture)

        with tempfile.TemporaryDirectory() as temporary:
            capture = build_capture(Path(temporary) / "capture")
            patch = capture / "patches" / "platform-patch.patch"
            patch.write_text("different bytes", encoding="utf-8")
            manifest = json.loads((capture / "manifest.json").read_text(encoding="utf-8"))
            refresh_inventory(capture, manifest)
            with self.assertRaisesRegex(AndroidChangeV2Error, "content_sha1 differs"):
                preflight_capture(capture)

        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            capture = build_capture(workspace / "capture")
            outside = workspace / "outside.json"
            outside.write_text('{"result":"PASS"}\n', encoding="utf-8")
            evidence = capture / "evidence" / "coding-standard-check.json"
            evidence.unlink()
            evidence.symlink_to(outside)
            with self.assertRaisesRegex(AndroidChangeV2Error, "symbolic link"):
                preflight_capture(capture)

    def test_evidence_component_result_and_path_bindings_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            capture = build_capture(Path(temporary) / "capture")
            manifest = json.loads((capture / "manifest.json").read_text(encoding="utf-8"))
            for evidence in manifest["evidence"]:
                evidence["component_ids"] = ["platform-core"]
            write_json(capture / "manifest.json", manifest)
            with self.assertRaisesRegex(AndroidChangeV2Error, "qualification evidence binding"):
                preflight_capture(capture)

        with tempfile.TemporaryDirectory() as temporary:
            capture = build_capture(Path(temporary) / "capture")
            write_json(capture / "evidence" / "coding-standard-check.json", {"result": "FAIL"})
            manifest = json.loads((capture / "manifest.json").read_text(encoding="utf-8"))
            refresh_inventory(capture, manifest)
            with self.assertRaisesRegex(AndroidChangeV2Error, "manifest result differs"):
                preflight_capture(capture)

        with tempfile.TemporaryDirectory() as temporary:
            capture = build_capture(Path(temporary) / "capture")
            manifest = json.loads((capture / "manifest.json").read_text(encoding="utf-8"))
            manifest["evidence"][1]["path"] = manifest["evidence"][0]["path"]
            write_json(capture / "manifest.json", manifest)
            with self.assertRaisesRegex(AndroidChangeV2Error, "paths must be unique"):
                preflight_capture(capture)

    def test_root_directory_swap_is_detected_while_pinned_inode_remains_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            capture = build_capture(workspace / "capture")
            displaced = workspace / "capture-displaced"
            replacement_sentinel = workspace / "capture" / "replacement-sentinel"
            original_inventory = capture_adapter._archive_inventory
            calls = 0

            def inventory_then_swap(root_fd: int) -> dict[str, dict[str, object]]:
                nonlocal calls
                result = original_inventory(root_fd)
                calls += 1
                if calls == 1:
                    capture.rename(displaced)
                    capture.mkdir()
                    replacement_sentinel.write_text("replacement", encoding="utf-8")
                return result

            with mock.patch.object(
                capture_adapter,
                "_archive_inventory",
                side_effect=inventory_then_swap,
            ):
                with self.assertRaisesRegex(AndroidChangeV2Error, "root pathname changed"):
                    preflight_capture(capture)
            self.assertEqual(replacement_sentinel.read_text(encoding="utf-8"), "replacement")
            self.assertTrue((displaced / "manifest.json").is_file())

    def test_semantic_evidence_read_is_bound_to_the_first_archive_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            capture = build_capture(Path(temporary) / "capture")
            original_read = capture_adapter._read_regular

            def substitute_after_inventory(
                root_fd: int,
                relative: str,
                *,
                max_bytes: int = capture_adapter.MAX_JSON_BYTES,
            ) -> bytes:
                if relative == "evidence/coding-standard-check.json":
                    return b'{"result":"FAIL"}\n'
                return original_read(root_fd, relative, max_bytes=max_bytes)

            with mock.patch.object(
                capture_adapter,
                "_read_regular",
                side_effect=substitute_after_inventory,
            ):
                with self.assertRaisesRegex(AndroidChangeV2Error, "pinned archive inventory"):
                    preflight_capture(capture)

    def test_capture_21_hashes_capture_labels_without_renaming_or_changing_bytes(self) -> None:
        for source_id in ("frameworks@base+change", "a" * 254 + "@+", "changed-files"):
            with self.subTest(source_id=source_id), tempfile.TemporaryDirectory() as temporary:
                workspace = Path(temporary)
                capture = build_capture_v21(workspace / "capture")
                manifest = json.loads((capture / "manifest.json").read_text(encoding="utf-8"))
                patch = manifest["patches"][0]
                old_id = patch["id"]
                patch["id"] = source_id
                original = capture / patch["path"]
                patch["path"] = "patches/frameworks@base+change.patch"
                original.rename(capture / patch["path"])
                long_evidence_id = "e" * 128
                evidence = manifest["evidence"][0]
                old_evidence_id = evidence["id"]
                evidence["id"] = long_evidence_id
                for binding in manifest["qualification_bindings"]:
                    binding["patch_ids"] = [source_id if value == old_id else value for value in binding["patch_ids"]]
                    binding["evidence_ids"] = [
                        long_evidence_id if value == old_evidence_id else value for value in binding["evidence_ids"]
                    ]
                refresh_inventory(capture, manifest)
                before = {path.relative_to(capture).as_posix(): path.read_bytes() for path in capture.rglob("*") if path.is_file()}
                result = materialize_capture(capture, member_alias="member01", output_root=workspace / "adapted")
                destination = Path(result["package"])
                canonical = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
                self.assertEqual(validation.check_package(destination)["status"], "PASS")
                digest = hashlib.sha256(source_id.encode("utf-8")).hexdigest()
                change = canonical["changes"][0]
                self.assertEqual(change["id"], "patch-" + digest)
                self.assertEqual(change["file_id"], "patch-file-" + digest)
                file_by_id = {row["id"]: row for row in canonical["files"]}
                self.assertEqual(len(file_by_id), len(canonical["files"]))
                for row in canonical["files"]:
                    self.assertLessEqual(len(row["id"]), 128)
                    self.assertRegex(row["id"], r"^[a-z0-9][a-z0-9._-]*$")
                    if row["role"] in {"patch", "evidence", "readme"}:
                        self.assertEqual((destination / row["path"]).read_bytes(), before[row["path"]])
                self.assertEqual(file_by_id[change["file_id"]]["path"], patch["path"])
                evidence_row = canonical["evidence"][0]
                self.assertEqual(evidence_row["id"], long_evidence_id)
                self.assertEqual(evidence_row["file_id"], "evidence-file-" + hashlib.sha256(long_evidence_id.encode()).hexdigest())
                mapping = canonical["extensions"]["akbs.android/capture-artifact-ids"]
                self.assertEqual(mapping["patches"][0]["source_id"], source_id)
                self.assertEqual(mapping["patches"][0]["change_id"], change["id"])
                self.assertEqual(mapping["patches"][0]["file_id"], change["file_id"])
                self.assertEqual(mapping["evidence"][0]["source_id"], long_evidence_id)
                self.assertEqual(mapping["evidence"][0]["file_id"], evidence_row["file_id"])
                second = materialize_capture(capture, member_alias="member01", output_root=workspace / "adapted")
                self.assertTrue(second["idempotent_reuse"])
                independent = materialize_capture(capture, member_alias="member01", output_root=workspace / "independent")
                self.assertEqual(result["manifest_sha256"], independent["manifest_sha256"])
                self.assertEqual(result["archive_inventory_sha256"], independent["archive_inventory_sha256"])
                after = {path.relative_to(capture).as_posix(): path.read_bytes() for path in capture.rglob("*") if path.is_file()}
                self.assertEqual(before, after)

    def test_capture_21_materializes_canonical_package_idempotently_without_rewrite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            capture = build_capture_v21(workspace / "capture")
            before = {
                path.relative_to(capture).as_posix(): path.read_bytes()
                for path in capture.rglob("*")
                if path.is_file()
            }
            output_root = workspace / "adapted"
            with mock.patch.object(urllib.request, "urlopen") as urlopen:
                first = materialize_capture(
                    capture,
                    member_alias="member01",
                    output_root=output_root,
                )
                second = materialize_capture(
                    capture,
                    member_alias="member01",
                    output_root=output_root,
                )
            self.assertEqual(first["status"], "PASS")
            self.assertFalse(first["server_qualified"])
            self.assertFalse(first["source_capture_rewritten"])
            self.assertFalse(first["idempotent_reuse"])
            self.assertTrue(second["idempotent_reuse"])
            self.assertFalse(second["source_capture_rewritten"])
            self.assertEqual(set(first), set(second))
            self.assertEqual(
                first["qualification_input_sha256"],
                second["qualification_input_sha256"],
            )
            self.assertEqual(first["package"], second["package"])
            checked = validation.check_package(Path(first["package"]))
            self.assertFalse(checked["coherence"]["server_qualified"])
            inputs_path = (
                Path(first["package"])
                / "metadata/qualification-adapter-inputs.json"
            )
            inputs = json.loads(inputs_path.read_text(encoding="utf-8"))
            item_schema = validation._load_contract(
                materializer.ADAPTER_INPUT_SCHEMA_PATH
            )
            collection_schema = validation._load_contract(
                materializer.ADAPTER_INPUTS_SCHEMA_PATH
            )
            validate_document(inputs, collection_schema)
            for item in inputs["inputs"]:
                validate_document(item, item_schema)
                self.assertIn(
                    item["component"]["id"], item["evidence"]["component_ids"]
                )
            unknown_collection = copy.deepcopy(inputs)
            unknown_collection["unknown"] = True
            with self.assertRaisesRegex(SchemaError, "additional properties"):
                validate_document(unknown_collection, collection_schema)
            unknown_item = copy.deepcopy(inputs["inputs"][0])
            unknown_item["evidence"]["unknown"] = True
            with self.assertRaisesRegex(SchemaError, "additional properties"):
                validate_document(unknown_item, item_schema)
            assertion_item = next(
                item
                for item in inputs["inputs"]
                if item["evidence"]["kind"] == "component_assertion"
            )
            consumer_group = copy.deepcopy(assertion_item)
            consumer_group["evidence"]["payload"]["assertions"][0][
                "group_id"
            ] = "permission_and_signing"
            with self.assertRaisesRegex(SchemaError, "oneOf|additional properties"):
                validate_document(consumer_group, item_schema)
            naked_pass = copy.deepcopy(assertion_item)
            naked_pass["evidence"]["payload"]["assertions"][0].pop(
                "observations"
            )
            with self.assertRaises(SchemaError):
                validate_document(naked_pass, item_schema)
            outer_pass = copy.deepcopy(assertion_item)
            outer_pass["evidence"]["result"] = "PASS"
            outer_pass["evidence"]["payload"]["result"] = "PASS"
            with self.assertRaises(SchemaError):
                validate_document(outer_pass, item_schema)
            after = {
                path.relative_to(capture).as_posix(): path.read_bytes()
                for path in capture.rglob("*")
                if path.is_file()
            }
            self.assertEqual(after, before)
            urlopen.assert_not_called()

    def test_capture_21_materializer_copies_only_descriptor_snapshot_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            capture = build_capture_v21(workspace / "capture")
            original_read_bytes = Path.read_bytes

            def reject_capture_path_reads(path: Path) -> bytes:
                resolved = path.resolve(strict=False)
                if resolved == capture or capture in resolved.parents:
                    raise AssertionError(f"unsafe capture pathname read: {path}")
                return original_read_bytes(path)

            with mock.patch.object(Path, "read_bytes", reject_capture_path_reads):
                result = materialize_capture(
                    capture,
                    member_alias="member01",
                    output_root=workspace / "adapted",
                )
            self.assertEqual(result["status"], "PASS")
            self.assertFalse(result["source_capture_rewritten"])

    def test_capture_21_large_patch_is_streamed_without_json_buffering(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            capture = build_capture_v21(workspace / "capture")
            patch_path = capture / "patches/platform-patch.patch"
            large_raw = b"diff --git a/core.java b/core.java\n" + b"x" * (
                capture_adapter.MAX_JSON_BYTES + 1024
            )
            patch_path.write_bytes(large_raw)
            manifest = json.loads((capture / "manifest.json").read_text(encoding="utf-8"))
            patch = next(row for row in manifest["patches"] if row["id"] == "platform-patch")
            patch["content_sha1"] = hashlib.sha1(large_raw).hexdigest()
            patch["facts"]["content_sha1"] = patch["content_sha1"]
            refresh_inventory(capture, manifest)
            original_read = capture_adapter._read_regular
            original_path_read = Path.read_bytes

            def forbid_patch_buffering(
                root_fd: int,
                relative: str,
                *,
                max_bytes: int = capture_adapter.MAX_JSON_BYTES,
            ) -> bytes:
                if relative.endswith(".patch"):
                    raise AssertionError(f"patch was buffered as JSON: {relative}")
                return original_read(root_fd, relative, max_bytes=max_bytes)

            def forbid_patch_path_reads(path: Path) -> bytes:
                if path.suffix == ".patch":
                    raise AssertionError(f"patch used Path.read_bytes: {path}")
                return original_path_read(path)

            with (
                mock.patch.object(
                    capture_adapter,
                    "_read_regular",
                    side_effect=forbid_patch_buffering,
                ),
                mock.patch.object(Path, "read_bytes", forbid_patch_path_reads),
            ):
                result = materialize_capture(
                    capture,
                    member_alias="member01",
                    output_root=workspace / "adapted",
                )
            copied = Path(result["package"]) / "patches/platform-patch.patch"
            self.assertEqual(copied.stat().st_size, len(large_raw))

    def test_capture_21_idempotency_rejects_foreign_identity_and_symlink_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            capture = build_capture_v21(workspace / "capture")
            output_root = workspace / "adapted"
            first = materialize_capture(
                capture,
                member_alias="member01",
                output_root=output_root,
            )
            valid = Path(first["package"])
            run_id = valid.name

            foreign = output_root / "member02" / run_id
            foreign.parent.mkdir(parents=True)
            shutil.copytree(valid, foreign)
            with self.assertRaisesRegex(AndroidChangeV2Error, "idempotency_conflict"):
                materialize_capture(
                    capture,
                    member_alias="member02",
                    output_root=output_root,
                )

            linked_root = workspace / "linked-destination-root"
            (linked_root / "member01").mkdir(parents=True)
            (linked_root / "member01" / run_id).symlink_to(
                valid,
                target_is_directory=True,
            )
            with self.assertRaisesRegex(AndroidChangeV2Error, "idempotency_path_unsafe"):
                materialize_capture(
                    capture,
                    member_alias="member01",
                    output_root=linked_root,
                )

            member_link_root = workspace / "linked-member-root"
            real_member = workspace / "real-member"
            member_link_root.mkdir()
            real_member.mkdir()
            (member_link_root / "member01").symlink_to(
                real_member,
                target_is_directory=True,
            )
            with self.assertRaisesRegex(AndroidChangeV2Error, "idempotency_path_unsafe"):
                materialize_capture(
                    capture,
                    member_alias="member01",
                    output_root=member_link_root,
                )

            real_root = workspace / "real-root"
            real_root.mkdir()
            root_link = workspace / "root-link"
            root_link.symlink_to(real_root, target_is_directory=True)
            with self.assertRaisesRegex(AndroidChangeV2Error, "idempotency_path_unsafe"):
                materialize_capture(
                    capture,
                    member_alias="member01",
                    output_root=root_link,
                )

    def test_capture_21_rejects_cross_component_borrow_and_unknown_layer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            capture = build_capture_v21(workspace / "capture-cross")
            changed_path = capture / "evidence/changed-files.json"
            changed = json.loads(changed_path.read_text(encoding="utf-8"))
            changed["repositories"] = changed["repositories"][:1]
            write_json(changed_path, changed)
            manifest = json.loads((capture / "manifest.json").read_text(encoding="utf-8"))
            refresh_inventory(capture, manifest)
            with self.assertRaisesRegex(
                AndroidChangeV2Error,
                "settings-ui.source_integrity",
            ):
                materialize_capture(
                    capture,
                    member_alias="member01",
                    output_root=workspace / "adapted-cross",
                )

            unknown = build_capture_v21(workspace / "capture-unknown")
            manifest = json.loads((unknown / "manifest.json").read_text(encoding="utf-8"))
            manifest["components"][0]["layer"] = "unknown-layer"
            refresh_inventory(unknown, manifest)
            with self.assertRaisesRegex(AndroidChangeV2Error, "enum mismatch"):
                materialize_capture(
                    unknown,
                    member_alias="member01",
                    output_root=workspace / "adapted-unknown",
                )

    def test_capture_21_each_layer_materializes_checks_and_prepares_bound_evidence(self) -> None:
        for layer, fixture in LAYER_CAPTURE_FIXTURES.items():
            with self.subTest(layer=layer), tempfile.TemporaryDirectory() as temporary:
                workspace = Path(temporary)
                capture = build_capture_v21(workspace / "capture", layers=(layer,))
                component_id = fixture["facets"][0]
                source_bytes = {
                    path.relative_to(capture).as_posix(): path.read_bytes()
                    for path in capture.rglob("*") if path.is_file()
                }
                with mock.patch.object(urllib.request, "urlopen") as urlopen:
                    result = materialize_capture(capture, member_alias="member01", output_root=workspace / "adapted")
                    package = Path(result["package"])
                    read = validation.read_package(package)
                    checked = validation.check_package(package)
                    prepared = validation.prepare_package(package, pending_root=workspace / "pending")
                urlopen.assert_not_called()
                self.assertEqual(result["status"], "PASS")
                self.assertEqual(read["component_layers"], [layer])
                self.assertEqual(checked["status"], "PASS")
                self.assertTrue(prepared["bytes_preserved"])
                self.assertFalse(result["server_qualified"])
                self.assertFalse(prepared["server_qualified"])
                inputs = json.loads((package / "metadata/qualification-adapter-inputs.json").read_text(encoding="utf-8"))
                expected_groups = (
                    COMMON_CAPTURE_GROUPS | LAYER_VERIFICATION_GROUPS[layer]
                    | {row[0] for row in fixture["assertions"]}
                )
                self.assertEqual({row["group_id"] for row in inputs["inputs"]}, expected_groups)
                self.assertEqual(len(inputs["inputs"]), len(expected_groups))
                manifest = json.loads((capture / "manifest.json").read_text(encoding="utf-8"))
                evidence_paths = {row["id"]: row["path"] for row in manifest["evidence"]}
                for row in inputs["inputs"]:
                    self.assertEqual(row["component"], manifest["components"][0])
                    self.assertEqual(row["evidence"]["component_ids"], [component_id])
                    raw = source_bytes[evidence_paths[row["evidence"]["id"]]]
                    self.assertEqual(row["evidence"]["sha256"], hashlib.sha256(raw).hexdigest())
                    self.assertEqual(row["evidence"]["payload"], json.loads(raw))
                for relative, raw in source_bytes.items():
                    self.assertEqual((capture / relative).read_bytes(), raw)
                    if relative != "manifest.json":
                        self.assertEqual((package / relative).read_bytes(), raw)
                prepared_package = Path(prepared["package"])
                for path in package.rglob("*"):
                    if path.is_file():
                        self.assertEqual((prepared_package / path.relative_to(package)).read_bytes(), path.read_bytes())

    def test_capture_21_platform_native_cross_layer_qualification_is_per_component(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            capture = build_capture_v21(workspace / "capture", layers=("platform", "native"))
            result = materialize_capture(capture, member_alias="member01", output_root=workspace / "adapted")
            package = Path(result["package"])
            inputs = json.loads((package / "metadata/qualification-adapter-inputs.json").read_text(encoding="utf-8"))
            outputs = json.loads((package / "metadata/client-adapter-outputs.json").read_text(encoding="utf-8"))
            manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual({row["layer"] for row in manifest["components"]}, {"platform", "native"})
            self.assertEqual(len([row for row in manifest["files"] if row["role"] == "patch"]), 2)
            self.assertEqual({row["component_id"] for row in outputs["components"]}, {"platform-core", "native-brightness"})
            for component in manifest["components"]:
                fixture = LAYER_CAPTURE_FIXTURES[component["layer"]]
                required = COMMON_CAPTURE_GROUPS | LAYER_VERIFICATION_GROUPS[component["layer"]] | {row[0] for row in fixture["assertions"]}
                component_inputs = [row for row in inputs["inputs"] if row["component"]["id"] == component["id"]]
                self.assertEqual({row["group_id"] for row in component_inputs}, required)
                for row in component_inputs:
                    if row["evidence"]["kind"] in {"verification_result", "component_assertion"}:
                        self.assertEqual(row["evidence"]["component_ids"], [component["id"]])
                output = next(row for row in outputs["components"] if row["component_id"] == component["id"])
                self.assertEqual({row["group_id"] for row in output["outputs"]}, required)
            prepared = validation.prepare_package(package, pending_root=workspace / "pending")
            self.assertTrue(prepared["bytes_preserved"])
            self.assertFalse(prepared["server_qualified"])

    def test_capture_21_conditional_groups_require_their_own_assertion(self) -> None:
        cases = (
            ("application", "permission_and_signing", "permission_signing_compatibility"),
            ("application", "install_or_upgrade", "install_upgrade_behavior"),
            ("platform", "api_or_resource_compatibility", "api_resource_compatibility"),
            ("native", "abi_api_or_linker_compatibility", "abi_api_linker_compatibility"),
            ("hal", "hardware_behavior", "hardware_behavior"),
            ("kernel", "hardware_behavior", "hardware_behavior"),
            ("kernel", "power_and_suspend_resume", "power_suspend_resume"),
            ("build", "reproducibility", "reproducibility"),
        )
        for layer, group_id, assertion_id in cases:
            with self.subTest(layer=layer, group=group_id), tempfile.TemporaryDirectory() as temporary:
                workspace = Path(temporary)
                capture = build_capture_v21(workspace / "capture", layers=(layer,))
                component_id = LAYER_CAPTURE_FIXTURES[layer]["facets"][0]
                evidence_id = f"assertion-{component_id}"
                assertion_path = capture / f"evidence/{evidence_id}.json"
                payload = json.loads(assertion_path.read_text(encoding="utf-8"))
                payload["assertions"] = [row for row in payload["assertions"] if row["assertion_id"] != assertion_id]
                manifest = json.loads((capture / "manifest.json").read_text(encoding="utf-8"))
                if payload["assertions"]:
                    write_json(assertion_path, payload)
                else:
                    assertion_path.unlink()
                    manifest["evidence"] = [row for row in manifest["evidence"] if row["id"] != evidence_id]
                    manifest["qualification_bindings"][0]["evidence_ids"].remove(evidence_id)
                refresh_inventory(capture, manifest)
                output_root = workspace / "adapted"
                with self.assertRaisesRegex(AndroidChangeV2Error, rf"{component_id}\.{group_id} has 0 qualifying evidence rows"):
                    materialize_capture(capture, member_alias="member01", output_root=output_root)
                self.assertFalse(output_root.exists())

    def test_capture_21_new_layers_do_not_accept_pass_without_build_or_runtime_facts(self) -> None:
        cases = (
            ("native", "build", "native_build"),
            ("native", "steps", "native_runtime"),
            ("hal", "build", "hal_build"),
            ("kernel", "build", "kernel_or_module_build"),
            ("kernel", "steps", "boot"),
            ("device", "build", "board_build"),
            ("device", "steps", "boot_integration"),
            ("build", "build", "build_result"),
        )
        for layer, field, group_id in cases:
            with self.subTest(layer=layer, field=field), tempfile.TemporaryDirectory() as temporary:
                workspace = Path(temporary)
                capture = build_capture_v21(workspace / "capture", layers=(layer,))
                component_id = LAYER_CAPTURE_FIXTURES[layer]["facets"][0]
                evidence_path = capture / f"evidence/verification-{component_id}.json"
                payload = json.loads(evidence_path.read_text(encoding="utf-8"))
                self.assertEqual(payload["result"], "PASS")
                payload[field] = []
                write_json(evidence_path, payload)
                manifest = json.loads((capture / "manifest.json").read_text(encoding="utf-8"))
                refresh_inventory(capture, manifest)
                with self.assertRaisesRegex(AndroidChangeV2Error, rf"{component_id}\.{group_id} has 0 qualifying evidence rows"):
                    materialize_capture(capture, member_alias="member01", output_root=workspace / "adapted")

    def test_capture_21_new_layer_assertions_require_observations_not_naked_pass(self) -> None:
        for layer in ("native", "hal", "kernel", "device", "build"):
            with self.subTest(layer=layer), tempfile.TemporaryDirectory() as temporary:
                workspace = Path(temporary)
                capture = build_capture_v21(workspace / "capture", layers=(layer,))
                component_id = LAYER_CAPTURE_FIXTURES[layer]["facets"][0]
                assertion_path = capture / f"evidence/assertion-{component_id}.json"
                payload = json.loads(assertion_path.read_text(encoding="utf-8"))
                payload["assertions"][0].pop("observations")
                write_json(assertion_path, payload)
                manifest = json.loads((capture / "manifest.json").read_text(encoding="utf-8"))
                refresh_inventory(capture, manifest)
                with self.assertRaisesRegex(AndroidChangeV2Error, "component assertion"):
                    materialize_capture(capture, member_alias="member01", output_root=workspace / "adapted")

    def test_capture_21_native_cannot_borrow_platform_verification_or_assertions(self) -> None:
        for evidence_kind in ("verification", "assertion"):
            with self.subTest(kind=evidence_kind), tempfile.TemporaryDirectory() as temporary:
                workspace = Path(temporary)
                capture = build_capture_v21(workspace / "capture", layers=("platform", "native"))
                manifest = json.loads((capture / "manifest.json").read_text(encoding="utf-8"))
                binding = next(row for row in manifest["qualification_bindings"] if row["component_id"] == "native-brightness")
                binding["evidence_ids"].remove(f"{evidence_kind}-native-brightness")
                binding["evidence_ids"].append(f"{evidence_kind}-platform-core")
                refresh_inventory(capture, manifest)
                with self.assertRaisesRegex(AndroidChangeV2Error, "qualification evidence binding differs: native-brightness"):
                    materialize_capture(capture, member_alias="member01", output_root=workspace / "adapted")

        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            capture = build_capture_v21(workspace / "capture", layers=("platform", "native"))
            manifest = json.loads((capture / "manifest.json").read_text(encoding="utf-8"))
            assertion_path = capture / "evidence/assertion-native-brightness.json"
            payload = json.loads(assertion_path.read_text(encoding="utf-8"))
            payload["assertions"][0]["component_id"] = "platform-core"
            write_json(assertion_path, payload)
            refresh_inventory(capture, manifest)
            with self.assertRaisesRegex(AndroidChangeV2Error, "component assertion identity/result differs"):
                materialize_capture(capture, member_alias="member01", output_root=workspace / "adapted")

    def test_capture_21_mandatory_structured_groups_cannot_be_not_applicable(self) -> None:
        cases = (
            ("hal", "interface_version", "interface_version_compatibility"),
            ("hal", "vintf_selinux_service_registration", "vintf_selinux_service_registration"),
            ("hal", "hardware_behavior", "hardware_behavior"),
            ("kernel", "kconfig_and_build_graph", "kconfig_build_graph"),
            ("kernel", "probe_bind_or_dmesg", "probe_bind_dmesg"),
            ("kernel", "hardware_behavior", "hardware_behavior"),
            ("device", "partition_overlay_or_policy", "partition_overlay_policy"),
            ("device", "device_behavior", "device_behavior"),
            ("build", "dependency_graph", "dependency_graph"),
            ("build", "clean_and_incremental", "clean_incremental_build"),
            ("build", "artifact_contract", "artifact_contract"),
        )
        for layer, group_id, assertion_id in cases:
            with self.subTest(layer=layer, group=group_id), tempfile.TemporaryDirectory() as temporary:
                workspace = Path(temporary)
                capture = build_capture_v21(workspace / "capture", layers=(layer,))
                component_id = LAYER_CAPTURE_FIXTURES[layer]["facets"][0]
                assertion_path = capture / f"evidence/assertion-{component_id}.json"
                payload = json.loads(assertion_path.read_text(encoding="utf-8"))
                assertion = next(row for row in payload["assertions"] if row["assertion_id"] == assertion_id)
                assertion.pop("observations")
                assertion.update(result="NOT_APPLICABLE", basis="Claimed static-only change", limits="Only this captured patch")
                write_json(assertion_path, payload)
                manifest = json.loads((capture / "manifest.json").read_text(encoding="utf-8"))
                refresh_inventory(capture, manifest)
                with self.assertRaisesRegex(AndroidChangeV2Error, rf"qualification_result_not_allowed: {component_id}\.{group_id} derived NOT_APPLICABLE"):
                    materialize_capture(capture, member_alias="member01", output_root=workspace / "adapted")

    def test_capture_21_upgrade_reuses_legacy_two_layer_package_without_changing_bytes(self) -> None:
        legacy_contract = legacy_qualification_contract()
        legacy_sha = "ac064f0c6215ff9471b3b7c6ab8f9dab9fcec066b112cadfbb334985cd09b1a4"
        self.assertEqual(hashlib.sha256(legacy_contract[0]).hexdigest(), legacy_sha)
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            capture = build_capture_v21(workspace / "capture")
            output_root = workspace / "adapted"
            with mock.patch.object(
                materializer, "_load_qualification_contract",
                return_value=legacy_contract,
            ):
                original = materialize_capture(capture, member_alias="member01", output_root=output_root)
            package = Path(original["package"])
            before = {
                path.relative_to(package).as_posix(): path.read_bytes()
                for path in package.rglob("*") if path.is_file()
            }
            with mock.patch.object(urllib.request, "urlopen") as urlopen:
                upgraded = materialize_capture(capture, member_alias="member01", output_root=output_root)
                read = validation.read_package(package)
                checked = validation.check_package(package)
                prepared = validation.prepare_package(package, pending_root=workspace / "pending")
            urlopen.assert_not_called()
            self.assertTrue(upgraded["idempotent_reuse"])
            self.assertFalse(upgraded["source_capture_rewritten"])
            self.assertFalse(upgraded["server_qualified"])
            self.assertEqual(original["qualification_contract_sha256"], legacy_sha)
            self.assertEqual(upgraded["qualification_contract_sha256"], legacy_sha)
            for field in ("package", "source_package_key", "manifest_sha256", "archive_inventory_sha256", "qualification_input_sha256"):
                self.assertEqual(upgraded[field], original[field])
            self.assertEqual(read["component_layers"], ["application", "platform"])
            self.assertEqual(checked["status"], "PASS")
            self.assertTrue(prepared["bytes_preserved"])
            after = {
                path.relative_to(package).as_posix(): path.read_bytes()
                for path in package.rglob("*") if path.is_file()
            }
            self.assertEqual(after, before)
            prepared_package = Path(prepared["package"])
            prepared_bytes = {
                path.relative_to(prepared_package).as_posix(): path.read_bytes()
                for path in prepared_package.rglob("*") if path.is_file()
            }
            self.assertEqual(prepared_bytes, before)

    def test_capture_21_native_package_cannot_reuse_legacy_two_layer_hash(self) -> None:
        _raw, current_pack, profiles, item_schema, collection_schema = materializer._load_qualification_contract()
        legacy_raw = legacy_qualification_contract()[0]
        self.assertEqual(hashlib.sha256(legacy_raw).hexdigest(), materializer.LEGACY_QUALIFICATION_PACK_SHA256)
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            capture = build_capture_v21(workspace / "capture", layers=("native",))
            output_root = workspace / "adapted"
            # Deliberately forge only the pack-byte source in this adversarial
            # fixture; all native evidence and derived manifest hashes are real.
            with mock.patch.object(
                materializer, "_load_qualification_contract",
                return_value=(legacy_raw, current_pack, profiles, item_schema, collection_schema),
            ):
                forged = materialize_capture(capture, member_alias="member01", output_root=output_root)
            package = Path(forged["package"])
            before = {path.relative_to(package).as_posix(): path.read_bytes() for path in package.rglob("*") if path.is_file()}
            with self.assertRaisesRegex(AndroidChangeV2Error, "idempotency_conflict: legacy qualification does not cover this component"):
                materialize_capture(capture, member_alias="member01", output_root=output_root)
            self.assertEqual(
                {path.relative_to(package).as_posix(): path.read_bytes() for path in package.rglob("*") if path.is_file()},
                before,
            )

    def test_capture_21_legacy_reuse_rejects_resigned_patch_bytes(self) -> None:
        legacy_contract = legacy_qualification_contract()
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            capture = build_capture_v21(workspace / "capture")
            output_root = workspace / "adapted"
            with mock.patch.object(materializer, "_load_qualification_contract", return_value=legacy_contract):
                original = materialize_capture(capture, member_alias="member01", output_root=output_root)
            package = Path(original["package"])
            manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
            patch_row = next(row for row in manifest["files"] if row["role"] == "patch")
            patch_path = package / patch_row["path"]
            patch_path.write_bytes(
                patch_path.read_bytes()
                + b"--- a/core.java\n+++ b/core.java\n@@ -1 +1 @@\n-return requested;\n+return 0;\n"
            )
            resign_canonical_package(package, manifest)
            checked = validation.check_package(package)
            self.assertEqual(checked["status"], "PASS")
            self.assertNotEqual(checked["manifest_sha256"], original["manifest_sha256"])
            self.assertNotEqual(checked["coherence"]["qualification_input_sha256"], original["qualification_input_sha256"])
            before = {path.relative_to(package).as_posix(): path.read_bytes() for path in package.rglob("*") if path.is_file()}
            with self.assertRaisesRegex(AndroidChangeV2Error, "idempotency_conflict"):
                materialize_capture(capture, member_alias="member01", output_root=output_root)
            self.assertEqual(
                {path.relative_to(package).as_posix(): path.read_bytes() for path in package.rglob("*") if path.is_file()},
                before,
            )

    def test_capture_21_legacy_reuse_rejects_resigned_component_facets(self) -> None:
        legacy_contract = legacy_qualification_contract()
        for facet, replacement in (("partition", "product"), ("ownership", "vendor")):
            with self.subTest(facet=facet), tempfile.TemporaryDirectory() as temporary:
                workspace = Path(temporary)
                capture = build_capture_v21(workspace / "capture")
                output_root = workspace / "adapted"
                with mock.patch.object(materializer, "_load_qualification_contract", return_value=legacy_contract):
                    original = materialize_capture(capture, member_alias="member01", output_root=output_root)
                package = Path(original["package"])
                manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
                self.assertNotEqual(manifest["components"][0][facet], replacement)
                manifest["components"][0][facet] = replacement
                resign_canonical_package(package, manifest)
                checked = validation.check_package(package)
                self.assertEqual(checked["status"], "PASS")
                self.assertNotEqual(checked["manifest_sha256"], original["manifest_sha256"])
                self.assertNotEqual(checked["coherence"]["qualification_input_sha256"], original["qualification_input_sha256"])
                before = {path.relative_to(package).as_posix(): path.read_bytes() for path in package.rglob("*") if path.is_file()}
                with self.assertRaisesRegex(AndroidChangeV2Error, "idempotency_conflict"):
                    materialize_capture(capture, member_alias="member01", output_root=output_root)
                self.assertEqual(
                    {path.relative_to(package).as_posix(): path.read_bytes() for path in package.rglob("*") if path.is_file()},
                    before,
                )

    def test_capture_21_not_applicable_rule_and_hash_replay_are_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            capture = build_capture_v21(workspace / "capture-na")
            assertion_path = capture / "evidence/component-assertion.json"
            assertion = json.loads(assertion_path.read_text(encoding="utf-8"))
            assertion["assertions"][0].update(
                result="NOT_APPLICABLE",
                basis="application does not change signing or privileged grants",
                limits="valid only for this package's unchanged manifest and certificate path",
            )
            assertion["assertions"][0].pop("observations")
            write_json(assertion_path, assertion)
            manifest = json.loads((capture / "manifest.json").read_text(encoding="utf-8"))
            refresh_inventory(capture, manifest)
            result = materialize_capture(
                capture,
                member_alias="member01",
                output_root=workspace / "adapted-na",
            )
            package = Path(result["package"])
            outputs = json.loads(
                (package / "metadata/client-adapter-outputs.json").read_text(
                    encoding="utf-8"
                )
            )
            permission = next(
                row
                for component in outputs["components"]
                if component["component_id"] == "settings-ui"
                for row in component["outputs"]
                if row["group_id"] == "permission_and_signing"
            )
            self.assertEqual(permission["adapter_result"], "NOT_APPLICABLE")
            self.assertEqual(
                set(permission["not_applicable_basis"]),
                {"basis", "limits"},
            )

            permission["source_evidence_sha256"] = "0" * 64
            output_path = package / "metadata/client-adapter-outputs.json"
            output_raw = write_json(output_path, outputs)
            package_manifest = json.loads(
                (package / "manifest.json").read_text(encoding="utf-8")
            )
            output_file = next(
                row
                for row in package_manifest["files"]
                if row["id"] == "qualification-client-output"
            )
            output_file["sha256"] = hashlib.sha256(output_raw).hexdigest()
            output_file["size_bytes"] = len(output_raw)
            write_json(package / "manifest.json", package_manifest)
            with self.assertRaisesRegex(
                AndroidChangeV2Error,
                "client evidence adapter output differs",
            ):
                validation.check_package(package)

            invalid = build_capture_v21(workspace / "capture-invalid-na")
            assertion_path = invalid / "evidence/component-assertion.json"
            assertion = json.loads(assertion_path.read_text(encoding="utf-8"))
            assertion["assertions"][0].update(
                result="NOT_APPLICABLE",
                basis="claimed N/A",
            )
            write_json(assertion_path, assertion)
            manifest = json.loads((invalid / "manifest.json").read_text(encoding="utf-8"))
            refresh_inventory(invalid, manifest)
            with self.assertRaisesRegex(
                AndroidChangeV2Error,
                "component assertion N/A basis differs",
            ):
                materialize_capture(
                    invalid,
                    member_alias="member01",
                    output_root=workspace / "adapted-invalid-na",
                )

    def test_capture_21_assertion_and_search_results_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            failed_assertion = build_capture_v21(workspace / "capture-failed-assertion")
            assertion_path = failed_assertion / "evidence/component-assertion.json"
            assertion = json.loads(assertion_path.read_text(encoding="utf-8"))
            assertion["assertions"][0]["result"] = "FAIL"
            write_json(assertion_path, assertion)
            manifest = json.loads(
                (failed_assertion / "manifest.json").read_text(encoding="utf-8")
            )
            refresh_inventory(failed_assertion, manifest)
            with self.assertRaisesRegex(
                AndroidChangeV2Error,
                "qualification_result_not_allowed",
            ):
                materialize_capture(
                    failed_assertion,
                    member_alias="member01",
                    output_root=workspace / "adapted-failed-assertion",
                )

            mismatched_union = build_capture_v21(workspace / "capture-union")
            assertion_path = mismatched_union / "evidence/component-assertion.json"
            assertion = json.loads(assertion_path.read_text(encoding="utf-8"))
            assertion["component_ids"] = ["platform-core", "settings-ui"]
            write_json(assertion_path, assertion)
            manifest = json.loads(
                (mismatched_union / "manifest.json").read_text(encoding="utf-8")
            )
            row = next(
                item for item in manifest["evidence"] if item["id"] == "component-assertion"
            )
            row["component_ids"] = ["platform-core", "settings-ui"]
            refresh_inventory(mismatched_union, manifest)
            with self.assertRaisesRegex(
                AndroidChangeV2Error,
                "component assertion component union differs",
            ):
                materialize_capture(
                    mismatched_union,
                    member_alias="member01",
                    output_root=workspace / "adapted-union",
                )

            failed_search = build_capture_v21(workspace / "capture-failed-search")
            search_path = failed_search / "evidence/search-before-change.json"
            search = json.loads(search_path.read_text(encoding="utf-8"))
            search["result"] = "FAIL"
            write_json(search_path, search)
            manifest = json.loads(
                (failed_search / "manifest.json").read_text(encoding="utf-8")
            )
            next(
                item for item in manifest["evidence"] if item["id"] == "search-before-change"
            )["result"] = "FAIL"
            refresh_inventory(failed_search, manifest)
            with self.assertRaisesRegex(
                AndroidChangeV2Error,
                "pre_change_search",
            ):
                materialize_capture(
                    failed_search,
                    member_alias="member01",
                    output_root=workspace / "adapted-failed-search",
                )

    def test_cli_preflight_has_zero_network_write_or_v1_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            capture = build_capture(workspace / "capture")
            before = {
                path.relative_to(workspace).as_posix(): path.read_bytes()
                for path in workspace.rglob("*")
                if path.is_file()
            }
            spec = importlib.util.spec_from_file_location("akbs_patch_submit_capture_test", SCRIPT)
            self.assertIsNotNone(spec)
            self.assertIsNotNone(spec.loader)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            output = io.StringIO()
            with (
                mock.patch.object(
                    module,
                    "installed_plugin_family_status",
                    return_value={"status": "PASS", "blocking": False},
                ),
                mock.patch.object(module, "incoming_main") as v1_main,
                mock.patch.object(module, "route_arguments") as v1_router,
                mock.patch.object(urllib.request, "urlopen") as urlopen,
                mock.patch.object(Path, "write_bytes", side_effect=AssertionError("unexpected write")),
                mock.patch.object(Path, "write_text", side_effect=AssertionError("unexpected write")),
                mock.patch.object(Path, "mkdir", side_effect=AssertionError("unexpected mkdir")),
                contextlib.redirect_stdout(output),
            ):
                result = module.main(["android-change-v2", "adapt-capture", str(capture)])
            self.assertEqual(result, 1)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["status"], "BLOCKED")
            self.assertFalse(payload["adapter"]["output_created"])
            v1_main.assert_not_called()
            v1_router.assert_not_called()
            urlopen.assert_not_called()
            after = {
                path.relative_to(workspace).as_posix(): path.read_bytes()
                for path in workspace.rglob("*")
                if path.is_file()
            }
            self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
