from __future__ import annotations

import os
import urllib.parse
from pathlib import Path
from typing import Any, Callable

from akbs_member_ops.artifact_paths import require_safe_artifact_path
from akbs_member_ops.member_config import (
    default_codex_home,
    expand_codex_path,
    find_project_report_config,
    load_toml,
)
from akbs_member_ops.member.profile import MemberProfileError, load_member_profile


ROOT_MARKERS = (
    Path("index") / "case-index.jsonl",
    Path("index") / "variant-index.jsonl",
    Path("index") / "symbol-index.jsonl",
    Path("index") / "evidence-index.jsonl",
    Path("index") / "search-docs.jsonl",
)
ENV_PREFIXES = ("CODEX_KNOWLEDGE_", "CODEX_REPORT_", "CODEX_WORK_REPORT_")
AKBS_ENDPOINT_ENV_PREFIXES = ("CODEX_REPORT_AKBS_ENDPOINT_", "CODEX_WORK_REPORT_AKBS_ENDPOINT_")
DEFAULT_AKBS_API_BASE_URL = "http://192.168.100.118:8088"
MEMBER_SEARCH_PATH = "/akbs/api/member/knowledge-search"
MEMBER_MERGE_CONFIRMATIONS_PATH = "/akbs/api/member/me/merge-confirmations"


def member_config_paths() -> list[Path]:
    home = codex_home()
    target = home / "akbs-member-ops.toml"
    if target.exists() or target.is_symlink():
        # A present target is the sole AKBS authority.  Legacy files are not
        # discovered, parsed, or consulted for conflict checks in this mode.
        return [target]
    paths = [
        home / "android-knowledge-search.toml",
        home / "android-knowledge-intake.toml",
        home / "report" / "config.toml",
    ]
    project_config = find_project_report_config()
    if project_config:
        paths.append(project_config)
    return paths


def expand_path(value: str | os.PathLike[str]) -> Path:
    return expand_codex_path(value, resolve=True)


def codex_home() -> Path:
    return expand_path(default_codex_home())


