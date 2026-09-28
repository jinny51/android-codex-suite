#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


PLUGIN_ROOT = Path(__file__).resolve().parents[3]
PLUGIN_LIB = PLUGIN_ROOT / "lib"
if PLUGIN_LIB.is_dir() and str(PLUGIN_LIB) not in sys.path:
    sys.path.insert(0, str(PLUGIN_LIB))

from akbs_member_ops.knowledge_search.api import (
    fetch_case_detail,
    fetch_merge_confirmation_payload,
    fetch_server_results,
    merge_api_error,
    post_merge_dispute,
    server_fallback_reason,
    should_try_server,
)
from akbs_member_ops.knowledge_search.config import ROOT_MARKERS, codex_home, config_payloads, configured_roots, expand_path
from akbs_member_ops.knowledge_search.config import search_usage_root as configured_search_usage_root
from akbs_member_ops.knowledge_search.config import selected_member_alias
from akbs_member_ops.knowledge_search.formatting import compact_list, format_case_detail, format_markdown
from akbs_member_ops.knowledge_search.local_index import load_rows, search
from akbs_member_ops.knowledge_search.originals import download_case_patch, fetch_case_patches
from akbs_member_ops.json_io import write_json_once
from akbs_member_ops.http_client import failure_result
from akbs_member_ops.member_config import expand_codex_path


REUSE_DECISIONS = ("reuse", "adapt", "reference_only", "not_applicable", "not_found", "unknown")
REUSE_OUTCOMES = ("not_started", "reused_success", "adapted_success", "failed", "partial", "unverified", "not_applicable")
MAX_QUERY_COUNT = 8
MAX_SERVER_LIMIT = 50
LEGACY_LOCAL_TYPES = frozenset({"variant", "report", "event", "evidence"})
DECISION_REUSE_GRADES = {
    "reuse": frozenset({"direct_reuse_candidate"}),
    "adapt": frozenset({"direct_reuse_candidate", "adaptation_candidate"}),
}


def search_usage_root() -> Path:
    return configured_search_usage_root(config_payloads_fn=config_payloads)


def result_id(row: dict[str, Any]) -> str:
    kind = str(row.get("kind") or row.get("type") or "")
    keys = {
        "case": ("case_id",),
        "implementation": ("implementation_id",),
        "patch": ("evidence_binding_id", "patch_asset_id", "patch_id"),
        "symbol": ("symbol",),
    }.get(kind, ("id", "case_id"))
    for key in keys:
        value = row.get(key)
        if isinstance(value, str) and value:
            if kind == "symbol":
                return "\x1f".join(
                    str(row.get(name) or "")
                    for name in ("case_id", "implementation_id", "evidence_binding_id", "symbol")
                )
            return value
    return ""


def usage_result(row: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "kind": row.get("kind", ""),
        "id": result_id(row),
    }
    title = row.get("title") or row.get("patch_name") or row.get("summary") or row.get("problem") or row.get("symbol")
    if isinstance(title, str) and title:
        payload["title"] = title
    path = row.get("path")
    if isinstance(path, str) and path:
        payload["path"] = path
    score = row.get("_score")
    if isinstance(score, (int, float)):
        payload["score"] = score
    for key in (
        "source",
        "search_mode",
        "reuse_grade",
        "qualification_reason",
        "requires_revalidation",
        "layers",
        "required_bindings",
        "evidence_gaps",
        "environment_comparison",
        "target_environment",
        "matched_channels",
        "matched_anchors",
        "case_id",
        "implementation_id",
        "evidence_binding_id",
        "patch_asset_id",
        "package_id",
    ):
        value = row.get(key)
        if value not in (None, "", []):
            payload[key] = value
    return payload


