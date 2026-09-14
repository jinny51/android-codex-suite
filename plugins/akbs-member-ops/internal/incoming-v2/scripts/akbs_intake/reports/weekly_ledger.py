from __future__ import annotations

import re
from typing import Any


WEEKLY_LEDGER_SCHEMA = "akbs-weekly-project-ledger-v2"
BUSINESS_COUNT_KEYS = ("demand", "migration", "bug")
LEDGER_CHANGE_KEYS = (
    "added",
    "reopened",
    "closed_without_change",
    "removed",
    "transferred_to_bsp",
    "bsp_closed",
)


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalize_business_counts(value: Any) -> dict[str, int]:
    result = {key: 0 for key in BUSINESS_COUNT_KEYS}
    if not isinstance(value, dict):
        return result
    aliases = {
        "demand": "demand",
        "requirement": "demand",
        "需求": "demand",
        "migration": "migration",
        "port": "migration",
        "移植": "migration",
        "feature_port": "migration",
        "bug": "bug",
        "Bug": "bug",
        "buglist": "bug",
        "Buglist": "bug",
    }
    for raw_key, raw_value in value.items():
        key = aliases.get(str(raw_key))
        if key is None:
            continue
        try:
            result[key] = max(0, int(raw_value))
        except (TypeError, ValueError):
            continue
    return result


def normalize_scalar(value: Any) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return max(0, value)
    return 0


def counts_total(counts: dict[str, int]) -> int:
    return sum(int(counts.get(key, 0) or 0) for key in BUSINESS_COUNT_KEYS)


def add_counts(*values: dict[str, int]) -> dict[str, int]:
    return {
        key: sum(int(value.get(key, 0) or 0) for value in values)
        for key in BUSINESS_COUNT_KEYS
    }


def subtract_counts(left: dict[str, int], *values: dict[str, int]) -> dict[str, int]:
    return {
        key: int(left.get(key, 0) or 0)
        - sum(int(value.get(key, 0) or 0) for value in values)
        for key in BUSINESS_COUNT_KEYS
    }


def zero_business_counts() -> dict[str, int]:
    return {key: 0 for key in BUSINESS_COUNT_KEYS}


def normalize_ledger_count(value: Any, work_type: str) -> dict[str, int] | int:
    return normalize_scalar(value) if work_type == "App" else normalize_business_counts(value)


def empty_weekly_ledger(work_type: str) -> dict[str, Any]:
    empty: dict[str, int] | int = 0 if work_type == "App" else zero_business_counts()
    return {
        "schema": WEEKLY_LEDGER_SCHEMA,
        "opening": False,
        "baseline_package_key": "",
        "baseline_week_range": "",
        "project_completed": dict(empty) if isinstance(empty, dict) else empty,
        "changes": {key: dict(empty) if isinstance(empty, dict) else empty for key in LEDGER_CHANGE_KEYS},
        "bsp_pending": dict(empty) if isinstance(empty, dict) else 0,
        "baseline": {
            "total": dict(empty) if isinstance(empty, dict) else empty,
            "android_remaining": dict(empty) if isinstance(empty, dict) else empty,
            "bsp_pending": dict(empty) if isinstance(empty, dict) else 0,
        },
    }


def normalize_weekly_ledger(value: Any, work_type: str) -> dict[str, Any]:
    result = empty_weekly_ledger(work_type)
    if not isinstance(value, dict):
        return result
    result["opening"] = value.get("opening") is True
    result["baseline_package_key"] = clean_text(value.get("baseline_package_key"))
    result["baseline_week_range"] = clean_text(value.get("baseline_week_range"))
    result["project_completed"] = normalize_ledger_count(value.get("project_completed"), work_type)
    changes = value.get("changes") if isinstance(value.get("changes"), dict) else {}
    result["changes"] = {
        key: normalize_ledger_count(changes.get(key), work_type)
        for key in LEDGER_CHANGE_KEYS
    }
    result["bsp_pending"] = normalize_ledger_count(value.get("bsp_pending"), work_type)
    return result


