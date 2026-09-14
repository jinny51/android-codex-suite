from __future__ import annotations

from pathlib import Path
from typing import Any

from akbs_member_ops.patch_analysis import (
    changed_files_from_diff,
    facts_from_diff as shared_patch_facts_from_diff,
    modules_from_files as patch_modules_from_files,
    semantic_flags as patch_semantic_flags,
    semantic_keywords as patch_semantic_keywords,
    semantic_problem_solution as patch_semantic_problem_solution,
    semantic_risk_areas as patch_semantic_risk_areas,
)


def patch_modified_files(path: Path) -> list[str]:
    return changed_files_from_diff(path.read_text(encoding="utf-8", errors="ignore"))


def patch_facts_from_text(text: str) -> dict[str, Any]:
    facts = shared_patch_facts_from_diff(text)
    facts.pop("content_sha1", None)
    facts["modules"] = patch_modules_from_files(facts["modified_files"])
    return facts


def patch_problem_and_risk_payloads(patch_id: str, source_patch: str, summary: str, facts: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    files = facts.get("modified_files") or []
    modules = facts.get("modules") or patch_modules_from_files(files)
    joined = " ".join([summary, " ".join(files), " ".join(modules)]).lower()
    flags = patch_semantic_flags(joined, modules)
    keywords = sorted(
        {
            *modules,
            *[Path(path).stem for path in files],
            *patch_semantic_keywords(flags),
            *[item for item in ["focus", "launcher", "power", "policy", "package", "input"] if item in joined],
        }
    )
    basis = [f"补丁修改文件: {path}" for path in files]
    basis.extend(f"根据路径归属到模块: {module}" for module in modules)
    basis.extend(f"根据 diff hunk 识别符号: {symbol}" for symbol in facts.get("symbols", []))
    if summary:
        basis.append("提交时提供了补丁摘要")
    if not basis:
        basis = ["补丁文件存在，但缺少可解析的 diff 路径"]

    problem, solution, confidence = patch_semantic_problem_solution(modules, flags)
    risks = patch_semantic_risk_areas(modules, flags)

    limits = [
        "补丁内容不能单独证明原始需求文字",
        "补丁内容不能单独证明设备验证结果",
        "补丁内容不能单独证明发布状态",
    ]
    problem_payload = {
        "kind": "patch_problem_summary",
        "patch_id": patch_id,
        "source_patch": source_patch,
        "confidence": confidence,
        "problem_summary": problem,
        "solution_summary": solution,
        "keywords": keywords,
        "basis": basis,
        "limits": limits,
    }
    risk_payload = {
        "kind": "risk_surface",
        "patch_id": patch_id,
        "source_patch": source_patch,
        "confidence": confidence,
        "risk_areas": risks,
        "basis": basis,
        "limits": limits,
    }
    return problem_payload, risk_payload