def validate_reuse_decision(
    *,
    decision: str,
    targets: list[str],
    results: list[dict[str, Any]],
) -> None:
    if decision not in {"reuse", "adapt", "reference_only"}:
        return
    normalized_targets = {str(value).strip() for value in targets if str(value).strip()}
    if not normalized_targets:
        raise SystemExit(f"{decision} requires at least one --reuse-target from this search")
    result_by_id = {
        result_id(item): item
        for item in results
        if result_id(item)
    }
    missing = sorted(normalized_targets - set(result_by_id))
    if missing:
        raise SystemExit(
            f"reuse target is not present in this search result: {', '.join(missing)}"
        )
    # Referring to a matching source is not an authorization to apply it. It may
    # be a local hint or an incomplete server candidate; preserve that judgment.
    if decision == "reference_only":
        return
    if decision in {"reuse", "adapt"}:
        non_implementations = sorted(
            target
            for target in normalized_targets
            if str(
                result_by_id[target].get("kind")
                or result_by_id[target].get("type")
                or ""
            )
            != "implementation"
        )
        if non_implementations:
            raise SystemExit(
                f"{decision} must bind an implementation result: {', '.join(non_implementations)}"
            )
    allowed_grades = DECISION_REUSE_GRADES[decision]
    mismatched = sorted(
        target
        for target in normalized_targets
        if result_by_id[target].get("reuse_grade") not in allowed_grades
    )
    if mismatched:
        raise SystemExit(
            f"{decision} requires server grade {' or '.join(sorted(allowed_grades))}: {', '.join(mismatched)}"
        )
    invalid_evidence = []
    for target in sorted(normalized_targets):
        item = result_by_id[target]
        bindings = item.get("required_bindings")
        if (
            not isinstance(bindings, list)
            or not bindings
            or any(
                not isinstance(binding, dict)
                or binding.get("binding_role") != "implementation"
                or binding.get("binding_state") != "accepted"
                or not str(binding.get("acceptance_ref") or "")
                or binding.get("closure_state") != "closed"
                for binding in bindings
            )
            or (decision == "reuse" and item.get("requires_revalidation") is not False)
        ):
            invalid_evidence.append(target)
    if invalid_evidence:
        raise SystemExit(
            f"{decision} requires closed accepted implementation evidence: "
            f"{', '.join(invalid_evidence)}"
        )


def record_search_usage(
    args: argparse.Namespace,
    root: Path | None,
    query: str,
    results: list[dict[str, Any]],
    *,
    source: str,
    search_mode: str,
    fallback_reason: str = "",
    queries: list[str] | None = None,
    server_payloads: list[dict[str, Any]] | None = None,
) -> Path | None:
    if not query:
        return None
    effective_queries = queries or [query]
    payloads = server_payloads or []
    decision = args.reuse_decision or "unknown"
    if source != "server_api" and decision in {
        "reuse",
        "adapt",
        "not_found",
    }:
        raise SystemExit(
            "local text search cannot authorize reuse, adapt, or not_found"
        )
    validate_reuse_decision(
        decision=decision,
        targets=args.reuse_target or [],
        results=results,
    )
    if decision in DECISION_REUSE_GRADES and not healthy_server_search(
        source=source,
        queries=effective_queries,
        payloads=payloads,
    ):
        raise SystemExit(
            f"{decision} requires complete server search responses with a ready and complete projection"
        )
    if decision == "not_found" and not healthy_multi_query_empty(
        source=source,
        queries=effective_queries,
        payloads=payloads,
        results=results,
    ):
        raise SystemExit(
            "not_found requires at least two distinct, complete server queries with a ready and complete projection"
        )
    if args.no_record_usage:
        return None
    now = dt.datetime.now().astimezone()
    profile, member_alias = selected_member_alias()
    result_payloads = [usage_result(item) for item in results]
    payload = {
        "schema": "android-knowledge-search-usage",
        "schema_version": "1",
        "created_at": now.isoformat(timespec="microseconds"),
        "date": now.date().isoformat(),
        "profile": profile,
        "member_alias": member_alias,
        "root": str(root) if root else "",
        "query": query,
        "queries": effective_queries,
        "type": args.type,
        "target_environment": {
            "project": str(args.project or "").strip(),
            "platform": str(args.platform or "").strip().lower(),
            "android_version": str(args.android_version or "").strip(),
        },
        "component_layers": sorted(
            {str(value) for value in args.component_layer or [] if str(value)}
        ),
        "limit": max(args.limit, 1),
        "source": source,
        "search_mode": search_mode,
        "fallback_reason": fallback_reason,
        "reuse_grades": sorted({str(item.get("reuse_grade") or "") for item in results if item.get("reuse_grade")}),
        "searched": True,
        "decision": decision,
        "reuse_decision": decision,
        "targets": args.reuse_target or [],
        "match_points": args.reuse_match or [],
        "mismatch_points": args.reuse_mismatch or [],
        "reason": args.reuse_reason or "",
        "outcome": args.reuse_outcome or "not_started",
        "result_count": len(results),
        "results": result_payloads,
        "server_search_health": [
            {
                "query": payload.get("query", ""),
                "result_state": payload.get("result_state", ""),
                "completeness": payload.get("completeness", ""),
                "reason_code": payload.get("reason_code", ""),
                "projection": payload.get("projection", {}),
                "pagination": payload.get("pagination", {}),
                "target_environment": payload.get("target_environment", {}),
                "filters": payload.get("filters", {}),
            }
            for payload in payloads
        ],
    }
    semantic_digest = hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    path = (
        search_usage_root()
        / now.strftime("%Y%m%d")
        / f"{now.strftime('%Y%m%d-%H%M%S-%f')}-{semantic_digest[:20]}.json"
    )
    write_json_once(path, payload)
    return path


