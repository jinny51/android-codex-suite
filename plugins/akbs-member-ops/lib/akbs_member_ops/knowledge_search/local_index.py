from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from akbs_member_ops.knowledge_search.formatting import parse_json, result_date


AI_DEFAULT_RESULT_KINDS = {"case", "implementation", "patch", "symbol"}
SERVER_QUALIFICATION_FIELDS = {
    "reuse_grade",
    "qualification_reason",
    "requires_revalidation",
    "required_bindings",
    "evidence_gaps",
    "environment_comparison",
    "target_environment",
    "reuse_score",
    "reuse_hint",
}
AI_EVIDENCE_KINDS = {
    "patch_diff_facts", "patch_problem_summary", "project_inference",
    "risk_surface", "build_result", "verification_result", "search_before_change",
}


def strip_server_qualification(item: dict[str, Any]) -> dict[str, Any]:
    """Local legacy indexes are text hints, never V4 reuse authority."""

    public = {
        key: value
        for key, value in item.items()
        if key not in SERVER_QUALIFICATION_FIELDS
    }
    if isinstance(public.get("knowledge_validity"), dict):
        public["knowledge_validity"] = {
            key: value
            for key, value in public["knowledge_validity"].items()
            if key not in {"reuse_score", "reuse_hint"}
        }
    if "variant_ids" in public:
        public["implementation_ids"] = public.pop("variant_ids")
    if "variant_id" in public:
        public["implementation_id"] = public.pop("variant_id")
    if public.get("type") == "variant":
        public["type"] = "implementation"
    return public


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def object_status(value: Any) -> str:
    return str(value or "").strip().lower()


def is_retracted_object(item: dict[str, Any]) -> bool:
    validity = item.get("knowledge_validity") if isinstance(item.get("knowledge_validity"), dict) else {}
    return (
        bool(item.get("retracted"))
        or bool(validity.get("retracted"))
        or object_status(item.get("status")) == "retracted"
        or object_status(item.get("status_maturity")) == "retracted"
        or object_status(item.get("result")) == "retracted"
    )


def row_case_id(item: dict[str, Any]) -> str:
    return str(item.get("case_id") or "").strip()


def case_is_searchable(case_id: str, active_case_ids: set[str]) -> bool:
    return not active_case_ids or not case_id or case_id in active_case_ids


def append_token(tokens: set[str], value: Any) -> None:
    text = str(value or "").strip()
    if text:
        tokens.add(text)


def retracted_reference_tokens(index_rows: list[dict[str, Any]], patch_rows: list[dict[str, Any]]) -> set[str]:
    tokens: set[str] = set()
    for item in [*index_rows, *patch_rows]:
        if not is_retracted_object(item):
            continue
        append_token(tokens, item.get("case_id"))
        append_token(tokens, item.get("variant_id"))
        append_token(tokens, item.get("patch_id"))
        append_token(tokens, item.get("id"))
        for key in ("case_ids", "variant_ids", "patch_ids"):
            value = item.get(key)
            if isinstance(value, list):
                for entry in value:
                    append_token(tokens, entry)
    return tokens


def contains_retracted_reference(value: Any, tokens: set[str]) -> bool:
    if not tokens:
        return False
    return any(token and token in stringify(value) for token in tokens)


def redact_retracted_references(value: Any, tokens: set[str]) -> Any:
    if not tokens:
        return value
    if isinstance(value, list):
        return [
            redacted
            for item in value
            if not contains_retracted_reference(item, tokens)
            for redacted in [redact_retracted_references(item, tokens)]
        ]
    if isinstance(value, dict):
        return {key: redact_retracted_references(item, tokens) for key, item in value.items()}
    if contains_retracted_reference(value, tokens):
        return ""
    return value


def evidence_row(item: dict[str, Any]) -> dict[str, Any]:
    row = dict(item)
    row["evidence_kind"] = row.pop("kind", "")
    row["id"] = row.get("id") or row.get("evidence_id") or ""
    row["payload"] = parse_json(row.get("payload"), {})
    row["kind"] = "evidence"
    return row


