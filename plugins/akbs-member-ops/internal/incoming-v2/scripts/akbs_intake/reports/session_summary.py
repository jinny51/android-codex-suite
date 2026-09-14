from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from typing import Any, Callable

from akbs_member_ops.knowledge_rules import find_company_project, find_company_projects

from akbs_intake.config import expanded_path, parse_bool
from akbs_intake.report_sessions import (
    NOISE_TEXT_RE,
    SessionWork,
    compact_text,
    is_report_generation_request,
    project_anchor,
    should_skip_message,
    strip_project_anchor,
)
from akbs_intake.reports.scope import (
    WorkScopeInference,
    combine_scope_inferences,
    infer_work_scope,
)
from akbs_intake.reports.document_work import DOCUMENT_WORK_TYPE, clean_document_name


DAILY_STATUS_VALUES = ("已完成", "处理中", "待验证", "阻塞")
DAILY_METHOD_MISSING = "未从授权会话中提取到具体处理过程，需成员补充。"


def summarize_session(work: SessionWork) -> str:
    text = " ".join([work.thread_name, *work.messages, *work.outcomes]).lower()
    if all(keyword in text for keyword in ("codex", "日报")) and any(keyword in text for keyword in ("自动化", "skill", "插件", "知识库")):
        return "搭建 Codex incoming 自动上传材料与知识库检索能力"
    project = project_anchor(" ".join([work.project, work.thread_name, *work.messages]))
    messages = [
        item
        for item in work.messages
        if not item.startswith("执行命令:") and not should_skip_message(item) and not is_report_generation_request(item)
    ]
    anchored_messages = [item for item in messages if project and project.lower() in item.lower()]
    candidates = anchored_messages or messages
    if candidates:
        summaries = [clean_work_summary(item, project) for item in candidates[:3]]
        summary = "；".join(item for item in summaries if item)
        if summary:
            return compact_text(summary, 240)
    outcome_summaries = [clean_work_summary(item, project) for item in work.outcomes[:2]]
    outcome_summary = "；".join(item for item in outcome_summaries if item)
    if outcome_summary:
        return compact_text(outcome_summary, 240)
    name = work.thread_name.strip()
    if name and not re.fullmatch(r"[0-9a-f-]{20,}", name) and not NOISE_TEXT_RE.search(name):
        summary = clean_work_summary(name, project)
        if summary:
            return compact_text(summary, 120)
    return "处理Codex对话中的开发问题"


def clean_work_summary(text: str, project: str) -> str:
    if project:
        customer_prefix = re.match(
            rf"^\s*(?:今天|今日|本周)?\s*{re.escape(project)}\s+"
            r"(?P<context>[^，,\n]{1,48})[，,]\s*",
            text,
            re.IGNORECASE,
        )
        if customer_prefix and not re.search(
            r"完成|解决|修改|处理|排查|验证|测试|修复|实现|适配|移植|进行|阻塞|等待|提交|构建|编译",
            customer_prefix.group("context"),
        ):
            text = text[customer_prefix.end() :]
    text = strip_project_anchor(text, project)
    text = re.sub(r"(?i)\b(?:today|daily|weekly)\b", " ", text)
    text = re.sub(r"^(?:今天|今日|本周|继续|主要|围绕|完成|处理|修复|排查|整理|在|通过)\s*", "", text)
    text = re.sub(r"源码里\s*", "", text)
    text = re.sub(r"连接服务器\s*", "", text)
    text = re.sub(r"通过\s*[，,]?\s*", "", text)
    text = re.sub(r"在\s*/?\s*处理", "处理", text)
    text = re.sub(r"在\s*/?\s*", "", text)
    text = re.sub(r"\s+", " ", text).strip(" ，,。；;")
    return text