def distinct_queries(primary: str, additional: list[str]) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for value in [primary, *additional]:
        normalized = " ".join(str(value or "").split())
        key = normalized.casefold()
        if normalized and key not in seen:
            values.append(normalized)
            seen.add(key)
    if len(values) > MAX_QUERY_COUNT:
        raise SystemExit(f"at most {MAX_QUERY_COUNT} distinct search queries are allowed")
    return values


def healthy_multi_query_empty(
    *,
    source: str,
    queries: list[str],
    payloads: list[dict[str, Any]],
    results: list[dict[str, Any]],
) -> bool:
    if source != "server_api" or len(queries) < 2 or len(payloads) != len(queries) or results:
        return False
    for query, payload in zip(queries, payloads):
        projection = payload.get("projection")
        pagination = payload.get("pagination")
        if (
            payload.get("query") != query
            or payload.get("result_state") != "empty_for_this_query"
            or payload.get("completeness") != "complete"
            or payload.get("results") != []
            or not isinstance(projection, dict)
            or projection.get("ready") is not True
            or projection.get("complete") is not True
            or not isinstance(pagination, dict)
            or pagination.get("total") != 0
            or pagination.get("has_more") is not False
        ):
            return False
    return True


def healthy_server_search(
    *,
    source: str,
    queries: list[str],
    payloads: list[dict[str, Any]],
) -> bool:
    if source != "server_api" or not queries or len(payloads) != len(queries):
        return False
    for query, payload in zip(queries, payloads):
        projection = payload.get("projection")
        pagination = payload.get("pagination")
        if (
            payload.get("query") != query
            or payload.get("result_state") == "indeterminate"
            or payload.get("completeness") != "complete"
            or not isinstance(projection, dict)
            or projection.get("ready") is not True
            or projection.get("complete") is not True
            or not isinstance(pagination, dict)
        ):
            return False
    return True


def combine_results(result_sets: list[tuple[str, list[dict[str, Any]]]]) -> list[dict[str, Any]]:
    combined: dict[tuple[str, str], dict[str, Any]] = {}
    for query, rows in result_sets:
        for row in rows:
            key = (str(row.get("kind") or ""), result_id(row))
            if not key[1]:
                key = (key[0], json.dumps(row, ensure_ascii=False, sort_keys=True))
            existing = combined.get(key)
            if existing is None:
                existing = dict(row)
                existing["matched_queries"] = [query]
                combined[key] = existing
            elif query not in existing["matched_queries"]:
                existing["matched_queries"].append(query)
    return list(combined.values())


def codex_documents_roots() -> list[Path]:
    candidates: list[Path] = []
    if os.environ.get("CODEX_DOCUMENTS"):
        candidates.append(expand_path(os.environ["CODEX_DOCUMENTS"]))
    candidates.append(expand_path(Path.home() / "Documents" / "Codex"))

    windows_users = Path("/mnt/c/Users")
    if windows_users.is_dir():
        try:
            for user_dir in windows_users.iterdir():
                candidates.append(user_dir / "Documents" / "Codex")
        except OSError:
            pass

    result: list[Path] = []
    seen: set[str] = set()
    for item in candidates:
        key = str(item)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def is_knowledge_root(path: Path) -> bool:
    try:
        return path.is_dir() and any((path / marker).exists() for marker in ROOT_MARKERS)
    except OSError:
        return False


def parent_candidates(path: Path) -> list[Path]:
    candidates = [path]
    candidates.extend(path.parents)
    return candidates


def candidate_roots(explicit_root: str | None) -> list[Path]:
    candidates: list[Path] = []
    if explicit_root:
        candidates.append(expand_path(explicit_root))
    env_root = os.environ.get("CODEX_KNOWLEDGE_ROOT")
    if env_root:
        candidates.append(expand_path(env_root))
    candidates.extend(configured_roots())

    try:
        candidates.extend(parent_candidates(Path.cwd().resolve()))
    except OSError:
        pass

    home = codex_home()
    for documents in codex_documents_roots():
        candidates.extend(
            [
                documents / "worktrees" / "knowledge",
            ]
        )
    candidates.extend(
        [
            home / "worktrees" / "knowledge",
            Path("/mnt/z/knowledge/knowledge"),
        ]
    )

    result: list[Path] = []
    seen: set[str] = set()
    for item in candidates:
        try:
            resolved = item.resolve()
        except OSError:
            resolved = item
        key = str(resolved)
        if key not in seen:
            seen.add(key)
            result.append(resolved)
    return result


