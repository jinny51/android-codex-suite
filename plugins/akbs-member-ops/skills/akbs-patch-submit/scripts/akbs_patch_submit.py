#!/usr/bin/env python3
"""Prepare, validate, and submit Android change packages on incoming v1."""

from __future__ import annotations

import sys
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[3]
for path in (PLUGIN_ROOT / "lib", PLUGIN_ROOT / "internal" / "incoming-v1" / "scripts"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from akbs_member_ops.incoming_v1.cli import route_arguments  # noqa: E402
from akbs_member_intake import main as incoming_main  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    return incoming_main(route_arguments("patch", arguments))


if __name__ == "__main__":
    raise SystemExit(main())
