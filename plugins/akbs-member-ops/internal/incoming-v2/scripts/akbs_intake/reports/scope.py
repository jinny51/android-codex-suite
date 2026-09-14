from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .document_work import (
    DOCUMENT_WORK_TYPE,
    NON_PROJECT_WORK_TYPES,
    document_name_from_text,
)
from .gms import gms_release_heading, gms_scope_identity


PROJECT_WORK_TYPES = {"Patch", "App", "GMS", "Doc", "Other"}
ALLOWED_WORK_TYPES = PROJECT_WORK_TYPES | NON_PROJECT_WORK_TYPES

EXPLICIT_PATCH_RE = re.compile(
    r"(?i)(?:类型|工作类型|开发类型)\s*[:：=]\s*patch\b|"
    r"(?:属于|这是|这项(?:工作|开发)?是|做的是)\s*(?:系统源码定制|patch)\b"
)
EXPLICIT_APP_RE = re.compile(
    r"(?i)(?:类型|工作类型|开发类型)\s*[:：=]\s*app\b|"
    r"(?:属于|这是|这项(?:工作|开发)?是|做的是)\s*(?:独立\s*)?(?:app|应用开发)\b|"
    r"(?:app|应用)\s*(?:名称|名)\s*[:：=]"
)
EXPLICIT_DOCUMENT_RE = re.compile(
    r"(?i)(?:类型|工作类型|开发类型)\s*[:：=]\s*(?:doc|document|文档)\b|"
    r"(?:属于|这是|这项(?:工作|任务)?是|做的是)\s*(?:doc|document|文档(?:整理|编写|工作)?)\b|"
    r"项目\s*[:：=]\s*文档\b"
)
EXPLICIT_GMS_RE = re.compile(
    r"(?i)(?:类型|工作类型|开发类型)\s*[:：=]\s*gms\b|"
    r"(?:属于|这是|这项(?:工作|任务)?是|做的是)\s*gms\b"
)
EXPLICIT_OTHER_RE = re.compile(
    r"(?i)(?:类型|工作类型|开发类型)\s*[:：=]\s*other\b|"
    r"(?:属于|这是|这项(?:工作|任务)?是|做的是)\s*other\b"
)
PATCH_TEXT_RE = re.compile(
    r"(?i)(?:frameworks/base|system_server|systemui|launcher3|settingsprovider|"
    r"windowmanager|activitytaskmanager|packagemanager|系统源码(?:定制|修改|开发)|"
    r"(?:生成|产出|提交|应用|移植|制作|修改)\s*(?:了|一个|一份)?\s*(?:patch|补丁)|"
    r"(?:patch|补丁)\s*(?:已|生成|提交|移植|验证|修改|开发|维护|调试|构建|联调))"
)
APP_TEXT_RE = re.compile(
    r"(?i)(?:(?:独立|单独)\s*(?:app|应用)|(?:app|应用|demo)\s*(?:开发|维护|调试|构建|联调)|"
    r"(?:开发|实现|维护|调试|构建|联调)\s*[^，,。；;\n]{0,28}\s*(?:app|应用)|"
    r"\bapplicationid\b|\bassemble(?:debug|release)\b|(?:生成|产出|构建|安装)\s*[^，,。；;\n]{0,12}\.(?:apk|aab)\b)"
)
DOCUMENT_TEXT_RE = re.compile(
    r"(?i)(?:编写|撰写|整理|更新|完善|补充|维护|修订|输出|产出)\s*"
    r"[^，,。；;\n]{0,64}\s*文档\b"
)
GMS_TEXT_RE = re.compile(
    r"(?i)(?:\bGMS\b|\bCTS(?:-Verifier)?\b|\bGTS\b|\bVTS\b|\bGSI\b|"
    r"Camera\s+ITS|认证测试|正式送测|全量测试)"
)
APP_NAME_PATTERNS = (
    re.compile(r"(?i)(?:app|应用)\s*(?:名称|名)?\s*[:：=]\s*([^，,。；;|\n]{1,32})"),
    re.compile(
        r"(?i)(?:开发|实现|维护|调试|构建|适配|联调)\s*"
        r"([A-Za-z0-9_+.-]*[\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9_+.-]{1,23})\s*(?:app|应用)\b"
    ),
    re.compile(
        r"(?i)([A-Za-z0-9_+.-]*[\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9_+.-]{1,23})\s*"
        r"(?:app|应用)\s*(?:开发|维护|调试|构建|适配|联调)"
    ),
)
ANDROID_SYSTEM_PATH_MARKERS = (
    "/frameworks/",
    "/system/",
    "/system_ext/",
    "/packages/systemui/",
    "/packages/apps/",
    "/packages/modules/",
    "/packages/providers/",
    "/device/",
    "/hardware/",
    "/vendor/",
)
GENERIC_APP_NAMES = {
    "android",
    "app",
    "application",
    "demo",
    "debug",
    "release",
    "应用",
    "应用开发",
    "客户",
    "独立",
    "系统",
}