def find_root(explicit_root: str | None) -> Path:
    checked: list[str] = []
    for root in candidate_roots(explicit_root):
        checked.append(str(root))
        if is_knowledge_root(root):
            return root
    raise SystemExit(
        "knowledge repository root not found. Pass --root <path>, set CODEX_KNOWLEDGE_ROOT, or configure knowledge_repo_worktree. Checked:\n"
        + "\n".join(f" - {item}" for item in checked[:16])
    )


def merge_identifier_from_payload(payload: dict[str, Any]) -> str:
    return str(payload.get("confirmation_id") or "").strip()


def text_or_unknown(value: Any) -> str:
    text = str(value or "").strip()
    return text if text else "unknown"


def merge_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    items = payload.get("items")
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def merge_detail_from_payloads(detail: dict[str, Any], target: dict[str, Any], compare: dict[str, Any]) -> dict[str, Any]:
    merged = dict(detail)
    for key, value in target.items():
        if value not in (None, "", [], {}) and key not in merged:
            merged[key] = value
    for key, value in compare.items():
        if value not in (None, "", [], {}):
            merged[key] = value
    return merged


def compact_evidence_items(value: Any, limit: int = 5) -> list[str]:
    items = value if isinstance(value, list) else []
    lines: list[str] = []
    for item in items[:limit]:
        if isinstance(item, dict):
            summary = item.get("summary") or item.get("title") or item.get("reason") or item.get("id") or item.get("kind")
            lines.append(str(summary or item))
        else:
            lines.append(str(item))
    return [line for line in lines if line]


def build_merge_analysis(detail: dict[str, Any], target: dict[str, Any], compare: dict[str, Any]) -> dict[str, Any]:
    combined = merge_detail_from_payloads(detail, target, compare)
    context = combined.get("member_agent_context") if isinstance(combined.get("member_agent_context"), dict) else {}
    target_knowledge = combined.get("target_knowledge") if isinstance(combined.get("target_knowledge"), dict) else {}
    if not target_knowledge and isinstance(context.get("target_case"), dict):
        target_knowledge = context["target_case"]
    supporting = compact_evidence_items(combined.get("merge_basis") or context.get("supporting_evidence"))
    counter = compact_evidence_items(combined.get("counter_evidence") or context.get("counter_evidence"))
    matched_anchors = combined.get("matched_anchors") or context.get("matched_anchors") or []
    code_anchors = context.get("code_anchors") if isinstance(context.get("code_anchors"), dict) else {}
    can_dispute = bool((combined.get("actions") if isinstance(combined.get("actions"), dict) else {}).get("can_submit_dispute"))
    has_counter = bool(counter)
    recommendation = "建议先核对反向证据；如目标知识与实际修改目标不一致，再发送异议。" if has_counter else "当前未看到明确反向证据；默认建议先确认合并依据，不自动提出异议。"
    if not can_dispute:
        recommendation = "当前状态不允许成员提交异议；只能作为只读分析材料。"
    draft = ""
    if can_dispute:
        title = target_knowledge.get("title") or combined.get("material_display_title") or merge_identifier_from_payload(combined)
        draft = f"我认为该材料不应合并到“{title}”。请复核目标知识、代码锚点和反向证据。"
        if counter:
            draft += " 反向证据：" + "；".join(counter[:3])
    return {
        "schema": "akbs-member-merge-confirmation-analysis-v1",
        "confirmation_id": str(combined.get("confirmation_id") or ""),
        "patch_package_id": str(combined.get("patch_package_id") or ""),
        "review_id": str(combined.get("review_id") or ""),
        "source_package_key": str(combined.get("package_key") or ""),
        "confirmation_status": str(combined.get("confirmation_status") or context.get("merge_status") or ""),
        "material_title": str(combined.get("material_display_title") or ""),
        "target_knowledge": target_knowledge,
        "why_merged": supporting,
        "matched_anchors": matched_anchors if isinstance(matched_anchors, list) else [],
        "code_anchors": code_anchors,
        "counter_evidence": counter,
        "can_submit_dispute": can_dispute,
        "recommendation": recommendation,
        "dispute_reason_draft": draft,
        "member_agent_context": context,
    }


