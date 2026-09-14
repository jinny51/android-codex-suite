from __future__ import annotations

import datetime as dt
import hashlib
import re
from pathlib import Path
from typing import Any, Callable

from akbs_member_ops.knowledge_rules import find_company_project

from ..report_sessions import clean_report_customer_name
from ..session_privacy import session_evidence_errors
from .daily_facts import (
    DAILY_FACT_SOURCES_SCHEMA,
    LEGACY_DAILY_GMS_FACT_SOURCES_SCHEMA,
    SUPPORTED_DAILY_FACT_SOURCES_SCHEMAS,
    planning_only_work_item,
    validate_tomorrow_plan,
)
from .document_work import (
    DOCUMENT_WORK_TYPE,
    DOCUMENT_WORK_TYPES,
    STANDALONE_WORK_TYPES,
    clean_document_name,
    clean_list as clean_document_list,
    normalize_standalone_work_type,
    standalone_work_name,
    validate_daily_documents,
    validate_daily_standalone_work,
)
from .gms import validate_gms_fields
from .render import (
    REPORT_MISSING_CUSTOMER_VALUES,
    REPORT_MISSING_PROJECT_VALUES,
    report_markdown_from_view,
)
from .render_binding import REPORT_RENDER_BINDING_SCHEMA
from .weekly_facts import (
    LEGACY_WEEKLY_FACT_SOURCES_SCHEMA,
    OLDEST_WEEKLY_FACT_SOURCES_SCHEMA,
    WEEKLY_FACT_SOURCES_SCHEMA,
)
from .weekly_ledger import WEEKLY_LEDGER_SCHEMA


RequireFile = Callable[[Any, str], Path | None]
LoadEvidence = Callable[[list[Any]], dict[str, dict[str, Any]]]
ReadReferencedJson = Callable[[Path, str], dict[str, Any] | None]


FORBIDDEN_REPORT_VIEW_FIELDS = {
    "ui_card",
    "one_line_summary",
    "display_title",
    "daily_overview",
    "work_items",
    "items",
    "outputs",
    "next_steps",
    "project_overview",
    "source_lists",
    "source_category_stats",
    "project_ledgers",
    "weekly_detail_sections",
    "weekly_progress_summary",
    "patch_outputs",
    "delivery_verifications",
}
WEEKLY_ALLOWED_SOURCES = {"CR", "TL", "PM", "TE", "BSP"}
WEEKLY_ALLOWED_PROJECT_ROLES = {"主责", "协作"}
WEEKLY_ALLOWED_WORK_TYPES = {"Patch", "App", "GMS", "Doc", "Other"}
WEEKLY_EMPTY_PLAN_VALUES = {"无", "无。", "暂无", "暂无。", "无下周计划", "暂无下周计划"}
WEEKLY_COUNT_RE = re.compile(
    r"^(?P<label>共|本周完成|当前剩余)\s+(?P<total>\d+)\s*项(?:：(?P<parts>.+))?$"
)
WEEKLY_COUNT_PART_RE = re.compile(r"^(需求|移植|Bug|BSP)\s+(\d+)$", re.IGNORECASE)
WEEKLY_SCALAR_COUNT_RE = re.compile(r"^(?P<label>共|本周完成|当前剩余)\s+(?P<total>\d+)\s*项$")
DAILY_STATUS_VALUES = {"已完成", "处理中", "待验证", "阻塞"}
OLD_DAILY_HOW_TEXT = "根据 Codex 会话记录、工程修改、命令执行和材料证据整理实际处理过程。"
MISSING_PROJECT_GUIDANCE = (
    "当前会话未关联项目，请补充项目名和客户名；例如：TVE1086U 青鸾云；"
    "如有客户的客户：TVE1091U AOC 福建移动高清。"
    "建议后续先创建项目，再在项目下创建开发会话。无项目文档使用 documents，其他无项目工作使用 standalone_work，"
    "不得把“文档”伪造成项目编号。"
)


def report_project_customer_errors(
    rel: str,
    rows: Any,
    label: str,
    *,
    allow_work_scopes: bool = False,
) -> list[str]:
    row_errors: list[str] = []
    if not isinstance(rows, list) or not rows:
        row_errors.append(f"{rel} payload.{label} 必须包含项目和客户信息")
        return row_errors
    seen_projects: dict[str, tuple[int, str, str]] = {}
    seen_scopes: dict[tuple[str, str, str], int] = {}
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            row_errors.append(f"{rel} payload.{label}[{index}] 必须是对象")
            continue
        project = str(row.get("project") or "").strip()
        canonical_project = find_company_project(project)
        if project in REPORT_MISSING_PROJECT_VALUES or not canonical_project:
            row_errors.append(f"{rel} payload.{label}[{index}].project 未识别到公司项目名。{MISSING_PROJECT_GUIDANCE}")
        elif project.upper() != canonical_project.upper():
            row_errors.append(
                f"{rel} payload.{label}[{index}].project 只能填写规范项目编号 {canonical_project}；其他内容应写入事项字段"
            )
        customer = str(row.get("customer") or row.get("customer_name") or "").strip()
        compatibility_customer = str(row.get("customer_name") or "").strip()
        if customer and compatibility_customer and customer != compatibility_customer:
            row_errors.append(
                f"{rel} payload.{label}[{index}].customer 与 customer_name 必须一致"
            )
        if customer in REPORT_MISSING_CUSTOMER_VALUES or not clean_report_customer_name(customer):
            row_errors.append(
                f"{rel} payload.{label}[{index}].customer 缺少客户名，请按“项目名 客户名”补充；可选第三段为客户的客户，例如：TVE1091U AOC 福建移动高清"
            )
        downstream_customer = str(
            row.get("downstream_customer")
            or row.get("customer_of_customer")
            or row.get("end_customer")
            or ""
        ).strip()
        if downstream_customer and not clean_report_customer_name(downstream_customer):
            row_errors.append(
                f"{rel} payload.{label}[{index}].downstream_customer 不是有效的客户名称"
            )
        if canonical_project:
            identity = (customer, downstream_customer)
            previous = seen_projects.get(canonical_project)
            if previous:
                previous_index, previous_customer, previous_downstream = previous
                if identity != (previous_customer, previous_downstream):
                    row_errors.append(
                        f"{rel} payload.{label}[{index}] 与 [{previous_index}] 的 {canonical_project} 客户链冲突；"
                        "同一项目必须保留唯一客户链"
                    )
                elif not allow_work_scopes:
                    row_errors.append(
                        f"{rel} payload.{label}[{index}].project 与 [{previous_index}] 重复；"
                        "同一项目只能有一行，具体工作内容应合并到事项数组"
                    )
            else:
                seen_projects[canonical_project] = (index, customer, downstream_customer)
            if allow_work_scopes:
                work_type = str(row.get("work_type") or "").strip()
                app_name = str(row.get("app_name") or "").strip().casefold() if work_type == "App" else ""
                scope = (canonical_project, work_type, app_name)
                previous_scope = seen_scopes.get(scope)
                if previous_scope is not None:
                    row_errors.append(
                        f"{rel} payload.{label}[{index}] 与 [{previous_scope}] 的统计对象重复；"
                        "同一项目工作范围只能有一行"
                    )
                else:
                    seen_scopes[scope] = index
    return row_errors


