"""Resolve one source-access command to the detected host adapter."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from .host import SourceAccessHost, UnsupportedSourceAccessHost, adapter_root


ADAPTER_COMMANDS = {
    "windows": {"register-project"},
    "wsl": {
        "discover-samba-share.sh",
        "ensure-samba-share.sh",
        "inspect-android-sdk.sh",
        "install-ssh-key.sh",
        "mount-from-remote-path.sh",
        "mount-platform.sh",
        "plan-from-remote-path.sh",
        "resolve-ssh-candidate.sh",
        "restore-project-mount.sh",
    },
    "macos": {
        "detect-projects.sh",
        "discover-samba-share.sh",
        "keychain-check.sh",
        "keychain-delete.sh",
        "keychain-read.sh",
        "keychain-store.sh",
        "mount-share.sh",
        "register-project.sh",
        "restore-mounts.sh",
        "unmount-share.sh",
    },
}
RESERVED_ROUTING_ARGUMENTS = frozenset({"--channel-script", "--inspection-helper"})


def adapter_skill_root(plugin_root: Path, host: str) -> Path:
    return adapter_root(plugin_root, host) / "skills" / "android-source-access"


def resolve_adapter_command(plugin_root: Path, host: SourceAccessHost, command: str) -> Path:
    allowed = ADAPTER_COMMANDS.get(host.host)
    if allowed is None or command not in allowed:
        raise UnsupportedSourceAccessHost(
            f"source-access command {command!r} is not available on host {host.host}"
        )
    entry = f"{command}.py" if host.host == "windows" else command
    script = adapter_skill_root(plugin_root, host.host) / "scripts" / entry
    if not script.is_file():
        raise UnsupportedSourceAccessHost(f"source-access adapter command is missing: {script}")
    return script


def adapter_environment(
    plugin_root: Path,
    host: SourceAccessHost,
    base: Mapping[str, str] | None = None,
) -> dict[str, str]:
    environment = dict(os.environ if base is None else base)
    environment["ANDROID_REMOTE_CHANNEL_SCRIPT"] = str(
        plugin_root / "skills/android-remote-channel/scripts/remote-channel.sh"
    )
    environment["ANDROID_REMOTE_SOURCE_INSPECTION_HELPER"] = str(
        plugin_root / "lib/android_engineering_ops/remote_source_inspection.py"
    )
    environment["ANDROID_SOURCE_ACCESS_HOST"] = host.host
    environment["ANDROID_SOURCE_ACCESS_ADAPTER_ROOT"] = str(
        adapter_skill_root(plugin_root, host.host)
    )
    return environment


def dispatch_adapter_command(
    plugin_root: Path,
    host: SourceAccessHost,
    command: str,
    arguments: Sequence[str],
    *,
    execve: Callable[[str, list[str], Mapping[str, str]], object] = os.execve,
    base_environment: Mapping[str, str] | None = None,
) -> object:
    for argument in arguments:
        option = argument.split("=", 1)[0]
        if option in RESERVED_ROUTING_ARGUMENTS:
            raise UnsupportedSourceAccessHost(
                f"canonical source access owns {option}; caller override is forbidden"
            )
    script = resolve_adapter_command(plugin_root, host, command)
    environment = adapter_environment(plugin_root, host, base_environment)
    if host.host == "windows":
        return subprocess.call([sys.executable, str(script), *arguments], env=environment)
    return execve(str(script), [str(script), *arguments], environment)
