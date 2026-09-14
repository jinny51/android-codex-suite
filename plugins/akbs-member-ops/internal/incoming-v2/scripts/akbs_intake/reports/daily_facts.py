from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from akbs_member_ops.knowledge_rules import find_company_project

from ..config import expanded_path
from ..io_utils import read_json_file
from ..report_sessions import (
    REPORT_MISSING_CUSTOMER_VALUES,
    clean_report_customer_name,
    normalize_report_customer_context,
    report_customer_context_for_project,
)
from .document_work import (
    DOCUMENT_WORK_TYPE,
    clean_document_name,
    normalize_standalone_work_type,
    standalone_work_name,
    validate_daily_documents,
    validate_daily_standalone_work,
)
from .gms import GMS_CURRENT_FIELDS, GMS_PLAN_FIELDS, normalize_gms_fields, validate_gms_fields
from .scope import ALLOWED_WORK_TYPES, PROJECT_WORK_TYPES, clean_scope_text, report_scope_key


DAILY_FACTS_SCHEMA = "akbs-daily-work-facts-v4"
LEGACY_DAILY_GMS_FACTS_SCHEMA = "akbs-daily-work-facts-v3"
LEGACY_DAILY_FACTS_SCHEMA = "akbs-daily-project-facts-v1"
LEGACY_DAILY_WORK_FACTS_SCHEMA = "akbs-daily-work-facts-v2"
SUPPORTED_DAILY_FACTS_SCHEMAS = {
    DAILY_FACTS_SCHEMA,
    LEGACY_DAILY_GMS_FACTS_SCHEMA,
    LEGACY_DAILY_WORK_FACTS_SCHEMA,
    LEGACY_DAILY_FACTS_SCHEMA,
}
DAILY_FACT_SOURCES_SCHEMA = "akbs-daily-fact-sources-v4"
LEGACY_DAILY_GMS_FACT_SOURCES_SCHEMA = "akbs-daily-fact-sources-v3"
LEGACY_DAILY_FACT_SOURCES_SCHEMA = "akbs-daily-fact-sources-v1"
LEGACY_DAILY_WORK_FACT_SOURCES_SCHEMA = "akbs-daily-fact-sources-v2"
SUPPORTED_DAILY_FACT_SOURCES_SCHEMAS = {
    DAILY_FACT_SOURCES_SCHEMA,
    LEGACY_DAILY_GMS_FACT_SOURCES_SCHEMA,
    LEGACY_DAILY_WORK_FACT_SOURCES_SCHEMA,
    LEGACY_DAILY_FACT_SOURCES_SCHEMA,
}
DAILY_STATUS_VALUES = {"已完成", "处理中", "待验证", "阻塞"}
NO_TOMORROW_FOCUS_VALUES = {"无", "无。", "暂无", "暂无。", "没有", "没有。"}
OLD_DAILY_HOW_TEXT = "根据 Codex 会话记录、工程修改、命令执行和材料证据整理实际处理过程。"


@dataclass(frozen=True)
class DailyFactsResult:
    projects: list[dict[str, Any]]
    documents: list[dict[str, Any]]
    standalone_work: list[dict[str, Any]]
    tomorrow_plan: dict[str, list[dict[str, Any]]]
    evidence: dict[str, Any]


def clean_list(value: Any) -> list[str]:
    rows = value if isinstance(value, list) else [value] if value else []
    result: list[str] = []
    for row in rows:
        text = clean_scope_text(row)
        if text and text not in result:
            result.append(text)
    return result


TOMORROW_PLAN_COLLECTIONS = ("projects", "documents", "standalone_work")
PLANNING_NOT_STARTED_RE = re.compile(
    r"(?:尚未|还没|没有|未)(?:开始|开展|执行|处理|测试|开发|验证|着手)"
)
PLANNING_FUTURE_RE = re.compile(
    r"(?:明天|明日|下个工作日|后续).{0,24}(?:开始|开展|执行|处理|测试|开发|验证|推进)"
)


def empty_tomorrow_plan() -> dict[str, list[dict[str, Any]]]:
    return {collection: [] for collection in TOMORROW_PLAN_COLLECTIONS}


def normalize_plan_items(value: Any) -> list[str]:
    return [item for item in clean_list(value) if item not in NO_TOMORROW_FOCUS_VALUES]


def planning_only_work_item(value: Any) -> bool:
    row = value if isinstance(value, dict) else {}
    text = " ".join(
        item
        for item in (
            clean_scope_text(row.get("name")),
            *clean_list(row.get("did")),
            *clean_list(row.get("how")),
            clean_scope_text(row.get("result")),
        )
        if item
    )
    return bool(PLANNING_NOT_STARTED_RE.search(text) and PLANNING_FUTURE_RE.search(text))