def validate_daily_project_inference(
    *,
    manifest: dict[str, Any],
    evidence_by_kind: dict[str, dict[str, Any]],
    errors: list[str],
) -> None:
    project_inference = evidence_by_kind.get("project_inference")
    if not project_inference:
        errors.append("daily_trace 缺少 project_inference evidence")
        return
    project_payload = project_inference.get("payload", {}) if isinstance(project_inference, dict) else {}
    project = str(project_payload.get("project") or "")
    inferred_projects = (
        [str(value).strip() for value in project_payload.get("projects", []) if str(value).strip()]
        if isinstance(project_payload.get("projects"), list)
        else []
    )
    if project and project != "unknown" and project not in inferred_projects:
        inferred_projects.append(project)
    non_project_work = bool(project_payload.get("non_project_work"))
    if not inferred_projects and project == "unknown" and non_project_work:
        if not manifest.get("has_non_project_work"):
            errors.append("非项目日报 manifest.has_non_project_work 必须为 true")
        documents = project_payload.get("documents")
        standalone_work = project_payload.get("standalone_work")
        has_document = isinstance(documents, list) and any(clean_document_name(value) for value in documents)
        has_standalone = isinstance(standalone_work, list) and any(str(value or "").strip() for value in standalone_work)
        if not has_document and not has_standalone:
            errors.append("非项目日报 project_inference 必须提供 documents 或 standalone_work 名称")
    elif not inferred_projects and project == "unknown":
        errors.append(f"project_inference.project 未识别到公司项目名。{MISSING_PROJECT_GUIDANCE}")
        if not isinstance(project_payload.get("checked_sources"), list) or not project_payload.get("checked_sources"):
            errors.append("unknown project_inference 必须记录 checked_sources")
        if not isinstance(project_payload.get("limits"), list) or not project_payload.get("limits"):
            errors.append("unknown project_inference 必须记录 limits")
    elif not inferred_projects:
        errors.append("project_inference.projects 必须至少提供一个项目")
    manifest_projects = manifest.get("projects") if isinstance(manifest.get("projects"), list) else []
    if manifest_projects:
        if sorted(str(value) for value in manifest_projects) != sorted(inferred_projects):
            errors.append("daily_trace manifest.projects 必须等于 project_inference.projects")
    elif len(inferred_projects) == 1 and manifest.get("project") != inferred_projects[0]:
        errors.append("daily_trace manifest.project 必须等于 project_inference.project")
    customer_rows = (
        project_payload.get("project_customers")
        if isinstance(project_payload.get("project_customers"), list)
        else []
    )
    if not customer_rows and project and project != "unknown":
        customer_rows = [
            {
                "project": project,
                "customer_name": project_payload.get("customer_name"),
                "downstream_customer": project_payload.get("downstream_customer"),
            }
        ]
    customers_by_project = {
        str(row.get("project") or ""): row
        for row in customer_rows
        if isinstance(row, dict)
    }
    for inferred_project in inferred_projects:
        row = customers_by_project.get(inferred_project, {})
        customer = str(row.get("customer_name") or row.get("customer") or "").strip()
        if customer in REPORT_MISSING_CUSTOMER_VALUES or not clean_report_customer_name(customer):
            errors.append(
                f"project_inference.project_customers[{inferred_project}] 缺少客户名，请按“项目名 客户名”补充"
            )
        downstream_customer = str(row.get("downstream_customer") or "").strip()
        if downstream_customer and not clean_report_customer_name(downstream_customer):
            errors.append(
                f"project_inference.project_customers[{inferred_project}].downstream_customer 不是有效客户名称"
            )


def validate_daily_fact_sources(
    *,
    manifest: dict[str, Any],
    evidence_by_kind: dict[str, dict[str, Any]],
    errors: list[str],
) -> None:
    evidence = evidence_by_kind.get("daily_fact_sources")
    if not isinstance(evidence, dict):
        errors.append("daily_trace 缺少 daily_fact_sources evidence")
        return
    payload = evidence.get("payload")
    if not isinstance(payload, dict):
        errors.append("daily_fact_sources payload 必须是对象")
        return
    if payload.get("schema") not in SUPPORTED_DAILY_FACT_SOURCES_SCHEMAS:
        errors.append(f"daily_fact_sources.schema 必须是 {DAILY_FACT_SOURCES_SCHEMA}")
    if payload.get("report_date") != manifest.get("date"):
        errors.append("daily_fact_sources.report_date 必须等于 manifest.date")
    counts: dict[str, int] = {}
    for field in (
        "project_count",
        "document_count",
        "standalone_work_count",
        "work_scope_count",
        "tomorrow_plan_scope_count",
    ):
        try:
            count = int(payload.get(field) or 0)
        except (TypeError, ValueError):
            count = 0
        counts[field] = count
    if counts["work_scope_count"] < 1:
        errors.append("daily_fact_sources.work_scope_count 必须大于 0")
    if counts["project_count"] + counts["document_count"] + counts["standalone_work_count"] < 1:
        errors.append("daily_fact_sources 必须至少包含一个项目、文档或独立工作范围")
    if counts["tomorrow_plan_scope_count"] < 0:
        errors.append("daily_fact_sources.tomorrow_plan_scope_count 必须是非负整数")
    missing = payload.get("missing_fields")
    if not isinstance(missing, list):
        errors.append("daily_fact_sources.missing_fields 必须是数组")
    elif missing:
        errors.append(
            "日报范围事实不完整，请补充后使用 --daily-facts 重新生成: "
            + "、".join(str(item) for item in missing)
        )
    if not re.fullmatch(r"[0-9a-f]{64}", str(payload.get("facts_sha256") or "")):
        errors.append("daily_fact_sources.facts_sha256 必须提供")


