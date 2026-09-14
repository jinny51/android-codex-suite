from __future__ import annotations

import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Any

OPS_PLUGIN_LIB = Path(__file__).resolve().parents[4] / "lib"
if OPS_PLUGIN_LIB.is_dir() and str(OPS_PLUGIN_LIB) not in sys.path:
    sys.path.insert(0, str(OPS_PLUGIN_LIB))

from akbs_member_ops.knowledge_rules import (
    classify_pre_change_search,
    workflow_requires_pre_change_search as shared_workflow_requires_pre_change_search,
)

from akbs_intake.config import CONFIG_DEFAULTS, artifact_read_roots, expanded_path
from akbs_intake.diagnostics import warn_local_input
from akbs_intake.io_utils import list_string_values, unique_strings
from akbs_intake.patch.validation import SCOPE_ANCHOR_GENERIC_TOKENS, scope_semantic_tokens
from akbs_intake.reports.common import ymd


SEARCH_USAGE_GENERIC_TOKENS = SCOPE_ANCHOR_GENERIC_TOKENS | {
    "android",
    "app",
    "apps",
    "base",
    "case",
    "core",
    "framework",
    "frameworks",
    "java",
    "package",
    "packages",
    "res",
    "service",
    "services",
    "src",
    "value",
    "values",
    "xml",
}


def search_usage_root(config: dict[str, str]) -> Path:
    return expanded_path(config.get("out_dir") or CONFIG_DEFAULTS["out_dir"]) / "search-usage"


def search_usage_record_dirs(config: dict[str, str], date: dt.date) -> list[Path]:
    return [
        root / "search-usage" / date_key
        for root in artifact_read_roots(config)
        for date_key in (ymd(date), date.isoformat())
    ]


def load_search_usage_records(config: dict[str, str], date: dt.date) -> list[dict[str, Any]]:
    member_alias = config.get("member_alias", "").strip()
    records: list[dict[str, Any]] = []
    seen: set[Path] = set()
    valid_dates = {date.isoformat(), ymd(date)}
    for directory in search_usage_record_dirs(config, date):
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.json")):
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            try:
                raw = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                warn_local_input("search_usage_unreadable", path)
                continue
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                warn_local_input("search_usage_invalid_json", path)
                continue
            if not isinstance(payload, dict):
                warn_local_input("search_usage_invalid_object", path)
                continue
            if payload.get("schema") != "android-knowledge-search-usage":
                warn_local_input("search_usage_unsupported_schema", path)
                continue
            record_date = str(payload.get("date") or "").strip()
            if record_date and record_date not in valid_dates:
                continue
            record_member = str(payload.get("member_alias") or "").strip()
            if record_member and member_alias and record_member != member_alias:
                continue
            payload["_record_path"] = str(path)
            records.append(payload)
    records.sort(key=lambda item: (str(item.get("created_at") or ""), str(item.get("_record_path") or "")))
    return records


def summarize_usage_result(result: Any) -> str:
    if isinstance(result, str):
        return result.strip()
    if not isinstance(result, dict):
        return ""
    kind = str(result.get("kind") or "").strip()
    result_id = str(result.get("id") or result.get("case_id") or result.get("variant_id") or result.get("patch_id") or "").strip()
    title = str(result.get("title") or result.get("summary") or "").strip()
    parts = [part for part in (kind, result_id, title) if part]
    return ": ".join(parts[:1]) + (" " + " / ".join(parts[1:]) if len(parts) > 1 else "")


def choose_search_usage_decision(records: list[dict[str, Any]]) -> str:
    for item in reversed(records):
        decision = str(item.get("reuse_decision") or item.get("decision") or "").strip()
        if decision and decision != "unknown":
            return decision
    if records and all(str(item.get("reuse_decision") or item.get("decision") or "") == "not_found" for item in records):
        return "not_found"
    return "unknown"


def search_usage_tokens(*values: Any) -> set[str]:
    return {token for token in scope_semantic_tokens(*values) if token not in SEARCH_USAGE_GENERIC_TOKENS}


