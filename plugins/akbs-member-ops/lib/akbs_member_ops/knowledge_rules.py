from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from typing import Any


AKBS_RULES_CONTRACT_VERSION = "2026-08-27.1"
SEMVER_RE = re.compile(r"^\d+(?:\.\d+){1,3}(?:[-+][A-Za-z0-9_.-]+)?$")
RUN_ID_TIMESTAMP_RE = re.compile(r"^(?P<date>\d{8})-(?P<time>\d{6})(?:-|$)")
GARBLED_QUESTION_MARK_RE = re.compile(r"[?？]{3,}")
SOURCE_VERSION_COMPATIBILITY_MATRIX = {
    "source_version_evidence": {
        "min_plugin_version": "1.0.60",
        "description": "source.json records plugin, installed, remote, skill cache, and gate check versions",
    },
    "report_view_v1": {
        "min_plugin_version": "1.0.61",
        "description": "daily and weekly packages include reports/*.md plus materials/display/report_view.json",
    },
    "patch_view_v1": {
        "min_plugin_version": "1.0.62",
        "description": "patch packages include human-visible patch view fields",
    },
    "patch_ai_facts_v1": {
        "min_plugin_version": "1.0.62",
        "description": "patch packages include AI-usable patch facts evidence",
    },
    "split_report_skills": {
        "min_plugin_version": "1.0.63",
        "description": "daily, weekly, and patch intake are exposed as separate member skills",
    },
    "report_view_v2": {
        "min_plugin_version": "1.0.63",
        "description": "daily and weekly report_view contains the v2 UI read model",
    },
    "report_view_project_ledger_v1": {
        "min_plugin_version": "1.0.71",
        "description": "weekly report_view contains member-maintained project ledgers for personal weekly report structure and management aggregation",
    },
    "report_project_identity_v1": {
        "min_plugin_version": "1.0.77",
        "description": "daily and weekly reports use company project identity rules instead of source directory or branch names as project conclusions",
    },
    "report_project_customer_required_v1": {
        "min_plugin_version": "1.0.79",
        "description": "daily and weekly report packages must carry recognized company project names and customer names before upload",
    },
    "report_view_human_v1": {
        "min_plugin_version": "1.0.79",
        "description": "daily and weekly report_view uses akbs-report-view-human-v1 with material_name/material_summary and project blocks",
    },
    "weekly_effective_report_facts_v1": {
        "min_plugin_version": "1.0.132",
        "description": "weekly reports use current effective daily/weekly facts, replacement leaves, and explicit fact completion",
    },
    "weekly_requirement_taxonomy_v1": {
        "min_plugin_version": "1.0.133",
        "description": "weekly reports classify migration as an implementation method, reserve BSP for explicit outstanding ownership, and reject BSP completions",
    },
    "weekly_work_type_v1": {
        "min_plugin_version": "1.0.150",
        "description": "weekly reports require Patch/App scope typing, App names, separate count semantics, and scope-level main/collaborator aggregation",
    },
    "daily_work_type_v1": {
        "min_plugin_version": "1.0.151",
        "description": "daily reports require Patch/App scope typing, conditional App names, and scope-bound work items",
    },
    "daily_work_type_inference_v1": {
        "min_plugin_version": "1.0.152",
        "description": "daily reports infer Patch/App from explicit or high-confidence development evidence and ask only for unresolved scope facts",
    },
    "report_document_work_v1": {
        "min_plugin_version": "1.0.153",
        "description": "daily and weekly reports preserve standalone Document work without fabricated project or customer identity",
    },
    "daily_no_focus_marker_v1": {
        "min_plugin_version": "1.0.154",
        "description": "daily reports preserve an explicit no-next-day-focus answer as the visible marker 无",
    },
    "weekly_project_ledger_v2": {
        "min_plugin_version": "1.0.156",
        "description": "weekly reports bind the effective previous-week scope, restrict project changes to the main member, and separate Android remaining from BSP tracking",
    },
    "report_scope_containers_v1": {
        "min_plugin_version": "1.0.158",
        "description": "project-bound work uses projects, non-project Doc uses documents, and non-project Other uses standalone_work; GMS is project-only",
    },
    "daily_attention_fields_v1": {
        "min_plugin_version": "1.0.159",
        "description": "daily report scopes carry optional key points and dependency or coordination arrays that weekly intake uses as scoped candidates",
    },
    "daily_tomorrow_plan_v1": {
        "min_plugin_version": "1.0.161",
        "description": "daily reports separate independent project, document, and standalone tomorrow plans from actual today work",
    },
    "gms_report_progress_v1": {
        "min_plugin_version": "1.0.163",
        "description": "GMS report scopes carry release type, target, independent self-test rounds, and independent submission attempts",
    },
    "report_render_binding_v1": {
        "min_plugin_version": "1.0.151",
        "description": "daily and weekly facts bind deterministic Markdown and report_view output before submission",
    },
    "report_customer_hierarchy_v1": {
        "min_plugin_version": "1.0.134",
        "description": "daily and weekly report projects preserve direct customer and optional downstream customer as separate identity levels",
    },
    "report_markdown_project_emphasis_v1": {
        "min_plugin_version": "1.0.135",
        "description": "daily and weekly Markdown bold every project-name occurrence while structured report fields remain plain text",
    },
    "patch_package_unification_v1": {
        "min_plugin_version": "1.0.139",
        "description": "framework changes upload as one immutable patch package",
    },
    "queue_information_completion_v1": {
        "min_plugin_version": "1.0.139",
        "description": "lightweight queue gaps append metadata or non-patch assets to the existing patch package",
    },
    "patch_package_subject_v2": {
        "min_plugin_version": "1.0.140",
        "description": "patch_package_id remains the sole business subject across queue and main while package_key is source-only",
    },
    "verification_acceptance_v2": {
        "min_plugin_version": "1.0.142",
        "description": "feature acceptance is versioned and cannot be inferred from build or delivery PASS evidence",
    },
}
DEFAULT_SOURCE_VERSION_CAPABILITIES = ("source_version_evidence",)
VALID_FRAMEWORK_PLATFORMS = {"mtk", "rk", "unisoc"}
PROJECT_PLATFORM_CODES = {
    "mtk": "M",
    "rk": "R",
    "unisoc": "U",
}
PLATFORM_PREFIX_ALIASES = {
    "mtk": "mtk",
    "rk": "rk",
    "unisoc": "unisoc",
    "sprd": "unisoc",
    "u": "unisoc",
}
PLATFORM_TOKEN_RE = re.compile(r"^(mtk|rk|unisoc|sprd|u)(\d{1,2})(?:-|$)", re.I)
PLATFORM_ARG_TOKEN_RE = re.compile(r"^(mtk|rk|unisoc|sprd|u)(\d{1,2})$", re.I)
PLATFORM_TOKEN_SEARCH_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:platform\s*[:=]\s*)?(mtk|rk|unisoc|sprd|u)(\d{1,2})(?![A-Za-z0-9])",
    re.I,
)
VERSION_ONLY_TOKEN_RE = re.compile(r"^(?:android|app)(\d{1,2})(?:-|$)", re.I)