def validate_work_findings(
    *,
    evidence_by_kind: dict[str, dict[str, Any]],
    package_status_values: set[str],
    errors: list[str],
) -> None:
    work_findings = evidence_by_kind.get("work_findings", {})
    payload = work_findings.get("payload", {}) if isinstance(work_findings, dict) else {}
    if not isinstance(payload.get("scanned_sources"), list) or not payload.get("scanned_sources"):
        errors.append("work_findings.scanned_sources 必须是非空数组")
    if not isinstance(payload.get("items", []), list):
        errors.append("work_findings.items 必须是数组")
    for item in payload.get("items", []) if isinstance(payload.get("items", []), list) else []:
        if "maturity" in item:
            errors.append("work_findings item 不允许使用 maturity；请使用 work_status")
        work_status = item.get("work_status")
        if work_status and work_status not in package_status_values:
            errors.append(f"work_findings item work_status 非法: {work_status}")


def validate_session_privacy_evidence(*, evidence_by_kind: dict[str, dict[str, Any]], errors: list[str]) -> None:
    evidence = evidence_by_kind.get("codex_sessions")
    if not isinstance(evidence, dict):
        errors.append("report trace 缺少 codex_sessions privacy evidence")
        return
    errors.extend(session_evidence_errors(evidence.get("payload")))


def validate_weekly_fact_sources(
    *,
    manifest: dict[str, Any],
    evidence_by_kind: dict[str, dict[str, Any]],
    errors: list[str],
) -> None:
    evidence = evidence_by_kind.get("weekly_fact_sources")
    if not isinstance(evidence, dict):
        errors.append("weekly_trace 缺少 weekly_fact_sources evidence")
        return
    payload = evidence.get("payload")
    if not isinstance(payload, dict):
        errors.append("weekly_fact_sources payload 必须是对象")
        return
    if payload.get("schema") not in {
        WEEKLY_FACT_SOURCES_SCHEMA,
        LEGACY_WEEKLY_FACT_SOURCES_SCHEMA,
        OLDEST_WEEKLY_FACT_SOURCES_SCHEMA,
    }:
        errors.append(f"weekly_fact_sources.schema 必须是 {WEEKLY_FACT_SOURCES_SCHEMA}")
    if payload.get("week_range") != manifest.get("week_range"):
        errors.append("weekly_fact_sources.week_range 必须等于 manifest.week_range")
    try:
        project_count = int(payload.get("project_count") or 0)
    except (TypeError, ValueError):
        project_count = 0
    try:
        document_count = int(payload.get("document_count") or 0)
    except (TypeError, ValueError):
        document_count = 0
    try:
        standalone_work_count = int(payload.get("standalone_work_count") or 0)
    except (TypeError, ValueError):
        standalone_work_count = 0
    if project_count + document_count + standalone_work_count < 1:
        errors.append("周报未形成项目、文档或独立工作事实，请补充有效日报、上一周周报或 --weekly-facts")
    missing = payload.get("missing_fields")
    if not isinstance(missing, list):
        errors.append("weekly_fact_sources.missing_fields 必须是数组")
    elif missing:
        errors.append("周报项目事实不完整，请补充后使用 --weekly-facts 重新生成: " + "、".join(str(item) for item in missing))
    identity_conflicts = payload.get("identity_conflicts", [])
    if not isinstance(identity_conflicts, list):
        errors.append("weekly_fact_sources.identity_conflicts 必须是数组")
    elif identity_conflicts:
        errors.append("周报项目客户链与当前会话已确认身份冲突，请修正结构化事实后重新生成")
    attention_candidates = payload.get("attention_review_candidates", [])
    if not isinstance(attention_candidates, list):
        errors.append("weekly_fact_sources.attention_review_candidates 必须是数组")


def weekly_project_identity_consistency_errors(
    rel: str,
    rows: Any,
    expected_rows: Any,
) -> list[str]:
    if not isinstance(rows, list) or not isinstance(expected_rows, list) or not expected_rows:
        return []

    def identities(values: list[Any]) -> dict[str, tuple[str, str]]:
        result: dict[str, tuple[str, str]] = {}
        for value in values:
            if not isinstance(value, dict):
                continue
            project = find_company_project(str(value.get("project") or ""))
            customer = str(value.get("customer") or value.get("customer_name") or "").strip()
            downstream = str(value.get("downstream_customer") or "").strip()
            if project and customer:
                result[project] = (customer, downstream)
        return result

    actual = identities(rows)
    expected = identities(expected_rows)
    if actual == expected:
        return []
    return [
        f"{rel} payload.projects 项目客户身份必须与 project_inference.project_customers 来源证据一致"
    ]