def normalize_tomorrow_plan(value: Any) -> dict[str, list[dict[str, Any]]]:
    raw = value if isinstance(value, dict) else {}
    plan = empty_tomorrow_plan()
    for item in raw.get("projects", []) if isinstance(raw.get("projects"), list) else []:
        if not isinstance(item, dict):
            continue
        row: dict[str, Any] = {
            "project": find_company_project(clean_scope_text(item.get("project")))
            or clean_scope_text(item.get("project")),
            "customer": clean_scope_text(item.get("customer") or item.get("customer_name")),
            "work_type": clean_scope_text(item.get("work_type") or item.get("type")),
            "plan_items": normalize_plan_items(item.get("plan_items") or item.get("tomorrow_focus")),
        }
        row["customer_name"] = row["customer"]
        downstream = clean_scope_text(item.get("downstream_customer"))
        if downstream:
            row["downstream_customer"] = downstream
        app_name = clean_scope_text(item.get("app_name"))
        if app_name:
            row["app_name"] = app_name
        row.update(normalize_gms_fields(item, plan=True))
        plan["projects"].append(row)
    for collection, expected_type, name_field in (
        ("documents", DOCUMENT_WORK_TYPE, "document_name"),
        ("standalone_work", "Other", "work_name"),
    ):
        for item in raw.get(collection, []) if isinstance(raw.get(collection), list) else []:
            if not isinstance(item, dict):
                continue
            name = (
                clean_document_name(item.get(name_field))
                if name_field == "document_name"
                else clean_scope_text(item.get(name_field))
            )
            row = {
                "work_type": normalize_standalone_work_type(item.get("work_type")) or expected_type,
                name_field: name,
                "plan_items": normalize_plan_items(item.get("plan_items") or item.get("tomorrow_focus")),
            }
            platform = clean_scope_text(item.get("platform")).upper()
            if platform:
                row["platform"] = platform
            plan[collection].append(row)
    return plan


def tomorrow_plan_identity(collection: str, row: dict[str, Any]) -> tuple[str, ...]:
    if collection == "projects":
        work_type = clean_scope_text(row.get("work_type"))
        return (
            find_company_project(clean_scope_text(row.get("project"))) or clean_scope_text(row.get("project")),
            clean_scope_text(row.get("customer") or row.get("customer_name")),
            clean_scope_text(row.get("downstream_customer")),
            work_type,
            clean_scope_text(row.get("app_name")).casefold() if work_type == "App" else "",
            clean_scope_text(row.get("gms_release_type")).upper() if work_type == "GMS" else "",
            clean_scope_text(row.get("gms_target")).casefold() if work_type == "GMS" else "",
        )
    name_field = "document_name" if collection == "documents" else "work_name"
    return (clean_scope_text(row.get("work_type")), clean_scope_text(row.get(name_field)).casefold())


