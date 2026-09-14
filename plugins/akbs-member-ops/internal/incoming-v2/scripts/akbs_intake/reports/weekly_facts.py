from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from akbs_member_ops.http_client import HttpClientFailure, request_json
from akbs_member_ops.knowledge_rules import find_company_project

from ..config import expanded_path, local_now, parse_bool, submission_api_base_url
from ..io_utils import read_json_file
from ..report_sessions import (
    clean_report_customer_name,
    normalize_report_customer_context,
    report_customer_context_for_project,
)
from .common import iter_local_manifests, replacement_run_id
from .document_work import (
    DOCUMENT_WORK_TYPE,
    clean_document_name,
    normalize_standalone_work_type,
    standalone_work_name,
    validate_weekly_documents,
    validate_weekly_standalone_work,
)
from .gms import GMS_CURRENT_FIELDS, normalize_gms_fields, validate_gms_fields
from .scope import PROJECT_WORK_TYPES, report_scope_key
from .weekly_ledger import (
    BUSINESS_COUNT_KEYS,
    LEDGER_CHANGE_KEYS,
    WEEKLY_LEDGER_SCHEMA,
    matching_previous_scope,
    normalize_weekly_ledger,
    validate_v5_project_ledger,
)


WEEKLY_FACTS_SCHEMA = "akbs-weekly-work-facts-v6"
PREVIOUS_WEEKLY_FACTS_SCHEMA = "akbs-weekly-work-facts-v5"
LEGACY_WEEKLY_FACTS_SCHEMA = "akbs-weekly-work-facts-v4"
OLDEST_WEEKLY_FACTS_SCHEMA = "akbs-weekly-project-facts-v3"
WEEKLY_FACT_SOURCES_SCHEMA = "akbs-weekly-fact-sources-v3"
LEGACY_WEEKLY_FACT_SOURCES_SCHEMA = "akbs-weekly-fact-sources-v2"
OLDEST_WEEKLY_FACT_SOURCES_SCHEMA = "akbs-weekly-fact-sources-v1"
COUNT_KEYS = ("demand", "migration", "bug", "bsp")
ALLOWED_PROJECT_ROLES = {"主责", "协作"}
ALLOWED_SOURCES = {"CR", "TL", "PM", "TE", "BSP"}
MISSING_VALUES = {"", "unknown", "需成员确认", "需成员补充", "待确认"}
EMPTY_ITEM_VALUES = {
    "无",
    "无。",
    "暂无",
    "暂无。",
    "无下周计划",
    "暂无下周计划",
    "暂无明确完成项",
    "无明确剩余项",
    "无超过 3 天无进展事项。",
    "无外部依赖事项。",
}
COUNT_PART_RE = re.compile(
    r"(需求|新增功能|功能添加|移植|Buglist|Bug|BSP)\s*(\d+)",
    re.IGNORECASE,
)
LEGACY_CUSTOM_RE = re.compile(r"(?:定制(?:需求)?)\s*(\d+)", re.IGNORECASE)
LEGACY_CUSTOM_KEYS = {"custom", "定制", "定制需求", "feature_add"}


@dataclass(frozen=True)
class WeeklyFactsResult:
    projects: list[dict[str, Any]]
    documents: list[dict[str, Any]]
    standalone_work: list[dict[str, Any]]
    evidence: dict[str, Any]


def clean_text(value: Any, fallback: str = "") -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text or fallback


def prefer_fact(value: Any, fallback: str) -> str:
    text = clean_text(value)
    return fallback if text in MISSING_VALUES else text


def clean_list(value: Any) -> list[str]:
    rows = value if isinstance(value, list) else [value] if value else []
    result: list[str] = []
    for row in rows:
        text = clean_text(row)
        if not text or text in EMPTY_ITEM_VALUES or text in result:
            continue
        result.append(text)
    return result


def attention_scope_key(
    work_type: Any,
    app_name: Any = "",
    gms_release_type: Any = "",
    gms_target: Any = "",
) -> tuple[str, str, str, str]:
    normalized_type = clean_text(work_type)
    normalized_app = clean_text(app_name).casefold() if normalized_type == "App" else ""
    release_type = clean_text(gms_release_type).upper() if normalized_type == "GMS" else ""
    target = clean_text(gms_target).casefold() if normalized_type == "GMS" else ""
    return normalized_type, normalized_app, release_type, target


def daily_scope_attention(daily_meta: dict[str, Any], row: dict[str, Any]) -> dict[str, list[str]]:
    scopes = daily_meta.get("attention_by_scope")
    if not isinstance(scopes, dict):
        return {"key_points": [], "dependencies": []}
    value = scopes.get(
        attention_scope_key(
            row.get("work_type"),
            row.get("app_name"),
            row.get("gms_release_type"),
            row.get("gms_target"),
        )
    )
    if not isinstance(value, dict):
        return {"key_points": [], "dependencies": []}
    return {
        "key_points": clean_list(value.get("key_points")),
        "dependencies": clean_list(value.get("dependencies")),
    }


def daily_scope_plan(daily_meta: dict[str, Any], row: dict[str, Any]) -> list[str]:
    scopes = daily_meta.get("plan_by_scope")
    if isinstance(scopes, dict):
        value = scopes.get(
            attention_scope_key(
                row.get("work_type"),
                row.get("app_name"),
                row.get("gms_release_type"),
                row.get("gms_target"),
            )
        )
        if value is not None:
            return clean_list(value)
    return clean_list(daily_meta.get("latest_focus"))


def append_attention_values(target: dict[str, Any], field: str, value: Any) -> None:
    rows = target.setdefault(field, [])
    if not isinstance(rows, list):
        rows = []
        target[field] = rows
    for item in clean_list(value):
        add_unique(rows, item)


def weekly_summary(value: Any, completed_items: list[str], remaining_items: list[str]) -> str:
    explicit = clean_text(value)
    if explicit and explicit not in MISSING_VALUES:
        return explicit
    if completed_items:
        return "本周完成" + "、".join(completed_items[:3]) + ("等事项。" if len(completed_items) > 3 else "。")
    if remaining_items:
        return "本周持续推进" + "、".join(remaining_items[:3]) + ("等事项。" if len(remaining_items) > 3 else "。")
    return "本周无新增处理事项。"


def count_field_present(value: Any) -> bool:
    return isinstance(value, dict) or bool(clean_text(value))


def zero_counts() -> dict[str, int]:
    return {key: 0 for key in COUNT_KEYS}


def normalize_counts(value: Any) -> dict[str, int]:
    if isinstance(value, dict):
        result = zero_counts()
        aliases = {
            "demand": "demand",
            "requirement": "demand",
            "需求": "demand",
            "新增功能": "demand",
            "功能添加": "demand",
            "migration": "migration",
            "port": "migration",
            "移植": "migration",
            "feature_port": "migration",
            "bug": "bug",
            "Bug": "bug",
            "buglist": "bug",
            "Buglist": "bug",
            "bsp": "bsp",
            "BSP": "bsp",
        }
        for raw_key, raw_value in value.items():
            key = aliases.get(str(raw_key))
            if not key:
                continue
            try:
                result[key] = max(0, int(raw_value))
            except (TypeError, ValueError):
                continue
        return result
    text = clean_text(value)
    result = zero_counts()
    for label, raw_count in COUNT_PART_RE.findall(text):
        normalized_label = label.lower()
        if normalized_label in {"需求", "新增功能", "功能添加"}:
            key = "demand"
        elif normalized_label == "移植":
            key = "migration"
        elif normalized_label in {"bug", "buglist"}:
            key = "bug"
        else:
            key = "bsp"
        result[key] += int(raw_count)
    return result


def legacy_custom_count(value: Any) -> int:
    if isinstance(value, dict):
        total = 0
        for raw_key, raw_value in value.items():
            if str(raw_key) not in LEGACY_CUSTOM_KEYS:
                continue
            try:
                total += max(0, int(raw_value))
            except (TypeError, ValueError):
                continue
        return total
    return sum(int(raw_count) for raw_count in LEGACY_CUSTOM_RE.findall(clean_text(value)))


def count_total(counts: dict[str, int]) -> int:
    return sum(int(counts.get(key, 0) or 0) for key in COUNT_KEYS)


def scalar_count_present(value: Any) -> bool:
    return (isinstance(value, int) and not isinstance(value, bool)) or bool(clean_text(value))


def normalize_scalar_count(value: Any) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return max(0, value)
    match = re.fullmatch(r"(?:(?:共|本周完成|当前剩余)\s*)?(\d+)\s*项?", clean_text(value))
    return int(match.group(1)) if match else 0


