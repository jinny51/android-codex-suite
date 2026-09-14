from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable

try:
    from .config import local_now
except ImportError:  # pragma: no cover - direct script import fallback
    scripts_root = Path(__file__).resolve().parents[1]
    if str(scripts_root) not in sys.path:
        sys.path.insert(0, str(scripts_root))
    from akbs_intake.config import local_now


RunCommand = Callable[[list[str]], Any]
MetadataLoader = Callable[[], dict[str, str]]
VersionGate = Callable[[dict[str, str], bool, bool], dict[str, Any]]


def plugin_commit(plugin_root: Path, run_command: RunCommand) -> str:
    cp = run_command(["git", "-C", str(plugin_root), "rev-parse", "--short", "HEAD"])
    if cp.returncode == 0:
        return cp.stdout.strip()
    return ""


def source_metadata(
    config: dict[str, str],
    skill: str,
    *,
    plugin_root: Path,
    run_command: RunCommand,
    plugin_install_metadata_fn: MetadataLoader,
    plugin_version_gate_check_fn: VersionGate,
    last_plugin_version_gate: dict[str, Any] | None,
) -> dict[str, Any]:
    metadata = plugin_install_metadata_fn()
    plugin_version = metadata.get("plugin_version") or ""
    root_cp = run_command(["git", "-C", str(plugin_root), "rev-parse", "--show-toplevel"])
    plugin_installation = "git" if root_cp.returncode == 0 else metadata.get("plugin_installation", "unknown")
    gate = last_plugin_version_gate or plugin_version_gate_check_fn(config, False, False)
    skill_cache_version = str(gate.get("skill_cache_version") or plugin_version)
    remote_plugin_version = str(gate.get("remote_plugin_version") or gate.get("remote_version") or "")
    installed_plugin_version = str(gate.get("installed_plugin_version") or plugin_version)
    return {
        "source": "akbs-member-ops",
        "tool": skill,
        "skill": skill,
        "skill_version": skill_cache_version or plugin_version,
        "plugin_name": metadata.get("plugin_name") or "akbs-member-ops",
        "plugin_version": plugin_version,
        "plugin_installation": plugin_installation,
        "plugin_commit": plugin_commit(plugin_root, run_command),
        "installed_plugin_version": installed_plugin_version,
        "remote_plugin_version": remote_plugin_version,
        "skill_cache_version": skill_cache_version,
        "plugin_version_check": {
            "checked_at": gate.get("checked_at", local_now(config).isoformat()),
            "result": gate.get("result") or gate.get("status"),
            "status": gate.get("status"),
            "blocking": bool(gate.get("blocking")),
            "message": gate.get("message", ""),
            "plugin_version": gate.get("plugin_version") or plugin_version,
            "installed_plugin_version": installed_plugin_version,
            "remote_plugin_version": remote_plugin_version,
            "skill_cache_version": skill_cache_version,
            "skill_cache_path": gate.get("skill_cache_path", ""),
            "auto_update": gate.get("auto_update", {}),
        },
        "member_alias": config["member_alias"],
        "generated_at": local_now(config).isoformat(),
    }