def validate_daily_report_view_project(
    rel: str,
    index: int,
    project: dict[str, Any],
    errors: list[str],
    *,
    contract_v3: bool,
    require_current_gms: bool,
) -> None:
    work_type = str(project.get("work_type") or "").strip()
    if work_type not in WEEKLY_ALLOWED_WORK_TYPES:
        errors.append(f"{rel} payload.projects[{index}].work_type 只能是 Patch、App、GMS、Doc 或 Other")
    if work_type == "App" and not str(project.get("app_name") or "").strip():
        errors.append(f"{rel} payload.projects[{index}].app_name 类型为 App 时必须提供")
    if work_type != "App" and project.get("app_name"):
        errors.append(f"{rel} payload.projects[{index}].app_name 仅类型为 App 时允许提供")
    if work_type == "GMS" and require_current_gms:
        errors.extend(validate_gms_fields(project, prefix=f"{rel} payload.projects[{index}]"))
    for field in ("today_topic", "current_result"):
        if not project.get(field):
            errors.append(f"{rel} payload.projects[{index}].{field} 必须提供")
    for field in ("work_items", "key_points", "dependencies"):
        if not isinstance(project.get(field), list):
            errors.append(f"{rel} payload.projects[{index}].{field} 必须是数组")
    if contract_v3 and "tomorrow_focus" in project:
        errors.append(
            f"{rel} payload.projects[{index}].tomorrow_focus 已废弃；请改用顶层 tomorrow_plan"
        )
    if not contract_v3 and not isinstance(project.get("tomorrow_focus"), list):
        errors.append(f"{rel} payload.projects[{index}].tomorrow_focus 必须是数组")
    for field in ("key_points", "dependencies"):
        values = project.get(field)
        if isinstance(values, list) and any(
            not isinstance(value, str) or not value.strip()
            for value in values
        ):
            errors.append(f"{rel} payload.projects[{index}].{field} 只能包含非空文本")
    work_items = project.get("work_items") if isinstance(project.get("work_items"), list) else []
    if not work_items:
        errors.append(f"{rel} payload.projects[{index}].work_items 必须至少包含一项今日工作")
    for item_index, item in enumerate(work_items):
        prefix = f"{rel} payload.projects[{index}].work_items[{item_index}]"
        if not isinstance(item, dict):
            errors.append(f"{prefix} 必须是对象")
            continue
        for field in ("name", "result"):
            if not str(item.get(field) or "").strip():
                errors.append(f"{prefix}.{field} 必须提供")
        for field in ("did", "how"):
            rows = item.get(field)
            if not isinstance(rows, list) or not any(str(value or "").strip() for value in rows):
                errors.append(f"{prefix}.{field} 必须是非空数组")
        if OLD_DAILY_HOW_TEXT in [str(value) for value in item.get("how", []) if isinstance(item.get("how"), list)]:
            errors.append(f"{prefix}.how 不得使用固定套话，必须写实际处理方法")
        if item.get("status") not in DAILY_STATUS_VALUES:
            errors.append(f"{prefix}.status 必须是已完成、处理中、待验证或阻塞")
        if contract_v3 and planning_only_work_item(item):
            errors.append(
                f"{prefix} 是尚未开展的明日计划，不得伪造成今日工作；请移入 payload.tomorrow_plan"
            )


def parse_weekly_count_text(
    value: Any,
    *,
    expected_label: str,
    allow_bsp: bool,
    field_path: str,
    errors: list[str],
) -> dict[str, int] | None:
    match = WEEKLY_COUNT_RE.fullmatch(str(value or "").strip())
    if not match or match.group("label") != expected_label:
        errors.append(f"{field_path} 计数格式非法，应使用“{expected_label} N 项：需求 N、移植 N、Bug N”")
        return None
    counts = {"demand": 0, "migration": 0, "bug": 0, "bsp": 0}
    label_keys = {"需求": "demand", "移植": "migration", "bug": "bug", "bsp": "bsp"}
    parts_text = str(match.group("parts") or "").strip()
    seen: set[str] = set()
    if parts_text:
        for part in re.split(r"、", parts_text):
            part_match = WEEKLY_COUNT_PART_RE.fullmatch(part.strip())
            if not part_match:
                errors.append(f"{field_path} 含非法分类: {part.strip()}")
                return None
            key = label_keys[part_match.group(1).lower()]
            count = int(part_match.group(2))
            if key in seen:
                errors.append(f"{field_path} 分类重复: {part_match.group(1)}")
                return None
            if count == 0:
                errors.append(f"{field_path} 为 0 的分类应省略")
            if key == "bsp" and not allow_bsp:
                errors.append(f"{field_path} 本周完成不得包含 BSP")
            seen.add(key)
            counts[key] = count
    total = int(match.group("total"))
    if total != sum(counts.values()):
        errors.append(f"{field_path} 合计与分类不一致")
    if total > 0 and not any(counts[key] > 0 for key in ("demand", "migration", "bug")):
        errors.append(f"{field_path} 至少要有一项需求、移植或 Bug，不能只填 BSP")
    return counts


def parse_weekly_scalar_count_text(
    value: Any,
    *,
    expected_label: str,
    field_path: str,
    errors: list[str],
) -> int | None:
    match = WEEKLY_SCALAR_COUNT_RE.fullmatch(str(value or "").strip())
    if not match or match.group("label") != expected_label:
        errors.append(f"{field_path} 计数格式非法，应使用“{expected_label} N 项”")
        return None
    return int(match.group("total"))


def validate_weekly_report_view_ledger(
    rel: str,
    index: int,
    project: dict[str, Any],
    errors: list[str],
) -> None:
    ledger = project.get("ledger")
    if ledger is None:
        return
    prefix = f"{rel} payload.projects[{index}].ledger"
    if not isinstance(ledger, dict):
        errors.append(f"{prefix} 必须是对象")
        return
    if ledger.get("schema") != WEEKLY_LEDGER_SCHEMA:
        errors.append(f"{prefix}.schema 必须是 {WEEKLY_LEDGER_SCHEMA}")
    expected_ledger_fields = {
        "schema",
        "opening",
        "baseline_package_key",
        "baseline_week_range",
        "project_completed",
        "changes",
        "bsp_pending",
        "baseline",
    }
    if set(ledger) != expected_ledger_fields:
        errors.append(f"{prefix} 必须提供固定的 v2 台账字段")
    if not isinstance(ledger.get("opening"), bool):
        errors.append(f"{prefix}.opening 必须是布尔值")
    for field in ("baseline_package_key", "baseline_week_range"):
        if not isinstance(ledger.get(field), str):
            errors.append(f"{prefix}.{field} 必须是字符串")
    changes = ledger.get("changes")
    if not isinstance(changes, dict):
        errors.append(f"{prefix}.changes 必须是对象")
        return
    expected_changes = {
        "added",
        "reopened",
        "closed_without_change",
        "removed",
        "transferred_to_bsp",
        "bsp_closed",
    }
    if set(changes) != expected_changes:
        errors.append(f"{prefix}.changes 必须提供固定的六类流转字段")
    work_type = project.get("work_type")
    values = [ledger.get("project_completed"), *(changes.get(key) for key in expected_changes)]
    values.append(ledger.get("bsp_pending"))
    baseline = ledger.get("baseline")
    if not isinstance(baseline, dict) or set(baseline) != {"total", "android_remaining", "bsp_pending"}:
        errors.append(f"{prefix}.baseline 必须提供 total、android_remaining 和 bsp_pending")
        baseline_values: list[Any] = []
    else:
        baseline_values = [baseline.get("total"), baseline.get("android_remaining"), baseline.get("bsp_pending")]
    values.extend(baseline_values)
    if work_type == "App":
        if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in values):
            errors.append(f"{prefix} App 流转计数必须是非负整数")
    else:
        for value in values:
            if not isinstance(value, dict) or set(value) != {"demand", "migration", "bug"}:
                errors.append(f"{prefix} Patch 流转计数必须只包含 demand/migration/bug")
                break
            if any(not isinstance(count, int) or isinstance(count, bool) or count < 0 for count in value.values()):
                errors.append(f"{prefix} Patch 流转计数必须是非负整数")
                break
    if not isinstance(project.get("project_change"), str) or not project.get("project_change"):
        errors.append(f"{rel} payload.projects[{index}].project_change 必须提供")
    if not isinstance(project.get("bsp_pending"), str) or not project.get("bsp_pending"):
        errors.append(f"{rel} payload.projects[{index}].bsp_pending 必须提供")