def row_count(row: dict[str, Any], field: str) -> int:
    scalar_fields = {
        "requirement_structure_counts": "work_total",
        "completed_this_week_counts": "completed_this_week_total",
        "remaining_counts": "remaining_total",
    }
    if row.get("work_type") == "App":
        return int(row.get(scalar_fields[field], 0) or 0)
    return count_total(normalize_counts(row.get(field)))


def item_category(text: str, *, completed: bool = False) -> str:
    lowered = text.lower()
    if not completed and re.search(r"\bBSP\b", text, re.IGNORECASE):
        return "bsp"
    if any(token in lowered for token in ("bug", "问题", "缺陷", "报错", "失败", "异常", "修复")):
        return "bug"
    if any(token in lowered for token in ("移植", "复用", "port")):
        return "migration"
    return "demand"


def progress_completed(value: Any) -> bool:
    text = clean_text(value)
    if any(token in text for token in ("阻塞", "失败", "未完成", "待处理", "处理中", "进行中", "待验证", "验证中")):
        return False
    return bool(re.search(r"(?:^|[^0-9])100\s*%|已完成|已解决|验证完成|测试通过|构建通过|修复完成", text))


def item_key(value: str) -> str:
    text = re.sub(r"TV[DEAI][A-Za-z0-9]+", "", value, flags=re.IGNORECASE)
    return re.sub(r"[^A-Za-z0-9\u4e00-\u9fff]+", "", text).lower()


def same_item(left: str, right: str) -> bool:
    a = item_key(left)
    b = item_key(right)
    if not a or not b:
        return False
    return a == b or (min(len(a), len(b)) >= 8 and (a in b or b in a))


def add_unique(rows: list[str], value: str) -> None:
    if value and not any(same_item(value, existing) for existing in rows):
        rows.append(value)


