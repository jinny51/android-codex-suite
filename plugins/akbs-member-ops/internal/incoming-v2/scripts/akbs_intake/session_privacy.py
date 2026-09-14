from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import os
import re
import shlex
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from akbs_member_ops.artifact_paths import require_safe_artifact_path
from akbs_member_ops.http_client import sanitize_public_text


SESSION_CONSENT_VERSION = "akbs-report-session-consent-v1"
SESSION_RETENTION_POLICY = "memory_only_no_raw_copy"
ALLOWED_SESSION_FIELDS = frozenset(
    {"work_summary", "project_hint", "work_scope_hint", "command_summary", "patch_discovery"}
)
CONSENT_KEY_PREFIX = "_report_session_consent_"
SOURCE_ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f-]{27,}$", re.I)
REPORT_REQUEST_TAIL_RE = re.compile(r"(?:，|,)?\s*(?:帮我|请).{0,48}(?:生成|提交|上传).{0,24}(?:日报|周报|报告).*$")
WORK_CLAUSE_RE = re.compile(
    r"(?i)(?:framework|systemui|launcher|settings|patch|补丁|修复|排查|分析|验证|构建|适配|完成|进度|需求|项目|客户)"
)
COMMAND_SUBCOMMANDS = frozenset(
    {
        "apply",
        "build",
        "diff",
        "log",
        "status",
        "test",
        "checkout",
        "branch",
        "show",
        "compile",
        "install",
        "push",
        "shell",
        "pytest",
        "unittest",
    }
)
FORBIDDEN_EVIDENCE_KEYS = frozenset(
    {
        "messages",
        "message",
        "thread_name",
        "cwd",
        "command",
        "commands",
        "raw",
        "raw_text",
        "session_text",
        "clipboard",
        "environment",
    }
)
SESSION_EVIDENCE_KEYS = frozenset(
    {"source", "synthetic_data", "source_session_ids", "time_range", "consent", "retention"}
)
TIME_RANGE_KEYS = frozenset({"start_date", "end_date", "timezone"})
CONSENT_PAYLOAD_KEYS = frozenset({"version", "granted", "scope", "fields"})
RETENTION_KEYS = frozenset({"policy", "raw_session_copied", "temporary_artifacts_retained"})
MINIMAL_SOURCE_ID_RE = re.compile(r"(?:[0-9a-f]{8}-[0-9a-f-]{27,}|session_[0-9a-f]{16})$")
TIMEZONE_RE = re.compile(r"[A-Za-z_+-]+(?:/[A-Za-z0-9_+.-]+)*$")


@dataclass(frozen=True)
class ReportSessionConsent:
    version: str
    start_date: dt.date
    end_date: dt.date
    fields: frozenset[str]
    granted: bool

    def payload(self, *, synthetic: bool) -> dict[str, Any]:
        return {
            "version": self.version,
            "granted": self.granted,
            "scope": "synthetic_fixture" if synthetic else "single_report_generation",
            "fields": sorted(self.fields),
        }


def configure_report_session_consent(
    config: dict[str, str],
    dates: set[dt.date],
    *,
    granted: bool,
    fields: list[str],
) -> None:
    for key in list(config):
        if key.startswith(CONSENT_KEY_PREFIX):
            config.pop(key, None)
    if not granted:
        return
    start = min(dates)
    end = max(dates)
    config[f"{CONSENT_KEY_PREFIX}granted"] = "true"
    config[f"{CONSENT_KEY_PREFIX}version"] = SESSION_CONSENT_VERSION
    config[f"{CONSENT_KEY_PREFIX}start_date"] = start.isoformat()
    config[f"{CONSENT_KEY_PREFIX}end_date"] = end.isoformat()
    config[f"{CONSENT_KEY_PREFIX}fields"] = ",".join(dict.fromkeys(fields))


