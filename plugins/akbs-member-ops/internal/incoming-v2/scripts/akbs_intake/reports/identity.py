from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from akbs_member_ops.knowledge_rules import (
    find_company_project,
    find_company_projects,
    parse_company_project,
)
from akbs_intake.config import artifact_read_roots
from akbs_intake.io_utils import read_json_file, read_text_sample, unique_strings
from akbs_intake.project_identity import project_inference_payload
from akbs_intake.report_sessions import (
    MISSING_REPORT_CUSTOMER,
    REPORT_MISSING_PROJECT_VALUES,
    SessionWork,
    report_project_customers_from_clues,
    ymd,
)


def related_report_project_clues(
    config: dict[str, str],
    run_ids: list[str],
    *,
    daily_label_prefix: str = "关联日报",
    weekly_label_prefix: str = "关联周报",
) -> list[tuple[str, str]]:
    member_alias = config.get("member_alias", "")
    clues: list[tuple[str, str]] = []
    for run_id in unique_strings(run_ids):
        if not run_id:
            continue
        manifests_by_path: dict[Path, Path] = {}
        for out_dir in artifact_read_roots(config):
            for bucket in ("pending", "submitted"):
                patterns = [f"*/*/{run_id}/manifest.json"]
                if member_alias:
                    patterns.insert(0, f"*/{member_alias}/{run_id}/manifest.json")
                for pattern in patterns:
                    for manifest_path in sorted((out_dir / bucket).glob(pattern)):
                        manifests_by_path[manifest_path] = manifest_path
        for manifest_path in manifests_by_path:
            package_dir = manifest_path.parent
            manifest = read_json_file(manifest_path)
            if manifest.get("package_kind") not in {"daily_trace", "weekly_trace"}:
                continue
            label_prefix = daily_label_prefix if manifest.get("package_kind") == "daily_trace" else weekly_label_prefix
            project = str(manifest.get("project") or "").strip()
            if project:
                clues.append((f"{label_prefix} project", project))
            projects = manifest.get("projects")
            if isinstance(projects, list):
                for item in projects:
                    if isinstance(item, str) and item.strip():
                        clues.append((f"{label_prefix} projects", item))
            summary = str(manifest.get("summary") or "").strip()
            if summary:
                clues.append((f"{label_prefix} summary", summary))
            report_path = manifest.get("report_path")
            if isinstance(report_path, str) and report_path:
                report_text = read_text_sample(package_dir / report_path)
                if report_text:
                    clues.append((f"{label_prefix}正文", report_text))
            files = manifest.get("files") if isinstance(manifest.get("files"), dict) else {}
            evidence_paths = files.get("evidence", []) if isinstance(files, dict) else []
            if isinstance(evidence_paths, list):
                for rel in evidence_paths:
                    if not isinstance(rel, str) or Path(rel).name != "project_inference.json":
                        continue
                    evidence = read_json_file(package_dir / rel)
                    payload = evidence.get("payload") if isinstance(evidence.get("payload"), dict) else {}
                    values = [payload.get("project"), *(payload.get("basis") or []), *(payload.get("raw_inputs") or [])]
                    for value in values:
                        if isinstance(value, str) and value.strip():
                            clues.append((f"{label_prefix} project_inference", value))
    return clues


def same_day_daily_report_run_ids(config: dict[str, str], date: dt.date) -> list[str]:
    member_alias = config.get("member_alias", "")
    run_ids: list[str] = []
    for out_dir in artifact_read_roots(config):
        for bucket in ("submitted", "pending"):
            daily_root = out_dir / bucket / ymd(date) / member_alias
            for manifest_path in sorted(daily_root.glob("*/manifest.json")):
                manifest = read_json_file(manifest_path)
                if manifest.get("package_kind") != "daily_trace":
                    continue
                if str(manifest.get("date") or "") != date.isoformat():
                    continue
                run_id = str(manifest.get("run_id") or manifest_path.parent.name)
                if run_id:
                    run_ids.append(run_id)
    run_ids = unique_strings(run_ids)
    if not run_ids:
        return []

    clues = related_report_project_clues(config, run_ids, daily_label_prefix="自动关联同日日报")
    projects = sorted(dict.fromkeys(project for _, text in clues for project in find_company_projects(text)))
    return run_ids if len(projects) == 1 else []


