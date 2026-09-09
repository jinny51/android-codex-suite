#!/usr/bin/env python3
"""One update check per engineering task, never per build/remote command."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from android_engineering_ops.install_family import (
    OFFICIAL_MARKETPLACE,
    TARGET_PLUGIN,
    InstallFamilyError,
    _absolute_without_symlinks,
    _read_active_inventory,
    _strict_json_file,
    assert_target_install_family,
)
from codex_plugin_update import (
    compare_versions, fetch_manifest, github_manifest_url, update_plugin, version_parts,
)

SCHEMA = "android-engineering-task-start-v1"
MANIFEST_URL = github_manifest_url("https://github.com/jinny51/android-codex-suite", TARGET_PLUGIN)
TASK_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
STABLE_VERSION_RE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")


def fetch_remote_manifest() -> dict[str, Any]:
    return fetch_manifest(MANIFEST_URL, timeout=10)


def run_codex(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command, stdin=subprocess.DEVNULL, capture_output=True,
        text=True, check=False, timeout=60,
    )


def _version(value: Any) -> tuple[int, ...]:
    if not isinstance(value, str) or not STABLE_VERSION_RE.fullmatch(value):
        raise ValueError("only a valid stable x.y.z release can be auto-updated")
    return version_parts(value)


def _result(status: str, message: str, **fields: Any) -> dict[str, Any]:
    return {
        "schema": SCHEMA, "status": status, "blocking": status != "PASS",
        "reused": False, "message": message, **fields,
    }


def _save(path: Path, value: dict[str, Any]) -> None:
    # The task record is private local update state, never source or package evidence.
    descriptor, name = tempfile.mkstemp(prefix=".startup-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _verify_updated_install(home: Path, expected_version: str) -> dict[str, Any]:
    inventory = _read_active_inventory()
    targets = [
        row for row in inventory["installed"]
        if row.get("name") == TARGET_PLUGIN
        and row.get("installed") is True and row.get("enabled") is True
    ]
    if len(targets) != 1:
        raise InstallFamilyError("updated engineering plugin is not uniquely active")
    installed_version = targets[0].get("version")
    if compare_versions(installed_version, expected_version) < 0:
        raise InstallFamilyError("plugin add did not install the advertised release")
    root = home / "plugins/cache" / OFFICIAL_MARKETPLACE / TARGET_PLUGIN / installed_version
    assert_target_install_family(root, inventory=inventory, codex_home=home)
    return {"installed_plugin_version": installed_version, "installed_plugin_path": str(root), "installed_plugin_active": True}


def ensure_task_started(
    plugin_root: Path, task_id: str, *, retry: bool = False,
    codex_home: Path | None = None,
) -> dict[str, Any]:
    """Check once at an explicit task boundary; keep low-level guards local-only.

    A successful record pins this task's execution root/version. Failed checks are
    retained too, and require explicit --retry. Updating never re-execs new code.
    """
    if not TASK_ID_RE.fullmatch(task_id):
        return _result("INVALID_TASK_ID", "请为当前工程任务选择一个稳定的任务标识。")
    home = Path(codex_home or os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser()
    try:
        assert_target_install_family(plugin_root, codex_home=home)
        root = _absolute_without_symlinks(plugin_root, label="executing engineering plugin")
        manifest = _strict_json_file(root / ".codex-plugin/plugin.json", label="engineering manifest")
        local_version = manifest.get("version")
        _version(local_version)
        directory = home / "artifacts/android-engineering-ops/startup"
        directory.mkdir(parents=True, exist_ok=True)
        directory = _absolute_without_symlinks(directory, label="engineering startup state")
    except (InstallFamilyError, OSError, ValueError) as exc:
        return _result(
            "INSTALL_FAMILY_INVALID",
            f"当前工程插件安装或执行缓存不一致：{exc}。如已更新，请重启 Codex；不要切换脚本继续旧任务。",
        )

    binding = {
        "task_id": task_id, "plugin_name": TARGET_PLUGIN,
        "execution_root": str(root), "local_version": local_version,
        "manifest_sha256": hashlib.sha256((root / ".codex-plugin/plugin.json").read_bytes()).hexdigest(),
    }
    state_path = directory / f"{task_id}.json"
    # One short, on-demand lock serializes checks/installs across engineering tasks.
    # No daemon, TTL, polling, or automatic task retirement is involved.
    try:
        descriptor = os.open(directory / ".update.lock", os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(descriptor, "a+") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return _result("STARTUP_BUSY", "另一个工程任务正在检查或更新插件；本次未开始业务操作。", **binding)

            # Inventory may have changed while entering the startup boundary.
            assert_target_install_family(root, codex_home=home)
            previous = None
            if state_path.exists() or state_path.is_symlink():
                previous = _strict_json_file(state_path, label="engineering startup record")
                if previous.get("schema") != SCHEMA or previous.get("task_id") != task_id:
                    raise ValueError("startup record identity differs")
                if (previous.get("status") not in {"PASS", "CHECK_FAILED", "UPDATE_FAILED", "UPDATED_RESTART_REQUIRED"}
                        or previous.get("blocking") is not (previous.get("status") != "PASS")):
                    raise ValueError("startup record status is invalid")
                same_binding = all(previous.get(key) == value for key, value in binding.items())
                if not same_binding and previous.get("status") == "PASS":
                    return _result("TASK_VERSION_CHANGED", "该工程任务已经绑定其他插件版本；先安全结束旧任务，不自动切换版本。", **binding)
                if same_binding and (previous.get("status") == "PASS" or not retry):
                    return {**previous, "reused": True}
                if not same_binding and previous.get("status") != "UPDATED_RESTART_REQUIRED" and not retry:
                    return _result("TASK_VERSION_CHANGED", "任务启动记录与当前缓存不同；确认处于安全起点后显式重试。", **binding)

            try:
                remote = fetch_remote_manifest()
                if remote.get("name") != TARGET_PLUGIN:
                    raise ValueError("remote manifest names a different plugin")
                remote_version = remote.get("version")
                _version(remote_version)
                newer = compare_versions(remote_version, local_version) > 0
            except Exception as exc:
                result = _result("CHECK_FAILED", f"无法确认工程插件最新版本（{type(exc).__name__}）；未开始业务操作。网络恢复后用 --retry 重试。", **binding)
            else:
                if not newer:
                    result = _result("PASS", "工程插件版本检查通过；本任务后续步骤复用该结果。", remote_version=remote_version, **binding)
                else:
                    updated = update_plugin(
                        TARGET_PLUGIN,
                        run_command=lambda command, timeout: run_codex(command),
                        verify_install=lambda: _verify_updated_install(home, remote_version),
                        expected_version=remote_version,
                    )
                    if updated["status"] == "PASS":
                        installed_version = updated["installed_plugin_version"]
                        result = _result("UPDATED_RESTART_REQUIRED", f"工程插件已更新至 {installed_version}。请退出并重启 Codex，再继续原任务；本次未执行工程操作。", remote_version=remote_version, installed_version=installed_version, **binding)
                    else:
                        reason = updated.get("reason", "unknown")
                        result = _result("UPDATE_FAILED", f"检测到工程插件 {remote_version}，但自动更新未确认成功（{reason}）。请检查插件市场安装状态，修复后用 --retry 重试。", remote_version=remote_version, **binding)
            result["checked_at"] = datetime.now(timezone.utc).isoformat()
            _save(state_path, result)
            return result
    except (InstallFamilyError, OSError, ValueError) as exc:
        return _result("STARTUP_STATE_INVALID", f"工程启动状态无法确认：{exc}。未开始业务操作。", **binding)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-id", required=True, help="Stable ID for one engineering task; reuse across its Skills.")
    parser.add_argument("--retry", action="store_true", help="Explicitly retry a failed startup check, never refresh an active PASS.")
    parser.add_argument("--plugin-root", type=Path, default=Path(__file__).resolve().parents[2], help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    result = ensure_task_started(args.plugin_root, args.task_id, retry=args.retry)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not result["blocking"] else 78


if __name__ == "__main__":
    raise SystemExit(main())
