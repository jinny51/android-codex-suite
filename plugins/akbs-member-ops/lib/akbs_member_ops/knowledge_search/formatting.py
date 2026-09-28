from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def parse_json(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


def format_case_detail(payload: dict[str, Any]) -> str:
    lines = [
        f"# {payload['title']}",
        "",
        f"case_id={payload['case_id']}",
        "",
        "以下为既有知识详情；未绑定本次目标环境，不能替代搜索时的复用分级或工程验收。",
    ]
    for section in payload["sections"]:
        lines.extend(["", f"## {section['label']}", ""])
        if section["kind"] == "text":
            lines.append(section["value"])
        else:
            lines.extend(f"- {item}" for item in section["items"])
    for implementation in payload["implementations"]:
        lines.extend([
            "", f"## Implementation {implementation['implementation_id']}", "",
            implementation["implementation_summary"],
            "", f"语义审核状态：{implementation['review_state']}",
            "", implementation["approach"],
        ])
        for field, label in (
            ("key_decisions", "关键决策"), ("code_anchors", "代码锚点"),
            ("risk_and_rollback", "风险与回滚"), ("applicability", "声明的精确适用元组"),
            ("evidence_bindings", "原件证据绑定"), ("evidence_gaps", "证据缺口"),
        ):
            if field in implementation:
                lines.extend([
                    "", f"### {label}", "", "```json",
                    json.dumps(implementation[field], ensure_ascii=False, indent=2), "```",
                ])
    return "\n".join(lines)


def result_date(row: dict[str, Any]) -> str:
    return str(row.get("date") or row.get("week_range") or "")


def rel_or_empty(root: Path, value: Any) -> str:
    if not value:
        return ""
    text = str(value)
    if text.startswith("/") or re.match(r"^[A-Za-z]:[\\/]", text):
        return text
    return str(root / text)


def compact_list(value: Any, limit: int = 4) -> str:
    items = parse_json(value, value)
    if not isinstance(items, list):
        items = [items] if items else []
    text_items = [localized_analysis_text(str(item)) for item in items if str(item)]
    if len(text_items) > limit:
        return ", ".join(text_items[:limit]) + f" ... (+{len(text_items) - limit})"
    return ", ".join(text_items)


def format_knowledge_validity(row: dict[str, Any]) -> str:
    validity = row.get("knowledge_validity") if isinstance(row.get("knowledge_validity"), dict) else {}
    confidence = validity.get("confidence") or row.get("confidence") or row.get("case_confidence") or ""
    evidence_level = validity.get("evidence_level") or row.get("evidence_level") or ""
    risk_level = validity.get("risk_level") or row.get("risk_level") or ""
    reuse_score = validity.get("reuse_score", row.get("reuse_score", ""))
    if not any(str(value) for value in (confidence, evidence_level, risk_level, reuse_score)):
        return ""
    is_local = str(row.get("source") or "").startswith("local")
    prefix = (
        "来源原有标注（非本次服务端分级）: "
        if is_local
        else "知识有效度: "
    )
    return (
        prefix +
        f"可信度（confidence）={confidence or 'unknown'} / "
        f"证据等级（evidence_level）={evidence_level or 'unknown'} / "
        f"风险等级（risk_level）={risk_level or 'unknown'}"
        + ("" if is_local else f" / 复用分（reuse_score）={reuse_score if reuse_score != '' else 'unknown'}")
    )


def localized_analysis_text(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    exact = {
        "Launcher or activity launch path may leave window focus in an unexpected state": "Launcher 或 Activity 启动链路可能导致窗口焦点状态异常",
        "Adjust ActivityTaskManager or WindowManager focus update behavior around resumed activity or visible windows": "调整 ActivityTaskManager 或 WindowManager 中恢复 Activity、可见窗口相关的焦点更新逻辑",
        "Power or policy behavior may not match the product requirement": "电源或策略行为可能与产品需求不一致",
        "Adjust policy or power handling code in the modified Framework path": "调整修改路径中的策略或电源处理逻辑",
        "Problem cannot be confidently inferred from patch paths alone": "需要结合修改文件和说明确认问题场景",
        "Review the changed files and readme before reuse": "复用前先核对修改文件和说明",
        "Audio capture/microphone or camera path may not match product permission or fallback policy": "音频录制、麦克风或相机链路可能不符合产品权限或回退策略要求",
        "Adjust Audio/Camera services or HAL paths, then verify recording, camera, scan, and permission-switch scenarios": "调整 Audio/Camera 相关服务或 HAL 路径，并验证录音、拍照、扫码和权限切换场景",
        "Audio route, volume, or microphone behavior may not match the product requirement": "音频路由、音量或麦克风行为可能不符合产品要求",
        "Adjust AudioService, AudioFlinger, or volume policy paths and verify volume, recording, and media playback scenarios": "调整 AudioService、AudioFlinger 或音量策略相关路径，并验证音量、录音和媒体播放场景",
        "Camera preview, scan, capture, or permission behavior may not match the product requirement": "相机预览、扫码、拍照或相机权限行为可能不符合产品要求",
        "Adjust CameraService, Camera2, or camera HAL paths and verify target camera scenarios": "调整 CameraService、Camera2 或相机 HAL 相关路径，并验证目标相机场景",
        "External storage, mount, or app storage-access permission behavior may not match the product requirement": "外部存储、挂载或应用访问存储的权限行为可能不符合产品要求",
        "Adjust vold, VolumeManager, or storage-access paths and verify USB disk, OBB, and external-storage scenarios": "调整 vold、VolumeManager 或存储访问相关路径，并验证 U 盘、OBB 和外部存储访问场景",
        "Wi-Fi service, default configuration, or connection permission behavior may not match the product requirement": "Wi-Fi 服务、默认配置或连接权限行为可能不符合产品要求",
        "Adjust Wi-Fi service or product configuration paths and verify connect, toggle, and permission scenarios": "调整 Wi-Fi service 或产品配置路径，并验证连接、开关和权限相关场景",
        "USB device node, permission, or peripheral detection behavior may not match the product requirement": "USB 设备节点、权限或外设识别行为可能不符合产品要求",
        "Adjust ueventd, USB permission, or device configuration paths and verify target peripheral detection and access permission": "调整 ueventd、USB 权限或设备配置路径，并验证目标外设识别和访问权限",
        "Product build configuration, preinstalled apps, or board-level toggles may not match the project requirement": "产品编译配置、预置应用或板级开关可能不符合项目要求",
        "Adjust BoardConfig, device makefiles, or preinstall lists and verify build artifacts and first-boot state": "调整 BoardConfig、device makefile 或预置应用清单，并验证编译产物和首次开机状态",
        "power or policy behavior": "按键/电源/策略行为",
        "activity launch/resume": "Activity 启动/恢复",
        "window focus": "窗口焦点/显示层级",
        "launcher handoff": "Launcher 交接",
        "input dispatch": "输入分发",
        "system UI behavior": "SystemUI 行为",
        "package install or package state": "包安装/包状态",
        "keyguard or password flow": "锁屏/密码流程",
        "recovery or factory reset flow": "Recovery/恢复出厂流程",
        "split screen behavior": "分屏行为",
        "screenshot behavior": "截屏行为",
        "media sound behavior": "媒体声音行为",
        "launcher workspace or recents": "Launcher 桌面/最近任务",
        "quick settings interaction": "快捷设置交互",
        "modified code path requires requirement-specific verification": "修改路径需按需求验证",
        "settings provider defaults": "Settings 默认值",
        "permission grant state": "权限授权状态",
        "resource overlay or string display": "资源覆盖/字符串显示",
        "wallpaper settings": "壁纸设置",
        "font rendering": "字体显示",
        "locale or language display": "语言/地区显示",
        "settings regulatory info": "法规信息设置",
        "audio route or volume behavior": "音频路由/音量行为",
        "camera behavior": "相机行为",
        "storage or volume management": "存储/挂载管理",
        "wifi service or configuration": "Wi-Fi 服务/配置",
        "usb or device permission": "USB/设备权限",
        "product config or preinstall": "产品配置/预置应用",
        "audio route/volume": "音频路由/音量",
        "storage/volume": "存储/挂载",
        "usb/device permission": "USB/设备权限",
        "product config/preinstall": "产品配置/预置",
    }
    if raw in exact:
        return exact[raw]
    match = re.match(r"^Behavior in (.+) may need correction$", raw)
    if match:
        return f"{match.group(1)} 相关行为需要核对和修正"
    match = re.match(r"^Patch changes (.+) code paths and should be reviewed against the target requirement$", raw)
    if match:
        return f"补丁修改了 {match.group(1)} 相关代码路径，复用前需要按目标需求核对"
    return raw


def format_case(root: Path, row: dict[str, Any], index: int) -> str:
    title = row.get("title") or row.get("case_id") or row.get("id") or "(case)"
    lines = [
        f"{index}. [case] {title}",
        f"   - id: {row.get('case_id') or row.get('id', '')}",
    ]
    if row.get("problem") or row.get("requirement_or_symptom"):
        lines.append(f"   - 问题/需求: {row.get('problem') or row.get('requirement_or_symptom')}")
    if row.get("solution_summary"):
        lines.append(f"   - 方案摘要: {row.get('solution_summary')}")
    validity_line = format_knowledge_validity(row)
    if validity_line:
        lines.append(f"   - {validity_line}")
    if row.get("implementation_ids"):
        lines.append(f"   - implementations: {compact_list(row.get('implementation_ids'), 6)}")
    if row.get("replacement_case_id"):
        lines.append(f"   - 推荐替代: {row.get('replacement_case_id')} / {row.get('replacement_title', '')}")
    if row.get("replaces_case_ids"):
        lines.append(f"   - 替代旧案例: {compact_list(row.get('replaces_case_ids'), 6)}")
    if row.get("source_priority"):
        lines.append(f"   - source_priority: {row.get('source_priority')}")
    if row.get("_matched_terms"):
        lines.append(f"   - matched: {', '.join(row.get('_matched_terms', []))}")
    return "\n".join(lines)


def format_implementation(root: Path, row: dict[str, Any], index: int) -> str:
    title = row.get("implementation_scope") or row.get("implementation_id") or row.get("id") or "(implementation)"
    lines = [
        f"{index}. [implementation] {title}",
        f"   - id: {row.get('implementation_id') or row.get('id', '')}",
        f"   - case/status: {row.get('case_id', '')} / {row.get('status', '')}",
    ]
    lines.append(
        "   - 平台/Android/项目: "
        f"{row.get('platform') or 'unknown'} / {row.get('android_version') or 'unknown'} / {row.get('project') or 'unknown'}"
    )
    validity_line = format_knowledge_validity(row)
    if validity_line:
        lines.append(f"   - {validity_line}")
    if row.get("repo_paths"):
        lines.append(f"   - 仓库路径: {compact_list(row.get('repo_paths'), 6)}")
    if row.get("modified_files"):
        lines.append(f"   - 修改文件: {compact_list(row.get('modified_files'), 6)}")
    if row.get("patch_ids"):
        lines.append(f"   - patches: {compact_list(row.get('patch_ids'), 6)}")
    verification = row.get("verification") if isinstance(row.get("verification"), dict) else {}
    if verification:
        lines.append(
            f"   - 验证: {verification.get('status', '')} / {verification.get('method', '')} / {verification.get('summary', '')}"
        )
    if row.get("report_ids"):
        lines.append(f"   - reports: {compact_list(row.get('report_ids'), 6)}")
    if row.get("_matched_terms"):
        lines.append(f"   - matched: {', '.join(row.get('_matched_terms', []))}")
    return "\n".join(lines)


def format_patch(root: Path, row: dict[str, Any], index: int) -> str:
    title = row.get("title") or row.get("summary") or row.get("patch_name") or row.get("id") or row.get("patch_id") or "(untitled patch)"
    lines = [
        f"{index}. [patch] {title}",
        f"   - id: {row.get('id') or row.get('patch_id', '')}",
        f"   - author/date/status: {row.get('author', '')} / {result_date(row)} / {row.get('status', '') or 'unknown'}",
    ]
    if row.get("project"):
        lines.append(f"   - project: {row.get('project')}")
    if row.get("scope") or row.get("repo_path") or row.get("feature_slug"):
        lines.append(
            "   - scope/repo/feature: "
            f"{row.get('scope') or 'unknown'} / {row.get('repo_path') or 'unknown'} / {row.get('feature_slug') or 'unknown'}"
        )
    if row.get("filename_confidence") or row.get("original_patch_name"):
        lines.append(f"   - filename: {row.get('filename_confidence') or 'unknown'} / {row.get('original_patch_name') or ''}")
    if row.get("summary") and row.get("summary") != title:
        lines.append(f"   - summary: {row.get('summary')}")
    if row.get("modified_files"):
        lines.append(f"   - modified_files: {compact_list(row.get('modified_files'))}")
    if row.get("modules"):
        lines.append(f"   - modules: {compact_list(row.get('modules'))}")
    if row.get("problem_summary") or row.get("solution_summary"):
        lines.append("   - 补丁问题线索: " f"{localized_analysis_text(row.get('problem_summary') or '')}")
        if row.get("solution_summary"):
            lines.append(f"   - 补丁方案线索: {localized_analysis_text(row.get('solution_summary') or '')}")
    if row.get("keywords") or row.get("risk_areas"):
        lines.append(
            "   - 关键词/风险面: "
            f"{compact_list(row.get('keywords'))}"
            f" / {compact_list(row.get('risk_areas'))}"
        )
    symbols = compact_list(
        [
            *parse_json(row.get("system_properties"), []),
            *parse_json(row.get("settings_keys"), []),
            *parse_json(row.get("resource_keys"), []),
            *parse_json(row.get("strings"), []),
            *parse_json(row.get("framework_log_keys"), []),
        ]
    )
    if symbols:
        lines.append(f"   - symbols: {symbols}")
    if row.get("readme"):
        lines.append(f"   - readme: {rel_or_empty(root, row.get('readme'))}")
    if row.get("patch_files"):
        patch_files = parse_json(row.get("patch_files"), [])
        if patch_files:
            lines.append(f"   - patch: {rel_or_empty(root, patch_files[0])}")
    elif row.get("path"):
        lines.append(f"   - patch: {rel_or_empty(root, Path(str(row.get('path'))).parent / 'patch.patch')}")
    if row.get("_matched_terms"):
        lines.append(f"   - matched: {', '.join(row.get('_matched_terms', []))}")
    return "\n".join(lines)


def format_report(root: Path, row: dict[str, Any], index: int) -> str:
    title = row.get("overview") or row.get("id") or "(report)"
    lines = [
        f"{index}. [report] {title}",
        f"   - id: {row.get('id', '')}",
        f"   - type/author/date: {row.get('type', '')} / {row.get('author', '')} / {result_date(row)}",
    ]
    items = row.get("items") or []
    if isinstance(items, list) and items:
        sample = []
        for item in items[:3]:
            sample.append(f"{item.get('project', '')}:{item.get('title', '')}".strip(":"))
        lines.append(f"   - items: {', '.join(sample)}")
    if row.get("report_path"):
        lines.append(f"   - report: {rel_or_empty(root, row.get('report_path'))}")
    if row.get("_matched_terms"):
        lines.append(f"   - matched: {', '.join(row.get('_matched_terms', []))}")
    return "\n".join(lines)


def format_symbol(root: Path, row: dict[str, Any], index: int) -> str:
    lines = [
        f"{index}. [symbol] {row.get('symbol', '')}",
        f"   - type/patch/author: {row.get('type', '')} / {row.get('patch_id', '')} / {row.get('author', '')}",
    ]
    if row.get("path"):
        lines.append(f"   - path: {rel_or_empty(root, row.get('path'))}")
    if row.get("_matched_terms"):
        lines.append(f"   - matched: {', '.join(row.get('_matched_terms', []))}")
    return "\n".join(lines)


def format_event(root: Path, row: dict[str, Any], index: int) -> str:
    title = row.get("summary") or row.get("id") or "(knowledge event)"
    lines = [
        f"{index}. [event] {title}",
        f"   - id: {row.get('id', '')}",
        f"   - kind/package_status: {row.get('package_kind', '')} / {row.get('package_status', '')}",
        f"   - member/date/platform: {row.get('member', '')} / {result_date(row)} / {row.get('platform', '')}",
    ]
    if row.get("project"):
        lines.append(f"   - project: {row.get('project')}")
    if row.get("path"):
        lines.append(f"   - event: {rel_or_empty(root, row.get('path'))}")
    if row.get("_matched_terms"):
        lines.append(f"   - matched: {', '.join(row.get('_matched_terms', []))}")
    return "\n".join(lines)


def format_evidence(root: Path, row: dict[str, Any], index: int) -> str:
    title = row.get("summary") or row.get("id") or "(evidence)"
    lines = [
        f"{index}. [evidence] {title}",
        f"   - id: {row.get('id', '')}",
        f"   - event/kind/result: {row.get('event_id', '')} / {row.get('evidence_kind', '')} / {row.get('result', '')}",
    ]
    if row.get("package_status") or row.get("project") or row.get("platform"):
        lines.append(f"   - context: {row.get('package_status', '')} / {row.get('project', '')} / {row.get('platform', '')}")
    if row.get("path"):
        lines.append(f"   - evidence: {rel_or_empty(root, row.get('path'))}")
    if row.get("_matched_terms"):
        lines.append(f"   - matched: {', '.join(row.get('_matched_terms', []))}")
    return "\n".join(lines)


REUSE_GRADE_LABELS = {
    "direct_reuse_candidate": "可直接复用候选",
    "adaptation_candidate": "需适配候选",
    "reference_only": "仅参考",
}


def format_server_result(row: dict[str, Any], index: int) -> str:
    grade = str(row.get("reuse_grade") or "")
    label = REUSE_GRADE_LABELS.get(grade, grade or "服务端未分级")
    title = row.get("title") or row.get("summary") or row.get("case_title") or "未命名知识候选"
    lines = [
        f"{index}. [{label}] {title}",
    ]
    if row.get("summary") and row.get("summary") != title:
        lines.append(f"   - 摘要: {row.get('summary')}")
    if row.get("relative_path"):
        lines.append(f"   - 补丁路径: {row.get('relative_path')}")
    if row.get("patch_sha256"):
        lines.append(f"   - 补丁 SHA256: {row.get('patch_sha256')}")
    if row.get("problem_summary"):
        lines.append(f"   - 问题: {row.get('problem_summary')}")
    if row.get("solution_summary"):
        lines.append(f"   - 方案: {row.get('solution_summary')}")
    if row.get("matched_channels"):
        lines.append(f"   - 命中通道: {compact_list(row.get('matched_channels'), 6)}")
    if row.get("matched_anchors"):
        lines.append(f"   - 命中锚点: {compact_list(row.get('matched_anchors'), 6)}")
    if row.get("layers"):
        lines.append(f"   - Android 层级: {compact_list(row.get('layers'), 7)}")
    revalidation = row.get("requires_revalidation")
    lines.append(
        "   - 需要重新验证: "
        + ("yes" if revalidation is True else "no" if revalidation is False else "unknown")
    )
    comparison = row.get("environment_comparison")
    if isinstance(comparison, dict):
        lines.append(f"   - 环境比较: {comparison.get('mode') or 'unknown'}")
        alternatives = comparison.get("alternative_tuples")
        if isinstance(alternatives, list) and alternatives:
            lines.append(f"   - 可参考环境差异: {compact_list(alternatives, 3)}")
        matched_sets = comparison.get("non_dominated_matched_dimension_sets")
        if isinstance(matched_sets, list) and matched_sets:
            rendered_sets = [
                "+".join(str(dimension) for dimension in dimensions)
                for dimensions in matched_sets
                if isinstance(dimensions, list) and dimensions
            ]
            if rendered_sets:
                lines.append(f"   - 已匹配环境维度: {compact_list(rendered_sets, 4)}")
    bindings = row.get("required_bindings")
    if isinstance(bindings, list):
        lines.append(f"   - 必需证据绑定: {len(bindings)}")
        for binding in bindings:
            if isinstance(binding, dict):
                lines.append(
                    "     - "
                    f"binding={binding.get('binding_id') or 'unknown'} / "
                    f"asset={binding.get('patch_asset_id') or 'unknown'} / "
                    f"sha256={binding.get('patch_sha256') or 'unknown'} / "
                    f"layer={binding.get('layer') or 'unknown'} / "
                    f"repository={binding.get('repository') or 'unknown'} / "
                    f"package={binding.get('patch_package_id') or 'unknown'} / "
                    f"role={binding.get('binding_role') or 'unknown'} / "
                    f"state={binding.get('binding_state') or 'unknown'} / "
                    f"closure={binding.get('closure_state') or 'unknown'} / "
                    f"closure_reason={binding.get('closure_reason') or '(none)'} / "
                    f"revision={binding.get('manifest_revision') if binding.get('manifest_revision') is not None else 'unknown'} / "
                    f"manifest_sha256={binding.get('manifest_sha256') or 'unknown'}"
                )
                environment_evidence = binding.get("environment_evidence")
                if isinstance(environment_evidence, list):
                    lines.append(
                        "       - 环境证据: "
                        + (compact_list(environment_evidence, len(environment_evidence)) or "none")
                    )
    evidence_gaps = row.get("evidence_gaps")
    if isinstance(evidence_gaps, list):
        lines.append(f"   - 证据缺口: {len(evidence_gaps)}")
        for gap in evidence_gaps:
            if isinstance(gap, dict):
                lines.append(
                    "     - "
                    f"reason={gap.get('reason') or 'unknown'} / "
                    f"binding={gap.get('binding_id') or '(global)'} / "
                    f"context={json.dumps(gap.get('context') or {}, ensure_ascii=False, sort_keys=True)}"
                )
    if row.get("qualification_reason"):
        lines.append(f"   - 分级原因: {row.get('qualification_reason')}")
    technical = [
        value
        for value in (
            row.get("case_id"),
            row.get("implementation_id"),
            row.get("evidence_binding_id"),
            row.get("patch_asset_id"),
            row.get("patch_id"),
            row.get("id"),
        )
        if value
    ]
    if technical:
        lines.append(f"   - 技术标识: {compact_list(technical, 6)}")
    return "\n".join(lines)


def format_markdown(
    root: Path | None,
    q: str,
    results: list[dict[str, Any]],
    refresh_status: str | None,
    *,
    source: str = "local_jsonl_fallback",
    search_mode: str = "local_jsonl",
    fallback_reason: str = "",
    server_payloads: list[dict[str, Any]] | None = None,
) -> str:
    lines = [
        "# 知识库搜索结果",
        "",
        f"- source: {source}",
        f"- search_mode: {search_mode}",
        f"- root: {root or '(server)'}",
        f"- query: {q or '(empty)'}",
        f"- results: {len(results)}",
    ]
    if source != "server_api":
        if fallback_reason:
            lines.append(f"- fallback_reason: {fallback_reason}")
        lines.append("- fallback 提示: 本地文本搜索，未经过服务端复用分级；不要把本地命中直接当作可复用结论。")
    payloads = server_payloads or []
    for payload in payloads:
        projection = payload.get("projection") if isinstance(payload.get("projection"), dict) else {}
        pagination = payload.get("pagination") if isinstance(payload.get("pagination"), dict) else {}
        target = payload.get("target_environment") if isinstance(payload.get("target_environment"), dict) else {}
        lines.append(
            "- 服务端状态: "
            f"query={payload.get('query') or ''} / "
            f"target={target.get('project') or '(empty)'}/{target.get('platform') or '(empty)'}/{target.get('android_version') or '(empty)'} / "
            f"state={payload.get('result_state') or ''} / "
            f"completeness={payload.get('completeness') or ''} / "
            f"reason={payload.get('reason_code') or '(none)'} / "
            f"projection_ready={projection.get('ready')} / "
            f"projection_complete={projection.get('complete')} / "
            f"projection_reason={projection.get('reason_code') or '(none)'} / "
            f"offset={pagination.get('offset', 0)} / total={pagination.get('total', 0)} / "
            f"has_more={pagination.get('has_more', False)}"
        )
    if fallback_reason and source == "server_api":
        lines.append(f"- fallback_reason: {fallback_reason}")
    if refresh_status:
        lines.append(f"- refresh: {refresh_status}")
    lines.append("")
    if not results:
        unhealthy = source == "server_api" and any(
            payload.get("result_state") == "indeterminate"
            or payload.get("completeness") != "complete"
            or not isinstance(payload.get("projection"), dict)
            or payload["projection"].get("ready") is not True
            or payload["projection"].get("complete") is not True
            for payload in payloads
        )
        if unhealthy:
            lines.append("本次服务端搜索不完整，当前无法确定是否存在可复用知识；不得记为 not_found。")
        elif source == "server_api":
            lines.append("本次完整服务端查询没有返回匹配结果；可换用代码锚点或补充查询继续确认。")
        else:
            lines.append("本地文本索引未找到匹配提示；这不是服务端 not_found 结论。")
        return "\n".join(lines)

    for index, row in enumerate(results, start=1):
        if row.get("source") == "server_api":
            lines.append(format_server_result(row, index))
            lines.append("")
            continue
        row = {**row, "source": source}
        kind = row.get("kind")
        if kind == "case":
            lines.append(format_case(root, row, index))
        elif kind == "implementation":
            lines.append(format_implementation(root, row, index))
        elif kind == "patch":
            lines.append(format_patch(root, row, index))
        elif kind == "report":
            lines.append(format_report(root, row, index))
        elif kind == "symbol":
            lines.append(format_symbol(root, row, index))
        elif kind == "event":
            lines.append(format_event(root, row, index))
        elif kind == "evidence":
            lines.append(format_evidence(root, row, index))
        else:
            lines.append(f"{index}. [{kind}] {row.get('id') or row.get('title') or row.get('symbol')}")
        lines.append("")
    return "\n".join(lines).rstrip()
