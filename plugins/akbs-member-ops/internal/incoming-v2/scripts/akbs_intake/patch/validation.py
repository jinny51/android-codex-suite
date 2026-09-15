from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any, Callable, Optional, Pattern

from akbs_member_ops.patch_analysis import (
    changed_lines as patch_changed_lines,
    resource_keys_from_patch_text,
)
from akbs_member_ops.verification_evidence import has_authoritative_requirement_result

RequireFile = Callable[[Any, str], Optional[Path]]
ReadReferencedJson = Callable[[Path, str], Optional[dict[str, Any]]]
ReadJsonFile = Callable[[Path], dict[str, Any]]
LoadEvidence = Callable[[list[Any]], dict[str, dict[str, Any]]]
ValidatePatchReadme = Callable[..., list[str]]
HasUncontrolledPatchAssetPrefix = Callable[[Any], bool]
ValueValidator = Callable[[str], bool]
TextFieldQualityErrors = Callable[[dict[str, Any]], list[str]]
ListStringValues = Callable[[Any], list[str]]
UniqueStrings = Callable[[list[str]], list[str]]
TemplateLeakErrors = Callable[..., list[str]]
AggregatePackageScopeErrors = Callable[[str, int], list[str]]
WorkflowContractRequiresPreChangeSearch = Callable[[str], bool]
SearchPayloadPredicate = Callable[[dict[str, Any]], bool]


SCOPE_POLLUTION_UNRELATED_ANCHOR_THRESHOLD = 4
SCOPE_POLLUTION_REPORT_LIMIT = 8
PATCH_SCOPE_README_HEADINGS = {"功能描述", "修改点"}
SCOPE_TEXT_ALIASES = {
    "电池": ["battery"],
    "性能": ["performance"],
    "模式": ["mode"],
    "三档": ["level"],
    "刷新率": ["refresh", "rate"],
    "刷新": ["refresh"],
    "节能": ["power", "save", "eco"],
    "省电": ["power", "save"],
    "中文": ["chinese", "zh"],
    "韩文": ["korean", "ko"],
    "文案": ["string", "text"],
    "颜色": ["color"],
    "色域": ["color", "gamut"],
    "代理": ["proxy"],
    "以太网": ["ethernet"],
    "手势": ["gesture"],
    "截图": ["screenshot"],
    "内存": ["ram", "memory"],
    "时区": ["zone", "timezone"],
    "蓝牙": ["bluetooth"],
    "重置": ["reset"],
}
SCOPE_ANCHOR_GENERIC_TOKENS = {
    "action",
    "array",
    "auto",
    "color",
    "config",
    "device",
    "mode",
    "name",
    "off",
    "on",
    "settings",
    "status",
    "string",
    "summary",
    "system",
    "text",
    "title",
}


@dataclass
class FrameworkChangeValidationContext:
    manifest_platform: str
    manifest_android_version: str
    package_status: str
    files: dict[str, Any]
    case_path: Path | None
    variant_path: Path | None
    readme_path: Path | None
    patch_paths: list[Any]
    display_paths: list[Any]
    evidence_paths: list[Any]


@dataclass
class FrameworkChangeStructureContext:
    case_problem: str
    case_solution: str
    evidence_by_kind: dict[str, dict[str, Any]]


@dataclass
class PatchAIFactsValidationContext:
    modified_files: list[str]


