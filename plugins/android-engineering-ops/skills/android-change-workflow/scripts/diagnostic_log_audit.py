#!/usr/bin/env python3
"""Audit diagnostic markers on REMOTE_ROOT through android-remote-channel."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shlex
import sys
import time


SKILLS_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_ROOT = Path(__file__).resolve().parents[3]
PLUGIN_LIB = PLUGIN_ROOT / "lib"
if str(PLUGIN_LIB) not in sys.path:
    sys.path.insert(0, str(PLUGIN_LIB))

from android_engineering_ops.local_host import run_channel  # noqa: E402


from android_engineering_ops.install_family import require_target_install_family  # noqa: E402

DEFAULT_CHANNEL = SKILLS_ROOT / "android-remote-channel" / "scripts" / "remote-channel.sh"

REMOTE_SCANNER = r'''from __future__ import annotations
import json
import os
from pathlib import Path
import re
import sys

extensions = {".java", ".kt", ".cc", ".cpp", ".c", ".h", ".hpp", ".aidl", ".xml", ".bp", ".mk"}
patterns = [
    ("temporary_marker", re.compile(r"TEMP_DIAG|TODO_DIAG|DEBUG_DIAG|REMOVE_BEFORE_SUBMIT")),
    ("println", re.compile(r"System\.out\.println|printStackTrace\s*\(")),
    ("android_log", re.compile(r"\bSlog\.(v|d|i|w|e)\b|\bLog\.(v|d|i|w|e)\b|ALOG[VDIWEF]\b")),
    ("todo_fixme", re.compile(r"\bTODO\b|\bFIXME\b")),
]
root = Path.cwd().resolve()
requested = json.loads(sys.argv[1])
include_logs = sys.argv[2] == "1"
findings = []

def files_under(start):
    if start.is_file():
        yield start
        return
    for current, directories, files in os.walk(start):
        directories[:] = [name for name in directories if name not in {".git", ".repo", ".codex", "out"}]
        current_path = Path(current)
        for name in sorted(files):
            yield current_path / name

for raw in requested:
    candidate = (root / raw).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        raise SystemExit(f"remote audit path escapes workspace: {raw}")
    if not candidate.exists():
        raise SystemExit(f"remote audit path is missing: {raw}")
    for path in files_under(candidate):
        if path.suffix not in extensions:
            continue
        try:
            lines = path.read_text(errors="replace").splitlines()
        except OSError:
            continue
        for lineno, line in enumerate(lines, 1):
            for name, pattern in patterns:
                if name == "android_log" and not include_logs and not re.search(
                    r"TEMP_DIAG|TODO_DIAG|DEBUG_DIAG|REMOVE_BEFORE_SUBMIT", line
                ):
                    continue
                if pattern.search(line):
                    findings.append(f"{path.relative_to(root)}:{lineno}: {name}: {line.strip()}")

if not findings:
    print("No diagnostic markers found.")
    raise SystemExit(0)
print("Diagnostic audit findings:")
print("\n".join(findings))
raise SystemExit(1)
'''


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ssh-host", required=True)
    parser.add_argument("--remote-root", required=True)
    parser.add_argument(
        "--path",
        action="append",
        required=True,
        help="Changed source file or bounded relative directory. Repeat as needed.",
    )
    parser.add_argument("--include-logs", action="store_true")
    parser.add_argument("--command-id", default="")
    parser.add_argument("--wait-timeout", type=int, default=900)
    parser.add_argument("--channel-script", type=Path, default=DEFAULT_CHANNEL)
    return parser.parse_args()


def require_relative_remote_path(value: str) -> str:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise SystemExit(f"--path must be a bounded relative remote path: {value}")
    if "\n" in value or "\r" in value:
        raise SystemExit("--path must not contain newlines")
    return path.as_posix()


def main() -> int:
    args = parse_args()
    require_target_install_family(PLUGIN_ROOT)
    paths = [require_relative_remote_path(value) for value in args.path]
    if args.wait_timeout <= 0:
        raise SystemExit("--wait-timeout must be positive")
    if args.channel_script != DEFAULT_CHANNEL:
        raise SystemExit(
            "--channel-script cannot override the bundled android-remote-channel owner"
        )
    if DEFAULT_CHANNEL.is_symlink() or not DEFAULT_CHANNEL.is_file():
        raise SystemExit(f"bundled android-remote-channel entry is missing: {DEFAULT_CHANNEL}")
    command_id = args.command_id.strip()
    if not command_id:
        digest = hashlib.sha256(
            json.dumps(paths, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:12]
        command_id = f"diagnostic-audit-{time.time_ns()}-{digest}"
    remote_command = (
        "python3 - "
        + shlex.quote(json.dumps(paths, ensure_ascii=False, separators=(",", ":")))
        + (" 1" if args.include_logs else " 0")
        + " <<'PY'\n"
        + REMOTE_SCANNER
        + "\nPY"
    )
    completed = run_channel(
        DEFAULT_CHANNEL,
        "--ssh-host",
        args.ssh_host,
        "--remote-root",
        args.remote_root,
        "run",
        "--lock",
        "none",
        "--command-id",
        command_id,
        "--wait-timeout",
        str(args.wait_timeout),
        "--",
        remote_command,
        check=False,
        text=True,
    )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
