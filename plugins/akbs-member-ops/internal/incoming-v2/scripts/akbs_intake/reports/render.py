from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from typing import Any

try:
    from .common import week_bounds, ymd
except ImportError:  # pragma: no cover - direct script import fallback
    from akbs_intake.reports.common import week_bounds, ymd

from akbs_member_ops.knowledge_rules import find_company_project
from akbs_member_ops.json_io import write_json
from akbs_intake.report_sessions import (
    MISSING_REPORT_CUSTOMER,
    MISSING_REPORT_PROJECT,
    REPORT_MISSING_CUSTOMER_VALUES,
    REPORT_MISSING_PROJECT_VALUES,
    report_customer_chain_label,
    report_customer_context_for_project,
    report_customer_for_project,
    report_downstream_customer_for_project,
)
from akbs_intake.reports.scope import report_scope_suffix
from akbs_intake.reports.gms import (
    GMS_CURRENT_FIELDS,
    gms_cycle_status_label,
    gms_release_description,
    gms_release_heading,
    gms_self_test_result_label,
    gms_stage_label,
    gms_submission_result_label,
)
from akbs_intake.reports.document_work import (
    DOCUMENT_WORK_TYPE,
    clean_document_name,
    normalize_standalone_work_type,
    standalone_work_name,
)


REPORT_PROJECT_MARKDOWN_RE = re.compile(
    r"(?<![A-Z0-9*])(TV[DEAI]\d{2}[A-Z0-9]{2}[MRU]\d?|TVI[A-Z0-9]{5}[A-Z0-9]?)(?![A-Z0-9*])",
    re.IGNORECASE,
)
EMPTY_WEEKLY_PLAN_VALUES = {"无", "无。", "暂无", "暂无。", "无下周计划", "暂无下周计划"}


def emphasize_report_project_names(markdown: Any) -> str:
    """Bold every project reference in Markdown without changing structured fields."""
    return REPORT_PROJECT_MARKDOWN_RE.sub(lambda match: f"**{match.group(1)}**", str(markdown or ""))


