from __future__ import annotations

import json
import hashlib
import os
import re
import shlex
import stat
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any

try:
    from .config import CONFIG_DEFAULTS, default_codex_home, expanded_path, local_now, parse_bool
    from .io_utils import read_optional_json_object as read_json_object
except ImportError:  # pragma: no cover - direct script import fallback
    scripts_root = Path(__file__).resolve().parents[1]
    if str(scripts_root) not in sys.path:
        sys.path.insert(0, str(scripts_root))
    from akbs_intake.config import CONFIG_DEFAULTS, default_codex_home, expanded_path, local_now, parse_bool
    from akbs_intake.io_utils import read_optional_json_object as read_json_object


PLUGIN_ROOT = Path(__file__).resolve().parents[4]
PLUGIN_UPDATE_SKIP_ENV = "CODEX_REPORT_SKIP_PLUGIN_UPDATE_CHECK"
PLUGIN_UPDATE_REQUIRE_ENV = "CODEX_REPORT_REQUIRE_PLUGIN_UPDATE_CHECK"
PLUGIN_REEXEC_ATTEMPT_ENV = "CODEX_REPORT_PLUGIN_REEXEC_ATTEMPTED"
PLUGIN_REMOTE_MANIFEST_TIMEOUT = 6
LAST_PLUGIN_VERSION_GATE: dict[str, Any] | None = None
PLUGIN_LIST_CACHE: tuple[dict[str, Any] | None, str] | None = None
TARGET_INSTALL_FAMILY = {"akbs-member-ops", "android-engineering-ops"}
LEGACY_INSTALL_FAMILY = {"android-framework-ops", "android-wsl-ops", "android-mac-ops"}
OPTIONAL_GENERATION_PLUGIN = "jinny-android-practices"
TARGET_GENERATION_FLOOR = "2.0.0"
TARGET_MEMBER_PLUGIN = "akbs-member-ops"
TARGET_MARKETPLACE = "android-codex-suite"
PLUGIN_VERSION_RE = re.compile(r"^[0-9]+(?:\.[0-9]+){1,3}(?:[-+][0-9A-Za-z.-]+)?$")
MAX_PLUGIN_MANIFEST_BYTES = 1024 * 1024


def run(
    cmd: list[str],
    check: bool = False,
    cwd: Path | None = None,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    cp = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        stdin=subprocess.DEVNULL,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        errors="replace",
        timeout=timeout,
    )
    if check and cp.returncode != 0:
        detail = cp.stderr.strip() or cp.stdout.strip()
        raise SystemExit(f"命令失败: {' '.join(cmd)}\n{detail}")
    return cp


def env_enabled(name: str) -> bool:
    return parse_bool(os.environ.get(name, ""))


def plugin_update_unknown(message: str, require: bool, git_root: Path | None = None, update_command: str = "") -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": "UNKNOWN",
        "blocking": require,
        "message": message,
    }
    if git_root is not None:
        payload["git_root"] = str(git_root)
    if update_command:
        payload["update_command"] = update_command
    if require:
        payload["message"] += " 已按强制策略停止本次生成；请先完成插件更新（plugin update）后重新运行原命令。"
    return payload


def plugin_manifest_path() -> Path | None:
    for directory in [PLUGIN_ROOT, *PLUGIN_ROOT.parents]:
        candidate = directory / ".codex-plugin" / "plugin.json"
        if candidate.is_file():
            return candidate
    return None


def plugin_install_metadata() -> dict[str, str]:
    manifest_path = plugin_manifest_path()
    payload: dict[str, Any] = {}
    if manifest_path:
        try:
            loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                payload = loaded
        except Exception:
            payload = {}
    return {
        "plugin_name": str(payload.get("name") or "akbs-member-ops"),
        "plugin_version": str(payload.get("version") or ""),
        "repository": str(payload.get("repository") or payload.get("homepage") or ""),
        "plugin_installation": "packaged" if manifest_path else "unknown",
    }


def current_skill_cache_metadata() -> dict[str, str]:
    metadata = plugin_install_metadata()
    return {
        "skill_cache_version": metadata.get("plugin_version", ""),
        "skill_cache_path": str(PLUGIN_ROOT),
        "skill_cache_installation": metadata.get("plugin_installation", "unknown"),
    }


def _plugin_list_payload() -> tuple[dict[str, Any] | None, str]:
    """Read the Codex-installed set; cache directories are not installation authority."""
    global PLUGIN_LIST_CACHE
    if PLUGIN_LIST_CACHE is not None:
        return PLUGIN_LIST_CACHE
    try:
        cp = run(["codex", "plugin", "list", "--json"], timeout=15)
    except (OSError, subprocess.TimeoutExpired) as exc:
        PLUGIN_LIST_CACHE = (None, f"codex plugin list --json is unavailable: {exc}")
        return PLUGIN_LIST_CACHE
    if cp.returncode != 0:
        detail = cp.stderr.strip() or cp.stdout.strip()
        PLUGIN_LIST_CACHE = (None, detail or "codex plugin list --json failed")
        return PLUGIN_LIST_CACHE
    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"duplicate key: {key}")
            value[key] = item
        return value

    def reject_non_finite(value: str) -> None:
        raise ValueError(f"non-finite number: {value}")

    try:
        payload = json.loads(
            cp.stdout,
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=reject_non_finite,
        )
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        PLUGIN_LIST_CACHE = (None, f"invalid codex plugin list JSON: {exc}")
        return PLUGIN_LIST_CACHE
    if not isinstance(payload, dict) or not isinstance(payload.get("installed"), list):
        PLUGIN_LIST_CACHE = (None, "codex plugin list JSON has no installed array")
        return PLUGIN_LIST_CACHE
    if any(not isinstance(row, dict) for row in payload["installed"]):
        PLUGIN_LIST_CACHE = (None, "codex plugin list installed array contains a non-object entry")
        return PLUGIN_LIST_CACHE
    PLUGIN_LIST_CACHE = (payload, "")
    return PLUGIN_LIST_CACHE