def load_from_jsonl(root: Path, include_archive: bool = False) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    case_index_rows = read_jsonl(root / "index" / "case-index.jsonl")
    variant_index_rows = read_jsonl(root / "index" / "variant-index.jsonl")
    evidence_index_rows = read_jsonl(root / "index" / "evidence-index.jsonl")
    symbol_index_rows = read_jsonl(root / "index" / "symbol-index.jsonl")
    patch_index_rows: list[dict[str, Any]] = []
    for path in sorted(root.glob("patches/by-id/*/patch.json")):
        item = parse_json(path.read_text(encoding="utf-8", errors="ignore"), {})
        if isinstance(item, dict):
            patch_index_rows.append({**item, "path": str(path.relative_to(root))})
    retracted_tokens = retracted_reference_tokens([*case_index_rows, *variant_index_rows, *evidence_index_rows], patch_index_rows)
    search_docs = {str(item.get("case_id", "")): item for item in read_jsonl(root / "index" / "search-docs.jsonl")}
    active_case_ids = {
        case_id
        for case_id, item in search_docs.items()
        if case_id and (include_archive or not is_retracted_object(item))
    }
    rejected_patch_ids: set[str] = set()
    active_patch_ids: set[str] = set()
    for item in case_index_rows:
        case_id = str(item.get("case_id", ""))
        if not include_archive and (is_retracted_object(item) or not case_is_searchable(case_id, active_case_ids)):
            continue
        doc = search_docs.get(case_id, {})
        replacement_fields = {
            key: doc.get(key, item.get(key))
            for key in ("replacement_case_id", "replacement_title", "replaces_case_ids")
            if doc.get(key, item.get(key))
        }
        rows.append(
            {
                **item,
                "kind": "case",
                "id": case_id,
                "source_priority": doc.get("source_priority", item.get("source_priority", 0)),
                "text": doc.get("text", item.get("text", "")),
                "variant_ids": doc.get("variant_ids", item.get("variant_ids", [])),
                **replacement_fields,
            }
        )
    for item in variant_index_rows:
        case_id = row_case_id(item)
        if not include_archive and (is_retracted_object(item) or not case_is_searchable(case_id, active_case_ids)):
            continue
        rows.append(
            {
                **item,
                "kind": "implementation",
                "id": item.get("variant_id", ""),
                "implementation_id": item.get("variant_id", ""),
            }
        )
    for item in evidence_index_rows:
        case_id = row_case_id(item)
        if not include_archive and (is_retracted_object(item) or not case_is_searchable(case_id, active_case_ids)):
            continue
        row = evidence_row(item)
        if not include_archive:
            row["payload"] = redact_retracted_references(row.get("payload"), retracted_tokens)
        rows.append(row)
    loaded_evidence_ids = {str(row.get("evidence_id") or row.get("id") or "") for row in rows if row.get("kind") == "evidence"}
    if include_archive:
        for path in sorted(root.glob("evidence/by-id/*.json")):
            item = parse_json(path.read_text(encoding="utf-8", errors="ignore"), {})
            evidence_id = str(item.get("evidence_id") or "")
            if isinstance(item, dict) and evidence_id not in loaded_evidence_ids:
                rows.append(evidence_row({**item, "path": str(path.relative_to(root))}))
    patch_rows: list[dict[str, Any]] = []
    for item in patch_index_rows:
        if isinstance(item, dict):
            patch_id = str(item.get("patch_id") or "")
            if not include_archive and (
                is_retracted_object(item) or not case_is_searchable(row_case_id(item), active_case_ids)
            ):
                if patch_id:
                    rejected_patch_ids.add(patch_id)
                continue
            if patch_id:
                active_patch_ids.add(patch_id)
            patch_rows.append({"kind": "patch", "id": patch_id, **item})
    for item in symbol_index_rows:
        case_id = row_case_id(item)
        patch_id = str(item.get("patch_id") or "")
        if not include_archive and (
            is_retracted_object(item)
            or not case_is_searchable(case_id, active_case_ids)
            or patch_id in rejected_patch_ids
            or (active_patch_ids and patch_id and patch_id not in active_patch_ids)
        ):
            continue
        rows.append({"kind": "symbol", "id": item.get("symbol_id", ""), "symbol": item.get("value", ""), **item})
    rows.extend(patch_rows)
    if include_archive:
        for path in sorted(root.glob("reports/by-id/*.json")):
            item = parse_json(path.read_text(encoding="utf-8", errors="ignore"), {})
            if isinstance(item, dict):
                rows.append({"kind": "report", "id": item.get("report_id", ""), "path": str(path.relative_to(root)), **item})
        for path in sorted(root.glob("events/by-id/*.json")):
            item = parse_json(path.read_text(encoding="utf-8", errors="ignore"), {})
            if isinstance(item, dict):
                rows.append({"kind": "event", "id": item.get("event_id", ""), "path": str(path.relative_to(root)), **item})
    return [strip_server_qualification(row) for row in rows]


def load_rows(root: Path, include_archive: bool = False) -> list[dict[str, Any]]:
    return load_from_jsonl(root, include_archive=include_archive)


def stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        return " ".join(stringify(item) for item in value)
    if isinstance(value, dict):
        return " ".join(f"{key} {stringify(item)}" for key, item in value.items())
    return str(value)