def split_work_clauses(text: str, project: str) -> list[str]:
    normalized = re.sub(r"(?m)^\s*(?:[-*•]|\d+[.、])\s*", "\n", str(text or ""))
    rows: list[str] = []
    for part in re.split(r"\n+|[；;]+|(?<=。)", normalized):
        raw = part.strip()
        if not raw or should_skip_message(raw) or is_report_generation_request(raw):
            continue
        value = clean_work_summary(raw, project)
        if not value or len(value) < 4:
            continue
        if re.match(r"^(?:项目客户|客户|合成测试)?(?:进度|状态|结果|说明)?\s*[:：]", value):
            continue
        if re.fullmatch(r"(?:好的?|收到|可以|继续|无|暂无)[。！!]?", value):
            continue
        if value not in rows:
            rows.append(compact_text(value, 160))
    return rows


def daily_item_key(value: str) -> str:
    text = re.sub(
        r"^(?:今天|今日|本周|继续|主要|完成|处理|修复|排查|推进|已完成|已解决|正在|待验证)\s*",
        "",
        str(value or ""),
    )
    text = re.sub(r"(?:已完成|已解决|处理中|进行中|待验证|验证通过|修复完成)$", "", text)
    return re.sub(r"[^A-Za-z0-9\u4e00-\u9fff]+", "", text).lower()


def same_daily_item(left: str, right: str) -> bool:
    a = daily_item_key(left)
    b = daily_item_key(right)
    if not a or not b:
        return False
    if a == b or (min(len(a), len(b)) >= 8 and (a in b or b in a)):
        return True
    if min(len(a), len(b)) < 4:
        return False
    a_pairs = {a[index : index + 2] for index in range(len(a) - 1)}
    b_pairs = {b[index : index + 2] for index in range(len(b) - 1)}
    return bool(a_pairs and b_pairs) and len(a_pairs & b_pairs) / max(1, len(a_pairs | b_pairs)) >= 0.55


def text_similarity(left: str, right: str) -> float:
    a = daily_item_key(left)
    b = daily_item_key(right)
    if not a or not b:
        return 0.0
    if a in b or b in a:
        return min(len(a), len(b)) / max(len(a), len(b))
    a_pairs = {a[index : index + 2] for index in range(len(a) - 1)}
    b_pairs = {b[index : index + 2] for index in range(len(b) - 1)}
    return len(a_pairs & b_pairs) / max(1, len(a_pairs | b_pairs))


def daily_status(text: str, *, has_patch: bool = False) -> str:
    value = str(text or "")
    if any(token in value for token in ("阻塞", "blocked", "无法实现", "缺少环境", "等待提供", "依赖外部")):
        return "阻塞"
    if any(token in value for token in ("待验证", "验证中", "等待测试", "等待客户验证", "待设备验证", "待回归")):
        return "待验证"
    if any(token in value for token in ("处理中", "进行中", "未完成", "待处理", "继续排查", "修改中", "正在")):
        return "处理中"
    if any(token in value for token in ("已完成", "已解决", "修复完成", "验证通过", "测试通过", "成功", "完成")):
        return "已完成"
    if has_patch:
        return "待验证"
    return "处理中"


def command_method(command: str) -> str:
    value = str(command or "").lower()
    if re.search(r"(?:pytest|unittest|\btest\b|verify|check)", value):
        return "运行相关自动化检查或测试"
    if re.search(r"(?:gradle|ninja|soong|make|m\s+|build)", value):
        return "执行构建验证"
    if "adb" in value and re.search(r"(?:install|push|sync)", value):
        return "部署构建产物到设备"
    if "adb" in value or "logcat" in value:
        return "通过设备命令或日志核对现象"
    if re.search(r"\b(?:rg|grep|find)\b", value):
        return "检索并定位相关代码或日志"
    if re.search(r"\bgit\s+(?:diff|show|status|log)\b", value):
        return "核对代码变更和版本状态"
    if re.search(r"\b(?:sed|cat|head|tail|nl)\b", value):
        return "查看相关代码、配置或日志"
    return "执行工程命令核对处理结果"


def clause_methods(clauses: list[str]) -> list[str]:
    text = " ".join(clauses)
    methods: list[str] = []
    mappings = (
        (r"排查|定位|分析|复现|抓取", "排查并定位问题原因"),
        (r"修改|调整|实现|适配|移植|合入", "修改或适配相关实现"),
        (r"验证|测试|回归|确认", "执行相关验证或回归测试"),
        (r"构建|编译", "执行构建验证"),
        (r"提交|交付|推送", "整理并交付处理结果"),
    )
    for pattern, method in mappings:
        if re.search(pattern, text) and method not in methods:
            methods.append(method)
    return methods