def unwrap_report_view(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    if value.get("kind") == "report_view" and isinstance(value.get("payload"), dict):
        return dict(value["payload"])
    return dict(value)


def report_item_view(item: dict[str, Any]) -> dict[str, Any]:
    for key in ("standard_view", "report_view"):
        view = unwrap_report_view(item.get(key))
        if view:
            return view
    return {}


def _api_enabled(config: dict[str, str]) -> bool:
    return parse_bool(config.get("weekly_history_api_enabled", "true"))


def _api_timeout(config: dict[str, str]) -> float:
    try:
        return max(0.5, min(10.0, float(config.get("weekly_history_api_timeout_seconds", "3"))))
    except ValueError:
        return 3.0


def fetch_current_report_items(config: dict[str, str], package_kind: str, month: str) -> list[dict[str, Any]]:
    query = urllib.parse.urlencode({"page": 1, "page_size": 100, "kind": package_kind, "month": month})
    url = f"{submission_api_base_url(config).rstrip('/')}/member/me/packages?{query}"
    request = urllib.request.Request(
        url,
        method="GET",
        headers={"Accept": "application/json", "X-AKBS-User": config["member_alias"]},
    )
    payload = request_json(request, timeout=_api_timeout(config))
    items = payload.get("items")
    if not isinstance(items, list):
        raise ValueError("AKBS member report history response missing items")
    return [item for item in items if isinstance(item, dict)]


def _manifest_report_view(package_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    files = manifest.get("files") if isinstance(manifest.get("files"), dict) else {}
    display = files.get("display") if isinstance(files.get("display"), list) else []
    for rel in display:
        path = package_dir / str(rel)
        if path.is_file():
            return unwrap_report_view(read_json_file(path))
    return {}


def local_current_report_items(
    config: dict[str, str],
    package_kind: str,
    identities: set[str],
) -> list[dict[str, Any]]:
    candidates: list[tuple[Path, dict[str, Any], str]] = []
    for bucket, package_dir, manifest in iter_local_manifests(config):
        if bucket != "submitted" or manifest.get("member_alias") != config.get("member_alias"):
            continue
        if manifest.get("package_kind") != package_kind:
            continue
        identity = str(manifest.get("date") if package_kind == "daily_trace" else manifest.get("week_range") or "")
        if identity not in identities:
            continue
        candidates.append((package_dir, manifest, identity))
    superseded = {replacement_run_id(manifest) for _, manifest, _ in candidates if replacement_run_id(manifest)}
    rows: list[dict[str, Any]] = []
    for package_dir, manifest, identity in candidates:
        run_id = str(manifest.get("run_id") or package_dir.name)
        if run_id in superseded:
            continue
        view = _manifest_report_view(package_dir, manifest)
        if not view:
            continue
        rows.append(
            {
                "package_key": f"local:{identity}:{run_id}",
                "package_kind": package_kind,
                "report_date": identity if package_kind == "daily_trace" else "",
                "week_range": identity if package_kind == "weekly_trace" else "",
                "report_view": view,
            }
        )
    return rows


def _target_months(dates: Iterable[dt.date]) -> list[str]:
    return sorted({date.strftime("%Y-%m") for date in dates})


def load_history(
    config: dict[str, str],
    start: dt.date,
    end: dt.date,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    current_dates = {start + dt.timedelta(days=offset) for offset in range((end - start).days + 1)}
    current_date_keys = {date.isoformat() for date in current_dates}
    previous_start = start - dt.timedelta(days=7)
    previous_end = end - dt.timedelta(days=7)
    previous_key = f"{previous_start:%Y%m%d}-{previous_end:%Y%m%d}"
    daily_items: list[dict[str, Any]] = []
    weekly_items: list[dict[str, Any]] = []
    api_errors: list[str] = []
    api_complete = _api_enabled(config)
    if api_complete:
        try:
            for month in _target_months(current_dates):
                daily_items.extend(fetch_current_report_items(config, "daily_trace", month))
            for month in _target_months((previous_start, previous_end)):
                weekly_items.extend(fetch_current_report_items(config, "weekly_trace", month))
        except (HttpClientFailure, ValueError) as error:
            api_complete = False
            code = error.result.code if isinstance(error, HttpClientFailure) else "invalid_success_response"
            api_errors.append(code)
    daily_items = [item for item in daily_items if clean_text(item.get("report_date")) in current_date_keys]
    weekly_items = [item for item in weekly_items if clean_text(item.get("week_range")) == previous_key]
    source = "akbs_api" if api_complete else "local_submitted"
    if not api_complete:
        daily_items = local_current_report_items(config, "daily_trace", current_date_keys)
        weekly_items = local_current_report_items(config, "weekly_trace", {previous_key})
    provenance = {
        "source": source,
        "api_errors": sorted(set(api_errors)),
        "daily_package_keys": sorted({clean_text(item.get("package_key")) for item in daily_items if item.get("package_key")}),
        "previous_weekly_package_keys": sorted({clean_text(item.get("package_key")) for item in weekly_items if item.get("package_key")}),
        "previous_week_range": previous_key,
    }
    return daily_items, weekly_items, provenance


def _weekly_project_row(value: dict[str, Any]) -> dict[str, Any]:
    project = find_company_project(clean_text(value.get("project"))) or clean_text(value.get("project"), "需成员补充项目名")
    customer = clean_text(value.get("customer") or value.get("customer_name"), "需成员补充客户名")
    downstream_customer = clean_text(
        value.get("downstream_customer")
        or value.get("customer_of_customer")
        or value.get("end_customer")
        or value.get("客户的客户")
    )
    raw_structure = value.get("requirement_structure_counts", value.get("requirement_structure"))
    raw_completed = value.get("completed_this_week_counts", value.get("completed_this_week"))
    raw_remaining = value.get("remaining_counts", value.get("remaining"))
    work_type = clean_text(value.get("work_type") or value.get("type"), "需成员确认")
    ledger = normalize_weekly_ledger(value.get("ledger"), work_type)
    raw_work_total = value.get("work_total")
    completed_items = clean_list(value.get("completed_items"))
    remaining_items = clean_list(value.get("remaining_items"))
    row = {
        "project": project,
        "customer": customer,
        "customer_name": customer,
        "downstream_customer": downstream_customer,
        "work_type": work_type,
        "app_name": clean_text(value.get("app_name")),
        "current_stage": clean_text(value.get("current_stage")),
        "project_role": clean_text(value.get("project_role"), "需成员确认"),
        "week_summary": weekly_summary(value.get("week_summary"), completed_items, remaining_items),
        "requirement_date": clean_text(value.get("requirement_date") or value.get("received_date"), "需成员确认"),
        "requirement_source": clean_text(value.get("requirement_source") or value.get("source"), "需成员确认"),
        "requirement_structure_present": count_field_present(raw_structure),
        "requirement_structure_counts": normalize_counts(raw_structure),
        "work_total_present": scalar_count_present(raw_work_total),
        "work_total": normalize_scalar_count(raw_work_total),
        "completed_this_week_counts": normalize_counts(raw_completed),
        "remaining_counts": normalize_counts(raw_remaining),
        "completed_this_week_total": normalize_scalar_count(raw_completed),
        "remaining_total": normalize_scalar_count(raw_remaining),
        "ledger_present": isinstance(value.get("ledger"), dict),
        "ledger": ledger,
        "bsp_pending_counts": ledger["bsp_pending"],
        "legacy_custom_counts": {
            "requirement_structure": legacy_custom_count(raw_structure),
            "completed_this_week": legacy_custom_count(raw_completed),
            "remaining": legacy_custom_count(raw_remaining),
        },
        "completed_items": completed_items,
        "remaining_items": remaining_items,
        "key_points": clean_list(value.get("key_points")) or ["无"],
        "risks": clean_list(value.get("risks")) or ["无超过 3 天无进展事项。"],
        "dependencies": clean_list(value.get("dependencies")) or ["无外部依赖事项。"],
        "next_week_plan": clean_list(value.get("next_week_plan")),
    }
    row.update(normalize_gms_fields(value))
    if work_type == "Patch":
        row["work_total_present"] = row["requirement_structure_present"]
        row["work_total"] = count_total(row["requirement_structure_counts"])
        row["completed_this_week_total"] = count_total(row["completed_this_week_counts"])
        row["remaining_total"] = count_total(row["remaining_counts"])
    elif work_type == "App":
        row["bsp_pending_counts"] = 0
    elif work_type in {"GMS", "Doc", "Other"}:
        row.update(
            {
                "requirement_structure_present": False,
                "requirement_structure_counts": zero_counts(),
                "work_total_present": False,
                "work_total": 0,
                "completed_this_week_counts": zero_counts(),
                "remaining_counts": zero_counts(),
                "completed_this_week_total": 0,
                "remaining_total": 0,
                "ledger_present": False,
                "ledger": {},
                "bsp_pending_counts": zero_counts(),
            }
        )
    return row


def _weekly_document_row(value: dict[str, Any]) -> dict[str, Any]:
    completed_items = clean_list(value.get("completed_items"))
    remaining_items = clean_list(value.get("remaining_items"))
    completed = normalize_scalar_count(value.get("completed_this_week"))
    remaining = normalize_scalar_count(value.get("remaining"))
    work_type = normalize_standalone_work_type(value.get("work_type")) or DOCUMENT_WORK_TYPE
    row = {
        "work_type": work_type,
        "week_summary": weekly_summary(value.get("week_summary"), completed_items, remaining_items),
        "completed_this_week": completed,
        "remaining": remaining,
        "completed_items": completed_items,
        "remaining_items": remaining_items,
        "key_points": clean_list(value.get("key_points")) or ["无"],
        "risks": clean_list(value.get("risks")) or ["无超过 3 天无进展事项。"],
        "dependencies": clean_list(value.get("dependencies")) or ["无外部依赖事项。"],
        "next_week_plan": clean_list(value.get("next_week_plan")),
    }
    if work_type == DOCUMENT_WORK_TYPE:
        row["document_name"] = clean_document_name(value.get("document_name") or value.get("work_name"))
    else:
        row["work_name"] = standalone_work_name({**value, "work_type": work_type})
    platform = clean_text(value.get("platform")).upper()
    if platform:
        row["platform"] = platform
    return row


def project_customer_identity_conflicts(
    projects: list[dict[str, Any]],
    expected_project_customers: dict[str, Any],
) -> list[dict[str, str]]:
    expected: dict[str, dict[str, str]] = {}
    for raw_project, raw_context in expected_project_customers.items():
        project = find_company_project(str(raw_project or ""))
        context = normalize_report_customer_context(raw_context)
        if project and context.get("customer_name"):
            expected[project] = context

    conflicts: list[dict[str, str]] = []
    for row in projects:
        project = find_company_project(clean_text(row.get("project")))
        context = expected.get(project or "")
        if not project or not context:
            continue
        actual_customer = clean_text(row.get("customer") or row.get("customer_name"))
        actual_downstream = clean_text(row.get("downstream_customer"))
        expected_customer = clean_text(context.get("customer_name"))
        expected_downstream = clean_text(context.get("downstream_customer"))
        direct_conflict = actual_customer != expected_customer
        downstream_conflict = bool(expected_downstream) and actual_downstream != expected_downstream
        if direct_conflict or downstream_conflict:
            conflicts.append(
                {
                    "project": project,
                    "actual_customer": actual_customer,
                    "actual_downstream_customer": actual_downstream,
                    "expected_customer": expected_customer,
                    "expected_downstream_customer": expected_downstream,
                }
            )
    return conflicts


def load_explicit_facts(
    path: Path,
    week_key: str,
    *,
    expected_project_customers: dict[str, Any] | None = None,
    include_documents: bool = False,
    include_all_scopes: bool = False,
    previous_projects: dict[str, list[dict[str, Any]]] | None = None,
) -> (
    list[dict[str, Any]]
    | tuple[list[dict[str, Any]], list[dict[str, Any]]]
    | tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]
):
    payload = read_json_file(path)
    if payload.get("schema") == "akbs-weekly-project-facts-v1":
        raise SystemExit(
            "weekly facts v1 的定制分类不能自动拆成需求和移植；请由成员确认后改用 "
            f"{WEEKLY_FACTS_SCHEMA}"
        )
    if payload.get("schema") == "akbs-weekly-project-facts-v2":
        raise SystemExit(
            "weekly facts v2 缺少 Patch/App 类型；请由成员确认类型后改用 "
            f"{WEEKLY_FACTS_SCHEMA}"
        )
    schema = payload.get("schema")
    if schema not in {
        WEEKLY_FACTS_SCHEMA,
        PREVIOUS_WEEKLY_FACTS_SCHEMA,
        LEGACY_WEEKLY_FACTS_SCHEMA,
        OLDEST_WEEKLY_FACTS_SCHEMA,
    }:
        raise SystemExit(f"weekly facts schema 必须是 {WEEKLY_FACTS_SCHEMA}")
    if clean_text(payload.get("week_range")) != week_key:
        raise SystemExit(f"weekly facts week_range 必须等于 {week_key}")
    projects = payload.get("projects")
    documents = payload.get("documents", [])
    standalone_work = payload.get("standalone_work", [])
    if not isinstance(projects, list):
        raise SystemExit("weekly facts projects 必须是数组")
    if not isinstance(documents, list):
        raise SystemExit("weekly facts documents 必须是数组")
    if not isinstance(standalone_work, list):
        raise SystemExit("weekly facts standalone_work 必须是数组")
    if schema == OLDEST_WEEKLY_FACTS_SCHEMA and (documents or standalone_work):
        raise SystemExit(f"非项目工作必须改用 {WEEKLY_FACTS_SCHEMA}")
    if not projects and not documents and not standalone_work:
        raise SystemExit("weekly facts projects、documents 和 standalone_work 至少提供一项")
    normalized: list[dict[str, Any]] = []
    errors: list[str] = []
    seen_project_chains: dict[str, tuple[int, str, str]] = {}
    seen_scopes: dict[tuple[str, ...], int] = {}
    for index, item in enumerate(projects):
        if not isinstance(item, dict):
            errors.append(f"projects[{index}] 必须是对象")
            continue
        raw_project = clean_text(item.get("project"))
        project = find_company_project(raw_project) or f"projects[{index}]"
        if project.startswith("projects["):
            errors.append(f"{project}.project 必须是公司项目名")
        elif raw_project.upper() != project.upper():
            errors.append(
                f"{project}.project 只能填写规范项目编号 {project}；其他内容请写入事项字段"
            )
        for field in ("customer", "work_type", "project_role", "requirement_date", "requirement_source"):
            if clean_text(item.get(field)) in MISSING_VALUES:
                errors.append(f"{project}.{field} 必须提供")
        customer = clean_text(item.get("customer"))
        if customer not in MISSING_VALUES and not clean_report_customer_name(customer):
            errors.append(
                f"{project}.customer 必须是有效的直接客户名称"
            )
        downstream_value = (
            item.get("downstream_customer")
            or item.get("customer_of_customer")
            or item.get("end_customer")
            or item.get("客户的客户")
        )
        if downstream_value is not None and clean_text(downstream_value) in MISSING_VALUES:
            errors.append(f"{project}.downstream_customer 如提供则必须是有效的客户名称")
        downstream_customer = clean_text(downstream_value)
        if downstream_customer and downstream_customer not in MISSING_VALUES and not clean_report_customer_name(downstream_customer):
            errors.append(
                f"{project}.downstream_customer 必须是有效的客户的客户名称"
            )
        work_type = clean_text(item.get("work_type"))
        app_name = clean_text(item.get("app_name"))
        if work_type not in PROJECT_WORK_TYPES:
            errors.append(f"{project}.work_type 只能是 Patch、App、GMS、Doc 或 Other")
        if work_type == "App" and not app_name:
            errors.append(f"{project}.app_name 类型为 App 时必须提供")
        if work_type != "App" and app_name:
            errors.append(f"{project}.app_name 仅类型为 App 时允许提供")
        if work_type == "GMS":
            if schema == WEEKLY_FACTS_SCHEMA:
                errors.extend(validate_gms_fields(item, prefix=project))
            elif not clean_text(item.get("current_stage")) and not clean_text(item.get("gms_cycle_status")):
                errors.append(f"{project}.current_stage 历史 GMS 行必须提供")
        if item.get("display_name") or item.get("model"):
            errors.append(f"{project}.display_name/model 不属于项目身份；项目标题只使用项目和客户")
        if not project.startswith("projects["):
            identity = (customer, downstream_customer)
            previous_identity = seen_project_chains.get(project)
            if previous_identity and identity != previous_identity[1:]:
                previous_index, _, _ = previous_identity
                errors.append(
                    f"{project} 在 projects[{previous_index}] 和 projects[{index}] 的客户链冲突；"
                    "同一项目必须保留唯一客户链"
                )
            elif not previous_identity:
                seen_project_chains[project] = (index, customer, downstream_customer)
            scope = report_scope_key(item)
            previous_scope_index = seen_scopes.get(scope)
            if previous_scope_index is not None:
                label = f"App {app_name}" if work_type == "App" else work_type
                errors.append(
                    f"{project} {label} 在 projects[{previous_scope_index}] 和 projects[{index}] 重复；"
                    "同一统计对象只能有一行"
                )
            else:
                seen_scopes[scope] = index
        requirement_date = clean_text(item.get("requirement_date"))
        try:
            valid_requirement_date = bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", requirement_date))
            if valid_requirement_date:
                dt.date.fromisoformat(requirement_date)
        except ValueError:
            valid_requirement_date = False
        if not valid_requirement_date:
            errors.append(f"{project}.requirement_date 必须是 YYYY-MM-DD")
        if item.get("project_role") not in ALLOWED_PROJECT_ROLES:
            errors.append(f"{project}.project_role 只能是主责或协作")
        if item.get("requirement_source") not in ALLOWED_SOURCES:
            errors.append(f"{project}.requirement_source 只能是 CR、TL、PM、TE 或 BSP")
        if "下周继续" in clean_text(item.get("week_summary")):
            errors.append(f"{project}.week_summary 不得用下周计划代替本周进展")
        completed_total = 0
        remaining_total = 0
        if work_type in {"GMS", "Doc", "Other"}:
            if any(item.get(field) not in (None, "", {}, 0) for field in ("requirement_structure", "work_total", "ledger")):
                errors.append(f"{project} {work_type} 不得填写 Patch/App 总账字段")
        elif work_type == "App":
            if item.get("requirement_structure") is not None:
                errors.append(f"{project}.requirement_structure 类型为 App 时不得提供")
            raw_app_counts = {
                "work_total": item.get("work_total"),
                "completed_this_week": item.get("completed_this_week"),
                "remaining": item.get("remaining"),
            }
            if item.get("project_role") == "主责" and raw_app_counts["work_total"] is None:
                errors.append(f"{project}.work_total App 主责必须提供")
            if item.get("project_role") == "协作" and raw_app_counts["work_total"] is None:
                raw_app_counts.pop("work_total")
            scalar_counts: dict[str, int] = {}
            for field, raw in raw_app_counts.items():
                if not isinstance(raw, int) or isinstance(raw, bool) or raw < 0:
                    errors.append(f"{project}.{field} App 计数必须是非负整数")
                    continue
                scalar_counts[field] = raw
            completed_total = scalar_counts.get("completed_this_week", 0)
            remaining_total = scalar_counts.get("remaining", 0)
            if "work_total" in scalar_counts and completed_total + remaining_total > scalar_counts["work_total"]:
                errors.append(f"{project} App 本周完成加当前剩余不能超过 App 总量")
        else:
            raw_count_fields = {
                "requirement_structure": item.get("requirement_structure"),
                "completed_this_week": item.get("completed_this_week"),
                "remaining": item.get("remaining"),
            }
            if item.get("project_role") == "主责" and not isinstance(raw_count_fields["requirement_structure"], dict):
                errors.append(f"{project}.requirement_structure 主责必须提供（Patch）")
            if item.get("project_role") == "协作" and raw_count_fields["requirement_structure"] is not None and not isinstance(raw_count_fields["requirement_structure"], dict):
                errors.append(f"{project}.requirement_structure 如提供则必须是对象")
            count_fields: dict[str, dict[str, int]] = {}
            for field, raw in raw_count_fields.items():
                if field == "requirement_structure" and item.get("project_role") == "协作" and raw is None:
                    continue
                if field != "requirement_structure" and not isinstance(raw, dict):
                    errors.append(f"{project}.{field} Patch 必须提供分类对象")
                    raw = {}
                counts = normalize_counts(raw)
                count_fields[field] = counts
                if not isinstance(raw, dict):
                    continue
                unknown_keys = sorted(str(key) for key in raw if str(key) not in COUNT_KEYS)
                if unknown_keys:
                    errors.append(
                        f"{project}.{field} 含非法分类 {','.join(unknown_keys)}；定制不能自动拆分，"
                        "只允许 demand/migration/bug/bsp"
                    )
                invalid_counts = any(
                    key in raw
                    and (not isinstance(raw.get(key), int) or isinstance(raw.get(key), bool) or int(raw.get(key)) < 0)
                    for key in COUNT_KEYS
                )
                if invalid_counts:
                    errors.append(f"{project}.{field} 计数必须是非负整数")
                if count_total(counts) > 0 and not any(counts[key] > 0 for key in BUSINESS_COUNT_KEYS):
                    errors.append(f"{project}.{field} 至少要有一项需求、移植或 Bug，不能只填 BSP")
            completed = count_fields.get("completed_this_week", zero_counts())
            remaining = count_fields.get("remaining", zero_counts())
            total = count_fields.get("requirement_structure")
            completed_total = count_total(completed)
            remaining_total = count_total(remaining)
            if completed["bsp"]:
                errors.append(f"{project}.completed_this_week.bsp 必须为 0；Android 团队完成项只能归入需求、移植或 Bug")
            if total is not None:
                for key in COUNT_KEYS:
                    if completed[key] + remaining[key] > total[key]:
                        errors.append(f"{project}.{key} 本周完成加当前剩余不能超过项目总量")
        completed_items = clean_list(item.get("completed_items"))
        remaining_items = clean_list(item.get("remaining_items"))
        if completed_total > 0 and not completed_items:
            errors.append(f"{project}.completed_items 本周完成大于 0 时必须提供")
        if remaining_total > 0 and not remaining_items:
            errors.append(f"{project}.remaining_items 当前剩余大于 0 时必须提供")
        if remaining_total > 0 and not clean_list(item.get("next_week_plan")):
            errors.append(f"{project}.next_week_plan 当前有剩余时必须提供")
        normalized_row = _weekly_project_row(item)
        previous, previous_ambiguous = matching_previous_scope(
            normalized_row,
            previous_projects or {},
        )
        if schema == WEEKLY_FACTS_SCHEMA and work_type in {"Patch", "App"}:
            validate_v5_project_ledger(
                item,
                normalized_row,
                previous,
                previous_ambiguous=previous_ambiguous,
                errors=errors,
            )
        elif work_type in {"Patch", "App"} and (previous is not None or previous_ambiguous):
            errors.append(
                f"{project}.ledger 已有上周台账的项目必须使用 {WEEKLY_FACTS_SCHEMA}；"
                "旧显式事实不得绕过上周基线"
            )
        normalized.append(normalized_row)
    for conflict in project_customer_identity_conflicts(normalized, expected_project_customers or {}):
        errors.append(
            f"{conflict['project']}.customer 客户链与当前会话已确认的项目身份不一致；"
            "请保持项目、直接客户和客户的客户各自独立"
        )
    if errors:
        raise SystemExit("weekly facts 校验失败: " + "；".join(errors))
    document_errors = validate_weekly_documents(documents)
    if document_errors:
        raise SystemExit("weekly facts 校验失败: " + "；".join(document_errors))
    normalized_documents = [
        _weekly_document_row(item)
        for item in documents
        if isinstance(item, dict)
    ]
    if len(normalized_documents) != len(documents):
        raise SystemExit("weekly facts 校验失败: documents 中每一项都必须是对象")
    standalone_errors = validate_weekly_standalone_work(standalone_work)
    if standalone_errors:
        raise SystemExit("weekly facts 校验失败: " + "；".join(standalone_errors))
    normalized_standalone_work = [
        _weekly_document_row(item)
        for item in standalone_work
        if isinstance(item, dict)
    ]
    if len(normalized_standalone_work) != len(standalone_work):
        raise SystemExit("weekly facts 校验失败: standalone_work 中每一项都必须是对象")
    if include_all_scopes:
        return normalized, normalized_documents, normalized_standalone_work
    return (normalized, normalized_documents) if include_documents else normalized


def _daily_project_records(items: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, str]]]]:
    metadata: dict[str, dict[str, Any]] = {}
    records: dict[str, dict[str, dict[str, str]]] = {}
    for item in sorted(items, key=lambda row: (clean_text(row.get("report_date")), clean_text(row.get("package_key")))):
        report_date = clean_text(item.get("report_date"))
        view = report_item_view(item)
        projects = view.get("projects") if isinstance(view.get("projects"), list) else []
        for raw_project in projects:
            if not isinstance(raw_project, dict):
                continue
            project = find_company_project(clean_text(raw_project.get("project")))
            if not project:
                continue
            project_meta = metadata.setdefault(project, {})
            project_meta["customer"] = clean_text(
                raw_project.get("customer") or raw_project.get("customer_name"),
                metadata.get(project, {}).get("customer", ""),
            )
            downstream_customer = clean_text(
                raw_project.get("downstream_customer")
                or raw_project.get("customer_of_customer")
                or raw_project.get("end_customer")
            )
            if downstream_customer:
                project_meta["downstream_customer"] = downstream_customer
            focuses = clean_list(raw_project.get("tomorrow_focus"))
            if focuses:
                project_meta["latest_focus"] = focuses
            work_items = raw_project.get("work_items") if isinstance(raw_project.get("work_items"), list) else []
            work_type = clean_text(raw_project.get("work_type"))
            app_name = clean_text(raw_project.get("app_name"))
            gms_fields = normalize_gms_fields(raw_project)
            attention_by_scope = project_meta.setdefault("attention_by_scope", {})
            scope_attention = attention_by_scope.setdefault(
                attention_scope_key(
                    work_type,
                    app_name,
                    gms_fields.get("gms_release_type"),
                    gms_fields.get("gms_target"),
                ),
                {"key_points": [], "dependencies": []},
            )
            append_attention_values(scope_attention, "key_points", raw_project.get("key_points"))
            append_attention_values(scope_attention, "dependencies", raw_project.get("dependencies"))
            for work_item in work_items:
                if not isinstance(work_item, dict):
                    continue
                name = clean_text(work_item.get("name"))
                did = clean_list(work_item.get("did"))
                text = name or (did[0] if did else "")
                if not text:
                    continue
                result = " ".join(
                    value
                    for value in (
                        clean_text(work_item.get("status")),
                        clean_text(work_item.get("result") or raw_project.get("current_result")),
                    )
                    if value
                )
                record_key = "|".join(
                    (
                        work_type,
                        app_name.casefold() if work_type == "App" else "",
                        clean_text(gms_fields.get("gms_release_type")).upper() if work_type == "GMS" else "",
                        clean_text(gms_fields.get("gms_target")).casefold() if work_type == "GMS" else "",
                        item_key(text) or text,
                    )
                )
                record = {
                    "date": report_date,
                    "text": text,
                    "progress": result,
                    "work_type": work_type,
                    "app_name": app_name,
                }
                record.update(gms_fields)
                records.setdefault(project, {})[record_key] = record
        tomorrow_plan = view.get("tomorrow_plan") if isinstance(view.get("tomorrow_plan"), dict) else {}
        planned_projects = (
            tomorrow_plan.get("projects")
            if isinstance(tomorrow_plan.get("projects"), list)
            else []
        )
        for raw_plan in planned_projects:
            if not isinstance(raw_plan, dict):
                continue
            project = find_company_project(clean_text(raw_plan.get("project")))
            if not project:
                continue
            project_meta = metadata.setdefault(project, {})
            customer = clean_text(raw_plan.get("customer") or raw_plan.get("customer_name"))
            if customer:
                project_meta["customer"] = customer
            downstream_customer = clean_text(raw_plan.get("downstream_customer"))
            if downstream_customer:
                project_meta["downstream_customer"] = downstream_customer
            plans = clean_list(raw_plan.get("plan_items"))
            if plans:
                project_meta.setdefault("plan_by_scope", {})[
                    attention_scope_key(
                        raw_plan.get("work_type"),
                        raw_plan.get("app_name"),
                        raw_plan.get("gms_release_type"),
                        raw_plan.get("gms_target"),
                    )
                ] = plans
    return metadata, {project: list(project_records.values()) for project, project_records in records.items()}


