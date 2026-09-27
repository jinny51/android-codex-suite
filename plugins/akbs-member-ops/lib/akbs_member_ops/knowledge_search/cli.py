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
from akbs_member_ops.knowledge_search.formatting import compact_list, format_markdown
from akbs_member_ops.knowledge_search.local_index import load_rows, search
from akbs_member_ops.knowledge_search.originals import download_case_patch, fetch_case_patches
from akbs_member_ops.json_io import write_json
from akbs_member_ops.http_client import failure_result
from akbs_member_ops.member_config import expand_codex_path


REUSE_DECISIONS = ("reuse", "adapt", "reference_only", "not_applicable", "not_found", "unknown")
REUSE_OUTCOMES = ("not_started", "reused_success", "adapted_success", "failed", "partial", "unverified", "not_applicable")


def search_usage_root() -> Path:
    return configured_search_usage_root(config_payloads_fn=config_payloads)


def result_id(row: dict[str, Any]) -> str:
    for key in ("case_id", "variant_id", "patch_id", "symbol", "evidence_id", "report_id", "event_id", "id"):
        value = row.get(key)
        if isinstance(value, str) and value:
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
    for key in ("source", "search_mode", "reuse_grade", "matched_channels", "matched_anchors", "case_id", "package_id"):
        value = row.get(key)
        if value not in (None, "", []):
            payload[key] = value
    return payload


def record_search_usage(
    args: argparse.Namespace,
    root: Path | None,
    query: str,
    results: list[dict[str, Any]],
    *,
    source: str,
    search_mode: str,
    fallback_reason: str = "",
) -> Path | None:
    if args.no_record_usage or not query:
        return None
    now = dt.datetime.now().astimezone()
    profile, member_alias = selected_member_alias()
    decision = args.reuse_decision or ("not_found" if not results else "unknown")
    result_payloads = [usage_result(item) for item in results]
    digest = hashlib.sha1(
        json.dumps(
            {
                "query": query,
                "created_at": now.isoformat(timespec="seconds"),
                "decision": decision,
                "results": result_payloads[:8],
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:12]
    payload = {
        "schema": "android-knowledge-search-usage",
        "schema_version": "1",
        "created_at": now.isoformat(timespec="seconds"),
        "date": now.date().isoformat(),
        "profile": profile,
        "member_alias": member_alias,
        "root": str(root) if root else "",
        "query": query,
        "type": args.type,
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
    }
    path = search_usage_root() / now.strftime("%Y%m%d") / f"{now.strftime('%Y%m%d-%H%M%S')}-{digest}.json"
    write_json(path, payload)
    return path


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
    parser.add_argument("--type", choices=["all", "case", "variant", "patch", "report", "symbol", "event", "evidence"], default="all", help="Result type filter.")
    parser.add_argument("--limit", type=int, default=8, help="Maximum result count.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    parser.add_argument("--refresh", action="store_true", help="Run git pull --ff-only first when root is a clean Git worktree.")
    parser.add_argument("--include-synthetic", action="store_true", help="Include synthetic test data.")
    parser.add_argument("--no-record-usage", action="store_true", help="Do not write member-side search usage evidence.")
    parser.add_argument("--source", choices=["auto", "server", "local"], default="auto", help="Search source: auto prefers server API, server forbids fallback, local uses JSONL only.")
    parser.add_argument("--server-timeout", type=float, default=3.0, help="Server search timeout in seconds.")
    parser.add_argument("--reuse-decision", choices=REUSE_DECISIONS, help="Member-side use decision for this search.")
    parser.add_argument("--reuse-target", action="append", default=[], help="Matched case, variant, patch, or evidence id considered by this search. Repeatable.")
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
    if args.case_patches:
        if args.query or args.merge_confirmation or args.source == "local" or args.root or args.refresh:
            parser.error("--case-patches is a separate server-only read, not a search or merge action")
        if bool(args.download_patch) != bool(args.out):
            parser.error("--download-patch and --out must be used together")
        try:
            payload = (
                download_case_patch(args.case_patches, args.download_patch, expand_codex_path(args.out, resolve=False), timeout=args.server_timeout)
                if args.download_patch else fetch_case_patches(args.case_patches, timeout=args.server_timeout)
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
            for patch in payload["patches"]:
                print(f"{patch['asset_id']}  {patch['display_name']}  SHA-256={patch['sha256']}")
            if not payload["patches"]:
                print("当前没有可授权取得的完整原件；不使用截断预览代替。")
        return 0
    if args.download_patch or args.out:
        parser.error("--download-patch/--out require --case-patches")
    if args.merge_confirmation:
        return handle_merge_confirmation_command(args)

    query = " ".join(args.query).strip()
    root: Path | None = None
    refresh_status = None
    fallback_reason = ""
    source = "server_api"
    search_mode = "unknown"
    results: list[dict[str, Any]] = []

    if should_try_server(args):
        try:
            results, server_payload = fetch_server_results(args, query)
            search_mode = str(server_payload.get("search_mode") or "unknown")
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

    if source == "local_jsonl_fallback":
        root = find_root(args.root)
        refresh_status = refresh_root(root) if args.refresh else None
        rows = load_rows(root, include_archive=args.type in {"report", "event", "evidence"})
        results = search(rows, query, args.type, max(args.limit, 1), args.include_synthetic)
        for item in results:
            item["source"] = "local_jsonl_fallback"
            item["search_mode"] = "local_jsonl"

    record_search_usage(args, root, query, results, source=source, search_mode=search_mode, fallback_reason=fallback_reason)

    if args.json:
        print(
            json.dumps(
                {
                    "root": str(root) if root else "",
                    "query": query,
                    "type": args.type,
                    "source": source,
                    "search_mode": search_mode,
                    "fallback_reason": fallback_reason,
                    "count": len(results),
                    "refresh": refresh_status,
                    "results": results,
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
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