def session_project_segments(session: SessionWork) -> list[SessionWork]:
    groups: dict[str, list[str]] = {}
    unanchored: list[str] = []
    for message in session.messages:
        projects = find_company_projects(message)
        if not projects:
            unanchored.append(message)
            continue
        for project in projects:
            groups.setdefault(project, []).append(message)
    fallback_project = find_company_project(session.project)
    if not groups and fallback_project:
        groups[fallback_project] = list(session.messages)
    if len(groups) == 1:
        groups[next(iter(groups))].extend(unanchored)
    if not groups:
        groups[session.project] = list(session.messages)

    segments: list[SessionWork] = []
    for project, messages in groups.items():
        relevant_outcomes = [
            outcome for outcome in session.outcomes if project in find_company_projects(outcome)
        ]
        relevant_commands = [
            command for command in session.commands if project in find_company_projects(command)
        ]
        if len(groups) == 1:
            if not relevant_outcomes:
                relevant_outcomes = list(session.outcomes)
            if not relevant_commands:
                relevant_commands = list(session.commands)
        segments.append(
            SessionWork(
                session_id=session.session_id,
                thread_name=session.thread_name,
                cwd=session.cwd,
                project=project,
                messages=messages,
                outcomes=relevant_outcomes,
                commands=relevant_commands,
                source_work_type_hint=session.source_work_type_hint,
                source_app_name_hint=session.source_app_name_hint,
                source_scope_basis=list(session.source_scope_basis),
                source_scope_conflict=session.source_scope_conflict,
                latest_at=session.latest_at,
            )
        )
    return segments


def inferred_scope_for_segment(segment: SessionWork, *, has_patch_artifact: bool) -> WorkScopeInference:
    source_hint = WorkScopeInference(
        segment.source_work_type_hint,
        segment.source_app_name_hint,
        tuple(segment.source_scope_basis),
        segment.source_scope_conflict,
    )
    member_hint = infer_work_scope(
        texts=[message for message in segment.messages if not is_report_generation_request(message)],
    )
    development_hint = infer_work_scope(
        path_hint=segment.cwd,
        texts=[segment.thread_name, *segment.outcomes, *segment.commands],
        allow_explicit=False,
    )
    inferred = combine_scope_inferences(source_hint, member_hint, development_hint)
    if not inferred.work_type and not inferred.conflict and has_patch_artifact:
        return WorkScopeInference("Patch", basis=("patch_artifact",))
    return inferred


def daily_rows_for_segment(segment: SessionWork, *, has_patch: bool) -> list[dict[str, Any]]:
    project = find_company_project(segment.project) or segment.project
    tasks = [
        clause
        for message in segment.messages
        if not is_report_generation_request(message)
        for clause in split_work_clauses(message, project)
    ]
    if not tasks:
        fallback = summarize_session(segment)
        if fallback != "处理Codex对话中的开发问题":
            tasks = [fallback]
    outcomes = [
        clause
        for outcome in segment.outcomes
        for clause in split_work_clauses(outcome, project)
    ]
    methods = list(dict.fromkeys(command_method(command) for command in segment.commands))
    methods.extend(method for method in clause_methods([*tasks, *outcomes]) if method not in methods)
    methods = methods[:4] or [DAILY_METHOD_MISSING]
    rows: list[dict[str, Any]] = []
    for task in tasks:
        ranked = sorted(
            ((text_similarity(task, outcome), outcome) for outcome in outcomes),
            reverse=True,
        )
        matched_outcome = ranked[0][1] if ranked and (ranked[0][0] >= 0.2 or len(tasks) == 1) else ""
        status_basis = matched_outcome or task
        status = daily_status(status_basis, has_patch=has_patch)
        result_text = compact_text(matched_outcome, 160) if matched_outcome else progress_for_session(segment, has_patch)
        rows.append(
            {
                "name": compact_text(task, 80),
                "did": [compact_text(task, 160)],
                "how": list(methods),
                "result": result_text or status,
                "status": status,
                "_latest_at": segment.latest_at,
            }
        )
    return rows


