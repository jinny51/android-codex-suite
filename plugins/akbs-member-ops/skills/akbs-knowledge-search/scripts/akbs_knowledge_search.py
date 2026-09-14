#!/usr/bin/env python3
"""Search AKBS knowledge and record member reuse decisions."""

from __future__ import annotations

import sys
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[3]
for path in (PLUGIN_ROOT / "lib", PLUGIN_ROOT / "internal" / "incoming-v2" / "scripts"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from akbs_intake.version_gate import installed_plugin_family_status  # noqa: E402
from akbs_member_ops.knowledge_search.cli import main as search_main  # noqa: E402


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
    if not _is_parser_help(arguments):
        family = installed_plugin_family_status()
        if family.get("blocking"):
            raise SystemExit(str(family.get("message") or "target plugin family is not active"))
    return search_main(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