def _daily_non_project_records(
    items: list[dict[str, Any]],
    *,
    collection: str,
    name_field: str,
) -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, str]]]]:
    metadata: dict[str, dict[str, Any]] = {}
    records: dict[str, dict[str, dict[str, str]]] = {}
    for item in sorted(items, key=lambda row: (clean_text(row.get("report_date")), clean_text(row.get("package_key")))):
        report_date = clean_text(item.get("report_date"))
        view = report_item_view(item)
        rows = view.get(collection) if isinstance(view.get(collection), list) else []
        for raw_document in rows:
            if not isinstance(raw_document, dict):
                continue
            name = (
                clean_document_name(raw_document.get(name_field))
                if name_field == "document_name"
                else clean_text(raw_document.get(name_field))
            )
            if not name:
                continue
            focuses = clean_list(raw_document.get("tomorrow_focus"))
            if focuses:
                metadata.setdefault(name, {})["latest_focus"] = focuses
            row_meta = metadata.setdefault(name, {})
            append_attention_values(row_meta, "key_points", raw_document.get("key_points"))
            append_attention_values(row_meta, "dependencies", raw_document.get("dependencies"))
            work_items = raw_document.get("work_items") if isinstance(raw_document.get("work_items"), list) else []
            for work_item in work_items:
                if not isinstance(work_item, dict):
                    continue
                item_name = clean_text(work_item.get("name"))
                did = clean_list(work_item.get("did"))
                text = item_name or (did[0] if did else "")
                if not text:
                    continue
                result = " ".join(
                    value
                    for value in (
                        clean_text(work_item.get("status")),
                        clean_text(work_item.get("result") or raw_document.get("current_result")),
                    )
                    if value
                )
                key = item_key(text) or text
                records.setdefault(name, {})[key] = {
                    "date": report_date,
                    "text": text,
                    "progress": result,
                }
        tomorrow_plan = view.get("tomorrow_plan") if isinstance(view.get("tomorrow_plan"), dict) else {}
        planned_rows = (
            tomorrow_plan.get(collection)
            if isinstance(tomorrow_plan.get(collection), list)
            else []
        )
        for raw_plan in planned_rows:
            if not isinstance(raw_plan, dict):
                continue
            name = (
                clean_document_name(raw_plan.get(name_field))
                if name_field == "document_name"
                else clean_text(raw_plan.get(name_field))
            )
            plans = clean_list(raw_plan.get("plan_items"))
            if name and plans:
                metadata.setdefault(name, {})["latest_plan"] = plans
    return metadata, {name: list(document_records.values()) for name, document_records in records.items()}