@dataclass(frozen=True)
class WorkScopeInference:
    work_type: str = ""
    app_name: str = ""
    basis: tuple[str, ...] = ()
    conflict: bool = False
    document_name: str = ""


def clean_scope_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def clean_app_name(value: Any) -> str:
    text = clean_scope_text(value).strip(" '\"`[]()（）<>《》")
    text = re.sub(
        r"(?i)\s*(?:app|应用)?\s*(?:开发|维护|调试|构建|适配|联调|已完成|处理中|待验证)?\s*$",
        "",
        text,
    ).strip(" '\"`[]()（）<>《》:-：")
    if not text or len(text) > 32 or text.casefold() in GENERIC_APP_NAMES:
        return ""
    if not re.search(r"[\u4e00-\u9fffA-Za-z0-9]", text):
        return ""
    return text


def app_name_from_text(text: Any) -> str:
    value = str(text or "")
    for pattern in APP_NAME_PATTERNS:
        match = pattern.search(value)
        if match:
            name = clean_app_name(match.group(1))
            if name:
                return name
    return ""


def path_scope_inference(path_hint: Any) -> WorkScopeInference:
    raw_path = str(path_hint or "").replace("\\", "/").strip("/")
    normalized = "/" + raw_path.lower() + "/"
    if normalized == "//":
        return WorkScopeInference()
    if any(marker in normalized for marker in ANDROID_SYSTEM_PATH_MARKERS):
        return WorkScopeInference("Patch", basis=("android_system_source_path",))

    parts = [part for part in raw_path.split("/") if part]
    lower_parts = [part.lower() for part in parts]
    app_name = ""
    if lower_parts and lower_parts[-1] == "app" and len(parts) >= 2:
        app_name = clean_app_name(parts[-2])
    elif parts and re.search(r"(?i)(?:app|application)$", parts[-1]):
        app_name = clean_app_name(parts[-1])
    elif "apps" in lower_parts:
        index = len(parts) - 1 - lower_parts[::-1].index("apps")
        if index + 1 < len(parts):
            app_name = clean_app_name(parts[index + 1])
    if app_name:
        return WorkScopeInference("App", app_name, ("standalone_app_source_path",))
    return WorkScopeInference()


def text_scope_inference(
    texts: list[Any] | tuple[Any, ...],
    *,
    allow_explicit: bool = True,
) -> WorkScopeInference:
    text = " ".join(clean_scope_text(value) for value in texts if clean_scope_text(value))
    if not text:
        return WorkScopeInference()
    explicit_patch = allow_explicit and bool(EXPLICIT_PATCH_RE.search(text))
    explicit_app = allow_explicit and bool(EXPLICIT_APP_RE.search(text))
    explicit_document = allow_explicit and bool(EXPLICIT_DOCUMENT_RE.search(text))
    explicit_gms = allow_explicit and bool(EXPLICIT_GMS_RE.search(text))
    explicit_other = allow_explicit and bool(EXPLICIT_OTHER_RE.search(text))
    if sum(bool(value) for value in (explicit_patch, explicit_app, explicit_document, explicit_gms, explicit_other)) > 1:
        return WorkScopeInference(basis=("conflicting_explicit_work_type",), conflict=True)
    if explicit_patch:
        return WorkScopeInference("Patch", basis=("explicit_work_type",))
    if explicit_app:
        return WorkScopeInference("App", app_name_from_text(text), ("explicit_work_type",))
    if explicit_document:
        return WorkScopeInference(
            DOCUMENT_WORK_TYPE,
            basis=("explicit_work_type",),
            document_name=document_name_from_text(text),
        )
    if explicit_gms:
        return WorkScopeInference("GMS", basis=("explicit_work_type",))
    if explicit_other:
        return WorkScopeInference("Other", basis=("explicit_work_type",))

    patch = bool(PATCH_TEXT_RE.search(text))
    app = bool(APP_TEXT_RE.search(text))
    document = bool(DOCUMENT_TEXT_RE.search(text))
    gms = bool(GMS_TEXT_RE.search(text))
    if sum(bool(value) for value in (patch, app, document, gms)) > 1:
        return WorkScopeInference(basis=("conflicting_development_evidence",), conflict=True)
    if patch:
        return WorkScopeInference("Patch", basis=("framework_development_terms",))
    if app:
        return WorkScopeInference("App", app_name_from_text(text), ("standalone_app_development_terms",))
    if document:
        return WorkScopeInference(
            DOCUMENT_WORK_TYPE,
            basis=("document_work_terms",),
            document_name=document_name_from_text(text),
        )
    if gms:
        return WorkScopeInference("GMS", basis=("gms_test_terms",))
    return WorkScopeInference()


