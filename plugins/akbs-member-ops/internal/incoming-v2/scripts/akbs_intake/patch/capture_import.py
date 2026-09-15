from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from akbs_member_ops.knowledge_rules import aggregate_package_scope_errors
from akbs_member_ops.verification_evidence import has_authoritative_requirement_result

from akbs_intake.io_utils import (
    MATERIALS_DIR,
    list_string_values,
    materials_rel,
    read_json_file,
    safe_id,
    sha1_file,
    unique_strings,
)


COMPONENT_LAYERS = {
    "application",
    "platform",
    "native",
    "hal",
    "kernel",
    "device",
    "build",
}


def capture_patch_layers(manifest: dict[str, Any], patch_paths: list[str]) -> dict[str, str]:
    if manifest.get("package_type") == "framework_feature_patch":
        return {path: "platform" for path in patch_paths}
    if manifest.get("package_type") != "android_feature_patch":
        raise SystemExit("不是 android-patch-capture 工作包")
    components = manifest.get("components")
    if not isinstance(components, list) or not components:
        raise SystemExit("android-patch-capture 工作包缺少 components")
    assigned: dict[str, str] = {}
    for component in components:
        if not isinstance(component, dict) or set(component) != {"layer", "patches"}:
            raise SystemExit("capture components 项只能包含 layer 和 patches")
        layer = component.get("layer")
        paths = component.get("patches")
        if layer not in COMPONENT_LAYERS or not isinstance(paths, list) or not paths:
            raise SystemExit("capture components 必须声明有效 layer 和非空 patches")
        for path in paths:
            if not isinstance(path, str) or not path:
                raise SystemExit("capture components.patches 必须只包含非空字符串路径")
            relative = path
            if relative not in patch_paths or relative in assigned:
                raise SystemExit("capture components 必须对每个 patch 精确分类一次")
            assigned[relative] = layer
    if set(assigned) != set(patch_paths):
        raise SystemExit("capture components 必须完整覆盖 patches")
    return assigned


