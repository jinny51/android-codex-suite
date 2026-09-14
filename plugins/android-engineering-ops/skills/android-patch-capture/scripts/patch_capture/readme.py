from __future__ import annotations

from typing import Any

from patch_capture.git_diff import prefixed_files, unique_preserve


def bullet_list(items: list[str]) -> str:
    if not items:
        return "无"
    return "\n".join(f"- `{item}`" for item in items)


def plain_bullets(items: list[str]) -> str:
    if not items:
        return "待补充"
    return "\n".join(f"- {item}" for item in items)


def string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    if isinstance(value, str) and value:
        return [value]
    return []


def infer_reuse_decision(queries: list[str], results: list[str], summary: str) -> str:
    text = "\n".join([*queries, *results, summary]).lower()
    if not text.strip():
        return "unknown"
    if any(token in text for token in ("未发现", "未命中", "no reuse", "no candidate", "not found")):
        return "not_found"
    return "unknown"


def function_boundary_text(args: Any, captures: list[Any], facts: dict[str, Any]) -> str:
    modules = unique_preserve([module for capture in captures for module in capture.facts.get("modules", [])])
    anchors = unique_preserve(
        [
            *facts.get("system_properties", []),
            *facts.get("settings_keys", []),
            *facts.get("resource_keys", []),
            *facts.get("framework_log_keys", []),
        ]
    )
    relation_lines = []
    for capture in captures:
        files = prefixed_files(capture.repo_path, capture.facts.get("modified_files", []))
        modules_text = ", ".join(capture.facts.get("modules", [])) or "unknown"
        files_text = "；".join(files[:5]) if files else "未识别具体文件"
        relation_lines.append(
            f"- `{capture.patch_rel}`：属于 `{capture.repo_path}`，模块 {modules_text}；"
            f"本子改动通过 {files_text} 服务同一变更目标。"
        )
    if not relation_lines:
        relation_lines.append("- 未识别补丁文件；请重新从干净工作树采集。")
    anchor_text = ", ".join(anchors[:12]) if anchors else "未提取到关键锚点"
    module_text = ", ".join(modules) if modules else "unknown"
    return (
        f"- 变更目标: {args.summary}\n"
        f"- 模块范围: {module_text}\n"
        f"- 关键锚点: {anchor_text}\n"
        "- 关系说明: 以下子改动必须共同服务上述变更目标；如果某一项可独立删除且不影响该目标，"
        "应停止上传并拆成独立补丁包。\n"
        + "\n".join(relation_lines)
    )


