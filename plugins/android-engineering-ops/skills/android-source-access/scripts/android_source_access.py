#!/usr/bin/env python3
"""Detect the host and dispatch one internal source-access adapter command."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[3]
PLUGIN_LIB = PLUGIN_ROOT / "lib"
if str(PLUGIN_LIB) not in sys.path:
    sys.path.insert(0, str(PLUGIN_LIB))

from android_engineering_ops.source_access import (  # noqa: E402
    ADAPTER_COMMANDS,
    UnsupportedSourceAccessHost,
    adapter_skill_root,
    detect_source_access_host,
    dispatch_adapter_command,
)
from android_engineering_ops.install_family import (  # noqa: E402
    InstallFamilyError,
    assert_target_install_family,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Route source access by local host.")
    parser.add_argument("--expected-host", choices=("windows", "wsl", "macos"))
    subparsers = parser.add_subparsers(dest="action", required=True)
    detect = subparsers.add_parser("detect")
    detect.add_argument("--print-field", choices=("host", "adapter_skill_dir", "adapter_scripts_dir"))
    subparsers.add_parser("list-commands")
    run = subparsers.add_parser("run")
    run.add_argument("command")
    run.add_argument("arguments", nargs=argparse.REMAINDER)
    return parser.parse_args(argv)


def host_payload() -> dict[str, object]:
    host = detect_source_access_host()
    skill_root = adapter_skill_root(PLUGIN_ROOT, host.host)
    return {
        "schema": "android-source-access-host-v1",
        "host": host.host,
        "system": host.system,
        "release": host.release,
        "evidence": list(host.evidence),
        "adapter_skill_dir": str(skill_root),
        "adapter_scripts_dir": str(skill_root / "scripts"),
        "commands": sorted(ADAPTER_COMMANDS[host.host]),
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        host = detect_source_access_host()
        if args.expected_host and host.host != args.expected_host:
            raise UnsupportedSourceAccessHost(
                f"platform entry expects {args.expected_host}, detected {host.host}"
            )
        payload = host_payload()
        if args.action == "detect":
            print(payload[args.print_field] if args.print_field else json.dumps(payload, ensure_ascii=False, sort_keys=True))
            return 0
        if args.action == "list-commands":
            for command in payload["commands"]:
                print(command)
            return 0
        assert_target_install_family(PLUGIN_ROOT)
        arguments = list(args.arguments)
        if arguments[:1] == ["--"]:
            arguments = arguments[1:]
        return dispatch_adapter_command(PLUGIN_ROOT, host, args.command, arguments)
    except InstallFamilyError as exc:
        print(f"ANDROID_ENGINEERING_INSTALL_FAMILY_INVALID: {exc}", file=sys.stderr)
        return 78
    except UnsupportedSourceAccessHost as exc:
        print(f"SOURCE_ACCESS_HOST_UNSUPPORTED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