PROJECT_MODEL_RE = re.compile(
    r"(?<![A-Z0-9])(?P<base>TV[DEAI]\d{2}[A-Z0-9]{2}[MRU]\d?|TVI[A-Z0-9]{5}[A-Z0-9]?|TVE8402)"
    r"(?P<branch_suffix>(?:_[A-Z0-9]+)*)(?![A-Z0-9_])",
    re.I,
)
PROJECT_SHORT_MODEL_RE = re.compile(
    r"(?<![A-Z0-9])(?P<base>TV[DEAI]\d{2}[A-Z0-9]{2})"
    r"(?P<branch_suffix>(?:_[A-Z0-9]+)*)(?![A-Z0-9_])",
    re.I,
)
PROJECT_FIELD_RE = re.compile(r"TV[DEAI]\d{2}[A-Z0-9]{2}[MRU]\d?|TVI[A-Z0-9]{5}[A-Z0-9]?", re.I)
PROJECT_SHORT_FIELD_RE = re.compile(r"TV[DEAI]\d{2}[A-Z0-9]{2}", re.I)
PROJECT_ANCHOR_RE = PROJECT_MODEL_RE
PROJECT_ALIASES = {"TVE8402": "TVE8402M"}

DAILY_BUNDLE_SUMMARY_RE = re.compile(
    r"(?:今日|本日|当天)补丁|补丁合集|(?:今日|本日|当天).*?(?:\d+\s*(?:个|项|份)|多个|若干).*?补丁"
)
MULTI_FEATURE_COLLECTION_RE = re.compile(
    r"(?:多功能混包|多个独立(?:功能|需求|补丁)|多个(?:功能|需求|补丁).*?(?:合集|汇总|整理)|"
    r"[两二三四五六七八九十]\s*项功能|\d+\s*项功能|\d+\s*个独立(?:功能|需求|补丁))"
)
LISTED_FEATURE_PACKAGE_RE = re.compile(r"[^，。；\n]+、[^，。；\n]+(?:，)?(?:以及|和)[^，。；\n]+补丁包")

