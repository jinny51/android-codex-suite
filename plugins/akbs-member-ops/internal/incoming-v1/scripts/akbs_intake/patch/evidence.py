from __future__ import annotations

from pathlib import Path
from typing import Any

from akbs_intake.io_utils import (
    MATERIALS_DIR,
    list_string_values,
    materials_rel,
    read_json_file,
    read_referenced_json,
    safe_id,
    sha1_file,
    unique_strings,
    write_json,
)
from akbs_intake.patch.facts import patch_facts_from_text, patch_modified_files, patch_problem_and_risk_payloads
from akbs_intake.report_sessions import compact_text


REQUIRED_PATCH_EXPLANATION_KINDS = {"patch_problem_summary", "risk_surface"}


def bind_framework_evidence(package_dir: Path, rel: str, case_id: str, variant_id: str) -> None:
    path = package_dir / rel
    if not path.is_file():
        return
    payload = read_json_file(path)
    payload["case_id"] = case_id
    payload["variant_id"] = variant_id
    if payload.get("kind") == "source":
        source_payload = payload.get("payload")
        if not isinstance(source_payload, dict):
            source_payload = {}
        payload["payload"] = source_payload
    write_json(path, payload)


def bind_framework_evidence_paths(package_dir: Path, evidence_paths: list[str], case_id: str, variant_id: str) -> None:
    for rel in evidence_paths:
        bind_framework_evidence(package_dir, rel, case_id, variant_id)


def evidence_covers_patch(item: dict[str, Any], payload: dict[str, Any] | None, patch: dict[str, Any], patch_count: int) -> bool:
    if patch_count == 1:
        return True
    patch_id = str(patch.get("id") or "")
    patch_path = str(patch.get("path") or "")
    values = [item.get("patch_id"), item.get("patch"), item.get("source_patch")]
    if isinstance(payload, dict):
        values.extend([payload.get("patch_id"), payload.get("patch"), payload.get("source_patch"), payload.get("patch_path")])
    normalized = {str(value) for value in values if value}
    return bool((patch_id and patch_id in normalized) or (patch_path and patch_path in normalized))


def existing_explanation_kinds_for_entry(package_dir: Path, evidence_entries: list[dict[str, Any]], entry: dict[str, Any], patch_count: int) -> set[str]:
    patch = {"id": Path(str(entry.get("path", ""))).stem, "path": entry.get("path", "")}
    kinds: set[str] = set()
    for item in evidence_entries:
        if item.get("kind") not in REQUIRED_PATCH_EXPLANATION_KINDS:
            continue
        rel = item.get("path")
        payload = read_referenced_json(package_dir, rel) if isinstance(rel, str) else None
        if evidence_covers_patch(item, payload, patch, patch_count):
            kinds.add(str(item.get("kind")))
    return kinds


def ensure_patch_analysis_evidence(package_dir: Path, patch_entries: list[dict[str, Any]], evidence_entries: list[dict[str, Any]], summary: str) -> None:
    evidence_dir = package_dir / MATERIALS_DIR / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    patch_count = len(patch_entries)
    for entry in patch_entries:
        rel = str(entry.get("path") or "")
        if not rel:
            continue
        patch_path = package_dir / rel
        if not patch_path.is_file():
            continue
        text = patch_path.read_text(encoding="utf-8", errors="ignore")
        facts = patch_facts_from_text(text)
        captured_facts = entry.get("facts") if isinstance(entry.get("facts"), dict) else {}
        merged_facts = {**facts, **{key: value for key, value in captured_facts.items() if value}}
        entry["facts"] = merged_facts

        existing = existing_explanation_kinds_for_entry(package_dir, evidence_entries, entry, patch_count)
        patch_id = Path(rel).stem
        safe_patch_id = safe_id(patch_id)
        source_patch = rel

        diff_facts_payload = {
            "kind": "patch_diff_facts",
            "patch_id": patch_id,
            "source_patch": source_patch,
            "content_sha1": merged_facts.get("content_sha1") or sha1_file(patch_path),
            "modified_files": merged_facts.get("modified_files", []),
            "modules": merged_facts.get("modules", []),
            "symbols": merged_facts.get("symbols", []),
            "system_properties": merged_facts.get("system_properties", []),
            "settings_keys": merged_facts.get("settings_keys", []),
            "resource_keys": merged_facts.get("resource_keys", []),
            "framework_log_keys": merged_facts.get("framework_log_keys", []),
        }
        diff_facts_path = evidence_dir / f"{safe_patch_id}-patch-diff-facts.json"
        if not diff_facts_path.exists():
            write_json(diff_facts_path, diff_facts_payload)
            evidence_entries.append(
                {
                    "id": f"{safe_patch_id}-patch-diff-facts",
                    "kind": "patch_diff_facts",
                    "patch_id": patch_id,
                    "path": materials_rel("evidence", diff_facts_path.name),
                    "result": "INFO",
                    "summary": "patch facts from member-side package generation",
                }
            )

        problem_payload, risk_payload = patch_problem_and_risk_payloads(patch_id, source_patch, summary, merged_facts)
        if "patch_problem_summary" not in existing:
            problem_path = evidence_dir / f"{safe_patch_id}-patch-problem.json"
            write_json(problem_path, problem_payload)
            evidence_entries.append(
                {
                    "id": f"{safe_patch_id}-patch-problem",
                    "kind": "patch_problem_summary",
                    "patch_id": patch_id,
                    "path": materials_rel("evidence", problem_path.name),
                    "result": "INFO",
                    "summary": "member-side patch problem explanation",
                }
            )
        if "risk_surface" not in existing:
            risk_path = evidence_dir / f"{safe_patch_id}-risk-surface.json"
            write_json(risk_path, risk_payload)
            evidence_entries.append(
                {
                    "id": f"{safe_patch_id}-risk-surface",
                    "kind": "risk_surface",
                    "patch_id": patch_id,
                    "path": materials_rel("evidence", risk_path.name),
                    "result": "INFO",
                    "summary": "member-side patch risk surface",
                }
            )


