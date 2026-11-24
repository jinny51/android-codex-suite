"""Shared marketplace update primitives, packaged unchanged in both core plugins.

This module owns transport and update execution, not task triggers or install-family
policy. Callers verify their local installation before using it and provide the
same plugin-specific verifier for the refreshed installation.
"""

from __future__ import annotations

import errno
import json
import math
import os
import platform
import re
import shutil
import subprocess
import socket
import ssl
import sys
import tempfile
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any


MARKETPLACE = "android-codex-suite"
UPDATABLE_PLUGINS = frozenset({"akbs-member-ops", "android-engineering-ops"})
PLUGIN_VERSION_RE = re.compile(r"^[0-9]+(?:\.[0-9]+){1,3}(?:[-+][0-9A-Za-z.-]+)?$")
MAX_MANIFEST_BYTES = 1024 * 1024
REMOTE_MANIFEST_TIMEOUT = 6
UPDATE_COMMAND_TIMEOUT = 60
MAX_COMMAND_OUTPUT_CHARS = 16000


def manifest_transport_unavailable(error: BaseException) -> bool:
    """Only unavailable transport, never a received or untrusted manifest."""
    if isinstance(error, (urllib.error.HTTPError, ssl.SSLError)):
        return False
    if isinstance(error, urllib.error.URLError):
        return isinstance(error.reason, BaseException) and manifest_transport_unavailable(error.reason)
    return isinstance(error, (TimeoutError, ConnectionError, socket.gaierror)) or (
        isinstance(error, OSError)
        and error.errno in {errno.ENETDOWN, errno.ENETUNREACH, errno.EHOSTUNREACH}
    )


def _linux_codex_runtime_executable() -> str | None:
    """Find the executing Codex app-server or sandbox binary."""
    pid = os.getppid()
    for _ in range(32):
        if pid < 1:
            break
        process = Path("/proc") / str(pid)
        try:
            parent = next(
                line for line in (process / "status").read_text().splitlines()
                if line.startswith("PPid:")
            )
            executable = (process / "exe").readlink()
            if executable.name == "codex":
                argv = (process / "cmdline").read_bytes().split(b"\0")
                if (b"app-server" in argv[1:] or argv[0] == b"codex-linux-sandbox") and executable.is_file():
                    return str(executable)
            pid = int(parent.partition(":")[2])
        except (OSError, StopIteration, ValueError):
            break
    return None


def _windows_codex_executable() -> str:
    native = shutil.which("codex.exe")
    if native:
        return native

    # The official npm installation may add only its .cmd shim to PATH.
    # Run the native dependency directly so all callers keep shell=False.
    shim = shutil.which("codex.cmd")
    target = {"amd64": ("x64", "x86_64"), "x86_64": ("x64", "x86_64"),
              "arm64": ("arm64", "aarch64"), "aarch64": ("arm64", "aarch64")}.get(platform.machine().casefold())
    if shim and target:
        shim_dir = Path(shim).parent
        modules = shim_dir.parent if shim_dir.name == ".bin" else shim_dir / "node_modules"
        architecture, triplet = target
        windows_package = f"codex-win32-{architecture}"
        vendor = Path("vendor") / f"{triplet}-pc-windows-msvc"
        npm_codex = modules / "@openai" / "codex"
        for package in (
            npm_codex / "node_modules" / "@openai" / windows_package,
            modules / "@openai" / windows_package,
            npm_codex,
        ):
            for relative in (Path("bin/codex.exe"), Path("codex/codex.exe")):
                executable = package / vendor / relative
                if executable.is_file():
                    return str(executable.resolve())
    raise FileNotFoundError("Native Windows codex.exe was not found on PATH or in the Codex npm installation.")


