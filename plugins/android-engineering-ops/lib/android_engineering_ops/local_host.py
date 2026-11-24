"""Launch the existing Android tools using the workstation's native executables."""

from __future__ import annotations

import errno
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import IO


_WINDOWS_CHANNEL_BOOTSTRAP = """export MSYS2_ARG_CONV_EXCL='*' MSYS_NO_PATHCONV=1
export CODEX_REMOTE_CHANNEL_SSH_MUX=0
export CODEX_REMOTE_CHANNEL_WINDOWS_SSH="$1"
export CODEX_REMOTE_CHANNEL_WINDOWS_PYTHON="$2"
shift 2
script=$(cygpath -u -- "$1") || exit 2
shift
exec "$BASH" "$script" "$@"
"""


def openssh_command(tool: str) -> str:
    if os.name != "nt":
        return tool
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    executable = Path(system_root) / "System32" / "OpenSSH" / f"{tool}.exe"
    if not executable.is_file():
        raise FileNotFoundError(f"Windows OpenSSH {tool}.exe was not found in System32/OpenSSH")
    return str(executable)


def channel_command(script: Path, *arguments: str) -> list[str]:
    """Use one shell transport on every host without translating remote paths."""
    if os.name != "nt":
        return [str(script), *arguments]
    git = shutil.which("git.exe")
    ssh = Path(openssh_command("ssh"))
    if not git:
        raise SystemExit("Git for Windows is required to interpret the remote-channel script.")
    bash = Path(git).parent.parent / "bin" / "bash.exe"
    if not bash.is_file():
        raise SystemExit("Git for Windows bin/bash.exe was not found beside git.exe.")
    return [
        str(bash), "-c", _WINDOWS_CHANNEL_BOOTSTRAP, "android-remote-channel",
        ssh.as_posix(), Path(sys.executable).as_posix(), str(script), *arguments,
    ]


def run_channel(script: Path, *arguments: str, **options: object) -> subprocess.CompletedProcess:
    """Pass Windows build/capture payloads over stdin to avoid CreateProcess size limits."""
    if os.name == "nt" and "run" in arguments and "--" in arguments:
        separator = arguments.index("--")
        if arguments.index("run") < separator and arguments[separator + 1:]:
            command = " ".join(arguments[separator + 1:])
            if "\0" in command:
                raise ValueError("Android remote command contains a NUL character")
            arguments = (*arguments[:separator], "--stdin")
            # Text subprocess input substitutes CRLF for LF on Windows.
            # Send bytes so remote shell commands and stable IDs keep their identity.
            text = options.pop("text", False)
            encoding = options.pop("encoding", None)
            errors = options.pop("errors", "strict")
            completed = subprocess.run(
                channel_command(script, *arguments),
                input=command.encode("utf-8"),
                **options,
            )
            if not (text or encoding):
                return completed
            return subprocess.CompletedProcess(
                completed.args,
                completed.returncode,
                completed.stdout.decode(encoding or "utf-8", errors) if completed.stdout is not None else None,
                completed.stderr.decode(encoding or "utf-8", errors) if completed.stderr is not None else None,
            )
    return subprocess.run(channel_command(script, *arguments), **options)


def try_lock_file(lock: IO[str]) -> bool:
    if os.name == "nt":
        import msvcrt

        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                return False
            raise
        return True

    import fcntl

    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True