def incoming_patch_item(package_dir: Path, patch_entry: dict[str, Any]) -> dict[str, Any]:
    patch_path = package_dir / str(patch_entry["path"])
    captured_facts = patch_entry.get("facts") if isinstance(patch_entry.get("facts"), dict) else {}
    content_sha1 = str(patch_entry.get("content_sha1") or sha1_file(patch_path))
    repo_path = str(patch_entry.get("repo_path") or captured_facts.get("repo_path") or "").strip("/")
    implementation_origin = str(patch_entry.get("implementation_origin") or captured_facts.get("implementation_origin") or "unknown")
    workflow_contract = str(
        patch_entry.get("workflow_contract")
        or captured_facts.get("workflow_contract")
        or ""
    )
    captured_by = str(patch_entry.get("captured_by") or captured_facts.get("captured_by") or "")
    coding_standard_check = patch_entry.get("coding_standard_check") if isinstance(patch_entry.get("coding_standard_check"), dict) else {}
    modified_files = captured_facts.get("modified_files") or patch_modified_files(patch_path)
    if repo_path:
        prefix = repo_path + "/"
        modified_files = [path if str(path).startswith(prefix) else prefix + str(path) for path in list_string_values(modified_files)]
    facts = {
        "content_sha1": content_sha1,
        "repo_path": repo_path,
        "platform_token": str(patch_entry.get("platform_token") or ""),
        "platform": str(patch_entry.get("platform") or ""),
        "android_version": str(patch_entry.get("android_version") or ""),
        "implementation_origin": implementation_origin,
        "workflow_contract": workflow_contract,
        "captured_by": captured_by,
        "modified_files": modified_files,
        "modules": captured_facts.get("modules") or [],
        "symbols": captured_facts.get("symbols") or [],
        "system_properties": captured_facts.get("system_properties") or [],
        "settings_keys": captured_facts.get("settings_keys") or [],
        "resource_keys": captured_facts.get("resource_keys") or [],
        "framework_log_keys": captured_facts.get("framework_log_keys") or [],
    }
    reuse_hint = patch_entry.get("reuse_hint", False)
    return {
        "id": Path(str(patch_entry["path"])).stem,
        "path": patch_entry["path"],
        "layer": str(patch_entry.get("layer") or ""),
        "readme": patch_entry.get("readme", ""),
        "content_sha1": content_sha1,
        "status": patch_entry.get("status", "candidate"),
        "reuse_hint": reuse_hint if isinstance(reuse_hint, bool) else reuse_hint,
        "note": str(patch_entry.get("note") or ""),
        "repo_path": repo_path,
        "implementation_origin": implementation_origin,
        "workflow_contract": workflow_contract,
        "captured_by": captured_by,
        "coding_standard_check": coding_standard_check,
        "artifact": "",
        "facts": facts,
    }


