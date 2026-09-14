#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any


PLUGIN_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_ROOT = Path(__file__).resolve().parent
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))
PLUGIN_LIB = PLUGIN_ROOT / "lib"
if PLUGIN_LIB.is_dir() and str(PLUGIN_LIB) not in sys.path:
    sys.path.insert(0, str(PLUGIN_LIB))

from android_engineering_ops.knowledge_rules import (
    aggregate_package_scope_errors,
    classify_patch_asset_names,
    classify_pre_change_search,
    find_company_project,
    find_company_projects,
    parse_platform_input as parse_platform_arg,
    template_leak_errors,
)
from android_engineering_ops.artifact_paths import require_safe_artifact_path
from android_engineering_ops.json_io import write_json
from android_engineering_ops.patch_analysis import (
    BANNED_LOG_PATTERNS,
    added_lines,
    changed_files_from_diff,
    changed_lines,
    facts_from_diff,
    modules_from_files,
    resource_keys_from_patch_text,
    semantic_flags,
    semantic_keywords,
    semantic_problem_solution,
    semantic_risk_areas,
    sha1_text,
    symbols_from_diff,
)
from android_engineering_ops.project_registry import source_access_registry_clues as registry_source_access_registry_clues
from android_engineering_ops.remote_patch_snapshot import (
    RemotePatchSnapshotError,
    decode_snapshot_blob,
    load_remote_patch_snapshot,
)
from android_engineering_ops.verification_evidence import (
    build_delivery_contract_fields,
    has_authoritative_requirement_result,
    requirement_contract_fields,
)
from android_engineering_ops.member.profile import MemberProfileError, load_member_profile
from android_engineering_ops.policy.patch_markers import (
    POLICY_ID,
    POLICY_VERSION,
    analyze_unified_diff_markers,
)
SCHEMA_VERSION = "2.0"
PATCH_NAME_RE = re.compile(r"^[a-z0-9]+[0-9]+-[A-Za-z0-9._-]+@[a-z0-9_.-]+\.patch$")
FRAMEWORK_LOG_LITERAL_RE = re.compile(r"FrameworkLog\.(?:d|i|w|e)\s*\([^,]+,\s*\"")
SUPPORTED_EXTERNAL_EVIDENCE_KINDS = {"build_result", "deploy_result", "device_health"}
AUTO_VERIFICATION_EVIDENCE_NAMES = (
    ".codex/evidence/latest-build-delivery.json",
    ".codex/evidence/build-delivery.json",
)
IMPLEMENTATION_ORIGINS = ("codex", "manual", "external", "historical", "mixed", "unknown")
COMPONENT_LAYERS = (
    "application",
    "platform",
    "native",
    "hal",
    "kernel",
    "device",
    "build",
)
LEGACY_DOMAIN_LAYERS = {
    "framework": "platform",
    "system_app": "application",
    "app": "application",
    "native": "native",
    "hal": "hal",
    "kernel": "kernel",
    "driver": "kernel",
    "device": "device",
    "build": "build",
}
CAPTURE_REVIEW_REQUIRED_ORIGINS = {"manual", "external", "historical", "mixed", "unknown"}
REUSE_DECISIONS = ("reuse", "adapt", "reference_only", "not_applicable", "not_found", "unknown")
REUSE_OUTCOMES = ("not_started", "reused_success", "adapted_success", "failed", "partial", "unverified", "not_applicable")


@dataclass
class RepositoryCapture:
    source_root: str
    repo_path: str
    git_info: dict[str, Any]
    diff_text: str
    facts: dict[str, Any]
    module: str
    patch_name: str
    patch_rel: str



from patch_capture.git_diff import (  # noqa: E402
    common_parent,
    filter_mode_only_diff_sections,
    git_metadata,
    git_root,
    infer_module_for_repo,
    infer_repo_path_from_root,
    mode_only_diff_path,
    prefixed_files,
    run,
    slug,
    split_diff_sections,
    unique_preserve,
)
from patch_capture.readme import change_readme_text as feature_readme_text, infer_reuse_decision  # noqa: E402


def direct_log_call_lines(diff_text: str) -> list[str]:
    hits: list[str] = []
    for line in added_lines(diff_text):
        if any(pattern in line for pattern in BANNED_LOG_PATTERNS):
            hits.append(line.strip())
    return hits