def weekly_scope_identity(row: dict[str, Any]) -> tuple[str, ...]:
    work_type = clean_text(row.get("work_type"))
    return (
        clean_text(row.get("project")).upper(),
        clean_text(row.get("customer")),
        clean_text(row.get("downstream_customer")),
        work_type,
        clean_text(row.get("app_name")).casefold() if work_type == "App" else "",
        clean_text(row.get("gms_release_type")).upper() if work_type == "GMS" else "",
        clean_text(row.get("gms_target")).casefold() if work_type == "GMS" else "",
    )


def matching_previous_scope(
    row: dict[str, Any],
    previous_projects: dict[str, list[dict[str, Any]]],
) -> tuple[dict[str, Any] | None, bool]:
    matches = [
        previous
        for previous in previous_projects.get(clean_text(row.get("project")).upper(), [])
        if weekly_scope_identity(previous) == weekly_scope_identity(row)
    ]
    return (matches[0] if len(matches) == 1 else None, len(matches) > 1)


def raw_ledger_count_errors(value: Any, *, work_type: str, field_path: str) -> list[str]:
    if work_type == "App":
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return [f"{field_path} App 计数必须是非负整数"]
        return []
    if not isinstance(value, dict):
        return [f"{field_path} Patch 计数必须是分类对象"]
    errors: list[str] = []
    unknown = sorted(str(key) for key in value if str(key) not in BUSINESS_COUNT_KEYS)
    if unknown:
        errors.append(f"{field_path} 只允许 demand/migration/bug；BSP 是责任状态，不是事项类型")
    if any(
        key in value
        and (not isinstance(value.get(key), int) or isinstance(value.get(key), bool) or int(value.get(key)) < 0)
        for key in BUSINESS_COUNT_KEYS
    ):
        errors.append(f"{field_path} 计数必须是非负整数")
    return errors


def nonzero_ledger_count(value: dict[str, int] | int) -> bool:
    return value > 0 if isinstance(value, int) else counts_total(value) > 0