def _wsl_codex_executable(executable: str) -> str:
    windows_npm_shim = re.compile(r"/mnt/[a-z]/.*?/appdata/roaming/npm/codex", re.IGNORECASE)

    def is_windows_shim(path: Path) -> bool:
        return bool(windows_npm_shim.fullmatch(path.as_posix()) or windows_npm_shim.fullmatch(path.resolve().as_posix()))

    from_path = shutil.which(executable)
    if from_path and is_windows_shim(Path(from_path)):
        native_search_path = os.pathsep.join(
            entry for entry in os.get_exec_path() if not is_windows_shim(Path(entry) / executable)
        )
        from_path = shutil.which(executable, path=native_search_path) if native_search_path else None
    if from_path:
        return from_path
    desktop = Path.home() / ".local/share/codex/desktop-cli/public/current/codex"
    if desktop.is_file() and os.access(desktop, os.X_OK):
        return str(desktop.resolve())
    raise FileNotFoundError("Use a native Linux Codex in WSL; the Windows npm Codex shim cannot run here.")


def resolve_codex_executable(executable: str = "codex") -> str:
    """Choose the native CLI without borrowing another host's npm shim."""
    if executable != "codex":
        return executable
    if os.name == "nt":
        return _windows_codex_executable()
    if sys.platform.startswith("linux"):
        server = _linux_codex_runtime_executable()
        if server:
            return server
        try:
            kernel = Path("/proc/sys/kernel/osrelease").read_text(encoding="ascii").casefold()
        except OSError:
            return executable
        if "microsoft" in kernel or "wsl" in kernel:
            return _wsl_codex_executable(executable)
    return executable


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


OFFICIAL_REPOSITORY = "https://github.com/jinny51/android-codex-suite.git"
EXACT_RELEASE = re.compile(r"[0-9a-f]{40}")
PLUGIN_CACHE_RELATIVE = Path("plugins/cache") / MARKETPLACE


class UpdateError(RuntimeError):
    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


def _command(run_command: Callable[..., Any], stage: str, *command: str, env: dict[str, str] | None = None) -> str:
    options: dict[str, Any] = {"timeout": UPDATE_COMMAND_TIMEOUT}
    if env is not None:
        options["env"] = env
    try:
        result = run_command(list(command), **options)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UpdateError(f"{stage}_command_failed", str(exc)) from exc
    if result.returncode != 0:
        raise UpdateError(f"{stage}_command_failed", _output(result.stderr) or _output(result.stdout) or stage)
    output = result.stdout
    return output.decode("utf-8") if isinstance(output, bytes) else output


def _catalog(run_command: Callable[..., Any]) -> Mapping[str, Any] | None:
    rows = json.loads(_command(run_command, "inventory", "codex", "plugin", "marketplace", "list", "--json"))["marketplaces"]
    matches = [row for row in rows if row.get("name") == MARKETPLACE]
    if len(matches) > 1:
        raise UpdateError("ambiguous_marketplace", "The official marketplace is registered more than once.")
    return matches[0] if matches else None


def _installed(run_command: Callable[..., Any]) -> dict[str, str]:
    rows = json.loads(_command(run_command, "inventory", "codex", "plugin", "list", "--marketplace", MARKETPLACE, "--json"))["installed"]
    result: dict[str, str] = {}
    for row in rows:
        if row.get("installed") is not True or row.get("enabled") is not True:
            continue
        name, _, market = str(row.get("pluginId", "")).partition("@")
        if market != MARKETPLACE:
            continue
        if name in result or not re.fullmatch(r"[a-z0-9][a-z0-9.-]*", name):
            raise UpdateError("invalid_inventory", "The marketplace has ambiguous enabled plugins.")
        version = row.get("version")
        version_parts(version)
        result[name] = version
    return result


@contextmanager
def _update_lock(home: Path):
    # Both copies of this module must serialize the same Codex back end.
    path = home / ".akbs-marketplace-update.lock"
    if path.is_symlink():
        raise UpdateError("invalid_update_lock", "The marketplace update lock is not a regular file.")
    fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "r+b") as stream:
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise UpdateError("update_busy", "Another AKBS plugin update is in progress.") from exc
        yield