def read_toml(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise ValueError(f"member config must not be a symlink: {path}")
    return load_toml(path, strict=True)


def append_nonempty_text(values: list[str], value: Any) -> None:
    if isinstance(value, str) and value.strip():
        values.append(value.strip())


def selected_profile(payload: dict[str, Any]) -> str:
    for prefix in ENV_PREFIXES:
        value = os.environ.get(f"{prefix}PROFILE")
        if value:
            return value
    value = payload.get("default_profile", "")
    return str(value).strip() if value else ""


def configured_worktree_values(payload: dict[str, Any]) -> list[str]:
    values: list[str] = []
    append_nonempty_text(values, payload.get("knowledge_repo_worktree"))
    knowledge = payload.get("knowledge")
    if isinstance(knowledge, dict):
        append_nonempty_text(values, knowledge.get("worktree"))
        append_nonempty_text(values, knowledge.get("repo_worktree"))
        append_nonempty_text(values, knowledge.get("knowledge_repo_worktree"))
    paths = payload.get("paths")
    if isinstance(paths, dict):
        append_nonempty_text(values, paths.get("knowledge_worktree"))
        append_nonempty_text(values, paths.get("knowledge_repo_worktree"))
    profiles = payload.get("profiles")
    profile = selected_profile(payload)
    if profile and isinstance(profiles, dict):
        profile_payload = profiles.get(profile)
        if isinstance(profile_payload, dict):
            append_nonempty_text(values, profile_payload.get("knowledge_repo_worktree"))
            append_nonempty_text(values, profile_payload.get("knowledge_worktree"))
    return values


def configured_roots() -> list[Path]:
    roots: list[Path] = []
    for env_key in (
        "CODEX_KNOWLEDGE_REPO_WORKTREE",
        "CODEX_REPORT_KNOWLEDGE_REPO_WORKTREE",
    ):
        if os.environ.get(env_key):
            roots.append(expand_path(os.environ[env_key]))

    for path in member_config_paths():
        if not path.exists() and not path.is_symlink():
            continue
        for value in configured_worktree_values(read_toml(path)):
            roots.append(expand_path(value))
    return roots


def config_payloads() -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for path in member_config_paths():
        if path.exists() or path.is_symlink():
            payloads.append(read_toml(path))
    return payloads


def selected_profile_payload(payload: dict[str, Any]) -> dict[str, Any]:
    profile = selected_profile(payload)
    profiles = payload.get("profiles")
    if profile and isinstance(profiles, dict):
        profile_payload = profiles.get(profile)
        if isinstance(profile_payload, dict):
            return profile_payload
    return {}


def configured_endpoint_values(payload: dict[str, Any]) -> dict[str, str]:
    values: dict[str, str] = {}

    def add(key: str, value: Any) -> None:
        if isinstance(value, str) and value.strip():
            values[key] = value.strip()

    def add_section(section: Any) -> None:
        if not isinstance(section, dict):
            return
        add("member_search_url", section.get("member_search_url"))
        add("api_base_url", section.get("api_base_url"))

    add("member_search_url", payload.get("member_search_url"))
    add("api_base_url", payload.get("api_base_url"))
    add_section(payload.get("akbs_endpoint"))
    add_section(payload.get("endpoint"))

    profile_payload = selected_profile_payload(payload)
    role = str(profile_payload.get("role") or payload.get("role") or "").strip()
    if role == "admin":
        add("member_search_url", profile_payload.get("member_search_url"))
        add("api_base_url", profile_payload.get("api_base_url"))
        add_section(profile_payload.get("akbs_endpoint"))
        add_section(profile_payload.get("endpoint"))
    return values


def configured_out_dir_values(payload: dict[str, Any]) -> list[str]:
    values: list[str] = []
    append_nonempty_text(values, payload.get("out_dir"))
    paths = payload.get("paths")
    if isinstance(paths, dict):
        append_nonempty_text(values, paths.get("out_dir"))
    profiles = payload.get("profiles")
    profile = selected_profile(payload)
    if profile and isinstance(profiles, dict):
        profile_payload = profiles.get(profile)
        if isinstance(profile_payload, dict):
            append_nonempty_text(values, profile_payload.get("out_dir"))
    return values


def search_usage_root(config_payloads_fn: Callable[[], list[dict[str, Any]]] = config_payloads) -> Path:
    # Configured legacy out_dir values remain readable inputs, never target write destinations.
    config_payloads_fn()
    return require_safe_artifact_path(
        codex_home() / "artifacts" / "akbs-member-ops" / "search-usage",
        purpose="search usage output",
    )


def selected_member_alias() -> tuple[str, str]:
    # Identity has one resolver.  In particular, repository-controlled
    # .codex/report.toml and MEMBER_ALIAS environment variables may configure
    # neither an alias nor profile selection.
    try:
        profile = load_member_profile()
    except MemberProfileError as exc:
        raise ValueError(str(exc)) from exc
    return profile.profile, profile.member_alias


def akbs_endpoint_env_value(name: str) -> str:
    for prefix in AKBS_ENDPOINT_ENV_PREFIXES:
        value = os.environ.get(f"{prefix}{name.upper()}")
        if value:
            return value.strip()
    return ""


def member_search_endpoint_url() -> tuple[str, str]:
    explicit = akbs_endpoint_env_value("MEMBER_SEARCH_URL")
    if explicit:
        return explicit, "env_override"
    env_base = akbs_endpoint_env_value("API_BASE_URL")
    if env_base:
        return env_base.rstrip("/") + MEMBER_SEARCH_PATH, "env_override"
    for payload in config_payloads():
        configured = configured_endpoint_values(payload)
        if configured.get("member_search_url"):
            return configured["member_search_url"], "admin_config_override"
        if configured.get("api_base_url"):
            return configured["api_base_url"].rstrip("/") + MEMBER_SEARCH_PATH, "admin_config_override"
    base = DEFAULT_AKBS_API_BASE_URL
    return base.rstrip("/") + MEMBER_SEARCH_PATH, "default"


def member_api_base_url() -> tuple[str, str]:
    env_base = akbs_endpoint_env_value("API_BASE_URL")
    if env_base:
        return env_base.rstrip("/"), "env_override"
    for payload in config_payloads():
        configured = configured_endpoint_values(payload)
        if configured.get("api_base_url"):
            return configured["api_base_url"].rstrip("/"), "admin_config_override"
    search_url, source = member_search_endpoint_url()
    if search_url.endswith(MEMBER_SEARCH_PATH):
        return search_url[: -len(MEMBER_SEARCH_PATH)].rstrip("/"), source
    parsed = urllib.parse.urlsplit(search_url)
    if parsed.scheme and parsed.netloc:
        return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", "")).rstrip("/"), source
    return DEFAULT_AKBS_API_BASE_URL, "default"


def member_merge_confirmations_url(
    confirmation_id: str = "", action: str = "", *, page: int = 1, page_size: int = 50,
) -> str:
    base, _source = member_api_base_url()
    path = MEMBER_MERGE_CONFIRMATIONS_PATH
    if confirmation_id:
        path += "/" + urllib.parse.quote(confirmation_id, safe="")
    if action:
        path += "/" + action.strip("/")
    if not confirmation_id:
        path += "?" + urllib.parse.urlencode({"page": page, "page_size": page_size})
    return base.rstrip("/") + path