def validate_weekly_report_view_project(rel: str, index: int, project: dict[str, Any], errors: list[str]) -> None:
    for old_field in ("received_date", "source", "requirement_type", "expected_finish"):
        if old_field in project:
            errors.append(f"{rel} payload.projects[{index}].{old_field} 是旧周报字段，不得继续提供")
    required_fields = [
        "week_summary",
        "work_type",
        "project_role",
        "requirement_date",
        "requirement_source",
    ]
    if project.get("work_type") in {"Patch", "App"}:
        required_fields.extend(["completed_this_week", "remaining"])
    for field in required_fields:
        if not project.get(field):
            errors.append(f"{rel} payload.projects[{index}].{field} 必须提供")
    role = project.get("project_role")
    if role not in WEEKLY_ALLOWED_PROJECT_ROLES:
        errors.append(f"{rel} payload.projects[{index}].project_role 只能是主责或协作")
    work_type = project.get("work_type")
    if work_type not in WEEKLY_ALLOWED_WORK_TYPES:
        errors.append(f"{rel} payload.projects[{index}].work_type 只能是 Patch、App、GMS、Doc 或 Other")
    if work_type == "App" and not str(project.get("app_name") or "").strip():
        errors.append(f"{rel} payload.projects[{index}].app_name 类型为 App 时必须提供")
    if work_type != "App" and project.get("app_name"):
        errors.append(f"{rel} payload.projects[{index}].app_name 仅类型为 App 时允许提供")
    if work_type == "GMS":
        errors.extend(validate_gms_fields(project, prefix=f"{rel} payload.projects[{index}]"))
    if role == "主责" and work_type == "Patch" and not project.get("requirement_structure"):
        errors.append(f"{rel} payload.projects[{index}].requirement_structure Patch 主责必须提供")
    if role == "主责" and work_type == "App" and not project.get("work_total"):
        errors.append(f"{rel} payload.projects[{index}].work_total App 主责必须提供")
    if project.get("requirement_source") not in WEEKLY_ALLOWED_SOURCES:
        errors.append(
            f"{rel} payload.projects[{index}].requirement_source 只能是 CR、TL、PM、TE 或 BSP"
        )
    requirement_date = str(project.get("requirement_date") or "")
    try:
        valid_requirement_date = bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", requirement_date))
        if valid_requirement_date:
            dt.date.fromisoformat(requirement_date)
    except ValueError:
        valid_requirement_date = False
    if not valid_requirement_date:
        errors.append(f"{rel} payload.projects[{index}].requirement_date 必须是 YYYY-MM-DD")
    if "下周继续" in str(project.get("week_summary") or ""):
        errors.append(f"{rel} payload.projects[{index}].week_summary 不得用下周计划代替本周进展")
    parsed_completed_total = 0
    parsed_remaining_total = 0
    if project.get("display_name") or project.get("model"):
        errors.append(f"{rel} payload.projects[{index}].display_name/model 不得参与项目身份")
    if work_type in {"GMS", "Doc", "Other"}:
        if any(project.get(field) for field in ("requirement_structure", "work_total", "ledger")):
            errors.append(f"{rel} payload.projects[{index}] {work_type} 不得填写 Patch/App 总账字段")
    elif work_type == "App":
        if project.get("requirement_structure"):
            errors.append(f"{rel} payload.projects[{index}].requirement_structure 类型为 App 时不得提供")
        parsed_scalars: dict[str, int] = {}
        count_specs = (
            ("work_total", "共"),
            ("completed_this_week", "本周完成"),
            ("remaining", "当前剩余"),
        )
        for field, label in count_specs:
            if field == "work_total" and not project.get(field):
                continue
            count = parse_weekly_scalar_count_text(
                project.get(field),
                expected_label=label,
                field_path=f"{rel} payload.projects[{index}].{field}",
                errors=errors,
            )
            if count is not None:
                parsed_scalars[field] = count
        parsed_remaining_total = parsed_scalars.get("remaining", 0)
        parsed_completed_total = parsed_scalars.get("completed_this_week", 0)
        if all(field in parsed_scalars for field in ("work_total", "completed_this_week", "remaining")):
            if parsed_scalars["completed_this_week"] + parsed_scalars["remaining"] > parsed_scalars["work_total"]:
                errors.append(f"{rel} payload.projects[{index}] App 本周完成加当前剩余不能超过 App 总量")
    else:
        parsed_counts: dict[str, dict[str, int]] = {}
        count_specs = (
            ("requirement_structure", "共", True),
            ("completed_this_week", "本周完成", False),
            ("remaining", "当前剩余", True),
        )
        for field, label, allow_bsp in count_specs:
            if field == "requirement_structure" and not project.get(field):
                continue
            counts = parse_weekly_count_text(
                project.get(field),
                expected_label=label,
                allow_bsp=allow_bsp,
                field_path=f"{rel} payload.projects[{index}].{field}",
                errors=errors,
            )
            if counts is not None:
                parsed_counts[field] = counts
        if "remaining" in parsed_counts:
            parsed_remaining_total = sum(parsed_counts["remaining"].values())
        if "completed_this_week" in parsed_counts:
            parsed_completed_total = sum(parsed_counts["completed_this_week"].values())
        if all(field in parsed_counts for field in ("requirement_structure", "completed_this_week", "remaining")):
            total = parsed_counts["requirement_structure"]
            completed = parsed_counts["completed_this_week"]
            remaining = parsed_counts["remaining"]
            for category in ("demand", "migration", "bug", "bsp"):
                if completed[category] + remaining[category] > total[category]:
                    errors.append(f"{rel} payload.projects[{index}].{category} 本周完成加当前剩余不能超过项目总量")
        if project.get("ledger") is not None:
            for field in ("requirement_structure", "remaining"):
                if parsed_counts.get(field, {}).get("bsp", 0) > 0:
                    errors.append(
                        f"{rel} payload.projects[{index}].{field} v5 不得把 BSP 当作事项类型"
                    )
    for field in ("completed_items", "remaining_items", "key_points", "risks", "dependencies", "next_week_plan"):
        if not isinstance(project.get(field), list):
            errors.append(f"{rel} payload.projects[{index}].{field} 必须是数组")
    completed_items = (
        [str(item).strip() for item in project.get("completed_items", []) if str(item).strip() not in {"暂无明确完成项"} and str(item).strip()]
        if isinstance(project.get("completed_items"), list)
        else []
    )
    remaining_items = (
        [str(item).strip() for item in project.get("remaining_items", []) if str(item).strip() not in {"无明确剩余项"} and str(item).strip()]
        if isinstance(project.get("remaining_items"), list)
        else []
    )
    if parsed_completed_total > 0 and not completed_items:
        errors.append(f"{rel} payload.projects[{index}].completed_items 本周完成大于 0 时必须提供")
    if parsed_remaining_total > 0 and not remaining_items:
        errors.append(f"{rel} payload.projects[{index}].remaining_items 当前剩余大于 0 时必须提供")
    next_week_plan = project.get("next_week_plan")
    effective_next_week_plan = (
        [str(item).strip() for item in next_week_plan if str(item).strip() not in WEEKLY_EMPTY_PLAN_VALUES and str(item).strip()]
        if isinstance(next_week_plan, list)
        else []
    )
    if isinstance(next_week_plan, list) and any(str(item).strip() in WEEKLY_EMPTY_PLAN_VALUES for item in next_week_plan):
        errors.append(
            f"{rel} payload.projects[{index}].next_week_plan 不得使用“无”或空计划占位；没有下周动作时应使用空数组"
        )
    progress_remaining = (
        work_type in {"GMS", "Doc", "Other"}
        and any(str(value or "").strip() not in {"", "无明确剩余项"} for value in project.get("remaining_items", []))
    )
    if (parsed_remaining_total > 0 or progress_remaining) and not effective_next_week_plan:
        errors.append(f"{rel} payload.projects[{index}].next_week_plan 有剩余事项时必须提供")
    if work_type in {"Patch", "App"}:
        validate_weekly_report_view_ledger(rel, index, project, errors)