def _mirror_transport(repo: str, mirror: Path) -> dict[str, str]:
    env = dict(os.environ)
    index = int(env.get("GIT_CONFIG_COUNT", "0"))
    if index < 0:
        raise ValueError("invalid Git configuration count")
    env.update({
        "GIT_CONFIG_COUNT": str(index + 1),
        f"GIT_CONFIG_KEY_{index}": f"url.{mirror.as_uri()}.insteadOf",
        f"GIT_CONFIG_VALUE_{index}": repo,
        "GIT_ALLOW_PROTOCOL": "file",
        "GIT_TERMINAL_PROMPT": "0",
    })
    return env


def _pin_catalog(run_command: Callable[..., Any], repo: str, ref: str, env: dict[str, str]) -> None:
    if _catalog(run_command) is not None:
        _command(run_command, "marketplace_remove", "codex", "plugin", "marketplace", "remove", MARKETPLACE, "--json", env=env)
    _command(run_command, "marketplace_add", "codex", "plugin", "marketplace", "add", repo, "--ref", ref, "--json", env=env)


def _install(run_command: Callable[..., Any], names: list[str], env: dict[str, str]) -> None:
    for name in names:
        _command(run_command, "install", "codex", "plugin", "add", f"{name}@{MARKETPLACE}", "--json", env=env)


def _verify_enabled(run_command: Callable[..., Any], home: Path, versions: dict[str, str], *, skip: str = "") -> None:
    if _installed(run_command) != versions:
        raise UpdateError("installed_version_mismatch", "Codex did not select all intended installed versions.")
    for name in sorted((versions.keys() & UPDATABLE_PLUGINS) - {skip}):
        root = home / PLUGIN_CACHE_RELATIVE / name / versions[name]
        if name == "akbs-member-ops":
            args = [str(root / "skills/akbs-member-setup/scripts/akbs_member_setup.py"), "preflight-install-family"]
        else:
            args = [str(root / "lib/android_engineering_ops/install_family.py"), "--plugin-root", str(root)]
        _command(run_command, "install_verification", sys.executable, "-B", *args)


def _verified_evidence(
    verify_install: Callable[[], Mapping[str, Any]], version: str,
) -> dict[str, Any]:
    try:
        installation = verify_install()
    except Exception as exc:
        raise UpdateError("install_verification_failed", str(exc)) from exc
    if not isinstance(installation, Mapping):
        raise UpdateError("install_verification_failed", "Installation verifier returned no metadata.")
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
        raise UpdateError("install_verification_failed", str(installation.get("message") or "Plugin is not a verified active installation."))
    if installation.get("installed_plugin_version") != version:
        raise UpdateError("installed_version_mismatch", f"Verified plugin version differs from staged version {version}.")
    evidence = {key: value for key, value in installation.items() if key.startswith("installed_plugin_")}
    if isinstance(family, Mapping):
        evidence["install_family"] = dict(family)
    return evidence


def _stage_release(
    run_command: Callable[..., Any], mirror: Path, source: Path, repo: str, versions: dict[str, str],
) -> tuple[str, str, dict[str, str]]:
    _command(run_command, "stage", "git", "init", "--bare", str(mirror))
    def git(*args: str) -> str:
        return _command(run_command, "stage", "git", "-C", str(mirror), *args).strip()

    git("fetch", "--no-tags", "--depth=1", str(source), "HEAD:refs/heads/previous")
    git("fetch", "--no-tags", "--depth=1", repo, "refs/heads/main:refs/heads/release")
    previous = git("rev-parse", "refs/heads/previous")
    release = git("rev-parse", "refs/heads/release")
    if not EXACT_RELEASE.fullmatch(previous) or not EXACT_RELEASE.fullmatch(release):
        raise UpdateError("invalid_release_commit", "Git returned an invalid publication identity.")

    target = versions.copy()
    for name, old in versions.items():
        path = f"plugins/{name}"
        manifest_path = f"{path}/.codex-plugin/plugin.json"
        manifest = json.loads(git("show", f"{previous}:{manifest_path}"))
        if manifest.get("name") != name or manifest.get("version") != old:
            raise UpdateError("inconsistent_previous_install", f"The current publication does not match installed {name} {old}.")
        if git("rev-parse", f"{previous}:{path}") == git("rev-parse", f"{release}:{path}"):
            continue
        if name not in UPDATABLE_PLUGINS:
            raise UpdateError("unsupported_companion_update", f"Enabled plugin {name} also changed and requires its ordinary update.")
        manifest = json.loads(git("show", f"{release}:{manifest_path}"))
        new = manifest.get("version")
        if manifest.get("name") != name or compare_versions(new, old) <= 0:
            raise UpdateError("invalid_release_version", f"The new publication of {name} lacks a newer valid version.")
        target[name] = new
    return previous, release, target


