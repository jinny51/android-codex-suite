from __future__ import annotations

import datetime as dt
import os
import sys
from pathlib import Path
from typing import Any

try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:  # pragma: no cover
    ZoneInfo = None
    ZoneInfoNotFoundError = KeyError


PLUGIN_ROOT = Path(__file__).resolve().parents[4]
OPS_PLUGIN_LIB = Path(__file__).resolve().parents[4] / "lib"
if OPS_PLUGIN_LIB.is_dir() and str(OPS_PLUGIN_LIB) not in sys.path:
    sys.path.insert(0, str(OPS_PLUGIN_LIB))

from akbs_member_ops.artifact_paths import artifact_path_guard_error, require_safe_artifact_path
from akbs_member_ops.member_config import (
    default_codex_home,
    expand_codex_path,
    find_project_report_config,
    load_toml,
    parse_bool,
    parse_simple_toml,
    parse_toml_scalar,
)
from akbs_member_ops.member.profile import MemberProfileError, load_member_profile
from akbs_intake.diagnostics import warn_local_input

INCOMING_SCHEMA_VERSION = "2"
ENV_PREFIXES = ("CODEX_REPORT_", "CODEX_WORK_REPORT_")
DEFAULT_SUBMISSION_API_BASE_URL = "http://192.168.100.118:8088/akbs/api"
AKBS_ENDPOINT_ENV_PREFIXES = ("CODEX_REPORT_AKBS_ENDPOINT_", "CODEX_WORK_REPORT_AKBS_ENDPOINT_")
AKBS_ENDPOINT_DEFAULTS = {
    "submission_api_base_url": DEFAULT_SUBMISSION_API_BASE_URL,
}

CONFIG_DEFAULTS = {
    "default_profile": "",
    "profile": "",
    "role": "",
    "allowed_modes": "",
    "member_alias": "",
    "member_name": "",
    "knowledge_repo_worktree": "",
    "git_user_name": "",
    "git_user_email": "",
    "codex_home": "$CODEX_HOME",
    "out_dir": "$CODEX_HOME/artifacts/akbs-member-ops",
    "incoming_schema_version": "2",
    "include_patches": "true",
    "max_attachment_mb": "5",
    "timezone": "Asia/Shanghai",
    "synthetic_data": "false",
    "synthetic_item_count": "3",
    "weekly_history_api_enabled": "true",
    "weekly_history_api_timeout_seconds": "3",
}


def expanded_path(value: str) -> Path:
    return expand_codex_path(value)


def artifact_read_roots(config: dict[str, str]) -> list[Path]:
    """Return the target artifact root first, then the immutable legacy root."""
    roots = [
        expanded_path(config.get("out_dir") or CONFIG_DEFAULTS["out_dir"]),
        expanded_path("$CODEX_HOME/artifacts/android-knowledge-intake"),
    ]
    result: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root.resolve())
        if key not in seen:
            seen.add(key)
            result.append(root)
    return result


def synthetic_mode(config: dict[str, str]) -> bool:
    return parse_bool(config.get("synthetic_data", "false"))


