#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


PLUGIN_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_ROOT = Path(__file__).resolve().parent
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))
OPS_PLUGIN_LIB = Path(__file__).resolve().parents[3] / "lib"
if OPS_PLUGIN_LIB.is_dir() and str(OPS_PLUGIN_LIB) not in sys.path:
    sys.path.insert(0, str(OPS_PLUGIN_LIB))

from akbs_member_ops.knowledge_rules import (
    aggregate_package_scope_errors,
    apply_platform_overrides,
    find_company_project,
    find_platform_tokens,
    has_uncontrolled_patch_asset_prefix,
    is_valid_android_version_value,
    is_valid_platform_value,
    normalize_android_version,
    parse_known_platform_token,
    parse_platform_token,
    parse_version_only_token,
    patch_upload_gate_errors,
    split_company_project,
    template_leak_errors,
    text_field_quality_errors,
)
PLUGIN_UPDATE_SKIP_ENV = "CODEX_REPORT_SKIP_PLUGIN_UPDATE_CHECK"
PLUGIN_UPDATE_REQUIRE_ENV = "CODEX_REPORT_REQUIRE_PLUGIN_UPDATE_CHECK"
PLUGIN_REEXEC_ATTEMPT_ENV = "CODEX_REPORT_PLUGIN_REEXEC_ATTEMPTED"
PLUGIN_REMOTE_MANIFEST_TIMEOUT = 6
PACKAGE_TYPES = {"daily", "weekly", "patch"}
INCOMING_KINDS = {"daily_trace", "weekly_trace", "framework_change"}
PACKAGE_STATUS_VALUES = {"validated", "candidate", "draft", "failed", "blocked"}
TRACE_REQUIRED_EVIDENCE_KINDS = {"source", "work_findings"}
FRAMEWORK_REQUIRED_EVIDENCE_KINDS = {
    "source",
    "patch_diff_facts",
    "patch_ai_facts",
    "project_inference",
    "patch_problem_summary",
    "risk_surface",
    "verification_result",
    "search_before_change",
}
FRAMEWORK_OPTIONAL_EVIDENCE_KINDS = {"build_result", "deploy_result", "device_health"}
DATE_KEY_RE = re.compile(r"^\d{8}$")
DATE_DISPLAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
RUN_ID_RE = re.compile(r"^\d{8}-\d{6}(-[A-Za-z0-9_.-]+)?$")


LAST_PLUGIN_VERSION_GATE: dict[str, Any] | None = None


from akbs_intake import version_gate as _version_gate  # noqa: E402
from akbs_intake.version_gate import (  # noqa: E402
    LAST_PLUGIN_VERSION_GATE,
    auto_update_packaged_plugin,
    compare_versions,
    current_skill_cache_metadata,
    env_enabled,
    fetch_remote_plugin_manifest,
    github_raw_plugin_manifest_url,
    latest_installed_plugin_cache_metadata,
    packaged_plugin_freshness,
    plugin_freshness_check,
    plugin_install_metadata,
    plugin_manifest_path,
    plugin_update_unknown,
    plugin_version_gate_check,
    reexec_latest_plugin_script_after_update,
    run,
    updated_plugin_intake_script_path,
    version_parts,
)

_ORIGINAL_VERSION_GATE_RUN = getattr(_version_gate, "_AKBS_ORIGINAL_RUN", _version_gate.run)
_ORIGINAL_VERSION_GATE_FETCH_REMOTE_PLUGIN_MANIFEST = getattr(
    _version_gate,
    "_AKBS_ORIGINAL_FETCH_REMOTE_PLUGIN_MANIFEST",
    _version_gate.fetch_remote_plugin_manifest,
)
_version_gate._AKBS_ORIGINAL_RUN = _ORIGINAL_VERSION_GATE_RUN
_version_gate._AKBS_ORIGINAL_FETCH_REMOTE_PLUGIN_MANIFEST = _ORIGINAL_VERSION_GATE_FETCH_REMOTE_PLUGIN_MANIFEST
run = _ORIGINAL_VERSION_GATE_RUN
fetch_remote_plugin_manifest = _ORIGINAL_VERSION_GATE_FETCH_REMOTE_PLUGIN_MANIFEST