def validate_android_change_manifest_and_files(
    *,
    package_dir: Path,
    manifest: dict[str, Any],
    package_status_values: set[str],
    run_id_re: Pattern[str],
    require_file: RequireFile,
    validate_patch_readme: ValidatePatchReadme,
    has_uncontrolled_patch_asset_prefix: HasUncontrolledPatchAssetPrefix,
    is_valid_platform_value: ValueValidator,
    is_valid_android_version_value: ValueValidator,
    errors: list[str],
) -> FrameworkChangeValidationContext:
    for field in ("case_id", "variant_id", "package_status", "platform", "android_version", "project"):
        if not manifest.get(field):
            errors.append(f"android_change 缺少 {field}")

    manifest_platform = str(manifest.get("platform") or "").strip().lower()
    manifest_android_version = str(manifest.get("android_version") or "").strip().lower()
    if manifest_platform and not is_valid_platform_value(manifest_platform):
        errors.append(f"android_change platform 非法: {manifest_platform}；只能使用 mtk/rk/unisoc/unknown")
    if manifest_android_version and not is_valid_android_version_value(manifest_android_version):
        errors.append(f"android_change android_version 非法: {manifest_android_version}")
    if "maturity" in manifest:
        errors.append("android_change manifest 不允许使用 maturity；请使用 package_status")

    package_status = str(manifest.get("package_status", ""))
    if package_status not in package_status_values:
        errors.append(f"package_status 非法: {package_status}")

    if "related_report_run_ids" in manifest:
        related = manifest.get("related_report_run_ids")
        if not isinstance(related, list):
            errors.append("related_report_run_ids 必须是数组")
        else:
            for item in related:
                if not run_id_re.fullmatch(str(item or "")):
                    errors.append(f"related_report_run_ids 包含非法 run_id: {item}")

    files = manifest.get("files")
    if not isinstance(files, dict):
        errors.append("android_change files 必须是对象")
        files = {}

    case_path = require_file(files.get("case"), "files.case")
    variant_path = require_file(files.get("variant"), "files.variant")
    readme_path = require_file(files.get("readme"), "files.readme")
    patch_paths = files.get("patches", [])
    display_paths = files.get("display", [])
    evidence_paths = files.get("evidence", [])

    if not isinstance(patch_paths, list):
        errors.append("files.patches 必须是数组")
        patch_paths = []
    elif not patch_paths:
        errors.append("files.patches 必须是非空数组")
        patch_paths = []

    if not isinstance(display_paths, list) or not display_paths:
        errors.append("android_change files.display 必须包含 materials/display/patch_view.json")
        display_paths = []
    if not isinstance(evidence_paths, list) or not evidence_paths:
        errors.append("files.evidence 必须是非空数组")
        evidence_paths = []

    for patch_path in patch_paths:
        path = require_file(patch_path, "patch")
        if path and path.suffix not in {".patch", ".diff"}:
            errors.append(f"patch 文件必须是 .patch 或 .diff: {patch_path}")
        if has_uncontrolled_patch_asset_prefix(patch_path):
            errors.append(
                f"补丁资产（patch asset）不能使用非受控前缀: {patch_path}；"
                "前缀必须是合法项目名（project）或 mtk/rk/unisoc 受控平台 Android 版本前缀。"
            )

    if readme_path:
        errors.extend(
            validate_patch_readme(
                readme_path,
                expected_version=str(manifest.get("schema_version") or ""),
                expected_kind=str(manifest.get("package_kind") or ""),
            )
        )
    for patch_readme_path in sorted((package_dir / "patches").glob("*.readme.md")):
        errors.extend(validate_patch_readme(patch_readme_path))

    return FrameworkChangeValidationContext(
        manifest_platform=manifest_platform,
        manifest_android_version=manifest_android_version,
        package_status=package_status,
        files=files,
        case_path=case_path,
        variant_path=variant_path,
        readme_path=readme_path,
        patch_paths=patch_paths,
        display_paths=display_paths,
        evidence_paths=evidence_paths,
    )