def has_legacy_bsp(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    try:
        return int(value.get("bsp", 0) or 0) > 0
    except (TypeError, ValueError):
        return False


def validate_v5_project_ledger(
    raw: dict[str, Any],
    row: dict[str, Any],
    previous: dict[str, Any] | None,
    *,
    previous_ambiguous: bool,
    errors: list[str],
) -> None:
    project = clean_text(row.get("project")) or "unknown"
    work_type = clean_text(row.get("work_type"))
    role = clean_text(row.get("project_role"))
    raw_ledger = raw.get("ledger")
    if not isinstance(raw_ledger, dict):
        errors.append(f"{project}.ledger v5 周报必须提供上周基线和本周变化")
        return
    if previous_ambiguous:
        errors.append(f"{project}.ledger 上周存在多个相同统计对象，无法确定唯一基线")
        return

    allowed_ledger_keys = {
        "schema",
        "opening",
        "baseline_package_key",
        "baseline_week_range",
        "project_completed",
        "changes",
        "bsp_pending",
    }
    unknown_ledger_keys = sorted(str(key) for key in raw_ledger if str(key) not in allowed_ledger_keys)
    if unknown_ledger_keys:
        errors.append(f"{project}.ledger 含未知字段：{','.join(unknown_ledger_keys)}")
    if raw_ledger.get("schema") != WEEKLY_LEDGER_SCHEMA:
        errors.append(f"{project}.ledger.schema 必须是 {WEEKLY_LEDGER_SCHEMA}")
    if not isinstance(raw_ledger.get("opening"), bool):
        errors.append(f"{project}.ledger.opening 必须是 true 或 false")
    errors.extend(
        raw_ledger_count_errors(
            raw_ledger.get("project_completed", 0 if work_type == "App" else {}),
            work_type=work_type,
            field_path=f"{project}.ledger.project_completed",
        )
    )
    changes = raw_ledger.get("changes")
    if not isinstance(changes, dict):
        errors.append(f"{project}.ledger.changes 必须是对象")
        changes = {}
    unknown_change_keys = sorted(str(key) for key in changes if str(key) not in LEDGER_CHANGE_KEYS)
    if unknown_change_keys:
        errors.append(f"{project}.ledger.changes 含未知字段：{','.join(unknown_change_keys)}")
    for key in LEDGER_CHANGE_KEYS:
        errors.extend(
            raw_ledger_count_errors(
                changes.get(key, 0 if work_type == "App" else {}),
                work_type=work_type,
                field_path=f"{project}.ledger.changes.{key}",
            )
        )
    errors.extend(
        raw_ledger_count_errors(
            raw_ledger.get("bsp_pending", 0 if work_type == "App" else {}),
            work_type=work_type,
            field_path=f"{project}.ledger.bsp_pending",
        )
    )

    ledger = row["ledger"]
    normalized_changes = ledger["changes"]
    if role == "协作":
        if ledger["opening"] or any(nonzero_ledger_count(value) for value in normalized_changes.values()):
            errors.append(f"{project}.ledger 只有主责可以新建项目或修改项目总量和流转")
        if nonzero_ledger_count(ledger["bsp_pending"]):
            errors.append(f"{project}.ledger.bsp_pending 只由主责维护")
        if nonzero_ledger_count(ledger["project_completed"]):
            errors.append(f"{project}.ledger.project_completed 只由主责确认项目全员完成量")
        if previous:
            if ledger["baseline_package_key"] != clean_text(previous.get("_package_key")):
                errors.append(f"{project}.ledger.baseline_package_key 必须绑定上一份有效周报")
            if ledger["baseline_week_range"] != clean_text(previous.get("_week_range")):
                errors.append(f"{project}.ledger.baseline_week_range 必须等于上一周周期")
        return
    if role != "主责":
        return

    opening = bool(ledger["opening"])
    if previous is None:
        if not opening:
            errors.append(f"{project}.ledger.opening 新项目主责必须设为 true")
        if ledger["baseline_package_key"] or ledger["baseline_week_range"]:
            errors.append(f"{project}.ledger 新项目不得伪造上周基线")
        for key in ("added", "reopened", "removed"):
            if nonzero_ledger_count(normalized_changes[key]):
                errors.append(f"{project}.ledger.changes.{key} 新项目已由初始总量覆盖，必须为 0")
    else:
        if opening:
            errors.append(f"{project}.ledger.opening 已有上周台账的项目不得重新开项或重置总量")
        if ledger["baseline_package_key"] != clean_text(previous.get("_package_key")):
            errors.append(f"{project}.ledger.baseline_package_key 必须绑定上一份有效周报")
        if ledger["baseline_week_range"] != clean_text(previous.get("_week_range")):
            errors.append(f"{project}.ledger.baseline_week_range 必须等于上一周周期")

    if work_type == "App":
        if any(
            int(normalized_changes[key] or 0) > 0
            for key in ("transferred_to_bsp", "bsp_closed")
        ) or int(ledger["bsp_pending"] or 0) > 0:
            errors.append(f"{project}.ledger App 暂不支持 BSP 跟踪")
        current_total = int(row.get("work_total", 0) or 0)
        member_completed = int(row.get("completed_this_week_total", 0) or 0)
        project_completed = int(ledger["project_completed"] or 0)
        if project_completed < member_completed:
            errors.append(f"{project}.ledger.project_completed 不能小于主责个人本周完成")
        current_remaining = int(row.get("remaining_total", 0) or 0)
        prior_total = int(previous.get("work_total", 0) or 0) if previous else current_total
        prior_remaining = int(previous.get("remaining_total", 0) or 0) if previous else current_total
        ledger["baseline"] = {
            "total": prior_total,
            "android_remaining": prior_remaining,
            "bsp_pending": 0,
        }
        expected_total = prior_total + int(normalized_changes["added"]) - int(normalized_changes["removed"])
        expected_remaining = (
            prior_remaining
            + int(normalized_changes["added"])
            + int(normalized_changes["reopened"])
            - project_completed
            - int(normalized_changes["closed_without_change"])
            - int(normalized_changes["removed"])
        )
        if current_total != expected_total:
            errors.append(f"{project}.work_total 应为 {expected_total}，不能绕过上周总量直接重填")
        if expected_remaining < 0 or current_remaining != expected_remaining:
            errors.append(f"{project}.remaining 应为 {max(0, expected_remaining)}，请补齐本周变化后重新计算")
        return

    current_total = normalize_business_counts(row.get("requirement_structure_counts"))
    member_completed = normalize_business_counts(row.get("completed_this_week_counts"))
    project_completed = ledger["project_completed"]
    if any(project_completed[key] < member_completed[key] for key in BUSINESS_COUNT_KEYS):
        errors.append(f"{project}.ledger.project_completed 不能小于主责个人本周完成")
    current_remaining = normalize_business_counts(row.get("remaining_counts"))
    if has_legacy_bsp(row.get("requirement_structure_counts")):
        errors.append(f"{project}.requirement_structure v5 不再把 BSP 当作事项类型")
    if has_legacy_bsp(row.get("remaining_counts")):
        errors.append(f"{project}.remaining v5 不再把 BSP 混入 Android 当前剩余")
    if previous:
        if has_legacy_bsp(previous.get("requirement_structure_counts")) or has_legacy_bsp(
            previous.get("remaining_counts")
        ):
            errors.append(f"{project}.ledger 上周 BSP 分类缺少原始需求/移植/Bug 类型，需主责先确认迁移")
        prior_total = normalize_business_counts(previous.get("requirement_structure_counts"))
        prior_remaining = normalize_business_counts(previous.get("remaining_counts"))
        prior_bsp = previous.get("bsp_pending_counts")
        prior_bsp = prior_bsp if isinstance(prior_bsp, dict) else zero_business_counts()
    else:
        prior_total = current_total
        prior_remaining = current_total
        prior_bsp = zero_business_counts()

    ledger["baseline"] = {
        "total": dict(prior_total),
        "android_remaining": dict(prior_remaining),
        "bsp_pending": dict(prior_bsp),
    }

    expected_total = subtract_counts(
        add_counts(prior_total, normalized_changes["added"]),
        normalized_changes["removed"],
    )
    available = add_counts(prior_remaining, normalized_changes["added"], normalized_changes["reopened"])
    expected_remaining = subtract_counts(
        available,
        project_completed,
        normalized_changes["closed_without_change"],
        normalized_changes["removed"],
        normalized_changes["transferred_to_bsp"],
    )
    expected_bsp = subtract_counts(
        add_counts(prior_bsp, normalized_changes["transferred_to_bsp"]),
        normalized_changes["bsp_closed"],
    )
    for label, counts in (
        ("项目总量", expected_total),
        ("当前剩余", expected_remaining),
        ("BSP 跟踪", expected_bsp),
    ):
        for category, count in counts.items():
            if count < 0:
                errors.append(f"{project}.ledger {label}的 {category} 计算为负数，请检查本周变化")
    if current_total != expected_total:
        errors.append(f"{project}.requirement_structure 与上周总量和主责本周变化不一致")
    if current_remaining != expected_remaining:
        errors.append(f"{project}.remaining 与上周剩余和本周流转不一致")
    if ledger["bsp_pending"] != expected_bsp:
        errors.append(f"{project}.ledger.bsp_pending 与转 BSP/关闭 BSP 的流转不一致")
