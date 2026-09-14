from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any, Callable

from akbs_member_ops.knowledge_rules import apply_platform_overrides

from akbs_intake.config import expanded_path, local_now, require_config, require_safe_artifact_path, synthetic_mode
from akbs_intake.io_utils import list_string_values, materials_rel, unique_strings, write_json
from akbs_intake.project_identity import infer_project as _infer_project
from akbs_intake.report_sessions import ymd
from akbs_intake.reports.identity import related_report_project_clues, same_day_daily_report_run_ids
from akbs_intake.search_usage import patch_search_feature_tokens, search_payload_has_member_decision, search_usage_payload
from akbs_intake.patch.assets import (
    copy_patch_assets,
    patch_infos_from_paths,
    patch_readme_usable_for_inference,
    synthetic_patch_info,
    validate_patch_attribution,
    write_feature_readme_from_patch_entries,
)
from akbs_intake.patch.capture_import import copy_patch_capture_packages, patch_capture_package_scope_errors
from akbs_intake.patch.evidence import (
    aggregate_patch_diff_facts,
    bind_framework_evidence,
    bind_framework_evidence_paths,
    ensure_patch_analysis_evidence,
    ensure_required_patch_explanation_evidence,
    incoming_patch_item,
    select_search_before_change_payload,
    verification_payload_or_missing,
    write_patch_view_and_ai_facts,
)
from akbs_intake.patch.manifest import (
    framework_case_variant_ids,
    android_change_evidence_paths,
    android_change_manifest,
    write_case_file,
    write_variant_file,
)
from akbs_intake.patch.metadata import first_evidence_path, first_evidence_payload, infer_platform_metadata, repo_paths_from_files
from akbs_intake.patch.package_quality import (
    downgrade_validated_patch_entries,
    framework_metadata_is_traceable,
    framework_package_status_from_patch_statuses,
    write_default_evidence,
)

ValidatePackage = Callable[[Path], dict[str, Any]]
WritePackageSource = Callable[[Path, dict[str, str], str], dict[str, Any]]
PluginInstallMetadata = Callable[[], dict[str, str]]
COMPONENT_LAYERS = (
    "application",
    "platform",
    "native",
    "hal",
    "kernel",
    "device",
    "build",
)


def components_from_patch_items(patch_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[str]] = {layer: [] for layer in COMPONENT_LAYERS}
    for item in patch_items:
        layer = str(item.get("layer") or "")
        path = str(item.get("path") or "")
        if layer not in grouped:
            raise SystemExit(f"补丁缺少有效 layer 分类: {path}")
        grouped[layer].append(path)
    return [
        {"layer": layer, "patches": sorted(grouped[layer])}
        for layer in COMPONENT_LAYERS
        if grouped[layer]
    ]


def infer_project(
    explicit_project: str,
    patch_entries: list[dict[str, Any]],
    patch_sources: list[dict[str, Any]],
    summary: str,
    package_dir: Path | None = None,
    source_contexts: list[dict[str, Any]] | None = None,
    related_report_clues: list[tuple[str, str]] | None = None,
    trusted_platform: str = "",
) -> tuple[str, dict[str, Any]]:
    return _infer_project(
        explicit_project,
        patch_entries,
        patch_sources,
        summary,
        package_dir=package_dir,
        source_contexts=source_contexts,
        related_report_clues=related_report_clues,
        trusted_platform=trusted_platform,
        readme_usable_for_inference=patch_readme_usable_for_inference,
    )