def validate_android_change_structure(
    *,
    package_dir: Path,
    manifest: dict[str, Any],
    package_status: str,
    case_path: Path | None,
    variant_path: Path | None,
    evidence_paths: list[Any],
    load_evidence: LoadEvidence,
    read_json_file: ReadJsonFile,
    read_referenced_json: ReadReferencedJson,
    text_field_quality_errors: TextFieldQualityErrors,
    is_valid_platform_value: ValueValidator,
    is_valid_android_version_value: ValueValidator,
    framework_required_evidence_kinds: set[str],
    errors: list[str],
) -> FrameworkChangeStructureContext:
    case_problem = ""
    case_solution = ""
    if case_path:
        case = read_json_file(case_path)
        if case.get("case_id") != manifest.get("case_id"):
            errors.append("case_id 不一致")
        for field in ("title", "problem", "solution_summary"):
            if not case.get(field):
                errors.append(f"case 缺少 {field}")
        errors.extend(
            text_field_quality_errors(
                {
                    "case.title": case.get("title"),
                    "case.problem": case.get("problem"),
                    "case.solution_summary": case.get("solution_summary"),
                }
            )
        )
        case_problem = str(case.get("problem") or "")
        case_solution = str(case.get("solution_summary") or "")

    if variant_path:
        variant = read_json_file(variant_path)
        if variant.get("variant_id") != manifest.get("variant_id"):
            errors.append("variant_id 不一致")
        if "status" in variant:
            errors.append("variant 不允许使用 status；请使用 package_status")
        if variant.get("package_status") != package_status:
            errors.append("variant.package_status 必须等于 manifest.package_status")
        required_variant_fields = ("platform", "android_version", "project", "repo_paths", "package_status")
        for field in required_variant_fields:
            if not variant.get(field):
                errors.append(f"variant 缺少 {field}")
        variant_platform = str(variant.get("platform") or "").strip().lower()
        variant_android_version = str(variant.get("android_version") or "").strip().lower()
        if variant_platform and not is_valid_platform_value(variant_platform):
            errors.append(f"variant.platform 非法: {variant_platform}；只能使用 mtk/rk/unisoc/unknown")
        if variant_android_version and not is_valid_android_version_value(variant_android_version):
            errors.append(f"variant.android_version 非法: {variant_android_version}")
        if variant.get("platform") != manifest.get("platform"):
            errors.append("variant.platform 必须等于 manifest.platform")
        if variant.get("android_version") != manifest.get("android_version"):
            errors.append("variant.android_version 必须等于 manifest.android_version")
        if variant.get("project") != manifest.get("project"):
            errors.append("variant.project 必须等于 manifest.project")

    evidence_by_kind = load_evidence(evidence_paths)
    for rel in evidence_paths:
        if not isinstance(rel, str):
            continue
        evidence = read_referenced_json(package_dir, rel)
        if not isinstance(evidence, dict):
            continue
        if evidence.get("case_id") != manifest.get("case_id"):
            errors.append(f"{rel} evidence.case_id 必须等于 manifest.case_id")
        if evidence.get("variant_id") != manifest.get("variant_id"):
            errors.append(f"{rel} evidence.variant_id 必须等于 manifest.variant_id")
        if evidence.get("kind") == "patch_problem_summary":
            payload = evidence
            if "payload" in payload:
                errors.append(f"{rel} 必须直接使用顶层字段，不能再包一层 payload")
            if not payload.get("problem_summary") or not payload.get("solution_summary"):
                errors.append(f"{rel} 必须包含 problem_summary 和 solution_summary")
            if not isinstance(payload.get("basis"), list) or not payload.get("basis"):
                errors.append(f"{rel} basis 必须是非空数组")
            if not isinstance(payload.get("limits"), list):
                errors.append(f"{rel} limits 必须是数组")

    for kind in framework_required_evidence_kinds:
        if kind not in evidence_by_kind:
            errors.append(f"android_change 缺少 {kind} evidence")

    return FrameworkChangeStructureContext(
        case_problem=case_problem,
        case_solution=case_solution,
        evidence_by_kind=evidence_by_kind,
    )