def validate_daily_report_view_documents(
    rel: str,
    documents: Any,
    errors: list[str],
    *,
    contract_v3: bool,
) -> None:
    prefix = f"{rel} payload.documents"
    errors.extend(
        validate_daily_documents(
            documents,
            prefix=prefix,
            allow_legacy_tomorrow_focus=not contract_v3,
        )
    )
    if not isinstance(documents, list):
        return
    for index, document in enumerate(documents):
        if not isinstance(document, dict):
            continue
        work_items = document.get("work_items") if isinstance(document.get("work_items"), list) else []
        for item_index, item in enumerate(work_items):
            if not isinstance(item, dict):
                continue
            if OLD_DAILY_HOW_TEXT in clean_document_list(item.get("how")):
                errors.append(
                    f"{prefix}[{index}].work_items[{item_index}].how 不得使用固定套话，必须写实际处理方法"
                )
            if contract_v3 and planning_only_work_item(item):
                errors.append(
                    f"{prefix}[{index}].work_items[{item_index}] 是尚未开展的明日计划，"
                    "不得伪造成今日工作；请移入 payload.tomorrow_plan"
                )


def validate_daily_report_view_standalone_work(
    rel: str,
    rows: Any,
    errors: list[str],
    *,
    contract_v3: bool,
) -> None:
    prefix = f"{rel} payload.standalone_work"
    errors.extend(
        validate_daily_standalone_work(
            rows,
            prefix=prefix,
            allow_legacy_tomorrow_focus=not contract_v3,
        )
    )
    if not isinstance(rows, list):
        return
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        work_items = row.get("work_items") if isinstance(row.get("work_items"), list) else []
        for item_index, item in enumerate(work_items):
            if isinstance(item, dict) and OLD_DAILY_HOW_TEXT in clean_document_list(item.get("how")):
                errors.append(
                    f"{prefix}[{index}].work_items[{item_index}].how 不得使用固定套话，必须写实际处理方法"
                )
            if contract_v3 and isinstance(item, dict) and planning_only_work_item(item):
                errors.append(
                    f"{prefix}[{index}].work_items[{item_index}] 是尚未开展的明日计划，"
                    "不得伪造成今日工作；请移入 payload.tomorrow_plan"
                )


def validate_weekly_report_view_non_project(
    rel: str,
    rows: Any,
    errors: list[str],
    *,
    collection: str,
    allowed_types: set[str],
    expected_type: str,
) -> None:
    prefix = f"{rel} payload.{collection}"
    if not isinstance(rows, list):
        errors.append(f"{prefix} 必须是数组")
        return
    seen: dict[str, int] = {}
    for index, document in enumerate(rows):
        row_prefix = f"{prefix}[{index}]"
        if not isinstance(document, dict):
            errors.append(f"{row_prefix} 必须是对象")
            continue
        work_type = normalize_standalone_work_type(document.get("work_type"))
        if work_type not in allowed_types:
            errors.append(f"{row_prefix}.work_type 必须是 {expected_type}")
        name = standalone_work_name(document)
        if not name:
            errors.append(f"{row_prefix} 必须提供具体 document_name 或 work_name")
        elif name.casefold() in seen:
            errors.append(f"{row_prefix} 与 {prefix}[{seen[name.casefold()]}] 的文档重复")
        else:
            seen[name.casefold()] = index
        for forbidden in ("project", "customer", "customer_name", "downstream_customer", "app_name"):
            if str(document.get(forbidden) or "").strip():
                errors.append(f"{row_prefix}.{forbidden} 文档工作不得伪造项目或客户字段")
        if not str(document.get("week_summary") or "").strip():
            errors.append(f"{row_prefix}.week_summary 必须提供")
        completed = parse_weekly_scalar_count_text(
            document.get("completed_this_week"),
            expected_label="本周完成",
            field_path=f"{row_prefix}.completed_this_week",
            errors=errors,
        )
        remaining = parse_weekly_scalar_count_text(
            document.get("remaining"),
            expected_label="当前剩余",
            field_path=f"{row_prefix}.remaining",
            errors=errors,
        )
        for field in ("completed_items", "remaining_items", "key_points", "risks", "dependencies", "next_week_plan"):
            if not isinstance(document.get(field), list):
                errors.append(f"{row_prefix}.{field} 必须是数组")
        completed_items = [
            item
            for item in clean_document_list(document.get("completed_items"))
            if item != "暂无明确完成项"
        ]
        remaining_items = [
            item
            for item in clean_document_list(document.get("remaining_items"))
            if item != "无明确剩余项"
        ]
        if completed and not completed_items:
            errors.append(f"{row_prefix}.completed_items 本周完成大于 0 时必须提供")
        if remaining and not remaining_items:
            errors.append(f"{row_prefix}.remaining_items 当前剩余大于 0 时必须提供")
        plans = [
            item
            for item in clean_document_list(document.get("next_week_plan"))
            if item not in WEEKLY_EMPTY_PLAN_VALUES
        ]
        if remaining and not plans:
            errors.append(f"{row_prefix}.next_week_plan 当前有剩余时必须提供")


