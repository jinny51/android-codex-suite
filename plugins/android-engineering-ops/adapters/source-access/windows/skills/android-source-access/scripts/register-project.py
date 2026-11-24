#!/usr/bin/env python3
"""Register a Windows SMB bridge and an Android project using existing SSH."""

from __future__ import annotations

import argparse
import ctypes
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shlex
import socket
import subprocess
import sys
import tempfile


PLUGIN_ROOT = Path(__file__).resolve().parents[6]
sys.path.insert(0, str(PLUGIN_ROOT / "lib"))

from android_engineering_ops.install_family import require_target_install_family  # noqa: E402
from android_engineering_ops.local_host import openssh_command, run_channel, try_lock_file  # noqa: E402
from android_engineering_ops.project_registry import derive_project_id  # noqa: E402


def smb_project_parts(path: str) -> tuple[str, str, tuple[str, ...]]:
    extended = "\\\\?\\UNC\\"
    if path.casefold().startswith(extended.casefold()):
        path = "\\\\" + path[len(extended):]
    unc = PureWindowsPath(path)
    authority = unc.drive.removeprefix("\\\\").split("\\")
    if not unc.drive.startswith("\\\\") or len(authority) != 2 or not all(authority):
        raise ValueError("Windows SMB 项目目录无法解析为服务器、共享和相对路径")
    server, share = authority
    if server.casefold() in {"wsl$", "wsl.localhost"}:
        raise ValueError("Windows 项目请使用 SMB 目录")
    return server, share, unc.parts[1:]


def _ssh_output(*arguments: str) -> str:
    result = subprocess.run(
        [openssh_command("ssh"), "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", *arguments],
        capture_output=True, encoding="utf-8", timeout=20, check=False,
    )
    if result.returncode != 0:
        raise ValueError(f"SSH 无法确认项目的 SMB 配置 (exit {result.returncode})")
    return result.stdout.strip()


def _host_addresses(host: str) -> set[str]:
    return {str(row[4][0]) for row in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)}


def require_smb_project_binding(
    bridge: Path, ssh_host: str, share: str, canonical_root: str,
) -> None:
    smb_host, mapped_share, subpath = smb_project_parts(str(bridge))
    if mapped_share.casefold() != share.casefold():
        raise ValueError("本机 SMB 路径的共享名称与 --share 不一致")
    ssh_config = _ssh_output("-G", ssh_host)
    ssh_hostname = next(
        (line.split(None, 1)[1] for line in ssh_config.splitlines() if line.startswith("hostname ")),
        "",
    )
    if not ssh_hostname or (
        ssh_hostname.casefold() != smb_host.casefold()
        and not _host_addresses(smb_host) <= _host_addresses(ssh_hostname)
    ):
        raise ValueError("本机 SMB 服务器与 SSH 服务器不一致")

    # This read-only operation inspects Samba configuration, not mounted Android source.
    share_path = _ssh_output(
        ssh_host, shlex.join(["testparm", "-s", "--section-name", mapped_share, "--parameter-name", "path"]),
    )
    samba_root = PurePosixPath(share_path)
    if not samba_root.is_absolute() or any(ch in share_path for ch in "\r\n%"):
        raise ValueError("无法确认 SMB 共享的服务端绝对路径")
    mapped = samba_root.joinpath(*subpath)
    verified = _ssh_output(ssh_host, shlex.join(["realpath", "-e", "--", str(mapped)]))
    if verified != canonical_root:
        raise ValueError("本机 SMB 目录与 SSH 确认的远程项目根不一致")


def register(args: argparse.Namespace) -> Path:
    host = args.ssh_host
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", host):
        raise ValueError("请使用 Windows SSH 配置中的主机别名")
    if not args.share or any(ch in args.share for ch in "/\\"):
        raise ValueError("SMB 共享名称必须是一段名称")
    root = PurePosixPath(args.remote_root)
    if not root.is_absolute() or ".." in root.parts:
        raise ValueError("远程项目根必须是绝对 Linux 路径")
    bridge = args.local_path.expanduser()
    if not bridge.is_absolute() or not bridge.is_dir():
        raise ValueError("Windows 上的 SMB 或 UNC 项目目录不可读取")
    if ctypes.windll.kernel32.GetDriveTypeW(bridge.anchor) != 4:
        raise ValueError("请选择 Windows SMB 映射或 UNC 项目目录")
    bridge = bridge.resolve(strict=True)
    smb_project_parts(str(bridge))

    script = PLUGIN_ROOT / "skills/android-remote-channel/scripts/remote-channel.sh"
    checked = run_channel(
        script, "--ssh-host", host, "--remote-root", str(root), "check",
        capture_output=True,
        encoding="utf-8",
        check=False,
    )
    if checked.returncode:
        raise ValueError((checked.stderr or checked.stdout).strip())
    canonical = re.search(r"^REMOTE_ROOT_OK .* canonical=(.+)$", checked.stdout, re.M)
    workspace = re.search(r"^WORKSPACE_OK id=([0-9a-f]{16})\b", checked.stdout, re.M)
    if canonical is None or workspace is None:
        raise ValueError("远程项目根身份未得到已有 SSH 通道确认")
    verified_root = canonical.group(1)
    require_smb_project_binding(bridge, host, args.share, verified_root)
    entry = {
        "project_id": args.project_id,
        "sdk_name": args.project,
        "platform": args.platform,
        "local_path": str(bridge),
    }
    project = {
        "identity_schema": "android-remote-project-identity-v1",
        "project_id": derive_project_id(entry),
        "ssh_host": host,
        "platform": args.platform,
        "local_path": str(bridge),
        "artifact_bridge_path": str(bridge),
        "mount_transport": "windows-smb",
        "remote_path": verified_root,
        "remote_root": verified_root,
    }

    registry_dir = args.registry_dir.expanduser().resolve()
    registry_dir.mkdir(parents=True, exist_ok=True)
    path = registry_dir / f"{host}.json"
    with path.with_suffix(".lock").open("a+", encoding="utf-8") as lock:
        if not try_lock_file(lock):
            raise ValueError("该主机的项目登记正在更新，请稍后重试")
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"server": host}
        if not isinstance(data, dict) or data.get("server") != host:
            raise ValueError("现有项目登记与 SSH 别名不一致")
        share = data.setdefault("shares", {}).setdefault(args.share, {})
        projects = share.setdefault("projects", {})
        active = projects.get(args.project)
        if active and active.get("remote_path") != verified_root:
            raise ValueError("同名项目已绑定不同的远端路径，请先核对原登记")
        if active and active.get("project_id"):
            if args.project_id and active["project_id"] != project["project_id"]:
                raise ValueError("同名项目已拥有不同的 project ID")
            project["project_id"] = active["project_id"]
        projects[args.project] = project
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=registry_dir, prefix=".registry-", delete=False,
        ) as temporary:
            staging = Path(temporary.name)
            try:
                json.dump(data, temporary, ensure_ascii=False, indent=2)
                temporary.write("\n")
                temporary.close()
                os.replace(staging, path)
            finally:
                staging.unlink(missing_ok=True)
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ssh-host", required=True)
    parser.add_argument("--remote-root", required=True)
    parser.add_argument("--local-path", required=True, type=Path)
    parser.add_argument("--share", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--platform", required=True)
    parser.add_argument("--project-id", default="")
    parser.add_argument("--registry-dir", type=Path, default=Path.home() / ".servers" / "projects")
    args = parser.parse_args()
    require_target_install_family(PLUGIN_ROOT)
    try:
        print(register(args))
        return 0
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(f"SOURCE_ACCESS_REGISTRATION_FAILED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