def validate_patch_ai_facts_and_diff(
    *,
    evidence_by_kind: dict[str, dict[str, Any]],
    list_string_values: ListStringValues,
    unique_strings: UniqueStrings,
    errors: list[str],
) -> PatchAIFactsValidationContext:
    ai_facts = evidence_by_kind.get("patch_ai_facts", {})
    ai_payload = ai_facts.get("payload", {}) if isinstance(ai_facts, dict) else {}
    if isinstance(ai_payload, dict):
        for field in (
            "module",
            "feature_domain",
            "patch_behavior_goal",
            "code_anchors",
            "patch_assets",
            "verification_targets",
            "search_usage",
            "search_match_class",
            "merge_gate_inputs",
            "protocol_version",
            "plugin_version",
        ):
            if not ai_payload.get(field):
                errors.append(f"patch_ai_facts.{field} 必须提供")
        anchors = ai_payload.get("code_anchors", {})
        if isinstance(anchors, dict):
            if not any(
                list_string_values(anchors.get(key))
                for key in (
                    "files",
                    "symbols",
                    "resource_keys",
                    "settings_keys",
                    "system_properties",
                    "framework_log_keys",
                )
            ):
                errors.append("patch_ai_facts.code_anchors 必须包含至少一种代码锚点")
        else:
            errors.append("patch_ai_facts.code_anchors 必须是对象")
        merge_inputs = ai_payload.get("merge_gate_inputs", {})
        if isinstance(merge_inputs, dict):
            for field in (
                "module",
                "feature_domain",
                "code_anchors",
                "patch_behavior_goal",
                "verification_targets",
                "project",
                "platform",
                "android_version",
            ):
                if not merge_inputs.get(field):
                    errors.append(f"patch_ai_facts.merge_gate_inputs.{field} 必须提供")
        else:
            errors.append("patch_ai_facts.merge_gate_inputs 必须是对象")

    patch_diff_payload = evidence_payload(evidence_by_kind.get("patch_diff_facts", {}))
    modified_files = list_string_values(patch_diff_payload.get("modified_files"))
    patch_items = patch_diff_payload.get("patches")
    if isinstance(patch_items, list):
        for item in patch_items:
            if isinstance(item, dict):
                modified_files.extend(list_string_values(item.get("modified_files")))
    return PatchAIFactsValidationContext(modified_files=unique_strings(modified_files))


def validate_patch_template_leaks(
    *,
    package_dir: Path,
    manifest: dict[str, Any],
    evidence_paths: list[Any],
    case_problem: str,
    case_solution: str,
    patch_paths: list[Any],
    modified_files: list[str],
    read_referenced_json: ReadReferencedJson,
    template_leak_errors: TemplateLeakErrors,
    errors: list[str],
) -> None:
    errors.extend(
        template_leak_errors(
            summary=manifest.get("summary"),
            problem=case_problem,
            solution=case_solution,
            patch_paths=patch_paths,
            modified_files=modified_files,
        )
    )
    for rel in evidence_paths:
        if not isinstance(rel, str):
            continue
        evidence = read_referenced_json(package_dir, rel)
        if not isinstance(evidence, dict) or evidence.get("kind") != "patch_problem_summary":
            continue
        errors.extend(
            template_leak_errors(
                summary=manifest.get("summary"),
                problem=evidence.get("problem_summary"),
                solution=evidence.get("solution_summary"),
                patch_paths=patch_paths,
                modified_files=modified_files,
            )
        )

def evidence_payload(evidence: dict[str, Any]) -> dict[str, Any]:
    payload = evidence.get("payload")
    return payload if isinstance(payload, dict) else evidence


def patch_count_from_framework_package(patch_paths: list[Any], evidence_by_kind: dict[str, dict[str, Any]]) -> int:
    counts = [len(patch_paths)]
    patch_diff = evidence_by_kind.get("patch_diff_facts", {})
    payload = evidence_payload(patch_diff) if isinstance(patch_diff, dict) else {}
    try:
        counts.append(int(payload.get("patch_count") or 0))
    except (TypeError, ValueError):
        pass
    patches = payload.get("patches")
    if isinstance(patches, list):
        counts.append(len(patches))
    return max(counts or [0])