def infer_report_project(
    report_type: str,
    summary: str,
    items: dict[str, list[tuple[str, str]]],
    sessions: list[SessionWork],
    patches: list[Any],
) -> tuple[str, dict[str, Any]]:
    label_prefix = "日报上下文" if report_type == "daily" else "周报上下文"
    clues: list[tuple[str, str]] = []
    if summary:
        clues.append((f"{label_prefix} summary", summary))
    for project, entries in sorted(items.items()):
        if project:
            clues.append((f"{label_prefix} 项目分组", project))
        for title, progress in entries:
            if title:
                clues.append((f"{label_prefix} 工作项", title))
            if progress:
                clues.append((f"{label_prefix} 进展", progress))
    for session in sessions:
        for label, value in (
            ("session project", session.project),
            ("session cwd", session.cwd),
            ("session thread", session.thread_name),
        ):
            if value:
                clues.append((f"{label_prefix} {label}", value))
        for message in session.messages:
            if message:
                clues.append((f"{label_prefix} session message", message))
    for patch in patches:
        for label, value in (("patch project", patch.project), ("patch name", patch.name), ("patch path", str(patch.path))):
            if value:
                clues.append((f"{label_prefix} {label}", value))

    checked_sources = sorted(dict.fromkeys(label for label, value in clues if str(value).strip()))
    raw_inputs = [f"{label}: {value}" for label, value in clues if str(value).strip()]
    project_customers, customer_basis = report_project_customers_from_clues(clues)

    def attach_customers(payload: dict[str, Any]) -> dict[str, Any]:
        payload["project_customers"] = []
        for project, context in sorted(project_customers.items()):
            row = {"project": project, "customer_name": context["customer_name"]}
            if context.get("downstream_customer"):
                row["downstream_customer"] = context["downstream_customer"]
            payload["project_customers"].append(row)
        payload["customer_basis"] = {
            project: basis[:5]
            for project, basis in sorted(customer_basis.items())
        }
        project = str(payload.get("project") or "")
        if project and project not in REPORT_MISSING_PROJECT_VALUES:
            context = project_customers.get(project, {})
            payload["customer_name"] = context.get("customer_name", MISSING_REPORT_CUSTOMER)
            if context.get("downstream_customer"):
                payload["downstream_customer"] = context["downstream_customer"]
        return payload

    matched: list[tuple[str, str, str]] = []
    for label, value in clues:
        project = find_company_project(str(value))
        if project:
            matched.append((project, label, str(value)))

    unique_projects = sorted(dict.fromkeys(project for project, _, _ in matched))
    if len(unique_projects) == 1:
        project, label, value = matched[0]
        return project, attach_customers(project_inference_payload(project, [f"{label}: {value}"], checked_sources, raw_inputs))
    if len(unique_projects) > 1:
        base_models = sorted(dict.fromkeys(parse_company_project(project).get("base_model", "") for project in unique_projects))
        if len(base_models) == 1:
            base_project = base_models[0]
            payload = attach_customers(project_inference_payload(
                base_project,
                [f"{label_prefix}候选项目: {', '.join(unique_projects)}"],
                checked_sources,
                raw_inputs,
                [f"多个候选共享基础项目 {base_project}，日报写入基础项目并保留完整候选证据"],
            ))
            payload["candidates"] = unique_projects
            return base_project, payload
        payload = attach_customers(project_inference_payload(
            "unknown",
            [],
            checked_sources,
            raw_inputs,
            [f"{label_prefix}包含多个项目型号: {', '.join(unique_projects)}，不能写成单一项目"],
        ))
        payload["candidates"] = unique_projects
        return "unknown", payload
    return "unknown", attach_customers(project_inference_payload(
        "unknown",
        [],
        checked_sources,
        raw_inputs,
        [f"{label_prefix}未识别到 TVD/TVE/TVA/TVI 项目型号"],
    ))
