#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import time


PLUGIN_ROOT = Path(__file__).resolve().parents[3]
PLUGIN_LIB = PLUGIN_ROOT / "lib"
if str(PLUGIN_LIB) not in sys.path:
    sys.path.insert(0, str(PLUGIN_LIB))

from android_engineering_ops.artifact_paths import require_safe_artifact_path
from android_engineering_ops.install_family import require_target_install_family
from android_engineering_ops.remote_patch_snapshot import (
    RemotePatchSnapshotError,
    load_remote_patch_snapshot,
)


SNAPSHOT_MODULE = PLUGIN_LIB / "android_engineering_ops" / "remote_patch_snapshot.py"
CAPTURE_SCRIPT = (
    PLUGIN_ROOT
    / "skills"
    / "android-patch-capture"
    / "scripts"
    / "capture_android_patch.py"
)
CHANNEL_SCRIPT = (
    PLUGIN_ROOT
    / "skills"
    / "android-remote-channel"
    / "scripts"
    / "remote-channel.sh"
)
_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_WORKSPACE_RE = re.compile(r"[0-9a-f]{16}")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_PACKAGER_OWNED_OPTIONS = (
    "--patch-artifact",
    "--patch-repo-path",
    "--remote-snapshot",
    "--remote-source-root",
    "--snapshot-command-id",
    "--snapshot-max-age-seconds",
    "--snapshot-sha256",
    "--snapshot-workspace-id",
    "--source-root",
    "--workflow-contract",
)


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as exc:
        raise SystemExit(f"无法执行 snapshot handoff 命令: {command[0]}: {exc}") from exc


def _snapshot_command(repository_paths: list[str]) -> str:
    source = base64.b64encode(SNAPSHOT_MODULE.read_bytes()).decode("ascii")
    python = "import base64;exec(compile(base64.b64decode(%r),'<remote_patch_snapshot>','exec'))" % source
    arguments = " ".join(f"--repo-path {shlex.quote(item)}" for item in repository_paths)
    return (
        f"python3 -c {shlex.quote(python)} generate "
        '--remote-root "$CANONICAL_ROOT" '
        '--workspace-id "${STATE_DIR##*/}" '
        '--command-id "$CURRENT_ID" '
        f"{arguments}"
    )


def _parse_channel_handoff(output: str) -> dict[str, str]:
    names = {
        "SNAPSHOT_REMOTE_PATH",
        "SNAPSHOT_SHA256",
        "SNAPSHOT_WORKSPACE_ID",
        "SNAPSHOT_COMMAND_ID",
        "SNAPSHOT_REMOTE_ROOT",
    }
    values: dict[str, str] = {}
    for line in output.splitlines():
        key, separator, value = line.partition("=")
        if separator and key in names:
            if key in values and values[key] != value:
                raise SystemExit(f"remote channel 返回了冲突的 {key}")
            values[key] = value.strip()
    missing = sorted(names - values.keys())
    if missing:
        raise SystemExit(f"remote channel 未返回完整 snapshot handoff: {', '.join(missing)}")
    if not _WORKSPACE_RE.fullmatch(values["SNAPSHOT_WORKSPACE_ID"]):
        raise SystemExit("remote channel snapshot workspace id 非法")
    if not _ID_RE.fullmatch(values["SNAPSHOT_COMMAND_ID"]):
        raise SystemExit("remote channel snapshot command id 非法")
    if not _SHA256_RE.fullmatch(values["SNAPSHOT_SHA256"]):
        raise SystemExit("remote channel snapshot sha256 非法")
    expected_prefix = (
        f"/.codex/android-remote-sessions/{values['SNAPSHOT_WORKSPACE_ID']}/"
        f"snapshots/{values['SNAPSHOT_COMMAND_ID']}/snapshot.json"
    )
    remote_path = values["SNAPSHOT_REMOTE_PATH"]
    if not remote_path.startswith("/") or not remote_path.endswith(expected_prefix):
        raise SystemExit("remote channel snapshot path 不在 canonical private state 中")
    return values


def _default_output(workspace_id: str, command_id: str) -> Path:
    codex_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()
    return (
        codex_home
        / "artifacts"
        / "android-patch-capture"
        / "snapshots"
        / workspace_id
        / command_id
        / "snapshot.json"
    )


def _require_codex_artifact_output(path: Path) -> Path:
    codex_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()
    artifacts = (codex_home / "artifacts").resolve()
    resolved = path.expanduser().resolve()
    try:
        resolved.relative_to(artifacts)
    except ValueError as exc:
        raise SystemExit(f"snapshot 必须写入 $CODEX_HOME/artifacts: {resolved}") from exc
    return resolved


def _package_arguments(raw: list[str], *, enabled: bool) -> list[str]:
    arguments = list(raw)
    if arguments and arguments[0] == "--":
        arguments.pop(0)
    if not enabled:
        if arguments:
            raise SystemExit("capture 参数只能与 --package 一起使用")
        return []
    if not arguments:
        raise SystemExit("--package 必须在 -- 后提供 capture_android_patch.py 参数")
    for argument in arguments:
        for option in _PACKAGER_OWNED_OPTIONS:
            if argument == option or argument.startswith(option + "="):
                raise SystemExit(f"--package 参数不得覆盖一步流程绑定的 {option}")
    return arguments