def merge_daily_row(rows: list[dict[str, Any]], row: dict[str, Any]) -> None:
    existing = next((item for item in rows if same_daily_item(item["name"], row["name"])), None)
    if existing is None:
        rows.append(row)
        return
    for field in ("did", "how"):
        for value in row[field]:
            if value not in existing[field]:
                existing[field].append(value)
    if row["_latest_at"] >= existing.get("_latest_at", ""):
        existing["result"] = row["result"]
        existing["status"] = row["status"]
        existing["_latest_at"] = row["_latest_at"]


def daily_work_scopes(
    sessions: list[SessionWork],
    patches: list[Any],
) -> list[dict[str, Any]]:
    valid_projects = sorted(
        dict.fromkeys(project for session in sessions for project in [find_company_project(session.project)] if project)
    )
    fallback_project = valid_projects[0] if len(valid_projects) == 1 else ""
    patch_projects = {find_company_project(patch.project) or fallback_project or patch.project for patch in patches}
    result: dict[tuple[str, str, str], dict[str, Any]] = {}

    for session in sorted(sessions, key=lambda item: (item.latest_at, item.session_id)):
        for segment in session_project_segments(session):
            canonical_project = find_company_project(segment.project)
            project = canonical_project or segment.project
            inferred = inferred_scope_for_segment(segment, has_patch_artifact=project in patch_projects)
            if inferred.work_type in {DOCUMENT_WORK_TYPE, "Other"} and not canonical_project:
                project = ""
            scope_name = (
                inferred.app_name.casefold()
                if inferred.work_type == "App"
                else inferred.document_name.casefold()
                if inferred.work_type == DOCUMENT_WORK_TYPE
                else ""
            )
            key = (project, inferred.work_type, scope_name)
            scope = result.setdefault(
                key,
                {
                    "project": project,
                    "work_type": inferred.work_type,
                    "app_name": inferred.app_name if inferred.work_type == "App" else "",
                    "document_name": (
                        clean_document_name(inferred.document_name)
                        if inferred.work_type == DOCUMENT_WORK_TYPE
                        else ""
                    ),
                    "work_name": "",
                    "work_items": [],
                    "inference_basis": list(inferred.basis),
                    "inference_conflict": inferred.conflict,
                },
            )
            for basis in inferred.basis:
                if basis not in scope["inference_basis"]:
                    scope["inference_basis"].append(basis)
            scope["inference_conflict"] = bool(scope["inference_conflict"] or inferred.conflict)
            segment_rows = daily_rows_for_segment(
                segment,
                has_patch=project in patch_projects and inferred.work_type == "Patch",
            )
            if not project and inferred.work_type == "Other" and segment_rows and not scope["work_name"]:
                scope["work_name"] = str(segment_rows[0].get("name") or "").strip()
            for row in segment_rows:
                merge_daily_row(scope["work_items"], row)

    for patch in patches:
        project = find_company_project(patch.project) or fallback_project or patch.project
        key = (project, "Patch", "")
        if key not in result:
            result[key] = {
                "project": project,
                "work_type": "Patch",
                "app_name": "",
                "work_items": [
                    {
                        "name": "产出功能补丁",
                        "did": ["产出功能补丁"],
                        "how": ["整理代码改动并生成补丁"],
                        "result": "补丁已生成，等待验证",
                        "status": "待验证",
                        "_latest_at": "",
                    }
                ],
                "inference_basis": ["patch_artifact"],
                "inference_conflict": False,
            }
    scopes = sorted(
        result.values(),
        key=lambda row: (
            row["project"],
            0 if row["work_type"] == "Patch" else 1 if row["work_type"] == "App" else 2,
            (row.get("app_name") or row.get("document_name") or row.get("work_name") or "").casefold(),
        ),
    )
    for scope in scopes:
        for row in scope["work_items"]:
            row.pop("_latest_at", None)
    return scopes