def _active_plugin_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        row
        for row in payload.get("installed", [])
        if isinstance(row, dict)
        and row.get("installed") is True
        and row.get("enabled") is True
        and isinstance(row.get("name"), str)
    ]


def _absolute_without_symlinks(path: Path, *, label: str) -> Path:
    absolute = Path(os.path.abspath(os.fspath(path.expanduser())))
    current = Path(absolute.anchor)
    try:
        for part in absolute.parts[1:]:
            current = current / part
            info = current.lstat()
            if stat.S_ISLNK(info.st_mode):
                raise OSError(f"contains a symbolic link: {current}")
    except OSError as exc:
        raise OSError(f"cannot inspect {label}: {current}: {exc}") from exc
    return absolute


def _stable_regular_file(
    path: Path,
    *,
    label: str,
    max_bytes: int | None = None,
) -> tuple[bytes, bool]:
    """Read one regular file and bind its identity plus normalized executable bit."""
    safe = _absolute_without_symlinks(path, label=label)
    descriptor = -1
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(safe, flags)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise OSError(f"not a regular file: {safe}")
        if max_bytes is not None and before.st_size > max_bytes:
            raise OSError(f"exceeds {max_bytes} bytes: {safe}")
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if max_bytes is not None and size > max_bytes:
                raise OSError(f"exceeds {max_bytes} bytes: {safe}")
        after = os.fstat(descriptor)
        rebound = safe.lstat()
    except OSError:
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    identity = lambda item: (
        item.st_dev,
        item.st_ino,
        item.st_size,
        item.st_mode,
        item.st_mtime_ns,
        item.st_ctime_ns,
    )
    if identity(before) != identity(after) or not os.path.samestat(after, rebound):
        raise OSError(f"{label} changed while being read: {safe}")
    return b"".join(chunks), bool(after.st_mode & 0o111)


def _stable_bytes(path: Path, *, label: str, max_bytes: int | None = None) -> bytes:
    """Read one regular file without following symlinks and bind its identity."""
    raw, _executable = _stable_regular_file(path, label=label, max_bytes=max_bytes)
    return raw


def _tree_digest_once(root: Path, *, label: str) -> tuple[str, tuple[int, int, int, int]]:
    """Hash one stable publication view, excluding only Python runtime caches."""
    safe_root = _absolute_without_symlinks(root, label=label)
    root_before = safe_root.stat()
    if not stat.S_ISDIR(root_before.st_mode):
        raise OSError(f"{label} is not a directory: {safe_root}")
    digest = hashlib.sha256()
    for path in sorted(
        safe_root.rglob("*"), key=lambda item: item.relative_to(safe_root).as_posix()
    ):
        relative = path.relative_to(safe_root)
        if "__pycache__" in relative.parts or path.suffix == ".pyc":
            continue
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise OSError(f"{label} contains a symbolic link: {relative.as_posix()}")
        if stat.S_ISDIR(info.st_mode):
            digest.update(b"D\0" + relative.as_posix().encode("utf-8") + b"\0")
            continue
        if not stat.S_ISREG(info.st_mode):
            raise OSError(f"{label} contains a non-regular entry: {relative.as_posix()}")
        raw, executable = _stable_regular_file(path, label=f"{label} file")
        digest.update(b"F\0" + relative.as_posix().encode("utf-8") + b"\0")
        digest.update(b"X\0" if executable else b"-\0")
        digest.update(hashlib.sha256(raw).digest())
    root_after = safe_root.stat()
    identity = lambda item: (
        item.st_dev,
        item.st_ino,
        item.st_mtime_ns,
        item.st_ctime_ns,
    )
    if identity(root_before) != identity(root_after):
        raise OSError(f"{label} root changed while being hashed")
    return digest.hexdigest(), identity(root_after)


def _tree_content_sha256(root: Path, *, label: str) -> str:
    """Hash twice so a half-refreshed publication is never treated as active."""
    first_digest, first_identity = _tree_digest_once(root, label=label)
    second_digest, second_identity = _tree_digest_once(root, label=label)
    if (first_digest, first_identity) != (second_digest, second_identity):
        raise OSError(f"{label} changed between content-hash scans")
    return first_digest


def _strict_plugin_manifest(root: Path) -> tuple[dict[str, Any] | None, str, str]:
    """Read a direct manifest with stable, no-follow publication semantics."""
    manifest = root / ".codex-plugin" / "plugin.json"

    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"duplicate key: {key}")
            value[key] = item
        return value

    try:
        raw = _stable_bytes(
            manifest,
            label="plugin manifest",
            max_bytes=MAX_PLUGIN_MANIFEST_BYTES,
        )
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"non-finite number: {value}")
            ),
        )
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        return None, "", f"manifest is malformed: {manifest}: {exc}"
    if not isinstance(payload, dict):
        return None, "", f"manifest is not a JSON object: {manifest}"
    return payload, hashlib.sha256(raw).hexdigest(), ""