def _parse_date(value: str, label: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise SystemExit(f"report session consent {label} 必须是 YYYY-MM-DD") from exc


def require_report_session_consent(
    config: dict[str, str],
    dates: set[dt.date],
    *,
    synthetic: bool,
) -> ReportSessionConsent:
    start = min(dates)
    end = max(dates)
    if synthetic:
        return ReportSessionConsent(
            version=SESSION_CONSENT_VERSION,
            start_date=start,
            end_date=end,
            fields=frozenset(),
            granted=False,
        )
    if config.get(f"{CONSENT_KEY_PREFIX}granted") != "true":
        raise SystemExit(
            "读取 Codex session 生成报告需要本次明确 --session-consent 和至少一个 --session-field；未授权时不读取会话、不打包、不发送 HTTP。"
        )
    version = config.get(f"{CONSENT_KEY_PREFIX}version", "")
    if version != SESSION_CONSENT_VERSION:
        raise SystemExit("report session consent version 缺失或不支持")
    consent_start = _parse_date(config.get(f"{CONSENT_KEY_PREFIX}start_date", ""), "start_date")
    consent_end = _parse_date(config.get(f"{CONSENT_KEY_PREFIX}end_date", ""), "end_date")
    if consent_start != start or consent_end != end:
        raise SystemExit(
            f"report session consent 时间窗口必须严格等于本次报告范围 {start.isoformat()}..{end.isoformat()}"
        )
    raw_fields = config.get(f"{CONSENT_KEY_PREFIX}fields", "")
    fields = frozenset(item.strip() for item in raw_fields.split(",") if item.strip())
    if not fields:
        raise SystemExit("report session consent fields 缺失；请显式提供 --session-field")
    unsupported = fields - ALLOWED_SESSION_FIELDS
    if unsupported:
        raise SystemExit("report session consent fields 不支持: " + ", ".join(sorted(unsupported)))
    if "patch_discovery" in fields and "project_hint" not in fields:
        raise SystemExit("patch_discovery consent 需要同时授权 project_hint")
    return ReportSessionConsent(version, consent_start, consent_end, fields, True)


def minimal_source_id(value: Any) -> str:
    text = str(value or "").strip()
    if SOURCE_ID_RE.fullmatch(text):
        return text.lower()
    digest = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]
    return f"session_{digest}"


def sanitize_work_summary(value: Any, *, limit: int = 180) -> str:
    text = sanitize_public_text(value, "", limit=640)
    text = REPORT_REQUEST_TAIL_RE.sub("", text)
    clauses = [item.strip(" ，,。；;") for item in re.split(r"[\n\r。；;]+", text) if item.strip()]
    selected = [item for item in clauses if WORK_CLAUSE_RE.search(item) and "[REDACTED]" not in item]
    if not selected:
        selected = [item for item in clauses if item and "[REDACTED]" not in item]
    summary = "；".join(selected[:2])
    return sanitize_public_text(summary, "", limit=limit)


def sanitize_command_summary(value: Any, *, limit: int = 120) -> str:
    try:
        tokens = shlex.split(str(value or ""), posix=True)
    except ValueError:
        tokens = []
    if not tokens:
        return ""
    command = Path(tokens[0]).name
    safe: list[str] = [sanitize_public_text(command, "command", limit=32)]
    for token in tokens[1:12]:
        if "=" in token or token.startswith(("/", "~")) or re.match(r"^[A-Za-z]:[\\/]", token):
            safe.append("[ARG]")
            continue
        if token.startswith("-"):
            safe.append(token.split("=", 1)[0][:32])
            continue
        normalized = re.sub(r"[^A-Za-z0-9_.-]", "", token).lower()
        safe.append(normalized if normalized in COMMAND_SUBCOMMANDS else "[ARG]")
    compact: list[str] = []
    for token in safe:
        if not compact or compact[-1] != token:
            compact.append(token)
    return sanitize_public_text(" ".join(compact), "", limit=limit)


@contextlib.contextmanager
def session_extraction_workspace() -> Iterator[Path]:
    configured = os.environ.get("AKBS_REPORT_SESSION_TMPDIR", "").strip()
    parent = require_safe_artifact_path(
        Path(configured) if configured else Path(tempfile.gettempdir()),
        purpose="report session temporary extraction",
    )
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="akbs-report-session-", dir=parent) as temporary:
        yield Path(temporary)