def scope_words(value: Any) -> set[str]:
    text = str(value or "")
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    text = text.replace("_", " ").replace("-", " ").replace("/", " ").replace(".", " ")
    words = {
        token.lower()
        for token in re.findall(r"[a-zA-Z][a-zA-Z0-9]{1,}|[0-9]+|[\u4e00-\u9fff]{2,}", text)
        if len(token.strip()) >= 2
    }
    for marker, aliases in SCOPE_TEXT_ALIASES.items():
        if marker in str(value or ""):
            words.update(aliases)
    return words


def scope_semantic_tokens(*values: Any) -> set[str]:
    tokens: set[str] = set()
    for value in values:
        tokens.update(scope_words(value))
    return {token for token in tokens if token not in SCOPE_ANCHOR_GENERIC_TOKENS}


def patch_scope_readme_text(readme_text: str) -> str:
    sections: list[str] = []
    current_heading = ""
    current_lines: list[str] = []
    for line in readme_text.splitlines():
        match = re.match(r"^##\s+(.+?)\s*$", line)
        if match:
            if current_heading in PATCH_SCOPE_README_HEADINGS:
                sections.append("\n".join(current_lines).strip())
            current_heading = match.group(1).strip()
            current_lines = []
            continue
        current_lines.append(line)
    if current_heading in PATCH_SCOPE_README_HEADINGS:
        sections.append("\n".join(current_lines).strip())
    return "\n\n".join(section for section in sections if section)


def scope_anchor_tokens(value: str) -> set[str]:
    return {token for token in scope_words(value) if token not in SCOPE_ANCHOR_GENERIC_TOKENS}


def scope_anchor_related(value: str, semantic_tokens: set[str]) -> bool:
    anchor_tokens = scope_anchor_tokens(value)
    if not anchor_tokens:
        return False
    if anchor_tokens & semantic_tokens:
        return True
    anchor_text = re.sub(r"[^a-z0-9]+", "", value.lower())
    return any(len(token) >= 4 and token in anchor_text for token in semantic_tokens)


def patch_resource_keys_from_evidence(
    evidence_by_kind: dict[str, dict[str, Any]],
    list_string_values: ListStringValues,
) -> list[str]:
    keys: list[str] = []
    patch_diff = evidence_by_kind.get("patch_diff_facts", {})
    payload = evidence_payload(patch_diff) if isinstance(patch_diff, dict) else {}
    keys.extend(list_string_values(payload.get("resource_keys")))
    patches = payload.get("patches")
    if isinstance(patches, list):
        for patch in patches:
            if isinstance(patch, dict):
                keys.extend(list_string_values(patch.get("resource_keys")))
    return sorted(set(keys))


def patch_resource_keys_from_files(package_dir: Path, patch_paths: list[Any]) -> list[str]:
    keys: list[str] = []
    package_root = package_dir.resolve()
    for rel in patch_paths:
        if not isinstance(rel, str):
            continue
        path = (package_dir / rel).resolve()
        try:
            path.relative_to(package_root)
        except ValueError:
            continue
        if not path.is_file():
            continue
        patch_text = path.read_text(encoding="utf-8", errors="replace")
        keys.extend(resource_keys_from_patch_text("\n".join(patch_changed_lines(patch_text))))
    return sorted(set(keys))


def validate_framework_scope_pollution(
    package_dir: Path,
    manifest: dict[str, Any],
    readme_text: str,
    patch_paths: list[Any],
    evidence_by_kind: dict[str, dict[str, Any]],
    list_string_values: ListStringValues,
) -> list[str]:
    semantic_tokens = scope_semantic_tokens(manifest.get("summary"), readme_text)
    if not semantic_tokens:
        return []
    file_resource_keys = patch_resource_keys_from_files(package_dir, patch_paths)
    evidence_resource_keys = [] if file_resource_keys else patch_resource_keys_from_evidence(evidence_by_kind, list_string_values)
    resource_keys = sorted(set([*file_resource_keys, *evidence_resource_keys]))
    anchors = [key for key in resource_keys if scope_anchor_tokens(key)]
    related = [key for key in anchors if scope_anchor_related(key, semantic_tokens)]
    if not related:
        return []
    unrelated = [key for key in anchors if not scope_anchor_related(key, semantic_tokens)]
    if len(unrelated) < SCOPE_POLLUTION_UNRELATED_ANCHOR_THRESHOLD:
        return []
    sample = "、".join(unrelated[:SCOPE_POLLUTION_REPORT_LIMIT])
    return [
        (
            "补丁包功能范围与补丁资源锚点不一致，疑似补丁资产污染。"
            f"无关资源键示例：{sample}。"
            "请在干净工作树重新采集同一功能补丁包；"
            "如果实际是多个独立功能，请按功能拆分（function split）为多个新的补丁包。"
        )
    ]