def _active_target_binding(row: dict[str, Any]) -> dict[str, Any]:
    """Bind marketplace source identity to the exact versioned execution cache."""
    issues: list[str] = []
    name = row.get("name")
    version = row.get("version")
    plugin_id = row.get("pluginId")
    marketplace = row.get("marketplaceName")
    source = row.get("source")

    if name != TARGET_MEMBER_PLUGIN:
        issues.append("inventory name is not akbs-member-ops")
    if not isinstance(version, str) or not PLUGIN_VERSION_RE.fullmatch(version):
        issues.append("inventory version is missing or malformed")
    if not isinstance(marketplace, str) or not marketplace.strip():
        issues.append("inventory marketplaceName is missing or malformed")
    elif marketplace != TARGET_MARKETPLACE:
        issues.append(f"inventory marketplaceName is not {TARGET_MARKETPLACE}")
    expected_plugin_id = f"{TARGET_MEMBER_PLUGIN}@{TARGET_MARKETPLACE}"
    if not isinstance(plugin_id, str) or not plugin_id.strip():
        issues.append("inventory pluginId is missing or malformed")
    elif plugin_id != expected_plugin_id:
        issues.append("inventory pluginId does not match the published member plugin identity")

    source_path: Path | None = None
    resolved_source: Path | None = None
    source_realpath = ""
    execution_realpath = ""
    if not isinstance(source, dict):
        issues.append("inventory source is missing or malformed")
    else:
        if source.get("source") != "local":
            issues.append("inventory source.source is not local")
        raw_path = source.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            issues.append("inventory source.path is missing or malformed")
        else:
            source_path = Path(raw_path)
            if not source_path.is_absolute():
                issues.append("inventory source.path is not absolute")
                source_path = None

    execution_root: Path | None = None
    try:
        execution_root = _absolute_without_symlinks(
            PLUGIN_ROOT,
            label="current execution plugin root",
        )
        execution_realpath = str(execution_root)
        if not execution_root.is_dir():
            issues.append("current execution plugin root is not a directory")
    except OSError as exc:
        issues.append(f"current execution plugin root cannot be resolved: {exc}")

    if source_path is not None:
        try:
            resolved_source = _absolute_without_symlinks(
                source_path,
                label="inventory source plugin root",
            )
            source_realpath = str(resolved_source)
            if not resolved_source.is_dir():
                issues.append("inventory source.path is not a directory")
        except OSError as exc:
            issues.append(f"inventory source.path cannot be resolved: {exc}")

    if (
        execution_root is not None
        and isinstance(version, str)
        and PLUGIN_VERSION_RE.fullmatch(version)
    ):
        expected_cache = (
            Path(default_codex_home())
            / "plugins"
            / "cache"
            / TARGET_MARKETPLACE
            / TARGET_MEMBER_PLUGIN
            / version
        )
        try:
            expected_cache_root = _absolute_without_symlinks(
                expected_cache,
                label="expected versioned Codex plugin cache",
            )
            if expected_cache_root != execution_root:
                issues.append("current execution root is not the exact versioned Codex plugin cache")
        except OSError as exc:
            issues.append(f"expected versioned Codex plugin cache cannot be resolved: {exc}")
    if resolved_source is not None and execution_root is not None and resolved_source == execution_root:
        issues.append("inventory marketplace source and execution cache must be distinct roots")

    execution_manifest: dict[str, Any] | None = None
    execution_manifest_sha256 = ""
    if execution_root is not None:
        execution_manifest, execution_manifest_sha256, manifest_error = _strict_plugin_manifest(
            execution_root
        )
        if manifest_error:
            issues.append(manifest_error)
    if execution_manifest is not None:
        if execution_manifest.get("name") != TARGET_MEMBER_PLUGIN:
            issues.append("execution manifest name is not akbs-member-ops")
        manifest_version = execution_manifest.get("version")
        if not isinstance(manifest_version, str) or not PLUGIN_VERSION_RE.fullmatch(manifest_version):
            issues.append("execution manifest version is missing or malformed")
        elif isinstance(version, str) and version.strip() and manifest_version != version:
            issues.append("inventory version differs from execution manifest version")

    source_manifest: dict[str, Any] | None = None
    source_manifest_sha256 = ""
    if resolved_source is not None and resolved_source.is_dir():
        source_manifest, source_manifest_sha256, source_manifest_error = _strict_plugin_manifest(
            resolved_source
        )
        if source_manifest_error:
            issues.append(source_manifest_error)
    if source_manifest is not None:
        if source_manifest.get("name") != TARGET_MEMBER_PLUGIN:
            issues.append("source manifest name is not akbs-member-ops")
        source_version = source_manifest.get("version")
        if not isinstance(source_version, str) or not PLUGIN_VERSION_RE.fullmatch(source_version):
            issues.append("source manifest version is missing or malformed")
        elif isinstance(version, str) and version.strip() and source_version != version:
            issues.append("inventory version differs from source manifest version")
    if (
        source_manifest_sha256
        and execution_manifest_sha256
        and source_manifest_sha256 != execution_manifest_sha256
    ):
        issues.append("source and execution plugin manifests differ byte-for-byte")

    source_tree_sha256 = ""
    execution_tree_sha256 = ""
    if resolved_source is not None and execution_root is not None:
        try:
            source_tree_sha256 = _tree_content_sha256(
                resolved_source,
                label="active akbs-member-ops marketplace source",
            )
            execution_tree_sha256 = _tree_content_sha256(
                execution_root,
                label="executing akbs-member-ops versioned cache",
            )
        except OSError as exc:
            issues.append(f"cannot bind source/execution publication content: {exc}")
        if (
            source_tree_sha256
            and execution_tree_sha256
            and source_tree_sha256 != execution_tree_sha256
        ):
            issues.append("source and execution plugin publication content hashes differ")

    return {
        "valid": not issues,
        "issues": issues,
        "inventory_plugin_id": plugin_id if isinstance(plugin_id, str) else "",
        "inventory_version": version if isinstance(version, str) else "",
        "inventory_marketplace": marketplace if isinstance(marketplace, str) else "",
        "inventory_source_realpath": source_realpath,
        "execution_plugin_realpath": execution_realpath,
        "source_manifest_name": str((source_manifest or {}).get("name") or ""),
        "source_manifest_version": str((source_manifest or {}).get("version") or ""),
        "source_manifest_sha256": source_manifest_sha256,
        "source_tree_sha256": source_tree_sha256,
        "execution_manifest_name": str((execution_manifest or {}).get("name") or ""),
        "execution_manifest_version": str((execution_manifest or {}).get("version") or ""),
        "execution_manifest_sha256": execution_manifest_sha256,
        "execution_tree_sha256": execution_tree_sha256,
    }


