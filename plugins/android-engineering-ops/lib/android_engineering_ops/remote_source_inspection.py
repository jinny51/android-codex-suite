#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import shlex
import sys
import uuid
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from android_engineering_ops.local_host import run_channel


PROJECT_IDENTITY_SCHEMA = "android-remote-project-identity-v1"
INSPECTION_TRANSPORT = "android-remote-channel-v2"
WORKSPACE_PATTERN = re.compile(r"\bsession=codex-android-([0-9a-f]{16})\b")
INSPECTOR = Path(__file__).resolve().parent / "source_access" / "remote_inspector.sh"
INSPECTOR_FIELDS = (
    "REMOTE_ROOT",
    "PLATFORM",
    "SDK_NAME",
    "SOURCE_PLATFORM",
    "SOURCE_SDK_NAME",
    "SOURCE_SDK_SOURCE",
    "PROJECT_BRANCH",
    "ANDROID_PRODUCT_NAME",
    "TARGET_BOARD_PLATFORM",
    "PLATFORM_SCORE_RK",
    "PLATFORM_SCORE_UNISOC",
    "PLATFORM_SCORE_MTK",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inspect an Android source tree exclusively through android-remote-channel v2."
    )
    parser.add_argument("--channel-script", type=Path, default=None)
    parser.add_argument("--ssh-host", required=True)
    parser.add_argument("--remote-root", required=True)
    parser.add_argument("--platform", choices=("rk", "mtk", "unisoc"), default="")
    parser.add_argument("--sdk-name", default="")
    parser.add_argument("--accept-platform-conflict", action="store_true")
    parser.add_argument("--accept-sdk-name-conflict", action="store_true")
    parser.add_argument("--mode", choices=("strict", "discovery"), default="strict")
    parser.add_argument("--command-id", default="")
    parser.add_argument("--wait-timeout", type=int, default=120)
    return parser.parse_args()


def resolve_channel_script(value: Path | None) -> Path:
    raw = value or Path(os.environ.get("ANDROID_REMOTE_CHANNEL_SCRIPT", ""))
    if not str(raw):
        raise SystemExit(
            "REMOTE_CHANNEL_REQUIRED: pass --channel-script or set ANDROID_REMOTE_CHANNEL_SCRIPT"
        )
    path = raw.expanduser().resolve()
    if not path.is_file():
        raise SystemExit(f"REMOTE_CHANNEL_MISSING: {path}")
    return path


def remote_command(args: argparse.Namespace) -> str:
    if not INSPECTOR.is_file():
        raise SystemExit(f"REMOTE_INSPECTOR_MISSING: {INSPECTOR}")
    source = INSPECTOR.read_text(encoding="utf-8")
    inspector_args = (
        ".",
        args.platform,
        args.sdk_name,
        "1" if args.accept_platform_conflict else "0",
        "1" if args.accept_sdk_name_conflict else "0",
        args.mode,
    )
    quoted_args = " ".join(shlex.quote(value) for value in inspector_args)
    return f"printf '%s' {shlex.quote(source)} | bash -s -- {quoted_args}"


def decode_shell_value(value: str) -> str:
    try:
        parts = shlex.split(value, posix=True)
    except ValueError as exc:
        raise SystemExit(f"REMOTE_INSPECTION_INVALID_VALUE: {value!r}: {exc}") from exc
    if len(parts) != 1:
        raise SystemExit(f"REMOTE_INSPECTION_INVALID_VALUE: {value!r}")
    return parts[0]


def parse_inspection_output(output: str) -> dict[str, str]:
    values: dict[str, str] = {}
    allowed = set(INSPECTOR_FIELDS)
    for raw in output.splitlines():
        if "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        if key in allowed:
            values[key] = decode_shell_value(value)
    missing = [key for key in ("REMOTE_ROOT", "PLATFORM", "SDK_NAME") if not values.get(key)]
    if missing:
        raise SystemExit("REMOTE_INSPECTION_INCOMPLETE: " + ", ".join(missing))
    if not values["REMOTE_ROOT"].startswith("/"):
        raise SystemExit("REMOTE_INSPECTION_INVALID_ROOT: canonical remote root is not absolute")
    return values


def project_id(platform: str, sdk_name: str) -> str:
    platform_part = re.sub(r"[^A-Za-z0-9._-]+", "-", platform.lower()).strip("-._")
    project_part = re.sub(r"[^A-Za-z0-9._-]+", "-", sdk_name).strip("-._")
    if not platform_part or not project_part:
        raise SystemExit("REMOTE_INSPECTION_INVALID_PROJECT_ID")
    return f"{platform_part}-{project_part}"


def print_identity(args: argparse.Namespace, values: dict[str, str], workspace_id: str) -> None:
    fields = {
        "PROJECT_IDENTITY_SCHEMA": PROJECT_IDENTITY_SCHEMA,
        "PROJECT_ID": project_id(values["PLATFORM"], values["SDK_NAME"]),
        "WORKSPACE_ID": workspace_id,
        "SSH_HOST": args.ssh_host,
        "INSPECTION_TRANSPORT": INSPECTION_TRANSPORT,
        **values,
    }
    ordered = (
        "PROJECT_IDENTITY_SCHEMA",
        "PROJECT_ID",
        "WORKSPACE_ID",
        "SSH_HOST",
        "REMOTE_ROOT",
        "PLATFORM",
        "SDK_NAME",
        "INSPECTION_TRANSPORT",
        *[field for field in INSPECTOR_FIELDS if field not in {"REMOTE_ROOT", "PLATFORM", "SDK_NAME"}],
    )
    for key in ordered:
        if key in fields:
            print(f"{key}={shlex.quote(fields[key])}")


def main() -> int:
    args = parse_args()
    if args.accept_platform_conflict and not args.platform:
        raise SystemExit("--accept-platform-conflict requires --platform")
    if args.accept_sdk_name_conflict and not args.sdk_name:
        raise SystemExit("--accept-sdk-name-conflict requires --sdk-name")
    if args.wait_timeout <= 0:
        raise SystemExit("--wait-timeout must be positive")

    channel = resolve_channel_script(args.channel_script)
    command_id = args.command_id or f"source-inspect-{uuid.uuid4().hex[:12]}"
    command = [
        str(channel),
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
        remote_command(args),
    ]
    result = run_channel(
        Path(command[0]), *command[1:],
        check=False,
        encoding="utf-8",
        capture_output=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        if detail:
            print(detail, file=sys.stderr)
        return result.returncode
    workspace = WORKSPACE_PATTERN.search(result.stdout)
    if not workspace:
        raise SystemExit("REMOTE_INSPECTION_WORKSPACE_ID_MISSING")
    values = parse_inspection_output(result.stdout)
    print_identity(args, values, workspace.group(1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
