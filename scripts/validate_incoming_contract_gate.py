#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import textwrap
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable


sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = Path(__file__).resolve().parent
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

TARGET_PLUGIN_NAMES = ("akbs-member-ops", "android-engineering-ops")
RESIDUE_TABLES = (
    "packages",
    "intake_queue",
    "daily_reports",
    "weekly_reports",
    "patch_packages",
    "patch_package_aliases",
    "patch_package_sources",
    "patch_package_asset_memberships",
    "patch_package_revisions",
    "patch_package_events",
    "patch_package_case_links",
    "package_assets",
    "package_material_identity",
)

MANUAL_CONTRACT_MIGRATIONS = (
    ("024_retired_schema_artifacts.sql", ("PREPARE", "FINALIZE")),
    ("025_origin_aware_legacy_review.sql", ("FORWARD",)),
    ("026_patch_package_unification.sql", ("FORWARD",)),
    ("027_patch_package_history_governance.sql", ("FORWARD",)),
    ("028_patch_package_subject_migration.sql", ("FORWARD",)),
    ("029_patch_package_legacy_retirement.sql", ("FORWARD",)),
)


def run_json(command: list[str], cwd: Path, env: dict[str, str]) -> dict[str, Any]:
    result = subprocess.run(
        command,
        cwd=str(cwd),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        errors="replace",
        check=False,
    )
    if result.returncode:
        raise AssertionError(result.stderr or result.stdout)
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(result.stdout + result.stderr) from exc
    if not isinstance(payload, dict):
        raise AssertionError(f"command did not return a JSON object: {command}")
    return payload


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_fixture_schema(schema: dict[str, Any], manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for field in schema.get("required", []):
        if field not in manifest:
            errors.append(f"missing required field: {field}")
    properties = schema.get("properties", {})
    for field, rules in properties.items():
        if field not in manifest or not isinstance(rules, dict):
            continue
        value = manifest[field]
        if "const" in rules and value != rules["const"]:
            errors.append(f"{field} does not match const")
        if "enum" in rules and value not in rules["enum"]:
            errors.append(f"{field} is outside enum")
        if rules.get("type") == "string" and not isinstance(value, str):
            errors.append(f"{field} is not a string")
        if rules.get("type") == "object" and not isinstance(value, dict):
            errors.append(f"{field} is not an object")
        if isinstance(value, str) and int(rules.get("minLength", 0)) > len(value):
            errors.append(f"{field} is too short")
        if isinstance(value, str) and rules.get("pattern") and re.fullmatch(str(rules["pattern"]), value) is None:
            errors.append(f"{field} does not match pattern")
    if manifest.get("package_kind") == "android_change":
        then = schema.get("allOf", [{}])[0].get("then", {})
        for field in then.get("required", []):
            if field not in manifest:
                errors.append(f"android_change missing required field: {field}")
        status_const = then.get("properties", {}).get("package_status", {}).get("const")
        if status_const and manifest.get("package_status") != status_const:
            errors.append("android_change package_status does not match const")
    return errors


def _relative_contract_path(value: Any, *, label: str) -> Path:
    relative = Path(str(value or ""))
    if not relative.parts or relative.is_absolute() or ".." in relative.parts:
        raise AssertionError(f"invalid {label}: {value}")
    return relative


def _git_head(root: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode:
        raise AssertionError(f"cannot read system source commit: {result.stderr.strip()}")
    return result.stdout.strip()


def verify_public_contract(system_root: Path, suite_root: Path = REPO_ROOT) -> tuple[dict[str, Any], dict[str, Any]]:
    plugin_contract_root = suite_root / "contracts" / "incoming" / "v2"
    pin = load_json(plugin_contract_root / "contract-pin.json")
    if pin.get("schema_version") != "2" or pin.get("compatibility") != "strict-content-hash-equality":
        raise AssertionError("plugin compatibility pin must enforce strict incoming v2 content-hash equality")
    provenance = pin.get("source_provenance")
    if not isinstance(provenance, dict):
        raise AssertionError("plugin compatibility pin is missing source provenance")
    provenance_commit = str(provenance.get("commit") or "")
    if not re.fullmatch(r"[0-9a-f]{40}", provenance_commit) or provenance.get("compatibility_condition") is not False:
        raise AssertionError("source provenance must be an audit-only Git commit")
    observed_system_commit = _git_head(system_root)

    contract_set = pin.get("contract_set")
    if not isinstance(contract_set, dict):
        raise AssertionError("plugin compatibility pin is missing the AKBS contract set")
    system_contract_set = system_root / _relative_contract_path(
        contract_set.get("system_path"), label="system contract-set path"
    )
    consumer_contract_set = suite_root / _relative_contract_path(
        contract_set.get("consumer_path"), label="plugin contract-set path"
    )
    expected_contract_set_sha = str(contract_set.get("sha256") or "")
    if (
        not re.fullmatch(r"[0-9a-f]{64}", expected_contract_set_sha)
        or not system_contract_set.is_file()
        or not consumer_contract_set.is_file()
        or system_contract_set.read_bytes() != consumer_contract_set.read_bytes()
        or sha256(system_contract_set) != expected_contract_set_sha
    ):
        raise AssertionError("AKBS contract set drift")

    source = pin.get("public_contract")
    if not isinstance(source, dict):
        raise AssertionError("plugin compatibility pin is missing public_contract metadata")
    system_relative = _relative_contract_path(source.get("system_path"), label="system public contract path")
    consumer_relative = _relative_contract_path(source.get("consumer_path"), label="plugin consumer contract path")
    system_public_path = system_root / system_relative
    consumer_public_path = suite_root / consumer_relative
    expected_public_sha = str(source.get("sha256") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_public_sha):
        raise AssertionError("plugin compatibility pin has an invalid public contract SHA-256")
    if not system_public_path.is_file() or not consumer_public_path.is_file():
        raise AssertionError("system or plugin consumer public contract is missing")
    system_public = load_json(system_public_path)
    consumer_public = load_json(consumer_public_path)
    if system_public != consumer_public:
        raise AssertionError("plugin consumer public contract is not strictly equal to the system public contract")
    system_public_sha = sha256(system_public_path)
    consumer_public_sha = sha256(consumer_public_path)
    if system_public_sha != expected_public_sha or consumer_public_sha != expected_public_sha:
        raise AssertionError(
            f"public contract SHA drift: pin={expected_public_sha} plugin={consumer_public_sha} server={system_public_sha}"
        )

    evaluator_source = pin.get("verification_reference_evaluator")
    if not isinstance(evaluator_source, dict):
        raise AssertionError("plugin compatibility pin is missing the verification evaluator")
    evaluator_plugin_path = suite_root / _relative_contract_path(
        evaluator_source.get("source_path"),
        label="plugin verification evaluator path",
    )
    evaluator_system_path = system_root / _relative_contract_path(
        evaluator_source.get("server_path"),
        label="system verification evaluator path",
    )
    expected_evaluator_sha = str(evaluator_source.get("sha256") or "")
    if (
        not re.fullmatch(r"[0-9a-f]{64}", expected_evaluator_sha)
        or not evaluator_plugin_path.is_file()
        or not evaluator_system_path.is_file()
    ):
        raise AssertionError("verification evaluator pin is invalid")
    plugin_evaluator_sha = sha256(evaluator_plugin_path)
    system_evaluator_sha = sha256(evaluator_system_path)
    if (
        evaluator_plugin_path.read_bytes() != evaluator_system_path.read_bytes()
        or plugin_evaluator_sha != expected_evaluator_sha
        or system_evaluator_sha != expected_evaluator_sha
    ):
        raise AssertionError(
            "verification evaluator drift: "
            f"pin={expected_evaluator_sha} plugin={plugin_evaluator_sha} "
            f"server={system_evaluator_sha}"
        )

    error_source = pin.get("error_envelope")
    if not isinstance(error_source, dict):
        raise AssertionError("plugin compatibility pin is missing error envelope metadata")
    error_system_path = system_root / _relative_contract_path(
        error_source.get("system_path"), label="system error envelope path"
    )
    error_consumer_path = suite_root / _relative_contract_path(
        error_source.get("consumer_path"), label="plugin error envelope path"
    )
    expected_error_sha = str(error_source.get("sha256") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_error_sha):
        raise AssertionError("plugin compatibility pin has an invalid error envelope SHA-256")
    if not error_system_path.is_file() or not error_consumer_path.is_file():
        raise AssertionError("system or plugin error envelope contract is missing")
    if load_json(error_system_path) != load_json(error_consumer_path):
        raise AssertionError("plugin error envelope consumer is not strictly equal to the system schema")
    system_error_sha = sha256(error_system_path)
    consumer_error_sha = sha256(error_consumer_path)
    if system_error_sha != expected_error_sha or consumer_error_sha != expected_error_sha:
        raise AssertionError(
            f"error envelope SHA drift: pin={expected_error_sha} plugin={consumer_error_sha} server={system_error_sha}"
        )

    manifest_schema = system_public.get("manifest_schema")
    verification_acceptance = system_public.get("verification_acceptance")
    fixtures = system_public.get("golden_fixtures")
    if (
        not isinstance(manifest_schema, dict)
        or not isinstance(verification_acceptance, dict)
        or verification_acceptance.get("schema")
        != "akbs-verification-acceptance-contract-v2"
        or not isinstance(fixtures, dict)
        or set(fixtures) != {
        "daily",
        "weekly",
        "patch",
        }
    ):
        raise AssertionError("system public contract artifact declarations are incomplete")
    declared_artifacts = [
        manifest_schema,
        verification_acceptance,
        *fixtures.values(),
    ]
    public_artifacts: dict[str, str] = {}
    for declaration in declared_artifacts:
        if not isinstance(declaration, dict):
            raise AssertionError("system public contract artifact declaration is invalid")
        relative = _relative_contract_path(declaration.get("path"), label="public artifact path").as_posix()
        digest = str(declaration.get("sha256") or "")
        if not re.fullmatch(r"[0-9a-f]{64}", digest) or relative in public_artifacts:
            raise AssertionError("system public contract artifact declaration is invalid")
        public_artifacts[relative] = digest
    if pin.get("artifact_sha256") != public_artifacts:
        raise AssertionError("plugin artifact SHA pin is not exactly derived from the system public contract")
    system_contract_root = system_public_path.parent
    for relative, expected_digest in public_artifacts.items():
        plugin_path = plugin_contract_root / relative
        system_path = system_contract_root / relative
        if not plugin_path.is_file() or not system_path.is_file():
            raise AssertionError(f"missing public incoming artifact: {relative}")
        plugin_digest = sha256(plugin_path)
        system_digest = sha256(system_path)
        if plugin_digest != expected_digest or system_digest != expected_digest:
            raise AssertionError(
                f"incoming artifact drift: {relative}: pin={expected_digest} plugin={plugin_digest} server={system_digest}"
            )

    families = system_public.get("reason_code_families")
    success_codes = system_public.get("success_reason_codes")
    if not isinstance(families, dict) or not isinstance(success_codes, list):
        raise AssertionError("system public contract reason-code declarations are invalid")
    reason_codes = sorted(code for values in families.values() if isinstance(values, list) for code in values)
    if len(reason_codes) != len(set(reason_codes)) or pin.get("reason_codes") != reason_codes:
        raise AssertionError("plugin error reason-code pin is not exactly derived from the system public contract")
    if pin.get("success_reason_codes") != success_codes:
        raise AssertionError("plugin success reason-code pin is not exactly derived from the system public contract")

    patch_contract = system_public.get("patch_package_contract")
    if not isinstance(patch_contract, dict) or patch_contract.get("schema") != "akbs-patch-package-contract/v2":
        raise AssertionError("system public contract is missing patch package subject v2")
    patch_identity = patch_contract.get("business_identity")
    if not isinstance(patch_identity, dict) or {
        "identity_field": patch_identity.get("identity_field"),
        "canonical_identity": patch_identity.get("canonical_identity"),
        "queue_identity": patch_identity.get("queue_identity"),
        "main_branch_identity": patch_identity.get("main_branch_identity"),
        "source_identity_field": patch_identity.get("source_identity_field"),
        "source_identity_role": patch_identity.get("source_identity_role"),
    } != {
        "identity_field": "patch_package_id",
        "canonical_identity": "patch_packages.patch_package_id",
        "queue_identity": "patch_packages.patch_package_id",
        "main_branch_identity": "patch_packages.patch_package_id",
        "source_identity_field": "package_key",
        "source_identity_role": "source_only",
    }:
        raise AssertionError("patch package business and source identities drifted")
    queue = patch_contract.get("queue")
    curation = patch_contract.get("curation")
    if not isinstance(queue, dict) or queue.get("states") != [
        "received",
        "under_review",
        "information_required",
        "information_review",
        "closed",
    ] or queue.get("terminal_states") != ["closed"]:
        raise AssertionError("patch package queue stage contract drifted")
    if not isinstance(curation, dict) or curation.get("stages") != [
        "under_review",
        "pending_merge_confirmation",
        "dispute_open",
        "closed",
    ]:
        raise AssertionError("patch package main-branch stage contract drifted")

    schema_relative = _relative_contract_path(manifest_schema.get("path"), label="manifest schema path")
    schema = load_json(plugin_contract_root / schema_relative)
    for name, declaration in fixtures.items():
        manifest = load_json(plugin_contract_root / _relative_contract_path(declaration.get("path"), label=f"{name} fixture path"))
        errors = validate_fixture_schema(schema, manifest)
        if errors:
            raise AssertionError(f"{name} golden manifest failed schema: {errors[0]}")
    return (
        {
            "artifacts": len(public_artifacts),
            "fixtures": len(fixtures),
            "reason_codes": len(reason_codes),
            "contract_set_sha256": expected_contract_set_sha,
            "public_contract_sha256": system_public_sha,
            "verification_evaluator_sha256": system_evaluator_sha,
            "error_envelope_sha256": system_error_sha,
            "source_provenance_commit": provenance_commit,
            "observed_system_commit": observed_system_commit,
            "source_provenance_matches": observed_system_commit == provenance_commit,
        },
        system_public,
    )


def install_plugin_family(
    root: Path,
    codex_home: Path,
    plugin_names: tuple[str, ...],
    *,
    inventory_dir_name: str,
) -> dict[str, Path]:
    marketplace = "android-codex-suite"
    runtimes: dict[str, Path] = {}
    installed: list[dict[str, Any]] = []
    for plugin_name in plugin_names:
        checkout = REPO_ROOT / "plugins" / plugin_name
        source = (
            codex_home
            / ".tmp/marketplaces"
            / marketplace
            / "plugins"
            / plugin_name
        )
        source.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(
            checkout,
            source,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"),
        )
        manifest_path = source / ".codex-plugin/plugin.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        version = str(manifest.get("version") or "")
        if manifest.get("name") != plugin_name or not re.fullmatch(r"\d+\.\d+\.\d+", version):
            raise AssertionError(f"plugin manifest identity is invalid: {manifest_path}")
        runtime = (
            codex_home
            / "plugins/cache"
            / marketplace
            / plugin_name
            / version
        )
        runtime.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(
            source,
            runtime,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"),
        )
        if source.resolve() == runtime.resolve() or source.is_symlink() or runtime.is_symlink():
            raise AssertionError(f"plugin source/cache identity is not independent: {plugin_name}")
        runtimes[plugin_name] = runtime
        installed.append(
            {
                "pluginId": f"{plugin_name}@{marketplace}",
                "name": plugin_name,
                "marketplaceName": marketplace,
                "version": version,
                "installed": True,
                "enabled": True,
                "source": {"source": "local", "path": str(source)},
            }
        )

    inventory_bin = root / inventory_dir_name
    inventory_bin.mkdir()
    codex = inventory_bin / "codex"
    payload = {"installed": installed, "available": []}
    codex.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        f"payload = {payload!r}\n"
        "if sys.argv[1:] != ['plugin', 'list', '--json']:\n"
        "    raise SystemExit(64)\n"
        "print(json.dumps(payload, sort_keys=True))\n",
        encoding="utf-8",
    )
    codex.chmod(0o700)
    runtimes["inventory_bin"] = inventory_bin
    return runtimes


def write_config(root: Path) -> tuple[dict[str, str], dict[str, Path]]:
    codex_home = root / "codex-home"
    codex_home.mkdir(parents=True)
    (codex_home / "akbs-member-ops.toml").write_text(
        textwrap.dedent(
            f"""
            default_profile = "wick"
            incoming_schema_version = "2"

            [paths]
            out_dir = "{(codex_home / 'artifacts/akbs-member-ops').as_posix()}"

            [profiles.wick]
            member_alias = "wick"
            member_name = "刘杰钊"
            role = "member"
            allowed_modes = ["daily", "weekly", "patch"]
            knowledge_repo_worktree = "{(root / 'knowledge').as_posix()}"
            git_user_name = "刘杰钊"
            git_user_email = "wick@example.invalid"
            synthetic_data = true
            synthetic_item_count = "2"
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["CODEX_HOME"] = str(codex_home)
    env["CODEX_REPORT_SKIP_PLUGIN_UPDATE_CHECK"] = "1"
    runtimes = install_plugin_family(
        root,
        codex_home,
        TARGET_PLUGIN_NAMES,
        inventory_dir_name="target-active-plugin-inventory",
    )
    env["PATH"] = f"{runtimes['inventory_bin']}{os.pathsep}{env['PATH']}"
    return env, runtimes


def init_framework_source(root: Path) -> Path:
    source_root = root / "android-source"
    source = source_root / "frameworks/base/packages/SystemUI/src/com/android/systemui/volume/VolumeDialogImpl.java"
    source.parent.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(source_root)], check=True)
    subprocess.run(["git", "config", "user.email", "contract@example.invalid"], cwd=source_root, check=True)
    subprocess.run(["git", "config", "user.name", "Contract Gate"], cwd=source_root, check=True)
    source.write_text("class VolumeDialogImpl {}\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=source_root, check=True)
    subprocess.run(["git", "commit", "-m", "contract baseline"], cwd=source_root, check=True, stdout=subprocess.DEVNULL)
    source.write_text(
        "class VolumeDialogImpl {\n"
        "  //wick 20260711@{\n"
        "  static final String KEY = \"persist.sys.contract_gate\";\n"
        "  //wick 20260711@}\n"
        "}\n",
        encoding="utf-8",
    )
    return source_root


def build_legacy_framework_capture_fixture(
    env: dict[str, str], source_root: Path, patch_artifact: Path
) -> Path:
    """Build a current v2 capture fixture for the single Android change package path."""
    capture = (
        Path(env["CODEX_HOME"])
        / "artifacts/android-framework-patch-capture/packages"
        / "20260711-110000-patch"
    )
    patch_rel = "patches/mtk15-frameworks-base@incoming-contract-gate.patch"
    verification_rel = "evidence/verification-result.json"
    (capture / "patches").mkdir(parents=True)
    (capture / "evidence").mkdir()
    patch_bytes = patch_artifact.read_bytes()
    (capture / patch_rel).write_bytes(patch_bytes)
    (capture / "README.md").write_text(
        """# incoming-contract-gate

## 功能描述

验证 legacy Framework v1 补丁仍通过原有成员上传生命周期。

## 修改点

- 修改 SystemUI 测试属性，用于验证 v2 capture reader 和提交路由。

## 日志控制

无新增运行时日志。

## SystemProperties

`persist.sys.contract_gate`

## 字符串国际化

无新增字符串资源。

## 可回滚性

反向应用测试补丁即可回滚。
""",
        encoding="utf-8",
    )
    (capture / verification_rel).write_text(
        json.dumps(
            {
                "kind": "verification_result",
                "contract_version": "akbs-verification-evidence/v2",
                "scope": "feature",
                "requirement_acceptance": "accepted",
                "result": "PASS",
                "method": "device",
                "device": "TVE8402M",
                "build": ["SystemUI 编译通过"],
                "steps": ["incoming 合同验证通过"],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    manifest = {
        "package_type": "framework_feature_patch",
        "change_domain": "framework",
        "summary": "incoming v2 跨仓合同门禁",
        "feature": "incoming-contract-gate",
        "project": "TVE8402M",
        "platform_token": "mtk15",
        "platform": "mtk",
        "android_version": "15",
        "implementation_origin": "historical",
        "workflow_contract": "historical_import",
        "captured_by": "codex",
        "coding_standard_check": {
            "required": True,
            "mode": "capture_gate",
            "result": "PASS",
        },
        "readme": "README.md",
        "git_repositories": [
            {
                "root": str(source_root),
                "repo_path": ".",
                "git": {"branch": "main"},
            }
        ],
        "patches": [
            {
                "path": patch_rel,
                "repo_path": ".",
                "source_root": str(source_root),
                "content_sha1": hashlib.sha1(patch_bytes).hexdigest(),
                "status": "validated",
                "reuse_hint": True,
                "project": "TVE8402M",
                "platform_token": "mtk15",
                "platform": "mtk",
                "android_version": "15",
                "implementation_origin": "historical",
                "workflow_contract": "historical_import",
                "captured_by": "codex",
                "facts": {
                    "modified_files": [
                        "frameworks/base/packages/SystemUI/src/com/android/systemui/volume/VolumeDialogImpl.java"
                    ],
                    "modules": ["SystemUI"],
                    "system_properties": ["persist.sys.contract_gate"],
                },
            }
        ],
        "evidence": [
            {
                "id": "verification-result",
                "kind": "verification_result",
                "path": verification_rel,
                "result": "PASS",
                "summary": "legacy Framework v2 contract verification",
            }
        ],
    }
    (capture / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return capture


def generate_real_packages(
    root: Path,
    env: dict[str, str],
    runtimes: dict[str, Path],
) -> dict[str, Path]:
    intake_script = (
        runtimes["akbs-member-ops"]
        / "internal/incoming-v2/scripts/akbs_member_intake.py"
    )
    common = [sys.executable, str(intake_script), "--profile", "wick"]
    daily = run_json(common + ["daily", "--date", "2026-07-11", "--run-id", "20260711-090000-daily", "--prepare"], REPO_ROOT, env)
    weekly = run_json(common + ["weekly", "--date", "2026-07-11", "--run-id", "20260711-100000-weekly", "--prepare"], REPO_ROOT, env)
    source_root = init_framework_source(root)
    patch_artifact = root / "historical-framework-change.patch"
    patch_artifact.write_bytes(
        subprocess.run(
            ["git", "diff", "--binary", "--", "frameworks/base"],
            cwd=source_root,
            check=True,
            stdout=subprocess.PIPE,
        ).stdout
    )
    target_capture = build_legacy_framework_capture_fixture(
        env, source_root, patch_artifact
    )
    patch = run_json(
        common
        + [
            "patch",
            "--date",
            "2026-07-11",
            "--run-id",
            "20260711-120000-patch",
            "--patch-package",
            str(target_capture),
            "--summary",
            "incoming v2 跨仓合同门禁",
            "--status",
            "validated",
            "--prepare",
        ],
        REPO_ROOT,
        env,
    )
    return {name: Path(payload["package"]) for name, payload in (("daily", daily), ("weekly", weekly), ("patch", patch))}


def tar_bytes(package_dir: Path) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for path in sorted(package_dir.rglob("*")):
            archive.add(path, arcname=path.relative_to(package_dir).as_posix(), recursive=False)
    return buffer.getvalue()


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise AssertionError(f"expected JSON object: {path}")
    return payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def mutate_package(root: Path, source: Path, name: str, mutation: Callable[[Path, dict[str, Any]], None]) -> Path:
    target = root / "mutations" / name
    shutil.copytree(source, target)
    manifest_path = target / "manifest.json"
    manifest = load_json(manifest_path)
    mutation(target, manifest)
    write_json(manifest_path, manifest)
    return target


def runtime_snapshot(db_path: Path, data_root: Path) -> tuple[dict[str, int], tuple[str, ...]]:
    with sqlite3.connect(db_path) as conn:
        counts = {table: int(conn.execute(f"select count(*) from {table}").fetchone()[0]) for table in RESIDUE_TABLES}
    uploads = data_root / "uploads"
    paths = tuple(sorted(path.relative_to(uploads).as_posix() for path in uploads.rglob("*") if path.is_file())) if uploads.exists() else ()
    return counts, paths


def apply_isolated_current_contract_schema(system_root: Path, db_path: Path) -> None:
    """Advance a new validation-only database through the guarded current schema."""
    migration_root = system_root / "migration_plans"
    with sqlite3.connect(db_path) as conn:
        conn.execute("pragma foreign_keys = on")
        for filename, sections in MANUAL_CONTRACT_MIGRATIONS:
            source = (migration_root / filename).read_text(encoding="utf-8")
            for section in sections:
                begin = f"-- AKBS_{section}_BEGIN"
                end = f"-- AKBS_{section}_END"
                if source.count(begin) != 1 or source.count(end) != 1:
                    raise AssertionError(
                        f"manual contract migration markers are invalid: {filename}:{section}"
                    )
                conn.executescript(source.split(begin, 1)[1].split(end, 1)[0])
        conn.commit()


def expect_reject(client: Any, db_path: Path, data_root: Path, route: str, package: Path, code: str) -> None:
    before = runtime_snapshot(db_path, data_root)
    response = client.post(f"/akbs/api/member/me/uploads/{route}", content=tar_bytes(package))
    if response.status_code not in {400, 403, 409} or code not in str(response.json().get("detail", "")):
        raise AssertionError(f"{package.name}: expected {code} rejection, got {response.status_code}: {response.text}")
    after = runtime_snapshot(db_path, data_root)
    if after != before:
        raise AssertionError(f"rejected mutation left runtime residue: {code}")


class _PluginHttpResponse:
    def __init__(self, content: bytes):
        self.content = content

    def __enter__(self) -> _PluginHttpResponse:
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
        return False

    def read(self) -> bytes:
        return self.content


class _TestClientUrlopen:
    def __init__(self, client: Any):
        self.client = client

    def __call__(self, request: urllib.request.Request, timeout: int = 0) -> _PluginHttpResponse:
        del timeout
        path = urllib.parse.urlsplit(request.full_url).path
        headers = {key: value for key, value in request.header_items()}
        response = self.client.post(path, content=request.data or b"", headers=headers)
        if response.status_code >= 400:
            raise urllib.error.HTTPError(
                request.full_url,
                response.status_code,
                response.reason_phrase,
                response.headers,
                io.BytesIO(response.content),
            )
        return _PluginHttpResponse(response.content)


def load_plugin_submit(suite_root: Path) -> Any:
    plugin_root = suite_root / "plugins" / "akbs-member-ops"
    scripts_root = plugin_root / "internal" / "incoming-v2" / "scripts"
    plugin_lib = plugin_root / "lib"
    if not (scripts_root / "akbs_intake" / "submit.py").is_file():
        raise AssertionError(f"plugin HTTP client is missing: {scripts_root}")
    for path in (plugin_lib, scripts_root):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
    from akbs_intake import submit

    return submit


@contextlib.contextmanager
def plugin_test_client(plugin_submit: Any, client: Any):
    endpoint_key = "CODEX_REPORT_AKBS_ENDPOINT_SUBMISSION_API_BASE_URL"
    previous_endpoint = os.environ.get(endpoint_key)
    previous_urlopen = plugin_submit.urllib.request.urlopen
    os.environ[endpoint_key] = "http://testserver/akbs/api"
    plugin_submit.urllib.request.urlopen = _TestClientUrlopen(client)
    try:
        yield
    finally:
        plugin_submit.urllib.request.urlopen = previous_urlopen
        if previous_endpoint is None:
            os.environ.pop(endpoint_key, None)
        else:
            os.environ[endpoint_key] = previous_endpoint


def plugin_submit_package(plugin_submit: Any, client: Any, package: Path, member: str) -> dict[str, Any]:
    with plugin_test_client(plugin_submit, client):
        result = plugin_submit.server_submit_package(package, {"member_alias": member}, "http")
    if not isinstance(result, dict):
        raise AssertionError("plugin HTTP client did not return a JSON object")
    return result


def expect_plugin_conflict(
    plugin_submit: Any,
    client: Any,
    db_path: Path,
    data_root: Path,
    package: Path,
    member: str,
    reason_code: str,
    http_status: int,
) -> None:
    before = runtime_snapshot(db_path, data_root)
    try:
        plugin_submit_package(plugin_submit, client, package, member)
    except SystemExit as error:
        detail = str(error)
        if (
            f"HTTP {http_status}" not in detail
            or f"code={reason_code}" not in detail
            or re.search(r"request_id=req_[0-9a-f]{32}", detail) is None
            or "legacy_fallback=true" in detail
        ):
            raise AssertionError(f"plugin client did not preserve the declared conflict reason: {detail}") from error
    else:
        raise AssertionError("plugin client accepted a different-content duplicate identity")
    after = runtime_snapshot(db_path, data_root)
    if after != before:
        raise AssertionError("different-content duplicate identity left runtime residue")


def set_nested_json(package: Path, relative: str, updater: Callable[[dict[str, Any]], None]) -> None:
    path = package / relative
    payload = load_json(path)
    updater(payload)
    write_json(path, payload)


def exercise_server(
    system_root: Path,
    root: Path,
    packages: dict[str, Path],
    suite_root: Path,
    public_contract: dict[str, Any],
) -> dict[str, int]:
    sys.path.insert(0, str(system_root))
    from fastapi.testclient import TestClient
    from akbs_active.app import create_app
    from akbs_active.db import apply_migrations
    from akbs_active.upload_domains.archive_payload import ArchiveLimits
    from tests.auth_helpers import login_member

    db_path = root / "runtime" / "akbs.sqlite3"
    data_root = root / "runtime" / "data"
    db_path.parent.mkdir(parents=True)
    apply_migrations(db_path)
    apply_isolated_current_contract_schema(system_root, db_path)
    client = TestClient(create_app(db_path, data_root=data_root))
    login_member(client, "wick")
    plugin_submit = load_plugin_submit(suite_root)

    accepted = 0
    rejected = 0
    archive_errors = 0

    limited_db = root / "limited-runtime" / "akbs.sqlite3"
    limited_data = root / "limited-runtime" / "data"
    limited_db.parent.mkdir(parents=True)
    apply_migrations(limited_db)
    apply_isolated_current_contract_schema(system_root, limited_db)
    limited_client = TestClient(
        create_app(
            limited_db,
            data_root=limited_data,
            archive_limits=ArchiveLimits(max_compressed_bytes=1),
        )
    )
    login_member(limited_client, "wick")
    limited_before = runtime_snapshot(limited_db, limited_data)
    try:
        plugin_submit_package(plugin_submit, limited_client, packages["patch"], "wick")
    except SystemExit as error:
        detail = str(error)
        if (
            "HTTP 413" not in detail
            or "code=archive_compressed_bytes_exceeded" not in detail
            or "kind=resource_limit" not in detail
            or re.search(r"request_id=req_[0-9a-f]{32}", detail) is None
            or "legacy_fallback=true" in detail
        ):
            raise AssertionError(f"plugin client did not consume the archive resource envelope: {detail}") from error
    else:
        raise AssertionError("plugin client accepted an archive above the configured compressed-byte limit")
    if runtime_snapshot(limited_db, limited_data) != limited_before:
        raise AssertionError("archive resource rejection left runtime residue")
    rejected += 1
    archive_errors += 1
    for route in ("daily", "weekly"):
        result = plugin_submit_package(plugin_submit, client, packages[route], "wick")
        if not result.get("accepted"):
            raise AssertionError(f"real plugin {route} package was not accepted: {result}")
        accepted += 1

    patch = packages["patch"]
    mutation_cases: list[tuple[str, str, Callable[[Path, dict[str, Any]], None]]] = [
        ("required-field", "missing_required_field", lambda _p, m: m.pop("summary")),
        ("referenced-path", "missing_referenced_file", lambda p, m: (p / m["files"]["patches"][0]).unlink()),
        ("case-binding", "case_binding_mismatch", lambda p, m: set_nested_json(p, m["files"]["case"], lambda v: v.update(case_id="case-drift"))),
        ("variant-binding", "variant_binding_mismatch", lambda p, m: set_nested_json(p, m["files"]["variant"], lambda v: v.update(variant_id="variant-drift"))),
        ("project", "variant_trace_mismatch", lambda _p, m: m.update(project="TVE9999U")),
        ("platform", "variant_trace_mismatch", lambda _p, m: m.update(platform="rk")),
        ("android", "variant_trace_mismatch", lambda _p, m: m.update(android_version="14")),
        ("patch-asset", "invalid_patch_asset", lambda p, m: (p / m["files"]["patches"][0]).write_text("not a git diff\n", encoding="utf-8")),
        (
            "verification",
            "verification_not_pass",
            lambda p, m: set_nested_json(
                p,
                next(path for path in m["files"]["evidence"] if path.endswith("verification_result.json")),
                lambda v: v.setdefault("payload", {}).update(result="FAIL"),
            ),
        ),
    ]
    for name, code, mutation in mutation_cases:
        candidate = mutate_package(root, patch, name, mutation)
        expect_reject(client, db_path, data_root, "patch", candidate, code)
        rejected += 1
    for status in ("candidate", "draft", "failed", "blocked", "missing", "unknown"):
        def status_mutation(_p: Path, manifest: dict[str, Any], value: str = status) -> None:
            if value == "missing":
                manifest.pop("package_status", None)
            else:
                manifest["package_status"] = value

        code = "missing_required_field" if status == "missing" else "package_status_not_validated"
        candidate = mutate_package(root, patch, f"status-{status}", status_mutation)
        expect_reject(client, db_path, data_root, "patch", candidate, code)
        rejected += 1

    patch_result = plugin_submit_package(plugin_submit, client, patch, "wick")
    if not patch_result.get("accepted"):
        raise AssertionError(f"real plugin patch package was not accepted: {patch_result}")
    accepted += 1

    duplicate_contract = public_contract.get("duplicate_package_identity")
    if not isinstance(duplicate_contract, dict):
        raise AssertionError("public contract is missing duplicate identity semantics")
    replay_contract = duplicate_contract.get("same_file_tree_sha256")
    conflict_contract = duplicate_contract.get("different_file_tree_sha256")
    if not isinstance(replay_contract, dict) or not isinstance(conflict_contract, dict):
        raise AssertionError("public contract duplicate identity branches are invalid")
    before_replay = runtime_snapshot(db_path, data_root)
    replay_result = plugin_submit_package(plugin_submit, client, patch, "wick")
    after_replay = runtime_snapshot(db_path, data_root)
    if int(replay_contract.get("http_status", -1)) != 200 or replay_contract.get("outcome") != "idempotent_replay":
        raise AssertionError("public contract same-tree duplicate semantics drifted")
    if after_replay != before_replay:
        raise AssertionError("same-tree duplicate replay created a new runtime fact")
    first_hash = patch_result.get("agent_context", {}).get("content_hash")
    replay_hash = replay_result.get("agent_context", {}).get("content_hash")
    if not first_hash or replay_hash != first_hash:
        raise AssertionError("same-tree duplicate replay did not preserve plugin/server content identity")
    accepted += 1

    conflict = mutate_package(
        root,
        patch,
        "duplicate-content-conflict",
        lambda _p, manifest: manifest.update(summary="same identity with a different file tree"),
    )
    expect_plugin_conflict(
        plugin_submit,
        client,
        db_path,
        data_root,
        conflict,
        "wick",
        str(conflict_contract.get("reason_code") or ""),
        int(conflict_contract.get("http_status", -1)),
    )
    if conflict_contract.get("outcome") != "reject_conflict":
        raise AssertionError("public contract different-tree duplicate semantics drifted")
    rejected += 1

    login_member(client, "jared")
    jared_patch = mutate_package(root, patch, "jared-patch", lambda p, m: retarget_member(p, m, "jared", "20260711-120001-patch"))
    jared_result = plugin_submit_package(plugin_submit, client, jared_patch, "jared")
    if not jared_result.get("accepted"):
        raise AssertionError(f"cross-member setup patch rejected: {jared_result}")
    accepted += 1
    return {"accepted": accepted, "rejected": rejected, "archive_errors": archive_errors}


def exercise_remote_server(packages: dict[str, Path], host: str, runtime_root: str, python_path: str) -> dict[str, int]:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        archive.add(Path(__file__), arcname="gate.py")
        archive.add(REPO_ROOT / "contracts", arcname="suite/contracts")
        archive.add(REPO_ROOT / "plugins" / "akbs-member-ops", arcname="suite/plugins/akbs-member-ops")
        archive.add(
            REPO_ROOT / "plugins" / "android-engineering-ops",
            arcname="suite/plugins/android-engineering-ops",
        )
        for name, package in sorted(packages.items()):
            archive.add(package, arcname=f"packages/{name}")
    command = (
        "set -euo pipefail; "
        f"AKBS_SYSTEM={shlex.quote(runtime_root)}; export AKBS_SYSTEM; "
        f"source {shlex.quote(runtime_root + '/scripts/lib/controlled-validation-output.sh')}; "
        "akbs_validation_output_init; "
        "tmp=\"$AKBS_VALIDATION_OUTPUT_ROOT/harness\"; mkdir -p -- \"$tmp\"; "
        "tar -xzf - -C \"$tmp\"; "
        f"PYTHONDONTWRITEBYTECODE=1 PYTHONPATH={shlex.quote(python_path)} python3 \"$tmp/gate.py\" "
        f"--system-root {shlex.quote(runtime_root)} --server-packages-root \"$tmp/packages\" "
        "--plugin-suite-root \"$tmp/suite\""
    )
    result = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", host, "bash", "-c", shlex.quote(command)],
        input=buffer.getvalue(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode:
        stdout = result.stdout.decode("utf-8", errors="replace")
        stderr = result.stderr.decode("utf-8", errors="replace")
        raise AssertionError(
            f"remote server contract harness exited {result.returncode}\n"
            f"stdout:\n{stdout}\nstderr:\n{stderr}"
        )
    try:
        payload = json.loads(result.stdout.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise AssertionError(result.stdout.decode("utf-8", errors="replace") + result.stderr.decode("utf-8", errors="replace")) from exc
    if payload.get("status") != "PASS":
        raise AssertionError(f"remote server contract harness failed: {payload}")
    return {
        "accepted": int(payload["accepted"]),
        "rejected": int(payload["rejected"]),
        "archive_errors": int(payload["archive_errors"]),
    }


def retarget_member(package: Path, manifest: dict[str, Any], member: str, run_id: str) -> None:
    manifest["member_alias"] = member
    manifest["member_name"] = member
    manifest["run_id"] = run_id
    display = manifest["files"]["display"][0]
    set_nested_json(package, display, lambda value: value.setdefault("payload", {}).update(member_alias=member, member_name=member))


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the stable incoming v2 contract.")
    parser.add_argument(
        "--mode",
        choices=("client-only", "remote-pilot"),
        default="client-only",
        help="Local v1 checks are the default; remote-pilot checks the current v2 server contract.",
    )
    parser.add_argument("--system-root", type=Path, help="Read-only AKBS system repository root")
    parser.add_argument("--server-host", default="test35", help="SSH host providing the authoritative system Python runtime")
    parser.add_argument("--server-runtime-root", default="/home/test35/akbs/system", help="System repository path on the server host")
    parser.add_argument(
        "--server-python-path",
        default="/home/test35/akbs/system:/home/test35/akbs/runtime/python-vendor",
        help="Authoritative server PYTHONPATH used by the temporary harness",
    )
    parser.add_argument("--server-packages-root", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--plugin-suite-root", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if (REPO_ROOT / ".git").exists():
        from validator_hygiene import repository_cleanup

        cleanup = repository_cleanup(REPO_ROOT)
    else:
        cleanup = contextlib.nullcontext()
    with cleanup:
        if args.server_packages_root:
            if args.system_root is None:
                raise SystemExit("--system-root is required with --server-packages-root")
            system_root = args.system_root.resolve()
            if not (system_root / "akbs_active" / "app.py").is_file():
                raise SystemExit(f"invalid system root: {system_root}")
            package_root = args.server_packages_root.resolve()
            if args.plugin_suite_root is None:
                raise SystemExit("--plugin-suite-root is required with --server-packages-root")
            suite_root = args.plugin_suite_root.resolve()
            packages = {name: package_root / name for name in ("daily", "weekly", "patch")}
            public, public_contract = verify_public_contract(system_root, suite_root)
            runtime = exercise_server(system_root, package_root.parent, packages, suite_root, public_contract)
            print(json.dumps({"status": "PASS", **public, **runtime}, ensure_ascii=False, sort_keys=True))
            return 0
        with tempfile.TemporaryDirectory(prefix="akbs-contract-gate-") as temporary:
            root = Path(temporary)
            env, runtimes = write_config(root)
            packages = generate_real_packages(root, env, runtimes)
            if args.mode == "client-only":
                print(
                    json.dumps(
                        {
                            "status": "PASS",
                            "contract": "incoming-v2-client",
                            "execution": "local-fixture-only",
                            "packages": sorted(packages),
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                )
                return 0
            if args.system_root is None:
                raise SystemExit("--system-root is required for --mode remote-pilot")
            system_root = args.system_root.resolve()
            if not (system_root / "akbs_active" / "app.py").is_file():
                raise SystemExit(f"invalid system root: {system_root}")
            public, _ = verify_public_contract(system_root)
            runtime = exercise_remote_server(packages, args.server_host, args.server_runtime_root, args.server_python_path)
        print(json.dumps({"status": "PASS", "contract": "incoming-v2", **public, **runtime}, ensure_ascii=False, sort_keys=True))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