def parse_repository_layers(values: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        repo_path, separator, layer = value.partition("=")
        repo_path = repo_path.strip().strip("/")
        layer = layer.strip()
        if not separator or not repo_path or layer not in COMPONENT_LAYERS:
            raise SystemExit(
                "--repo-layer 必须使用 REPO_PATH=LAYER，LAYER 只能是 "
                + ", ".join(COMPONENT_LAYERS)
            )
        if repo_path in result and result[repo_path] != layer:
            raise SystemExit(f"同一 repo_path 不能声明多个 layer: {repo_path}")
        result[repo_path] = layer
    return result


def capture_layers(
    captures: list[RepositoryCapture],
    *,
    default_layer: str,
    repository_layers: dict[str, str],
) -> dict[str, str]:
    known = {capture.repo_path.strip("/") for capture in captures}
    unknown = sorted(set(repository_layers) - known)
    if unknown:
        raise SystemExit("--repo-layer 引用了本次补丁不存在的 repo_path: " + ", ".join(unknown))
    resolved: dict[str, str] = {}
    for capture in captures:
        repo_path = capture.repo_path.strip("/")
        layer = repository_layers.get(repo_path) or default_layer
        if layer not in COMPONENT_LAYERS:
            raise SystemExit(f"补丁缺少 layer 分类: {repo_path}")
        resolved[capture.patch_rel] = layer
    return resolved


def component_rows(patch_layers: dict[str, str]) -> list[dict[str, Any]]:
    return [
        {
            "layer": layer,
            "patches": sorted(
                path
                for path, assigned_layer in patch_layers.items()
                if assigned_layer == layer
            ),
        }
        for layer in COMPONENT_LAYERS
        if layer in set(patch_layers.values())
    ]


def framework_log_literal_lines(diff_text: str) -> list[str]:
    return [line.strip() for line in added_lines(diff_text) if FRAMEWORK_LOG_LITERAL_RE.search(line)]


def direct_debug_property_lines(capture: RepositoryCapture) -> list[str]:
    if any(path.endswith("FrameworkLog.java") for path in capture.facts.get("modified_files", [])):
        return []
    lines: list[str] = []
    for line in added_lines(capture.diff_text):
        if "SystemProperties.getBoolean(" in line and "persist.sys.framework.debug" in line:
            lines.append(line.strip())
    return lines


def collect_repository_captures(args: argparse.Namespace, platform: str, feature: str) -> list[RepositoryCapture]:
    raw_roots = args.source_root or ["."]
    roots: list[Path] = []
    for raw_root in raw_roots:
        root = git_root(Path(raw_root).expanduser().resolve())
        if root not in roots:
            roots.append(root)
    if len(roots) > 1 and args.module:
        raise SystemExit("多源码仓库功能包不接受单个 --module；模块名会按每个源码仓库自动推断。")

    captures: list[RepositoryCapture] = []
    used_names: set[str] = set()
    skipped_mode_only_roots: list[str] = []
    for root in roots:
        diff_cp = run(["git", "diff", "--binary", "--full-index", "HEAD", "--"], root, check=True)
        raw_diff_text = diff_cp.stdout
        if not raw_diff_text.strip():
            cached_diff_cp = run(["git", "diff", "--cached", "--binary", "--full-index", "HEAD", "--"], root, check=True)
            raw_diff_text = cached_diff_cp.stdout
        diff_text, mode_only_paths = filter_mode_only_diff_sections(raw_diff_text)
        if not raw_diff_text.strip():
            raise SystemExit(f"源码仓库没有发现相对 HEAD 的 git diff，无法生成补丁: {root}")
        if mode_only_paths and not diff_text.strip():
            skipped_mode_only_roots.append(f"{root}: {', '.join(mode_only_paths)}")
            continue
        if not diff_text.strip():
            continue

        facts = facts_from_diff(diff_text)
        repo_path = infer_repo_path_from_root(root, roots, facts["modified_files"])
        facts["repo_path"] = repo_path
        facts["modules"] = modules_from_files(prefixed_files(repo_path, facts["modified_files"]))
        facts["symbols"] = symbols_from_diff(diff_text)
        module = slug(args.module or infer_module_for_repo(repo_path, facts["modified_files"]))
        patch_name = f"{platform}-{module}@{feature}.patch"
        if patch_name in used_names:
            module = slug(repo_path.replace("/", "-"))
            patch_name = f"{platform}-{module}@{feature}.patch"
        if patch_name in used_names:
            module = f"{module}-{sha1_text(str(root))[:8]}"
            patch_name = f"{platform}-{module}@{feature}.patch"
        if not PATCH_NAME_RE.fullmatch(patch_name):
            raise SystemExit(f"生成的 patch 文件名不符合规范: {patch_name}")
        used_names.add(patch_name)
        captures.append(
            RepositoryCapture(
                source_root=str(root),
                repo_path=repo_path,
                git_info=git_metadata(root),
                diff_text=diff_text,
                facts=facts,
                module=module,
                patch_name=patch_name,
                patch_rel=f"patches/{patch_name}",
            )
        )
    if not captures and skipped_mode_only_roots:
        raise SystemExit(
            "源码仓库只有文件权限变化，已过滤权限噪声，无法生成有效功能补丁；"
            f"文件: {'; '.join(skipped_mode_only_roots)}"
        )
    return captures


def _capture_from_diff(
    *,
    args: argparse.Namespace,
    platform: str,
    feature: str,
    source_root: str,
    repo_path: str,
    git_info: dict[str, Any],
    diff_text: str,
    used_names: set[str],
) -> RepositoryCapture | None:
    filtered, mode_only_paths = filter_mode_only_diff_sections(diff_text)
    if mode_only_paths and not filtered.strip():
        return None
    if not filtered.strip():
        raise SystemExit(f"snapshot/patch artifact 没有可打包的 binary diff: {repo_path}")
    facts = facts_from_diff(filtered)
    facts["repo_path"] = repo_path
    facts["modules"] = modules_from_files(prefixed_files(repo_path, facts["modified_files"]))
    facts["symbols"] = symbols_from_diff(filtered)
    module = slug(args.module or infer_module_for_repo(repo_path, facts["modified_files"]))
    patch_name = f"{platform}-{module}@{feature}.patch"
    if patch_name in used_names:
        module = slug(repo_path.replace("/", "-"))
        patch_name = f"{platform}-{module}@{feature}.patch"
    if patch_name in used_names:
        module = f"{module}-{sha1_text(source_root)[:8]}"
        patch_name = f"{platform}-{module}@{feature}.patch"
    if not PATCH_NAME_RE.fullmatch(patch_name):
        raise SystemExit(f"生成的 patch 文件名不符合规范: {patch_name}")
    used_names.add(patch_name)
    return RepositoryCapture(
        source_root=source_root,
        repo_path=repo_path,
        git_info=git_info,
        diff_text=filtered,
        facts=facts,
        module=module,
        patch_name=patch_name,
        patch_rel=f"patches/{patch_name}",
    )


def collect_snapshot_captures(
    args: argparse.Namespace,
    platform: str,
    feature: str,
) -> tuple[list[RepositoryCapture], dict[str, Any]]:
    try:
        snapshot = load_remote_patch_snapshot(
            args.remote_snapshot,
            expected_workspace_id=args.snapshot_workspace_id,
            expected_command_id=args.snapshot_command_id,
            expected_remote_root=args.remote_source_root,
            expected_sha256=args.snapshot_sha256,
            now_ns=time.time_ns(),
            max_age_ns=args.snapshot_max_age_seconds * 1_000_000_000,
        )
    except RemotePatchSnapshotError as exc:
        raise SystemExit(f"remote snapshot 验证失败: {exc}") from exc
    if len(snapshot["repositories"]) > 1 and args.module:
        raise SystemExit("多源码仓库 snapshot 不接受单个 --module；模块名按每个远端仓库推断。")

    captures: list[RepositoryCapture] = []
    used_names: set[str] = set()
    mode_only: list[str] = []
    for repository in snapshot["repositories"]:
        repo_path = str(repository["repo_path"])
        head_diff = decode_snapshot_blob(repository["head_diff"], field=f"{repo_path}.head_diff")
        untracked_diff = decode_snapshot_blob(
            repository["untracked_diff"],
            field=f"{repo_path}.untracked_diff",
        )
        try:
            diff_text = (head_diff + untracked_diff).decode("utf-8")
            status_text = decode_snapshot_blob(
                repository["status"],
                field=f"{repo_path}.status",
            ).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SystemExit(f"remote snapshot Git 数据不是 UTF-8: {repo_path}") from exc
        remotes = repository.get("remotes") if isinstance(repository.get("remotes"), list) else []
        first_remote = ""
        for remote in remotes:
            urls = remote.get("fetch_urls") if isinstance(remote, dict) else []
            if isinstance(urls, list) and urls:
                first_remote = str(urls[0])
                break
        capture = _capture_from_diff(
            args=args,
            platform=platform,
            feature=feature,
            source_root=str(repository["root"]),
            repo_path=repo_path,
            git_info={
                "root": str(repository["root"]),
                "branch": str(repository["branch"]),
                "head": str(repository["head"]),
                "remote": first_remote,
                "remotes": remotes,
                "status": status_text,
            },
            diff_text=diff_text,
            used_names=used_names,
        )
        if capture is None:
            mode_only.append(repo_path)
            continue
        snapshot_changed = set(str(item) for item in repository["changed_files"])
        diff_changed = set(capture.facts.get("modified_files", []))
        if not diff_changed.issubset(snapshot_changed):
            raise SystemExit(f"remote snapshot changed_files 与 binary diff 不一致: {repo_path}")
        captures.append(capture)
    if not captures:
        suffix = f"；仅权限变化仓库: {', '.join(mode_only)}" if mode_only else ""
        raise SystemExit(f"remote snapshot 没有可打包功能 diff{suffix}")
    return captures, snapshot


def _stable_patch_text(path: Path) -> tuple[str, str]:
    try:
        before = path.stat()
        data = path.read_bytes()
        after = path.stat()
    except OSError as exc:
        raise SystemExit(f"显式 patch artifact 无法读取: {path}: {exc}") from exc
    identity = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns)
    if identity(before) != identity(after):
        raise SystemExit(f"显式 patch artifact 在读取期间发生变化: {path}")
    try:
        return data.decode("utf-8"), hashlib.sha256(data).hexdigest()
    except UnicodeDecodeError as exc:
        raise SystemExit(f"显式 patch artifact 不是 UTF-8 Git binary patch: {path}") from exc


def collect_patch_artifact_captures(
    args: argparse.Namespace,
    platform: str,
    feature: str,
) -> list[RepositoryCapture]:
    if len(args.patch_artifact) != len(args.patch_repo_path):
        raise SystemExit("--patch-artifact 与 --patch-repo-path 必须一一对应")
    if len(args.patch_artifact) > 1 and args.module:
        raise SystemExit("多 patch artifact 不接受单个 --module")
    captures: list[RepositoryCapture] = []
    used_names: set[str] = set()
    for raw_path, repo_path in zip(args.patch_artifact, args.patch_repo_path):
        if repo_path == ".":
            raise SystemExit(
                "manual/historical import 的 --patch-repo-path 必须是实际 Git 仓库路径，不能使用 Android 源码顶层 '.'；"
                "跨仓库功能必须为每个仓库分别提供 --patch-artifact 和 --patch-repo-path"
            )
        repo = PurePosixPath(repo_path)
        if (
            not repo_path
            or "\\" in repo_path
            or repo.is_absolute()
            or repo_path != repo.as_posix()
            or any(part in {"", ".", ".."} for part in repo.parts)
        ):
            raise SystemExit(f"--patch-repo-path 必须是规范的相对 Git 仓库路径: {repo_path}")
        path = Path(raw_path).expanduser().resolve()
        if not path.is_file():
            raise SystemExit(f"--patch-artifact 文件不存在: {path}")
        text, digest = _stable_patch_text(path)
        capture = _capture_from_diff(
            args=args,
            platform=platform,
            feature=feature,
            source_root=args.remote_source_root or f"manual-import:{digest}",
            repo_path=repo_path,
            git_info={
                "root": args.remote_source_root or "manual-import",
                "branch": "",
                "head": "",
                "remote": "",
                "remotes": [],
                "status": "explicit immutable patch artifact",
                "patch_artifact_sha256": digest,
            },
            diff_text=text,
            used_names=used_names,
        )
        if capture is not None:
            workspace_relative = []
            for raw_modified in capture.facts.get("modified_files", []):
                modified = str(raw_modified).removeprefix("./")
                modified_path = PurePosixPath(modified)
                if (
                    not modified
                    or modified_path.is_absolute()
                    or any(part in {"", ".", ".."} for part in modified_path.parts)
                ):
                    raise SystemExit(
                        f"显式 patch 包含越出仓库边界的路径: {raw_modified} ({repo_path})"
                    )
                if modified == repo_path or modified.startswith(repo_path.rstrip("/") + "/"):
                    workspace_relative.append(str(raw_modified))
            if workspace_relative:
                raise SystemExit(
                    "显式 patch 的文件路径相对于 Android 源码顶层，而不是声明的 Git 仓库根；"
                    f"请在 {repo_path} 仓库内单独生成 patch: {', '.join(workspace_relative)}"
                )
            captures.append(capture)
    if not captures:
        raise SystemExit("显式 patch artifact 没有可打包功能 diff")
    return captures