def aggregate_patch_diff_facts(patch_items: list[dict[str, Any]]) -> dict[str, Any]:
    aggregate: dict[str, list[str]] = {
        "modified_files": [],
        "modules": [],
        "symbols": [],
        "system_properties": [],
        "settings_keys": [],
        "resource_keys": [],
        "framework_log_keys": [],
    }
    patches: list[dict[str, Any]] = []
    content_hashes: list[str] = []
    implementation_origins: list[str] = []
    capture_tools: list[str] = []
    for item in patch_items:
        facts = item.get("facts", {}) if isinstance(item.get("facts"), dict) else {}
        content_sha1 = str(item.get("content_sha1") or facts.get("content_sha1") or "")
        if content_sha1:
            content_hashes.append(content_sha1)
        implementation_origin = str(item.get("implementation_origin") or facts.get("implementation_origin") or "").strip()
        captured_by = str(item.get("captured_by") or facts.get("captured_by") or "").strip()
        if implementation_origin:
            implementation_origins.append(implementation_origin)
        if captured_by:
            capture_tools.append(captured_by)
        for key in aggregate:
            aggregate[key].extend(list_string_values(facts.get(key)))
        patches.append(
            {
                "id": item.get("id", ""),
                "path": item.get("path", ""),
                "repo_path": item.get("repo_path", ""),
                "content_sha1": content_sha1,
                "status": item.get("status", "candidate"),
                "reuse_hint": bool(item.get("reuse_hint")),
                "note": str(item.get("note") or ""),
                "implementation_origin": implementation_origin,
                "captured_by": captured_by,
                "modified_files": list_string_values(facts.get("modified_files")),
                "modules": list_string_values(facts.get("modules")),
            }
        )
    payload: dict[str, Any] = {
        "patch_count": len(patch_items),
        "patches": patches,
        "content_sha1": content_hashes[0] if len(content_hashes) == 1 else "",
        "implementation_origins": unique_strings(implementation_origins),
        "capture_tools": unique_strings(capture_tools),
    }
    payload.update({key: unique_strings(values) for key, values in aggregate.items()})
    return payload


def ensure_required_patch_explanation_evidence(
    package_dir: Path,
    *,
    required_generated: dict[str, str],
    case_id: str,
    variant_id: str,
    summary: str,
) -> dict[str, str]:
    updated = dict(required_generated)
    for kind, rel in list(updated.items()):
        if rel:
            continue
        fallback = materials_rel("evidence", f"{kind}.json")
        payload: dict[str, Any] = {"basis": ["自动生成兜底证据"], "limits": ["缺少可解析补丁证据"]}
        if kind == "patch_problem_summary":
            payload.update(
                {
                    "problem_summary": summary,
                    "solution_summary": "成员端 Codex 未取得更完整的补丁说明，需结合 diff 和验证证据复核。",
                    "keywords": [],
                }
            )
        if kind == "risk_surface":
            payload["risk_areas"] = ["修改路径需按需求验证"]
        write_json(
            package_dir / fallback,
            {
                "kind": kind,
                "case_id": case_id,
                "variant_id": variant_id,
                **payload,
            },
        )
        updated[kind] = fallback
    return updated


def verification_payload_or_missing(verification_payload: dict[str, Any]) -> dict[str, Any]:
    result = str(verification_payload.get("result", "")).upper()
    if result in {"PASS", "FAIL"}:
        return verification_payload
    return {
        "result": "MISSING",
        "method": "not_provided",
        "summary": "未携带设备或等价验证证据，按非 validated 包状态上传。",
    }


def default_search_before_change_payload() -> dict[str, Any]:
    return {
        "result": "INFO",
        "method": "knowledge_search",
        "searched": False,
        "queries": [],
        "results": [],
        "summary": "未提供开发前知识库检索记录。",
    }


def select_search_before_change_payload(
    *,
    capture_search_payload: dict[str, Any],
    member_search_payload: dict[str, Any],
    capture_has_member_decision: bool,
) -> dict[str, Any]:
    if capture_has_member_decision:
        return capture_search_payload
    if member_search_payload:
        return member_search_payload
    if capture_search_payload:
        return capture_search_payload
    return default_search_before_change_payload()


def concrete_module_from_files(modified_files: list[str], repo_paths: list[str]) -> str:
    for path in modified_files:
        parts = [part for part in Path(path).parts if part not in {"", "."}]
        if len(parts) >= 4:
            return "/".join(parts[:4])
        if len(parts) >= 2:
            return "/".join(parts[:2])
    for repo_path in repo_paths:
        if repo_path:
            return repo_path
    return "unknown"