def cjk_token(value: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", value))


def token_sets_related(left: set[str], right: set[str]) -> bool:
    if not left or not right:
        return False
    if left & right:
        return True
    for a in left:
        for b in right:
            if len(a) >= 4 and len(b) >= 4 and (a in b or b in a):
                return True
            if (cjk_token(a) or cjk_token(b)) and len(a) >= 2 and len(b) >= 2 and (a in b or b in a):
                return True
    return False


def search_usage_record_text_values(record: dict[str, Any]) -> list[Any]:
    values: list[Any] = [
        record.get("query"),
        record.get("decision"),
        record.get("reuse_decision"),
        record.get("reason"),
        record.get("outcome"),
        *list_string_values(record.get("targets")),
        *list_string_values(record.get("match_points")),
        *list_string_values(record.get("mismatch_points")),
    ]
    results = record.get("results")
    if isinstance(results, list):
        for result in results:
            values.append(summarize_usage_result(result))
            if isinstance(result, dict):
                values.extend([result.get("id"), result.get("case_id"), result.get("variant_id"), result.get("patch_id"), result.get("title"), result.get("summary")])
            else:
                values.append(result)
    return values


def search_usage_record_matches_feature(record: dict[str, Any], feature_tokens: set[str]) -> bool:
    if not feature_tokens:
        return False
    return token_sets_related(search_usage_tokens(*search_usage_record_text_values(record)), feature_tokens)


def patch_search_feature_tokens(summary: str, patch_items: list[dict[str, Any]], modified_files: list[str]) -> set[str]:
    values: list[Any] = [summary, *modified_files]
    for item in patch_items:
        facts = item.get("facts") if isinstance(item.get("facts"), dict) else {}
        values.extend([item.get("id"), item.get("path"), item.get("repo_path")])
        for key in ("symbols", "system_properties", "settings_keys", "resource_keys", "framework_log_keys"):
            values.extend(list_string_values(facts.get(key)))
        for path in list_string_values(facts.get("modified_files")):
            stem = Path(path).stem
            if stem:
                values.append(stem)
    return search_usage_tokens(*values)


def search_usage_payload(config: dict[str, str], date: dt.date, feature_tokens: set[str] | None = None) -> dict[str, Any]:
    records = load_search_usage_records(config, date)
    if feature_tokens is not None:
        records = [item for item in records if search_usage_record_matches_feature(item, feature_tokens)]
    if not records:
        return {}
    decision = choose_search_usage_decision(records)
    queries = unique_strings([str(item.get("query") or "").strip() for item in records])
    result_summaries = unique_strings(
        [
            summary
            for item in records
            for result in list(item.get("results") or [])
            for summary in [summarize_usage_result(result)]
            if summary
        ]
    )
    targets = unique_strings(
        [
            target
            for item in records
            for target in list_string_values(item.get("targets"))
        ]
    )
    match_points = unique_strings([point for item in records for point in list_string_values(item.get("match_points"))])
    mismatch_points = unique_strings([point for item in records for point in list_string_values(item.get("mismatch_points"))])
    reasons = unique_strings([str(item.get("reason") or "").strip() for item in records if str(item.get("reason") or "").strip()])
    compact_records = []
    for item in records:
        compact_records.append(
            {
                "created_at": item.get("created_at", ""),
                "query": item.get("query", ""),
                "type": item.get("type", "all"),
                "decision": item.get("decision") or item.get("reuse_decision") or "unknown",
                "reuse_decision": item.get("reuse_decision") or item.get("decision") or "unknown",
                "targets": list_string_values(item.get("targets")),
                "result_count": item.get("result_count", 0),
                "record_path": item.get("_record_path", ""),
            }
        )
    return {
        "result": "INFO",
        "method": "knowledge_search",
        "searched": True,
        "queries": queries,
        "results": result_summaries,
        "summary": f"收集到 {len(records)} 条成员侧知识搜索使用记录。",
        "decision": decision,
        "reuse_decision": decision,
        "targets": targets,
        "match_points": match_points,
        "mismatch_points": mismatch_points,
        "reason": "；".join(reasons),
        "outcome": "not_started",
        "records": compact_records,
    }


def search_payload_has_member_decision(payload: dict[str, Any]) -> bool:
    if not payload:
        return False
    if isinstance(payload.get("payload"), dict):
        payload = payload["payload"]
    decision = str(payload.get("reuse_decision") or payload.get("decision") or "").strip()
    if decision and decision != "unknown":
        return True
    for key in ("targets", "match_points", "mismatch_points"):
        values = payload.get(key)
        if isinstance(values, list) and any(str(item).strip() for item in values):
            return True
    if str(payload.get("reason") or "").strip():
        return True
    outcome = str(payload.get("outcome") or "").strip()
    return bool(outcome and outcome != "not_started")


def search_payload_needs_closed_decision(payload: dict[str, Any]) -> bool:
    if not payload:
        return False
    if isinstance(payload.get("payload"), dict):
        payload = payload["payload"]
    classification = classify_pre_change_search(
        payload,
        workflow_contract="current_codex_skill",
        package_status="validated",
    )
    return bool(classification.get("member_can_complete_before_upload"))


def search_payload_missing_required_pre_change_search(payload: dict[str, Any]) -> bool:
    if isinstance(payload.get("payload"), dict):
        payload = payload["payload"]
    classification = classify_pre_change_search(
        payload,
        workflow_contract="current_codex_skill",
        package_status="validated",
    )
    return bool(classification.get("requires_pre_change_search")) and not bool(classification.get("searched"))


def workflow_contract_requires_pre_change_search(workflow_contract: str) -> bool:
    return shared_workflow_requires_pre_change_search(workflow_contract)