REUSE_DECISIONS = {"reuse", "adapt", "reference_only", "not_applicable", "not_found", "unknown"}
WORKFLOW_CONTRACTS = {
    "current_codex_skill",
    "manual_import",
    "historical_import",
}
PRE_CHANGE_SEARCH_WORKFLOW_CONTRACTS = {"current_codex_skill"}
CURATION_TEXT_FIELD_ORDER = {
    "patch_asset_correction": 0,
    "function_split": 1,
    "patch_companion_readme": 2,
}
FUNCTION_SPLIT_MARKERS = (
    "按日期聚合",
    "多功能混包",
    "多个独立功能",
    "一个补丁包只能对应一个功能",
    "按功能拆分",
    "今日补丁合集",
    "功能拆分不足",
)
DECISIVE_FUNCTION_SPLIT_MARKERS = (
    "按日期聚合",
    "多功能混包",
    "多个独立功能",
    "一个补丁包只能对应一个功能",
    "今日补丁合集",
    "功能拆分不足",
)
PATCH_ASSET_CORRECTION_MARKERS = (
    "补丁资产修正",
    "patch asset correction",
    "补丁资产被污染",
    "补丁资产污染",
    "补丁资产（patch asset）存在",
    "补丁资产（patch asset）疑似",
    "补丁资产仍使用",
    "功能范围与补丁锚点不一致",
    "混杂 diff",
    "不能证明受控平台",
    "受控平台（platform）边界",
    "非受控前缀",
    "干净补丁资产",
    "脏工作树",
    "干净工作树重新采集",
    "重新采集同一功能补丁包",
    "重新采集干净补丁",
    "重新采集干净单功能",
    "重新捕获只包含本功能",
)
PATCH_ASSET_SUPPRESSES_FUNCTION_SPLIT_MARKERS = (
    "不是按功能拆分",
    "不是功能拆分",
    "不是 function split",
    "not function split",
    "脏工作树",
    "干净工作树",
    "混杂 diff",
    "重新采集干净补丁",
    "重新采集同一功能",
    "重新采集干净单功能",
    "重新捕获只包含本功能",
    "干净补丁资产",
)
PATCH_COMPANION_README_MARKERS = ("补丁配套说明", "todo", "模板内容", "模板化")


def normalize_android_version(platform: str, version: str) -> str:
    platform = str(platform or "").strip().lower()
    version = str(version or "").strip().lower()
    if platform == "rk" and version in {"71", "90"}:
        return {"71": "7.1", "90": "9.0"}[version]
    return version.lstrip("0") or version


def is_valid_platform_value(value: str) -> bool:
    value = str(value or "").strip().lower()
    return value in VALID_FRAMEWORK_PLATFORMS or value == "unknown"


def valid_framework_platform(value: str) -> bool:
    return str(value or "").strip().lower() in VALID_FRAMEWORK_PLATFORMS


def patch_asset_name_prefix(value: Any) -> str:
    name = Path(str(value or "")).name
    if not name.lower().endswith(".patch"):
        return ""
    if "-" not in name:
        return ""
    return name.split("-", 1)[0]


def has_uncontrolled_patch_asset_prefix(value: Any) -> bool:
    prefix = patch_asset_name_prefix(value)
    if not prefix:
        return False
    if parse_known_platform_token(Path(str(value or "")).name) != ("", ""):
        return False
    return not valid_project_model(prefix)