def daily_work_items_from_scopes(scopes: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for scope in scopes:
        project = str(scope.get("project") or "")
        for row in scope.get("work_items", []):
            if isinstance(row, dict):
                copied = {
                    **row,
                    "did": list(row.get("did", [])),
                    "how": list(row.get("how", [])),
                    "_latest_at": "",
                }
                merge_daily_row(result.setdefault(project, []), copied)
    for rows in result.values():
        for row in rows:
            row.pop("_latest_at", None)
    return result


def daily_work_items_by_project(
    sessions: list[SessionWork],
    patches: list[Any],
) -> dict[str, list[dict[str, Any]]]:
    return daily_work_items_from_scopes(daily_work_scopes(sessions, patches))


def progress_phrase(text: str) -> str:
    progress = ""
    match = re.search(r"(?:进度|完成度)[:：]?\s*([0-9]{1,3}%\s*(?:->|~|～|-|到)\s*[0-9]{1,3}%|[0-9]{1,3}%)", text)
    if match:
        progress = match.group(1).replace(" ", "")
    if any(word in text for word in ("阻塞", "blocked", "缺少", "等待", "依赖")):
        status = "阻塞"
    elif any(word in text for word in ("待验证", "验证中", "等待测试验证", "待设备验证", "待客户验证")):
        status = "待验证"
    elif any(word in text for word in ("失败", "报错", "未解决", "未完成", "待处理", "继续排查", "修改中", "处理中")):
        status = "处理中"
    elif any(word in text for word in ("已完成", "解决", "修复", "成功", "通过", "验证完成", "改好", "完成")):
        status = "已完成"
    else:
        status = ""
    if progress and status:
        return f"{progress}，{status}"
    return progress or status


def progress_for_session(work: SessionWork, has_patch: bool) -> str:
    outcome_text = " ".join(work.outcomes)
    text = " ".join([work.thread_name, *work.messages, outcome_text])
    outcome_phrase = progress_phrase(outcome_text)
    if outcome_phrase:
        return outcome_phrase
    phrase = progress_phrase(text)
    if phrase:
        return phrase
    if any(word in text for word in ("失败", "报错", "未解决", "未完成", "待处理", "继续排查")):
        return "进行中"
    if any(word in text for word in ("已完成", "解决", "修复", "成功", "通过", "验证完成", "改好", "完成")):
        return "已完成并产出 Patch" if has_patch else "已完成"
    return "已产出 Patch" if has_patch else "进行中"


def work_finding_for_session(session: SessionWork) -> dict[str, Any]:
    text = " ".join([session.thread_name, *session.messages, *session.outcomes]).lower()
    blocked = any(word in text for word in ("失败", "报错", "未解决", "阻塞", "blocked", "fail"))
    framework_like = any(
        token in text
        for token in (
            "framework",
            "frameworks/base",
            "systemui",
            "launcher",
            "settings",
            "patch",
            "补丁",
            "修复",
            "验证",
        )
    )
    if blocked:
        work_status = "blocked"
    elif framework_like:
        work_status = "candidate"
    else:
        work_status = "draft"
    basis = [session.thread_name or session.session_id]
    basis.extend(session.messages[:3])
    basis.extend(session.outcomes[:2])
    missing_evidence = []
    if framework_like:
        missing_evidence.append("需要 patch-capture 判断是否可升级为 android_change")
    else:
        missing_evidence.append("未识别到可直接归档的 Framework patch")
    return {
        "title": summarize_session(session),
        "kind": "possible_android_change" if framework_like else "work_record",
        "work_status": work_status,
        "project": session.project,
        "basis": basis,
        "missing_evidence": missing_evidence,
        "recommended_action": "补齐 diff、构建或验证证据后再判断是否升级为 android_change",
    }

def discover_patches(
    config: dict[str, str],
    sessions: list[SessionWork],
    start,
    end,
    *,
    patch_info_factory: Callable[[Path, str, str], Any],
) -> list[Any]:
    if not parse_bool(config.get("include_patches", "true")):
        return []
    patches: dict[Path, Any] = {}
    if sessions:
        out_dir = expanded_path(config.get("out_dir", "$CODEX_HOME/artifacts/akbs-member-ops"))
        artifacts_root = out_dir.parent
        for manifest_path in artifacts_root.glob("**/manifest.json"):
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                continue
            if not isinstance(manifest, dict) or manifest.get("package_type") != "framework_feature_patch":
                continue
            created_at = str(manifest.get("created_at") or "")
            try:
                capture_date = dt.datetime.fromisoformat(created_at.replace("Z", "+00:00")).date()
            except ValueError:
                continue
            if not (start <= capture_date <= end):
                continue
            project = find_company_project(str(manifest.get("project") or ""))
            if not project and len(sessions) == 1:
                project = sessions[0].project
            for item in manifest.get("patches") or []:
                if not isinstance(item, dict):
                    continue
                rel = str(item.get("path") or "")
                if not rel:
                    continue
                path = (manifest_path.parent / rel).resolve()
                try:
                    path.relative_to(artifacts_root.resolve())
                except ValueError:
                    continue
                if path.is_file() and path.suffix == ".patch":
                    patches[path] = patch_info_factory(path, path.name, project)
    return sorted(patches.values(), key=lambda item: item.name)


def items_by_project(sessions: list[SessionWork], patches: list[Any]) -> dict[str, list[tuple[str, str]]]:
    valid_session_projects = sorted(
        dict.fromkeys(project for session in sessions for project in [find_company_project(session.project)] if project)
    )
    fallback_project = valid_session_projects[0] if len(valid_session_projects) == 1 else ""
    patch_projects = {find_company_project(patch.project) or fallback_project or patch.project for patch in patches}
    items: dict[str, list[tuple[str, str]]] = {}
    for session in sessions:
        for segment in session_project_segments(session):
            project = segment.project
            desc = summarize_session(segment)
            progress = progress_for_session(segment, project in patch_projects)
            entry = (desc, progress)
            if entry not in items.setdefault(project, []):
                items[project].append(entry)
    for patch in patches:
        project = find_company_project(patch.project) or fallback_project or patch.project
        if not items.get(project):
            items.setdefault(project, []).append(("产出功能补丁", "已产出 Patch"))
    return items


def overview_text(report_type: str, items: dict[str, list[tuple[str, str]]], patches: list[Any]) -> str:
    tasks: list[str] = []
    for entries in items.values():
        for desc, _ in entries:
            if desc not in tasks and desc != "产出功能补丁":
                tasks.append(desc)
    if not tasks:
        return "未发现可归档事项，无patch。"
    task_text = "、".join(compact_text(item, 36) for item in tasks[:3]) + ("等" if len(tasks) > 3 else "")
    patch_text = f"产出 {len(patches)} 个patch。" if patches else "无patch。"
    prefix = "今天" if report_type == "daily" else "本周"
    return f"{prefix}处理了{task_text}，{patch_text}"


def work_findings_payload(sessions: list[SessionWork], patches: list[Any]) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    blocked_or_failed: list[dict[str, Any]] = []
    seen_titles: set[str] = set()
    for session in sessions:
        item = work_finding_for_session(session)
        title = str(item.get("title", ""))
        if title and title not in seen_titles:
            seen_titles.add(title)
            items.append(item)
        if item.get("work_status") == "blocked":
            blocked_or_failed.append(
                {
                    "title": title,
                    "work_status": "blocked",
                    "basis": item.get("basis", []),
                }
            )
    for patch in patches:
        title = patch.name
        if title in seen_titles:
            continue
        seen_titles.add(title)
        items.append(
            {
                "title": title,
                "kind": "possible_android_change",
                "work_status": "candidate",
                "basis": [f"发现 patch 文件: {patch.name}", f"项目线索: {patch.project}"],
                "missing_evidence": ["需要 patch-capture 补齐 case/variant/风险/验证证据"],
                "recommended_action": "满足验证条件后升级为 android_change",
            }
        )
    return {
        "scanned_sources": ["codex_sessions", "git_activity", "patch_files", "build_or_verification_records"],
        "items": items,
        "blocked_or_failed": blocked_or_failed,
    }