def feature_domain_from_text(summary: str, problem: str, modified_files: list[str]) -> str:
    text = " ".join([summary, problem, *modified_files]).lower()
    domains = [
        ("lockscreen", "锁屏"),
        ("launcher", "Launcher"),
        ("settings", "Settings"),
        ("systemui", "SystemUI"),
        ("display", "显示策略"),
        ("navigation", "导航策略"),
        ("audio", "音频策略"),
        ("camera", "相机"),
        ("usb", "USB 权限"),
        ("hdmi", "HDMI"),
        ("permission", "权限"),
    ]
    for token, label in domains:
        if token in text or label.lower() in text:
            return label
    if modified_files:
        stem = Path(modified_files[0]).stem
        return stem or "Framework 功能"
    return "Framework 功能"


def search_decision_value(search_payload: dict[str, Any]) -> str:
    payload = search_payload.get("payload", search_payload) if isinstance(search_payload, dict) else {}
    if not isinstance(payload, dict):
        return "unknown"
    decision = str(payload.get("reuse_decision") or payload.get("decision") or "").strip()
    if decision in {"reuse", "adapt", "reference_only", "not_found", "not_applicable", "unknown"}:
        return decision
    if payload.get("searched") is False:
        return "unknown"
    return "unknown"


def search_match_class_payload(search_payload: dict[str, Any]) -> dict[str, Any]:
    decision = search_decision_value(search_payload)
    if decision == "reuse":
        merge_hint = "candidate_only"
        explanation = "成员声明直接复用已有知识，但仍必须通过模块、细分领域、代码锚点、补丁行为和验证目标硬门禁。"
    elif decision in {"adapt", "reference_only"}:
        merge_hint = "reference_only"
        explanation = f"{decision} 只能作为参考证据，不能直接触发合并。"
    elif decision == "not_found":
        merge_hint = "not_found"
        explanation = "成员搜索未命中可复用知识，管理端仍需执行沉淀前重叠检索。"
    elif decision == "not_applicable":
        merge_hint = "not_applicable"
        explanation = "成员判断搜索结果不适用，不能触发合并。"
    else:
        merge_hint = "insufficient_evidence"
        explanation = "搜索使用决策缺失或未知，不能让管理端用标题猜合并。"
    payload = search_payload.get("payload", search_payload) if isinstance(search_payload, dict) else {}
    if not isinstance(payload, dict):
        payload = {}
    return {
        "decision": decision,
        "merge_hint": merge_hint,
        "targets": list_string_values(payload.get("targets")),
        "queries": list_string_values(payload.get("queries")),
        "explanation": explanation,
    }


def patch_view_payload(
    manifest_like: dict[str, Any],
    *,
    case_problem: str,
    case_solution: str,
    verification_payload: dict[str, Any],
    risk_payload: dict[str, Any],
    patch_rel_paths: list[str],
) -> dict[str, Any]:
    summary = str(manifest_like.get("summary") or "").strip() or "Framework 补丁包"
    result_summary = str(verification_payload.get("summary") or verification_payload.get("result") or "验证结果未提供")
    risks = list_string_values(risk_payload.get("risk_areas")) or list_string_values(risk_payload.get("limits"))
    risk_or_gap = "；".join(risks[:2]) if risks else "暂无明确遗留风险"
    detail_sections = [
        {"title": "问题", "items": [case_problem or summary]},
        {"title": "修改内容", "items": [case_solution or summary, *patch_rel_paths]},
        {"title": "验证结果", "items": [result_summary]},
        {"title": "遗留风险", "items": risks or ["暂无明确遗留风险"]},
        {"title": "下一步", "items": ["按管理端入库校验和沉淀判断继续处理。"]},
    ]
    return {
        "kind": "patch_view",
        "payload": {
            "package_label": "补丁包",
            "display_title": compact_text(summary, 80),
            "problem_summary": case_problem or summary,
            "solution_summary": case_solution or summary,
            "result_summary": result_summary,
            "project": manifest_like.get("project", "unknown"),
            "platform": manifest_like.get("platform", "unknown"),
            "android_version": manifest_like.get("android_version", "unknown"),
            "member_alias": manifest_like.get("member_alias", ""),
            "member_name": manifest_like.get("member_name", ""),
            "verification": {
                "result": str(verification_payload.get("result") or ""),
                "method": str(verification_payload.get("method") or ""),
                "summary": str(verification_payload.get("summary") or ""),
            },
            "ui_card": {
                "title": compact_text(summary, 48),
                "subtitle": f"{manifest_like.get('project', 'unknown')} / {manifest_like.get('platform', 'unknown')} / Android {manifest_like.get('android_version', 'unknown')}",
                "summary": compact_text(case_problem or summary, 120),
                "risk_or_gap": compact_text(risk_or_gap, 160),
            },
            "detail_sections": detail_sections,
        },
    }