def installed_plugin_family_status() -> dict[str, Any]:
    """Report the active install family and reject legacy/target coexistence."""
    payload, error = _plugin_list_payload()
    if payload is None:
        return {
            "status": "UNKNOWN",
            # A business action cannot prove that the target-only family is
            # active when authoritative inventory is unavailable.  Static
            # discovery/help bypasses this function at the public CLI layer.
            "blocking": True,
            "authority": "current_execution_plugin_root_fallback",
            "fallback": True,
            "message": f"无法读取 Codex active plugin 列表: {error}",
            "active_plugins": [],
        }
    rows = _active_plugin_rows(payload)
    active_names = {str(row["name"]) for row in rows}
    active_target = sorted(active_names & TARGET_INSTALL_FAMILY)
    active_legacy = sorted(active_names & LEGACY_INSTALL_FAMILY)
    jinny_rows = [row for row in rows if row.get("name") == OPTIONAL_GENERATION_PLUGIN]
    jinny_target = [
        str(row.get("version") or "")
        for row in jinny_rows
        if PLUGIN_VERSION_RE.fullmatch(str(row.get("version") or ""))
        and compare_versions(str(row.get("version") or ""), TARGET_GENERATION_FLOOR) >= 0
    ]
    jinny_legacy = [
        str(row.get("version") or "")
        for row in jinny_rows
        if PLUGIN_VERSION_RE.fullmatch(str(row.get("version") or ""))
        and compare_versions(str(row.get("version") or ""), TARGET_GENERATION_FLOOR) < 0
    ]
    jinny_unknown = [
        str(row.get("pluginId") or OPTIONAL_GENERATION_PLUGIN)
        for row in jinny_rows
        if not PLUGIN_VERSION_RE.fullmatch(str(row.get("version") or ""))
    ]
    duplicate_targets = sorted(
        name
        for name in TARGET_INSTALL_FAMILY
        if sum(1 for row in rows if row.get("name") == name) > 1
    )
    duplicate_jinny = len(jinny_rows) > 1
    # This function executes from the target member plugin. Running it while a
    # legacy family is active is itself a mixed-family business invocation,
    # even when the target was launched directly from a checkout.
    # This module is physically part of akbs-member-ops.  A malformed or
    # replaced manifest must not make the executing target disappear from the
    # gate and thereby turn a missing active installation into PASS.
    executing_target = True
    target_member_active = "akbs-member-ops" in active_names
    target_member_rows = [row for row in rows if row.get("name") == TARGET_MEMBER_PLUGIN]
    target_binding = (
        _active_target_binding(target_member_rows[0])
        if len(target_member_rows) == 1
        else {
            "valid": False,
            "issues": [
                "Codex active inventory must contain exactly one enabled akbs-member-ops entry"
            ],
        }
    )
    mixed = bool(
        (active_legacy and (active_target or executing_target or jinny_target))
        or (active_target and jinny_legacy)
        or (jinny_target and jinny_legacy)
    )
    target_not_active = executing_target and not target_member_active
    binding_mismatch = target_member_active and len(target_member_rows) == 1 and not target_binding["valid"]
    blocking = (
        mixed
        or bool(duplicate_targets)
        or duplicate_jinny
        or bool(jinny_unknown)
        or target_not_active
        or binding_mismatch
    )
    if mixed:
        status = "MIXED_INSTALL"
        message = "检测到 legacy 与 target Android 插件代际混装；请按迁移顺序选择完整的一代后再运行 target 业务命令。"
    elif duplicate_targets or duplicate_jinny:
        status = "AMBIGUOUS_INSTALL"
        message = "Codex active plugin 列表中存在重复的 target 插件，无法确定唯一执行版本。"
    elif jinny_unknown:
        status = "AMBIGUOUS_INSTALL"
        message = "启用的 jinny-android-practices 缺少版本，无法确定其迁移代际。"
    elif target_not_active:
        status = "TARGET_NOT_ACTIVE"
        message = "当前执行的是 akbs-member-ops，但 Codex active plugin 列表未启用该 target 插件；checkout 仅作为开发执行证据，不能冒充已安装版本。"
    elif binding_mismatch:
        status = "ACTIVE_IDENTITY_MISMATCH"
        message = (
            "Codex active akbs-member-ops 的 pluginId/version/source 与当前执行插件根或 manifest 不一致；"
            "不能借用另一安装路径的 active 身份运行 target 业务命令。"
        )
    else:
        status = "PASS"
        message = "Codex active plugin 家族未发现混装。"
    return {
        "status": status,
        "blocking": blocking,
        "authority": "codex_plugin_list",
        "fallback": False,
        "active_plugins": sorted(active_names),
        "active_target_family": active_target,
        "active_legacy_family": active_legacy,
        "optional_jinny_target_versions": sorted(jinny_target),
        "optional_jinny_legacy_versions": sorted(jinny_legacy),
        "optional_jinny_unknown": sorted(jinny_unknown),
        "target_member_active": target_member_active,
        "duplicate_target_plugins": duplicate_targets,
        "duplicate_optional_jinny": duplicate_jinny,
        "target_member_binding": target_binding,
        "message": message,
    }


def _manifest_metadata(root: Path, plugin_name: str, version: str = "") -> dict[str, Any]:
    manifest_path = root / ".codex-plugin" / "plugin.json"
    payload = read_json_object(manifest_path)
    if str(payload.get("name") or "") != plugin_name:
        return {}
    manifest_version = str(payload.get("version") or "")
    if version and manifest_version != version:
        return {}
    return {
        "installed_plugin_version": manifest_version,
        "installed_plugin_path": str(root),
        "installed_plugin_manifest": str(manifest_path),
    }


def _historical_cache_evidence(plugin_name: str) -> list[str]:
    """Return non-authoritative cache facts without selecting a highest version."""
    cache_root = Path(default_codex_home()) / "plugins" / "cache"
    if not cache_root.is_dir():
        return []
    paths: set[str] = set()
    for pattern in (
        f"*/{plugin_name}/*/.codex-plugin/plugin.json",
        f"*/{plugin_name}/.codex-plugin/plugin.json",
        f"{plugin_name}/*/.codex-plugin/plugin.json",
        f"{plugin_name}/.codex-plugin/plugin.json",
    ):
        paths.update(str(path) for path in cache_root.glob(pattern) if path.is_file())
    return sorted(paths)