def _emit_completed_process(result: subprocess.CompletedProcess[str]) -> None:
    if result.stdout:
        sys.stdout.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create a source snapshot through android-remote-channel v2, transfer it locally, "
            "and optionally package it immediately."
        )
    )
    parser.add_argument("--ssh-host", required=True)
    parser.add_argument("--remote-root", required=True)
    parser.add_argument("--repo-path", action="append", required=True)
    parser.add_argument("--command-id", required=True)
    parser.add_argument("--channel-script", type=Path, default=CHANNEL_SCRIPT)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--max-age-seconds", type=int, default=900)
    parser.add_argument(
        "--package",
        action="store_true",
        help=(
            "Immediately invoke the bundled capture_android_patch.py with a freshly "
            "transferred snapshot; put its arguments after --."
        ),
    )
    parser.add_argument("package_args", nargs=argparse.REMAINDER)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    package_arguments = _package_arguments(args.package_args, enabled=args.package)
    if args.package and package_arguments == ["--help"]:
        help_result = _run([sys.executable, str(CAPTURE_SCRIPT), "--help"])
        _emit_completed_process(help_result)
        return help_result.returncode
    require_target_install_family(PLUGIN_ROOT)
    if not _ID_RE.fullmatch(args.command_id):
        raise SystemExit("--command-id 必须符合 remote-channel v2 id 合同")
    if args.max_age_seconds <= 0 or args.max_age_seconds > 86400:
        raise SystemExit("--max-age-seconds 必须在 1..86400 范围")
    if args.channel_script != CHANNEL_SCRIPT:
        raise SystemExit(
            "--channel-script cannot override the bundled android-remote-channel owner"
        )
    channel_script = CHANNEL_SCRIPT
    if channel_script.is_symlink() or not channel_script.is_file():
        raise SystemExit(f"bundled android-remote-channel 脚本不存在或不是普通文件: {channel_script}")
    if not SNAPSHOT_MODULE.is_file():
        raise SystemExit(f"remote snapshot 合同模块不存在: {SNAPSHOT_MODULE}")
    if args.package and not CAPTURE_SCRIPT.is_file():
        raise SystemExit(f"本地 capture packager 不存在: {CAPTURE_SCRIPT}")

    remote_command = _snapshot_command(args.repo_path)
    channel = _run(
        [
            str(channel_script),
            "--ssh-host",
            args.ssh_host,
            "--remote-root",
            args.remote_root,
            "run",
            "--lock",
            "exclusive",
            "--command-id",
            args.command_id,
            "--",
            remote_command,
        ]
    )
    if channel.returncode != 0:
        raise SystemExit(
            "remote-channel snapshot 命令失败；禁止回退到 mounted source 或 direct SSH source command:\n"
            + (channel.stderr or channel.stdout)
        )
    handoff = _parse_channel_handoff(channel.stdout)
    if handoff["SNAPSHOT_COMMAND_ID"] != args.command_id:
        raise SystemExit("remote snapshot command id 与请求不一致")

    target = args.out.expanduser() if args.out else _default_output(
        handoff["SNAPSHOT_WORKSPACE_ID"],
        handoff["SNAPSHOT_COMMAND_ID"],
    )
    if not target.is_absolute():
        target = Path.cwd() / target
    target = _require_codex_artifact_output(target)
    target = require_safe_artifact_path(target, purpose="remote patch snapshot handoff")
    if target.exists():
        raise SystemExit(f"本地 immutable snapshot 已存在: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".snapshot-transfer.", dir=target.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        transfer = _run(
            [
                "scp",
                "-q",
                f"{args.ssh_host}:{handoff['SNAPSHOT_REMOTE_PATH']}",
                str(temporary),
            ]
        )
        if transfer.returncode != 0:
            raise SystemExit(f"snapshot SSH 文件传输失败: {transfer.stderr or transfer.stdout}")
        try:
            load_remote_patch_snapshot(
                temporary,
                expected_workspace_id=handoff["SNAPSHOT_WORKSPACE_ID"],
                expected_command_id=handoff["SNAPSHOT_COMMAND_ID"],
                expected_remote_root=handoff["SNAPSHOT_REMOTE_ROOT"],
                expected_sha256=handoff["SNAPSHOT_SHA256"],
                now_ns=time.time_ns(),
                max_age_ns=args.max_age_seconds * 1_000_000_000,
            )
        except RemotePatchSnapshotError as exc:
            raise SystemExit(f"本地 snapshot 验证失败: {exc}") from exc
        os.chmod(temporary, 0o400)
        try:
            os.link(temporary, target)
        except FileExistsError as exc:
            raise SystemExit(f"本地 immutable snapshot 已存在: {target}") from exc
    finally:
        temporary.unlink(missing_ok=True)

    result = {
        "schema": "android-remote-patch-snapshot-handoff-v1",
        "snapshot": str(target),
        "snapshot_sha256": handoff["SNAPSHOT_SHA256"],
        "workspace_id": handoff["SNAPSHOT_WORKSPACE_ID"],
        "command_id": handoff["SNAPSHOT_COMMAND_ID"],
        "remote_root": handoff["SNAPSHOT_REMOTE_ROOT"],
    }
    if not args.package:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0

    capture = _run(
        [
            sys.executable,
            str(CAPTURE_SCRIPT),
            "--remote-snapshot",
            str(target),
            "--snapshot-workspace-id",
            handoff["SNAPSHOT_WORKSPACE_ID"],
            "--snapshot-command-id",
            handoff["SNAPSHOT_COMMAND_ID"],
            "--snapshot-sha256",
            handoff["SNAPSHOT_SHA256"],
            "--snapshot-max-age-seconds",
            str(args.max_age_seconds),
            "--remote-source-root",
            handoff["SNAPSHOT_REMOTE_ROOT"],
            "--workflow-contract",
            "current_codex_skill",
            *package_arguments,
        ]
    )
    _emit_completed_process(capture)
    if capture.returncode != 0 and not capture.stdout.strip():
        print(f"fresh snapshot 已保留供诊断: {target}", file=sys.stderr)
    return capture.returncode


if __name__ == "__main__":
    raise SystemExit(main())
