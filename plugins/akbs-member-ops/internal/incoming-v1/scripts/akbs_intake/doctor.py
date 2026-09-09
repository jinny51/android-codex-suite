from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import urllib.request
from pathlib import Path
from typing import Any, Callable

from akbs_member_ops.incoming_v1.patch_markers import ALIAS_RE
from akbs_member_ops.http_client import HttpClientFailure, request_json

from akbs_intake.config import (
    allowed_modes,
    artifact_read_roots,
    artifact_path_guard_error,
    default_codex_home,
    expanded_path,
    knowledge_repo_worktree,
    parse_bool,
    resolve_akbs_endpoint,
    submission_api_base_url,
)
from akbs_intake.diagnostics import warn_local_input


RunCommand = Callable[[list[str]], subprocess.CompletedProcess[str]]
PluginGateCheck = Callable[..., dict[str, Any]]


def member_identity_check(config: dict[str, str], *, check_remote: bool) -> dict[str, Any]:
    """Separate the configured claim from a read-only server-authenticated identity."""
    alias = config.get("member_alias", "").strip()
    result: dict[str, Any] = {
        "mode": "fixed_ip_alias",
        "configured_alias": alias,
        "authenticated": False,
        "status": "configured_unverified" if alias else "alias_required",
        "message": "本地已配置成员标识，尚未向服务器确认身份。" if alias else "请先配置成员标识。",
    }
    if not alias:
        return result
    if alias in {"member_alias", "admin_alias", "unknown"} or not ALIAS_RE.fullmatch(alias):
        result.update(status="alias_invalid", message="本地成员标识无效，未请求服务器。")
        return result
    if not check_remote:
        return result

    try:
        request = urllib.request.Request(
            submission_api_base_url(config).rstrip("/") + "/member/me/identity",
            headers={"Accept": "application/json", "X-AKBS-User": alias},
            method="GET",
        )
        identity = request_json(request, timeout=6)
    except ValueError:
        result.update(status="endpoint_invalid", message="身份诊断入口地址无效，未确认成员身份。")
        return result
    except HttpClientFailure as exc:
        failure = exc.result
        if failure.status_code in {401, 403}:
            status, message = "authentication_failed", "服务器未认可当前成员身份，请管理员核对账号和来源网络绑定。"
        elif failure.status_code == 404:
            status, message = "server_unsupported", "服务器尚未提供身份诊断接口，不能据此确认成员身份。"
        elif failure.code == "invalid_success_response":
            status, message = "invalid_response", "服务器身份响应格式不正确，未确认成员身份。"
        else:
            status, message = "server_unavailable", "暂时无法向服务器确认身份；本地配置未改动。"
        result.update(
            status=status, message=message, http_status=failure.status_code,
            error_code=failure.code, request_id=failure.request_id,
        )
        return result

    text_fields = ("member_alias", "member_name", "member_status")
    policy_fields = ("ranking_eligible", "daily_required", "weekly_required")
    if (
        identity.get("schema") != "akbs-member-identity-v1"
        or identity.get("authenticated") is not True
        or any(not isinstance(identity.get(key), str) or not identity[key].strip() for key in text_fields)
        or any(type(identity.get(key)) is not bool for key in policy_fields)
    ):
        result.update(status="invalid_response", message="服务器身份响应不符合约定，未确认成员身份。")
        return result
    if identity["member_alias"] != alias:
        result.update(status="identity_mismatch", message="服务器确认的成员与本地配置不一致；未修改本地身份。")
        return result
    result.update({key: identity[key] for key in (*text_fields, *policy_fields)})
    result.update(
        authenticated=True,
        status="server_confirmed",
        message="服务器已确认当前成员身份；参与排名和应交日报/周报以返回的服务器策略为准，不代表所有业务操作均可用。",
    )
    return result


def latest_pending(report_type: str, config: dict[str, str], date: dt.date | None = None) -> Path:
    candidates: list[Path] = []
    manifest_paths = [
        manifest_path
        for root in artifact_read_roots(config)
        for manifest_path in sorted((root / "pending").glob("*/*/*/manifest.json"))
    ]
    for manifest_path in manifest_paths:
        try:
            raw = manifest_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            warn_local_input("pending_manifest_unreadable", manifest_path)
            continue
        try:
            manifest = json.loads(raw)
        except json.JSONDecodeError:
            warn_local_input("pending_manifest_invalid_json", manifest_path)
            continue
        if not isinstance(manifest, dict):
            warn_local_input("pending_manifest_invalid_object", manifest_path)
            continue
        kind = str(manifest.get("package_kind", ""))
        manifest_type = (
            "daily"
            if kind == "daily_trace"
            else "weekly"
            if kind == "weekly_trace"
            else "patch"
            if kind == "framework_change"
            else ""
        )
        if manifest_type != report_type:
            continue
        if manifest.get("member_alias") != config.get("member_alias"):
            continue
        if date and str(manifest.get("date")) != date.isoformat():
            continue
        candidates.append(manifest_path.parent)
    if not candidates:
        raise SystemExit(f"没有找到 {report_type} pending 工作包")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def nearest_existing_parent(path: Path) -> Path:
    current = path
    while not current.exists() and current.parent != current:
        current = current.parent
    return current