def has_uncontrolled_app_patch_asset_prefix(value: Any) -> bool:
    return has_uncontrolled_patch_asset_prefix(value)


def is_valid_android_version_value(value: str) -> bool:
    value = str(value or "").strip().lower()
    return value == "unknown" or bool(re.fullmatch(r"\d+(?:\.\d+)?", value))


def valid_android_version(value: str) -> bool:
    value = str(value or "").strip().lower()
    return value not in {"", "unknown"} and bool(re.fullmatch(r"\d+(?:\.\d+)?", value))


def text_field_quality_errors(fields: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for label, raw_value in fields.items():
        if raw_value is None:
            continue
        value = str(raw_value).strip()
        if not value:
            continue
        if GARBLED_QUESTION_MARK_RE.search(value):
            errors.append(f"{label} 含有问号乱码（garbled question marks），请重新生成包，不能上传损坏文本。")
    return errors


CAMERA_TEMPLATE_MARKERS = (
    "camera",
    "cameraservice",
    "camera2",
    "camera hal",
    "相机预览",
    "相机场景",
    "相机权限",
    "相机行为",
    "扫码",
    "拍照",
)


def text_has_camera_semantics(value: Any) -> bool:
    normalized = str(value or "").lower()
    return any(marker in normalized for marker in CAMERA_TEMPLATE_MARKERS)


def template_leak_errors(
    *,
    summary: Any = "",
    problem: Any = "",
    solution: Any = "",
    patch_paths: list[Any] | None = None,
    modified_files: list[Any] | None = None,
) -> list[str]:
    evidence_text = " ".join([str(problem or ""), str(solution or "")])
    if not text_has_camera_semantics(evidence_text):
        return []
    context_text = " ".join(
        [
            str(summary or ""),
            *(str(item or "") for item in patch_paths or []),
            *(str(item or "") for item in modified_files or []),
        ]
    )
    if text_has_camera_semantics(context_text):
        return []
    return [
        "结构化证据疑似模板文本泄漏（template leak）："
        "case 或 patch_problem_summary 出现相机/CameraService/Camera2 模板内容，"
        "但 manifest 摘要、补丁文件名和修改文件都没有相机语义。"
        "请重新生成补丁说明和问题/方案证据，不能把无关模板文本随包上传。"
    ]


def run_id_local_datetime(run_id: Any) -> dt.datetime | None:
    match = RUN_ID_TIMESTAMP_RE.match(str(run_id or "").strip())
    if not match:
        return None
    try:
        return dt.datetime.strptime(match.group("date") + match.group("time"), "%Y%m%d%H%M%S")
    except ValueError:
        return None


def future_run_id_errors(run_id: Any, *, now: dt.datetime, tolerance_seconds: int = 60) -> list[str]:
    run_at = run_id_local_datetime(run_id)
    if run_at is None:
        return []
    now_local = now.astimezone().replace(tzinfo=None) if now.tzinfo else now
    if run_at <= now_local + dt.timedelta(seconds=tolerance_seconds):
        return []
    return [
        "run_id 时间晚于服务器当前时间（future upload timestamp）："
        f"{run_at:%Y-%m-%d %H:%M:%S} > {now_local:%Y-%m-%d %H:%M:%S}。"
        "请同步成员机器时间后重新生成包。"
    ]


def parse_known_platform_token(value: str) -> tuple[str, str]:
    token = Path(str(value or "")).name.lower()
    match = PLATFORM_TOKEN_RE.match(token)
    if not match:
        return "", ""
    prefix, raw_version = match.groups()
    platform = PLATFORM_PREFIX_ALIASES[prefix.lower()]
    return platform, normalize_android_version(platform, raw_version)


def find_platform_tokens(value: Any) -> list[tuple[str, str]]:
    text = str(value or "")
    tokens: list[tuple[str, str]] = []
    for match in PLATFORM_TOKEN_SEARCH_RE.finditer(text):
        prefix, raw_version = match.groups()
        platform = PLATFORM_PREFIX_ALIASES[prefix.lower()]
        token = (platform, normalize_android_version(platform, raw_version))
        if token not in tokens:
            tokens.append(token)
    return tokens


def parse_version_only_token(value: str) -> str:
    token = Path(str(value or "")).name.lower()
    match = VERSION_ONLY_TOKEN_RE.match(token)
    if not match:
        return ""
    raw_version = match.group(1)
    return raw_version.lstrip("0") or raw_version


def parse_platform_token(patch_entries: list[dict[str, Any]]) -> tuple[str, str]:
    explicit: list[tuple[str, str]] = []
    filename_tokens: list[tuple[str, str]] = []
    version_only: list[str] = []
    for item in patch_entries:
        platform = str(item.get("platform") or "").strip().lower()
        android_version = str(item.get("android_version") or "").strip().lower()
        if platform in VALID_FRAMEWORK_PLATFORMS and is_valid_android_version_value(android_version) and android_version != "unknown":
            explicit.append((platform, android_version))
        for key in ("platform_token", "path", "id", "name"):
            parsed = parse_known_platform_token(str(item.get(key) or ""))
            if parsed != ("", ""):
                filename_tokens.append(parsed)
        for key in ("path", "id", "name"):
            version = parse_version_only_token(str(item.get(key) or ""))
            if version:
                version_only.append(version)

    for candidates in (explicit, filename_tokens):
        unique = sorted(set(candidates))
        if len(unique) == 1:
            return unique[0]
        if len(unique) > 1:
            return "unknown", "unknown"

    unique_versions = sorted(set(version_only))
    if len(unique_versions) == 1:
        return "unknown", unique_versions[0]
    return "unknown", "unknown"


def parse_platform_input(value: str) -> tuple[str, str, str]:
    """Return a transient filename prefix plus canonical platform/version fields."""
    token = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip().lower())
    token = re.sub(r"-+", "-", token).strip("-._")
    match = PLATFORM_ARG_TOKEN_RE.fullmatch(token)
    if not match:
        return "", "", ""
    prefix, raw_version = match.groups()
    if prefix.lower() not in {"mtk", "rk", "unisoc"}:
        return "", "", ""
    platform = PLATFORM_PREFIX_ALIASES[prefix.lower()]
    android_version = normalize_android_version(platform, raw_version)
    filename_version = raw_version.lstrip("0") or raw_version
    return f"{platform}{filename_version}", platform, android_version