def infer_capture_project_for_feature(
    args: argparse.Namespace,
    captures: list[RepositoryCapture],
    trusted_platform: str = "",
) -> tuple[str, dict[str, Any]]:
    source_values: list[tuple[str, str]] = []
    for capture in captures:
        source_values.extend(
            [
                ("source_root", str(capture.source_root)),
                ("repo_path", capture.repo_path),
                ("git branch", capture.git_info.get("branch", "")),
                ("git remote", capture.git_info.get("remote", "")),
                ("git remotes", capture.git_info.get("remotes", "")),
                *source_access_registry_clues(capture.source_root),
            ]
        )
    diff_text = "\n".join(capture.diff_text for capture in captures)
    groups: list[tuple[str, list[tuple[str, str]]]] = [
        ("explicit", [("命令参数 project", args.project or "")]),
        ("source_context", source_values),
        (
            "patch_context",
            [
                ("功能摘要", args.summary or ""),
                ("功能名", args.feature or ""),
                ("补丁 diff", diff_text),
            ],
        ),
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
        if args.project and args.project.strip() not in {"", "unknown"}:
            limits.append("命令参数 project 与其他项目线索不一致，未作为项目名写入补丁包")
        payload = project_inference_payload("unknown", [], checked_sources, raw_inputs, limits)
        payload["candidates"] = unique_projects
        return "unknown", payload

    limits = ["未从命令参数、source_root、repo_path、git branch、git remote、source-access registry、功能摘要或 diff 中识别到 TVD/TVE/TVA/TVI 项目型号"]
    if args.project and args.project.strip() not in {"", "unknown"}:
        limits.append("命令参数 project 未匹配公司项目型号规范，未作为项目名写入补丁包")
    return "unknown", project_inference_payload("unknown", [], checked_sources, raw_inputs, limits)


def source_access_registry_clues(source_root: str | Path, registry_dir: Path | None = None) -> list[tuple[str, str]]:
    return registry_source_access_registry_clues([source_root], registry_dir)


def project_inference_payload(
    project: str,
    basis: list[str],
    checked_sources: list[str],
    raw_inputs: list[str],
    limits: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "project": project,
        "recognized": project != "unknown",
        "basis": basis,
        "checked_sources": checked_sources,
        "raw_inputs": raw_inputs[:20],
        "limits": limits or [],
        "recognition_scope": "TVD/TVE/TVA/TVI",
        "company_rule_match": project != "unknown",
    }


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise SystemExit(f"读取 evidence JSON 失败: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SystemExit(f"evidence JSON 必须是对象: {path}")
    return payload


def string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def merge_string_lists(first: list[str], second: list[str]) -> list[str]:
    result: list[str] = []
    for item in [*first, *second]:
        if item and item not in result:
            result.append(item)
    return result


def evidence_kind_from_file(path: Path, payload: dict[str, Any]) -> str:
    kind = str(payload.get("kind") or "").strip()
    if kind:
        return slug(kind)
    stem = path.stem.lower().replace("-", "_")
    aliases = {
        "build": "build_result",
        "build_result": "build_result",
        "deploy_result": "deploy_result",
        "device_health": "device_health",
        "verification_result": "verification_result",
        "search_before_change": "search_before_change",
    }
    return aliases.get(stem, stem or "external_evidence")


def evidence_result(payload: dict[str, Any]) -> str:
    value = str(payload.get("result") or payload.get("status") or "").upper()
    if value in {"PASS", "FAIL", "WARN", "INFO", "SKIPPED", "MISSING"}:
        return value
    return "INFO"


def source_roots_for_auto_evidence(args: argparse.Namespace) -> list[Path]:
    roots = [Path(item).expanduser().resolve() for item in args.source_root or []]
    if not roots:
        roots = [Path.cwd().resolve()]
    result: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        if root not in seen:
            seen.add(root)
            result.append(root)
    return result


def merge_auto_verification_payload(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    if not base:
        return dict(incoming)
    merged = dict(base)
    for key in ("result", "method", "summary", "device"):
        if not merged.get(key) and incoming.get(key):
            merged[key] = incoming[key]
    for key in ("build", "steps", "health_checks", "artifacts"):
        merged[key] = merge_string_lists(string_list(merged.get(key)), string_list(incoming.get(key)))
    for key in ("remote_build", "local_delivery"):
        current = merged.get(key) if isinstance(merged.get(key), dict) else {}
        update = incoming.get(key) if isinstance(incoming.get(key), dict) else {}
        combined = dict(current)
        for field, value in update.items():
            if field == "artifacts":
                combined[field] = list(value) if isinstance(value, list) else value
            elif not combined.get(field) and value:
                combined[field] = value
        if combined:
            merged[key] = combined
    return merged


def load_auto_verification_payload(args: argparse.Namespace) -> dict[str, Any]:
    if args.workflow_contract == "current_codex_skill":
        return {}
    payload: dict[str, Any] = {}
    for root in source_roots_for_auto_evidence(args):
        for rel in AUTO_VERIFICATION_EVIDENCE_NAMES:
            path = root / rel
            if not path.is_file():
                continue
            item = read_json(path)
            kind = evidence_kind_from_file(path, item)
            if kind != "verification_result":
                continue
            item.update(build_delivery_contract_fields())
            payload = merge_auto_verification_payload(payload, item)
    return payload


def implementation_review_required(origin: str) -> bool:
    return origin in CAPTURE_REVIEW_REQUIRED_ORIGINS


def implementation_review_mode(origin: str) -> str:
    return "capture_gate" if implementation_review_required(origin) else "development_safety_net"


def evidence_file_name(kind: str, source: Path, used_names: set[str]) -> str:
    base = f"{kind.replace('_', '-')}.json"
    if base not in used_names:
        return base
    return f"{kind.replace('_', '-')}-{sha1_text(str(source))[:8]}.json"


def collect_external_evidence(args: argparse.Namespace, evidence_dir: Path) -> list[dict[str, Any]]:
    sources: list[Path] = []
    for raw_dir in args.evidence_dir or []:
        directory = Path(raw_dir).expanduser().resolve()
        if not directory.is_dir():
            raise SystemExit(f"--evidence-dir 不是目录: {directory}")
        sources.extend(sorted(directory.glob("*.json")))
    for raw_file in args.build_result or []:
        source = Path(raw_file).expanduser().resolve()
        if not source.is_file():
            raise SystemExit(f"--build-result 文件不存在: {source}")
        sources.append(source)

    entries: list[dict[str, Any]] = []
    seen: set[Path] = set()
    used_names: set[str] = set()
    for source in sources:
        source = source.resolve()
        if source in seen:
            continue
        seen.add(source)
        payload = read_json(source)
        kind = evidence_kind_from_file(source, payload)
        if kind not in SUPPORTED_EXTERNAL_EVIDENCE_KINDS:
            allowed = ", ".join(sorted(SUPPORTED_EXTERNAL_EVIDENCE_KINDS))
            raise SystemExit(f"外部 evidence kind 不支持: {kind} ({source}); 允许: {allowed}")
        payload.setdefault("kind", kind)
        target_name = evidence_file_name(kind, source, used_names)
        used_names.add(target_name)
        target = evidence_dir / target_name
        write_json(target, payload)
        entries.append(
            {
                "id": slug(target.stem),
                "kind": kind,
                "path": f"evidence/{target.name}",
                "result": evidence_result(payload),
                "summary": str(payload.get("summary") or payload.get("message") or f"{kind} evidence"),
            }
        )
    return entries


def inferred_verification_method(args: argparse.Namespace, auto_payload: dict[str, Any] | None = None) -> str:
    if args.verification_method:
        return args.verification_method
    if auto_payload and auto_payload.get("method"):
        return str(auto_payload.get("method"))
    if args.equivalent_type or args.equivalent_reason or args.equivalent_coverage or args.remaining_risk:
        return "equivalent"
    if args.device or args.device_verification:
        return "device"
    return "not_provided"


def verification_result(args: argparse.Namespace, auto_payload: dict[str, Any] | None = None) -> dict[str, Any]:
    auto_payload = auto_payload or {}
    method = inferred_verification_method(args, auto_payload)
    has_evidence = bool(
        args.verification
        or args.device_verification
        or args.equivalent_coverage
        or args.equivalent_reason
        or args.health_check
        or args.artifact
        or auto_payload
    )
    result = args.verification_result or str(auto_payload.get("result") or "") or ("PASS" if has_evidence else "INFO")
    auto_remote_build = auto_payload.get("remote_build") if isinstance(auto_payload.get("remote_build"), dict) else {}
    auto_local_delivery = auto_payload.get("local_delivery") if isinstance(auto_payload.get("local_delivery"), dict) else {}
    auto_local_artifacts = string_list(auto_local_delivery.get("local_artifacts") if isinstance(auto_local_delivery, dict) else [])
    auto_adb_actions = string_list(auto_local_delivery.get("adb_actions") if isinstance(auto_local_delivery, dict) else [])
    auto_device_restarts = string_list(auto_local_delivery.get("device_restarts") if isinstance(auto_local_delivery, dict) else [])
    payload: dict[str, Any] = {
        "result": result,
        "method": method,
        "build": args.verification or string_list(auto_payload.get("build")),
        "device": args.device or str(auto_payload.get("device") or auto_local_delivery.get("adb_serial") or ""),
        "steps": args.device_verification or string_list(auto_payload.get("steps")),
        "observed": "\n".join(args.device_verification or string_list(auto_payload.get("steps"))),
        "health_checks": args.health_check or string_list(auto_payload.get("health_checks")),
        "artifacts": args.artifact or string_list(auto_payload.get("artifacts")),
    }
    remote_artifacts: list[dict[str, str]] = []
    for index, artifact in enumerate(args.remote_artifact or []):
        sha1 = args.artifact_sha1[index] if index < len(args.artifact_sha1 or []) else ""
        remote_artifacts.append({"path": artifact, "sha1": sha1})
    if not remote_artifacts and isinstance(auto_remote_build.get("artifacts"), list):
        remote_artifacts = [item for item in auto_remote_build.get("artifacts", []) if isinstance(item, dict)]
    payload["remote_build"] = {
        "host": args.remote_build_host or str(auto_remote_build.get("host") or ""),
        "source_root": args.remote_source_root or str(auto_remote_build.get("source_root") or ""),
        "command": args.remote_build_command or str(auto_remote_build.get("command") or ""),
        "profile": args.remote_build_profile or str(auto_remote_build.get("profile") or ""),
        "artifacts": remote_artifacts,
    }
    payload["local_delivery"] = {
        "transfer": args.artifact_transfer or str(auto_local_delivery.get("transfer") or ""),
        "local_artifacts": args.local_artifact or auto_local_artifacts,
        "adb_serial": args.adb_serial or str(auto_local_delivery.get("adb_serial") or ""),
        "adb_actions": args.adb_action or auto_adb_actions,
        "device_restarts": args.device_restart or auto_device_restarts,
    }
    if method == "equivalent":
        payload.update(
            {
                "equivalent_type": args.equivalent_type or "",
                "reason": args.equivalent_reason or "",
                "coverage": args.equivalent_coverage or [],
                "remaining_risk": args.remaining_risk or "",
            }
        )
    explicit_requirement_verification = (
        method == "device"
        and bool(args.device_verification)
        and bool(payload.get("build"))
        and bool(payload.get("steps"))
    ) or (
        method == "equivalent"
        and bool(payload.get("equivalent_type"))
        and bool(payload.get("reason"))
        and bool(payload.get("coverage"))
        and bool(payload.get("remaining_risk"))
    )
    if explicit_requirement_verification:
        payload.update(requirement_contract_fields(result))
    elif auto_payload:
        payload.update(build_delivery_contract_fields())
    else:
        payload.update(requirement_contract_fields("INFO"))
    return payload


def search_before_change(args: argparse.Namespace) -> dict[str, Any]:
    queries = args.search_query or []
    results = args.search_result or []
    summary = args.search_summary or ""
    decision = args.reuse_decision or infer_reuse_decision(queries, results, summary)
    return {
        "result": "INFO",
        "method": "knowledge_search",
        "searched": bool(queries or results or summary),
        "queries": queries,
        "results": results,
        "summary": summary or "not provided by capture command",
        "decision": decision,
        "reuse_decision": decision,
        "targets": args.reuse_target or [],
        "match_points": args.reuse_match or [],
        "mismatch_points": args.reuse_mismatch or [],
        "reason": args.reuse_reason or "",
        "outcome": args.reuse_outcome or "not_started",
    }


def validate_search_decision_for_status(args: argparse.Namespace, search_payload: dict[str, Any]) -> tuple[list[str], list[str]]:
    if args.status != "validated":
        return [], []
    classification = classify_pre_change_search(
        search_payload,
        workflow_contract=str(args.workflow_contract or ""),
        package_status=str(args.status or ""),
    )
    if not bool(classification.get("searched")):
        if not bool(classification.get("requires_pre_change_search")):
            return [], []
        return [
            "开发前知识搜索（pre-change knowledge search）未发生，不能事后补造。"
            "本包声明为 current_codex_skill 工作流，不能按当前 validated 上传；"
            "请保持真实工作流和实施来源，不能互相改写以规避门禁。"
            "当前结果可留在本地或报告上下文；后续重新开发时必须先完成知识检索。"
        ], []
    if not bool(classification.get("member_can_complete_before_upload")):
        return [], []
    return [
        "已验证（validated）补丁包命中知识搜索结果时必须闭合搜索使用决策（search usage decision），"
        "请传 --reuse-decision reuse/adapt/reference_only/not_applicable/not_found，"
        "并记录 --reuse-target、--reuse-match、--reuse-mismatch 或 --reuse-reason。"
    ], []


def validate_feature_scope(args: argparse.Namespace) -> list[str]:
    text = " ".join([str(args.summary or ""), str(args.feature or "")])
    return aggregate_package_scope_errors(text)


def validate_patch_asset_names(captures: list[RepositoryCapture]) -> list[str]:
    classification = classify_patch_asset_names([capture.patch_name for capture in captures])
    if classification.get("status") != "fail":
        return []
    names = "、".join(str(item) for item in classification.get("uncontrolled_prefixes", []))
    return [
        "补丁资产命名包含非受控前缀"
        + (f"：{names}" if names else "")
        + "。前缀必须是合法项目名（project）或 mtk/rk/unisoc 受控平台 Android 版本前缀。"
    ]


def validate_verification_for_status(args: argparse.Namespace, payload: dict[str, Any]) -> list[str]:
    if args.status != "validated":
        return []

    errors: list[str] = []
    method = payload.get("method")
    if payload.get("result") != "PASS":
        errors.append("status 是 validated 时 verification-result.result 必须是 PASS")
    elif not has_authoritative_requirement_result(payload, expected_result="PASS"):
        errors.append(
            "status 是 validated 时必须提供明确的需求级验收 PASS；"
            "构建、artifact delivery、adb push、重启或旧版无作用域 evidence 不能替代需求行为验收"
        )

    if method == "device":
        if not payload.get("build"):
            errors.append("status 是 validated 且 method=device 时必须提供 --verification 构建验证")
        if not payload.get("steps"):
            errors.append("status 是 validated 且 method=device 时必须提供 --device-verification 真机验证")
    elif method == "equivalent":
        if not payload.get("equivalent_type"):
            errors.append("status 是 validated 且 method=equivalent 时必须提供 --equivalent-type")
        if not payload.get("reason"):
            errors.append("status 是 validated 且 method=equivalent 时必须提供 --equivalent-reason")
        if not payload.get("coverage"):
            errors.append("status 是 validated 且 method=equivalent 时必须提供 --equivalent-coverage")
        if not payload.get("remaining_risk"):
            errors.append("status 是 validated 且 method=equivalent 时必须提供 --remaining-risk")
    else:
        errors.append("status 是 validated 时必须提供 device 或 equivalent 验证证据")

    return errors


def aggregate_feature_facts(captures: list[RepositoryCapture]) -> dict[str, Any]:
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
    for capture in captures:
        facts = capture.facts
        content_sha1 = str(facts.get("content_sha1") or "")
        if content_sha1:
            content_hashes.append(content_sha1)
        modified_files = prefixed_files(capture.repo_path, list(facts.get("modified_files", [])))
        aggregate["modified_files"].extend(modified_files)
        aggregate["modules"].extend(list(facts.get("modules", [])))
        aggregate["symbols"].extend(list(facts.get("symbols", [])))
        for key in ("system_properties", "settings_keys", "resource_keys", "framework_log_keys"):
            aggregate[key].extend(list(facts.get(key, [])))
        patches.append(
            {
                "id": Path(capture.patch_name).stem,
                "path": capture.patch_rel,
                "repo_path": capture.repo_path,
                "source_root": str(capture.source_root),
                "content_sha1": content_sha1,
                "status": "",
                "modified_files": modified_files,
                "modules": list(facts.get("modules", [])),
                "symbols": list(facts.get("symbols", [])),
            }
        )
    payload: dict[str, Any] = {
        "kind": "patch_diff_facts",
        "scope": "feature",
        "patch_count": len(captures),
        "patches": patches,
        "content_sha1": content_hashes[0] if len(content_hashes) == 1 else "",
    }
    payload.update({key: unique_preserve(values) for key, values in aggregate.items()})
    return payload


def feature_problem_and_risk_payloads(args: argparse.Namespace, captures: list[RepositoryCapture], facts: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    files = list(facts.get("modified_files", []))
    modules = list(facts.get("modules", []))
    symbols = list(facts.get("symbols", []))
    joined = "\n".join(
        [
            args.summary,
            args.feature,
            args.problem_summary,
            args.solution_summary,
            " ".join(files),
            " ".join(modules),
        ]
    ).lower()
    flags = semantic_flags(joined, modules)
    keywords = sorted(
        {
            *modules,
            *[Path(path).stem for path in files],
            *semantic_keywords(flags),
            *[item for item in ["focus", "launcher", "power", "policy", "package", "input"] if item in joined],
        }
    )
    basis = [f"功能涉及源码仓库: {capture.repo_path}" for capture in captures]
    basis.extend(f"补丁修改文件: {path}" for path in files)
    basis.extend(f"根据路径归属到模块: {module}" for module in modules)
    basis.extend(f"根据 diff hunk 识别符号: {symbol}" for symbol in symbols)
    if args.summary:
        basis.append("提交时提供了功能摘要")

    if args.problem_summary and args.solution_summary:
        problem_summary = args.problem_summary
        solution_summary = args.solution_summary
        confidence = "medium"
        basis.append("提交时显式提供了问题说明和方案说明")
    else:
        problem_summary, solution_summary, confidence = semantic_problem_solution(modules, flags)
    risks = semantic_risk_areas(modules, flags)
    limits = [
        "补丁内容不能单独证明原始需求文字",
        "补丁内容不能单独证明设备验证结果",
        "补丁内容不能单独证明发布状态",
    ]
    patch_paths = [capture.patch_rel for capture in captures]
    return (
        {
            "kind": "patch_problem_summary",
            "scope": "feature",
            "patches": patch_paths,
            "confidence": confidence,
            "problem_summary": problem_summary,
            "solution_summary": solution_summary,
            "keywords": keywords,
            "basis": basis,
            "limits": limits,
        },
        {
            "kind": "risk_surface",
            "scope": "feature",
            "patches": patch_paths,
            "confidence": confidence,
            "risk_areas": risks,
            "basis": basis,
            "limits": limits,
        },
    )


def coding_standard_check(args: argparse.Namespace, captures: list[RepositoryCapture]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    repositories: list[dict[str, Any]] = []
    for capture in captures:
        facts = capture.facts
        repo_errors: list[str] = []
        repo_warnings: list[str] = []
        direct_logs = direct_log_call_lines(capture.diff_text)
        hardcoded_logs = framework_log_literal_lines(capture.diff_text)
        direct_debug_props = direct_debug_property_lines(capture)
        non_framework_debug_props = [
            prop
            for prop in facts.get("system_properties", [])
            if prop.startswith("debug.") or (prop.startswith("persist.") and ".debug" in prop and not prop.startswith("persist.sys.framework.debug"))
        ]
        require_pairs = (
            args.workflow_contract == "current_codex_skill"
            and args.implementation_origin == "codex"
        )
        expected_alias = (
            args.policy_member_alias
            if args.workflow_contract == "current_codex_skill"
            and args.implementation_origin == "codex"
            else None
        )
        file_marker_analyses = analyze_unified_diff_markers(
            capture.diff_text,
            expected_alias=expected_alias,
            require_pairs=require_pairs,
        )
        marker_files: list[dict[str, Any]] = []
        marker_aliases: set[str] = set()
        marker_dates: set[str] = set()
        marker_count = 0
        marker_pair_count = 0
        legacy_marker_count = 0
        marker_errors: list[str] = []
        marker_exception = False
        for file_analysis in file_marker_analyses:
            if file_analysis.analysis is None:
                marker_files.append(
                    {
                        "path": file_analysis.path,
                        "comment_adapter": file_analysis.comment_adapter,
                        "result": "NOT_APPLICABLE",
                        "marker_count": 0,
                        "pair_count": 0,
                        "legacy_marker_count": 0,
                        "aliases": [],
                        "dates": [],
                        "errors": [],
                    }
                )
                continue
            analysis = file_analysis.analysis
            file_errors = list(analysis.errors)
            file_exception = False
            if args.allow_missing_author_date and file_errors == ["patch has no author/date marker"]:
                file_errors.clear()
                marker_exception = True
                file_exception = True
                repo_warnings.append(
                    f"{file_analysis.path}: manual/historical local draft has no author/date marker"
                )
            marker_errors.extend(
                f"{file_analysis.path}: {error}" for error in file_errors
            )
            marker_aliases.update(analysis.aliases)
            marker_dates.update(analysis.dates)
            marker_count += len(analysis.markers)
            marker_pair_count += sum(marker.kind == "open" for marker in analysis.markers)
            legacy_marker_count += sum(marker.kind == "legacy" for marker in analysis.markers)
            marker_files.append(
                {
                    "path": file_analysis.path,
                    "comment_adapter": file_analysis.comment_adapter,
                    "result": "FAIL" if file_errors else ("WARN" if file_exception else "PASS"),
                    "marker_count": len(analysis.markers),
                    "pair_count": sum(marker.kind == "open" for marker in analysis.markers),
                    "legacy_marker_count": sum(
                        marker.kind == "legacy" for marker in analysis.markers
                    ),
                    "aliases": list(analysis.aliases),
                    "dates": list(analysis.dates),
                    "errors": file_errors,
                }
            )
        if (
            args.workflow_contract == "current_codex_skill"
            and args.implementation_origin == "mixed"
            and not any(
                file_analysis.analysis is not None
                and any(
                    marker.kind == "open" and marker.alias == args.policy_member_alias
                    for marker in file_analysis.analysis.markers
                )
                for file_analysis in file_marker_analyses
            )
        ):
            marker_errors.append(
                "mixed Codex change has no paired marker for current member_alias "
                f"{args.policy_member_alias!r}"
            )
        repo_errors.extend(f"author/date marker: {error}" for error in marker_errors)
        framework_profile = args.change_domain == "framework"
        if framework_profile and direct_logs and not args.allow_banned_logs:
            repo_errors.append("新增代码包含直接 Log/Slog 调用，应改用 FrameworkLog")
        if framework_profile and hardcoded_logs:
            repo_warnings.append("FrameworkLog 调用疑似包含硬编码字符串，应优先使用字符串资源")
        if framework_profile and direct_debug_props:
            repo_warnings.append("模块代码疑似直接读取 persist.sys.framework.debug.*，应通过 FrameworkLog 统一访问")
        if framework_profile and non_framework_debug_props:
            repo_warnings.append("检测到非 FrameworkLog 规范调试属性: " + ", ".join(non_framework_debug_props))

        repositories.append(
            {
                "repo_path": capture.repo_path,
                "patch": capture.patch_rel,
                "author_date_marker_present": marker_count > 0,
                "marker_contract": (
                    "paired-current"
                    if require_pairs
                    else (
                        "mixed-current-pair-plus-legacy"
                        if args.workflow_contract == "current_codex_skill"
                        and args.implementation_origin == "mixed"
                        else "legacy-compatible"
                    )
                ),
                "marker_count": marker_count,
                "marker_pair_count": marker_pair_count,
                "legacy_marker_count": legacy_marker_count,
                "marker_aliases": sorted(marker_aliases),
                "marker_dates": sorted(marker_dates),
                "marker_errors": marker_errors,
                "marker_exception": (
                    "missing_marker_import_draft" if marker_exception else None
                ),
                "marker_files": marker_files,
                "direct_log_lines": direct_logs[:20],
                "framework_log_literal_lines": hardcoded_logs[:20],
                "direct_debug_property_lines": direct_debug_props[:20],
                "system_properties": facts.get("system_properties", []),
                "framework_log_keys": facts.get("framework_log_keys", []),
                "resource_keys": facts.get("resource_keys", []),
                "errors": repo_errors,
                "warnings": repo_warnings,
            }
        )
        errors.extend(f"{capture.repo_path}: {item}" for item in repo_errors)
        warnings.extend(f"{capture.repo_path}: {item}" for item in repo_warnings)

    return {
        "kind": "coding_standard_check",
        "policy_id": POLICY_ID,
        "policy_version": POLICY_VERSION,
        "policy_schema": "android-change-policy-v1",
        "policy_profile": args.change_domain,
        "applied_policy_profiles": [
            "universal_patch_archive",
            *(["framework"] if args.change_domain == "framework" else []),
        ],
        "change_domain": args.change_domain,
        "member_profile": args.policy_profile_name or None,
        "expected_member_alias": args.policy_member_alias or None,
        "identity_source": "current_member_profile" if args.policy_member_alias else None,
        "rewrite_authorship": False,
        "result": "FAIL" if errors else ("WARN" if warnings else "PASS"),
        "implementation_origin": args.implementation_origin,
        "workflow_contract": args.workflow_contract,
        "captured_by": "codex",
        "review_required": implementation_review_required(args.implementation_origin),
        "review_mode": implementation_review_mode(args.implementation_origin),
        "standard_sources": [
            "android-change-policy/v1",
            *(
                [
                    "Android Framework 补丁开发规范 v2.1 2025-10-16",
                    "Android Framework 日志管理规范 v1.0 2025-10-16",
                ]
                if args.change_domain == "framework"
                else []
            ),
        ],
        "rules": [
            "新 Codex 变更使用来自当前成员 profile 的成对作者日期标记",
            *(
                [
                    "直接 Log/Slog 调用禁止进入 Framework 补丁，应使用 FrameworkLog",
                    "persist.sys.framework.debug.* 调试属性集中在 FrameworkLog.java",
                    "Framework 日志和用户可见字符串应使用资源",
                ]
                if args.change_domain == "framework"
                else []
            ),
        ],
        "repositories": repositories,
        "errors": errors,
        "warnings": warnings,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Package one Android feature into README, patches, and evidence assets.")
    parser.add_argument(
        "--source-root",
        action="append",
        default=[],
        help="Legacy local Git root for manual/historical import only. current_codex_skill rejects it.",
    )
    parser.add_argument(
        "--profile",
        default="",
        help="Member profile used to resolve the policy member_alias. No free-form alias override is accepted.",
    )
    parser.add_argument(
        "--component-layer",
        choices=COMPONENT_LAYERS,
        default="",
        help="Layer shared by all captured repositories unless --repo-layer overrides it.",
    )
    parser.add_argument(
        "--repo-layer",
        action="append",
        default=[],
        metavar="REPO_PATH=LAYER",
        help="Assign one captured repository to a canonical Android layer. Repeatable.",
    )
    parser.add_argument(
        "--change-domain",
        choices=tuple(LEGACY_DOMAIN_LAYERS) + ("vendor",),
        default="",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--remote-snapshot",
        help="Immutable snapshot transferred by capture_remote_snapshot.py (current_codex_skill only).",
    )
    parser.add_argument("--snapshot-workspace-id", default="")
    parser.add_argument("--snapshot-command-id", default="")
    parser.add_argument("--snapshot-sha256", default="")
    parser.add_argument("--snapshot-max-age-seconds", type=int, default=900)
    parser.add_argument(
        "--patch-artifact",
        action="append",
        default=[],
        help="Explicit immutable Git binary patch for manual/historical import. Repeatable.",
    )
    parser.add_argument(
        "--patch-repo-path",
        action="append",
        default=[],
        help="Repository path corresponding to --patch-artifact. Repeatable.",
    )
    parser.add_argument(
        "--out-dir",
        help="Output root. current_codex_skill outputs must stay below $CODEX_HOME/artifacts.",
    )
    parser.add_argument("--run-id", help="Output package id. Default: YYYYMMDD-HHMMSS-feature")
    parser.add_argument("--platform", required=True, help="Platform plus Android version token, for example rk14, mtk14, unisoc13.")
    parser.add_argument("--module", help="Patch module name. Default inferred from changed files.")
    parser.add_argument("--feature", required=True, help="Feature slug for filename, for example allow-powerkey-to-user.")
    parser.add_argument("--summary", required=True, help="Human-readable requirement or patch summary.")
    parser.add_argument(
        "--problem-summary",
        default="",
        help="Requirement-level problem statement derived from the actual request and patch evidence. Must be paired with --solution-summary.",
    )
    parser.add_argument(
        "--solution-summary",
        default="",
        help="Implementation-level solution statement derived from the actual change and verification evidence. Must be paired with --problem-summary.",
    )
    parser.add_argument(
        "--implementation-origin",
        choices=IMPLEMENTATION_ORIGINS,
        default="codex",
        help="Who implemented the code before packaging. Use manual/external/historical/mixed/unknown for non-Codex-authored code.",
    )
    parser.add_argument(
        "--workflow-contract",
        choices=("current_codex_skill", "manual_import", "historical_import"),
        default="current_codex_skill",
        help=(
            "How this patch entered AKBS. This is independent from who wrote "
            "the code; current_codex_skill requires truthful pre-change search."
        ),
    )
    parser.add_argument("--project", default="unknown", help="Project name for manifest/readme.")
    parser.add_argument("--status", choices=["draft", "candidate", "validated", "failed", "blocked"], default="draft")
    parser.add_argument("--verification", action="append", default=[], help="Build verification fact. Repeatable.")
    parser.add_argument("--verification-result", choices=["PASS", "FAIL", "WARN", "INFO", "SKIPPED"], help="Overall verification result. Default: PASS when evidence is present, otherwise INFO.")
    parser.add_argument("--verification-method", choices=["device", "equivalent", "not_provided"], help="Verification method. Default inferred from verification arguments.")
    parser.add_argument("--device", default="", help="Device or board used for device verification.")
    parser.add_argument("--device-verification", action="append", default=[], help="Device verification fact. Repeatable.")
    parser.add_argument("--health-check", action="append", default=[], help="Health check evidence, such as boot, logcat, or dumpsys checks. Repeatable.")
    parser.add_argument("--artifact", action="append", default=[], help="Build or verification artifact path/reference. Repeatable.")
    parser.add_argument("--equivalent-type", default="", help="Equivalent verification type, for example artifact_static_check.")
    parser.add_argument("--equivalent-reason", default="", help="Why equivalent verification is acceptable.")
    parser.add_argument("--equivalent-coverage", action="append", default=[], help="Equivalent verification coverage item. Repeatable.")
    parser.add_argument("--remaining-risk", default="", help="Remaining risk after equivalent verification.")
    parser.add_argument("--search-query", action="append", default=[], help="Knowledge-base query performed before development. Repeatable.")
    parser.add_argument("--search-result", action="append", default=[], help="Search result or reuse decision from the pre-change search. Repeatable.")
    parser.add_argument("--search-summary", default="", help="Short summary of pre-change knowledge search.")
    parser.add_argument("--reuse-decision", choices=REUSE_DECISIONS, help="Pre-change knowledge use decision: reuse, adapt, reference_only, not_applicable, not_found, or unknown.")
    parser.add_argument("--reuse-target", action="append", default=[], help="Matched case, variant, patch, or evidence id considered before the change. Repeatable.")
    parser.add_argument("--reuse-match", action="append", default=[], help="Why the matched knowledge may apply. Repeatable.")
    parser.add_argument("--reuse-mismatch", action="append", default=[], help="Why the matched knowledge may not directly apply. Repeatable.")
    parser.add_argument("--reuse-reason", default="", help="Reason for the pre-change reuse/adapt/reference/not-applicable decision.")
    parser.add_argument("--reuse-outcome", choices=REUSE_OUTCOMES, help="Outcome after implementing and verifying with the pre-change decision.")
    parser.add_argument("--related-report-run-id", action="append", default=[], help="daily/weekly incoming run_id related to this patch package. Repeatable.")
    parser.add_argument("--evidence-dir", action="append", default=[], help="Directory containing structured evidence JSON files such as build-result.json. Repeatable.")
    parser.add_argument("--build-result", action="append", default=[], help="Structured build-result.json to include as build_result evidence. Repeatable.")
    parser.add_argument("--remote-build-host", default="", help="SSH host or alias of the remote build server used for source edits/build.")
    parser.add_argument("--remote-source-root", default="", help="Remote Android source root used for the build.")
    parser.add_argument("--remote-build-command", default="", help="Remote build command or wrapper invocation.")
    parser.add_argument("--remote-build-profile", default="", help="Remote build profile, module group, or wrapper profile.")
    parser.add_argument("--remote-artifact", action="append", default=[], help="Remote build artifact path. Repeatable.")
    parser.add_argument("--artifact-sha1", action="append", default=[], help="SHA1 for the corresponding --remote-artifact. Repeatable.")
    parser.add_argument("--artifact-transfer", default="", help="How the artifact moved from remote build server to member local machine.")
    parser.add_argument("--local-artifact", action="append", default=[], help="Local artifact path used for device delivery. Repeatable.")
    parser.add_argument("--adb-serial", default="", help="Local adb device serial used for verification.")
    parser.add_argument("--adb-action", action="append", default=[], help="Local adb push/install/sync action. Repeatable.")
    parser.add_argument("--device-restart", action="append", default=[], help="Device restart, remount, process restart, or reload action after delivery. Repeatable.")
    parser.add_argument("--risk", default="", help="Risk note for readme.")
    parser.add_argument("--rollback", default="", help="Rollback note for readme.")
    parser.add_argument(
        "--allow-missing-author-date",
        action="store_true",
        help="Legacy manual/historical local draft only: record a missing marker without blocking capture.",
    )
    parser.add_argument("--allow-banned-logs", action="store_true", help="Allow package even when added lines contain direct Log/Slog calls.")
    args = parser.parse_args()
    if args.change_domain:
        legacy_layer = LEGACY_DOMAIN_LAYERS.get(args.change_domain)
        if legacy_layer is None:
            parser.error("vendor 不是 layer；请使用 --component-layer 声明实际源码层")
        if args.component_layer and args.component_layer != legacy_layer:
            parser.error("--change-domain 与 --component-layer 分类冲突")
        args.component_layer = args.component_layer or legacy_layer
    args.repository_layers = parse_repository_layers(args.repo_layer)
    if not args.component_layer and not args.repository_layers:
        parser.error("必须使用 --component-layer；跨层功能可再用 --repo-layer 逐仓库覆盖")
    args.change_domain = (
        "framework" if args.component_layer == "platform" else args.component_layer
    )
    args.problem_summary = args.problem_summary.strip()
    args.solution_summary = args.solution_summary.strip()
    if bool(args.problem_summary) != bool(args.solution_summary):
        parser.error("--problem-summary 和 --solution-summary 必须同时提供")
    if args.snapshot_max_age_seconds <= 0 or args.snapshot_max_age_seconds > 86400:
        parser.error("--snapshot-max-age-seconds 必须在 1..86400 范围")
    if args.allow_missing_author_date and (
        args.status != "draft" or args.workflow_contract == "current_codex_skill"
    ):
        parser.error(
            "--allow-missing-author-date 只能用于 manual/historical import 的本地 draft"
        )
    snapshot_fields = (
        args.remote_snapshot,
        args.snapshot_workspace_id,
        args.snapshot_command_id,
        args.snapshot_sha256,
        args.remote_source_root,
    )
    if args.workflow_contract == "current_codex_skill":
        if args.source_root:
            parser.error(
                "current_codex_skill 禁止 --source-root；mounted Android source 不是 Codex 取证源，"
                "请先通过 capture_remote_snapshot.py + android-remote-channel v2 生成 snapshot"
            )
        if args.patch_artifact or args.patch_repo_path:
            parser.error("current_codex_skill 不接受 caller patch artifact；必须消费 channel snapshot")
        if not all(snapshot_fields):
            parser.error(
                "current_codex_skill 必须提供 --remote-snapshot、snapshot workspace/command/sha256 "
                "和 --remote-source-root"
            )
    else:
        if args.remote_snapshot or any(snapshot_fields[1:4]):
            parser.error("manual/historical import 应使用显式 --patch-artifact，不接受 current snapshot 参数")
        if args.source_root and args.patch_artifact:
            parser.error("--source-root 与 --patch-artifact 不能混用")
        if args.patch_artifact and not args.patch_repo_path:
            parser.error("--patch-artifact 必须配套 --patch-repo-path")
    args.policy_profile_name = ""
    args.policy_member_alias = ""
    if args.workflow_contract == "current_codex_skill" or args.profile:
        try:
            member_profile = load_member_profile(args.profile or None)
        except MemberProfileError as exc:
            parser.error(str(exc))
        args.policy_profile_name = member_profile.profile
        args.policy_member_alias = member_profile.member_alias
    return args


def codex_artifacts_root() -> Path:
    codex_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()
    return (codex_home / "artifacts").resolve()


def require_current_package_root(path: Path) -> Path:
    artifacts = codex_artifacts_root()
    resolved = path.expanduser().resolve()
    try:
        resolved.relative_to(artifacts)
    except ValueError as exc:
        raise SystemExit(
            f"current_codex_skill 补丁包必须写入 $CODEX_HOME/artifacts: {resolved}"
        ) from exc
    return resolved


def main() -> int:
    args = parse_args()
    platform_token, platform_name, android_version = parse_platform_arg(args.platform)
    if not platform_token:
        raise SystemExit("--platform 必须使用受控平台和 Android 版本，例如 rk14、mtk14、unisoc13；不能使用泛化或非规范令牌。")

    platform = platform_token
    feature = slug(args.feature)
    args.feature = feature
    scope_errors = validate_feature_scope(args)
    if scope_errors:
        for error in scope_errors:
            print(error, file=sys.stderr)
        return 1
    snapshot_payload: dict[str, Any] | None = None
    if args.workflow_contract == "current_codex_skill":
        captures, snapshot_payload = collect_snapshot_captures(args, platform, feature)
    elif args.patch_artifact:
        captures = collect_patch_artifact_captures(args, platform, feature)
    else:
        captures = collect_repository_captures(args, platform, feature)
    patch_layers = capture_layers(
        captures,
        default_layer=args.component_layer,
        repository_layers=args.repository_layers,
    )
    args.change_domain = (
        "framework" if "platform" in patch_layers.values() else args.component_layer
    )
    resolved_project, project_inference = infer_capture_project_for_feature(args, captures, trusted_platform=platform_name)
    args.project = resolved_project

    now = dt.datetime.now()
    run_id = args.run_id or f"{now:%Y%m%d-%H%M%S}-feature"
    out_root = (
        Path(args.out_dir).expanduser()
        if args.out_dir
        else codex_artifacts_root() / "android-patch-capture" / "packages"
    )
    if not out_root.is_absolute():
        out_root = Path.cwd() / out_root
    if args.workflow_contract == "current_codex_skill":
        out_root = require_current_package_root(out_root)
    package_dir = require_safe_artifact_path(out_root / run_id, purpose="patch package output")
    if package_dir.exists():
        raise SystemExit(f"输出目录已存在: {package_dir}")

    patch_dir = package_dir / "patches"
    evidence_dir = package_dir / "evidence"
    patch_dir.mkdir(parents=True)
    evidence_dir.mkdir(parents=True)

    for capture in captures:
        (patch_dir / capture.patch_name).write_text(capture.diff_text, encoding="utf-8")

    errors: list[str] = []
    warnings: list[str] = []
    auto_verification_payload = load_auto_verification_payload(args)
    verification_payload = verification_result(args, auto_verification_payload)
    search_payload = search_before_change(args)
    feature_facts = aggregate_feature_facts(captures)
    problem_payload, risk_payload = feature_problem_and_risk_payloads(args, captures, feature_facts)
    coding_check = coding_standard_check(args, captures)
    errors.extend(validate_feature_scope(args))
    errors.extend(
        template_leak_errors(
            summary=args.summary,
            problem=problem_payload.get("problem_summary"),
            solution=problem_payload.get("solution_summary"),
            patch_paths=[capture.patch_rel for capture in captures],
            modified_files=[file for capture in captures for file in prefixed_files(capture.repo_path, capture.facts.get("modified_files", []))],
        )
    )
    errors.extend(validate_patch_asset_names(captures))
    errors.extend(coding_check["errors"])
    warnings.extend(coding_check["warnings"])
    errors.extend(validate_verification_for_status(args, verification_payload))
    search_errors, search_warnings = validate_search_decision_for_status(args, search_payload)
    errors.extend(search_errors)
    warnings.extend(search_warnings)

    package_check = {"status": "FAIL" if errors else "PASS", "errors": errors, "warnings": warnings}
    readme_path = package_dir / "README.md"
    readme_path.write_text(feature_readme_text(args, captures, feature_facts, package_check, coding_check, verification_payload), encoding="utf-8")
    evidence_items = [
        {
            "id": "changed-files",
            "kind": "changed_files",
            "path": "evidence/changed-files.json",
            "result": "INFO",
            "scope": "feature",
            "summary": "changed files and extracted feature facts",
        },
        {
            "id": "verification-result",
            "kind": "verification_result",
            "path": "evidence/verification-result.json",
            "result": verification_payload["result"],
            "scope": "feature",
            "summary": f"{verification_payload['method']} verification evidence",
        },
        {
            "id": "patch-diff-facts",
            "kind": "patch_diff_facts",
            "path": "evidence/patch-diff-facts.json",
            "result": "INFO",
            "scope": "feature",
            "summary": "功能补丁 diff 中解析出的客观事实",
        },
        {
            "id": "patch-problem-summary",
            "kind": "patch_problem_summary",
            "path": "evidence/patch-problem-summary.json",
            "result": "INFO",
            "scope": "feature",
            "summary": "功能对应的问题与方案说明",
        },
        {
            "id": "risk-surface",
            "kind": "risk_surface",
            "path": "evidence/risk-surface.json",
            "result": "INFO",
            "scope": "feature",
            "summary": "功能风险面说明",
        },
        {
            "id": "coding-standard-check",
            "kind": "coding_standard_check",
            "path": "evidence/coding-standard-check.json",
            "result": coding_check["result"],
            "scope": "feature",
            "summary": "团队补丁开发与日志规范检查",
        },
        {
            "id": "search-before-change",
            "kind": "search_before_change",
            "path": "evidence/search-before-change.json",
            "result": "INFO",
            "scope": "feature",
            "summary": "knowledge search performed before development",
        },
        {
            "id": "package-check",
            "kind": "package_check",
            "path": "evidence/package-check.json",
            "result": package_check["status"],
            "scope": "feature",
            "summary": "local patch package checks",
        },
    ]
    evidence_items.extend(collect_external_evidence(args, evidence_dir))
    if snapshot_payload is not None:
        write_json(evidence_dir / "remote-source-snapshot.json", snapshot_payload)
        evidence_items.append(
            {
                "id": "remote-source-snapshot",
                "kind": "remote_source_snapshot",
                "path": "evidence/remote-source-snapshot.json",
                "result": "INFO",
                "scope": "feature",
                "summary": "immutable source facts generated through android-remote-channel v2",
            }
        )

    patch_items = [
        {
            "id": Path(capture.patch_name).stem,
            "path": capture.patch_rel,
            "repo_path": capture.repo_path,
            "source_root": str(capture.source_root),
            "content_sha1": capture.facts["content_sha1"],
            "status": args.status,
            "reuse_hint": args.status == "validated",
            "project": args.project,
            "platform_token": platform_token,
            "platform": platform_name,
            "android_version": android_version,
            "implementation_origin": args.implementation_origin,
            "layer": patch_layers[capture.patch_rel],
            "workflow_contract": args.workflow_contract,
            "captured_by": "codex",
            "facts": capture.facts,
        }
        for capture in captures
    ]
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "package_type": "android_feature_patch",
        "components": component_rows(patch_layers),
        "feature": feature,
        "readme": "README.md",
        "project": args.project,
        "platform_token": platform_token,
        "platform": platform_name,
        "android_version": android_version,
        "summary": args.summary,
        "status": args.status,
        "implementation_origin": args.implementation_origin,
        "workflow_contract": args.workflow_contract,
        "captured_by": "codex",
        "coding_standard_check": {
            "required": implementation_review_required(args.implementation_origin),
            "mode": implementation_review_mode(args.implementation_origin),
            "path": "evidence/coding-standard-check.json",
            "result": coding_check["result"],
        },
        "created_at": now.isoformat(timespec="seconds"),
        "related_report_run_ids": args.related_report_run_id or [],
        "source_roots": [str(capture.source_root) for capture in captures],
        "git_repositories": [
            {
                "repo_path": capture.repo_path,
                "root": str(capture.source_root),
                "git": capture.git_info,
            }
            for capture in captures
        ],
        "project_inference": project_inference,
        "verification_chain": {
            "remote_build": bool(
                verification_payload.get("remote_build", {}).get("host")
                or verification_payload.get("remote_build", {}).get("source_root")
                or verification_payload.get("remote_build", {}).get("command")
                or verification_payload.get("remote_build", {}).get("artifacts")
            ),
            "local_delivery": bool(
                verification_payload.get("local_delivery", {}).get("transfer")
                or verification_payload.get("local_delivery", {}).get("local_artifacts")
                or verification_payload.get("local_delivery", {}).get("adb_serial")
                or verification_payload.get("local_delivery", {}).get("adb_actions")
            ),
            "device_verification": bool(verification_payload.get("device") or verification_payload.get("steps")),
        },
        "patches": patch_items,
        "evidence": evidence_items,
    }
    if snapshot_payload is not None:
        manifest["source_snapshot"] = {
            "path": "evidence/remote-source-snapshot.json",
            "schema": snapshot_payload["schema"],
            "workspace_id": snapshot_payload["workspace_id"],
            "command_id": snapshot_payload["command_id"],
            "remote_root": snapshot_payload["remote_root"],
            "sha256": snapshot_payload["snapshot_sha256"],
        }
    write_json(package_dir / "manifest.json", manifest)
    write_json(
        evidence_dir / "changed-files.json",
        {
            "kind": "changed_files",
            "scope": "feature",
            "repositories": [
                {
                    "repo_path": capture.repo_path,
                    "root": str(capture.source_root),
                    "modified_files": prefixed_files(capture.repo_path, capture.facts["modified_files"]),
                }
                for capture in captures
            ],
            "modified_files": feature_facts["modified_files"],
        },
    )
    write_json(evidence_dir / "patch-diff-facts.json", feature_facts)
    write_json(evidence_dir / "patch-problem-summary.json", problem_payload)
    write_json(evidence_dir / "risk-surface.json", risk_payload)
    write_json(evidence_dir / "coding-standard-check.json", coding_check)
    write_json(evidence_dir / "verification-result.json", verification_payload)
    write_json(evidence_dir / "search-before-change.json", search_payload)
    write_json(evidence_dir / "package-check.json", package_check)

    result = {
        "package": str(package_dir),
        "patches": [str(package_dir / capture.patch_rel) for capture in captures],
        "readme": str(readme_path),
        "implementation_origin": args.implementation_origin,
        "workflow_contract": args.workflow_contract,
        "local_check": package_check,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