def validate_weekly_report_view_documents(rel: str, documents: Any, errors: list[str]) -> None:
    validate_weekly_report_view_non_project(
        rel,
        documents,
        errors,
        collection="documents",
        allowed_types=DOCUMENT_WORK_TYPES,
        expected_type="Doc",
    )


def validate_weekly_report_view_standalone_work(rel: str, rows: Any, errors: list[str]) -> None:
    validate_weekly_report_view_non_project(
        rel,
        rows,
        errors,
        collection="standalone_work",
        allowed_types=STANDALONE_WORK_TYPES,
        expected_type="Other",
    )


def validate_report_view_payload(
    *,
    rel: str,
    report_type: str,
    manifest: dict[str, Any],
    view: dict[str, Any],
    expected_weekly_project_identities: Any = None,
    daily_contract_v3: bool,
    daily_require_current_gms: bool,
    errors: list[str],
) -> None:
    for field in ("schema", "report_type", "material_name", "material_summary", "member_alias", "member_name", "display_date"):
        if not view.get(field):
            errors.append(f"{rel} payload.{field} 必须提供")
    if view.get("schema") != "akbs-report-view-human-v1":
        errors.append(f"{rel} payload.schema 必须是 akbs-report-view-human-v1")
    if view.get("report_type") != report_type:
        errors.append(f"{rel} payload.report_type 必须是 {report_type}")
    for field in sorted(FORBIDDEN_REPORT_VIEW_FIELDS & set(view)):
        errors.append(f"{rel} payload.{field} 是已废弃的 report_view 字段，新包不得提供")

    projects = view.get("projects")
    documents = view.get("documents", [])
    standalone_work = view.get("standalone_work", [])
    tomorrow_plan = view.get("tomorrow_plan")
    if not isinstance(projects, list):
        errors.append(f"{rel} payload.projects 必须是数组")
        projects = []
    if not isinstance(documents, list):
        errors.append(f"{rel} payload.documents 必须是数组")
        documents = []
    if not isinstance(standalone_work, list):
        errors.append(f"{rel} payload.standalone_work 必须是数组")
        standalone_work = []
    if not projects and not documents and not standalone_work:
        errors.append(f"{rel} payload.projects、payload.documents 和 payload.standalone_work 至少提供一项")
    if projects:
        errors.extend(
            report_project_customer_errors(
                rel,
                projects,
                "projects",
                allow_work_scopes=True,
            )
        )
    if report_type == "weekly":
        errors.extend(
            weekly_project_identity_consistency_errors(
                rel,
                projects,
                expected_weekly_project_identities,
            )
        )
    for index, project in enumerate(projects):
        if not isinstance(project, dict):
            continue
        if report_type == "daily":
            validate_daily_report_view_project(
                rel,
                index,
                project,
                errors,
                contract_v3=daily_contract_v3,
                require_current_gms=daily_require_current_gms,
            )
        else:
            validate_weekly_report_view_project(rel, index, project, errors)
    if report_type == "daily":
        validate_daily_report_view_documents(
            rel,
            documents,
            errors,
            contract_v3=daily_contract_v3,
        )
        validate_daily_report_view_standalone_work(
            rel,
            standalone_work,
            errors,
            contract_v3=daily_contract_v3,
        )
        if daily_contract_v3 or "tomorrow_plan" in view:
            errors.extend(validate_tomorrow_plan(tomorrow_plan, prefix=f"{rel} payload.tomorrow_plan"))
    else:
        if "tomorrow_plan" in view:
            errors.append(f"{rel} payload.tomorrow_plan 仅日报允许提供")
        validate_weekly_report_view_documents(rel, documents, errors)
        validate_weekly_report_view_standalone_work(rel, standalone_work, errors)
    if report_type == "daily" and view.get("report_date") != manifest.get("date"):
        errors.append(f"{rel} payload.report_date 必须等于 manifest.date")
    if report_type == "weekly":
        if view.get("week_range") != manifest.get("week_range"):
            errors.append(f"{rel} payload.week_range 必须等于 manifest.week_range")
        if not view.get("display_date"):
            errors.append(f"{rel} payload.display_date 必须提供")


def validate_report_display_files(
    *,
    package_dir: Path,
    files: dict[str, Any],
    manifest: dict[str, Any],
    report_type: str,
    expected_weekly_project_identities: Any,
    daily_contract_v3: bool,
    daily_require_current_gms: bool,
    require_file: RequireFile,
    read_referenced_json: ReadReferencedJson,
    errors: list[str],
) -> None:
    display_paths = files.get("display", [])
    if not isinstance(display_paths, list) or not display_paths:
        errors.append("report trace files.display 必须包含 report_view.json")
        display_paths = []
    for rel in display_paths:
        path = require_file(rel, "display")
        if not path:
            continue
        report_view = read_referenced_json(package_dir, rel)
        if not isinstance(report_view, dict):
            continue
        if report_view.get("kind") != "report_view":
            errors.append(f"{rel} kind 必须是 report_view")
            continue
        view = report_view.get("payload", {})
        if not isinstance(view, dict):
            errors.append(f"{rel} payload 必须是对象")
            continue
        validate_report_view_payload(
            rel=rel,
            report_type=report_type,
            manifest=manifest,
            view=view,
            expected_weekly_project_identities=expected_weekly_project_identities,
            daily_contract_v3=daily_contract_v3,
            daily_require_current_gms=daily_require_current_gms,
            errors=errors,
        )


