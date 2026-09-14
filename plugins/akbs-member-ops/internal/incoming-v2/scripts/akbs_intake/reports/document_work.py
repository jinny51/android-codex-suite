from __future__ import annotations

import re
from typing import Any


DOCUMENT_WORK_TYPE = "Doc"
LEGACY_DOCUMENT_WORK_TYPE = "Document"
DOCUMENT_WORK_TYPES = {DOCUMENT_WORK_TYPE}
STANDALONE_WORK_TYPES = {"Other"}
NON_PROJECT_WORK_TYPES = DOCUMENT_WORK_TYPES | STANDALONE_WORK_TYPES
DOCUMENT_NAME_LIMIT = 80
DOCUMENT_STATUS_VALUES = {"已完成", "处理中", "待验证", "阻塞"}
DOCUMENT_NAME_PATTERNS = (
    re.compile(r"(?:文档(?:名称|名)|整理文档)\s*[:：=]\s*([^，,。；;|\n]{1,80})", re.IGNORECASE),
    re.compile(
        r"(?:编写|撰写|整理|更新|完善|补充|维护|修订|输出|产出)\s*"
        r"([^，,。；;|\n]{1,64}?\s*文档)(?=$|[，,。；;|\n])",
        re.IGNORECASE,
    ),
)
GENERIC_DOCUMENT_NAMES = {"文档", "整理文档", "项目文档", "开发文档", "说明文档"}


def clean_document_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def normalize_standalone_work_type(value: Any) -> str:
    work_type = clean_document_text(value)
    return DOCUMENT_WORK_TYPE if work_type == LEGACY_DOCUMENT_WORK_TYPE else work_type


def standalone_work_name(value: dict[str, Any]) -> str:
    work_type = normalize_standalone_work_type(value.get("work_type"))
    if work_type == DOCUMENT_WORK_TYPE:
        return clean_document_name(value.get("document_name") or value.get("work_name"))
    return clean_document_text(value.get("work_name") or value.get("document_name"))


def clean_document_name(value: Any) -> str:
    text = clean_document_text(value).strip(" '\"`[]()（）<>《》:-：")
    text = re.sub(
        r"^(?:编写|撰写|整理|更新|完善|补充|维护|修订|输出|产出)\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\s*(?:工作|任务|进展)\s*$", "", text).strip(" '\"`[]()（）<>《》:-：")
    if not text or len(text) > DOCUMENT_NAME_LIMIT or text in GENERIC_DOCUMENT_NAMES:
        return ""
    if "文档" not in text:
        text = f"{text}文档"
    return text


def document_name_from_text(value: Any) -> str:
    text = str(value or "")
    for pattern in DOCUMENT_NAME_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        name = clean_document_name(match.group(1))
        if name:
            return name
    return ""


def clean_list(value: Any) -> list[str]:
    rows = value if isinstance(value, list) else [value] if value else []
    result: list[str] = []
    for row in rows:
        text = clean_document_text(row)
        if text and text not in result:
            result.append(text)
    return result


def normalize_work_item(value: Any) -> dict[str, Any]:
    row = value if isinstance(value, dict) else {}
    return {
        "name": clean_document_text(row.get("name")),
        "did": clean_list(row.get("did")),
        "how": clean_list(row.get("how")),
        "result": clean_document_text(row.get("result")),
        "status": clean_document_text(row.get("status")),
    }


def validate_work_items(work_items: Any, *, prefix: str, old_how_text: str = "") -> tuple[list[str], bool]:
    errors: list[str] = []
    if not isinstance(work_items, list) or not work_items:
        return [f"{prefix}.work_items 必须至少包含一项"], False
    unfinished = False
    for index, item in enumerate(work_items):
        item_prefix = f"{prefix}.work_items[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{item_prefix} 必须是对象")
            continue
        for field in ("name", "result"):
            if not clean_document_text(item.get(field)):
                errors.append(f"{item_prefix}.{field} 必须提供")
        for field in ("did", "how"):
            if not clean_list(item.get(field)):
                errors.append(f"{item_prefix}.{field} 必须是非空数组")
        if old_how_text and old_how_text in clean_list(item.get("how")):
            errors.append(f"{item_prefix}.how 不得使用固定套话")
        status = clean_document_text(item.get("status"))
        if status not in DOCUMENT_STATUS_VALUES:
            errors.append(f"{item_prefix}.status 必须是已完成、处理中、待验证或阻塞")
        unfinished = unfinished or status in {"处理中", "待验证", "阻塞"}
    return errors, unfinished


def validate_daily_documents(
    documents: Any,
    *,
    prefix: str = "documents",
    allow_legacy_tomorrow_focus: bool = False,
) -> list[str]:
    return _validate_daily_non_project_rows(
        documents,
        prefix=prefix,
        allowed_types=DOCUMENT_WORK_TYPES,
        expected="Doc",
        identity_label="文档",
        allow_legacy_tomorrow_focus=allow_legacy_tomorrow_focus,
    )


def validate_daily_standalone_work(
    rows: Any,
    *,
    prefix: str = "standalone_work",
    allow_legacy_tomorrow_focus: bool = False,
) -> list[str]:
    return _validate_daily_non_project_rows(
        rows,
        prefix=prefix,
        allowed_types=STANDALONE_WORK_TYPES,
        expected="Other",
        identity_label="独立工作",
        allow_legacy_tomorrow_focus=allow_legacy_tomorrow_focus,
    )