def latest_installed_plugin_cache_metadata(plugin_name: str = "akbs-member-ops") -> dict[str, Any]:
    """Resolve the one active install, never the numerically highest cache entry."""
    payload, list_error = _plugin_list_payload()
    if payload is not None:
        matches = [row for row in _active_plugin_rows(payload) if row.get("name") == plugin_name]
        if len(matches) == 1:
            row = matches[0]
            version = str(row.get("version") or "")
            marketplace = str(row.get("marketplaceName") or "")
            source = row.get("source")
            result: dict[str, Any] = {
                "installed_plugin_authority": "codex_plugin_list",
                "installed_plugin_fallback": False,
                "installed_plugin_active": True,
                "installed_plugin_id": str(row.get("pluginId") or ""),
                "inventory_plugin_version": version,
                "installed_plugin_marketplace": marketplace,
                "installed_plugin_source": source if isinstance(source, dict) else {},
            }
            if plugin_name == TARGET_MEMBER_PLUGIN:
                binding = _active_target_binding(row)
                result["installed_plugin_binding"] = binding
                if binding.get("valid"):
                    execution_root = str(binding.get("execution_plugin_realpath") or "")
                    result.update(
                        {
                            "installed_plugin_version": version,
                            "installed_plugin_path": execution_root,
                            "installed_plugin_manifest": str(
                                Path(execution_root) / ".codex-plugin" / "plugin.json"
                            ),
                            "installed_plugin_manifest_sha256": binding.get(
                                "execution_manifest_sha256", ""
                            ),
                            "installed_plugin_tree_sha256": binding.get(
                                "execution_tree_sha256", ""
                            ),
                            "installed_plugin_source_tree_sha256": binding.get(
                                "source_tree_sha256", ""
                            ),
                        }
                    )
                else:
                    result["installed_plugin_manifest_status"] = "IDENTITY_MISMATCH"
                    result["installed_plugin_manifest_error"] = "; ".join(
                        str(item) for item in binding.get("issues", [])
                    )
                return result

            resolved: dict[str, Any] = {}
            source_path = source.get("path") if isinstance(source, dict) else None
            manifest_error = ""
            if isinstance(source_path, str) and source_path and Path(source_path).is_absolute():
                root = Path(source_path)
                manifest, _manifest_sha256, manifest_error = _strict_plugin_manifest(root)
                if (
                    manifest is not None
                    and manifest.get("name") == plugin_name
                    and manifest.get("version") == version
                ):
                    try:
                        resolved_root = _absolute_without_symlinks(
                            root,
                            label=f"active {plugin_name} source root",
                        )
                    except OSError:
                        resolved_root = root
                    resolved = {
                        "installed_plugin_version": version,
                        "installed_plugin_path": str(resolved_root),
                        "installed_plugin_manifest": str(
                            resolved_root / ".codex-plugin" / "plugin.json"
                        ),
                    }
                else:
                    manifest_error = manifest_error or "manifest name/version differs from inventory"
            else:
                manifest_error = "inventory source.path is missing, malformed, or not absolute"
            result.update(resolved)
            if not resolved:
                result["installed_plugin_manifest_status"] = "NOT_FOUND_OR_MISMATCH"
                result["installed_plugin_manifest_error"] = manifest_error
            return result
        if len(matches) > 1:
            return {
                "installed_plugin_authority": "codex_plugin_list",
                "installed_plugin_fallback": False,
                "installed_plugin_active": True,
                "installed_plugin_ambiguous": True,
                "installed_plugin_matches": [str(row.get("pluginId") or "") for row in matches],
            }

    # A checkout/cache process may run before Codex exposes the target as an
    # active install. In that case only the executing plugin root is current;
    # other cache entries are retained as explicitly non-authoritative evidence.
    current = _manifest_metadata(PLUGIN_ROOT, plugin_name)
    result = {
        "installed_plugin_authority": "current_execution_plugin_root_fallback",
        "installed_plugin_fallback": True,
        "installed_plugin_active": False,
        "installed_plugin_list_error": list_error or "target_plugin_not_active",
        "historical_cache_evidence": _historical_cache_evidence(plugin_name),
    }
    if current:
        result.update(
            {
                "execution_plugin_version": current.get("installed_plugin_version", ""),
                "execution_plugin_path": current.get("installed_plugin_path", ""),
                "execution_plugin_manifest": current.get("installed_plugin_manifest", ""),
            }
        )
    return result


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


def github_raw_plugin_manifest_url(metadata: dict[str, str]) -> str:
    repository = str(metadata.get("repository") or "").strip().removesuffix(".git")
    match = re.search(r"github\.com[:/](?P<owner>[^/]+)/(?P<repo>[^/#?]+)", repository)
    plugin_name = str(metadata.get("plugin_name") or "akbs-member-ops").strip()
    if not match or not plugin_name:
        return ""
    owner = match.group("owner")
    repo = match.group("repo")
    return f"https://raw.githubusercontent.com/{owner}/{repo}/main/plugins/{plugin_name}/.codex-plugin/plugin.json"


def fetch_remote_plugin_manifest(metadata: dict[str, str]) -> dict[str, Any]:
    url = github_raw_plugin_manifest_url(metadata)
    if not url:
        raise RuntimeError("插件仓库不是可识别的 GitHub 仓库，不能读取远端插件版本。")
    with urllib.request.urlopen(url, timeout=PLUGIN_REMOTE_MANIFEST_TIMEOUT) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("远端插件清单不是 JSON 对象。")
    return payload


def git_remote_plugin_version(git_root: Path, ref: str) -> str:
    manifest_path = plugin_manifest_path()
    if not manifest_path:
        return ""
    try:
        rel = manifest_path.resolve().relative_to(git_root.resolve()).as_posix()
    except ValueError:
        return ""
    cp = run(["git", "-C", str(git_root), "show", f"{ref}:{rel}"])
    if cp.returncode != 0:
        return ""
    try:
        payload = json.loads(cp.stdout)
    except Exception:
        return ""
    return str(payload.get("version") or "") if isinstance(payload, dict) else ""


