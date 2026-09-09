"""Shared marketplace update primitives, packaged unchanged in both core plugins.

This module owns transport and update execution, not task triggers or install-family
policy. Callers verify their local installation before using it and provide the
same plugin-specific verifier for the refreshed installation.
"""

from __future__ import annotations

import json
import math
import re
import shlex
import subprocess
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any


MARKETPLACE = "android-codex-suite"
UPDATABLE_PLUGINS = frozenset({"akbs-member-ops", "android-engineering-ops"})
PLUGIN_VERSION_RE = re.compile(r"^[0-9]+(?:\.[0-9]+){1,3}(?:[-+][0-9A-Za-z.-]+)?$")
MAX_MANIFEST_BYTES = 1024 * 1024
REMOTE_MANIFEST_TIMEOUT = 6
UPDATE_COMMAND_TIMEOUT = 60
MAX_COMMAND_OUTPUT_CHARS = 16000


def version_parts(value: str) -> tuple[int, ...]:
    text = str(value or "")
    if not PLUGIN_VERSION_RE.fullmatch(text):
        raise ValueError(f"malformed plugin version: {text!r}")
    release = re.split(r"[-+]", text, maxsplit=1)[0]
    return tuple(int(item) for item in release.split("."))


def compare_versions(left: str, right: str) -> int:
    left_parts = list(version_parts(left))
    right_parts = list(version_parts(right))
    size = max(len(left_parts), len(right_parts), 1)
    left_parts.extend([0] * (size - len(left_parts)))
    right_parts.extend([0] * (size - len(right_parts)))
    return (left_parts > right_parts) - (left_parts < right_parts)


def github_manifest_url(repository: str, plugin_name: str) -> str:
    """Build one main-branch manifest URL from an exact GitHub repository URL."""
    repository = str(repository or "").strip().removesuffix("/").removesuffix(".git")
    match = re.fullmatch(
        r"(?:https://github\.com/|ssh://git@github\.com/|git@github\.com:)"
        r"(?P<owner>[A-Za-z0-9][A-Za-z0-9._-]*)/(?P<repo>[A-Za-z0-9][A-Za-z0-9._-]*)",
        repository,
    )
    if not match or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", plugin_name):
        return ""
    return (
        f"https://raw.githubusercontent.com/{match['owner']}/{match['repo']}"
        f"/main/plugins/{plugin_name}/.codex-plugin/plugin.json"
    )


def fetch_manifest(
    url: str,
    timeout: float = REMOTE_MANIFEST_TIMEOUT,
    *,
    opener: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Read a bounded strict JSON object; the caller validates plugin identity."""
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("manifest timeout must be finite and positive")
    open_url = opener if opener is not None else urllib.request.urlopen
    with open_url(url, timeout=timeout) as response:
        raw = response.read(MAX_MANIFEST_BYTES + 1)
    if len(raw) > MAX_MANIFEST_BYTES:
        raise ValueError(f"remote plugin manifest exceeds {MAX_MANIFEST_BYTES} bytes")

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for key, value in pairs:
            if key in payload:
                raise ValueError(f"remote plugin manifest repeats key: {key}")
            payload[key] = value
        return payload

    def reject_non_finite(value: str) -> None:
        raise ValueError(f"remote plugin manifest contains non-finite number: {value}")

    payload = json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=unique_object,
        parse_constant=reject_non_finite,
    )
    if not isinstance(payload, dict):
        raise ValueError("remote plugin manifest must be a JSON object")
    return payload


def _output(value: Any) -> str:
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return str(value or "").strip()[:MAX_COMMAND_OUTPUT_CHARS]


def update_plugin(
    plugin_name: str,
    *,
    run_command: Callable[..., Any],
    verify_install: Callable[[], Mapping[str, Any]],
    expected_version: str | None = None,
) -> dict[str, Any]:
    """Refresh the official marketplace, install one plugin, then verify it.

The verifier must inspect fresh active inventory and the exact installed cache,
returning installed_plugin_active/version/path only after local identity and
content checks pass. A command returning zero is never installation evidence.
"""
    result: dict[str, Any] = {"attempted": False, "status": "FAIL", "restart_required": False}
    if plugin_name not in UPDATABLE_PLUGINS:
        return {**result, "reason": "unsupported_plugin", "message": "Unsupported plugin update target."}
    if expected_version is not None:
        try:
            version_parts(expected_version)
        except ValueError as exc:
            return {**result, "reason": "invalid_expected_version", "message": str(exc)}
    upgrade = ["codex", "plugin", "marketplace", "upgrade", MARKETPLACE, "--json"]
    install = ["codex", "plugin", "add", f"{plugin_name}@{MARKETPLACE}", "--json"]
    result.update({"attempted": True, "upgrade_command": shlex.join(upgrade)})
    for stage, command in (("marketplace", upgrade), ("install", install)):
        if stage == "install":
            result["install_command"] = shlex.join(install)
        try:
            completed = run_command(command, timeout=UPDATE_COMMAND_TIMEOUT)
        except (OSError, subprocess.TimeoutExpired) as exc:
            result.update(
                {"reason": f"{stage}_command_failed", "message": str(exc),
                 "stdout": _output(getattr(exc, "stdout", "")),
                 "stderr": _output(getattr(exc, "stderr", ""))}
            )
            return result
        if completed.returncode != 0:
            result.update(
                {"reason": f"{stage}_command_failed", "stdout": _output(completed.stdout),
                 "stderr": _output(completed.stderr)}
            )
            return result
        result[f"{stage}_stdout"] = _output(completed.stdout)
    try:
        installation = verify_install()
    except Exception as exc:
        return {**result, "reason": "install_verification_failed", "message": str(exc)}
    if not isinstance(installation, Mapping):
        return {**result, "reason": "install_verification_failed", "message": "Installation verifier returned no metadata."}
    # Copy installation evidence without allowing it to overwrite update status.
    result.update({key: value for key, value in installation.items() if key.startswith("installed_plugin_")})
    if isinstance(installation.get("install_family"), Mapping):
        result["install_family"] = dict(installation["install_family"])
    family = installation.get("install_family")
    if (
        installation.get("blocking")
        or installation.get("status") not in (None, "PASS")
        or (isinstance(family, Mapping) and (family.get("blocking") or family.get("status") != "PASS"))
        or installation.get("installed_plugin_active") is not True
        or installation.get("installed_plugin_fallback")
        or installation.get("installed_plugin_ambiguous")
        or not installation.get("installed_plugin_path")
    ):
        return {**result, "reason": "install_verification_failed", "message": str(installation.get("message") or "Updated plugin is not a verified active installation.")}
    version = installation.get("installed_plugin_version")
    try:
        if not isinstance(version, str):
            raise ValueError("updated plugin has no installed version")
        version_parts(version)
        if expected_version is not None and compare_versions(version, expected_version) < 0:
            raise ValueError(f"installed plugin {version} is older than expected {expected_version}")
    except ValueError as exc:
        return {**result, "reason": "installed_version_mismatch", "message": str(exc)}
    result.update(
        {"status": "PASS", "restart_required": True,
         "message": "Plugin files were updated and verified. Restart the task/session to load the updated Skill instructions."}
    )
    return result