def combine_scope_inferences(*values: WorkScopeInference) -> WorkScopeInference:
    relevant = [value for value in values if value.work_type or value.conflict]
    if not relevant:
        return WorkScopeInference()
    explicit = [value for value in relevant if "explicit_work_type" in value.basis]
    if explicit:
        explicit_types = {value.work_type for value in explicit if value.work_type}
        if len(explicit_types) != 1 or any(value.conflict for value in explicit):
            return WorkScopeInference(basis=("conflicting_explicit_work_type",), conflict=True)
        chosen_type = next(iter(explicit_types))
        chosen = next(value for value in explicit if value.work_type == chosen_type)
        return WorkScopeInference(
            chosen_type,
            chosen.app_name,
            tuple(dict.fromkeys(chosen.basis)),
            document_name=chosen.document_name,
        )
    if any(value.conflict for value in relevant):
        return WorkScopeInference(basis=("conflicting_development_evidence",), conflict=True)
    types = {value.work_type for value in relevant if value.work_type}
    if len(types) != 1:
        return WorkScopeInference(basis=("conflicting_development_evidence",), conflict=True)
    work_type = next(iter(types))
    names = [value.app_name for value in relevant if value.work_type == "App" and value.app_name]
    development_names = [
        value.app_name
        for value in relevant
        if value.work_type == "App"
        and value.app_name
        and ("explicit_work_type" in value.basis or "standalone_app_development_terms" in value.basis)
    ]
    if work_type == "App" and len({name.casefold() for name in development_names}) > 1:
        return WorkScopeInference(basis=("conflicting_app_name_evidence",), conflict=True)
    document_names = [
        value.document_name
        for value in relevant
        if value.work_type == DOCUMENT_WORK_TYPE and value.document_name
    ]
    if work_type == DOCUMENT_WORK_TYPE and len({name.casefold() for name in document_names}) > 1:
        return WorkScopeInference(basis=("conflicting_document_name_evidence",), conflict=True)
    basis = tuple(dict.fromkeys(item for value in relevant for item in value.basis))
    return WorkScopeInference(
        work_type,
        development_names[0] if development_names else names[0] if names else "",
        basis,
        document_name=document_names[0] if document_names else "",
    )


def infer_work_scope(
    *,
    path_hint: Any = "",
    texts: list[Any] | tuple[Any, ...] = (),
    has_patch_artifact: bool = False,
    allow_explicit: bool = True,
) -> WorkScopeInference:
    inferred = combine_scope_inferences(
        path_scope_inference(path_hint),
        text_scope_inference(texts, allow_explicit=allow_explicit),
    )
    if inferred.work_type or inferred.conflict:
        return inferred
    if has_patch_artifact:
        return WorkScopeInference("Patch", basis=("patch_artifact",))
    return inferred


def report_scope_key(row: dict[str, Any]) -> tuple[str, ...]:
    work_type = clean_scope_text(row.get("work_type"))
    app_name = clean_scope_text(row.get("app_name")).casefold() if work_type == "App" else ""
    release_type, target = gms_scope_identity(row) if work_type == "GMS" else ("", "")
    return clean_scope_text(row.get("project")).upper(), work_type, app_name, release_type, target


def report_scope_suffix(row: dict[str, Any]) -> str:
    work_type = clean_scope_text(row.get("work_type"))
    if work_type == "App":
        return f"App：{clean_scope_text(row.get('app_name')) or '需成员确认'}"
    if work_type == "GMS":
        return gms_release_heading(row)
    return work_type or "需成员确认"