def validate_framework_function_scope(
    *,
    package_dir: Path,
    manifest: dict[str, Any],
    readme_path: Path | None,
    patch_paths: list[Any],
    evidence_by_kind: dict[str, dict[str, Any]],
    list_string_values: ListStringValues,
    aggregate_package_scope_errors: AggregatePackageScopeErrors,
) -> list[str]:
    patch_count = patch_count_from_framework_package(patch_paths, evidence_by_kind)
    readme_text = readme_path.read_text(encoding="utf-8", errors="ignore") if readme_path and readme_path.is_file() else ""
    errors: list[str] = []
    scope_readme_text = patch_scope_readme_text(readme_text) or readme_text
    errors.extend(
        validate_framework_scope_pollution(
            package_dir,
            manifest,
            scope_readme_text,
            patch_paths,
            evidence_by_kind,
            list_string_values,
        )
    )
    text = "\n".join([str(manifest.get("summary") or ""), readme_text])
    errors.extend(aggregate_package_scope_errors(text, patch_count))
    return errors


def validate_patch_verification_result(
    *,
    evidence_by_kind: dict[str, dict[str, Any]],
    package_status: str,
    errors: list[str],
) -> None:
    verification = evidence_by_kind.get("verification_result", {})
    verification_payload = verification.get("payload", verification) if isinstance(verification, dict) else {}
    result = str(verification_payload.get("result", "")).upper()
    if result not in {"PASS", "FAIL", "MISSING"}:
        errors.append("verification_result.result 必须是 PASS、FAIL 或 MISSING")
    if not verification_payload.get("method"):
        errors.append("verification_result.method 必须提供")
    if package_status == "validated" and result != "PASS":
        errors.append("validated 必须提供 PASS 验证")
    elif package_status == "validated" and not has_authoritative_requirement_result(
        verification_payload,
        expected_result="PASS",
    ):
        errors.append(
            "validated 必须提供明确的需求级验收 PASS；"
            "build delivery、adb push 或旧版无作用域 verification_result 只能保留为非 validated evidence"
        )
    if package_status == "failed" and result != "FAIL":
        errors.append("failed 必须提供 FAIL 验证")