def apply_platform_overrides(
    inferred_platform: str,
    inferred_android_version: str,
    platform_override: str = "",
    android_version_override: str = "",
) -> tuple[str, str]:
    platform = str(inferred_platform or "unknown").strip().lower() or "unknown"
    android_version = str(inferred_android_version or "unknown").strip().lower() or "unknown"
    explicit_platform = str(platform_override or "").strip().lower()
    explicit_android_version = str(android_version_override or "").strip().lower()
    if explicit_platform:
        if not is_valid_platform_value(explicit_platform):
            raise SystemExit(f"--platform 只能使用 mtk/rk/unisoc/unknown，当前为: {explicit_platform}")
        platform = explicit_platform
    if explicit_android_version:
        android_version = normalize_android_version(platform, explicit_android_version)
        if not is_valid_android_version_value(android_version):
            raise SystemExit(f"--android-version 必须是明确数字版本或 unknown，当前为: {explicit_android_version}")
    return platform, android_version


def trusted_project_platform_code(platform: str) -> str:
    normalized = PLATFORM_PREFIX_ALIASES.get(str(platform or "").strip().lower(), str(platform or "").strip().lower())
    return PROJECT_PLATFORM_CODES.get(normalized, "")


def trusted_tvi_chip_code(platform: str, evidence_text: str = "") -> str:
    evidence = str(evidence_text or "").lower()
    if re.search(r"(?<![a-z0-9])x86(?:_64)?(?![a-z0-9])", evidence):
        return "X"
    normalized = PLATFORM_PREFIX_ALIASES.get(str(platform or "").strip().lower(), str(platform or "").strip().lower())
    if normalized in VALID_FRAMEWORK_PLATFORMS:
        return "A"
    return ""