def _call_version_gate(callback):
    original_root = _version_gate.PLUGIN_ROOT
    original_run = _version_gate.run
    original_fetch_remote = _version_gate.fetch_remote_plugin_manifest
    _version_gate.PLUGIN_ROOT = PLUGIN_ROOT
    _version_gate.run = run
    _version_gate.fetch_remote_plugin_manifest = fetch_remote_plugin_manifest
    try:
        return callback()
    finally:
        _version_gate.PLUGIN_ROOT = original_root
        _version_gate.run = original_run
        _version_gate.fetch_remote_plugin_manifest = original_fetch_remote


def plugin_install_metadata() -> dict[str, str]:
    return _call_version_gate(_version_gate.plugin_install_metadata)


def current_skill_cache_metadata() -> dict[str, str]:
    return _call_version_gate(_version_gate.current_skill_cache_metadata)


def installed_plugin_family_status() -> dict[str, Any]:
    """Read only the active install family; never enter freshness/update logic."""
    return _call_version_gate(_version_gate.installed_plugin_family_status)


def latest_installed_plugin_cache_metadata(plugin_name: str = "akbs-member-ops") -> dict[str, str]:
    return _call_version_gate(lambda: _version_gate.latest_installed_plugin_cache_metadata(plugin_name))


def plugin_freshness_check(fetch: bool = True, require: bool = False) -> dict[str, Any]:
    return _call_version_gate(lambda: _version_gate.plugin_freshness_check(fetch=fetch, require=require))


def plugin_version_gate_check(config: dict[str, str] | None = None, fetch: bool = True, require: bool = True) -> dict[str, Any]:
    global LAST_PLUGIN_VERSION_GATE
    gate = _call_version_gate(lambda: _version_gate.plugin_version_gate_check(config=config, fetch=fetch, require=require))
    LAST_PLUGIN_VERSION_GATE = gate
    return gate


from akbs_intake.config import (  # noqa: E402
    AKBS_ENDPOINT_DEFAULTS,
    AKBS_ENDPOINT_ENV_PREFIXES,
    CONFIG_DEFAULTS,
    DEFAULT_SUBMISSION_API_BASE_URL,
    ENV_PREFIXES,
    INCOMING_SCHEMA_VERSION,
    akbs_endpoint_env_value,
    allowed_modes,
    apply_env_overrides,
    artifact_path_guard_error,
    default_codex_home,
    enforce_mode_allowed,
    expanded_path,
    find_project_report_config,
    flatten_config_payload,
    knowledge_repo_worktree,
    load_config,
    local_now,
    parse_date_arg,
    parse_simple_toml,
    parse_toml_scalar,
    profile_configs,
    read_toml,
    require_config,
    require_safe_artifact_path,
    resolve_akbs_endpoint,
    stringify_config_value,
    submission_api_base_url,
    synthetic_mode,
)
from akbs_intake.report_sessions import (  # noqa: E402
    SessionWork,
    parse_sessions as _parse_sessions,
    report_customer_for_project,
    synthetic_sessions,
    week_bounds,
    ymd,
)
from akbs_intake.session_privacy import (  # noqa: E402
    ALLOWED_SESSION_FIELDS,
    configure_report_session_consent,
    require_report_session_consent,
)
from akbs_intake.io_utils import (  # noqa: E402
    MATERIALS_DIR,
    list_string_values,
    materials_rel,
    read_referenced_json,
    reference_path,
    read_json_file,
    read_optional_json_object,
    safe_id,
    unique_strings,
    write_json,
)
from akbs_intake.patch.assets import (  # noqa: E402
    PatchInfo,
    patch_readme_template,
    patch_readme_usable_for_inference,
    validate_patch_file,
    validate_patch_readme,
)
from akbs_intake.patch.builder import build_patch_package, infer_project  # noqa: E402
from akbs_intake.patch.evidence import (  # noqa: E402
    select_search_before_change_payload,
    verification_payload_or_missing,
)
from akbs_intake.patch.facts import patch_facts_from_text, patch_modules_from_files, patch_problem_and_risk_payloads  # noqa: E402
from akbs_intake.patch.metadata import (  # noqa: E402
    evidence_text_values,
    infer_platform_metadata,
)
from akbs_intake.patch.information_completion import (  # noqa: E402
    complete_information_request,
    inspect_information_request,
)
from akbs_intake.doctor import (  # noqa: E402
    doctor as _intake_doctor,
    doctor_strict_checks as _intake_doctor_strict_checks,
    latest_pending as _intake_latest_pending,
    nearest_existing_parent,
)