def doctor_strict_checks(
    config: dict[str, str],
    loaded: list[Path],
    check_remote: bool,
    allow_synthetic: bool,
    *,
    run_command: RunCommand,
    plugin_gate_check: PluginGateCheck,
    member_identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []

    def error(message: str) -> None:
        errors.append(message)

    def warn(message: str) -> None:
        warnings.append(message)

    profile = config.get("profile", "").strip()
    alias = config.get("member_alias", "").strip()
    name = config.get("member_name", "").strip()
    role = config.get("role", "").strip()
    knowledge_repo = knowledge_repo_worktree(config)
    out_dir = expanded_path(config.get("out_dir", ""))

    if not loaded:
        warn("未加载任何配置文件，仅依赖默认值或环境变量；成员端自动化建议使用显式 profile 配置。")
    if not profile:
        error("必须显式选择 profile，避免自动化误用默认身份。")
    if not alias:
        error("member_alias 不能为空。")
    elif alias in {"member_alias", "admin_alias", "unknown"}:
        error(f"member_alias 仍是占位值: {alias}")
    elif not ALIAS_RE.fullmatch(alias):
        error("member_alias 只能使用小写字母、数字、点、下划线或横线，且必须以小写字母或数字开头。")
    if not name:
        error("member_name 不能为空，UI 和索引必须显示真实姓名。")
    elif name in {"成员姓名", "管理员姓名", "unknown", "未知"}:
        error(f"member_name 仍是占位值: {name}")

    if role not in {"member", "admin"}:
        error("role 必须是 member 或 admin。")
        modes: set[str] = set()
    else:
        try:
            modes = allowed_modes(config)
        except SystemExit as exc:
            error(str(exc))
            modes = set()
        if role == "member":
            missing = {"daily", "weekly", "patch"} - modes
            if missing:
                error("member profile 必须允许 daily、weekly、patch，缺少: " + ", ".join(sorted(missing)))
        if role == "admin" and ({"daily", "weekly"} & modes):
            error("admin profile 只能用于手动 patch 贡献，不允许 daily/weekly 自动化。")

    if parse_bool(config.get("synthetic_data", "false")) and not allow_synthetic:
        error("synthetic_data=true 只能用于协议/灰度测试，成员端正式自动化必须关闭。")

    if not submission_api_base_url(config):
        error("submission_api_base_url 不能为空。")
    if ".codex/plugins/cache" in knowledge_repo.as_posix():
        error("knowledge_repo_worktree 不能放在插件缓存目录下。")
    if ".codex/plugins/cache" in out_dir.as_posix():
        error("out_dir 不能放在插件缓存目录下。")
    out_dir_guard_error = artifact_path_guard_error(out_dir, purpose="out_dir")
    if out_dir_guard_error:
        error(out_dir_guard_error)

    git_version = run_command(["git", "--version"])
    if git_version.returncode != 0:
        error("找不到 git，无法检查知识库仓库。")

    freshness = plugin_gate_check(config, fetch=check_remote, require=check_remote)
    if freshness.get("blocking"):
        error(str(freshness.get("message") or "插件更新检查失败。"))
    elif freshness.get("status") == "UNKNOWN":
        warn(str(freshness.get("message") or "无法确认插件是否为最新版本。"))

    if check_remote:
        identity = member_identity if member_identity is not None else member_identity_check(config, check_remote=True)
        if identity.get("status") != "server_confirmed":
            error(str(identity.get("message") or "服务器未确认成员身份。"))

    if not knowledge_repo.exists():
        warn(f"knowledge_repo_worktree 不存在，本地离线搜索不可用: {knowledge_repo}")
    elif not (knowledge_repo / ".git").exists():
        warn(f"knowledge_repo_worktree 已存在但不是 Git 仓库，本地兜底搜索不可用: {knowledge_repo}")

    out_parent = nearest_existing_parent(out_dir.parent)
    if not os.access(out_parent, os.W_OK):
        error(f"out_dir 父目录不可写，无法生成 pending 包: {out_parent}")

    return {
        "status": "FAIL" if errors else "PASS",
        "errors": errors,
        "warnings": warnings,
    }


def doctor(
    config: dict[str, str],
    loaded: list[Path],
    strict: bool = False,
    check_remote: bool = False,
    allow_synthetic: bool = False,
    *,
    plugin_root: Path,
    run_command: RunCommand,
    plugin_gate_check: PluginGateCheck,
) -> dict[str, Any]:
    knowledge_repo = knowledge_repo_worktree(config)
    endpoint = resolve_akbs_endpoint(config)
    public_endpoint = {
        "source": endpoint["source"],
        "submission_api_base_url": endpoint["submission_api_base_url"],
    }
    freshness = plugin_gate_check(config, fetch=check_remote, require=False)
    identity = member_identity_check(config, check_remote=check_remote)
    payload: dict[str, Any] = {
        "skill_root": str(plugin_root),
        "codex_home": default_codex_home(),
        "loaded_config": [str(path) for path in loaded],
        "profile": config.get("profile"),
        "role": config.get("role"),
        "allowed_modes": sorted(allowed_modes(config)),
        "synthetic_data": parse_bool(config.get("synthetic_data", "false")),
        "member_alias": config.get("member_alias"),
        "member_name": config.get("member_name"),
        "akbs_endpoint": public_endpoint,
        "member_identity": identity,
        "submission_api_base_url": submission_api_base_url(config),
        "knowledge_repo_worktree": str(knowledge_repo),
        "knowledge_repo_cloned": (knowledge_repo / ".git").exists(),
        "out_dir": str(expanded_path(config["out_dir"])),
        "git": run_command(["git", "--version"]).stdout.strip(),
        "plugin_freshness": freshness,
        "install_family": freshness.get("install_family", {}),
    }
    if freshness.get("blocking") or (check_remote and identity.get("status") != "server_confirmed"):
        payload["status"] = "FAIL"
    if strict:
        payload["strict"] = doctor_strict_checks(
            config,
            loaded,
            check_remote,
            allow_synthetic,
            run_command=run_command,
            plugin_gate_check=plugin_gate_check,
            member_identity=identity,
        )
        payload["status"] = payload["strict"]["status"]
    return payload