def format_merge_list(payload: dict[str, Any]) -> str:
    items = merge_items(payload)
    lines = [
        "# 合并确认列表",
        "",
        f"- total: {payload.get('total', len(items))}",
        "- source: server_merge_confirmation",
        "",
    ]
    if not items:
        lines.append("暂无需要 Codex 分析的合并确认项。")
        return "\n".join(lines)
    for index, item in enumerate(items, start=1):
        target = item.get("target_knowledge") if isinstance(item.get("target_knowledge"), dict) else {}
        actions = item.get("actions") if isinstance(item.get("actions"), dict) else {}
        lines.extend(
            [
                f"{index}. {text_or_unknown(item.get('material_display_title'))}",
                f"   - confirmation_id: {text_or_unknown(item.get('confirmation_id'))}",
                f"   - patch_package_id: {text_or_unknown(item.get('patch_package_id'))}",
                f"   - source package_key: {text_or_unknown(item.get('package_key'))}",
                f"   - 状态: {text_or_unknown(item.get('confirmation_status_label') or item.get('confirmation_status'))}",
                f"   - 目标知识: {text_or_unknown(target.get('case_id'))} / {text_or_unknown(target.get('title'))}",
                f"   - 可提交异议: {'yes' if actions.get('can_submit_dispute') else 'no'}",
                "",
            ]
        )
    return "\n".join(lines).rstrip()


def format_merge_payload(title: str, payload: dict[str, Any]) -> str:
    target = payload.get("target_knowledge") if isinstance(payload.get("target_knowledge"), dict) else {}
    context = payload.get("member_agent_context") if isinstance(payload.get("member_agent_context"), dict) else {}
    lines = [
        f"# {title}",
        "",
        f"- confirmation_id: {text_or_unknown(payload.get('confirmation_id'))}",
        f"- patch_package_id: {text_or_unknown(payload.get('patch_package_id'))}",
        f"- source package_key: {text_or_unknown(payload.get('package_key'))}",
    ]
    if payload.get("dispute_id") or payload.get("state"):
        lines.append(f"- dispute/state: {text_or_unknown(payload.get('dispute_id'))} / {text_or_unknown(payload.get('state'))}")
    if payload.get("material_display_title"):
        lines.append(f"- 材料: {payload.get('material_display_title')}")
    if target:
        lines.append(f"- 目标知识: {text_or_unknown(target.get('case_id'))} / {text_or_unknown(target.get('title'))}")
        if target.get("summary"):
            lines.append(f"- 目标摘要: {target.get('summary')}")
    if payload.get("source_material"):
        source = payload.get("source_material") if isinstance(payload.get("source_material"), dict) else {}
        lines.append(f"- 来源材料: {text_or_unknown(source.get('title'))} / {text_or_unknown(source.get('package_key'))}")
    if payload.get("merge_basis"):
        lines.append("- 合并依据: " + "；".join(compact_evidence_items(payload.get("merge_basis"))))
    if payload.get("matched_anchors"):
        lines.append(f"- 相同锚点: {compact_list(payload.get('matched_anchors'), 8)}")
    if payload.get("counter_evidence"):
        lines.append("- 反向证据: " + "；".join(compact_evidence_items(payload.get("counter_evidence"))))
    if context:
        lines.append(f"- Codex 分析证据: schema={text_or_unknown(context.get('schema'))}, reuse_grade={text_or_unknown(context.get('reuse_grade'))}")
    return "\n".join(lines)


def format_merge_analysis(analysis: dict[str, Any]) -> str:
    target = analysis.get("target_knowledge") if isinstance(analysis.get("target_knowledge"), dict) else {}
    lines = [
        "# 合并确认 Codex 分析摘要",
        "",
        "## 人看摘要",
        "",
        f"- 材料: {text_or_unknown(analysis.get('material_title'))}",
        f"- confirmation_id: {text_or_unknown(analysis.get('confirmation_id'))}",
        f"- patch_package_id: {text_or_unknown(analysis.get('patch_package_id'))}",
        f"- source package_key: {text_or_unknown(analysis.get('source_package_key'))}",
        f"- 当前状态: {text_or_unknown(analysis.get('confirmation_status'))}",
        f"- 目标知识: {text_or_unknown(target.get('case_id'))} / {text_or_unknown(target.get('title'))}",
        f"- 是否可提交异议: {'yes' if analysis.get('can_submit_dispute') else 'no'}",
        f"- 建议: {analysis.get('recommendation')}",
        "",
        "## Codex 分析证据",
        "",
    ]
    why_merged = analysis.get("why_merged") if isinstance(analysis.get("why_merged"), list) else []
    counter = analysis.get("counter_evidence") if isinstance(analysis.get("counter_evidence"), list) else []
    matched = analysis.get("matched_anchors") if isinstance(analysis.get("matched_anchors"), list) else []
    lines.append("- 合并依据: " + ("；".join(why_merged) if why_merged else "未返回结构化合并依据"))
    lines.append("- 相同锚点: " + (compact_list(matched, 8) if matched else "未返回结构化相同锚点"))
    lines.append("- 反向证据: " + ("；".join(counter) if counter else "未返回结构化反向证据"))
    lines.extend(["", "## 异议理由草稿", ""])
    lines.append(analysis.get("dispute_reason_draft") or "当前不生成异议理由草稿。")
    return "\n".join(lines)