def packaged_plugin_freshness(metadata: dict[str, str], fetch: bool, require: bool) -> dict[str, Any]:
    local_version = str(metadata.get("plugin_version") or "")
    cache_metadata = latest_installed_plugin_cache_metadata(metadata.get("plugin_name") or "akbs-member-ops")
    skill_cache = current_skill_cache_metadata()
    payload: dict[str, Any] = {
        "status": "PASS" if local_version else "UNKNOWN",
        "blocking": False,
        "plugin_name": metadata.get("plugin_name") or "akbs-member-ops",
        "local_version": local_version,
        "plugin_version": local_version,
        "skill_cache_version": skill_cache.get("skill_cache_version", ""),
        "skill_cache_path": skill_cache.get("skill_cache_path", ""),
        "installed_plugin_version": cache_metadata.get("installed_plugin_version", ""),
        "installed_plugin_path": cache_metadata.get("installed_plugin_path", ""),
        "installation": metadata.get("plugin_installation") or "packaged",
        "message": "插件缓存版本已记录。",
    }
    payload["installed_plugin_authority"] = cache_metadata.get("installed_plugin_authority", "")
    payload["installed_plugin_fallback"] = cache_metadata.get("installed_plugin_fallback", False)
    if cache_metadata.get("installed_plugin_ambiguous"):
        payload.update(
            {
                "status": "AMBIGUOUS_INSTALL",
                "blocking": True,
                "message": "Codex active plugin 列表中存在多个启用的 akbs-member-ops，无法确定唯一执行版本。",
            }
        )
        return payload
    if not local_version or not PLUGIN_VERSION_RE.fullmatch(local_version):
        return plugin_update_unknown("无法读取严格插件缓存版本，不能确认是否有更新。", require)
    installed_version = str(payload.get("installed_plugin_version") or "")
    if installed_version and not PLUGIN_VERSION_RE.fullmatch(installed_version):
        return plugin_update_unknown("Codex active 插件版本格式非法，不能确认是否有更新。", True)
    if installed_version and compare_versions(local_version, installed_version) < 0:
        payload.update(
            {
                "status": "SESSION_CACHE_STALE",
                "blocking": True,
                "message": (
                    f"Codex 已安装 AKBS Member Ops {installed_version}，但当前会话仍在使用过期技能缓存 {local_version}。"
                    "当前会话不能热刷新技能，请新开或重启 Codex 会话后再生成或上传。"
                ),
            }
        )
        return payload
    if not fetch:
        return payload
    try:
        remote_manifest = fetch_remote_plugin_manifest(metadata)
    except Exception as exc:
        return plugin_update_unknown(f"无法读取插件远端版本，不能确认是否有更新: {exc}", require)
    remote_version = str(remote_manifest.get("version") or "")
    payload["remote_version"] = remote_version
    payload["remote_plugin_version"] = remote_version
    if remote_version and not PLUGIN_VERSION_RE.fullmatch(remote_version):
        return plugin_update_unknown("远端插件版本格式非法，不能确认是否有更新。", require)
    if remote_version and compare_versions(local_version, remote_version) < 0:
        auto_update = auto_update_packaged_plugin(str(metadata.get("plugin_name") or "akbs-member-ops"))
        payload["auto_update"] = auto_update
        if auto_update.get("status") == "PASS":
            payload.update(
                {
                    "status": "UPDATED_RESTART_REQUIRED",
                    "blocking": True,
                    "message": (
                        f"Codex 插件缓存已自动更新到 AKBS Member Ops {remote_version}。"
                        "当前 Python 进程和 Codex 会话已经加载了过期技能缓存，当前会话不能热刷新；"
                        "请重新运行原命令，并在 Codex 会话仍显示旧技能时新开或重启会话。"
                    ),
                }
            )
            return payload
        payload.update(
            {
                "status": "STALE",
                "blocking": True,
                "message": (
                    f"GitHub 已发布 AKBS Member Ops {remote_version}，当前插件缓存是 {local_version}。"
                    "自动更新插件缓存失败，请先在 Codex 插件市场更新插件；如果已更新但当前会话仍显示旧版本，请新开或重启会话后再生成或上传。"
                ),
            }
        )
    elif remote_version:
        payload["message"] = "插件缓存版本已是当前远端版本。"
    else:
        payload.update(
            {
                "status": "UNKNOWN",
                "blocking": require,
                "message": "远端插件清单缺少版本号，不能确认是否有更新。",
            }
        )
    return payload


def auto_update_packaged_plugin(plugin_name: str) -> dict[str, Any]:
    family = installed_plugin_family_status()
    if family.get("blocking"):
        return {
            "attempted": False,
            "status": "FAIL",
            "reason": "mixed_install_family",
            "message": family.get("message"),
            "install_family": family,
        }
    marketplace = TARGET_MARKETPLACE
    upgrade_cmd = ["codex", "plugin", "marketplace", "upgrade", marketplace, "--json"]
    add_cmd = ["codex", "plugin", "add", f"{plugin_name}@{marketplace}", "--json"]
    upgrade_cp = run(upgrade_cmd)
    if upgrade_cp.returncode != 0:
        return {
            "attempted": True,
            "status": "FAIL",
            "upgrade_command": shlex.join(upgrade_cmd),
            "stderr": upgrade_cp.stderr.strip(),
            "stdout": upgrade_cp.stdout.strip(),
        }
    add_cp = run(add_cmd)
    if add_cp.returncode != 0:
        return {
            "attempted": True,
            "status": "FAIL",
            "upgrade_command": shlex.join(upgrade_cmd),
            "install_command": shlex.join(add_cmd),
            "marketplace_stdout": upgrade_cp.stdout.strip(),
            "stderr": add_cp.stderr.strip(),
            "stdout": add_cp.stdout.strip(),
        }
    payload = {
        "attempted": True,
        "status": "PASS",
        "upgrade_command": shlex.join(upgrade_cmd),
        "install_command": shlex.join(add_cmd),
        "marketplace_stdout": upgrade_cp.stdout.strip(),
        "install_stdout": add_cp.stdout.strip(),
    }
    payload.update(latest_installed_plugin_cache_metadata(plugin_name))
    return payload