def validate_report_render_consistency(
    *,
    package_dir: Path,
    manifest: dict[str, Any],
    report_type: str,
    files: dict[str, Any],
    evidence_by_kind: dict[str, dict[str, Any]],
    require_file: RequireFile,
    read_referenced_json: ReadReferencedJson,
    errors: list[str],
) -> None:
    binding = evidence_by_kind.get("report_render_binding")
    if not isinstance(binding, dict) or not isinstance(binding.get("payload"), dict):
        errors.append("report trace 缺少 report_render_binding evidence")
        return
    payload = binding["payload"]
    if payload.get("schema") != REPORT_RENDER_BINDING_SCHEMA:
        errors.append(f"report_render_binding.schema 必须是 {REPORT_RENDER_BINDING_SCHEMA}")
    if payload.get("report_type") != report_type:
        errors.append(f"report_render_binding.report_type 必须是 {report_type}")
    report_rel = str(manifest.get("report_path") or "")
    display_paths = files.get("display") if isinstance(files.get("display"), list) else []
    view_rel = next((str(rel) for rel in display_paths if Path(str(rel)).name == "report_view.json"), "")
    fact_kind = "daily_fact_sources" if report_type == "daily" else "weekly_fact_sources"
    fact_evidence = evidence_by_kind.get(fact_kind)
    fact_payload = fact_evidence.get("payload") if isinstance(fact_evidence, dict) else {}
    for field, expected in (
        ("report_path", report_rel),
        ("report_view_path", view_rel),
        ("facts_sha256", str(fact_payload.get("facts_sha256") or "")),
    ):
        if str(payload.get(field) or "") != expected:
            errors.append(f"report_render_binding.{field} 与当前报告事实不一致")
    report_path = require_file(report_rel, "report_path")
    view_path = require_file(view_rel, "report_view") if view_rel else None
    for field, path in (("report_sha256", report_path), ("report_view_sha256", view_path)):
        if path and hashlib.sha256(path.read_bytes()).hexdigest() != str(payload.get(field) or ""):
            errors.append(f"report_render_binding.{field} 与实际文件不一致，请重新生成报告")
    view_document = read_referenced_json(package_dir, view_rel) if view_rel else None
    view_payload = view_document.get("payload") if isinstance(view_document, dict) else None
    if report_path and isinstance(view_payload, dict):
        expected_markdown = report_markdown_from_view(view_payload)
        if report_path.read_text(encoding="utf-8") != expected_markdown:
            errors.append(
                "reports Markdown 与 report_view.json 不一致，请修改结构化事实后重新生成，不能只改其中一份"
            )


def validate_report_trace_package(
    *,
    package_dir: Path,
    manifest: dict[str, Any],
    trace_required_evidence_kinds: set[str],
    package_status_values: set[str],
    require_file: RequireFile,
    load_evidence: LoadEvidence,
    read_referenced_json: ReadReferencedJson,
    errors: list[str],
) -> None:
    package_kind = manifest.get("package_kind")
    report_type = "daily" if package_kind == "daily_trace" else "weekly"
    if manifest.get("report_type") != report_type:
        errors.append(f"{package_kind} report_type 必须是 {report_type}")
    report_path = manifest.get("report_path")
    require_file(report_path, "report_path")
    if "case_id" in manifest or "variant_id" in manifest:
        errors.append("report trace 不能携带 case_id 或 variant_id")
    if package_kind == "weekly_trace" and not manifest.get("week_range"):
        errors.append("weekly_trace 必须提供 week_range")
    files = manifest.get("files")
    if not isinstance(files, dict):
        errors.append("report trace files 必须是对象")
        files = {}

    evidence_paths = files.get("evidence", [])
    if not isinstance(evidence_paths, list) or not evidence_paths:
        errors.append("report trace files.evidence 必须是非空数组")
        evidence_paths = []
    evidence_by_kind = load_evidence(evidence_paths)
    for kind in trace_required_evidence_kinds:
        if kind not in evidence_by_kind:
            errors.append(f"report trace 缺少 {kind} evidence")
    if package_kind == "daily_trace":
        validate_daily_project_inference(manifest=manifest, evidence_by_kind=evidence_by_kind, errors=errors)
        validate_daily_fact_sources(manifest=manifest, evidence_by_kind=evidence_by_kind, errors=errors)
    else:
        validate_weekly_fact_sources(manifest=manifest, evidence_by_kind=evidence_by_kind, errors=errors)
    validate_session_privacy_evidence(evidence_by_kind=evidence_by_kind, errors=errors)
    validate_work_findings(evidence_by_kind=evidence_by_kind, package_status_values=package_status_values, errors=errors)
    validate_report_display_files(
        package_dir=package_dir,
        files=files,
        manifest=manifest,
        report_type=report_type,
        expected_weekly_project_identities=(
            evidence_by_kind.get("project_inference", {}).get("payload", {}).get("project_customers")
            if package_kind == "weekly_trace"
            and isinstance(evidence_by_kind.get("project_inference", {}).get("payload"), dict)
            else None
        ),
        daily_contract_v3=(
            package_kind == "daily_trace"
            and isinstance(evidence_by_kind.get("daily_fact_sources", {}).get("payload"), dict)
            and evidence_by_kind["daily_fact_sources"]["payload"].get("schema")
            in {DAILY_FACT_SOURCES_SCHEMA, LEGACY_DAILY_GMS_FACT_SOURCES_SCHEMA}
        ),
        daily_require_current_gms=(
            package_kind == "daily_trace"
            and isinstance(evidence_by_kind.get("daily_fact_sources", {}).get("payload"), dict)
            and evidence_by_kind["daily_fact_sources"]["payload"].get("schema")
            == DAILY_FACT_SOURCES_SCHEMA
        ),
        require_file=require_file,
        read_referenced_json=read_referenced_json,
        errors=errors,
    )
    validate_report_render_consistency(
        package_dir=package_dir,
        manifest=manifest,
        report_type=report_type,
        files=files,
        evidence_by_kind=evidence_by_kind,
        require_file=require_file,
        read_referenced_json=read_referenced_json,
        errors=errors,
    )