def refresh_root(root: Path) -> str:
    if not (root / ".git").exists():
        return "skip: root is not a Git worktree"
    status = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if status.returncode != 0:
        return f"skip: git status failed: {status.stderr.strip()}"
    if status.stdout.strip():
        return "skip: worktree is dirty"
    pull = subprocess.run(
        ["git", "-C", str(root), "pull", "--ff-only"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if pull.returncode != 0:
        return f"failed: {pull.stderr.strip() or pull.stdout.strip()}"
    return pull.stdout.strip() or "already up to date"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Search the Codex team knowledge repository.")
    parser.add_argument("query", nargs="*", help="Search terms. Use spaces to combine feature words, files, symbols, or project names.")
    parser.add_argument("--case-detail", help="Read the existing full solution for a server-returned case_id; no search or reuse receipt.")
    parser.add_argument("--implementation-id", help="Select one server-returned Implementation under --case-detail or --case-patches.")
    parser.add_argument("--case-patches", help="List authoritative patch originals for a server-returned case_id; server only.")
    parser.add_argument("--download-patch", help="Download one asset_id from --case-patches, verifying its size and SHA-256.")
    parser.add_argument("--out", help="New output file required with --download-patch; never overwrite existing work.")
    parser.add_argument(
        "--merge-confirmation",
        choices=["list", "detail", "target", "compare", "analyze", "dispute"],
        help="Read or explicitly dispute member merge confirmations instead of running knowledge search.",
    )
    parser.add_argument("--merge-confirmation-id", help="confirmation_id event identifier for merge confirmation detail, target, compare, analyze, or dispute.")
    parser.add_argument("--send-dispute", action="store_true", help="Actually POST a member merge dispute. Required with --merge-confirmation dispute.")
    parser.add_argument("--dispute-reason", default="", help="Human reason for a merge dispute. Only sent with --send-dispute.")
    parser.add_argument("--member-assessment", default="", help="Member/Codex assessment for a merge dispute. Only sent with --send-dispute.")
    parser.add_argument("--evidence-ref", action="append", default=[], help="Evidence reference to include when explicitly sending a merge dispute. Repeatable.")
    parser.add_argument("--root", help="Knowledge repository worktree path.")
    parser.add_argument("--type", choices=["all", "case", "implementation", "patch", "symbol", "variant", "report", "event", "evidence"], default="all", help="Result type filter; legacy variant/archive types are local text reads only.")
    parser.add_argument("--limit", type=int, default=8, help="Maximum result count.")
    parser.add_argument("--offset", type=int, default=0, help="Server result offset.")
    parser.add_argument("--project", default="", help="Target project for applicability comparison.")
    parser.add_argument("--platform", default="", help="Target chip platform for applicability comparison.")
    parser.add_argument("--android-version", default="", help="Target Android version for applicability comparison.")
    parser.add_argument("--component-layer", action="append", choices=["application", "platform", "native", "hal", "kernel", "device", "build"], default=[], help="Required Android layer filter. Repeatable.")
    parser.add_argument("--additional-query", action="append", default=[], help="Additional independent query for a bounded multi-query search. Repeatable.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    parser.add_argument("--refresh", action="store_true", help="Run git pull --ff-only first when root is a clean Git worktree.")
    parser.add_argument("--include-synthetic", action="store_true", help="Include synthetic test data.")
    parser.add_argument("--no-record-usage", action="store_true", help="Do not write member-side search usage evidence.")
    parser.add_argument("--source", choices=["auto", "server", "local"], default="auto", help="Search source: auto prefers server API, server forbids fallback, local uses JSONL only.")
    parser.add_argument("--server-timeout", type=float, default=3.0, help="Server search timeout in seconds.")
    parser.add_argument("--reuse-decision", choices=REUSE_DECISIONS, help="Member-side use decision for this search.")
    parser.add_argument("--reuse-target", action="append", default=[], help="Matched case, implementation, patch, or symbol id considered by this search. Repeatable.")
    parser.add_argument("--reuse-match", action="append", default=[], help="Why the matched knowledge may apply. Repeatable.")
    parser.add_argument("--reuse-mismatch", action="append", default=[], help="Why the matched knowledge may not directly apply. Repeatable.")
    parser.add_argument("--reuse-reason", default="", help="Reason for the reuse/adapt/reference/not-applicable decision.")
    parser.add_argument("--reuse-outcome", choices=REUSE_OUTCOMES, help="Outcome observed later for this search decision.")
    return parser


def handle_merge_confirmation_command(args: argparse.Namespace) -> int:
    action = args.merge_confirmation
    identifier = (args.merge_confirmation_id or " ".join(args.query)).strip()
    if action != "list" and not identifier:
        raise SystemExit("--merge-confirmation-id is required for this merge confirmation action")
    if action != "list" and "/" in identifier:
        raise SystemExit("--merge-confirmation-id requires the confirmation_id event identifier, not a source package_key")
    try:
        if action == "list":
            payload = fetch_merge_confirmation_payload(timeout=args.server_timeout)
            output = payload if args.json else format_merge_list(payload)
        elif action in {"detail", "target", "compare"}:
            endpoint_action = "" if action == "detail" else action
            payload = fetch_merge_confirmation_payload(identifier, endpoint_action, timeout=args.server_timeout)
            output = payload if args.json else format_merge_payload(f"合并确认 {action}", payload)
        elif action == "analyze":
            detail = fetch_merge_confirmation_payload(identifier, timeout=args.server_timeout)
            target = fetch_merge_confirmation_payload(identifier, "target", timeout=args.server_timeout)
            compare = fetch_merge_confirmation_payload(identifier, "compare", timeout=args.server_timeout)
            analysis = build_merge_analysis(detail, target, compare)
            output = analysis if args.json else format_merge_analysis(analysis)
        elif action == "dispute":
            if not args.send_dispute:
                raise SystemExit("--merge-confirmation dispute is read-only unless --send-dispute is also set")
            reason = args.dispute_reason.strip()
            assessment = args.member_assessment.strip()
            if not reason and not assessment:
                raise SystemExit("--dispute-reason or --member-assessment is required when sending a dispute")
            result = post_merge_dispute(
                identifier,
                reason=reason,
                member_assessment=assessment,
                evidence_refs=args.evidence_ref,
                agent_notes={"submitted_by": "akbs_knowledge_merge_review.py"},
                timeout=args.server_timeout,
            )
            output = result if args.json else format_merge_payload("合并异议发送结果", result)
        else:
            raise SystemExit(f"unsupported merge confirmation action: {action}")
    except Exception as exc:
        if isinstance(exc, ValueError) and "member_alias" in str(exc):
            raise SystemExit(str(exc))
        raise SystemExit(merge_api_error(exc))

    if args.json:
        print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(output)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.case_detail:
        if (
            args.query or args.case_patches or args.download_patch or args.out
            or args.merge_confirmation or args.source == "local" or args.root or args.refresh
            or args.additional_query or args.reuse_decision or args.reuse_outcome
        ):
            parser.error("--case-detail is a separate server-only read, not a search, download or reuse action")
        try:
            payload = fetch_case_detail(
                args.case_detail, implementation_id=args.implementation_id,
                timeout=args.server_timeout,
            )
        except ValueError as exc:
            raise SystemExit(str(exc)) from None
        except Exception as exc:
            raise SystemExit(failure_result(exc).safe_summary("knowledge detail API unavailable")) from None
        print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else format_case_detail(payload))
        return 0
    if args.case_patches:
        if args.query or args.merge_confirmation or args.source == "local" or args.root or args.refresh or args.additional_query or args.reuse_decision or args.reuse_outcome:
            parser.error("--case-patches is a separate server-only read, not a search or merge action")
        if bool(args.download_patch) != bool(args.out):
            parser.error("--download-patch and --out must be used together")
        try:
            payload = (
                download_case_patch(args.case_patches, args.download_patch, expand_codex_path(args.out, resolve=False), implementation_id=args.implementation_id, timeout=args.server_timeout)
                if args.download_patch else fetch_case_patches(args.case_patches, implementation_id=args.implementation_id, timeout=args.server_timeout)
            )
        except ValueError as exc:
            raise SystemExit(str(exc)) from None
        except Exception as exc:
            raise SystemExit(failure_result(exc).safe_summary("patch original API unavailable")) from None
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        elif args.download_patch:
            print(f"已取得原补丁并核对 SHA-256：{payload['output']}\n尚未应用或验证，不代表复用成功。")
        else:
            print(f"case_id={payload['case_id']}，原补丁={payload['availability']}")
            if payload.get("implementation_id"):
                print(f"implementation_id={payload['implementation_id']}")
                if payload["patches"]:
                    first = payload["patches"][0]
                    print(f"{first['implementation_summary']}；语义审核状态={first['review_state']}")
            for patch in payload["patches"]:
                print(f"{patch['asset_id']}  {patch['display_name']}  SHA-256={patch['sha256']}")
                if "authority" in patch:
                    role = patch.get("binding_role", "历史来源原件（未声明accepted role）")
                    print(f"  authority={patch['authority']}；role={role}；layer={patch['source']['layer']}")
                    for environment in patch["environments"]:
                        print("  环境=" + json.dumps(environment, ensure_ascii=False))
            if not payload["patches"]:
                print("当前没有可授权取得的完整原件；不使用截断预览代替。")
        return 0
    if args.download_patch or args.out:
        parser.error("--download-patch/--out require --case-patches")
    if args.implementation_id:
        parser.error("--implementation-id requires --case-detail or --case-patches")
    if args.merge_confirmation:
        return handle_merge_confirmation_command(args)
    if args.offset < 0:
        raise SystemExit("--offset must be zero or greater")
    if args.limit < 1 or args.limit > MAX_SERVER_LIMIT:
        raise SystemExit(f"--limit must be between 1 and {MAX_SERVER_LIMIT}")
    if args.type in LEGACY_LOCAL_TYPES and args.source == "server":
        raise SystemExit("legacy variant and archive result types require local text search")

    query = " ".join(args.query).strip()
    queries = distinct_queries(query, args.additional_query)
    if not queries:
        raise SystemExit("at least one non-empty search query is required")
    root: Path | None = None
    refresh_status = None
    fallback_reason = ""
    source = "server_api"
    search_mode = "unknown"
    results: list[dict[str, Any]] = []
    server_payloads: list[dict[str, Any]] = []

    if should_try_server(args) and args.type not in LEGACY_LOCAL_TYPES:
        try:
            result_sets = []
            for current_query in queries:
                current_results, server_payload = fetch_server_results(args, current_query)
                result_sets.append((current_query, current_results))
                server_payloads.append(server_payload)
            results = combine_results(result_sets)
            search_mode = str(server_payloads[0].get("search_mode") or "unknown")
        except Exception as exc:
            if isinstance(exc, ValueError) and "member_alias" in str(exc):
                raise SystemExit(str(exc))
            fallback_reason = server_fallback_reason(exc)
            if args.source == "server":
                raise SystemExit(fallback_reason)
            source = "local_jsonl_fallback"
            search_mode = "local_jsonl"
    else:
        source = "local_jsonl_fallback"
        search_mode = "local_jsonl"
        fallback_reason = "explicit local text search"

    if source == "local_jsonl_fallback":
        if args.reuse_decision in {"reuse", "adapt", "not_found"}:
            raise SystemExit(
                "local text search cannot authorize reuse, adapt, or not_found"
            )
        root = find_root(args.root)
        refresh_status = refresh_root(root) if args.refresh else None
        rows = load_rows(root, include_archive=args.type in {"report", "event", "evidence"})
        local_type = "implementation" if args.type == "variant" else args.type
        local_sets = [
            (
                current_query,
                search(rows, current_query, local_type, max(args.limit, 1), args.include_synthetic),
            )
            for current_query in queries
        ]
        results = combine_results(local_sets)[: max(args.limit, 1)]
        for item in results:
            item["source"] = "local_jsonl_fallback"
            item["search_mode"] = "local_jsonl"

    usage_receipt = record_search_usage(
        args,
        root,
        query,
        results,
        source=source,
        search_mode=search_mode,
        fallback_reason=fallback_reason,
        queries=queries,
        server_payloads=server_payloads,
    )
    usage_receipt_sha256 = ""
    if usage_receipt:
        usage_receipt_sha256 = hashlib.sha256(usage_receipt.read_bytes()).hexdigest()

    if args.json:
        print(
            json.dumps(
                {
                    "root": str(root) if root else "",
                    "query": query,
                    "queries": queries,
                    "type": args.type,
                    "source": source,
                    "search_mode": search_mode,
                    "fallback_reason": fallback_reason,
                    "count": len(results),
                    "refresh": refresh_status,
                    "results": results,
                    "server_responses": server_payloads,
                    "usage_receipt": str(usage_receipt or ""),
                    "usage_receipt_sha256": usage_receipt_sha256,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(
            format_markdown(
                root,
                query,
                results,
                refresh_status,
                source=source,
                search_mode=search_mode,
                fallback_reason=fallback_reason,
                server_payloads=server_payloads,
            )
        )
        if usage_receipt:
            print(f"\n搜索证据回执: {usage_receipt}")
            print(f"搜索证据 SHA256: {usage_receipt_sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