def compact_text(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def row_customer_context(row: dict[str, Any]) -> dict[str, str]:
    customer = str(row.get("customer") or row.get("customer_name") or MISSING_REPORT_CUSTOMER)
    downstream_customer = str(
        row.get("downstream_customer")
        or row.get("customer_of_customer")
        or row.get("end_customer")
        or ""
    )
    return {
        "customer_name": customer,
        "downstream_customer": downstream_customer,
    }


def project_customer_heading(project: Any, customer: Any, downstream_customer: Any = "") -> str:
    return f"{project} {report_customer_chain_label(customer, downstream_customer)}"


def project_customer_title_label(project: Any, customer: Any, downstream_customer: Any = "") -> str:
    customer_text = str(customer or MISSING_REPORT_CUSTOMER)
    downstream_text = str(downstream_customer or "")
    chain = f"{customer_text} → {downstream_text}" if downstream_text else customer_text
    return f"{project}（{chain}）"


def add_downstream_customer(row: dict[str, Any], downstream_customer: Any) -> dict[str, Any]:
    value = str(downstream_customer or "").strip()
    if value:
        row["downstream_customer"] = value
    return row


def materials_rel(*parts: str) -> str:
    return "/".join(("materials", *parts))


def progress_bucket(progress: str) -> str:
    text = str(progress or "")
    if any(token in text for token in ("阻塞", "blocked", "失败", "报错")):
        return "blocked"
    if re.search(r"(?:^|[^0-9])100\s*%|进度\s*100", text):
        return "completed"
    if any(token in text for token in ("待验证", "验证中")):
        return "in_progress"
    if any(token in text for token in ("处理中", "进行中", "继续排查", "未完成", "待处理")):
        return "in_progress"
    if any(token in text for token in ("已完成", "已解决", "已产出 Patch", "通过", "成功")):
        return "completed"
    return "not_started"


def daily_status_from_progress(progress: str) -> str:
    text = str(progress or "")
    if any(token in text for token in ("待验证", "验证中", "待回归")):
        return "待验证"
    bucket = progress_bucket(text)
    if bucket == "blocked":
        return "阻塞"
    if bucket == "completed":
        return "已完成"
    return "处理中"

def project_stats(entries: list[tuple[str, str]]) -> dict[str, int]:
    stats = {"total": len(entries), "completed": 0, "in_progress": 0, "not_started": 0, "blocked": 0}
    for _, progress in entries:
        bucket = progress_bucket(progress)
        stats[bucket] = stats.get(bucket, 0) + 1
    return stats

def status_label_from_stats(stats: dict[str, int]) -> str:
    if stats.get("blocked", 0):
        return "有阻塞"
    if stats.get("in_progress", 0):
        return "推进中"
    if stats.get("not_started", 0):
        return "未开始"
    if stats.get("completed", 0):
        return "已完成"
    return "无事项"

def next_step_for_entries(entries: list[tuple[str, str]], report_type: str) -> str:
    issues = [desc for desc, progress in entries if progress_bucket(progress) in {"blocked", "in_progress", "not_started"}]
    if issues:
        prefix = "明日继续" if report_type == "daily" else "下周继续"
        return f"{prefix}推进：{compact_text('；'.join(issues[:3]), 120)}"
    return "保持验证和交付记录完整，必要时补齐 patch-capture 证据。"

def report_item_source(desc: str) -> str:
    text = str(desc or "")
    if any(token in text for token in ("禅道", "zentao", "buglist", "Buglist")):
        return "禅道"
    if any(token in text for token in ("测试", "回归", "复现")):
        return "测试"
    if any(token in text for token in ("客户", "客诉", "现场")):
        return "客户"
    if any(token in text for token in ("项目经理", "PM", "需求文档")):
        return "项目经理"
    if any(token in text for token in ("上级", "TL", "负责人")):
        return "上级"
    return "临时工作/内部优化"

def report_list_type(desc: str) -> str:
    text = str(desc or "").lower()
    if any(token in text for token in ("bug", "问题", "缺陷", "报错", "失败", "异常", "buglist")):
        return "Buglist"
    if any(token in text for token in ("需求", "功能", "适配", "开发", "移植")):
        return "需求清单"
    return "临时工作"

def report_category(desc: str, progress: str) -> str:
    text = f"{desc} {progress}".lower()
    if any(token in text for token in ("bug", "问题", "修复", "异常", "失败", "报错")):
        return "Bug处理"
    if any(token in text for token in ("ui", "界面", "显示", "布局")):
        return "UI修改"
    return "功能添加"

def report_item_name(desc: str) -> str:
    return compact_text(str(desc or "未命名事项"), 80)

def weekly_display_date(date: dt.date) -> str:
    start, end = week_bounds(date)
    friday = start + dt.timedelta(days=4)
    return min(friday, end).isoformat()

def weekly_source_rows(items: dict[str, list[tuple[str, str]]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    grouped: dict[tuple[str, str, str], list[tuple[str, str]]] = {}
    for project, entries in sorted(items.items()):
        for desc, progress in entries:
            origin = report_item_source(desc)
            list_type = report_list_type(desc)
            list_name = "无正式清单来源" if list_type == "临时工作" else f"{project} {origin} {list_type}"
            grouped.setdefault((project, origin, list_type), []).append((desc, progress))
    for (project, origin, list_type), entries in sorted(grouped.items()):
        stats = project_stats(entries)
        rows.append(
            {
                "project": project,
                "origin": origin,
                "list_type": list_type,
                "list_name": "无正式清单来源" if list_type == "临时工作" else f"{project} {origin} {list_type}",
                "total": stats["total"],
                "new_this_week": stats["total"],
                "completed_this_week": stats["completed"],
                "remaining": stats["in_progress"] + stats["not_started"] + stats["blocked"],
                "risks": stats["blocked"],
                "note": "按临时工作说明记录" if list_type == "临时工作" else status_label_from_stats(stats),
            }
        )
    return rows or [
        {
            "project": "未识别项目",
            "origin": "临时工作/内部优化",
            "list_type": "临时工作",
            "list_name": "无正式清单来源",
            "total": 0,
            "new_this_week": 0,
            "completed_this_week": 0,
            "remaining": 0,
            "risks": 0,
            "note": "未发现可归档事项",
        }
    ]

def project_source_type(entries: list[tuple[str, str]]) -> str:
    list_types = {report_list_type(desc) for desc, _ in entries}
    has_requirement = "需求清单" in list_types
    has_bug = "Buglist" in list_types
    if has_requirement and has_bug:
        return "混合"
    if has_requirement:
        return "需求"
    if has_bug:
        return "Buglist"
    return "临时支持"

def project_source_note(entries: list[tuple[str, str]]) -> str:
    origins = sorted({report_item_source(desc) for desc, _ in entries})
    list_types = sorted({report_list_type(desc) for desc, _ in entries})
    if not entries:
        return "需成员补充需求单、Buglist 或临时安排来源"
    return f"来源：{'、'.join(origins)}；类型：{'、'.join(list_types)}"

def project_ledger_totals(entries: list[tuple[str, str]]) -> dict[str, int]:
    totals = {"demand": 0, "migration": 0, "bug": 0, "bsp": 0, "other": 0, "total": 0}
    for desc, progress in entries:
        text = f"{desc} {progress}"
        category = report_category(desc, progress)
        is_completed = progress_bucket(progress) == "completed"
        if not is_completed and re.search(r"\bBSP\b", text, re.IGNORECASE):
            totals["bsp"] += 1
        elif category == "Bug处理":
            totals["bug"] += 1
        elif re.search(r"移植|复用|\bport\b", text, re.IGNORECASE):
            totals["migration"] += 1
        elif category in {"功能添加", "UI修改"}:
            totals["demand"] += 1
        else:
            totals["other"] += 1
    totals["total"] = sum(totals[key] for key in ("demand", "migration", "bug", "bsp", "other"))
    return totals

def canonical_report_project_label(value: Any) -> str:
    project = find_company_project(str(value or ""))
    return project or MISSING_REPORT_PROJECT

def normalized_report_items(items: dict[str, list[tuple[str, str]]]) -> dict[str, list[tuple[str, str]]]:
    normalized: dict[str, list[tuple[str, str]]] = {}
    for raw_project, entries in sorted(items.items()):
        project = canonical_report_project_label(raw_project)
        normalized.setdefault(project, []).extend(entries)
    return normalized

def project_ledger_rows(
    items: dict[str, list[tuple[str, str]]],
    project_customers: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    items = normalized_report_items(items)
    source_rows = weekly_source_rows(items)
    if not items:
        return [
            {
                "project": MISSING_REPORT_PROJECT,
                "source_type": "需补基本信息",
                "source_note": "未发现可归档事项",
                "start_date": "需成员确认",
                "duration_label": "需成员确认",
                "customer_name": MISSING_REPORT_CUSTOMER,
                "downstream_customer": "",
                "previous_week_remaining": 0,
                "totals": {"demand": 0, "migration": 0, "bug": 0, "bsp": 0, "other": 0, "total": 0},
                "this_week_completed": 0,
                "cumulative_completed": 0,
                "remaining": 0,
                "status": "无事项",
                "risk": "无明确风险",
            }
        ]
    for project, entries in sorted(items.items()):
        stats = project_stats(entries)
        totals = project_ledger_totals(entries)
        remaining = max(0, totals["total"] - stats["completed"])
        project_sources = [row for row in source_rows if row["project"] == project]
        risk_items = [desc for desc, progress in entries if progress_bucket(progress) == "blocked"]
        rows.append(
            {
                "project": project,
                "source_type": project_source_type(entries),
                "source_note": project_source_note(entries),
                "start_date": "需成员确认",
                "duration_label": "需成员确认",
                "customer_name": report_customer_for_project(project, project_customers),
                "downstream_customer": report_downstream_customer_for_project(project, project_customers),
                "source_lists": project_sources,
                "totals": totals,
                "previous_week_remaining": remaining + stats["completed"],
                "this_week_completed": stats["completed"],
                "cumulative_completed": stats["completed"],
                "remaining": remaining,
                "status": status_label_from_stats(stats),
                "risk": "；".join(compact_text(item, 80) for item in risk_items[:3]) if risk_items else "无明确风险",
            }
        )
    return rows

def report_project_customer_title(
    items: dict[str, list[tuple[str, str]]],
    project_customers: dict[str, Any] | None = None,
    *,
    max_projects: int = 3,
) -> str:
    normalized = normalized_report_items(items)
    labels: list[str] = []
    for project in sorted(normalized):
        context = report_customer_context_for_project(project, project_customers)
        labels.append(
            project_customer_title_label(
                project,
                context["customer_name"],
                context.get("downstream_customer", ""),
            )
        )
    if not labels:
        return f"{MISSING_REPORT_PROJECT}（{MISSING_REPORT_CUSTOMER}）"
    visible = labels[:max_projects]
    suffix = f"等 {len(labels)} 个项目" if len(labels) > max_projects else ""
    return "、".join(visible + ([suffix] if suffix else []))

def daily_material_summary(
    items: dict[str, list[tuple[str, str]]],
    *,
    max_projects: int = 3,
) -> str:
    normalized = normalized_report_items(items)
    if not normalized:
        return "今日未识别到有效工作内容，需补充日报材料。"
    parts: list[str] = []
    for project, entries in sorted(normalized.items())[:max_projects]:
        topics = "；".join(compact_text(re.sub(r"^(?:处理|修复|排查|完成|推进)\s*", "", desc), 32) for desc, _ in entries[:3])
        parts.append(f"{project}：今日处理{topics or '未明确事项'}。")
    if len(normalized) > max_projects:
        parts.append(f"另有 {len(normalized) - max_projects} 个项目。")
    return compact_text("".join(parts), 220)


def daily_project_fact_summary(projects: list[dict[str, Any]], *, max_projects: int = 3) -> str:
    parts: list[str] = []
    for row in projects[:max_projects]:
        project = str(row.get("project") or MISSING_REPORT_PROJECT)
        if row.get("work_type") == "GMS":
            stage = gms_stage_label(row.get("gms_current_stage")) or gms_cycle_status_label(
                row.get("gms_cycle_status")
            )
            if row.get("gms_current_stage") == "self_test":
                progress = f"第 {int(row.get('gms_self_test_round') or 0)} 轮自测"
            elif row.get("gms_current_stage") == "submission":
                progress = f"第 {int(row.get('gms_submission_count') or 0)} 次送测"
            else:
                progress = gms_cycle_status_label(row.get("gms_cycle_status"))
            target = str(row.get("gms_target") or "").strip()
            gms_scope = f"GMS {row.get('gms_release_type') or '待确认'}" + (
                f"（{target}）" if target else ""
            )
            parts.append(
                f"{project}：{gms_scope}，{stage}，{progress}。"
            )
        else:
            parts.append(f"{project}：{row.get('today_topic') or '今日事项需确认'}。")
    if len(projects) > max_projects:
        parts.append(f"另有 {len(projects) - max_projects} 个项目。")
    return compact_text("".join(parts), 220)


def document_material_name(documents: list[dict[str, Any]], *, max_documents: int = 2) -> str:
    names = [standalone_work_name(row) for row in documents]
    names = [name for name in names if name]
    if not names:
        return ""
    visible = names[:max_documents]
    if len(names) > max_documents:
        visible.append(f"等 {len(names)} 项工作")
    return "、".join(visible)


def daily_document_summary(documents: list[dict[str, Any]], *, max_documents: int = 2) -> str:
    parts = [
        f"{standalone_work_name(row)}："
        f"{compact_text(str(row.get('current_result') or row.get('today_topic') or '需补充进展。'), 48)}"
        for row in documents[:max_documents]
        if standalone_work_name(row)
    ]
    if len(documents) > max_documents:
        parts.append(f"另有 {len(documents) - max_documents} 项工作。")
    return compact_text("".join(parts), 220)


def weekly_document_summary(documents: list[dict[str, Any]], *, max_documents: int = 2) -> str:
    parts = [
        f"{standalone_work_name(row)}：本周完成 {int(row.get('completed_this_week', 0) or 0)} 项，"
        f"剩余 {int(row.get('remaining', 0) or 0)} 项。"
        for row in documents[:max_documents]
        if standalone_work_name(row)
    ]
    if len(documents) > max_documents:
        parts.append(f"另有 {len(documents) - max_documents} 项工作。")
    return compact_text("".join(parts), 220)


def weekly_document_view_row(row: dict[str, Any]) -> dict[str, Any]:
    work_type = normalize_standalone_work_type(row.get("work_type")) or DOCUMENT_WORK_TYPE
    result = {
        "work_type": work_type,
        "week_summary": str(row.get("week_summary") or "需补充真实工作记录。"),
        "completed_this_week": f"本周完成 {int(row.get('completed_this_week', 0) or 0)} 项",
        "remaining": f"当前剩余 {int(row.get('remaining', 0) or 0)} 项",
        "completed_items": meaningful_fact_list(row.get("completed_items")) or ["暂无明确完成项"],
        "remaining_items": meaningful_fact_list(row.get("remaining_items")) or ["无明确剩余项"],
        "key_points": meaningful_fact_list(row.get("key_points")) or ["无"],
        "risks": meaningful_fact_list(row.get("risks")) or ["无超过 3 天无进展事项。"],
        "dependencies": meaningful_fact_list(row.get("dependencies")) or ["无外部依赖事项。"],
        "next_week_plan": weekly_plan_items(row.get("next_week_plan")),
    }
    if work_type == DOCUMENT_WORK_TYPE:
        result["document_name"] = clean_document_name(row.get("document_name") or row.get("work_name"))
    else:
        result["work_name"] = standalone_work_name(row)
    if row.get("platform"):
        result["platform"] = str(row.get("platform"))
    return result

def weekly_material_summary(
    items: dict[str, list[tuple[str, str]]],
    project_customers: dict[str, Any] | None = None,
    weekly_projects: list[dict[str, Any]] | None = None,
    *,
    max_projects: int = 3,
) -> str:
    if weekly_projects:
        parts = []
        for row in weekly_projects[:max_projects]:
            risk_text = "有风险" if meaningful_fact_list(row.get("risks"), {"无超过 3 天无进展事项。"}) else "无明确风险"
            if row.get("work_type") == "App":
                scope = f" / {row.get('app_name')}"
            elif row.get("work_type") == "GMS":
                target = str(row.get("gms_target") or "").strip()
                scope = f" / GMS：{row.get('gms_release_type') or '待确认'}" + (
                    f"（{target}）" if target else ""
                )
            else:
                scope = ""
            if row.get("work_type") in {"GMS", "Doc", "Other"}:
                gms_progress = (
                    f"{gms_cycle_status_label(row.get('gms_cycle_status'))} / "
                    f"{gms_stage_label(row.get('gms_current_stage')) or '周期已结束'}"
                    if row.get("work_type") == "GMS"
                    else ""
                )
                progress = compact_text(str(row.get("week_summary") or gms_progress or "需补充进展"), 72)
                parts.append(f"{row['project']}{scope}：{progress}，{risk_text}。")
            else:
                completed = weekly_fact_count_text(row, "completed_this_week_counts", include_bsp=False)
                remaining = weekly_fact_count_text(row, "remaining_counts", include_bsp=True)
                parts.append(f"{row['project']}{scope}：本周完成 {completed}，剩余 {remaining}，{risk_text}。")
        if len(weekly_projects) > max_projects:
            parts.append(f"另有 {len(weekly_projects) - max_projects} 个项目。")
        return compact_text("".join(parts), 220)
    ledgers = project_ledger_rows(items, project_customers)
    visible = [row for row in ledgers if row["project"] not in {MISSING_REPORT_PROJECT, "未识别项目"}]
    if not visible:
        return "本周未识别到有效项目进展，需补充周报材料。"
    parts: list[str] = []
    for ledger in visible[:max_projects]:
        risk_text = "有风险" if ledger.get("risk") not in ("", "无明确风险") else "无明确风险"
        parts.append(
            f"{ledger['project']}：本周完成 {ledger['this_week_completed']} 项，"
            f"剩余 {ledger['remaining']} 项，{risk_text}。"
        )
    if len(visible) > max_projects:
        parts.append(f"另有 {len(visible) - max_projects} 个项目。")
    return compact_text("".join(parts), 220)

def daily_topic_for_entries(entries: list[tuple[str, str]]) -> str:
    topics = [compact_text(re.sub(r"^(?:处理|修复|排查|完成|推进)\s*", "", desc), 48) for desc, _ in entries[:3]]
    return "、".join(item for item in topics if item) or "今日工作事项需成员确认"

def daily_result_for_entries(entries: list[tuple[str, str]]) -> str:
    stats = project_stats(entries)
    if stats.get("blocked", 0):
        return "存在阻塞或超过预期未收敛事项，需要继续推进。"
    if stats.get("in_progress", 0) or stats.get("not_started", 0):
        return "部分事项仍在推进中，明日继续处理。"
    if stats.get("completed", 0):
        return "相关事项今日已完成或进入验证收尾。"
    return "当前结果需成员确认。"

def weekly_source_label(entries: list[tuple[str, str]]) -> str:
    # 需求来源必须由成员在收到需求时明确提供，不能从日报措辞反推。
    return "需成员确认"

def weekly_requirement_counts(entries: list[tuple[str, str]], *, buckets: set[str] | None = None) -> dict[str, int]:
    counts = {"demand": 0, "migration": 0, "bug": 0, "bsp": 0}
    completed_scope = buckets == {"completed"}
    for desc, progress in entries:
        if buckets is not None and progress_bucket(progress) not in buckets:
            continue
        text = f"{desc} {progress}"
        if not completed_scope and re.search(r"\bBSP\b", text, re.IGNORECASE):
            counts["bsp"] += 1
        elif report_category(desc, progress) == "Bug处理" or report_list_type(desc) == "Buglist":
            counts["bug"] += 1
        elif re.search(r"移植|复用|\bport\b", text, re.IGNORECASE):
            counts["migration"] += 1
        else:
            counts["demand"] += 1
    return counts

def format_requirement_counts(counts: dict[str, int], *, include_bsp: bool, label: str = "") -> str:
    keys = ("demand", "migration", "bug", "bsp") if include_bsp else ("demand", "migration", "bug")
    total = sum(int(counts.get(key, 0) or 0) for key in keys)
    labels = {"demand": "需求", "migration": "移植", "bug": "Bug", "bsp": "BSP"}
    parts = [f"{labels[key]} {counts.get(key, 0)}" for key in keys if int(counts.get(key, 0) or 0) > 0]
    prefix = f"{label} " if label else ""
    return f"{prefix}{total} 项" + (f"：{'、'.join(parts)}" if parts else "")


def normalize_fact_counts(value: Any) -> dict[str, int]:
    result = {"demand": 0, "migration": 0, "bug": 0, "bsp": 0}
    if not isinstance(value, dict):
        return result
    for key in result:
        try:
            result[key] = max(0, int(value.get(key, 0) or 0))
        except (TypeError, ValueError):
            result[key] = 0
    return result


def meaningful_fact_list(value: Any, ignored: set[str] | None = None) -> list[str]:
    rows = value if isinstance(value, list) else []
    ignored = ignored or set()
    return [str(item).strip() for item in rows if str(item).strip() and str(item).strip() not in ignored]


def weekly_plan_items(value: Any) -> list[str]:
    return meaningful_fact_list(value, EMPTY_WEEKLY_PLAN_VALUES)


def weekly_fact_count_text(row: dict[str, Any], key: str, *, include_bsp: bool, label: str = "") -> str:
    if row.get("work_type") == "App":
        scalar_fields = {
            "requirement_structure_counts": "work_total",
            "completed_this_week_counts": "completed_this_week_total",
            "remaining_counts": "remaining_total",
        }
        prefix = f"{label} " if label else ""
        return f"{prefix}{int(row.get(scalar_fields[key], 0) or 0)} 项"
    counts = normalize_fact_counts(row.get(key))
    return format_requirement_counts(counts, include_bsp=include_bsp, label=label)


def weekly_ledger_event_text(value: Any, *, work_type: str, label: str) -> str:
    if work_type == "App":
        total = int(value or 0)
        return f"{label} {total} 项"
    counts = normalize_fact_counts(value)
    total = sum(counts[key] for key in ("demand", "migration", "bug"))
    labels = {"demand": "需求", "migration": "移植", "bug": "Bug"}
    parts = [f"{labels[key]} {counts[key]}" for key in labels if counts[key] > 0]
    return f"{label} {total} 项" + (f"（{'、'.join(parts)}）" if parts else "")


def weekly_ledger_change_text(row: dict[str, Any]) -> str:
    ledger = row.get("ledger") if isinstance(row.get("ledger"), dict) else {}
    changes = ledger.get("changes") if isinstance(ledger.get("changes"), dict) else {}
    work_type = str(row.get("work_type") or "Patch")
    labels = {
        "added": "新增",
        "reopened": "重新打开",
        "closed_without_change": "无需修改关闭",
        "removed": "移出",
        "transferred_to_bsp": "转 BSP",
        "bsp_closed": "BSP 关闭",
    }
    parts: list[str] = []
    if ledger.get("opening") is True:
        parts.append("新项目建账")
    for key, label in labels.items():
        value = changes.get(key, 0 if work_type == "App" else {})
        total = int(value or 0) if work_type == "App" else sum(
            normalize_fact_counts(value)[category]
            for category in ("demand", "migration", "bug")
        )
        if total > 0:
            parts.append(weekly_ledger_event_text(value, work_type=work_type, label=label))
    return "、".join(parts) or "无"


def weekly_bsp_pending_text(row: dict[str, Any]) -> str:
    ledger = row.get("ledger") if isinstance(row.get("ledger"), dict) else {}
    return weekly_ledger_event_text(
        ledger.get("bsp_pending", {}),
        work_type=str(row.get("work_type") or "Patch"),
        label="BSP 跟踪",
    )


def weekly_bsp_pending_total(row: dict[str, Any]) -> int:
    ledger = row.get("ledger") if isinstance(row.get("ledger"), dict) else {}
    value = ledger.get("bsp_pending", {})
    if row.get("work_type") == "App":
        return int(value or 0)
    counts = normalize_fact_counts(value)
    return sum(counts[key] for key in ("demand", "migration", "bug"))


def weekly_scope_heading(row: dict[str, Any]) -> str:
    context = row_customer_context(row)
    heading = project_customer_heading(
        str(row["project"]),
        context["customer_name"],
        context["downstream_customer"],
    )
    if row.get("work_type") == "App":
        return f"{heading}｜App：{row.get('app_name') or '需成员确认'}"
    if row.get("work_type") == "GMS":
        return f"{heading}｜{gms_release_heading(row)}"
    work_type = str(row.get("work_type") or "").strip()
    return f"{heading}｜{work_type}" if work_type else heading


def daily_scope_heading(row: dict[str, Any]) -> str:
    context = row_customer_context(row)
    heading = project_customer_heading(
        str(row.get("project") or MISSING_REPORT_PROJECT),
        context["customer_name"],
        context["downstream_customer"],
    )
    return f"{heading}｜{report_scope_suffix(row)}"


def document_scope_heading(row: dict[str, Any]) -> str:
    work_type = normalize_standalone_work_type(row.get("work_type")) or DOCUMENT_WORK_TYPE
    return f"{work_type}：{standalone_work_name(row) or '需成员确认'}"


def gms_report_lines(row: dict[str, Any]) -> list[str]:
    self_round = row.get("gms_self_test_round")
    submission_count = row.get("gms_submission_count")
    cycle_status = str(row.get("gms_cycle_status") or "")
    lines = [
        f"- 送测类别：{gms_release_description(row)}",
        f"- 目标版本：{row.get('gms_target') or '需成员确认'}",
        f"- 周期状态：{gms_cycle_status_label(cycle_status)}",
    ]
    if cycle_status == "active":
        lines.append(f"- 当前阶段：{gms_stage_label(row.get('gms_current_stage')) or '需成员确认'}")
    lines += [
        f"- 自测轮次：{'第 ' + str(self_round) + ' 轮' if isinstance(self_round, int) and self_round > 0 else '尚未开始'}",
        f"- 最新自测结果：{gms_self_test_result_label(row.get('gms_self_test_result'))}",
        f"- 正式送测：{'共 ' + str(submission_count) + ' 次' if isinstance(submission_count, int) and submission_count > 0 else '尚未送测'}",
        f"- 最新送测结果：{gms_submission_result_label(row.get('gms_submission_result'))}",
    ]
    return lines


def write_daily_scope_list(
    lines: list[str],
    *,
    title: str,
    rows: list[tuple[dict[str, Any], str]],
    field: str,
) -> None:
    lines += [title, ""]
    rendered = False
    for row, heading in rows:
        values = meaningful_fact_list(row.get(field))
        if not values:
            continue
        rendered = True
        lines += [f"### {heading}", "", *(f"- {value}" for value in values), ""]
    if not rendered:
        lines += ["无。", ""]

def weekly_risks(entries: list[tuple[str, str]]) -> list[str]:
    risks = [compact_text(desc, 120) for desc, progress in entries if progress_bucket(progress) == "blocked"]
    return risks or ["无超过 3 天无进展事项。"]

def weekly_dependencies(entries: list[tuple[str, str]]) -> list[str]:
    dependencies = [
        compact_text(desc, 120)
        for desc, progress in entries
        if re.search(r"依赖|等待|客户确认|外部|第三方|\bBSP\b|测试反馈", f"{desc} {progress}", re.IGNORECASE)
    ]
    return dependencies or ["无外部依赖事项。"]

def write_daily_report(
    lines: list[str],
    items: dict[str, list[tuple[str, str]]],
    patches: list[PatchInfo],
    project_customers: dict[str, Any] | None = None,
    daily_work_items: dict[str, list[dict[str, Any]]] | None = None,
    daily_projects: list[dict[str, Any]] | None = None,
    daily_documents: list[dict[str, Any]] | None = None,
    daily_standalone_work: list[dict[str, Any]] | None = None,
    daily_tomorrow_plan: dict[str, list[dict[str, Any]]] | None = None,
    tomorrow_plan_heading: str = "明日计划",
) -> None:
    items = normalized_report_items(items)
    project_rows = daily_projects or []
    document_rows = daily_documents or []
    standalone_rows = daily_standalone_work or []
    tomorrow_plan = daily_tomorrow_plan or {
        "projects": [],
        "documents": [],
        "standalone_work": [],
    }
    non_project_rows = [*document_rows, *standalone_rows]
    if not items and not project_rows and not non_project_rows:
        items = {MISSING_REPORT_PROJECT: [("未形成有效工作记录", "需补充真实工作记录")]}
    if not project_rows and not non_project_rows:
        for project, entries in sorted(items.items()):
            context = report_customer_context_for_project(project, project_customers)
            work_rows = (daily_work_items or {}).get(project) or [
                {
                    "name": report_item_name(desc),
                    "did": [desc],
                    "how": ["未从授权会话中提取到具体处理过程，需成员补充。"],
                    "result": progress,
                    "status": daily_status_from_progress(progress),
                }
                for desc, progress in entries
            ]
            row = {
                "project": project,
                "customer": context["customer_name"],
                "customer_name": context["customer_name"],
                "work_type": "需成员确认",
                "today_topic": daily_topic_for_entries(entries),
                "current_result": daily_result_for_entries(entries),
                "work_items": work_rows,
                "key_points": [],
                "dependencies": [],
            }
            if context.get("downstream_customer"):
                row["downstream_customer"] = context["downstream_customer"]
            project_rows.append(row)
    lines += ["## 一、今日概况", ""]
    for row in project_rows:
        lines += [
            f"### {daily_scope_heading(row)}",
            "",
            f"- 类型：{row.get('work_type') or '需成员确认'}",
            *([f"- App 名称：{row.get('app_name') or '需成员确认'}"] if row.get("work_type") == "App" else []),
            *(gms_report_lines(row) if row.get("work_type") == "GMS" else []),
            f"- 今日主题：{row.get('today_topic') or '今日工作事项需成员确认'}",
            f"- 当前结果：{row.get('current_result') or '当前结果需成员确认。'}",
            "",
        ]
    for row in non_project_rows:
        lines += [
            f"### {document_scope_heading(row)}",
            "",
            f"- 类型：{normalize_standalone_work_type(row.get('work_type')) or DOCUMENT_WORK_TYPE}",
            f"- 今日主题：{row.get('today_topic') or '今日事项需成员确认'}",
            f"- 当前结果：{row.get('current_result') or '当前结果需成员确认。'}",
            "",
        ]
    lines += ["## 二、今日工作", ""]
    all_rows = [
        *((row, daily_scope_heading(row)) for row in project_rows),
        *((row, document_scope_heading(row)) for row in non_project_rows),
    ]
    for row, heading in all_rows:
        lines += [f"### {heading}", ""]
        work_rows = row.get("work_items") if isinstance(row.get("work_items"), list) else []
        for index, work_item in enumerate(work_rows, start=1):
            lines += [
                f"#### {index}. {report_item_name(str(work_item.get('name') or '未命名事项'))}",
                "",
                "做了什么：",
                *(f"- {value}" for value in (work_item.get("did") or ["需成员补充"])),
                "",
                "怎么做的：",
                *(f"- {value}" for value in (work_item.get("how") or ["需成员补充"])),
                "",
                "结果：",
                f"- {work_item.get('result') or '需成员补充'}",
                "",
                "状态：",
                f"- {work_item.get('status') or '处理中'}",
                "",
            ]
    write_daily_scope_list(
        lines,
        title="## 三、重点说明",
        rows=all_rows,
        field="key_points",
    )
    write_daily_scope_list(
        lines,
        title="## 四、依赖 / 需协调",
        rows=all_rows,
        field="dependencies",
    )
    lines += [f"## 五、{tomorrow_plan_heading}", ""]
    plan_rows = [
        *((row, daily_scope_heading(row)) for row in tomorrow_plan.get("projects", []) if isinstance(row, dict)),
        *((row, document_scope_heading(row)) for row in tomorrow_plan.get("documents", []) if isinstance(row, dict)),
        *((row, document_scope_heading(row)) for row in tomorrow_plan.get("standalone_work", []) if isinstance(row, dict)),
    ]
    if not plan_rows:
        lines += ["无。", ""]
    for row, heading in plan_rows:
        plan_items = row.get("plan_items") if isinstance(row.get("plan_items"), list) else []
        if not plan_items:
            continue
        lines += [
            f"### {heading}",
            "",
            *(f"- {item}" for item in plan_items),
            "",
        ]

def write_weekly_report(
    lines: list[str],
    items: dict[str, list[tuple[str, str]]],
    patches: list[PatchInfo],
    project_customers: dict[str, Any] | None = None,
    weekly_projects: list[dict[str, Any]] | None = None,
    weekly_documents: list[dict[str, Any]] | None = None,
    weekly_standalone_work: list[dict[str, Any]] | None = None,
) -> None:
    items = normalized_report_items(items)
    document_rows = weekly_documents or []
    standalone_rows = weekly_standalone_work or []
    non_project_rows = [*document_rows, *standalone_rows]
    if not items and not weekly_projects and not non_project_rows:
        items = {MISSING_REPORT_PROJECT: [("未形成有效工作记录", "需补充真实工作记录")]}
    ledgers = [] if non_project_rows and not items else project_ledger_rows(items, project_customers)
    lines += [
        "## 一、本周概况",
        "",
    ]
    if weekly_projects:
        for row in weekly_projects:
            if row.get("work_type") in {"GMS", "Doc", "Other"}:
                lines += [
                    f"### {weekly_scope_heading(row)}",
                    "",
                    str(row.get("week_summary") or "需补充真实工作记录。"),
                    "",
                    f"- 类型：{row.get('work_type')}",
                    *(gms_report_lines(row) if row.get("work_type") == "GMS" else []),
                    f"- 项目角色：{row.get('project_role') or '需成员确认'}",
                    f"- 需求时间：{row.get('requirement_date') or '需成员确认'}",
                    f"- 需求来源：{row.get('requirement_source') or '需成员确认'}",
                    "",
                ]
                continue
            total_field = "work_total" if row.get("work_type") == "App" else "requirement_structure"
            total_value = row.get(total_field)
            if not total_value:
                total_value = weekly_fact_count_text(
                    row,
                    "requirement_structure_counts",
                    include_bsp=row.get("work_type") != "App",
                    label="共",
                )
            completed_value = row.get("completed_this_week") or weekly_fact_count_text(
                row, "completed_this_week_counts", include_bsp=False, label="本周完成"
            )
            remaining_value = row.get("remaining") or weekly_fact_count_text(
                row, "remaining_counts", include_bsp=True, label="当前剩余"
            )
            lines += [
                f"### {weekly_scope_heading(row)}",
                "",
                str(row.get("week_summary") or "需补充真实工作记录。"),
                "",
                f"- 类型：{row.get('work_type') or '需成员确认'}",
                *([f"- App 名称：{row.get('app_name') or '需成员确认'}"] if row.get("work_type") == "App" else []),
                f"- 项目角色：{row.get('project_role') or '需成员确认'}",
                f"- 需求时间：{row.get('requirement_date') or '需成员确认'}",
                f"- 需求来源：{row.get('requirement_source') or '需成员确认'}",
                *(
                    [f"- 本周变化：{weekly_ledger_change_text(row)}"]
                    if isinstance(row.get("ledger"), dict) and row.get("project_role") == "主责"
                    else []
                ),
                *(
                    [f"- {total_value}"]
                    if row.get("project_role") == "主责"
                    and (
                        row.get("work_total_present") or row.get("work_total")
                        if row.get("work_type") == "App"
                        else row.get("requirement_structure_present") or row.get("requirement_structure")
                    )
                    else []
                ),
                f"- {completed_value}",
                f"- {remaining_value}",
                *(
                    [f"- {weekly_bsp_pending_text(row)}"]
                    if isinstance(row.get("ledger"), dict) and weekly_bsp_pending_total(row) > 0
                    else []
                ),
                "",
            ]
    else:
        for ledger in ledgers:
            project = str(ledger["project"])
            entries = items.get(project, [])
            all_counts = weekly_requirement_counts(entries)
            completed_counts = weekly_requirement_counts(entries, buckets={"completed"})
            remaining_counts = weekly_requirement_counts(entries, buckets={"in_progress", "not_started", "blocked"})
            lines += [
                f"### {project_customer_heading(project, ledger['customer_name'], ledger.get('downstream_customer', ''))}",
                "",
                f"本周围绕 {project_customer_heading(project, ledger['customer_name'], ledger.get('downstream_customer', ''))} 项目推进：{next_step_for_entries(entries, 'weekly') if entries else '需补充真实工作记录。'}",
                "",
                "- 项目角色：需成员确认",
                "- 类型：需成员确认",
                f"- 需求时间：{ledger['start_date']}",
                f"- 需求来源：{weekly_source_label(entries)}",
                f"- {format_requirement_counts(all_counts, include_bsp=True, label='共')}",
                f"- {format_requirement_counts(completed_counts, include_bsp=False, label='本周完成')}",
                f"- {format_requirement_counts(remaining_counts, include_bsp=True, label='当前剩余')}",
                "",
            ]
    for row in non_project_rows:
        lines += [
            f"### {document_scope_heading(row)}",
            "",
            str(row.get("week_summary") or "需补充真实工作记录。"),
            "",
            f"- 类型：{normalize_standalone_work_type(row.get('work_type')) or DOCUMENT_WORK_TYPE}",
            f"- 本周完成 {int(row.get('completed_this_week', 0) or 0)} 项",
            f"- 当前剩余 {int(row.get('remaining', 0) or 0)} 项",
            "",
        ]
    detail_rows = weekly_projects or [
        {
            "project": ledger["project"],
            "customer": ledger["customer_name"],
            "downstream_customer": ledger.get("downstream_customer", ""),
            "completed_items": [desc for desc, progress in items.get(str(ledger["project"]), []) if progress_bucket(progress) == "completed"],
            "remaining_items": [desc for desc, progress in items.get(str(ledger["project"]), []) if progress_bucket(progress) != "completed"],
            "key_points": ["无"],
            "risks": weekly_risks(items.get(str(ledger["project"]), [])),
            "dependencies": weekly_dependencies(items.get(str(ledger["project"]), [])),
        }
        for ledger in ledgers
    ]
    if detail_rows:
        lines += ["", "## 二、项目详情", ""]
    for row in detail_rows:
        completed = meaningful_fact_list(row.get("completed_items"))
        unfinished = meaningful_fact_list(row.get("remaining_items"))
        key_points = meaningful_fact_list(row.get("key_points")) or ["无"]
        risks = meaningful_fact_list(row.get("risks")) or ["无超过 3 天无进展事项。"]
        dependencies = meaningful_fact_list(row.get("dependencies")) or ["无外部依赖事项。"]
        lines += [
            f"### {weekly_scope_heading(row)}",
            "",
            "#### 1. 本周完成",
            "",
            *(f"- {compact_text(item, 120)}" for item in (completed or ["暂无明确完成项"])),
            "",
            "#### 2. 当前剩余",
            "",
            *(f"- {compact_text(item, 120)}" for item in (unfinished or ["无明确剩余项"])),
            "",
            "#### 3. 重点说明",
            "",
            *(f"- {compact_text(item, 120)}" for item in key_points),
            "",
            "#### 4. 风险 / 依赖",
            "",
            "风险：",
            "",
            *(f"- {item}" for item in risks),
            "",
            "依赖：",
            "",
            *(f"- {item}" for item in dependencies),
            "",
        ]
    if non_project_rows:
        document_section = "三" if detail_rows else "二"
        standalone_types = [
            work_type
            for work_type in ("Doc", "Other")
            if any((normalize_standalone_work_type(row.get("work_type")) or DOCUMENT_WORK_TYPE) == work_type for row in non_project_rows)
        ]
        lines += [f"## {document_section}、{' / '.join(standalone_types)}", ""]
        for row in non_project_rows:
            completed = meaningful_fact_list(row.get("completed_items"))
            unfinished = meaningful_fact_list(row.get("remaining_items"))
            key_points = meaningful_fact_list(row.get("key_points")) or ["无"]
            risks = meaningful_fact_list(row.get("risks")) or ["无超过 3 天无进展事项。"]
            dependencies = meaningful_fact_list(row.get("dependencies")) or ["无外部依赖事项。"]
            lines += [
                f"### {document_scope_heading(row)}",
                "",
                "#### 1. 本周完成",
                "",
                *(f"- {compact_text(item, 120)}" for item in (completed or ["暂无明确完成项"])),
                "",
                "#### 2. 当前剩余",
                "",
                *(f"- {compact_text(item, 120)}" for item in (unfinished or ["无明确剩余项"])),
                "",
                "#### 3. 重点说明",
                "",
                *(f"- {compact_text(item, 120)}" for item in key_points),
                "",
                "#### 4. 风险 / 依赖",
                "",
                "风险：",
                "",
                *(f"- {item}" for item in risks),
                "",
                "依赖：",
                "",
                *(f"- {item}" for item in dependencies),
                "",
            ]
    plan_section = "四" if detail_rows and non_project_rows else "三"
    lines += [f"## {plan_section}、下周计划", ""]
    plan_rows = weekly_projects or [
        {
            "project": ledger["project"],
            "customer": ledger["customer_name"],
            "downstream_customer": ledger.get("downstream_customer", ""),
            "next_week_plan": (
                [next_step_for_entries(items.get(str(ledger["project"]), []), "weekly")]
                if any(
                    progress_bucket(progress) != "completed"
                    for _, progress in items.get(str(ledger["project"]), [])
                )
                else []
            ),
        }
        for ledger in ledgers
    ]
    for row in plan_rows:
        plans = weekly_plan_items(row.get("next_week_plan"))
        if not plans:
            continue
        lines += [
            f"### {weekly_scope_heading(row)}",
            "",
            *(f"- {plan}" for plan in plans),
            "",
        ]
    for row in non_project_rows:
        plans = weekly_plan_items(row.get("next_week_plan"))
        if not plans:
            continue
        lines += [
            f"### {document_scope_heading(row)}",
            "",
            *(f"- {plan}" for plan in plans),
            "",
        ]

def report_view_payload(
    report_type: str,
    date: dt.date,
    week_key: str,
    config: dict[str, str],
    items: dict[str, list[tuple[str, str]]],
    patches: list[PatchInfo],
    summary: str,
    project_customers: dict[str, Any] | None = None,
    weekly_projects: list[dict[str, Any]] | None = None,
    daily_work_items: dict[str, list[dict[str, Any]]] | None = None,
    daily_projects: list[dict[str, Any]] | None = None,
    weekly_documents: list[dict[str, Any]] | None = None,
    daily_documents: list[dict[str, Any]] | None = None,
    weekly_standalone_work: list[dict[str, Any]] | None = None,
    daily_standalone_work: list[dict[str, Any]] | None = None,
    daily_tomorrow_plan: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    items = normalized_report_items(items)
    document_rows = (daily_documents or []) if report_type == "daily" else (weekly_documents or [])
    standalone_rows = (
        (daily_standalone_work or [])
        if report_type == "daily"
        else (weekly_standalone_work or [])
    )
    non_project_rows = [*document_rows, *standalone_rows]
    tomorrow_plan = daily_tomorrow_plan or {
        "projects": [],
        "documents": [],
        "standalone_work": [],
    }
    has_project_facts = bool(daily_projects) if report_type == "daily" else bool(weekly_projects)
    if non_project_rows and not has_project_facts:
        items = {}
    if not items and not non_project_rows:
        items = {MISSING_REPORT_PROJECT: [("未形成有效工作记录", "需补充真实工作记录")]}
    project_material_name = report_project_customer_title(items, project_customers) if items else ""
    non_project_name = document_material_name(non_project_rows)
    material_name = "、".join(value for value in (project_material_name, non_project_name) if value)
    project_summary = (
        daily_project_fact_summary(daily_projects or [])
        if report_type == "daily" and daily_projects
        else daily_material_summary(items)
        if report_type == "daily" and items
        else weekly_material_summary(items, project_customers, weekly_projects)
        if report_type == "weekly" and (items or weekly_projects)
        else ""
    )
    document_summary = (
        daily_document_summary(non_project_rows)
        if report_type == "daily"
        else weekly_document_summary(non_project_rows)
    )
    material_summary = compact_text("".join(value for value in (project_summary, document_summary) if value), 220)
    payload: dict[str, Any] = {
        "schema": "akbs-report-view-human-v1",
        "report_type": report_type,
        "material_name": material_name,
        "material_summary": material_summary,
        "report_date": date.isoformat(),
        "week_range": week_key if report_type == "weekly" else "",
        "display_date": weekly_display_date(date) if report_type == "weekly" else date.isoformat(),
        "member_alias": config["member_alias"],
        "member_name": config["member_name"],
    }
    if report_type == "daily":
        if daily_projects:
            projects = [{key: value for key, value in row.items() if key != "tomorrow_focus"} for row in daily_projects]
            rendered_documents = [
                {key: value for key, value in row.items() if key != "tomorrow_focus"}
                for row in document_rows
            ]
            rendered_standalone = [
                {key: value for key, value in row.items() if key != "tomorrow_focus"}
                for row in standalone_rows
            ]
            payload.update(
                {
                    "projects": projects,
                    "documents": rendered_documents,
                    "standalone_work": rendered_standalone,
                    "tomorrow_plan": tomorrow_plan,
                }
            )
            return {"kind": "report_view", "payload": payload}
        projects: list[dict[str, Any]] = []
        for project, entries in sorted(items.items()):
            context = report_customer_context_for_project(project, project_customers)
            customer = context["customer_name"]
            work_items = (daily_work_items or {}).get(project) or [
                {
                    "name": report_item_name(desc),
                    "did": [desc],
                    "how": ["未从授权会话中提取到具体处理过程，需成员补充。"],
                    "result": progress,
                    "status": daily_status_from_progress(progress),
                }
                for desc, progress in entries
            ]
            projects.append(
                add_downstream_customer({
                    "project": project,
                    "customer": customer,
                    "customer_name": customer,
                    "today_topic": daily_topic_for_entries(entries),
                    "current_result": daily_result_for_entries(entries),
                    "work_items": work_items,
                    "key_points": [],
                    "dependencies": [],
                }, context.get("downstream_customer", ""))
            )
        payload.update(
            {
                "projects": projects,
                "documents": [
                    {key: value for key, value in row.items() if key != "tomorrow_focus"}
                    for row in document_rows
                ],
                "standalone_work": [
                    {key: value for key, value in row.items() if key != "tomorrow_focus"}
                    for row in standalone_rows
                ],
                "tomorrow_plan": tomorrow_plan,
            }
        )
        return {"kind": "report_view", "payload": payload}

    if weekly_projects:
        projects = []
        for row in weekly_projects:
            context = row_customer_context(row)
            customer = context["customer_name"]
            project_row = add_downstream_customer({
                    "project": str(row["project"]),
                    "customer": customer,
                    "customer_name": customer,
                    "work_type": str(row.get("work_type") or "需成员确认"),
                    "project_role": str(row.get("project_role") or "需成员确认"),
                    "week_summary": str(row.get("week_summary") or "需补充真实工作记录。"),
                    "requirement_date": str(row.get("requirement_date") or "需成员确认"),
                    "requirement_source": str(row.get("requirement_source") or "需成员确认"),
                    "completed_this_week": weekly_fact_count_text(
                        row, "completed_this_week_counts", include_bsp=False, label="本周完成"
                    ),
                    "remaining": weekly_fact_count_text(
                        row, "remaining_counts", include_bsp=True, label="当前剩余"
                    ),
                    "completed_items": meaningful_fact_list(row.get("completed_items")) or ["暂无明确完成项"],
                    "remaining_items": meaningful_fact_list(row.get("remaining_items")) or ["无明确剩余项"],
                    "key_points": meaningful_fact_list(row.get("key_points")) or ["无"],
                    "risks": meaningful_fact_list(row.get("risks")) or ["无超过 3 天无进展事项。"],
                    "dependencies": meaningful_fact_list(row.get("dependencies")) or ["无外部依赖事项。"],
                    "next_week_plan": weekly_plan_items(row.get("next_week_plan")),
                }, context["downstream_customer"])
            if row.get("work_type") in {"GMS", "Doc", "Other"}:
                project_row.pop("completed_this_week", None)
                project_row.pop("remaining", None)
                if row.get("work_type") == "GMS":
                    for field in GMS_CURRENT_FIELDS:
                        project_row[field] = row.get(field)
            elif row.get("work_type") == "App":
                project_row["app_name"] = str(row.get("app_name") or "需成员确认")
                if row.get("project_role") == "主责" and row.get("work_total_present"):
                    project_row["work_total"] = weekly_fact_count_text(
                        row, "requirement_structure_counts", include_bsp=False, label="共"
                    )
            elif row.get("project_role") == "主责" and row.get("requirement_structure_present"):
                project_row["requirement_structure"] = weekly_fact_count_text(
                    row, "requirement_structure_counts", include_bsp=True, label="共"
                )
            if row.get("ledger_present"):
                project_row["ledger"] = dict(row.get("ledger") or {})
                project_row["project_change"] = weekly_ledger_change_text(row)
                project_row["bsp_pending"] = weekly_bsp_pending_text(row)
            projects.append(project_row)
        payload.update(
            {
                "projects": projects,
                "documents": [weekly_document_view_row(row) for row in document_rows],
                "standalone_work": [weekly_document_view_row(row) for row in standalone_rows],
            }
        )
        return {"kind": "report_view", "payload": payload}

    projects: list[dict[str, Any]] = []
    ledgers = [] if non_project_rows and not items else project_ledger_rows(items, project_customers)
    for ledger in ledgers:
        project = str(ledger["project"])
        customer = str(ledger["customer_name"])
        downstream_customer = str(ledger.get("downstream_customer") or "")
        entries = items.get(project, [])
        all_counts = weekly_requirement_counts(entries)
        completed_counts = weekly_requirement_counts(entries, buckets={"completed"})
        remaining_counts = weekly_requirement_counts(entries, buckets={"in_progress", "not_started", "blocked"})
        completed = [compact_text(desc, 120) for desc, progress in entries if progress_bucket(progress) == "completed"]
        remaining = [compact_text(desc, 120) for desc, progress in entries if progress_bucket(progress) != "completed"]
        projects.append(
            add_downstream_customer({
                "project": project,
                "customer": customer,
                "customer_name": customer,
                "work_type": "需成员确认",
                "project_role": "需成员确认",
                "week_summary": f"本周围绕 {project_customer_heading(project, customer, downstream_customer)} 项目推进：{next_step_for_entries(entries, 'weekly') if entries else '需补充真实工作记录。'}",
                "requirement_date": str(ledger.get("start_date") or "需成员确认"),
                "requirement_source": weekly_source_label(entries),
                "requirement_structure": format_requirement_counts(all_counts, include_bsp=True, label="共"),
                "completed_this_week": format_requirement_counts(completed_counts, include_bsp=False, label="本周完成"),
                "remaining": format_requirement_counts(remaining_counts, include_bsp=True, label="当前剩余"),
                "completed_items": completed or ["暂无明确完成项"],
                "remaining_items": remaining or ["无明确剩余项"],
                "key_points": ["无"],
                "risks": weekly_risks(entries),
                "dependencies": weekly_dependencies(entries),
                "next_week_plan": (
                    [next_step_for_entries(entries, "weekly")]
                    if any(progress_bucket(progress) != "completed" for _, progress in entries)
                    else []
                ),
            }, downstream_customer)
        )
    payload.update(
        {
            "projects": projects,
            "documents": [weekly_document_view_row(row) for row in document_rows],
            "standalone_work": [weekly_document_view_row(row) for row in standalone_rows],
        }
    )
    return {"kind": "report_view", "payload": payload}

def write_report(
    package_dir: Path,
    report_type: str,
    date: dt.date,
    week_key: str,
    config: dict[str, str],
    items: dict[str, list[tuple[str, str]]],
    patches: list[PatchInfo],
    project_customers: dict[str, Any] | None = None,
    weekly_projects: list[dict[str, Any]] | None = None,
    daily_work_items: dict[str, list[dict[str, Any]]] | None = None,
    daily_projects: list[dict[str, Any]] | None = None,
    weekly_documents: list[dict[str, Any]] | None = None,
    daily_documents: list[dict[str, Any]] | None = None,
    weekly_standalone_work: list[dict[str, Any]] | None = None,
    daily_standalone_work: list[dict[str, Any]] | None = None,
    daily_tomorrow_plan: dict[str, list[dict[str, Any]]] | None = None,
) -> Path:
    report_path = package_dir / f"{report_type}.md"
    view = report_view_payload(
        report_type,
        date,
        week_key,
        config,
        items,
        patches,
        "",
        project_customers,
        weekly_projects,
        daily_work_items,
        daily_projects,
        weekly_documents,
        daily_documents,
        weekly_standalone_work,
        daily_standalone_work,
        daily_tomorrow_plan,
    )
    report_path.write_text(report_markdown_from_view(view["payload"]), encoding="utf-8")
    return report_path


def report_markdown_from_view(view: dict[str, Any]) -> str:
    report_type = str(view.get("report_type") or "")
    title_key = (
        str(view.get("report_date") or "").replace("-", "")
        if report_type == "daily"
        else str(view.get("week_range") or "")
    )
    title = "日报" if report_type == "daily" else "周报"
    lines = [f"# {title_key}_{view.get('member_name') or '需成员确认'}_{title}", ""]
    projects = view.get("projects") if isinstance(view.get("projects"), list) else []
    documents = view.get("documents") if isinstance(view.get("documents"), list) else []
    standalone_work = view.get("standalone_work") if isinstance(view.get("standalone_work"), list) else []
    legacy_daily_plan = report_type == "daily" and "tomorrow_plan" not in view
    tomorrow_plan = (
        view.get("tomorrow_plan")
        if isinstance(view.get("tomorrow_plan"), dict)
        else {"projects": [], "documents": [], "standalone_work": []}
    )
    if legacy_daily_plan:
        for collection, rows in (
            ("projects", projects),
            ("documents", documents),
            ("standalone_work", standalone_work),
        ):
            for row in rows:
                if not isinstance(row, dict):
                    continue
                focus = row.get("tomorrow_focus")
                plan_items = [
                    str(item).strip()
                    for item in focus
                    if str(item).strip()
                ] if isinstance(focus, list) else []
                if not plan_items:
                    continue
                if collection == "projects":
                    plan_row = {
                        field: row[field]
                        for field in (
                            "project",
                            "customer",
                            "customer_name",
                            "downstream_customer",
                            "work_type",
                            "app_name",
                        )
                        if row.get(field)
                    }
                elif collection == "documents":
                    plan_row = {
                        field: row[field]
                        for field in ("work_type", "document_name", "platform")
                        if row.get(field)
                    }
                else:
                    plan_row = {
                        field: row[field]
                        for field in ("work_type", "work_name", "platform")
                        if row.get(field)
                    }
                plan_row["plan_items"] = plan_items
                tomorrow_plan[collection].append(plan_row)
    if report_type == "daily":
        write_daily_report(
            lines,
            {},
            [],
            daily_projects=projects,
            daily_documents=documents,
            daily_standalone_work=standalone_work,
            daily_tomorrow_plan=tomorrow_plan,
            tomorrow_plan_heading="明日重点" if legacy_daily_plan else "明日计划",
        )
    else:
        def normalized_weekly_non_project(rows: list[Any]) -> list[dict[str, Any]]:
            normalized: list[dict[str, Any]] = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                copied = dict(row)
                completed_match = re.fullmatch(r"本周完成\s+(\d+)\s*项", str(row.get("completed_this_week") or ""))
                remaining_match = re.fullmatch(r"当前剩余\s+(\d+)\s*项", str(row.get("remaining") or ""))
                copied["completed_this_week"] = int(completed_match.group(1)) if completed_match else 0
                copied["remaining"] = int(remaining_match.group(1)) if remaining_match else 0
                normalized.append(copied)
            return normalized

        normalized_documents = normalized_weekly_non_project(documents)
        normalized_standalone_work = normalized_weekly_non_project(standalone_work)
        write_weekly_report(
            lines,
            {},
            [],
            weekly_projects=projects,
            weekly_documents=normalized_documents,
            weekly_standalone_work=normalized_standalone_work,
        )
    return emphasize_report_project_names("\n".join(lines).rstrip() + "\n")

def write_report_view(
    package_dir: Path,
    report_type: str,
    date: dt.date,
    week_key: str,
    config: dict[str, str],
    items: dict[str, list[tuple[str, str]]],
    patches: list[PatchInfo],
    summary: str,
    project_customers: dict[str, Any] | None = None,
    weekly_projects: list[dict[str, Any]] | None = None,
    daily_work_items: dict[str, list[dict[str, Any]]] | None = None,
    daily_projects: list[dict[str, Any]] | None = None,
    weekly_documents: list[dict[str, Any]] | None = None,
    daily_documents: list[dict[str, Any]] | None = None,
    weekly_standalone_work: list[dict[str, Any]] | None = None,
    daily_standalone_work: list[dict[str, Any]] | None = None,
    daily_tomorrow_plan: dict[str, list[dict[str, Any]]] | None = None,
) -> str:
    rel = materials_rel("display", "report_view.json")
    write_json(
        package_dir / rel,
        report_view_payload(
            report_type,
            date,
            week_key,
            config,
            items,
            patches,
            summary,
            project_customers,
            weekly_projects,
            daily_work_items,
            daily_projects,
            weekly_documents,
            daily_documents,
            weekly_standalone_work,
            daily_standalone_work,
            daily_tomorrow_plan,
        ),
    )
    return rel