def _validate_daily_non_project_rows(
    rows: Any,
    *,
    prefix: str,
    allowed_types: set[str],
    expected: str,
    identity_label: str,
    allow_legacy_tomorrow_focus: bool,
) -> list[str]:
    if not isinstance(rows, list):
        return [f"{prefix} 必须是数组"]
    errors: list[str] = []
    seen: dict[str, int] = {}
    for index, raw in enumerate(rows):
        row_prefix = f"{prefix}[{index}]"
        if not isinstance(raw, dict):
            errors.append(f"{row_prefix} 必须是对象")
            continue
        work_type = normalize_standalone_work_type(raw.get("work_type"))
        if work_type not in allowed_types:
            errors.append(f"{row_prefix}.work_type 必须是 {expected}")
        name = standalone_work_name(raw)
        if not name:
            errors.append(f"{row_prefix} 必须提供具体 document_name 或 work_name")
        elif name.casefold() in seen:
            errors.append(f"{row_prefix} 与 {prefix}[{seen[name.casefold()]}] 的{identity_label}重复")
        else:
            seen[name.casefold()] = index
        for forbidden in ("project", "customer", "customer_name", "downstream_customer", "app_name"):
            if clean_document_text(raw.get(forbidden)):
                errors.append(f"{row_prefix}.{forbidden} {identity_label}不得伪造项目或客户字段")
        for field in ("today_topic", "current_result"):
            if not clean_document_text(raw.get(field)):
                errors.append(f"{row_prefix}.{field} 必须提供")
        for field in ("key_points", "dependencies"):
            values = raw.get(field)
            if not isinstance(values, list):
                errors.append(f"{row_prefix}.{field} 必须是数组")
            elif any(not isinstance(item, str) or not item.strip() for item in values):
                errors.append(f"{row_prefix}.{field} 只能包含非空文本")
        item_errors, _unfinished = validate_work_items(raw.get("work_items"), prefix=row_prefix)
        errors.extend(item_errors)
        if "tomorrow_focus" in raw and not allow_legacy_tomorrow_focus:
            errors.append(f"{row_prefix}.tomorrow_focus 已废弃；请改用顶层 tomorrow_plan")
        elif "tomorrow_focus" in raw and not isinstance(raw.get("tomorrow_focus"), list):
            errors.append(f"{row_prefix}.tomorrow_focus 必须是数组")
    return errors


def validate_weekly_documents(documents: Any, *, prefix: str = "documents") -> list[str]:
    return _validate_weekly_non_project_rows(
        documents,
        prefix=prefix,
        allowed_types=DOCUMENT_WORK_TYPES,
        expected="Doc",
        identity_label="文档",
    )


def validate_weekly_standalone_work(rows: Any, *, prefix: str = "standalone_work") -> list[str]:
    return _validate_weekly_non_project_rows(
        rows,
        prefix=prefix,
        allowed_types=STANDALONE_WORK_TYPES,
        expected="Other",
        identity_label="独立工作",
    )


def _validate_weekly_non_project_rows(
    rows: Any,
    *,
    prefix: str,
    allowed_types: set[str],
    expected: str,
    identity_label: str,
) -> list[str]:
    if not isinstance(rows, list):
        return [f"{prefix} 必须是数组"]
    errors: list[str] = []
    seen: dict[str, int] = {}
    for index, raw in enumerate(rows):
        row_prefix = f"{prefix}[{index}]"
        if not isinstance(raw, dict):
            errors.append(f"{row_prefix} 必须是对象")
            continue
        work_type = normalize_standalone_work_type(raw.get("work_type"))
        if work_type not in allowed_types:
            errors.append(f"{row_prefix}.work_type 必须是 {expected}")
        name = standalone_work_name(raw)
        if not name:
            errors.append(f"{row_prefix} 必须提供具体 document_name 或 work_name")
        elif name.casefold() in seen:
            errors.append(f"{row_prefix} 与 {prefix}[{seen[name.casefold()]}] 的{identity_label}重复")
        else:
            seen[name.casefold()] = index
        for forbidden in ("project", "customer", "customer_name", "downstream_customer", "app_name"):
            if clean_document_text(raw.get(forbidden)):
                errors.append(f"{row_prefix}.{forbidden} {identity_label}不得伪造项目或客户字段")
        for field in ("week_summary", "completed_this_week", "remaining"):
            if field in {"completed_this_week", "remaining"}:
                value = raw.get(field)
                if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                    errors.append(f"{row_prefix}.{field} 必须是非负整数")
            elif not clean_document_text(raw.get(field)):
                errors.append(f"{row_prefix}.{field} 必须提供")
        for field in ("completed_items", "remaining_items", "key_points", "risks", "dependencies", "next_week_plan"):
            if not isinstance(raw.get(field), list):
                errors.append(f"{row_prefix}.{field} 必须是数组")
        completed = raw.get("completed_this_week") if isinstance(raw.get("completed_this_week"), int) else 0
        remaining = raw.get("remaining") if isinstance(raw.get("remaining"), int) else 0
        if completed > 0 and not clean_list(raw.get("completed_items")):
            errors.append(f"{row_prefix}.completed_items 本周完成大于 0 时必须提供")
        if remaining > 0 and not clean_list(raw.get("remaining_items")):
            errors.append(f"{row_prefix}.remaining_items 当前剩余大于 0 时必须提供")
        if remaining > 0 and not clean_list(raw.get("next_week_plan")):
            errors.append(f"{row_prefix}.next_week_plan 当前有剩余时必须提供")
    return errors