def parse_sessions(config: dict[str, str], dates: set[dt.date]) -> list[SessionWork]:
    return _parse_sessions(config, dates)


def discover_patches(config: dict[str, str], sessions: list[SessionWork], start: dt.date, end: dt.date) -> list[PatchInfo]:
    return _report_discover_patches(
        config,
        sessions,
        start,
        end,
        patch_info_factory=lambda path, name, project: PatchInfo(path=path, name=name, project=project),
    )


from akbs_intake import validation as _validation  # noqa: E402


def validate_package(package_dir: Path) -> dict[str, Any]:
    return _validation.validate_package(
        package_dir,
        incoming_schema_version=INCOMING_SCHEMA_VERSION,
        validate_incoming_package_fn=validate_incoming_package,
    )


from akbs_intake.search_usage import (  # noqa: E402
    workflow_contract_requires_pre_change_search,
    patch_search_feature_tokens,
    search_payload_has_member_decision,
    search_payload_missing_required_pre_change_search,
    search_payload_needs_closed_decision,
    search_usage_payload,
)


from akbs_intake import source_metadata as _source_metadata  # noqa: E402
from akbs_intake.incoming_contract import legacy_patch_contract_error  # noqa: E402


def source_metadata(config: dict[str, str], skill: str) -> dict[str, Any]:
    return _source_metadata.source_metadata(
        config,
        skill,
        plugin_root=PLUGIN_ROOT,
        run_command=run,
        plugin_install_metadata_fn=plugin_install_metadata,
        plugin_version_gate_check_fn=lambda gate_config, fetch, require: plugin_version_gate_check(
            gate_config,
            fetch=fetch,
            require=require,
        ),
        last_plugin_version_gate=LAST_PLUGIN_VERSION_GATE,
    )


def write_package_source(package_dir: Path, config: dict[str, str], skill: str) -> dict[str, Any]:
    source = source_metadata(config, skill)
    write_json(package_dir / materials_rel("evidence", "source.json"), {"kind": "source", "payload": source})
    return source



from akbs_intake.reports.common import (  # noqa: E402
    ensure_report_date_allowed,
    ensure_report_not_duplicate,
    format_report_duplicate_message,
    iter_local_manifests,
    local_report_packages,
    package_key_from_manifest,
    replacement_run_id,
    report_dates,
    report_duplicate_label,
    report_identity,
    report_identity_from_manifest,
    report_replace_option,
    report_type_from_manifest,
)
from akbs_intake.reports.identity import (  # noqa: E402
    infer_report_project,
    related_report_project_clues,
    same_day_daily_report_run_ids,
)
from akbs_intake.reports.builder import build_report_package  # noqa: E402
from akbs_intake.reports.render import (  # noqa: E402
    project_ledger_rows,
    write_report,
    write_report_view,
)
from akbs_intake.reports.session_summary import (  # noqa: E402
    discover_patches as _report_discover_patches,
    items_by_project,
    overview_text,
    work_findings_payload,
)
from akbs_intake.reports.validation import validate_report_trace_package  # noqa: E402