def session_evidence_payload(
    consent: ReportSessionConsent,
    *,
    synthetic: bool,
    source_session_ids: list[str],
    timezone: str,
) -> dict[str, Any]:
    return {
        "source": "akbs-member-ops",
        "synthetic_data": synthetic,
        "source_session_ids": list(dict.fromkeys(minimal_source_id(value) for value in source_session_ids)),
        "time_range": {
            "start_date": consent.start_date.isoformat(),
            "end_date": consent.end_date.isoformat(),
            "timezone": sanitize_public_text(timezone, "Asia/Shanghai", limit=64),
        },
        "consent": consent.payload(synthetic=synthetic),
        "retention": {
            "policy": SESSION_RETENTION_POLICY,
            "raw_session_copied": False,
            "temporary_artifacts_retained": False,
        },
    }


def _forbidden_keys(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in FORBIDDEN_EVIDENCE_KEYS:
                found.add(str(key))
            found.update(_forbidden_keys(item))
    elif isinstance(value, list):
        for item in value:
            found.update(_forbidden_keys(item))
    return found


def session_evidence_errors(payload: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["codex_sessions payload 必须是对象"]
    if set(payload) != SESSION_EVIDENCE_KEYS:
        errors.append("codex_sessions 只能包含最小 source/time/consent/retention 字段")
    if payload.get("source") not in {"akbs-member-ops", "android-knowledge-intake"}:
        errors.append("codex_sessions.source 非法")
    if not isinstance(payload.get("synthetic_data"), bool):
        errors.append("codex_sessions.synthetic_data 必须是布尔值")
    source_ids = payload.get("source_session_ids")
    if (
        not isinstance(source_ids, list)
        or len(source_ids) > 200
        or any(not isinstance(item, str) or MINIMAL_SOURCE_ID_RE.fullmatch(item) is None for item in source_ids)
        or len(source_ids) != len(set(source_ids))
    ):
        errors.append("codex_sessions.source_session_ids 必须是最小 source ID 数组")
    time_range = payload.get("time_range")
    if not isinstance(time_range, dict) or set(time_range) != TIME_RANGE_KEYS:
        errors.append("codex_sessions.time_range 必须记录授权时间范围")
    else:
        try:
            start = dt.date.fromisoformat(str(time_range.get("start_date") or ""))
            end = dt.date.fromisoformat(str(time_range.get("end_date") or ""))
        except ValueError:
            errors.append("codex_sessions.time_range 日期非法")
        else:
            if start > end:
                errors.append("codex_sessions.time_range 起止日期非法")
        timezone = time_range.get("timezone")
        if not isinstance(timezone, str) or len(timezone) > 64 or TIMEZONE_RE.fullmatch(timezone) is None:
            errors.append("codex_sessions.time_range.timezone 非法")
    consent = payload.get("consent")
    synthetic = payload.get("synthetic_data") is True
    if not isinstance(consent, dict) or set(consent) != CONSENT_PAYLOAD_KEYS:
        errors.append("codex_sessions.consent 只能包含最小授权字段")
    elif consent.get("version") != SESSION_CONSENT_VERSION:
        errors.append("codex_sessions.consent version 缺失或不支持")
    elif synthetic:
        if consent.get("scope") != "synthetic_fixture" or consent.get("granted") is not False or consent.get("fields") != []:
            errors.append("synthetic codex_sessions.consent 非法")
    else:
        fields = consent.get("fields")
        if consent.get("granted") is not True:
            errors.append("codex_sessions.consent.granted 必须为 true")
        if consent.get("scope") != "single_report_generation":
            errors.append("codex_sessions.consent.scope 非法")
        if (
            not isinstance(fields, list)
            or not fields
            or any(not isinstance(item, str) for item in fields)
            or len(fields) != len(set(fields))
            or set(fields) - ALLOWED_SESSION_FIELDS
        ):
            errors.append("codex_sessions.consent.fields 缺失或越界")
    retention = payload.get("retention")
    if not isinstance(retention, dict) or set(retention) != RETENTION_KEYS:
        errors.append("codex_sessions.retention 只能包含最小保留字段")
    elif retention.get("policy") != SESSION_RETENTION_POLICY:
        errors.append("codex_sessions.retention policy 缺失或不支持")
    elif retention.get("raw_session_copied") is not False or retention.get("temporary_artifacts_retained") is not False:
        errors.append("codex_sessions.retention 禁止保留原始会话或中间提取物")
    forbidden = _forbidden_keys(payload)
    if forbidden:
        errors.append("codex_sessions 含禁止的原始字段: " + ", ".join(sorted(forbidden)))
    return errors