def update_plugin(
    plugin_name: str,
    *,
    run_command: Callable[..., Any],
    verify_install: Callable[[], Mapping[str, Any]],
    expected_version: str | None = None,
    codex_home: Path | None = None,
) -> dict[str, Any]:
    """Stage two official snapshots before touching the installed marketplace."""
    result: dict[str, Any] = {"attempted": False, "status": "FAIL", "restart_required": False}
    if plugin_name not in UPDATABLE_PLUGINS:
        return {**result, "reason": "unsupported_plugin", "message": "Unsupported plugin update target."}
    if expected_version is not None:
        try:
            version_parts(expected_version)
        except ValueError as exc:
            return {**result, "reason": "invalid_expected_version", "message": str(exc)}

    home = Path(codex_home or os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser()
    try:
        with _update_lock(home):
            current = _catalog(run_command)
            source = current.get("marketplaceSource", {}) if current else {}
            repo = source.get("source", "")
            if (source.get("sourceType") != "git"
                    or github_manifest_url(repo, plugin_name) != github_manifest_url(OFFICIAL_REPOSITORY, plugin_name)):
                raise UpdateError("unsupported_marketplace", "Automatic updates require the official Git marketplace.")
            old = _installed(run_command)
            if plugin_name not in old:
                raise UpdateError("invalid_inventory", "The requested plugin is not an enabled installation.")
            checkout = Path(current["root"]).resolve(strict=True)
            if not (checkout / ".git").exists():
                raise UpdateError("unavailable_marketplace_snapshot", "The current Git marketplace snapshot is missing.")

            result["attempted"] = True
            stage = Path(tempfile.mkdtemp(prefix="akbs-plugin-release-")).resolve()
            mirror = stage / "release.git"
            keep_stage = False
            try:
                previous, release, target = _stage_release(run_command, mirror, checkout, repo, old)
                if expected_version is not None and compare_versions(target[plugin_name], expected_version) < 0:
                    raise UpdateError("release_not_ready", "The Git release is older than the advertised plugin version.")
                changes = sorted(name for name in target if target[name] != old[name])
                env = _mirror_transport(repo, mirror)
                try:
                    if changes:
                        _pin_catalog(run_command, repo, release, env)
                        _install(run_command, changes, env)
                    _verify_enabled(run_command, home, target, skip=plugin_name)
                    result.update(_verified_evidence(verify_install, target[plugin_name]))
                except (Exception, KeyboardInterrupt) as update_error:
                    if changes:
                        try:
                            _pin_catalog(run_command, repo, previous, env)
                            _install(run_command, changes, env)
                            _verify_enabled(run_command, home, old)
                            result["rolled_back"] = True
                        except Exception as rollback_error:
                            keep_stage = True
                            return {**result, "reason": "rollback_failed", "message": str(rollback_error),
                                    "update_error": str(update_error), "previous_ref": previous, "recovery_mirror": str(mirror)}
                    raise
                return {**result, "status": "PASS", "restart_required": True, "plugins_updated": changes,
                        "release_commit": release if changes else previous,
                        "message": "Plugin files were updated and verified. Restart the task/session to load the updated Skills."}
            finally:
                if not keep_stage:
                    shutil.rmtree(stage, ignore_errors=True)
    except (Exception, KeyboardInterrupt) as exc:
        return {**result, "reason": getattr(exc, "reason", "update_failed"), "message": str(exc)}