def _daily_document_records(
    items: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, str]]]]:
    return _daily_non_project_records(items, collection="documents", name_field="document_name")


def _daily_standalone_records(
    items: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, str]]]]:
    return _daily_non_project_records(items, collection="standalone_work", name_field="work_name")


def _previous_week_projects(items: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        view = report_item_view(item)
        projects = view.get("projects") if isinstance(view.get("projects"), list) else []
        for raw_project in projects:
            if not isinstance(raw_project, dict):
                continue
            row = _weekly_project_row(raw_project)
            project = find_company_project(row["project"])
            if project:
                row["project"] = project
                row["_package_key"] = clean_text(item.get("package_key"))
                row["_week_range"] = clean_text(item.get("week_range"))
                result.setdefault(project, []).append(row)
    return result


def _previous_week_documents(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in items:
        view = report_item_view(item)
        documents = view.get("documents") if isinstance(view.get("documents"), list) else []
        for raw_document in documents:
            if not isinstance(raw_document, dict):
                continue
            row = _weekly_document_row(raw_document)
            name = clean_document_name(row.get("document_name"))
            if name:
                result[name] = row
    return result


def _previous_week_standalone_work(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in items:
        view = report_item_view(item)
        rows = view.get("standalone_work") if isinstance(view.get("standalone_work"), list) else []
        for raw_row in rows:
            if not isinstance(raw_row, dict):
                continue
            row = _weekly_document_row(raw_row)
            name = clean_text(row.get("work_name"))
            if name:
                result[name] = row
    return result


def _history_document_row(
    document_name: str,
    previous: dict[str, Any] | None,
    daily_meta: dict[str, Any],
    daily_records: list[dict[str, Any]],
) -> dict[str, Any]:
    prior = dict(previous or {})
    prior_remaining = clean_list(prior.get("remaining_items"))
    current_remaining = list(prior_remaining)
    completed_items: list[str] = []
    current_unfinished: list[str] = []
    for record in sorted(daily_records, key=lambda item: item.get("date", "")):
        text = record["text"]
        matched = next((item for item in current_remaining if same_item(text, item)), "")
        if progress_completed(record.get("progress")):
            add_unique(completed_items, text)
            if matched:
                current_remaining = [item for item in current_remaining if not same_item(item, matched)]
        else:
            add_unique(current_unfinished, text)
    for item in current_unfinished:
        add_unique(current_remaining, item)
    risks = clean_list(prior.get("risks"))
    key_points = clean_list(daily_meta.get("key_points"))
    dependencies = clean_list(prior.get("dependencies"))
    for item in clean_list(daily_meta.get("dependencies")):
        add_unique(dependencies, item)
    for record in daily_records:
        progress = record.get("progress", "")
        if re.search(r"阻塞|失败|等待|依赖", progress):
            add_unique(risks, record["text"])
        if re.search(r"依赖|等待|评审|确认|反馈", f"{record['text']} {progress}"):
            add_unique(dependencies, record["text"])
    plans = (
        clean_list(daily_meta.get("latest_plan"))
        or clean_list(daily_meta.get("latest_focus"))
        or clean_list(prior.get("next_week_plan"))
    )
    if not plans and current_remaining:
        plans = ["继续推进：" + "、".join(current_remaining[:3])]
    return {
        "work_type": DOCUMENT_WORK_TYPE,
        "document_name": document_name,
        "week_summary": weekly_summary("", completed_items, current_remaining),
        "completed_this_week": len(completed_items),
        "remaining": len(current_remaining),
        "completed_items": completed_items,
        "remaining_items": current_remaining,
        "key_points": key_points or ["无"],
        "risks": risks or ["无超过 3 天无进展事项。"],
        "dependencies": dependencies or ["无外部依赖事项。"],
        "next_week_plan": plans,
        "_dependency_review_candidates": list(dependencies),
    }


def _history_standalone_row(
    work_name: str,
    previous: dict[str, Any] | None,
    daily_meta: dict[str, Any],
    daily_records: list[dict[str, str]],
) -> dict[str, Any]:
    row = _history_document_row(work_name, previous, daily_meta, daily_records)
    row["work_type"] = "Other"
    row.pop("document_name", None)
    row["work_name"] = work_name
    return row


def _history_project_row(
    project: str,
    previous: dict[str, Any] | None,
    daily_meta: dict[str, Any],
    daily_records: list[dict[str, Any]],
    *,
    period_start: dt.date,
    as_of: dt.date,
) -> tuple[dict[str, Any], list[str]]:
    prior = dict(previous or {})
    work_type = clean_text(prior.get("work_type"), "需成员确认")
    latest_gms_fields: dict[str, Any] = {}
    if work_type == "GMS":
        latest_gms = next(
            (
                record
                for record in sorted(
                    daily_records,
                    key=lambda item: item.get("date", ""),
                    reverse=True,
                )
                if normalize_gms_fields(record).get("gms_release_type")
                and normalize_gms_fields(record).get("gms_target")
            ),
            prior,
        )
        latest_gms_fields = normalize_gms_fields(latest_gms)
    total_counts = normalize_counts(prior.get("requirement_structure_counts"))
    remaining_counts = normalize_counts(prior.get("remaining_counts"))
    completed_counts = zero_counts()
    work_total = int(prior.get("work_total", 0) or 0)
    completed_total = 0
    remaining_total = int(prior.get("remaining_total", 0) or 0)
    prior_remaining = clean_list(prior.get("remaining_items"))
    current_remaining = list(prior_remaining)
    completed_items: list[str] = []
    current_unfinished: list[str] = []
    unmatched_existing_scope = False
    scope_change_candidates: list[str] = []

    for record in sorted(daily_records, key=lambda item: item.get("date", "")):
        text = record["text"]
        is_completed = progress_completed(record.get("progress"))
        matched = next((item for item in prior_remaining if same_item(text, item)), "")
        if previous and not matched and work_type in {"Patch", "App"}:
            unmatched_existing_scope = True
            add_unique(scope_change_candidates, text)
        if work_type in {"GMS", "Doc", "Other"}:
            if is_completed:
                add_unique(completed_items, text)
                if matched:
                    current_remaining = [item for item in current_remaining if not same_item(item, matched)]
            else:
                add_unique(current_unfinished, text)
            continue
        if work_type == "App":
            if is_completed:
                add_unique(completed_items, text)
                completed_total += 1
                if matched:
                    current_remaining = [item for item in current_remaining if not same_item(item, matched)]
                    remaining_total = max(0, remaining_total - 1)
                else:
                    work_total += 1
            else:
                add_unique(current_unfinished, text)
                if not matched:
                    work_total += 1
                    remaining_total += 1
            continue
        category = item_category(text, completed=is_completed)
        if is_completed:
            matched_category = item_category(matched) if matched else ""
            if matched_category and matched_category != "bsp":
                category = matched_category
            add_unique(completed_items, text)
            completed_counts[category] += 1
            if matched:
                current_remaining = [item for item in current_remaining if not same_item(item, matched)]
                if matched_category == "bsp":
                    if total_counts["bsp"] > 0:
                        total_counts["bsp"] -= 1
                    total_counts[category] += 1
                if remaining_counts[matched_category] > 0:
                    remaining_counts[matched_category] -= 1
            else:
                total_counts[category] += 1
        else:
            add_unique(current_unfinished, text)
            if not matched:
                total_counts[category] += 1
                remaining_counts[category] += 1
    for item in current_unfinished:
        add_unique(current_remaining, item)

    customer = clean_text(prior.get("customer") or daily_meta.get("customer"), "需成员补充客户名")
    downstream_customer = clean_text(
        prior.get("downstream_customer") or daily_meta.get("downstream_customer")
    )
    summary = weekly_summary("", completed_items, current_remaining)
    blocked = [record["text"] for record in daily_records if re.search(r"阻塞|失败|等待|依赖", record.get("progress", ""))]
    risks = clean_list(prior.get("risks"))
    for item in blocked:
        add_unique(risks, item)
    for item in current_remaining:
        recorded_dates: list[dt.date] = []
        for record in daily_records:
            if not same_item(item, record.get("text", "")):
                continue
            try:
                recorded_dates.append(dt.date.fromisoformat(record.get("date", "")))
            except ValueError:
                continue
        last_progress = max(recorded_dates) if recorded_dates else period_start - dt.timedelta(days=1)
        if (as_of - last_progress).days > 3:
            add_unique(risks, f"超过 3 天无进展：{item}（最后记录 {last_progress.isoformat()}）")
    scope_row = {**prior, **latest_gms_fields}
    attention = daily_scope_attention(daily_meta, scope_row)
    key_points = clean_list(attention.get("key_points"))
    dependencies = clean_list(prior.get("dependencies"))
    for item in clean_list(attention.get("dependencies")):
        add_unique(dependencies, item)
    for record in daily_records:
        if re.search(r"依赖|等待|客户确认|外部|第三方|\bBSP\b|测试反馈", f"{record['text']} {record.get('progress', '')}", re.IGNORECASE):
            add_unique(dependencies, record["text"])
    plans = daily_scope_plan(daily_meta, scope_row) or clean_list(prior.get("next_week_plan"))
    if not plans and current_remaining:
        plans = ["继续推进：" + "、".join(current_remaining[:3])]

    row = {
        "project": project,
        "customer": customer,
        "customer_name": customer,
        "downstream_customer": downstream_customer,
        "work_type": work_type,
        "app_name": clean_text(prior.get("app_name")),
        "current_stage": clean_text(prior.get("current_stage")),
        "project_role": clean_text(prior.get("project_role"), "需成员确认"),
        "week_summary": summary,
        "requirement_date": clean_text(prior.get("requirement_date"), "需成员确认"),
        "requirement_source": clean_text(prior.get("requirement_source"), "需成员确认"),
        "requirement_structure_present": bool(prior.get("requirement_structure_present")),
        "requirement_structure_counts": total_counts,
        "work_total_present": bool(prior.get("work_total_present")),
        "work_total": work_total,
        "completed_this_week_counts": completed_counts,
        "remaining_counts": remaining_counts,
        "completed_this_week_total": completed_total if work_type == "App" else count_total(completed_counts),
        "remaining_total": remaining_total if work_type == "App" else count_total(remaining_counts),
        "completed_items": completed_items,
        "remaining_items": current_remaining,
        "key_points": key_points or ["无"],
        "risks": risks or ["无超过 3 天无进展事项。"],
        "dependencies": dependencies or ["无外部依赖事项。"],
        "next_week_plan": plans,
        "_scope_change_candidates": scope_change_candidates,
        "_dependency_review_candidates": list(dependencies),
    }
    row.update(latest_gms_fields)
    missing: list[str] = []
    for field in ("customer", "work_type", "project_role", "requirement_date", "requirement_source"):
        if clean_text(row.get(field)) in MISSING_VALUES or clean_text(row.get(field)).startswith("需成员补充"):
            missing.append(f"{project}.{field}")
    if row["work_type"] not in PROJECT_WORK_TYPES:
        missing.append(f"{project}.work_type")
    if row["work_type"] == "App" and not row["app_name"]:
        missing.append(f"{project}.app_name")
    if row["work_type"] == "GMS":
        for field in GMS_CURRENT_FIELDS:
            if field == "gms_current_stage" and row.get("gms_cycle_status") in {
                "approved",
                "cancelled",
            }:
                continue
            if row.get(field) in (None, ""):
                missing.append(f"{project}.{field}")
    if row["project_role"] not in ALLOWED_PROJECT_ROLES:
        missing.append(f"{project}.project_role")
    if row["requirement_source"] not in ALLOWED_SOURCES:
        missing.append(f"{project}.requirement_source")
    if row["project_role"] == "主责":
        if row["work_type"] == "App" and not row["work_total_present"]:
            missing.append(f"{project}.work_total")
        elif row["work_type"] == "Patch" and not row["requirement_structure_present"]:
            missing.append(f"{project}.requirement_structure")
    for field, count in (prior.get("legacy_custom_counts") or {}).items():
        if int(count or 0) > 0:
            missing.append(f"{project}.{field}_category_split")
    if not row["next_week_plan"] and (
        bool(current_remaining)
        if work_type in {"GMS", "Doc", "Other"}
        else bool(row_count(row, "remaining_counts"))
    ):
        missing.append(f"{project}.next_week_plan")
    if previous and row_count(prior, "remaining_counts") > len(prior_remaining) and daily_records:
        missing.append(f"{project}.remaining_item_identity")
    if unmatched_existing_scope:
        missing.append(f"{project}.scope_change_classification")
    if dependencies:
        scope = clean_text(row.get("work_type"), "unknown")
        app = f".{clean_text(row.get('app_name'))}" if scope == "App" else ""
        missing.append(f"{project}.{scope}{app}.dependencies_confirmation")
    return row, missing


def _project_missing_fields(row: dict[str, Any]) -> list[str]:
    project = clean_text(row.get("project"), "unknown")
    missing: list[str] = []
    for field in ("customer", "work_type", "project_role", "requirement_date", "requirement_source"):
        value = clean_text(row.get(field))
        if value in MISSING_VALUES or value.startswith("需成员补充"):
            missing.append(f"{project}.{field}")
    if row.get("work_type") not in PROJECT_WORK_TYPES:
        missing.append(f"{project}.work_type")
    if row.get("work_type") == "App" and not clean_text(row.get("app_name")):
        missing.append(f"{project}.app_name")
    if row.get("work_type") == "GMS":
        for field in GMS_CURRENT_FIELDS:
            if field == "gms_current_stage" and row.get("gms_cycle_status") in {
                "approved",
                "cancelled",
            }:
                continue
            if row.get(field) in (None, ""):
                missing.append(f"{project}.{field}")
    if row.get("project_role") not in ALLOWED_PROJECT_ROLES:
        missing.append(f"{project}.project_role")
    if row.get("requirement_source") not in ALLOWED_SOURCES:
        missing.append(f"{project}.requirement_source")
    if row.get("project_role") == "主责":
        if row.get("work_type") == "App" and not row.get("work_total_present"):
            missing.append(f"{project}.work_total")
        elif row.get("work_type") == "Patch" and not row.get("requirement_structure_present"):
            missing.append(f"{project}.requirement_structure")
    for field, count in (row.get("legacy_custom_counts") or {}).items():
        if int(count or 0) > 0:
            missing.append(f"{project}.{field}_category_split")
    if not clean_list(row.get("next_week_plan")) and (
        bool(clean_list(row.get("remaining_items")))
        if row.get("work_type") in {"GMS", "Doc", "Other"}
        else bool(row_count(row, "remaining_counts"))
    ):
        missing.append(f"{project}.next_week_plan")
    return missing


def _session_fallback_projects(
    start: dt.date,
    items: dict[str, list[tuple[str, str]]],
    project_customers: dict[str, Any],
    *,
    synthetic: bool,
) -> tuple[list[dict[str, Any]], list[str]]:
    projects: list[dict[str, Any]] = []
    missing: list[str] = []
    for raw_project, entries in sorted(items.items()):
        project = find_company_project(raw_project)
        if not project:
            continue
        total_counts = zero_counts()
        completed_counts = zero_counts()
        remaining_counts = zero_counts()
        completed_items: list[str] = []
        remaining_items: list[str] = []
        for description, progress in entries:
            is_completed = progress_completed(progress)
            category = item_category(description, completed=is_completed)
            total_counts[category] += 1
            if is_completed:
                completed_counts[category] += 1
                add_unique(completed_items, description)
            else:
                remaining_counts[category] += 1
                add_unique(remaining_items, description)
        if completed_items:
            summary = "本周完成" + "、".join(completed_items[:3]) + ("等事项。" if len(completed_items) > 3 else "。")
        else:
            summary = "本周持续推进" + "、".join(remaining_items[:3]) + ("等事项。" if len(remaining_items) > 3 else "。")
        customer_context = report_customer_context_for_project(project, project_customers)
        row = {
            "project": project,
            "customer": customer_context["customer_name"],
            "customer_name": customer_context["customer_name"],
            "downstream_customer": customer_context.get("downstream_customer", ""),
            "work_type": "Patch" if synthetic else "需成员确认",
            "app_name": "",
            "project_role": "主责" if synthetic else "需成员确认",
            "week_summary": summary,
            "requirement_date": start.isoformat() if synthetic else "需成员确认",
            "requirement_source": "TL" if synthetic else "需成员确认",
            "requirement_structure_present": synthetic,
            "requirement_structure_counts": total_counts,
            "work_total_present": synthetic,
            "work_total": count_total(total_counts),
            "completed_this_week_counts": completed_counts,
            "remaining_counts": remaining_counts,
            "completed_this_week_total": count_total(completed_counts),
            "remaining_total": count_total(remaining_counts),
            "completed_items": completed_items,
            "remaining_items": remaining_items,
            "key_points": ["无"],
            "risks": ["无超过 3 天无进展事项。"],
            "dependencies": ["无外部依赖事项。"],
            "next_week_plan": ["继续推进：" + "、".join(remaining_items[:3])] if remaining_items else [],
        }
        projects.append(row)
        if not synthetic:
            missing.extend(_project_missing_fields(row))
    return projects, missing


def project_rows_to_items(projects: list[dict[str, Any]]) -> dict[str, list[tuple[str, str]]]:
    result: dict[str, list[tuple[str, str]]] = {}
    for row in projects:
        project = clean_text(row.get("project"), "需成员补充项目名")
        entries: list[tuple[str, str]] = []
        entries.extend((item, "已完成") for item in clean_list(row.get("completed_items")))
        entries.extend((item, "进行中") for item in clean_list(row.get("remaining_items")))
        if not entries:
            entries.append((clean_text(row.get("week_summary"), "未形成有效工作记录"), "进行中"))
        result.setdefault(project, []).extend(entries)
    return result


def assign_daily_records_to_scopes(
    prior_rows: list[dict[str, Any]],
    records: list[dict[str, str]],
) -> tuple[list[list[dict[str, str]]], bool]:
    if len(prior_rows) <= 1:
        return [records], False
    assigned: list[list[dict[str, str]]] = [[] for _ in prior_rows]
    ambiguous = False
    for record in records:
        text = clean_text(record.get("text"))
        record_work_type = clean_text(record.get("work_type"))
        record_app_name = clean_text(record.get("app_name"))
        record_gms_release_type = clean_text(record.get("gms_release_type")).upper()
        record_gms_target = clean_text(record.get("gms_target")).casefold()
        exact_candidates = [
            index
            for index, row in enumerate(prior_rows)
            if record_work_type
            and clean_text(row.get("work_type")) == record_work_type
            and (
                record_work_type != "App"
                or clean_text(row.get("app_name")).casefold() == record_app_name.casefold()
            )
            and (
                record_work_type != "GMS"
                or (
                    clean_text(row.get("gms_release_type")).upper() == record_gms_release_type
                    and clean_text(row.get("gms_target")).casefold() == record_gms_target
                )
            )
        ]
        if len(exact_candidates) == 1:
            assigned[exact_candidates[0]].append(record)
            continue
        candidates: list[int] = []
        for index, row in enumerate(prior_rows):
            app_name = clean_text(row.get("app_name"))
            known_items = [
                *clean_list(row.get("completed_items")),
                *clean_list(row.get("remaining_items")),
            ]
            if (app_name and app_name.casefold() in text.casefold()) or any(
                same_item(text, item) for item in known_items
            ):
                candidates.append(index)
        if len(candidates) == 1:
            assigned[candidates[0]].append(record)
        else:
            ambiguous = True
    return assigned, ambiguous


def daily_scope_seed_rows(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for record in records:
        work_type = clean_text(record.get("work_type"))
        app_name = clean_text(record.get("app_name"))
        gms_fields = normalize_gms_fields(record)
        key = attention_scope_key(
            work_type,
            app_name,
            gms_fields.get("gms_release_type"),
            gms_fields.get("gms_target"),
        )
        if work_type not in PROJECT_WORK_TYPES or key in seen:
            continue
        seen.add(key)
        row = {
                "work_type": work_type,
                "app_name": app_name,
                "project_role": "需成员确认",
                "requirement_date": "需成员确认",
                "requirement_source": "需成员确认",
                "requirement_structure_present": False,
                "work_total_present": False,
            }
        row.update(gms_fields)
        rows.append(row)
    return rows


def facts_hash(
    projects: list[dict[str, Any]],
    documents: list[dict[str, Any]],
    standalone_work: list[dict[str, Any]],
) -> str:
    payload = json.dumps(
        {"projects": projects, "documents": documents, "standalone_work": standalone_work},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_weekly_facts(
    config: dict[str, str],
    start: dt.date,
    end: dt.date,
    week_key: str,
    *,
    explicit_path: str = "",
    synthetic: bool = False,
    fallback_items: dict[str, list[tuple[str, str]]] | None = None,
    project_customers: dict[str, Any] | None = None,
) -> WeeklyFactsResult:
    missing_fields: list[str] = []
    if explicit_path:
        _, weekly_items, history_provenance = load_history(config, start, end)
        previous = _previous_week_projects(weekly_items)
        projects, documents, standalone_work = load_explicit_facts(
            expanded_path(explicit_path),
            week_key,
            expected_project_customers=project_customers,
            include_all_scopes=True,
            previous_projects=previous,
        )
        provenance = {
            **history_provenance,
            "source": "explicit_weekly_facts",
        }
        for row in projects:
            missing_fields.extend(_project_missing_fields(row))
    elif synthetic:
        provenance = {"source": "synthetic_fixture", "daily_package_keys": [], "previous_weekly_package_keys": []}
        projects, synthetic_missing = _session_fallback_projects(
            start,
            fallback_items or {},
            project_customers or {},
            synthetic=True,
        )
        documents = []
        standalone_work = []
        missing_fields.extend(synthetic_missing)
    else:
        daily_items, weekly_items, provenance = load_history(config, start, end)
        as_of = min(end, local_now(config).date())
        daily_meta, daily_records = _daily_project_records(daily_items)
        previous = _previous_week_projects(weekly_items)
        daily_document_meta, daily_document_records = _daily_document_records(daily_items)
        previous_documents = _previous_week_documents(weekly_items)
        daily_standalone_meta, daily_standalone_records = _daily_standalone_records(daily_items)
        previous_standalone_work = _previous_week_standalone_work(weekly_items)
        session_supplements: list[str] = []
        for raw_project, entries in sorted((fallback_items or {}).items()):
            project = find_company_project(raw_project)
            if not project or project in daily_records:
                continue
            daily_records[project] = [
                {"date": as_of.isoformat(), "text": description, "progress": progress}
                for description, progress in entries
            ]
            customer_context = report_customer_context_for_project(project, project_customers)
            if customer_context["customer_name"] != "需成员补充客户名":
                daily_meta.setdefault(project, {})["customer"] = customer_context["customer_name"]
            if customer_context.get("downstream_customer"):
                daily_meta.setdefault(project, {})["downstream_customer"] = customer_context["downstream_customer"]
            session_supplements.append(project)
        provenance["session_supplement_projects"] = session_supplements
        if not daily_items and not weekly_items and session_supplements:
            provenance["source"] = "session_fallback"
        projects = []
        project_names = sorted(set(previous) | set(daily_records))
        for project in project_names:
            prior_rows = previous.get(project, [])
            records = daily_records.get(project, [])
            if not prior_rows:
                seeded_rows = daily_scope_seed_rows(records)
                prior_rows = seeded_rows or [None]
                records_by_scope, ambiguous_scope = (
                    assign_daily_records_to_scopes(prior_rows, records)
                    if seeded_rows
                    else ([records], False)
                )
            else:
                records_by_scope, ambiguous_scope = assign_daily_records_to_scopes(prior_rows, records)
            for prior, scope_records in zip(prior_rows, records_by_scope):
                row, project_missing = _history_project_row(
                    project,
                    prior,
                    daily_meta.get(project, {}),
                    scope_records,
                    period_start=start,
                    as_of=as_of,
                )
                projects.append(row)
                missing_fields.extend(project_missing)
            if ambiguous_scope:
                missing_fields.append(f"{project}.work_scope_assignment")
        documents = [
            _history_document_row(
                document_name,
                previous_documents.get(document_name),
                daily_document_meta.get(document_name, {}),
                daily_document_records.get(document_name, []),
            )
            for document_name in sorted(set(previous_documents) | set(daily_document_records))
        ]
        standalone_work = [
            _history_standalone_row(
                work_name,
                previous_standalone_work.get(work_name),
                daily_standalone_meta.get(work_name, {}),
                daily_standalone_records.get(work_name, []),
            )
            for work_name in sorted(set(previous_standalone_work) | set(daily_standalone_records))
        ]
    if not projects and not documents and not standalone_work:
        projects, fallback_missing = _session_fallback_projects(
            start,
            fallback_items or {},
            project_customers or {},
            synthetic=synthetic,
        )
        missing_fields.extend(fallback_missing)
        provenance["source"] = "session_fallback" if not synthetic else "synthetic_fixture"
    for row in documents:
        if clean_list(row.get("_dependency_review_candidates")):
            missing_fields.append(f"Doc:{clean_text(row.get('document_name'), 'unknown')}.dependencies_confirmation")
    for row in standalone_work:
        if clean_list(row.get("_dependency_review_candidates")):
            missing_fields.append(f"Other:{clean_text(row.get('work_name'), 'unknown')}.dependencies_confirmation")
    identity_conflicts = project_customer_identity_conflicts(projects, project_customers or {})
    missing_fields.extend(
        f"{conflict['project']}.customer_identity_conflict"
        for conflict in identity_conflicts
    )
    evidence = {
        "schema": WEEKLY_FACT_SOURCES_SCHEMA,
        "week_range": week_key,
        **provenance,
        "project_count": len({clean_text(row.get("project")) for row in projects}),
        "document_count": len(documents),
        "standalone_work_count": len(standalone_work),
        "work_scope_count": len(projects) + len(documents) + len(standalone_work),
        "missing_fields": sorted(set(missing_fields)),
        "identity_conflicts": identity_conflicts,
        "scope_change_candidates": [
            {
                "project": clean_text(row.get("project")),
                "items": clean_list(row.get("_scope_change_candidates")),
            }
            for row in projects
            if clean_list(row.get("_scope_change_candidates"))
        ],
        "attention_review_candidates": [
            {
                "scope": " / ".join(
                    value
                    for value in (
                        clean_text(row.get("project")),
                        clean_text(row.get("work_type")),
                        clean_text(row.get("app_name")),
                    )
                    if value
                ),
                "dependencies": clean_list(row.get("_dependency_review_candidates")),
            }
            for row in projects
            if clean_list(row.get("_dependency_review_candidates"))
        ]
        + [
            {
                "scope": f"Doc / {clean_text(row.get('document_name'))}",
                "dependencies": clean_list(row.get("_dependency_review_candidates")),
            }
            for row in documents
            if clean_list(row.get("_dependency_review_candidates"))
        ]
        + [
            {
                "scope": f"Other / {clean_text(row.get('work_name'))}",
                "dependencies": clean_list(row.get("_dependency_review_candidates")),
            }
            for row in standalone_work
            if clean_list(row.get("_dependency_review_candidates"))
        ],
        "facts_sha256": facts_hash(projects, documents, standalone_work),
    }
    return WeeklyFactsResult(projects, documents, standalone_work, evidence)
