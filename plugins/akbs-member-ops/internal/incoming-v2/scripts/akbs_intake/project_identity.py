from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from akbs_member_ops.knowledge_rules import (
    find_company_project,
    find_company_projects,
    parse_company_project,
)
from akbs_member_ops.project_registry import source_access_registry_clues as registry_source_access_registry_clues
from akbs_intake.io_utils import read_text_sample


ReadmeUsable = Callable[[Path], bool]


def project_inference_payload(
    project: str,
    basis: list[str],
    checked_sources: list[str],
    raw_inputs: list[str],
    limits: list[str] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "project": project,
        "recognized": project != "unknown",
        "basis": basis,
        "checked_sources": checked_sources,
        "raw_inputs": raw_inputs[:20],
        "limits": limits or [],
        "company_rule_match": False,
    }
    if project != "unknown":
        payload.update(parse_company_project(project))
    return payload


def source_access_registry_clues(
    source_paths: list[str],
    registry_dir: Path | None = None,
) -> list[tuple[str, str]]:
    return registry_source_access_registry_clues(source_paths, registry_dir)


def source_context_clues(source_contexts: list[dict[str, Any]]) -> list[tuple[str, str]]:
    clues: list[tuple[str, str]] = []
    registry_paths: list[str] = []
    fields = (
        ("source_root", "capture source_root"),
        ("repo_path", "capture repo_path"),
        ("local_mount_path", "capture local_mount_path"),
        ("git_branch", "capture git branch"),
        ("git_remote", "capture git remote"),
        ("git_remotes", "capture git remotes"),
        ("remote_root", "capture remote_root"),
        ("ssh_host", "capture ssh_host"),
        ("sdk_name", "capture sdk_name"),
    )
    for context in source_contexts:
        for key, label in fields:
            value = context.get(key)
            if isinstance(value, str) and value.strip():
                clues.append((label, value))
                if key in {"source_root", "local_mount_path", "remote_root"}:
                    registry_paths.append(value)
    clues.extend(source_access_registry_clues(registry_paths))
    return clues


def infer_project(
    explicit_project: str,
    patch_entries: list[dict[str, Any]],
    patch_sources: list[dict[str, Any]],
    summary: str,
    package_dir: Path | None = None,
    source_contexts: list[dict[str, Any]] | None = None,
    related_report_clues: list[tuple[str, str]] | None = None,
    trusted_platform: str = "",
    readme_usable_for_inference: ReadmeUsable | None = None,
) -> tuple[str, dict[str, Any]]:
    explicit_clues: list[tuple[str, str]] = []
    if explicit_project and explicit_project.strip() != "unknown":
        explicit_clues.append(("命令参数 project", explicit_project.strip()))

    capture_project_clues: list[tuple[str, str]] = []
    for item in patch_entries:
        for key, label in (("project", "capture package project"),):
            value = item.get(key)
            if isinstance(value, str) and value:
                capture_project_clues.append((label, value))

    context_clues = source_context_clues(source_contexts or [])

    patch_clues: list[tuple[str, str]] = []
    for item in patch_entries:
        for key, label in (("path", "补丁路径"), ("readme", "补丁说明路径")):
            value = item.get(key)
            if isinstance(value, str) and value:
                patch_clues.append((label, value))
            if package_dir and key in {"path", "readme"} and isinstance(value, str) and value:
                source = package_dir / value
                if key == "readme" and readme_usable_for_inference and not readme_usable_for_inference(source):
                    continue
                sample = read_text_sample(source)
                if sample:
                    patch_clues.append((label.replace("路径", "内容"), sample))
    for item in patch_sources:
        for key, label in (("project", "补丁来源 project"), ("name", "补丁名称"), ("source", "补丁来源路径")):
            value = item.get(key)
            if isinstance(value, str) and value:
                if key == "project":
                    capture_project_clues.append((label, value))
                else:
                    patch_clues.append((label, value))
    if summary:
        patch_clues.append(("补丁摘要", summary))

    groups = [
        ("explicit", explicit_clues),
        ("capture_package_project", capture_project_clues),
        ("source_context", context_clues),
        ("patch_context", patch_clues),
        ("related_report", related_report_clues or []),
    ]
    clues = [(label, value) for _, values in groups for label, value in values if str(value).strip()]
    checked_sources = sorted(dict.fromkeys(label for label, _ in clues))
    raw_inputs = [f"{label}: {value}" for label, value in clues]
    matched: list[tuple[str, str, str]] = []
    for _, values in groups:
        for label, value in values:
            matched.extend((candidate, label, value) for candidate in find_company_projects(value, platform=trusted_platform))
    unique_projects = sorted(dict.fromkeys(project for project, _, _ in matched))
    if len(unique_projects) == 1:
        project = unique_projects[0]
        basis = [f"{label}: {value}" for matched_project, label, value in matched if matched_project == project]
        if trusted_platform and not any(project in str(value).upper() for matched_project, _, value in matched if matched_project == project):
            if project.startswith("TVI"):
                basis.append(f"可信平台证据 platform={trusted_platform} 用于按 TVI 芯片字段补齐，候选规范项目 {project}")
            else:
                basis.append(f"可信平台证据 platform={trusted_platform} 用于补齐缺失项目平台位，候选规范项目 {project}")
        return project, project_inference_payload(project, basis[:5], checked_sources, raw_inputs)
    if len(unique_projects) > 1:
        limits = [f"识别到多个项目型号: {', '.join(unique_projects)}，不能写成单一项目"]
        if explicit_project and explicit_project.strip() not in {"", "unknown"}:
            limits.append("命令参数 project 与其他项目线索不一致，未作为项目名写入上传包")
        payload = project_inference_payload("unknown", [], checked_sources, raw_inputs, limits)
        payload["candidates"] = unique_projects
        return "unknown", payload

    limits = ["未从命令参数、capture package、source_root/git/registry、补丁内容或关联报告中识别到 TVD/TVE/TVA/TVI 项目型号"]
    if explicit_project and explicit_project.strip() not in {"", "unknown"}:
        limits.append("命令参数 project 未匹配公司项目型号规范，未作为项目名写入上传包")
    weak_capture_projects = [
        value
        for label, value in capture_project_clues
        if value.strip() and value.strip() != "unknown" and not find_company_project(value, platform=trusted_platform)
    ]
    if weak_capture_projects:
        limits.append("capture package project 未匹配公司项目型号规范，未作为项目名写入上传包")
    return "unknown", project_inference_payload("unknown", [], checked_sources, raw_inputs, limits)