def complete_company_project_with_platform(value: str, platform: str, evidence_text: str = "") -> str:
    project = str(value or "").strip().upper()
    if not PROJECT_SHORT_FIELD_RE.fullmatch(project):
        return ""
    if project.startswith("TVI"):
        tvi_chip_code = trusted_tvi_chip_code(platform, evidence_text)
        if not tvi_chip_code:
            return ""
        candidate = project + tvi_chip_code
        if not PROJECT_FIELD_RE.fullmatch(candidate):
            return ""
        return candidate
    platform_code = trusted_project_platform_code(platform)
    if not platform_code:
        return ""
    candidate = project + platform_code
    if not PROJECT_FIELD_RE.fullmatch(candidate):
        return ""
    return candidate


def canonical_company_project(value: str, platform: str = "") -> str:
    project = str(value or "").strip().upper()
    project = PROJECT_ALIASES.get(project, project)
    if PROJECT_FIELD_RE.fullmatch(project):
        return project
    return complete_company_project_with_platform(project, platform) or project


def split_company_project(value: str, platform: str = "") -> tuple[str, str]:
    project = canonical_company_project(value, platform)
    match = PROJECT_FIELD_RE.fullmatch(project)
    if not match:
        return "", ""
    return match.group(0).upper(), ""


def valid_project_model(value: str, platform: str = "") -> bool:
    base, _suffix = split_company_project(value, platform)
    return bool(base)


def parse_company_project(value: str, platform: str = "") -> dict[str, Any]:
    base, suffix = split_company_project(value, platform)
    soc_code = base[7:8] if base else ""
    extension_code = base[8:] if len(base) > 8 else ""
    return {
        "base_model": base,
        "product_prefix": base[:2],
        "form_code": base[2:3],
        "mold_code": base[3:7],
        "soc_code": soc_code,
        "extension_code": extension_code,
        "suffix": suffix,
        "recognition_scope": "TVD/TVE/TVA/TVI",
        "company_rule_match": bool(base),
    }


def find_company_project(text: str, platform: str = "") -> str:
    match = PROJECT_MODEL_RE.search(str(text or "").upper())
    if match:
        return canonical_company_project(match.group("base"))
    platform_code = trusted_project_platform_code(platform)
    if not platform_code:
        return ""
    short_match = PROJECT_SHORT_MODEL_RE.search(str(text or "").upper())
    if not short_match:
        return ""
    return complete_company_project_with_platform(short_match.group("base"), platform, evidence_text=str(text or ""))


def find_company_projects(text: str, platform: str = "") -> list[str]:
    normalized_text = str(text or "").upper()
    projects = [canonical_company_project(match.group("base")) for match in PROJECT_MODEL_RE.finditer(normalized_text)]
    if trusted_project_platform_code(platform):
        projects.extend(
            completed
            for match in PROJECT_SHORT_MODEL_RE.finditer(normalized_text)
            for completed in [complete_company_project_with_platform(match.group("base"), platform, evidence_text=normalized_text)]
            if completed
        )
    return sorted(dict.fromkeys(projects))


def aggregate_package_scope_errors(text: str, patch_count: int = 0) -> list[str]:
    if not (
        DAILY_BUNDLE_SUMMARY_RE.search(text)
        or MULTI_FEATURE_COLLECTION_RE.search(text)
        or LISTED_FEATURE_PACKAGE_RE.search(text)
    ):
        return []
    count = f"当前约 {patch_count} 个补丁。" if patch_count else ""
    snippet = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(snippet) > 360:
        snippet = snippet[:180].rstrip() + " ... " + snippet[-180:].lstrip()
    detail = f"疑似聚合内容：{snippet}。" if snippet else ""
    return [
        f"补丁包（patch package）不能是无共同目标的聚合包（aggregate package）。{count}"
        f"{detail}"
        "请按功能拆分（function split）为多个新的补丁包；"
        "一个补丁包只能对应一个共同功能目标。"
    ]


def normalize_reuse_decision(value: Any) -> str:
    decision = str(value or "").strip().lower()
    return decision if decision in REUSE_DECISIONS else "unknown"


def workflow_requires_pre_change_search(workflow_contract: str) -> bool:
    contract = str(workflow_contract or "").strip().lower()
    return contract in PRE_CHANGE_SEARCH_WORKFLOW_CONTRACTS