def plugin_intake_script_from_root(root: Path) -> Path:
    if root.name == "incoming-v1" and root.parent.name == "internal":
        return root / "scripts" / "akbs_member_intake.py"
    return root / "internal" / "incoming-v1" / "scripts" / "akbs_member_intake.py"


def updated_plugin_intake_script_path(freshness: dict[str, Any]) -> Path | None:
    auto_update = freshness.get("auto_update") if isinstance(freshness.get("auto_update"), dict) else {}
    plugin_name = str(freshness.get("plugin_name") or "akbs-member-ops")
    root_values = [
        auto_update.get("installed_plugin_path"),
        freshness.get("installed_plugin_path"),
    ]
    cache_metadata = latest_installed_plugin_cache_metadata(plugin_name)
    if cache_metadata.get("installed_plugin_path"):
        root_values.append(cache_metadata["installed_plugin_path"])

    for root_value in root_values:
        if not root_value:
            continue
        script_path = plugin_intake_script_from_root(Path(str(root_value))).resolve()
        if script_path.is_file():
            return script_path

    if auto_update.get("status") == "PASS" and auto_update.get("command"):
        return Path(__file__).resolve()
    return None


def reexec_latest_plugin_script_after_update(freshness: dict[str, Any]) -> str:
    if freshness.get("status") not in {"UPDATED_RESTART_REQUIRED", "SESSION_CACHE_STALE"}:
        return ""
    if os.environ.get(PLUGIN_REEXEC_ATTEMPT_ENV):
        return ""
    auto_update = freshness.get("auto_update") if isinstance(freshness.get("auto_update"), dict) else {}
    if freshness.get("git_root") and auto_update.get("command") and not auto_update.get("installed_plugin_path"):
        return ""
    script_path = updated_plugin_intake_script_path(freshness)
    if not script_path:
        return "已更新插件缓存，但未找到新缓存里的上传脚本；请新开或重启 Codex 会话后重新运行。"
    os.environ[PLUGIN_REEXEC_ATTEMPT_ENV] = "1"
    try:
        os.execv(sys.executable, [sys.executable, str(script_path), *sys.argv[1:]])
    except OSError as exc:
        return f"已更新插件缓存，但无法切换到新脚本继续执行: {exc}。请新开或重启 Codex 会话后重新运行。"
    return ""


