#!/usr/bin/env python3
"""Handle legacy Framework v1 and general Android change v2 packages."""

from __future__ import annotations

import sys
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[3]
for path in (PLUGIN_ROOT / "lib", PLUGIN_ROOT / "internal" / "incoming-v1" / "scripts"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from akbs_member_ops.incoming_v1.cli import route_arguments  # noqa: E402
from akbs_member_ops.incoming_v2.cli import main as incoming_v2_main  # noqa: E402
from akbs_intake.version_gate import installed_plugin_family_status  # noqa: E402
from akbs_member_intake import main as incoming_main  # noqa: E402


def _is_parser_help(arguments: list[str]) -> bool:
    """Allow only options that argparse can still interpret as help."""
    for item in arguments:
        if item == "--":
            return False
        if item in {"-h", "--help"}:
            return True
    return False


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if arguments and arguments[0] == "android-change-v2":
        # Select the independent v2 surface before any v1 configuration,
        # contract, archive, or HTTP workflow. Never fall through to v1.
        if _is_parser_help(arguments[1:]):
            return incoming_v2_main(arguments[1:])
        family = installed_plugin_family_status()
        if family.get("blocking"):
            raise SystemExit(str(family.get("message") or "target plugin family is not active"))
        return incoming_v2_main(arguments[1:])
    if arguments == ["--help"]:
        print(
            "Android change v2: akbs_patch_submit.py android-change-v2 "
            "{read,check,prepare,submit} PACKAGE | adapt-capture CAPTURE\n"
            "Legacy Framework v1 options follow:\n"
        )
    return incoming_main(route_arguments("patch", arguments))


if __name__ == "__main__":
    raise SystemExit(main())