def patch_ai_facts_payload(
    *,
    manifest_like: dict[str, Any],
    patch_diff_payload: dict[str, Any],
    search_payload: dict[str, Any],
    verification_payload: dict[str, Any],
    case_problem: str,
    case_solution: str,
    plugin_version: str,
) -> dict[str, Any]:
    modified_files = list_string_values(patch_diff_payload.get("modified_files"))
    repo_paths = unique_strings(str(item.get("repo_path") or "").strip("/") for item in patch_diff_payload.get("patches", []) if isinstance(item, dict))
    module = concrete_module_from_files(modified_files, repo_paths)
    feature_domain = feature_domain_from_text(str(manifest_like.get("summary") or ""), case_problem, modified_files)
    code_anchors = {
        "files": modified_files,
        "symbols": list_string_values(patch_diff_payload.get("symbols")),
        "resource_keys": list_string_values(patch_diff_payload.get("resource_keys")),
        "settings_keys": list_string_values(patch_diff_payload.get("settings_keys")),
        "system_properties": list_string_values(patch_diff_payload.get("system_properties")),
        "framework_log_keys": list_string_values(patch_diff_payload.get("framework_log_keys")),
    }
    patch_assets = [
        {
            "path": item.get("path", ""),
            "content_sha1": item.get("content_sha1", ""),
            "repo_path": item.get("repo_path", ""),
            "modified_files": list_string_values(item.get("modified_files")),
        }
        for item in patch_diff_payload.get("patches", [])
        if isinstance(item, dict)
    ]
    search_class = search_match_class_payload(search_payload)
    verification_targets = {
        "result": verification_payload.get("result", "MISSING"),
        "method": verification_payload.get("method", "not_provided"),
        "summary": verification_payload.get("summary", ""),
    }
    return {
        "module": module,
        "feature_domain": feature_domain,
        "patch_behavior_goal": case_problem or str(manifest_like.get("summary") or ""),
        "solution_summary": case_solution,
        "code_anchors": code_anchors,
        "patch_assets": patch_assets,
        "verification_targets": verification_targets,
        "search_usage": search_payload.get("payload", search_payload),
        "search_match_class": search_class,
        "merge_gate_inputs": {
            "module": module,
            "feature_domain": feature_domain,
            "code_anchors": code_anchors,
            "patch_behavior_goal": case_problem or str(manifest_like.get("summary") or ""),
            "verification_targets": verification_targets,
            "project": manifest_like.get("project", "unknown"),
            "platform": manifest_like.get("platform", "unknown"),
            "android_version": manifest_like.get("android_version", "unknown"),
            "search_match_class": search_class,
        },
        "protocol_version": "patch-human-ai-evidence-v1",
        "plugin_version": plugin_version,
    }


def risk_payload_or_default(risk_payload: dict[str, Any]) -> dict[str, Any]:
    if risk_payload:
        return risk_payload
    return {"risk_areas": ["修改路径需按需求验证"], "limits": ["缺少可解析风险证据"]}


def write_patch_view_and_ai_facts(
    package_dir: Path,
    *,
    manifest_context: dict[str, Any],
    case_id: str,
    variant_id: str,
    case_problem: str,
    case_solution: str,
    verification_payload: dict[str, Any],
    risk_payload: dict[str, Any],
    patch_rel_paths: list[str],
    patch_diff_payload: dict[str, Any],
    search_payload: dict[str, Any],
    plugin_version: str,
) -> tuple[str, str]:
    normalized_risk_payload = risk_payload_or_default(risk_payload)
    patch_view_path = materials_rel("display", "patch_view.json")
    write_json(
        package_dir / patch_view_path,
        patch_view_payload(
            manifest_context,
            case_problem=case_problem,
            case_solution=case_solution,
            verification_payload=verification_payload,
            risk_payload=normalized_risk_payload,
            patch_rel_paths=patch_rel_paths,
        ),
    )
    patch_ai_facts_path = materials_rel("evidence", "patch_ai_facts.json")
    write_json(
        package_dir / patch_ai_facts_path,
        {
            "kind": "patch_ai_facts",
            "case_id": case_id,
            "variant_id": variant_id,
            "payload": patch_ai_facts_payload(
                manifest_like=manifest_context,
                patch_diff_payload=patch_diff_payload,
                search_payload=search_payload,
                verification_payload=verification_payload,
                case_problem=case_problem,
                case_solution=case_solution,
                plugin_version=plugin_version,
            ),
        },
    )
    return patch_view_path, patch_ai_facts_path