def search_results_need_usage_decision(results: Any) -> bool:
    if not isinstance(results, list) or not any(str(item).strip() for item in results):
        return False
    text = "\n".join(str(item) for item in results).lower()
    return not any(token in text for token in ("未发现", "未命中", "no reuse", "no candidate", "not found"))


def classify_pre_change_search(
    search_payload: dict[str, Any] | None,
    *,
    workflow_contract: str = "",
    package_status: str = "",
) -> dict[str, Any]:
    payload = search_payload if isinstance(search_payload, dict) else {}
    decision = normalize_reuse_decision(payload.get("reuse_decision") or payload.get("decision") or "")
    searched = bool(payload.get("searched"))
    results = payload.get("results")
    has_results = search_results_need_usage_decision(results)
    requires_search = workflow_requires_pre_change_search(workflow_contract)
    can_complete_before_upload = requires_search and searched and has_results and decision == "unknown"
    requires_overlap = not searched or not requires_search
    return {
        "searched": searched,
        "decision": decision,
        "has_results": has_results,
        "requires_pre_change_search": requires_search,
        "member_can_complete_before_upload": can_complete_before_upload,
        "missing_field": "search_usage" if can_complete_before_upload else "",
        "requires_post_change_overlap_check": requires_overlap,
        "validity_score_effect": "search_loop_score_possible" if searched and decision != "unknown" else "no_search_loop_score",
        "package_status": str(package_status or "").strip().lower(),
    }


def current_plugin_version() -> str:
    """Return the enclosing plugin release, or empty for a rules-only snapshot."""
    try:
        manifest_path = Path(__file__).resolve().parents[2] / ".codex-plugin" / "plugin.json"
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            version = str(payload.get("version") or "").strip()
            if version:
                return version
    except Exception:
        pass
    return ""


def source_version_compatibility_matrix() -> dict[str, dict[str, str]]:
    return {key: dict(value) for key, value in SOURCE_VERSION_COMPATIBILITY_MATRIX.items()}


def _version_parts(version: str) -> tuple[int, int, int, int]:
    normalized = str(version or "").strip()
    core = re.split(r"[-+]", normalized, maxsplit=1)[0]
    parts = [int(part) for part in core.split(".") if part.isdigit()]
    while len(parts) < 4:
        parts.append(0)
    return (parts[0], parts[1], parts[2], parts[3])


def _version_at_least(version: str, minimum: str) -> bool:
    return _version_parts(version) >= _version_parts(minimum)


def source_version_errors(
    payload: dict[str, Any] | None,
    *,
    expected_version: str | None = None,
    required_capabilities: list[str] | tuple[str, ...] | None = None,
) -> list[str]:
    source = payload if isinstance(payload, dict) else {}
    plugin_name = str(source.get("plugin_name") or "").strip()
    plugin_version = str(source.get("plugin_version") or "").strip()
    skill_version = str(source.get("skill_version") or "").strip()
    errors: list[str] = []
    if plugin_name not in {"akbs-member-ops", "android-framework-ops"}:
        errors.append("source evidence plugin_name must be akbs-member-ops or the legacy android-framework-ops")
    plugin_version_valid = bool(SEMVER_RE.fullmatch(plugin_version))
    skill_version_valid = bool(SEMVER_RE.fullmatch(skill_version))
    if not plugin_version_valid:
        errors.append("source evidence plugin_version is required for strict new uploads")
    if not skill_version_valid:
        errors.append("source evidence skill_version is required for strict new uploads")
    capabilities = tuple(required_capabilities or DEFAULT_SOURCE_VERSION_CAPABILITIES)
    matrix = SOURCE_VERSION_COMPATIBILITY_MATRIX
    for capability in capabilities:
        spec = matrix.get(str(capability or "").strip())
        if spec is None:
            errors.append(f"source evidence required capability is unknown: {capability}")
            continue
        minimum = spec["min_plugin_version"]
        if plugin_version_valid and not _version_at_least(plugin_version, minimum):
            errors.append(
                f"source evidence plugin_version {plugin_version} does not satisfy {capability} minimum {minimum}"
            )
        if skill_version_valid and not _version_at_least(skill_version, minimum):
            errors.append(
                f"source evidence skill_version {skill_version} does not satisfy {capability} minimum {minimum}"
            )
    version_check = source.get("plugin_version_check") if isinstance(source.get("plugin_version_check"), dict) else {}
    status = str(version_check.get("status") or version_check.get("result") or "").strip()
    if status == "SESSION_CACHE_STALE" or bool(version_check.get("blocking")):
        errors.append(f"source evidence plugin_version_check is blocking: {status or 'UNKNOWN'}")
    return errors