def row_text(row: dict[str, Any]) -> str:
    keys = [
        "id",
        "type",
        "case_id",
        "variant_id",
        "variant_ids",
        "report_ids",
        "title",
        "problem",
        "requirement_or_symptom",
        "solution_summary",
        "implementation_scope",
        "summary",
        "text",
        "overview",
        "author",
        "member_alias",
        "member_name",
        "project",
        "scope",
        "platform",
        "android_version",
        "repo_paths",
        "repo_path",
        "branch",
        "source_tree",
        "feature_slug",
        "original_patch_name",
        "patch_name",
        "patch_names",
        "content_sha1",
        "filename_confidence",
        "module",
        "status",
        "package_status",
        "reuse_hint",
        "package_kind",
        "validation_status",
        "result",
        "kind",
        "evidence_kind",
        "note",
        "source_package",
        "readme",
        "report_path",
        "patch_files",
        "modified_files",
        "modules",
        "symbols",
        "framework_log_keys",
        "system_properties",
        "settings_keys",
        "resource_keys",
        "strings",
        "keywords",
        "problem_summary",
        "solution_summary",
        "keywords",
        "inference_confidence",
        "inference_basis",
        "inference_limits",
        "risk_areas",
        "symbol",
        "path",
        "patch_id",
        "patch_ids",
        "items",
        "payload",
        "package_id",
        "event_id",
    ]
    return " ".join(stringify(row.get(key)) for key in keys)


def query_terms(query: str) -> list[str]:
    return [item.lower() for item in re.split(r"\s+", query.strip()) if item.strip()]


def score_row(row: dict[str, Any], terms: list[str]) -> tuple[int, list[str]]:
    if not terms:
        return 1, []
    weighted_fields = [
        (8, "title"),
        (8, "summary"),
        (8, "problem"),
        (8, "requirement_or_symptom"),
        (8, "solution_summary"),
        (8, "implementation_scope"),
        (8, "text"),
        (8, "feature_slug"),
        (8, "repo_path"),
        (8, "repo_paths"),
        (8, "summary"),
        (7, "scope"),
        (7, "symbol"),
        (7, "package_status"),
        (6, "modified_files"),
        (6, "patch_ids"),
        (6, "modules"),
        (6, "keywords"),
        (6, "problem_summary"),
        (6, "payload"),
        (6, "system_properties"),
        (6, "settings_keys"),
        (5, "risk_areas"),
        (5, "symbols"),
        (5, "strings"),
        (5, "framework_log_keys"),
        (4, "overview"),
        (4, "items"),
        (3, "project"),
        (3, "original_patch_name"),
        (3, "id"),
        (3, "case_id"),
        (3, "variant_id"),
        (2, "author"),
        (2, "status"),
        (2, "result"),
        (2, "package_kind"),
        (2, "evidence_kind"),
        (2, "note"),
        (1, "patch_files"),
        (1, "report_path"),
        (1, "path"),
    ]
    full_text = row_text(row).lower()
    score = 0
    matched: list[str] = []
    for term in terms:
        if term not in full_text:
            continue
        matched.append(term)
        score += 1
        for weight, field in weighted_fields:
            if term in stringify(row.get(field)).lower():
                score += weight
    return score, matched


def result_priority(row: dict[str, Any]) -> int:
    priority = 0
    try:
        priority += int(row.get("source_priority") or 0)
    except (TypeError, ValueError):
        pass
    status = str(row.get("package_status") or row.get("status") or "").lower()
    priority += {
        "validated": 50,
        "candidate": 30,
        "draft": 20,
        "failed": 10,
        "blocked": 5,
    }.get(status, 0)
    verification = row.get("verification") if isinstance(row.get("verification"), dict) else {}
    if str(verification.get("status", "")).lower() in {"pass", "passed"}:
        priority += 20
    return priority


def search(rows: list[dict[str, Any]], q: str, result_type: str, limit: int, include_synthetic: bool) -> list[dict[str, Any]]:
    terms = query_terms(q)
    results: list[dict[str, Any]] = []
    kind_filter = "" if result_type == "all" else result_type
    for row in rows:
        if result_type == "all" and not is_default_ai_result(row):
            continue
        if kind_filter and row.get("kind") != kind_filter:
            continue
        if not include_synthetic and bool(row.get("synthetic_data")):
            continue
        score, matched = score_row(row, terms)
        if score <= 0:
            continue
        normalized = dict(row)
        normalized["_score"] = score
        normalized["_matched_terms"] = matched
        results.append(normalized)
    results.sort(
        key=lambda item: (
            int(item.get("_score", 0)),
            result_priority(item),
            result_date(item),
            str(item.get("id") or item.get("case_id") or item.get("variant_id") or item.get("patch_id") or ""),
        ),
        reverse=True,
    )
    return results[:limit]


def is_default_ai_result(row: dict[str, Any]) -> bool:
    kind = str(row.get("kind") or "")
    if kind in AI_DEFAULT_RESULT_KINDS:
        return True
    return kind == "evidence" and str(row.get("evidence_kind") or "") in AI_EVIDENCE_KINDS