def change_readme_text(
    args: Any,
    captures: list[Any],
    facts: dict[str, Any],
    package_check: dict[str, Any],
    coding_check: dict[str, Any],
    verification_payload: dict[str, Any],
) -> str:
    remote_build = verification_payload.get("remote_build") if isinstance(verification_payload.get("remote_build"), dict) else {}
    local_delivery = verification_payload.get("local_delivery") if isinstance(verification_payload.get("local_delivery"), dict) else {}
    verification = args.verification or string_list(verification_payload.get("build"))
    device_verification = args.device_verification or string_list(verification_payload.get("steps"))
    remote_artifacts = args.remote_artifact or [
        " ".join(
            item
            for item in (
                str(artifact.get("path") or "").strip(),
                f"sha1={artifact.get('sha1')}" if artifact.get("sha1") else "",
            )
            if item
        )
        for artifact in remote_build.get("artifacts", [])
        if isinstance(artifact, dict)
    ]
    local_artifacts = args.local_artifact or string_list(local_delivery.get("local_artifacts"))
    adb_actions = args.adb_action or string_list(local_delivery.get("adb_actions"))
    device_restarts = args.device_restart or string_list(local_delivery.get("device_restarts"))
    risk = args.risk or "待结合当前项目、触发路径和验证结果补充。"
    rollback = args.rollback or "在对应源码仓库逐个执行 `git apply -R <patch>`，或回退对应源码仓库提交。"
    repo_lines = "\n".join(f"- `{capture.repo_path}` -> `{capture.patch_rel}`" for capture in captures)
    modification_sections = "\n\n".join(
        f"### {capture.repo_path}\n\n{bullet_list(prefixed_files(capture.repo_path, capture.facts['modified_files']))}" for capture in captures
    )
    modules = unique_preserve([module for capture in captures for module in capture.facts.get("modules", [])])
    direct_log_lines = [line for repo in coding_check["repositories"] for line in repo.get("direct_log_lines", [])]
    if direct_log_lines:
        log_control = "检测到直接 Log/Slog 新增，提交前必须改为 FrameworkLog：\n" + plain_bullets(direct_log_lines)
    else:
        log_control = "未检测到直接 Log/Slog 新增；如本变更新增调试日志，应统一使用 FrameworkLog。"

    return f"""# {args.feature}

## 变更描述

{args.summary}

## 变更边界

{function_boundary_text(args, captures, facts)}

## 实现来源

- implementation_origin: {args.implementation_origin}
- captured_by: codex
- coding_standard_review: {coding_check["review_mode"]}

## 涉及源码仓库

{repo_lines}

## 修改点

{modification_sections}

## 影响范围

- 项目: {args.project}
- 模块: {", ".join(modules) if modules else "unknown"}
- 状态: {args.status}

## 关键符号

### SystemProperties

{bullet_list(facts["system_properties"])}

### Settings Key

{bullet_list(facts["settings_keys"])}

### FrameworkLog Key

{bullet_list(facts["framework_log_keys"])}

### 字符串资源

{bullet_list(facts["resource_keys"])}

## 日志控制

{log_control}

## SystemProperties

{bullet_list(facts["system_properties"])}

## 字符串国际化

{bullet_list(facts["resource_keys"])}

## 构建验证

{plain_bullets(verification)}

## 设备验证

{plain_bullets(device_verification)}

## 开发前知识库检索

### 检索词

{plain_bullets(args.search_query or [])}

### 检索结果

{plain_bullets(args.search_result or [])}

### 使用决策

- decision: {args.reuse_decision or infer_reuse_decision(args.search_query or [], args.search_result or [], args.search_summary or "")}
- targets: {", ".join(args.reuse_target or []) if args.reuse_target else "待补充"}
- match_points: {", ".join(args.reuse_match or []) if args.reuse_match else "待补充"}
- mismatch_points: {", ".join(args.reuse_mismatch or []) if args.reuse_mismatch else "待补充"}
- reason: {args.reuse_reason or "待补充"}
- outcome: {args.reuse_outcome or "not_started"}

## 远端构建链路

- remote_build_host: {args.remote_build_host or str(remote_build.get("host") or "") or "待补充"}
- remote_source_root: {args.remote_source_root or str(remote_build.get("source_root") or "") or "待补充"}
- remote_build_profile: {args.remote_build_profile or str(remote_build.get("profile") or "") or "待补充"}
- remote_build_command: {args.remote_build_command or str(remote_build.get("command") or "") or "待补充"}
- remote_artifacts: {", ".join(remote_artifacts) if remote_artifacts else "待补充"}

## 本机交付和设备验证链路

- artifact_transfer: {args.artifact_transfer or str(local_delivery.get("transfer") or "") or "待补充"}
- local_artifacts: {", ".join(local_artifacts) if local_artifacts else "待补充"}
- adb_serial: {args.adb_serial or str(local_delivery.get("adb_serial") or "") or "待补充"}
- adb_actions: {", ".join(adb_actions) if adb_actions else "待补充"}
- device_restarts: {", ".join(device_restarts) if device_restarts else "待补充"}

## 风险说明

{risk}

## 可回滚性

{rollback}

## 团队规范检查

- coding_standard_check: {coding_check["result"]}
- errors: {len(coding_check["errors"])}
- warnings: {len(coding_check["warnings"])}

## 打包检查

- package_check: {package_check["status"]}
"""