def read_toml(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise SystemExit(f"配置文件不允许使用符号链接: {path}")
    try:
        return load_toml(path, strict=True)
    except ValueError as exc:
        raise SystemExit(f"读取配置失败: {path}: {exc}") from exc


def stringify_config_value(value: Any) -> str:
    if isinstance(value, list):
        return ",".join(str(item) for item in value)
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def flatten_config_payload(payload: dict[str, Any]) -> dict[str, str]:
    flattened: dict[str, str] = {}

    def add(key: str, value: Any, section: str = "") -> None:
        if isinstance(value, dict):
            return
        normalized = key
        if section == "member" and key == "alias":
            normalized = "member_alias"
        elif section == "member" and key == "name":
            normalized = "member_name"
        elif section == "knowledge" and key in {"repo_worktree", "worktree", "knowledge_repo_worktree"}:
            normalized = "knowledge_repo_worktree"
        elif section == "paths" and key in {"knowledge_repo_worktree", "knowledge_worktree"}:
            normalized = "knowledge_repo_worktree"
        elif section == "paths" and key in {"codex_home", "out_dir"}:
            normalized = key
        elif section == "git" and key in {"user_name", "name"}:
            normalized = "git_user_name"
        elif section == "git" and key in {"user_email", "email"}:
            normalized = "git_user_email"
        elif key == "person":
            normalized = "member_name"
        elif key == "name":
            normalized = "member_name"
        if normalized in CONFIG_DEFAULTS:
            flattened[normalized] = stringify_config_value(value)

    for key, value in payload.items():
        if key == "profiles":
            continue
        if isinstance(value, dict):
            for subkey, subvalue in value.items():
                add(str(subkey), subvalue, str(key))
        else:
            add(str(key), value)
    return flattened


def profile_configs(payload: dict[str, Any]) -> dict[str, dict[str, str]]:
    profiles = payload.get("profiles")
    if not isinstance(profiles, dict):
        return {}
    result: dict[str, dict[str, str]] = {}
    for name, profile_payload in profiles.items():
        if isinstance(profile_payload, dict):
            result[str(name)] = flatten_config_payload(profile_payload)
    return result


def apply_env_overrides(
    config: dict[str, str], *, allow_identity_override: bool = True
) -> None:
    aliases = {
        "MEMBER": "member_alias",
        "ALIAS": "member_alias",
        "PERSON": "member_name",
        "PERSON_NAME": "member_name",
        "MEMBER_NAME": "member_name",
        "KNOWLEDGE_WORKTREE": "knowledge_repo_worktree",
        "KNOWLEDGE_REPO_WORKTREE": "knowledge_repo_worktree",
    }
    for env_key, value in os.environ.items():
        for prefix in ENV_PREFIXES:
            if not env_key.startswith(prefix):
                continue
            raw = env_key[len(prefix) :]
            key = aliases.get(raw, raw.lower())
            if not allow_identity_override and key in {"member_alias", "member_name"}:
                continue
            if key in config:
                config[key] = value


def akbs_endpoint_env_value(name: str) -> str:
    for prefix in AKBS_ENDPOINT_ENV_PREFIXES:
        value = os.environ.get(f"{prefix}{name.upper()}")
        if value:
            return value
    return ""


def resolve_akbs_endpoint(config: dict[str, str]) -> dict[str, str]:
    endpoint = dict(AKBS_ENDPOINT_DEFAULTS)
    endpoint["source"] = "default"
    env_keys = {
        "submission_api_base_url": "SUBMISSION_API_BASE_URL",
    }
    env_overrides = {key: akbs_endpoint_env_value(env_key) for key, env_key in env_keys.items()}
    env_overrides = {key: value for key, value in env_overrides.items() if value}
    if env_overrides:
        endpoint.update(env_overrides)
        if "submission_api_base_url" in env_overrides:
            endpoint["source"] = "env_override"

    return endpoint


def load_config(profile_override: str | None = None) -> tuple[dict[str, str], list[Path]]:
    config = CONFIG_DEFAULTS.copy()
    codex_home = Path(default_codex_home())
    packaged_config = PLUGIN_ROOT / "internal" / "incoming-v2" / "config.toml"
    target_config = codex_home / "akbs-member-ops.toml"
    target_authoritative = target_config.exists() or target_config.is_symlink()
    try:
        identity = load_member_profile(profile_override)
    except MemberProfileError as exc:
        raise SystemExit(f"成员身份配置无效: {exc}") from exc
    if target_authoritative:
        # Presence, not successful parsing, selects the sole AKBS authority.
        # Do not even discover project/legacy configuration in this branch.
        paths = [packaged_config, target_config]
    else:
        legacy_paths = [
            codex_home / "android-knowledge-intake.toml",
            codex_home / "android-knowledge-search.toml",
            codex_home / "report" / "config.toml",
        ]
        project_config = find_project_report_config()
        if project_config:
            legacy_paths.append(project_config)
        paths = [packaged_config, *legacy_paths]

    loaded: list[Path] = []
    loaded_payloads: dict[Path, dict[str, Any]] = {}
    profile_overrides: dict[str, dict[str, str]] = {}
    for path in paths:
        if not path.exists() and not path.is_symlink():
            continue
        loaded.append(path)
        payload = read_toml(path)
        loaded_payloads[path] = payload
        for key, value in flatten_config_payload(payload).items():
            config[key] = value
        for name, values in profile_configs(payload).items():
            profile_overrides.setdefault(name, {}).update(values)

    selected_profile = identity.profile if identity.profile != "standalone" else ""
    if selected_profile:
        if selected_profile not in profile_overrides:
            raise SystemExit(f"profile 不存在: {selected_profile}")
        config.update(profile_overrides[selected_profile])
        config["profile"] = selected_profile
    apply_env_overrides(config, allow_identity_override=False)
    config["member_alias"] = identity.member_alias
    config["member_name"] = identity.member_name
    config["timezone"] = identity.timezone
    # Target writers never relocate or overwrite the permanent legacy artifact root.
    config["out_dir"] = CONFIG_DEFAULTS["out_dir"]
    return config, loaded


def require_config(config: dict[str, str]) -> None:
    missing = [key for key in ("member_alias", "member_name") if not config.get(key, "").strip()]
    if not submission_api_base_url(config):
        missing.append("submission_api_base_url")
    if missing:
        raise SystemExit("缺少必要配置: " + ", ".join(missing))


def knowledge_repo_worktree(config: dict[str, str]) -> Path:
    value = config.get("knowledge_repo_worktree") or "$CODEX_HOME/worktrees/knowledge"
    return expanded_path(value)


def submission_api_base_url(config: dict[str, str]) -> str:
    return resolve_akbs_endpoint(config)["submission_api_base_url"].strip()


def allowed_modes(config: dict[str, str]) -> set[str]:
    raw = config.get("allowed_modes", "").strip()
    if not raw:
        return {"daily", "weekly", "patch"}
    modes = {item.strip() for item in raw.split(",") if item.strip()}
    invalid = modes - {"daily", "weekly", "patch"}
    if invalid:
        raise SystemExit("allowed_modes 包含非法类型: " + ", ".join(sorted(invalid)))
    return modes


def enforce_mode_allowed(config: dict[str, str], report_type: str) -> None:
    modes = allowed_modes(config)
    if report_type not in modes:
        profile = config.get("profile") or config.get("member_alias") or "<unknown>"
        raise SystemExit(f"profile {profile} 不允许执行 {report_type}，允许类型: {', '.join(sorted(modes))}")


def local_now(config: dict[str, str]) -> dt.datetime:
    tz_name = config.get("timezone", "Asia/Shanghai")
    if ZoneInfo is not None:
        try:
            return dt.datetime.now(ZoneInfo(tz_name))
        except (ZoneInfoNotFoundError, TypeError, ValueError):
            warn_local_input("timezone_invalid", "config.timezone")
            try:
                return dt.datetime.now(ZoneInfo(CONFIG_DEFAULTS["timezone"]))
            except (ZoneInfoNotFoundError, TypeError, ValueError):
                warn_local_input("timezone_default_unavailable", "config.default_timezone")
    return dt.datetime.now()


def parse_date_arg(value: str | None, config: dict[str, str]) -> dt.date:
    if value:
        return dt.date.fromisoformat(value)
    return local_now(config).date()