def plugin_freshness_check(fetch: bool = True, require: bool = False) -> dict[str, Any]:
    require = require or env_enabled(PLUGIN_UPDATE_REQUIRE_ENV)
    family = installed_plugin_family_status()
    if family.get("blocking"):
        return {
            "status": str(family.get("status") or "INSTALL_FAMILY_BLOCKED"),
            "blocking": True,
            "message": family.get("message"),
            "install_family": family,
        }
    if env_enabled(PLUGIN_UPDATE_SKIP_ENV):
        return {
            "status": "SKIPPED",
            "blocking": False,
            "message": "已按环境变量跳过插件更新检查（plugin update check）。",
            "install_family": family,
        }

    root_cp = run(["git", "-C", str(PLUGIN_ROOT), "rev-parse", "--show-toplevel"])
    if root_cp.returncode != 0:
        metadata = plugin_install_metadata()
        if metadata.get("plugin_version"):
            return packaged_plugin_freshness(metadata, fetch, require)
        return plugin_update_unknown(
            "无法确认插件版本：当前插件目录不是 Git 仓库（git repository）。请在 Codex 插件市场更新 AKBS Member Ops 插件后重新运行。",
            require,
        )
    git_root = Path(root_cp.stdout.strip()).resolve()
    update_command = shlex.join(["git", "-C", str(git_root), "pull", "--ff-only"])

    branch_cp = run(["git", "-C", str(git_root), "rev-parse", "--abbrev-ref", "HEAD"])
    if branch_cp.returncode != 0:
        return plugin_update_unknown("无法读取插件当前分支，不能确认是否有更新。", require, git_root, update_command)
    branch = branch_cp.stdout.strip()
    if branch == "HEAD":
        return plugin_update_unknown("插件仓库处于 detached HEAD 状态，不能自动判断远端更新。", require, git_root, update_command)

    remote_cp = run(["git", "-C", str(git_root), "config", "--get", f"branch.{branch}.remote"])
    remote_name = remote_cp.stdout.strip() if remote_cp.returncode == 0 else ""
    if not remote_name:
        origin_cp = run(["git", "-C", str(git_root), "config", "--get", "remote.origin.url"])
        if origin_cp.returncode == 0 and origin_cp.stdout.strip():
            remote_name = "origin"
    if not remote_name:
        return plugin_update_unknown("插件仓库没有配置远端仓库，不能确认是否有更新。", require, git_root, update_command)

    if fetch:
        fetch_cp = run(["git", "-C", str(git_root), "fetch", "--quiet", remote_name])
        if fetch_cp.returncode != 0:
            detail = (fetch_cp.stderr.strip() or fetch_cp.stdout.strip()).splitlines()
            suffix = f": {detail[0]}" if detail else ""
            return plugin_update_unknown(f"无法访问插件远端仓库，不能确认是否有更新{suffix}", require, git_root, update_command)

    local_cp = run(["git", "-C", str(git_root), "rev-parse", "HEAD"])
    if local_cp.returncode != 0:
        return plugin_update_unknown("无法读取插件本地提交，不能确认是否有更新。", require, git_root, update_command)
    local_commit = local_cp.stdout.strip()

    upstream_cp = run(["git", "-C", str(git_root), "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"])
    upstream_ref = upstream_cp.stdout.strip() if upstream_cp.returncode == 0 else ""
    if not upstream_ref:
        for candidate in (f"{remote_name}/{branch}", "origin/main", "origin/master"):
            candidate_cp = run(["git", "-C", str(git_root), "rev-parse", "--verify", candidate])
            if candidate_cp.returncode == 0:
                upstream_ref = candidate
                break
    if not upstream_ref:
        return plugin_update_unknown("插件仓库没有可比较的上游分支，不能确认是否有更新。", require, git_root, update_command)

    remote_commit_cp = run(["git", "-C", str(git_root), "rev-parse", upstream_ref])
    if remote_commit_cp.returncode != 0:
        return plugin_update_unknown("无法读取插件远端提交，不能确认是否有更新。", require, git_root, update_command)
    remote_commit = remote_commit_cp.stdout.strip()

    dirty_cp = run(["git", "-C", str(git_root), "status", "--porcelain"])
    warnings: list[str] = []
    if dirty_cp.returncode == 0 and dirty_cp.stdout.strip():
        warnings.append("插件仓库存在未提交改动，更新前需要先处理本地改动。")

    payload: dict[str, Any] = {
        "git_root": str(git_root),
        "local_commit": local_commit[:12],
        "remote_ref": upstream_ref,
        "remote_commit": remote_commit[:12],
        "update_command": update_command,
    }
    metadata = plugin_install_metadata()
    if metadata.get("plugin_version"):
        payload["local_version"] = metadata["plugin_version"]
        payload["plugin_version"] = metadata["plugin_version"]
        payload["skill_cache_version"] = metadata["plugin_version"]
    remote_version = git_remote_plugin_version(git_root, upstream_ref)
    if remote_version:
        payload["remote_version"] = remote_version
        payload["remote_plugin_version"] = remote_version
    cache_metadata = latest_installed_plugin_cache_metadata(metadata.get("plugin_name") or "akbs-member-ops")
    if cache_metadata:
        payload.update(cache_metadata)
    if warnings:
        payload["warnings"] = warnings

    if local_commit == remote_commit:
        local_version = str(payload.get("local_version") or "")
        installed_version = str(payload.get("installed_plugin_version") or "")
        if installed_version and local_version and compare_versions(local_version, installed_version) < 0:
            payload.update(
                {
                    "status": "SESSION_CACHE_STALE",
                    "blocking": True,
                    "message": (
                        f"Codex 已安装 AKBS Member Ops {installed_version}，但当前会话仍在使用过期技能缓存 {local_version}。"
                        "当前会话不能热刷新技能，请新开或重启 Codex 会话后再生成或上传。"
                    ),
                }
            )
            return payload
        payload.update(
            {
                "status": "PASS",
                "blocking": False,
                "message": "插件已是当前远端版本。",
            }
        )
        return payload

    local_ancestor = run(["git", "-C", str(git_root), "merge-base", "--is-ancestor", local_commit, remote_commit])
    if local_ancestor.returncode == 0:
        if warnings:
            payload.update(
                {
                    "status": "STALE",
                    "blocking": True,
                    "message": "插件有更新，但当前插件仓库存在未提交改动，不能自动更新。请先处理本地改动后重新运行。",
                }
            )
            return payload
        pull_cp = run(["git", "-C", str(git_root), "pull", "--ff-only"])
        if pull_cp.returncode == 0:
            payload["auto_update"] = {
                "attempted": True,
                "status": "PASS",
                "command": update_command,
                "stdout": pull_cp.stdout.strip(),
            }
            payload.update(
                {
                    "status": "UPDATED_RESTART_REQUIRED",
                    "blocking": True,
                    "message": (
                        "插件已自动快进更新。当前 Python 进程和 Codex 会话已经加载了过期技能缓存，不能热刷新；"
                        "请重新运行原命令，并在 Codex 会话仍显示旧技能时新开或重启会话。"
                    ),
                }
            )
            return payload
        payload["auto_update"] = {
            "attempted": True,
            "status": "FAIL",
            "command": update_command,
            "stderr": pull_cp.stderr.strip(),
            "stdout": pull_cp.stdout.strip(),
        }
        payload.update(
            {
                "status": "STALE",
                "blocking": True,
                "message": "插件有更新，但自动快进更新失败，已停止本次生成。请先执行插件更新（plugin update）后重新运行原命令。",
            }
        )
        return payload

    remote_ancestor = run(["git", "-C", str(git_root), "merge-base", "--is-ancestor", remote_commit, local_commit])
    if remote_ancestor.returncode == 0:
        payload.update(
            {
                "status": "PASS",
                "blocking": False,
                "message": "本地插件提交领先远端，未发现必须先拉取的更新。",
            }
        )
        return payload

    payload.update(
        {
            "status": "DIVERGED",
            "blocking": True,
            "message": "插件本地分支和远端分支已分叉，已停止本次生成。请让管理员处理插件更新（plugin update）后重新运行原命令。",
        }
    )
    return payload


def plugin_version_gate_check(config: dict[str, str] | None = None, fetch: bool = True, require: bool = True) -> dict[str, Any]:
    global LAST_PLUGIN_VERSION_GATE
    gate = plugin_freshness_check(fetch=fetch, require=require)
    gate.setdefault("install_family", installed_plugin_family_status())
    metadata = plugin_install_metadata()
    skill_cache = current_skill_cache_metadata()
    cache_metadata = latest_installed_plugin_cache_metadata(metadata.get("plugin_name") or "akbs-member-ops")
    gate.setdefault("plugin_name", metadata.get("plugin_name") or "akbs-member-ops")
    gate.setdefault("plugin_version", metadata.get("plugin_version") or "")
    gate.setdefault("local_version", metadata.get("plugin_version") or "")
    gate.setdefault("skill_cache_version", skill_cache.get("skill_cache_version", ""))
    gate.setdefault("skill_cache_path", skill_cache.get("skill_cache_path", ""))
    if cache_metadata:
        gate.setdefault("installed_plugin_version", cache_metadata.get("installed_plugin_version", ""))
        gate.setdefault("installed_plugin_path", cache_metadata.get("installed_plugin_path", ""))
    gate["checked_at"] = local_now(config or CONFIG_DEFAULTS).isoformat()
    gate["result"] = gate.get("status", "UNKNOWN")
    LAST_PLUGIN_VERSION_GATE = gate
    return gate
