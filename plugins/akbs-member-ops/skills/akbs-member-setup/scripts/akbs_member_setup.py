#!/usr/bin/env python3
"""Configure and diagnose the standalone AKBS member plugin."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[3]
INCOMING_SCRIPTS = PLUGIN_ROOT / "internal" / "incoming-v2" / "scripts"
for path in (PLUGIN_ROOT / "lib", INCOMING_SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from akbs_member_intake import main as incoming_main  # noqa: E402
from akbs_intake.version_gate import installed_plugin_family_status  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser(
        "preflight-install-family",
        help="verify the target-only active install before creating member config",
    )

    doctor = subparsers.add_parser("doctor")
    doctor.add_argument("--profile")
    doctor.add_argument("--strict", action="store_true")
    doctor.add_argument("--check-remote", action="store_true")
    doctor.add_argument("--allow-synthetic", action="store_true")

    subparsers.add_parser("print-setup-prompt")
    return parser


def incoming_arguments(args: argparse.Namespace) -> list[str]:
    values: list[str] = []
    if args.profile:
        values.extend(("--profile", args.profile))
    values.append("doctor")
    if args.strict:
        values.append("--strict")
    if args.check_remote:
        values.append("--check-remote")
    if args.allow_synthetic:
        values.append("--allow-synthetic")
    return values


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "preflight-install-family":
        family = installed_plugin_family_status()
        print(json.dumps(family, ensure_ascii=False, indent=2, sort_keys=True))
        return 1 if family.get("blocking") else 0
    if args.command == "print-setup-prompt":
        prompt = PLUGIN_ROOT / "internal" / "incoming-v2" / "references" / "member-setup-prompt.md"
        print(prompt.read_text(encoding="utf-8"), end="")
        return 0
    return incoming_main(incoming_arguments(args))


if __name__ == "__main__":
    raise SystemExit(main())