def validate_patch_pre_change_search(
    *,
    manifest: dict[str, Any],
    evidence_by_kind: dict[str, dict[str, Any]],
    package_status: str,
    workflow_contract_requires_pre_change_search: WorkflowContractRequiresPreChangeSearch,
    search_payload_missing_required_pre_change_search: SearchPayloadPredicate,
    search_payload_needs_closed_decision: SearchPayloadPredicate,
    errors: list[str],
    warnings: list[str],
) -> None:
    search_evidence = evidence_by_kind.get("search_before_change", {})
    search_payload = search_evidence.get("payload", search_evidence) if isinstance(search_evidence, dict) else {}
    workflow_contract = str(manifest.get("workflow_contract") or "").strip()
    if workflow_contract not in {
        "current_codex_skill",
        "manual_import",
        "historical_import",
    }:
        errors.append(
            "android_change 必须显式记录 workflow_contract="
            "current_codex_skill/manual_import/historical_import；"
            "不能根据 implementation_origins 猜测工作流。"
        )
    search_payload_body = search_payload.get("payload", search_payload) if isinstance(search_payload, dict) else {}
    if not isinstance(search_payload_body, dict):
        search_payload_body = {}
    missing_pre_change_search = not bool(search_payload_body.get("searched"))
    requires_pre_change_search = workflow_contract_requires_pre_change_search(
        workflow_contract
    )
    if (
        package_status == "validated"
        and requires_pre_change_search
        and search_payload_missing_required_pre_change_search(search_payload)
    ):
        errors.append(
            "开发前知识搜索（pre-change knowledge search）未发生，不能事后补造。"
            "本包声明为 current_codex_skill 工作流，不能按当前 validated 上传；"
            "请保持真实工作流和实施来源，不能互相改写以规避门禁。"
            "当前结果可留在本地或报告上下文；后续重新开发时必须先完成知识检索。"
        )
    elif (
        package_status == "validated"
        and missing_pre_change_search
        and workflow_contract in {"manual_import", "historical_import"}
    ):
        warnings.append(
            "开发前知识搜索（pre-change knowledge search）未发生，不能事后补造；"
            "本包按已记录的导入工作流保留，"
            "管理端后续会执行沉淀前重叠检索（post-change overlap check），且不获得搜索闭环加分。"
        )
    if (
        package_status == "validated"
        and requires_pre_change_search
        and search_payload_needs_closed_decision(search_payload)
    ):
        errors.append(
            "已验证（validated）补丁包命中知识搜索结果时必须闭合搜索使用决策（search usage decision），"
            "请使用 reuse/adapt/reference_only/not_applicable/not_found"
        )


PATCH_VIEW_REQUIRED_FIELDS = (
    "package_label",
    "display_title",
    "problem_summary",
    "solution_summary",
    "result_summary",
    "project",
    "platform",
    "android_version",
    "member_alias",
    "member_name",
    "ui_card",
    "detail_sections",
)
PATCH_VIEW_FORBIDDEN_TITLE_MARKERS = (
    "case-",
    "variant-",
    "merge_case_add_variant",
    "target_case_id",
    "source_package_keys",
)


def validate_patch_view_payload(
    *,
    rel: str,
    manifest: dict[str, Any],
    view: dict[str, Any],
    errors: list[str],
) -> None:
    for field in PATCH_VIEW_REQUIRED_FIELDS:
        if not view.get(field):
            errors.append(f"{rel} payload.{field} 必须提供")
    if view.get("project") != manifest.get("project"):
        errors.append(f"{rel} payload.project 必须等于 manifest.project")
    if view.get("platform") != manifest.get("platform"):
        errors.append(f"{rel} payload.platform 必须等于 manifest.platform")
    if view.get("android_version") != manifest.get("android_version"):
        errors.append(f"{rel} payload.android_version 必须等于 manifest.android_version")
    if view.get("package_label") != "补丁包":
        errors.append(f"{rel} payload.package_label 必须是补丁包")
    title_text = " ".join(
        [
            str(view.get("display_title") or ""),
            str(view.get("ui_card", {}).get("title") if isinstance(view.get("ui_card"), dict) else ""),
        ]
    )
    for forbidden in PATCH_VIEW_FORBIDDEN_TITLE_MARKERS:
        if forbidden in title_text:
            errors.append(f"{rel} 主展示标题不能包含内部字段或机器锚点: {forbidden}")


def validate_patch_display_files(
    *,
    package_dir: Path,
    display_paths: list[Any],
    manifest: dict[str, Any],
    require_file: RequireFile,
    read_referenced_json: ReadReferencedJson,
    errors: list[str],
) -> None:
    for rel in display_paths:
        path = require_file(rel, "display")
        if not path:
            continue
        patch_view = read_referenced_json(package_dir, rel)
        if not isinstance(patch_view, dict):
            continue
        if patch_view.get("kind") != "patch_view":
            errors.append(f"{rel} kind 必须是 patch_view")
            continue
        view = patch_view.get("payload", {})
        if not isinstance(view, dict):
            errors.append(f"{rel} payload 必须是对象")
            continue
        validate_patch_view_payload(
            rel=rel,
            manifest=manifest,
            view=view,
            errors=errors,
        )