def patch_upload_gate_errors(manifest: dict[str, Any] | None, *, allow_incomplete: bool = False) -> list[str]:
    payload = manifest if isinstance(manifest, dict) else {}
    if payload.get("package_kind") != "framework_change":
        return []
    if allow_incomplete:
        return []
    package_status = str(payload.get("package_status") or "").strip().lower()
    errors: list[str] = []
    if package_status == "validated":
        return errors
    errors.append(
        "普通补丁包（patch package）上传必须是已验证（validated）状态。"
        f"当前 package_status={package_status or 'missing'}。"
        "候选（candidate）、草稿（draft）、失败（failed）或阻塞（blocked）工作请记录到日报包（daily report package）"
        "或继续在成员本机补齐证据；完成构建和设备/等价验证后再重新生成并上传补丁包。"
    )
    return errors


def classify_patch_asset_names(paths: list[Any]) -> dict[str, Any]:
    uncontrolled = [str(path) for path in paths if has_uncontrolled_patch_asset_prefix(path)]
    issue_codes: list[str] = []
    if uncontrolled:
        issue_codes.append("patch_asset_pollution")
    return {
        "status": "fail" if issue_codes else "pass",
        "issue_codes": issue_codes,
        "uncontrolled_prefixes": uncontrolled,
        "uncontrolled_app_prefixes": uncontrolled,
    }


def classify_function_scope(text: str, patch_count: int = 0) -> dict[str, Any]:
    errors = aggregate_package_scope_errors(text, patch_count)
    if errors:
        return {
            "status": "fail",
            "issue_codes": ["aggregate_package"],
            "messages": errors,
        }
    return {
        "status": "pass",
        "issue_codes": [],
        "messages": [],
    }


def curation_text_requires_patch_asset_correction(text: str) -> bool:
    normalized = str(text or "").lower()
    return any(marker in normalized for marker in PATCH_ASSET_CORRECTION_MARKERS)


def curation_text_requires_function_split(text: str) -> bool:
    normalized = str(text or "").lower()
    return any(marker in normalized for marker in FUNCTION_SPLIT_MARKERS)


def strip_conditional_function_split_caveats(text: str) -> str:
    stripped = str(text or "")
    for pattern in (
        r"如果实际是[^。；\n]*(?:多个独立功能|多功能混包)[^。；\n]*(?:按功能拆分|function split)[^。；\n]*[。；]?",
        r"如果[^。；\n]*(?:多个独立功能|多功能混包)[^。；\n]*(?:按功能拆分|function split)[^。；\n]*[。；]?",
    ):
        stripped = re.sub(pattern, "", stripped)
    return stripped


def curation_text_patch_asset_suppresses_function_split(text: str) -> bool:
    if not curation_text_requires_patch_asset_correction(text):
        return False
    decisive_text = strip_conditional_function_split_caveats(text).lower()
    if any(marker in decisive_text for marker in DECISIVE_FUNCTION_SPLIT_MARKERS):
        return False
    return any(marker in decisive_text for marker in PATCH_ASSET_SUPPRESSES_FUNCTION_SPLIT_MARKERS)


def curation_text_missing_fields(text: str) -> list[str]:
    normalized = str(text or "").lower()
    fields: list[str] = []
    patch_asset_required = curation_text_requires_patch_asset_correction(normalized)
    if (
        curation_text_requires_function_split(normalized)
        and not curation_text_patch_asset_suppresses_function_split(normalized)
    ):
        fields.append("function_split")
    if patch_asset_required:
        fields.append("patch_asset_correction")
    if any(marker in normalized for marker in PATCH_COMPANION_README_MARKERS):
        fields.append("patch_companion_readme")
    return sorted(dict.fromkeys(fields), key=lambda field: (CURATION_TEXT_FIELD_ORDER.get(field, 99), field))