def build_patch_package(
    date: dt.date,
    config: dict[str, str],
    run_id: str | None = None,
    patch_paths: list[str] | None = None,
    patch_package_paths: list[str] | None = None,
    project: str = "unknown",
    summary: str = "管理员手动归档补丁",
    status: str = "validated",
    schema_version: str = "",
    related_report_run_ids: list[str] | None = None,
    platform_override: str = "",
    android_version_override: str = "",
    workflow_contract_override: str = "",
    component_layer: str = "",
    *,
    incoming_schema_version: str,
    framework_optional_evidence_kinds: set[str],
    validate_package_fn: ValidatePackage,
    write_package_source_fn: WritePackageSource,
    plugin_install_metadata_fn: PluginInstallMetadata,
) -> Path:
    require_config(config)
    schema_version = schema_version or incoming_schema_version
    if schema_version != incoming_schema_version:
        raise SystemExit(f"incoming 只支持 schema_version={incoming_schema_version}")
    if patch_paths and len(patch_paths) > 1:
        raise SystemExit(
            "直接 --patch 只允许单个独立补丁。多个补丁必须先用补丁采集技能（android-patch-capture）"
            "按功能生成补丁包（patch package）；一个补丁包只能对应一个功能。"
        )
    if patch_paths and workflow_contract_override not in {"manual_import", "historical_import"}:
        raise SystemExit(
            "直接 --patch 只允许显式 manual_import 或 historical_import；当前 Codex 工作流请使用 --patch-package。"
        )
    if patch_paths and component_layer not in COMPONENT_LAYERS:
        raise SystemExit(
            "直接 --patch 必须使用 --component-layer 声明 application/platform/native/hal/kernel/device/build"
        )
    direct_patches = patch_infos_from_paths(patch_paths or [], project)
    direct_patch_errors = [
        error
        for patch in direct_patches
        for error in validate_patch_attribution(patch.path)
    ]
    if direct_patch_errors:
        raise SystemExit("\n".join(direct_patch_errors))
    artifact_input_root = expanded_path(config["out_dir"]).parent.resolve()
    for raw_package in patch_package_paths or []:
        capture_package = Path(raw_package).expanduser().resolve()
        try:
            capture_package.relative_to(artifact_input_root)
        except ValueError:
            raise SystemExit(
                f"--patch-package 必须位于 Codex artifacts 根目录下: {artifact_input_root}"
            ) from None
    run_id = run_id or f"{ymd(date)}-{local_now(config):%H%M%S}-patch"
    scope_errors = patch_capture_package_scope_errors(patch_package_paths, summary, run_id)
    if scope_errors:
        raise SystemExit("\n".join(scope_errors))
    out_dir = require_safe_artifact_path(expanded_path(config["out_dir"]), purpose="out_dir")
    package_dir = require_safe_artifact_path(out_dir / "pending" / ymd(date) / config["member_alias"] / run_id, purpose="incoming package output")
    if package_dir.exists():
        raise SystemExit(f"工作包已存在: {package_dir}")
    package_dir.mkdir(parents=True)

    patch_entries: list[dict[str, Any]] = []
    capture_evidence_entries: list[dict[str, Any]] = []
    patch_sources: list[dict[str, Any]] = []
    source_contexts: list[dict[str, Any]] = []
    has_pass_verification = False
    all_related_report_run_ids = list_string_values(related_report_run_ids)
    feature_readme_rel = ""

    if patch_package_paths:
        (
            capture_entries,
            evidence_entries,
            source_entries,
            capture_has_pass,
            capture_related_report_run_ids,
            capture_source_contexts,
            capture_feature_readme_rel,
        ) = copy_patch_capture_packages(
            package_dir,
            patch_package_paths,
            project,
            status,
        )
        patch_entries.extend(capture_entries)
        capture_evidence_entries.extend(evidence_entries)
        patch_sources.extend(source_entries)
        source_contexts.extend(capture_source_contexts)
        has_pass_verification = has_pass_verification or capture_has_pass
        all_related_report_run_ids.extend(capture_related_report_run_ids)
        feature_readme_rel = capture_feature_readme_rel

    if patch_paths:
        patches = direct_patches
    elif synthetic_mode(config):
        patches = [synthetic_patch_info(package_dir, date, project, config)]
        summary = summary if summary != "管理员手动归档补丁" else "合成测试补丁包"
        status = "candidate" if status == "validated" else status
    else:
        patches = []
    if not patches:
        if not patch_entries:
            raise SystemExit(
                "patch 模式必须显式使用 --patch-package（remote capture artifact），"
                "或在 manual_import/historical_import 中显式使用 --patch；不会从 cwd 自动发现补丁。"
            )
    else:
        direct_entries = copy_patch_assets(
            package_dir,
            patches,
            config,
            status=status,
            reuse_hint=status == "validated",
            note="管理员手动归档补丁",
        )
        for item in direct_entries:
            item["layer"] = component_layer or "platform"
        patch_entries.extend(direct_entries)
        patch_sources.extend([{"name": item.name, "source": str(item.path), "project": item.project} for item in patches])
    if not feature_readme_rel:
        feature_readme_rel = write_feature_readme_from_patch_entries(package_dir, summary, patch_entries)
    write_json(
        package_dir / materials_rel("evidence", "android_change_summary.json"),
        {
            "source": "akbs-member-ops",
            "mode": "patch",
            "synthetic_data": synthetic_mode(config),
            "patch_count": len(patch_entries),
            "patches": patch_sources,
            "capture_package_count": len(patch_package_paths or []),
            "implementation_origins": unique_strings(
                str(item.get("implementation_origin") or "")
                for item in patch_entries
                if str(item.get("implementation_origin") or "").strip()
            ),
            "capture_tools": unique_strings(str(item.get("captured_by") or "") for item in patch_entries if str(item.get("captured_by") or "").strip()),
        },
    )
    ensure_patch_analysis_evidence(package_dir, patch_entries, capture_evidence_entries, summary)
    if not has_pass_verification:
        downgrade_validated_patch_entries(patch_entries, "未携带 PASS 设备验证或合格等价验证，已按 candidate 提交")
    for item in patch_entries:
        if item.get("status") in {"failed", "blocked"}:
            item["reuse_hint"] = False

    platform, android_version = apply_platform_overrides(
        *infer_platform_metadata(patch_entries, capture_evidence_entries, package_dir),
        platform_override=platform_override,
        android_version_override=android_version_override,
    )
    auto_related_report_run_ids: list[str] = []
    if not all_related_report_run_ids:
        auto_related_report_run_ids = same_day_daily_report_run_ids(config, date)
        all_related_report_run_ids.extend(auto_related_report_run_ids)
    related_project_clues = related_report_project_clues(
        config,
        all_related_report_run_ids,
        daily_label_prefix="自动关联同日日报" if auto_related_report_run_ids else "关联日报",
    )
    project, project_payload = infer_project(
        project,
        patch_entries,
        patch_sources,
        summary,
        package_dir,
        source_contexts,
        related_project_clues,
        trusted_platform=platform,
    )
    if not framework_metadata_is_traceable(project, platform, android_version):
        downgrade_validated_patch_entries(
            patch_entries,
            "项目（project）、平台（platform）或 Android 版本（Android version）缺少可追溯元数据，已按 candidate 提交",
        )
    statuses = {str(item.get("status", "")) for item in patch_entries}
    source_path = materials_rel("evidence", "source.json")
    source = write_package_source_fn(package_dir, config, "akbs-patch-submit")
    package_status = framework_package_status_from_patch_statuses(statuses, has_pass_verification)
    all_patch_items = [incoming_patch_item(package_dir, item) for item in patch_entries]
    implementation_origins = unique_strings(
        str(item.get("implementation_origin") or "")
        for item in all_patch_items
        if str(item.get("implementation_origin") or "").strip()
    )
    workflow_contracts = unique_strings(
        str(item.get("workflow_contract") or "")
        for item in all_patch_items
        if str(item.get("workflow_contract") or "").strip()
    )
    if len(workflow_contracts) > 1:
        raise SystemExit(
            "一个 android_change 包只能声明一个 workflow_contract；"
            f"当前发现: {', '.join(workflow_contracts)}"
        )
    workflow_contract_override = str(workflow_contract_override or "").strip()
    if workflow_contract_override and workflow_contract_override not in {
        "current_codex_skill",
        "manual_import",
        "historical_import",
    }:
        raise SystemExit(f"不支持 workflow_contract={workflow_contract_override}")
    if (
        workflow_contracts
        and workflow_contract_override
        and workflow_contracts[0] != workflow_contract_override
    ):
        raise SystemExit(
            "命令行 workflow_contract 与补丁采集包不一致；"
            "不能通过导入参数改写采集时记录的工作流。"
        )
    if workflow_contracts:
        workflow_contract = workflow_contracts[0]
    elif workflow_contract_override:
        workflow_contract = workflow_contract_override
    elif patch_package_paths:
        raise SystemExit(
            "旧补丁采集包缺少 workflow_contract；"
            "必须显式使用 --workflow-contract manual_import/historical_import，"
            "不能根据 implementation_origin 或目录形态猜测。"
        )
    else:
        workflow_contract = "current_codex_skill"
    if implementation_origins:
        source["implementation_origins"] = implementation_origins
        if len(implementation_origins) == 1:
            source["implementation_origin"] = implementation_origins[0]
    if workflow_contract:
        source["workflow_contract"] = workflow_contract
    if implementation_origins or workflow_contract:
        write_json(package_dir / source_path, {"kind": "source", "payload": source})
    capture_tools = unique_strings(str(item.get("captured_by") or "") for item in all_patch_items if str(item.get("captured_by") or "").strip())
    modified_files = sorted(
        {
            file
            for item in all_patch_items
            for file in item.get("facts", {}).get("modified_files", [])
            if isinstance(file, str) and file
        }
    )
    repo_paths = sorted(
        {
            str(item.get("repo_path") or "").strip("/")
            for item in all_patch_items
            if str(item.get("repo_path") or "").strip("/")
        }
    ) or repo_paths_from_files(modified_files)
    patch_rel_paths = [str(item["path"]) for item in all_patch_items]
    components = components_from_patch_items(all_patch_items)
    all_related_report_run_ids = unique_strings(all_related_report_run_ids)
    case_id, variant_id = framework_case_variant_ids(
        summary=summary,
        platform=platform,
        android_version=android_version,
        project=project,
        repo_paths=repo_paths,
    )
    patch_problem_payload = first_evidence_payload(package_dir, capture_evidence_entries, "patch_problem_summary")
    case_problem = str(patch_problem_payload.get("problem_summary") or summary)
    case_solution = str(patch_problem_payload.get("solution_summary") or summary)

    case_path = write_case_file(
        package_dir,
        case_id=case_id,
        summary=summary,
        case_problem=case_problem,
        case_solution=case_solution,
    )
    variant_path = write_variant_file(
        package_dir,
        variant_id=variant_id,
        case_id=case_id,
        platform=platform,
        android_version=android_version,
        project=project,
        repo_paths=repo_paths,
        implementation_origins=implementation_origins,
        workflow_contract=workflow_contract,
        capture_tools=capture_tools,
        package_status=package_status,
    )

    project_path = write_default_evidence(
        package_dir,
        materials_rel("evidence", "project_inference.json"),
        {
            "kind": "project_inference",
            "case_id": case_id,
            "variant_id": variant_id,
            "payload": project_payload,
        },
    )

    verification_payload = verification_payload_or_missing(
        first_evidence_payload(package_dir, capture_evidence_entries, "verification_result")
    )
    verification_path = write_default_evidence(
        package_dir,
        materials_rel("evidence", "verification_result.json"),
        {
            "kind": "verification_result",
            "case_id": case_id,
            "variant_id": variant_id,
            "payload": verification_payload,
        },
    )
    if package_status == "validated" and str(verification_payload.get("result", "")).upper() != "PASS":
        package_status = "candidate"

    capture_search_payload = first_evidence_payload(package_dir, capture_evidence_entries, "search_before_change")
    member_search_payload = search_usage_payload(config, date, feature_tokens=patch_search_feature_tokens(summary, all_patch_items, modified_files))
    search_payload = select_search_before_change_payload(
        capture_search_payload=capture_search_payload,
        member_search_payload=member_search_payload,
        capture_has_member_decision=search_payload_has_member_decision(capture_search_payload),
    )
    search_path = write_default_evidence(
        package_dir,
        materials_rel("evidence", "search_before_change.json"),
        {
            "kind": "search_before_change",
            "case_id": case_id,
            "variant_id": variant_id,
            "payload": search_payload,
        },
    )
    optional_evidence_paths = [
        rel
        for kind in sorted(framework_optional_evidence_kinds)
        for rel in [first_evidence_path(capture_evidence_entries, kind)]
        if rel
    ]

    patch_diff_payload = aggregate_patch_diff_facts(all_patch_items)
    patch_diff_path = write_default_evidence(
        package_dir,
        materials_rel("evidence", "patch_diff_facts.json"),
        {
            "kind": "patch_diff_facts",
            "case_id": case_id,
            "variant_id": variant_id,
            "payload": patch_diff_payload,
        },
    )
    patch_problem_path = first_evidence_path(capture_evidence_entries, "patch_problem_summary")
    risk_path = first_evidence_path(capture_evidence_entries, "risk_surface")
    required_generated = {
        "patch_diff_facts": patch_diff_path,
        "patch_problem_summary": patch_problem_path,
        "risk_surface": risk_path,
    }
    required_generated = ensure_required_patch_explanation_evidence(
        package_dir,
        required_generated=required_generated,
        case_id=case_id,
        variant_id=variant_id,
        summary=summary,
    )

    manifest_context = {
        "summary": summary,
        "project": project,
        "platform": platform,
        "android_version": android_version,
        "member_alias": config["member_alias"],
        "member_name": config["member_name"],
    }
    patch_view_path, patch_ai_facts_path = write_patch_view_and_ai_facts(
        package_dir,
        manifest_context=manifest_context,
        case_id=case_id,
        variant_id=variant_id,
        case_problem=case_problem,
        case_solution=case_solution,
        verification_payload=verification_payload,
        risk_payload=first_evidence_payload(package_dir, capture_evidence_entries, "risk_surface"),
        patch_rel_paths=patch_rel_paths,
        patch_diff_payload=patch_diff_payload,
        search_payload=search_payload,
        plugin_version=plugin_install_metadata_fn().get("plugin_version", ""),
    )

    write_variant_file(
        package_dir,
        variant_id=variant_id,
        case_id=case_id,
        platform=platform,
        android_version=android_version,
        project=project,
        repo_paths=repo_paths,
        implementation_origins=implementation_origins,
        workflow_contract=workflow_contract,
        capture_tools=capture_tools,
        package_status=package_status,
        related_report_run_ids=all_related_report_run_ids,
    )
    manifest = android_change_manifest(
        schema_version=incoming_schema_version,
        config=config,
        date=date,
        run_id=run_id,
        case_id=case_id,
        variant_id=variant_id,
        package_status=package_status,
        platform=platform,
        android_version=android_version,
        project=project,
        summary=summary,
        implementation_origins=implementation_origins,
        workflow_contract=workflow_contract,
        capture_tools=capture_tools,
        case_path=case_path,
        variant_path=variant_path,
        feature_readme_rel=feature_readme_rel,
        patch_rel_paths=patch_rel_paths,
        components=components,
        patch_view_path=patch_view_path,
        evidence_paths=android_change_evidence_paths(
            source_path=source_path,
            patch_diff_path=required_generated["patch_diff_facts"],
            patch_ai_facts_path=patch_ai_facts_path,
            project_path=project_path,
            patch_problem_path=required_generated["patch_problem_summary"],
            risk_path=required_generated["risk_surface"],
            verification_path=verification_path,
            search_path=search_path,
            optional_evidence_paths=optional_evidence_paths,
        ),
        related_report_run_ids=all_related_report_run_ids,
    )
    bind_framework_evidence_paths(package_dir, manifest["files"]["evidence"], case_id, variant_id)
    write_json(package_dir / "manifest.json", manifest)
    check = validate_package_fn(package_dir)
    write_json(package_dir / "local-check.json", check)
    return package_dir