def validate_incoming_package(package_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []

    def require_file(rel: Any, label: str) -> Path | None:
        if not isinstance(rel, str) or not rel:
            errors.append(f"{label} path 必须提供")
            return None
        try:
            path = reference_path(package_dir, rel)
        except ValueError as exc:
            errors.append(str(exc))
            return None
        if not path.is_file():
            errors.append(f"{label} 文件不存在: {rel}")
            return None
        return path

    def load_evidence(paths: list[Any]) -> dict[str, dict[str, Any]]:
        by_kind: dict[str, dict[str, Any]] = {}
        for rel in paths:
            path = require_file(rel, "evidence")
            if not path:
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                errors.append(f"{rel} 解析失败: {exc}")
                continue
            if not isinstance(payload, dict):
                errors.append(f"{rel} evidence 必须是对象")
                continue
            kind = payload.get("kind")
            if not kind:
                errors.append(f"{rel} evidence.kind 必须提供")
                continue
            by_kind[str(kind)] = payload
        return by_kind

    required = {
        "schema",
        "schema_version",
        "package_kind",
        "member_alias",
        "member_name",
        "date",
        "run_id",
        "tool",
        "summary",
    }
    for field in sorted(required - set(manifest)):
        errors.append(f"manifest 缺少必填字段: {field}")
    if manifest.get("schema") != "knowledge-incoming-package":
        errors.append("schema 必须是 knowledge-incoming-package")
    if manifest.get("schema_version") != INCOMING_SCHEMA_VERSION:
        errors.append(f"schema_version 必须是 {INCOMING_SCHEMA_VERSION}")
    if not DATE_DISPLAY_RE.fullmatch(str(manifest.get("date") or "")):
        errors.append("manifest.date 必须是 YYYY-MM-DD")
    if not RUN_ID_RE.fullmatch(str(manifest.get("run_id") or "")):
        errors.append("manifest.run_id 必须是 YYYYMMDD-HHMMSS 或 YYYYMMDD-HHMMSS-suffix")
    errors.extend(
        text_field_quality_errors(
            {
                "manifest.summary": manifest.get("summary"),
            }
        )
    )
    retired_patch_error = legacy_patch_contract_error(manifest)
    if retired_patch_error:
        errors.append(retired_patch_error)
    package_kind = manifest.get("package_kind")
    if package_kind not in INCOMING_KINDS:
        errors.append(f"package_kind 非法: {package_kind}")

    if package_kind in {"daily_trace", "weekly_trace"}:
        validate_report_trace_package(
            package_dir=package_dir,
            manifest=manifest,
            trace_required_evidence_kinds=TRACE_REQUIRED_EVIDENCE_KINDS,
            package_status_values=PACKAGE_STATUS_VALUES,
            require_file=require_file,
            load_evidence=load_evidence,
            read_referenced_json=read_referenced_json,
            errors=errors,
        )

    if package_kind == "framework_change":
        patch_context = validate_framework_change_manifest_and_files(
            package_dir=package_dir,
            manifest=manifest,
            package_status_values=PACKAGE_STATUS_VALUES,
            run_id_re=RUN_ID_RE,
            require_file=require_file,
            validate_patch_readme=validate_patch_readme,
            has_uncontrolled_patch_asset_prefix=has_uncontrolled_patch_asset_prefix,
            is_valid_platform_value=is_valid_platform_value,
            is_valid_android_version_value=is_valid_android_version_value,
            errors=errors,
        )
        manifest_platform = patch_context.manifest_platform
        manifest_android_version = patch_context.manifest_android_version
        package_status = patch_context.package_status
        case_path = patch_context.case_path
        variant_path = patch_context.variant_path
        readme_path = patch_context.readme_path
        patch_paths = patch_context.patch_paths
        display_paths = patch_context.display_paths
        evidence_paths = patch_context.evidence_paths
        validate_patch_display_files(
            package_dir=package_dir,
            display_paths=display_paths,
            manifest=manifest,
            require_file=require_file,
            read_referenced_json=read_referenced_json,
            errors=errors,
        )
        structure_context = validate_framework_change_structure(
            package_dir=package_dir,
            manifest=manifest,
            package_status=package_status,
            case_path=case_path,
            variant_path=variant_path,
            evidence_paths=evidence_paths,
            load_evidence=load_evidence,
            read_json_file=read_json_file,
            read_referenced_json=read_referenced_json,
            text_field_quality_errors=text_field_quality_errors,
            is_valid_platform_value=is_valid_platform_value,
            is_valid_android_version_value=is_valid_android_version_value,
            framework_required_evidence_kinds=FRAMEWORK_REQUIRED_EVIDENCE_KINDS,
            errors=errors,
        )
        case_problem = structure_context.case_problem
        case_solution = structure_context.case_solution
        evidence_by_kind = structure_context.evidence_by_kind
        ai_context = validate_patch_ai_facts_and_diff(
            evidence_by_kind=evidence_by_kind,
            list_string_values=list_string_values,
            unique_strings=unique_strings,
            errors=errors,
        )
        modified_files = ai_context.modified_files
        validate_patch_template_leaks(
            package_dir=package_dir,
            manifest=manifest,
            evidence_paths=evidence_paths,
            case_problem=case_problem,
            case_solution=case_solution,
            patch_paths=patch_paths,
            modified_files=modified_files,
            read_referenced_json=read_referenced_json,
            template_leak_errors=template_leak_errors,
            errors=errors,
        )
        errors.extend(
            validate_framework_function_scope(
                package_dir=package_dir,
                manifest=manifest,
                readme_path=readme_path,
                patch_paths=patch_paths,
                evidence_by_kind=evidence_by_kind,
                list_string_values=list_string_values,
                aggregate_package_scope_errors=aggregate_package_scope_errors,
            )
        )
        validate_patch_verification_result(
            evidence_by_kind=evidence_by_kind,
            package_status=package_status,
            errors=errors,
        )
        validate_patch_pre_change_search(
            manifest=manifest,
            evidence_by_kind=evidence_by_kind,
            package_status=package_status,
            workflow_contract_requires_pre_change_search=workflow_contract_requires_pre_change_search,
            search_payload_missing_required_pre_change_search=search_payload_missing_required_pre_change_search,
            search_payload_needs_closed_decision=search_payload_needs_closed_decision,
            errors=errors,
            warnings=warnings,
        )
    return {"status": "FAIL" if errors else "PASS", "errors": errors, "warnings": warnings}


from akbs_intake.patch.validation import (  # noqa: E402
    validate_framework_change_manifest_and_files,
    validate_framework_change_structure,
    validate_framework_function_scope,
    validate_patch_display_files,
    validate_patch_ai_facts_and_diff,
    validate_patch_template_leaks,
    validate_patch_verification_result,
    validate_patch_pre_change_search,
)


def prepare_package(
    report_type: str,
    date: dt.date,
    config: dict[str, str],
    run_id: str | None = None,
    schema_version: str = INCOMING_SCHEMA_VERSION,
    replace_report_run_id: str = "",
    daily_facts_path: str = "",
    weekly_facts_path: str = "",
) -> Path:
    return build_report_package(
        report_type,
        date,
        config,
        run_id=run_id,
        schema_version=schema_version,
        replace_report_run_id=replace_report_run_id,
        daily_facts_path=daily_facts_path,
        weekly_facts_path=weekly_facts_path,
        incoming_schema_version=INCOMING_SCHEMA_VERSION,
        validate_package_fn=validate_package,
        write_package_source_fn=write_package_source,
        parse_sessions_fn=parse_sessions,
        discover_patches_fn=discover_patches,
    )


def prepare_patch_package(
    date: dt.date,
    config: dict[str, str],
    run_id: str | None = None,
    patch_paths: list[str] | None = None,
    patch_package_paths: list[str] | None = None,
    project: str = "unknown",
    summary: str = "管理员手动归档补丁",
    status: str = "validated",
    schema_version: str = INCOMING_SCHEMA_VERSION,
    related_report_run_ids: list[str] | None = None,
    platform_override: str = "",
    android_version_override: str = "",
    workflow_contract: str = "",
) -> Path:
    return build_patch_package(
        date,
        config,
        run_id=run_id,
        patch_paths=patch_paths,
        patch_package_paths=patch_package_paths,
        project=project,
        summary=summary,
        status=status,
        schema_version=schema_version,
        related_report_run_ids=related_report_run_ids,
        platform_override=platform_override,
        android_version_override=android_version_override,
        workflow_contract_override=workflow_contract,
        incoming_schema_version=INCOMING_SCHEMA_VERSION,
        framework_optional_evidence_kinds=FRAMEWORK_OPTIONAL_EVIDENCE_KINDS,
        validate_package_fn=validate_package,
        write_package_source_fn=write_package_source,
        plugin_install_metadata_fn=plugin_install_metadata,
    )


def submit_package(package_dir: Path, config: dict[str, str]) -> dict[str, Any]:
    return _submit_package(
        package_dir,
        config,
        validate_package_fn=validate_package,
        write_json_fn=write_json,
        patch_upload_gate_errors_fn=patch_upload_gate_errors,
    )


from akbs_intake.submit import (  # noqa: E402
    http_submit_package,
    package_tar_gz_bytes,
    server_submit_package,
    submit_package as _submit_package,
    upload_type_for_manifest,
)


def latest_pending(report_type: str, config: dict[str, str], date: dt.date | None = None) -> Path:
    return _intake_latest_pending(report_type, config, date)


def doctor_strict_checks(
    config: dict[str, str],
    loaded: list[Path],
    check_remote: bool,
    allow_synthetic: bool,
) -> dict[str, Any]:
    return _intake_doctor_strict_checks(
        config,
        loaded,
        check_remote,
        allow_synthetic,
        run_command=run,
        plugin_gate_check=plugin_version_gate_check,
    )


def doctor(
    config: dict[str, str],
    loaded: list[Path],
    strict: bool = False,
    check_remote: bool = False,
    allow_synthetic: bool = False,
) -> dict[str, Any]:
    return _intake_doctor(
        config,
        loaded,
        strict,
        check_remote,
        allow_synthetic,
        plugin_root=PLUGIN_ROOT,
        run_command=run,
        plugin_gate_check=plugin_version_gate_check,
    )

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate and submit Codex team knowledge incoming packages.")
    parser.add_argument("--profile", help="profile name from config, for example admin_alias or member_alias")
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor_parser = subparsers.add_parser("doctor")
    doctor_parser.add_argument("--strict", action="store_true", help="fail when the selected profile is unsafe for member-side automation")
    doctor_parser.add_argument("--check-remote", action="store_true", help="also verify plugin freshness and server-authenticated member identity")
    doctor_parser.add_argument("--allow-synthetic", action="store_true", help="allow synthetic_data=true for protocol or gray-flow testing")
    doctor_parser.set_defaults(report_type="")

    for report_type in ("daily", "weekly", "patch"):
        sub = subparsers.add_parser(report_type)
        sub.set_defaults(report_type=report_type)
        sub.add_argument("--date", help="YYYY-MM-DD, defaults to today")
        sub.add_argument("--run-id", help="override run id, format YYYYMMDD-HHMMSS[-suffix]")
        sub.add_argument("--schema-version", choices=[INCOMING_SCHEMA_VERSION], default="", help="incoming package schema version")
        if report_type == "patch":
            sub.add_argument("--patch", dest="patches", action="append", default=[], help="explicit manual_import/historical_import patch file; repeatable")
            sub.add_argument("--patch-package", dest="patch_packages", action="append", default=[], help="explicit remote capture package under the Codex artifacts root; repeatable")
            sub.add_argument("--project", default="unknown", help="project name for framework_change incoming")
            sub.add_argument("--platform", default="", help="explicit platform for framework_change incoming: mtk, rk, unisoc, or unknown")
            sub.add_argument("--android-version", default="", help="explicit Android version for framework_change incoming, for example 14, 16, or 9.0")
            sub.add_argument(
                "--workflow-contract",
                choices=["current_codex_skill", "manual_import", "historical_import"],
                default="",
                help=(
                    "explicit workflow for direct or legacy imports; current capture packages "
                    "carry their own workflow contract"
                ),
            )
            sub.add_argument("--summary", default="Framework 修改沉淀", help="summary for framework_change incoming")
            sub.add_argument("--related-report-run-id", dest="related_report_run_ids", action="append", default=[], help="daily/weekly incoming run_id related to this framework_change; repeatable")
            sub.add_argument(
                "--status",
                choices=["draft", "candidate", "validated", "failed", "blocked"],
                default="validated",
                help="patch package status",
            )
        if report_type == "weekly":
            sub.add_argument(
                "--weekly-facts",
                default="",
                help="authoritative akbs-weekly-work-facts-v6 JSON for unresolved project, GMS progress, ledger, or document facts",
            )
            sub.add_argument(
                "--replace-weekly-run-id",
                default="",
                help="explicitly generate a revised weekly package for the current report and write supersedes metadata",
            )
        if report_type == "daily":
            sub.add_argument(
                "--daily-facts",
                default="",
                help="authoritative akbs-daily-work-facts-v4 JSON with today scopes, GMS progress, and an independent tomorrow_plan",
            )
            sub.add_argument(
                "--replace-daily-run-id",
                default="",
                help="explicitly generate a revised daily package for the current report and write supersedes metadata",
            )
        if report_type in {"daily", "weekly"}:
            sub.add_argument(
                "--session-consent",
                action="store_true",
                help="explicitly authorize this one report run to read only the derived date window and selected session fields",
            )
            sub.add_argument(
                "--session-field",
                action="append",
                choices=sorted(ALLOWED_SESSION_FIELDS),
                default=[],
                help="authorized session field for this report run; repeatable and required with --session-consent",
            )
        action = sub.add_mutually_exclusive_group(required=True)
        action.add_argument("--prepare", action="store_true", help="generate pending package only")
        action.add_argument("--submit-latest", action="store_true", help="submit latest pending package")
        action.add_argument("--upload", action="store_true", help="prepare then submit")
        action.add_argument("--validate", metavar="PACKAGE_DIR", help="validate an existing package")
        if report_type == "patch":
            action.add_argument(
                "--inspect-information-request",
                metavar="REQUEST_ID",
                help="read one open queue information request for the existing patch package",
            )
            action.add_argument(
                "--complete-information-request",
                metavar="RESPONSE_JSON",
                help="submit text, fields, or non-patch attachments to the existing patch package",
            )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    config, loaded = load_config(args.profile)
    date: dt.date | None = None
    patch_submit_pending: Path | None = None

    if args.command == "doctor":
        result = doctor(config, loaded, args.strict, args.check_remote, args.allow_synthetic)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("status", "PASS") == "PASS" else 1

    if args.command in {"daily", "weekly"} and not args.validate and (args.prepare or args.upload):
        date = parse_date_arg(args.date, config)
        dates, _start, _end, _week_key = report_dates(args.command, date)
        configure_report_session_consent(
            config,
            dates,
            granted=bool(args.session_consent),
            fields=list(args.session_field),
        )
        require_report_session_consent(config, dates, synthetic=synthetic_mode(config))
    if args.command == "patch" and args.submit_latest:
        patch_submit_pending = latest_pending("patch", config, parse_date_arg(args.date, config) if args.date else None)

    patch_information_action = bool(
        getattr(args, "inspect_information_request", "")
        or getattr(args, "complete_information_request", "")
    )
    if args.command in PACKAGE_TYPES and args.validate:
        # Validation is a local read-only contract/install-family gate.  It must
        # never fetch metadata, update an installation, or re-exec another copy.
        install_family = installed_plugin_family_status()
        if install_family.get("blocking"):
            print(
                json.dumps(
                    {
                        "status": "FAIL",
                        "message": install_family.get("message") or "插件本地安装族检查失败。",
                        "install_family": install_family,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 1
    elif args.command in PACKAGE_TYPES and (
        args.prepare
        or args.submit_latest
        or args.upload
        or patch_information_action
    ):
        freshness = plugin_version_gate_check(config, fetch=True, require=True)
        if freshness.get("blocking"):
            reexec_error = reexec_latest_plugin_script_after_update(freshness)
            if reexec_error:
                freshness["reexec_error"] = reexec_error
            print(
                json.dumps(
                    {
                        "status": "FAIL",
                        "message": freshness.get("reexec_error") or freshness.get("message") or "插件更新检查失败。",
                        "plugin_freshness": freshness,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 1

    if patch_information_action:
        enforce_mode_allowed(config, "patch")
        if getattr(args, "inspect_information_request", ""):
            result = inspect_information_request(config, args.inspect_information_request)
        else:
            result = complete_information_request(config, Path(args.complete_information_request))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    date = date or parse_date_arg(args.date, config)
    if args.validate:
        result = validate_package(Path(args.validate))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] == "PASS" else 1
    if args.prepare or args.submit_latest or args.upload:
        enforce_mode_allowed(config, args.report_type)
    if args.prepare:
        schema_version = args.schema_version or config.get("incoming_schema_version", INCOMING_SCHEMA_VERSION)
        if args.report_type == "patch":
            package_dir = prepare_patch_package(
                date,
                config,
                args.run_id,
                args.patches,
                args.patch_packages,
                args.project,
                args.summary,
                args.status,
                schema_version,
                args.related_report_run_ids,
                args.platform,
                args.android_version,
                args.workflow_contract,
            )
        else:
            package_dir = prepare_package(
                args.report_type,
                date,
                config,
                args.run_id,
                schema_version,
                getattr(args, "replace_daily_run_id", "") or getattr(args, "replace_weekly_run_id", ""),
                getattr(args, "daily_facts", ""),
                getattr(args, "weekly_facts", ""),
            )
        result = json.loads((package_dir / "local-check.json").read_text(encoding="utf-8"))
        print(json.dumps({"package": str(package_dir), "local_check": result}, ensure_ascii=False, indent=2))
        return 0 if result["status"] == "PASS" else 1
    if args.submit_latest:
        package_dir = patch_submit_pending or latest_pending(args.report_type, config, date if args.date else None)
        result = submit_package(package_dir, config)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.upload:
        schema_version = args.schema_version or config.get("incoming_schema_version", INCOMING_SCHEMA_VERSION)
        if args.report_type == "patch":
            package_dir = prepare_patch_package(
                date,
                config,
                args.run_id,
                args.patches,
                args.patch_packages,
                args.project,
                args.summary,
                args.status,
                schema_version,
                args.related_report_run_ids,
                args.platform,
                args.android_version,
                args.workflow_contract,
            )
        else:
            package_dir = prepare_package(
                args.report_type,
                date,
                config,
                args.run_id,
                schema_version,
                getattr(args, "replace_daily_run_id", "") or getattr(args, "replace_weekly_run_id", ""),
                getattr(args, "daily_facts", ""),
                getattr(args, "weekly_facts", ""),
            )
        result = submit_package(package_dir, config)
        print(json.dumps({"package": str(package_dir), "submit": result}, ensure_ascii=False, indent=2))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