def copy_capture_file(source_root: Path, rel: str, target: Path) -> None:
    source = (source_root / rel).resolve()
    root = source_root.resolve()
    if source != root and root not in source.parents:
        raise SystemExit(f"capture package 引用路径越界: {rel}")
    if not source.is_file():
        raise SystemExit(f"capture package 引用文件不存在: {rel}")
    if target.exists():
        raise SystemExit(f"目标文件已存在，避免覆盖: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def verification_payload_passes(package_root: Path, evidence_item: dict[str, Any]) -> bool:
    if evidence_item.get("kind") not in {"verification_result", "device_verification", "equivalent_verification"}:
        return False
    if evidence_item.get("result") != "PASS":
        return False
    rel = evidence_item.get("path")
    if not isinstance(rel, str) or not rel:
        return False
    payload = read_json_file(package_root / rel)
    if not has_authoritative_requirement_result(payload, expected_result="PASS"):
        return False
    if payload.get("method") == "device":
        return True
    if payload.get("method") == "equivalent":
        return bool(payload.get("reason") and payload.get("coverage") and "remaining_risk" in payload)
    return False


def copy_patch_capture_packages(
    package_dir: Path,
    package_paths: list[str],
    default_project: str,
    default_status: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], bool, list[str], list[dict[str, Any]], str]:
    if len(package_paths) > 1:
        raise SystemExit("android_change incoming 一次只接受一个功能级 patch-capture 包；多个功能请分别提交。")
    patch_dir = package_dir / "patches"
    materials_dir = package_dir / MATERIALS_DIR
    evidence_dir = package_dir / MATERIALS_DIR / "evidence" / "capture"
    patch_dir.mkdir(parents=True, exist_ok=True)
    materials_dir.mkdir(parents=True, exist_ok=True)
    evidence_dir.mkdir(parents=True, exist_ok=True)

    patch_entries: list[dict[str, Any]] = []
    evidence_entries: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    has_pass_verification = False
    related_report_run_ids: list[str] = []
    source_contexts: list[dict[str, Any]] = []
    feature_readme_rel = ""

    for raw in package_paths:
        capture_dir = Path(raw).expanduser().resolve()
        manifest = read_json_file(capture_dir / "manifest.json")
        if manifest.get("package_type") not in {
            "android_feature_patch",
            "framework_feature_patch",
        }:
            raise SystemExit(f"不是功能级 android-patch-capture 工作包: {capture_dir}")
        readme_rel = str(manifest.get("readme") or "")
        if not readme_rel:
            raise SystemExit(f"capture package 缺少功能 readme: {capture_dir}")
        implementation_origin = str(manifest.get("implementation_origin") or "unknown")
        workflow_contract = str(manifest.get("workflow_contract") or "")
        captured_by = str(manifest.get("captured_by") or "codex")
        coding_standard_check = manifest.get("coding_standard_check") if isinstance(manifest.get("coding_standard_check"), dict) else {}
        feature_readme_rel = materials_rel("readme.md")
        copy_capture_file(capture_dir, readme_rel, package_dir / feature_readme_rel)
        related_report_run_ids.extend(list_string_values(manifest.get("related_report_run_ids")))
        repositories = manifest.get("git_repositories", [])
        if isinstance(repositories, list):
            for repository in repositories:
                if not isinstance(repository, dict):
                    continue
                git = repository.get("git") if isinstance(repository.get("git"), dict) else {}
                source_contexts.append(
                    {
                        "source_root": str(repository.get("root") or ""),
                        "repo_path": str(repository.get("repo_path") or ""),
                        "local_mount_path": str(repository.get("local_mount_path") or ""),
                        "remote_root": str(repository.get("remote_root") or ""),
                        "ssh_host": str(repository.get("ssh_host") or ""),
                        "sdk_name": str(repository.get("sdk_name") or ""),
                        "git_branch": str(git.get("branch") or ""),
                        "git_remote": str(git.get("remote") or ""),
                        "git_remotes": str(git.get("remotes") or ""),
                        "implementation_origin": implementation_origin,
                        "workflow_contract": workflow_contract,
                        "captured_by": captured_by,
                    }
                )
        capture_id = safe_id(capture_dir.name)
        patches = manifest.get("patches", [])
        if not isinstance(patches, list) or not patches:
            raise SystemExit(f"capture package 缺少 patches: {capture_dir}")
        patch_paths = [
            str(item.get("path") or "")
            for item in patches
            if isinstance(item, dict)
        ]
        if len(patch_paths) != len(patches) or not all(patch_paths):
            raise SystemExit(f"capture package patch 路径无效: {capture_dir}")
        patch_layers = capture_patch_layers(manifest, patch_paths)
        evidence = manifest.get("evidence", [])
        if not isinstance(evidence, list):
            evidence = []

        for index, item in enumerate(patches, start=1):
            if not isinstance(item, dict):
                raise SystemExit(f"capture package patches[{index}] 不是对象: {capture_dir}")
            patch_rel = str(item.get("path", ""))
            patch_name = Path(patch_rel).name
            if not patch_name:
                raise SystemExit(f"capture package patch 路径无效: {capture_dir}")
            copy_capture_file(capture_dir, patch_rel, patch_dir / patch_name)
            entry_status = item.get("status") or default_status
            copied_patch = patch_dir / patch_name
            patch_entries.append(
                {
                    "path": f"patches/{patch_name}",
                    "repo_path": str(item.get("repo_path") or ""),
                    "source_root": str(item.get("source_root") or ""),
                    "content_sha1": item.get("content_sha1") or sha1_file(copied_patch),
                    "status": entry_status,
                    "reuse_hint": bool(item.get("reuse_hint", entry_status == "validated")),
                    "project": item.get("project") or manifest.get("project") or default_project,
                    "platform_token": str(item.get("platform_token") or manifest.get("platform_token") or ""),
                    "platform": str(item.get("platform") or manifest.get("platform") or ""),
                    "android_version": str(item.get("android_version") or manifest.get("android_version") or ""),
                    "implementation_origin": str(item.get("implementation_origin") or implementation_origin),
                    "workflow_contract": str(
                        item.get("workflow_contract") or workflow_contract
                    ),
                    "layer": patch_layers[patch_rel],
                    "captured_by": str(item.get("captured_by") or captured_by),
                    "coding_standard_check": coding_standard_check,
                    "note": "来自 android-patch-capture 工作包",
                    "facts": item.get("facts") if isinstance(item.get("facts"), dict) else {},
                }
            )
            sources.append(
                {
                    "name": patch_name,
                    "source": str(capture_dir / patch_rel),
                    "project": item.get("project") or default_project,
                    "implementation_origin": str(item.get("implementation_origin") or implementation_origin),
                    "workflow_contract": str(
                        item.get("workflow_contract") or workflow_contract
                    ),
                    "captured_by": str(item.get("captured_by") or captured_by),
                }
            )

        for item in evidence:
            if not isinstance(item, dict):
                continue
            rel = item.get("path")
            if not isinstance(rel, str) or not rel:
                continue
            base_id = safe_id(str(item.get("id") or Path(rel).stem))
            target_name = f"{capture_id}-{Path(rel).name}"
            target = evidence_dir / target_name
            copy_capture_file(capture_dir, rel, target)
            copied = {
                "id": f"{capture_id}-{base_id}",
                "kind": item.get("kind", "capture_evidence"),
                "path": materials_rel("evidence", "capture", target_name),
                "result": item.get("result", "INFO"),
                "summary": item.get("summary", "captured patch evidence"),
            }
            evidence_entries.append(copied)
            if verification_payload_passes(capture_dir, item):
                has_pass_verification = True

    return patch_entries, evidence_entries, sources, has_pass_verification, unique_strings(related_report_run_ids), source_contexts, feature_readme_rel


def patch_capture_package_scope_errors(package_paths: list[str] | None, summary: str, run_id: str) -> list[str]:
    texts = [str(summary or ""), str(run_id or "")]
    patch_count = 0
    for raw in package_paths or []:
        capture_dir = Path(raw).expanduser().resolve()
        manifest = read_json_file(capture_dir / "manifest.json")
        texts.append(str(manifest.get("summary") or ""))
        texts.append(str(manifest.get("feature") or ""))
        patches = manifest.get("patches")
        if isinstance(patches, list):
            patch_count = max(patch_count, len(patches))
        readme_rel = str(manifest.get("readme") or "")
        if readme_rel:
            readme_path = (capture_dir / readme_rel).resolve()
            root = capture_dir.resolve()
            try:
                readme_path.relative_to(root)
            except ValueError as exc:
                raise SystemExit(f"capture package readme 路径越界: {readme_rel}") from exc
            if readme_path.is_file():
                texts.append(readme_path.read_text(encoding="utf-8", errors="ignore"))
    return aggregate_package_scope_errors("\n".join(texts), patch_count)