def merge_tomorrow_plans(
    *values: dict[str, list[dict[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    merged = empty_tomorrow_plan()
    indexes: dict[str, dict[tuple[str, ...], dict[str, Any]]] = {
        collection: {} for collection in TOMORROW_PLAN_COLLECTIONS
    }
    for value in values:
        for collection in TOMORROW_PLAN_COLLECTIONS:
            for raw in value.get(collection, []):
                if not isinstance(raw, dict):
                    continue
                row = dict(raw)
                identity = tomorrow_plan_identity(collection, row)
                current = indexes[collection].get(identity)
                if current is None:
                    current = row
                    current["plan_items"] = normalize_plan_items(row.get("plan_items"))
                    indexes[collection][identity] = current
                    merged[collection].append(current)
                    continue
                for item in normalize_plan_items(row.get("plan_items")):
                    if item not in current["plan_items"]:
                        current["plan_items"].append(item)
    return merged


def plan_row_from_daily_scope(collection: str, row: dict[str, Any]) -> dict[str, Any] | None:
    plan_items = normalize_plan_items(row.pop("tomorrow_focus", []))
    if not plan_items:
        return None
    if collection == "projects":
        plan_row: dict[str, Any] = {
            "project": row.get("project", ""),
            "customer": row.get("customer") or row.get("customer_name") or "",
            "customer_name": row.get("customer") or row.get("customer_name") or "",
            "work_type": row.get("work_type", ""),
            "plan_items": plan_items,
        }
        for field in ("downstream_customer", "app_name", *GMS_PLAN_FIELDS):
            if row.get(field):
                plan_row[field] = row[field]
        return plan_row
    name_field = "document_name" if collection == "documents" else "work_name"
    plan_row = {
        "work_type": row.get("work_type", ""),
        name_field: row.get(name_field, ""),
        "plan_items": plan_items,
    }
    if row.get("platform"):
        plan_row["platform"] = row["platform"]
    return plan_row


def separate_daily_and_tomorrow_plan(
    projects: list[dict[str, Any]],
    documents: list[dict[str, Any]],
    standalone_work: list[dict[str, Any]],
    explicit_plan: dict[str, list[dict[str, Any]]] | None = None,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, list[dict[str, Any]]],
]:
    legacy_plan = empty_tomorrow_plan()
    retained: dict[str, list[dict[str, Any]]] = {collection: [] for collection in TOMORROW_PLAN_COLLECTIONS}
    for collection, rows in (
        ("projects", projects),
        ("documents", documents),
        ("standalone_work", standalone_work),
    ):
        for raw in rows:
            row = dict(raw)
            plan_row = plan_row_from_daily_scope(collection, row)
            if plan_row:
                legacy_plan[collection].append(plan_row)
            work_items = row.get("work_items") if isinstance(row.get("work_items"), list) else []
            if work_items and all(planning_only_work_item(item) for item in work_items):
                if not plan_row:
                    fallback_items = [clean_scope_text(item.get("name")) for item in work_items if isinstance(item, dict)]
                    if fallback_items:
                        row["tomorrow_focus"] = fallback_items
                        fallback_plan = plan_row_from_daily_scope(collection, row)
                        if fallback_plan:
                            legacy_plan[collection].append(fallback_plan)
                continue
            retained[collection].append(row)
    plan = merge_tomorrow_plans(legacy_plan, explicit_plan or empty_tomorrow_plan())
    return retained["projects"], retained["documents"], retained["standalone_work"], plan


def normalize_work_item(value: Any) -> dict[str, Any]:
    row = value if isinstance(value, dict) else {}
    return {
        "name": clean_scope_text(row.get("name")),
        "did": clean_list(row.get("did")),
        "how": clean_list(row.get("how")),
        "result": clean_scope_text(row.get("result")),
        "status": clean_scope_text(row.get("status")),
    }


def fallback_work_items(
    project: str,
    project_items: dict[str, list[tuple[str, str]]],
    daily_work_items: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    rows = [normalize_work_item(item) for item in daily_work_items.get(project, [])]
    if rows:
        return rows
    return [
        {
            "name": clean_scope_text(description),
            "did": [clean_scope_text(description)],
            "how": ["未从授权会话中提取到具体处理过程，需成员补充。"],
            "result": clean_scope_text(progress),
            "status": status_from_progress(progress),
        }
        for description, progress in project_items.get(project, [])
        if clean_scope_text(description)
    ]


def status_from_progress(value: Any) -> str:
    text = clean_scope_text(value)
    if any(token in text for token in ("阻塞", "失败", "报错")):
        return "阻塞"
    if any(token in text for token in ("待验证", "验证中", "待回归")):
        return "待验证"
    if any(token in text for token in ("已完成", "已解决", "通过", "成功", "100%")):
        return "已完成"
    return "处理中"


def topic_from_work_items(work_items: list[dict[str, Any]]) -> str:
    names = [clean_scope_text(item.get("name")) for item in work_items]
    return "、".join(name for name in names[:3] if name) or "今日工作事项需成员确认"


def result_from_work_items(work_items: list[dict[str, Any]]) -> str:
    statuses = {clean_scope_text(item.get("status")) for item in work_items}
    if "阻塞" in statuses:
        return "存在阻塞事项，需要继续推进。"
    if statuses & {"处理中", "待验证"}:
        return "部分事项仍在推进或验证中。"
    if statuses == {"已完成"}:
        return "相关事项今日已完成。"
    return "当前结果需成员确认。"


def normalize_daily_project(
    value: dict[str, Any],
    *,
    project_items: dict[str, list[tuple[str, str]]],
    daily_work_items: dict[str, list[dict[str, Any]]],
    scope_count_by_project: dict[str, int],
) -> dict[str, Any]:
    raw_project = clean_scope_text(value.get("project"))
    project = find_company_project(raw_project) or raw_project
    raw_work_items = value.get("work_items")
    if isinstance(raw_work_items, list) and raw_work_items:
        work_items = [normalize_work_item(item) for item in raw_work_items]
    elif scope_count_by_project.get(project, 0) <= 1:
        work_items = fallback_work_items(project, project_items, daily_work_items)
    else:
        work_items = []
    customer = clean_scope_text(value.get("customer") or value.get("customer_name"))
    row: dict[str, Any] = {
        "project": project,
        "customer": customer,
        "customer_name": customer,
        "work_type": clean_scope_text(value.get("work_type") or value.get("type")),
        "today_topic": clean_scope_text(value.get("today_topic")) or topic_from_work_items(work_items),
        "current_result": clean_scope_text(value.get("current_result")) or result_from_work_items(work_items),
        "work_items": work_items,
        "key_points": clean_list(value.get("key_points")),
        "dependencies": clean_list(value.get("dependencies")),
    }
    if "tomorrow_focus" in value:
        row["tomorrow_focus"] = normalize_plan_items(value.get("tomorrow_focus"))
    downstream = clean_scope_text(
        value.get("downstream_customer")
        or value.get("customer_of_customer")
        or value.get("end_customer")
    )
    if downstream:
        row["downstream_customer"] = downstream
    app_name = clean_scope_text(value.get("app_name"))
    if app_name:
        row["app_name"] = app_name
    row.update(normalize_gms_fields(value))
    return row


def normalize_daily_document(value: dict[str, Any]) -> dict[str, Any]:
    raw_items = value.get("work_items")
    work_items = (
        [normalize_work_item(item) for item in raw_items if isinstance(item, dict)]
        if isinstance(raw_items, list)
        else []
    )
    work_type = normalize_standalone_work_type(value.get("work_type")) or DOCUMENT_WORK_TYPE
    name = standalone_work_name({**value, "work_type": work_type})
    row = {
        "work_type": work_type,
        "today_topic": clean_scope_text(value.get("today_topic")) or topic_from_work_items(work_items),
        "current_result": clean_scope_text(value.get("current_result")) or result_from_work_items(work_items),
        "work_items": work_items,
        "key_points": clean_list(value.get("key_points")),
        "dependencies": clean_list(value.get("dependencies")),
    }
    if "tomorrow_focus" in value:
        row["tomorrow_focus"] = normalize_plan_items(value.get("tomorrow_focus"))
    if work_type == DOCUMENT_WORK_TYPE:
        row["document_name"] = clean_document_name(name)
    else:
        row["work_name"] = name
    platform = clean_scope_text(value.get("platform")).upper()
    if platform:
        row["platform"] = platform
    return row


def validate_daily_projects(
    projects: list[dict[str, Any]],
    *,
    expected_project_customers: dict[str, Any] | None = None,
    require_current_gms: bool = True,
) -> list[str]:
    errors: list[str] = []
    seen_customers: dict[str, tuple[str, str]] = {}
    seen_scopes: dict[tuple[str, ...], int] = {}
    expected = {
        str(project).upper(): normalize_report_customer_context(context)
        for project, context in (expected_project_customers or {}).items()
    }
    for index, row in enumerate(projects):
        prefix = f"projects[{index}]"
        project = clean_scope_text(row.get("project"))
        canonical = find_company_project(project)
        if not canonical or canonical.upper() != project.upper():
            errors.append(f"{prefix}.project 必须是规范公司项目名")
            continue
        customer = clean_scope_text(row.get("customer") or row.get("customer_name"))
        downstream = clean_scope_text(row.get("downstream_customer"))
        if customer in REPORT_MISSING_CUSTOMER_VALUES or not clean_report_customer_name(customer):
            errors.append(f"{prefix}.customer 必须提供直接客户")
        elif not downstream and len(customer.split()) > 1:
            errors.append(
                f"{prefix}.customer 只能填写直接客户；客户的客户必须单独写入 downstream_customer"
            )
        if downstream and not clean_report_customer_name(downstream):
            errors.append(f"{prefix}.downstream_customer 不是有效客户名称")
        identity = (customer, downstream)
        previous = seen_customers.get(canonical)
        if previous and previous != identity:
            errors.append(f"{prefix} 与同项目其他范围的客户链不一致")
        else:
            seen_customers[canonical] = identity
        expected_context = expected.get(canonical.upper(), {})
        if expected_context and identity != (
            clean_scope_text(expected_context.get("customer_name")),
            clean_scope_text(expected_context.get("downstream_customer")),
        ):
            errors.append(f"{prefix}.customer 客户链与当前会话已确认身份不一致")
        work_type = clean_scope_text(row.get("work_type"))
        if work_type not in PROJECT_WORK_TYPES:
            errors.append(f"{prefix}.work_type 只能是 Patch、App、GMS、Doc 或 Other")
        app_name = clean_scope_text(row.get("app_name"))
        if work_type == "App" and not app_name:
            errors.append(f"{prefix}.app_name 类型为 App 时必须提供")
        if work_type != "App" and app_name:
            errors.append(f"{prefix}.app_name 仅类型为 App 时允许提供")
        if work_type == "GMS" and require_current_gms:
            errors.extend(validate_gms_fields(row, prefix=prefix))
        scope = report_scope_key(row)
        if scope in seen_scopes:
            errors.append(f"{prefix} 与 projects[{seen_scopes[scope]}] 的统计对象重复")
        else:
            seen_scopes[scope] = index
        for field in ("today_topic", "current_result"):
            if not clean_scope_text(row.get(field)):
                errors.append(f"{prefix}.{field} 必须提供")
        work_items = row.get("work_items")
        if not isinstance(work_items, list) or not work_items:
            errors.append(
                f"{prefix}.work_items 必须至少包含一项；同一项目有多个 Patch/App 范围时必须按范围明确填写"
            )
            work_items = []
        for item_index, item in enumerate(work_items):
            item_prefix = f"{prefix}.work_items[{item_index}]"
            if not isinstance(item, dict):
                errors.append(f"{item_prefix} 必须是对象")
                continue
            for field in ("name", "result"):
                if not clean_scope_text(item.get(field)):
                    errors.append(f"{item_prefix}.{field} 必须提供")
            for field in ("did", "how"):
                values = item.get(field)
                if not isinstance(values, list) or not clean_list(values):
                    errors.append(f"{item_prefix}.{field} 必须是非空数组")
            if OLD_DAILY_HOW_TEXT in clean_list(item.get("how")):
                errors.append(f"{item_prefix}.how 不得使用固定套话")
            status = clean_scope_text(item.get("status"))
            if status not in DAILY_STATUS_VALUES:
                errors.append(f"{item_prefix}.status 必须是已完成、处理中、待验证或阻塞")
            if planning_only_work_item(item):
                errors.append(
                    f"{item_prefix} 是尚未开展的明日计划，不得伪造成今日工作；请移入 tomorrow_plan"
                )
        for field in ("key_points", "dependencies"):
            values = row.get(field)
            if not isinstance(values, list):
                errors.append(f"{prefix}.{field} 必须是数组")
            elif any(not isinstance(item, str) or not item.strip() for item in values):
                errors.append(f"{prefix}.{field} 只能包含非空文本")
        if "tomorrow_focus" in row:
            errors.append(f"{prefix}.tomorrow_focus 已废弃；请改用顶层 tomorrow_plan")
    return errors


def validate_no_planning_only_rows(collection: str, rows: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    for index, row in enumerate(rows):
        work_items = row.get("work_items") if isinstance(row.get("work_items"), list) else []
        for item_index, item in enumerate(work_items):
            if planning_only_work_item(item):
                errors.append(
                    f"{collection}[{index}].work_items[{item_index}] 是尚未开展的明日计划，"
                    "不得伪造成今日工作；请移入 tomorrow_plan"
                )
    return errors


def validate_tomorrow_plan(
    value: Any,
    *,
    prefix: str = "tomorrow_plan",
    require_current_gms: bool = True,
) -> list[str]:
    if not isinstance(value, dict):
        return [f"{prefix} 必须是对象"]
    errors: list[str] = []
    normalized = normalize_tomorrow_plan(value)
    project_chains: dict[str, tuple[str, str]] = {}
    project_scopes: dict[tuple[str, ...], int] = {}
    for collection in TOMORROW_PLAN_COLLECTIONS:
        raw_rows = value.get(collection)
        if not isinstance(raw_rows, list):
            errors.append(f"{prefix}.{collection} 必须是数组")
            continue
        if len(normalized[collection]) != len(raw_rows):
            errors.append(f"{prefix}.{collection} 中每一项都必须是对象")
        seen: dict[tuple[str, ...], int] = {}
        for index, row in enumerate(normalized[collection]):
            row_prefix = f"{prefix}.{collection}[{index}]"
            plan_items = row.get("plan_items")
            if not isinstance(plan_items, list) or not plan_items:
                errors.append(f"{row_prefix}.plan_items 必须是非空数组")
            if collection == "projects":
                project = clean_scope_text(row.get("project"))
                canonical = find_company_project(project)
                if not canonical or canonical.upper() != project.upper():
                    errors.append(f"{row_prefix}.project 必须是规范公司项目名")
                customer = clean_scope_text(row.get("customer") or row.get("customer_name"))
                downstream = clean_scope_text(row.get("downstream_customer"))
                if customer in REPORT_MISSING_CUSTOMER_VALUES or not clean_report_customer_name(customer):
                    errors.append(f"{row_prefix}.customer 必须提供直接客户")
                elif not downstream and len(customer.split()) > 1:
                    errors.append(
                        f"{row_prefix}.customer 只能填写直接客户；客户的客户必须写入 downstream_customer"
                    )
                work_type = clean_scope_text(row.get("work_type"))
                if work_type not in PROJECT_WORK_TYPES:
                    errors.append(f"{row_prefix}.work_type 只能是 Patch、App、GMS、Doc 或 Other")
                app_name = clean_scope_text(row.get("app_name"))
                if work_type == "App" and not app_name:
                    errors.append(f"{row_prefix}.app_name 类型为 App 时必须提供")
                if work_type != "App" and app_name:
                    errors.append(f"{row_prefix}.app_name 仅类型为 App 时允许提供")
                if work_type == "GMS" and require_current_gms:
                    errors.extend(validate_gms_fields(row, prefix=row_prefix, plan=True))
                if canonical:
                    chain = (customer, downstream)
                    previous_chain = project_chains.get(canonical)
                    if previous_chain and previous_chain != chain:
                        errors.append(f"{row_prefix} 与同项目其他明日计划的客户链不一致")
                    else:
                        project_chains[canonical] = chain
                    scope = report_scope_key(row)
                    if scope in project_scopes:
                        errors.append(
                            f"{row_prefix} 与 {prefix}.projects[{project_scopes[scope]}] 的统计对象重复"
                        )
                    else:
                        project_scopes[scope] = index
            elif collection == "documents":
                if normalize_standalone_work_type(row.get("work_type")) != DOCUMENT_WORK_TYPE:
                    errors.append(f"{row_prefix}.work_type 必须是 Doc")
                if not clean_document_name(row.get("document_name")):
                    errors.append(f"{row_prefix}.document_name 必须提供具体文档名称")
            else:
                if normalize_standalone_work_type(row.get("work_type")) != "Other":
                    errors.append(f"{row_prefix}.work_type 必须是 Other")
                if not clean_scope_text(row.get("work_name")):
                    errors.append(f"{row_prefix}.work_name 必须提供具体工作名称")
            raw_row = raw_rows[index] if index < len(raw_rows) and isinstance(raw_rows[index], dict) else {}
            for forbidden in (
                "today_topic",
                "current_result",
                "work_items",
                "key_points",
                "dependencies",
                "tomorrow_focus",
                "status",
            ):
                if forbidden in raw_row:
                    errors.append(f"{row_prefix}.{forbidden} 不属于明日计划")
            if collection == "projects" and clean_scope_text(row.get("work_type")) == "GMS":
                for forbidden in set(GMS_CURRENT_FIELDS) - set(GMS_PLAN_FIELDS):
                    if forbidden in raw_row:
                        errors.append(f"{row_prefix}.{forbidden} 不属于明日计划")
            identity = tomorrow_plan_identity(collection, row)
            if identity in seen:
                errors.append(f"{row_prefix} 与 {prefix}.{collection}[{seen[identity]}] 重复")
            else:
                seen[identity] = index
    return errors


def load_explicit_facts(
    path: Path,
    report_date: dt.date,
    *,
    project_items: dict[str, list[tuple[str, str]]],
    daily_work_items: dict[str, list[dict[str, Any]]],
    expected_project_customers: dict[str, Any] | None = None,
    include_documents: bool = False,
    include_all_scopes: bool = False,
) -> (
    list[dict[str, Any]]
    | tuple[list[dict[str, Any]], list[dict[str, Any]]]
    | tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]
    | tuple[
        list[dict[str, Any]],
        list[dict[str, Any]],
        list[dict[str, Any]],
        dict[str, list[dict[str, Any]]],
    ]
):
    payload = read_json_file(path)
    schema = payload.get("schema")
    if schema not in SUPPORTED_DAILY_FACTS_SCHEMAS:
        raise SystemExit(f"daily facts schema 必须是 {DAILY_FACTS_SCHEMA}")
    if clean_scope_text(payload.get("report_date")) != report_date.isoformat():
        raise SystemExit(f"daily facts report_date 必须等于 {report_date.isoformat()}")
    raw_projects = payload.get("projects")
    raw_documents = payload.get("documents", [])
    raw_standalone_work = payload.get("standalone_work", [])
    raw_tomorrow_plan = payload.get("tomorrow_plan", empty_tomorrow_plan())
    if not isinstance(raw_projects, list):
        raise SystemExit("daily facts projects 必须是数组")
    if not isinstance(raw_documents, list):
        raise SystemExit("daily facts documents 必须是数组")
    if not isinstance(raw_standalone_work, list):
        raise SystemExit("daily facts standalone_work 必须是数组")
    if not isinstance(raw_tomorrow_plan, dict):
        raise SystemExit("daily facts tomorrow_plan 必须是对象")
    if schema == LEGACY_DAILY_FACTS_SCHEMA and (raw_documents or raw_standalone_work):
        raise SystemExit(f"非项目工作必须改用 {DAILY_FACTS_SCHEMA}")
    if not raw_projects and not raw_documents and not raw_standalone_work:
        raise SystemExit("daily facts projects、documents 和 standalone_work 至少提供一项")
    project_names = [
        find_company_project(clean_scope_text(item.get("project"))) or clean_scope_text(item.get("project"))
        for item in raw_projects
        if isinstance(item, dict)
    ]
    scope_count_by_project = {project: project_names.count(project) for project in set(project_names)}
    projects = [
        normalize_daily_project(
            item,
            project_items=project_items,
            daily_work_items=daily_work_items,
            scope_count_by_project=scope_count_by_project,
        )
        for item in raw_projects
        if isinstance(item, dict)
    ]
    documents = [normalize_daily_document(item) for item in raw_documents if isinstance(item, dict)]
    standalone_work = [normalize_daily_document(item) for item in raw_standalone_work if isinstance(item, dict)]
    normalized_counts = (len(projects), len(documents), len(standalone_work))
    raw_plan_errors = validate_tomorrow_plan(raw_tomorrow_plan) if schema == DAILY_FACTS_SCHEMA else []
    if schema in {DAILY_FACTS_SCHEMA, LEGACY_DAILY_GMS_FACTS_SCHEMA}:
        legacy_fields = [
            f"{collection}[{index}].tomorrow_focus"
            for collection, rows in (
                ("projects", raw_projects),
                ("documents", raw_documents),
                ("standalone_work", raw_standalone_work),
            )
            for index, row in enumerate(rows)
            if isinstance(row, dict) and "tomorrow_focus" in row
        ]
        if legacy_fields:
            raise SystemExit(
                "daily facts 校验失败: "
                + "；".join(f"{field} 已废弃；请改用顶层 tomorrow_plan" for field in legacy_fields)
            )
    projects, documents, standalone_work, tomorrow_plan = separate_daily_and_tomorrow_plan(
        projects,
        documents,
        standalone_work,
        normalize_tomorrow_plan(raw_tomorrow_plan),
    )
    require_current_gms = schema == DAILY_FACTS_SCHEMA
    errors = validate_daily_projects(
        projects,
        expected_project_customers=expected_project_customers,
        require_current_gms=require_current_gms,
    )
    if not projects and not documents and not standalone_work:
        errors.append("日报必须至少包含一项今日实际工作；明日计划不能替代今日工作")
    errors.extend(validate_daily_documents(documents))
    errors.extend(validate_daily_standalone_work(standalone_work))
    errors.extend(validate_no_planning_only_rows("documents", documents))
    errors.extend(validate_no_planning_only_rows("standalone_work", standalone_work))
    errors.extend(validate_tomorrow_plan(tomorrow_plan, require_current_gms=require_current_gms))
    errors.extend(raw_plan_errors)
    if normalized_counts[0] != len(raw_projects):
        errors.append("projects 中每一项都必须是对象")
    if normalized_counts[1] != len(raw_documents):
        errors.append("documents 中每一项都必须是对象")
    if normalized_counts[2] != len(raw_standalone_work):
        errors.append("standalone_work 中每一项都必须是对象")
    if errors:
        raise SystemExit("daily facts 校验失败: " + "；".join(errors))
    if include_all_scopes:
        return projects, documents, standalone_work, tomorrow_plan
    return (projects, documents) if include_documents else projects


def fallback_projects(
    *,
    project_items: dict[str, list[tuple[str, str]]],
    daily_work_items: dict[str, list[dict[str, Any]]],
    project_customers: dict[str, Any],
    synthetic: bool,
    inferred_scopes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    projects: list[dict[str, Any]] = []
    scopes_by_project: dict[str, list[dict[str, Any]]] = {}
    if not synthetic:
        for scope in inferred_scopes:
            raw_project = clean_scope_text(scope.get("project"))
            project = find_company_project(raw_project)
            if not project:
                continue
            scopes_by_project.setdefault(project, []).append(scope)
    has_non_project_scopes = any(
        not find_company_project(clean_scope_text(scope.get("project")))
        and clean_scope_text(scope.get("work_type")) in {DOCUMENT_WORK_TYPE, "Other"}
        for scope in inferred_scopes
    )
    item_projects = (
        {project for project in project_items if find_company_project(project)}
        if has_non_project_scopes
        else set(project_items)
    )
    project_names = sorted(item_projects | set(scopes_by_project))
    for project in project_names:
        context = report_customer_context_for_project(project, project_customers)
        scope_rows = scopes_by_project.get(project) or [{}]
        for scope in scope_rows:
            raw_scope_items = scope.get("work_items")
            work_items = (
                [normalize_work_item(item) for item in raw_scope_items if isinstance(item, dict)]
                if isinstance(raw_scope_items, list) and raw_scope_items
                else fallback_work_items(project, project_items, daily_work_items)
            )
            work_type = "Patch" if synthetic else clean_scope_text(scope.get("work_type")) or "需成员确认"
            row: dict[str, Any] = {
                "project": project,
                "customer": context["customer_name"],
                "customer_name": context["customer_name"],
                "work_type": work_type,
                "today_topic": topic_from_work_items(work_items),
                "current_result": result_from_work_items(work_items),
                "work_items": work_items,
                "key_points": clean_list(scope.get("key_points")),
                "dependencies": clean_list(scope.get("dependencies")),
            }
            if "tomorrow_focus" in scope:
                row["tomorrow_focus"] = normalize_plan_items(scope.get("tomorrow_focus"))
            app_name = clean_scope_text(scope.get("app_name"))
            if work_type == "App" and app_name:
                row["app_name"] = app_name
            row.update(normalize_gms_fields(scope))
            if context.get("downstream_customer"):
                row["downstream_customer"] = context["downstream_customer"]
            projects.append(row)
    return projects


def fallback_documents(*, inferred_scopes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []
    for scope in inferred_scopes:
        if (
            clean_scope_text(scope.get("work_type")) != DOCUMENT_WORK_TYPE
            or find_company_project(clean_scope_text(scope.get("project")))
        ):
            continue
        raw_items = scope.get("work_items")
        work_items = (
            [normalize_work_item(item) for item in raw_items if isinstance(item, dict)]
            if isinstance(raw_items, list)
            else []
        )
        documents.append(
            {
                "work_type": DOCUMENT_WORK_TYPE,
                "document_name": clean_document_name(scope.get("document_name")),
                "today_topic": topic_from_work_items(work_items),
                "current_result": result_from_work_items(work_items),
                "work_items": work_items,
                "key_points": clean_list(scope.get("key_points")),
                "dependencies": clean_list(scope.get("dependencies")),
            }
        )
        if "tomorrow_focus" in scope:
            documents[-1]["tomorrow_focus"] = normalize_plan_items(scope.get("tomorrow_focus"))
    return documents


def fallback_standalone_work(*, inferred_scopes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for scope in inferred_scopes:
        if (
            clean_scope_text(scope.get("work_type")) != "Other"
            or find_company_project(clean_scope_text(scope.get("project")))
        ):
            continue
        raw_items = scope.get("work_items")
        work_items = (
            [normalize_work_item(item) for item in raw_items if isinstance(item, dict)]
            if isinstance(raw_items, list)
            else []
        )
        work_name = clean_scope_text(scope.get("work_name")) or topic_from_work_items(work_items)
        rows.append(
            {
                "work_type": "Other",
                "work_name": work_name,
                "today_topic": topic_from_work_items(work_items),
                "current_result": result_from_work_items(work_items),
                "work_items": work_items,
                "key_points": clean_list(scope.get("key_points")),
                "dependencies": clean_list(scope.get("dependencies")),
            }
        )
        if "tomorrow_focus" in scope:
            rows[-1]["tomorrow_focus"] = normalize_plan_items(scope.get("tomorrow_focus"))
    return rows


def facts_hash(
    projects: list[dict[str, Any]],
    documents: list[dict[str, Any]],
    standalone_work: list[dict[str, Any]],
    tomorrow_plan: dict[str, list[dict[str, Any]]],
) -> str:
    payload = json.dumps(
        {
            "projects": projects,
            "documents": documents,
            "standalone_work": standalone_work,
            "tomorrow_plan": tomorrow_plan,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_daily_facts(
    report_date: dt.date,
    *,
    explicit_path: str = "",
    synthetic: bool = False,
    project_items: dict[str, list[tuple[str, str]]] | None = None,
    daily_work_items: dict[str, list[dict[str, Any]]] | None = None,
    project_customers: dict[str, Any] | None = None,
    inferred_scopes: list[dict[str, Any]] | None = None,
) -> DailyFactsResult:
    project_items = project_items or {}
    daily_work_items = daily_work_items or {}
    project_customers = project_customers or {}
    inferred_scopes = inferred_scopes or []
    if explicit_path:
        path = expanded_path(explicit_path)
        projects, documents, standalone_work, tomorrow_plan = load_explicit_facts(
            path,
            report_date,
            project_items=project_items,
            daily_work_items=daily_work_items,
            expected_project_customers=project_customers,
            include_all_scopes=True,
        )
        source = "explicit_daily_facts"
        source_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    else:
        projects = fallback_projects(
            project_items=project_items,
            daily_work_items=daily_work_items,
            project_customers=project_customers,
            synthetic=synthetic,
            inferred_scopes=inferred_scopes,
        )
        documents = fallback_documents(inferred_scopes=inferred_scopes)
        standalone_work = fallback_standalone_work(inferred_scopes=inferred_scopes)
        projects, documents, standalone_work, tomorrow_plan = separate_daily_and_tomorrow_plan(
            projects,
            documents,
            standalone_work,
        )
        inference_complete = bool(inferred_scopes) and all(
            (
                (
                    bool(find_company_project(clean_scope_text(row.get("project"))))
                    and clean_scope_text(row.get("work_type")) in PROJECT_WORK_TYPES
                    and (
                        clean_scope_text(row.get("work_type")) != "App"
                        or bool(clean_scope_text(row.get("app_name")))
                    )
                    and (
                        clean_scope_text(row.get("work_type")) != "GMS"
                        or not validate_gms_fields(row, prefix="gms")
                    )
                )
                or (
                    not find_company_project(clean_scope_text(row.get("project")))
                    and (
                        (
                            clean_scope_text(row.get("work_type")) == DOCUMENT_WORK_TYPE
                            and bool(clean_document_name(row.get("document_name")))
                        )
                        or (
                            clean_scope_text(row.get("work_type")) == "Other"
                            and bool(clean_scope_text(row.get("work_name")))
                        )
                    )
                )
                and not bool(row.get("inference_conflict"))
            )
            for row in inferred_scopes
        )
        if synthetic:
            source = "synthetic_fixture"
        elif inference_complete:
            source = "session_scope_inference"
        else:
            source = "session_draft"
        source_sha256 = ""
    missing_fields: list[str] = []
    if not explicit_path and not synthetic:
        for row in projects:
            project = clean_scope_text(row.get("project"))
            work_type = clean_scope_text(row.get("work_type"))
            if work_type not in ALLOWED_WORK_TYPES:
                missing_fields.append(f"{project}.work_type")
            elif work_type == "App" and not clean_scope_text(row.get("app_name")):
                missing_fields.append(f"{project}.app_name")
            elif work_type == "GMS":
                for field in GMS_CURRENT_FIELDS:
                    if field == "gms_current_stage" and row.get("gms_cycle_status") in {
                        "approved",
                        "cancelled",
                    }:
                        continue
                    value = row.get(field)
                    if value in (None, ""):
                        missing_fields.append(f"{project}.{field}")
        for row in documents:
            if not clean_document_name(row.get("document_name")):
                missing_fields.append("document.document_name")
        for row in standalone_work:
            if not clean_scope_text(row.get("work_name")):
                missing_fields.append("standalone_work.work_name")
    scope_inference = []
    if not explicit_path and not synthetic:
        for row in inferred_scopes:
            item: dict[str, Any] = {
                "project": clean_scope_text(row.get("project")),
                "work_type": clean_scope_text(row.get("work_type")) or "unresolved",
                "basis": clean_list(row.get("inference_basis")),
                "conflict": bool(row.get("inference_conflict")),
            }
            if clean_scope_text(row.get("app_name")):
                item["app_name"] = clean_scope_text(row.get("app_name"))
            if clean_document_name(row.get("document_name")):
                item["document_name"] = clean_document_name(row.get("document_name"))
            if clean_scope_text(row.get("work_name")):
                item["work_name"] = clean_scope_text(row.get("work_name"))
            scope_inference.append(item)
    evidence = {
        "schema": DAILY_FACT_SOURCES_SCHEMA,
        "report_date": report_date.isoformat(),
        "source": source,
        "source_sha256": source_sha256,
        "project_count": len({clean_scope_text(row.get("project")) for row in projects}),
        "document_count": len(documents),
        "standalone_work_count": len(standalone_work),
        "work_scope_count": len(projects) + len(documents) + len(standalone_work),
        "tomorrow_plan_scope_count": sum(len(tomorrow_plan[collection]) for collection in TOMORROW_PLAN_COLLECTIONS),
        "missing_fields": sorted(set(missing_fields)),
        "scope_inference": scope_inference,
        "facts_sha256": facts_hash(projects, documents, standalone_work, tomorrow_plan),
    }
    return DailyFactsResult(projects, documents, standalone_work, tomorrow_plan, evidence)


def project_rows_to_items(projects: list[dict[str, Any]]) -> dict[str, list[tuple[str, str]]]:
    result: dict[str, list[tuple[str, str]]] = {}
    for row in projects:
        project = clean_scope_text(row.get("project"))
        for item in row.get("work_items", []) if isinstance(row.get("work_items"), list) else []:
            if not isinstance(item, dict):
                continue
            name = clean_scope_text(item.get("name"))
            progress = " ".join(
                value
                for value in (
                    clean_scope_text(item.get("status")),
                    clean_scope_text(item.get("result")),
                )
                if value
            )
            if project and name:
                result.setdefault(project, []).append((name, progress))
    return result
