#!/usr/bin/env python3
"""Review or dispute one member-visible AKBS merge confirmation."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[3]
for path in (PLUGIN_ROOT / "lib", PLUGIN_ROOT / "internal" / "incoming-v2" / "scripts"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from akbs_intake.version_gate import installed_plugin_family_status  # noqa: E402
from akbs_member_ops.knowledge_search.cli import main as search_main  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("list", "detail", "target", "compare", "analyze", "dispute"))
    parser.add_argument("--confirmation-id")
    parser.add_argument("--send-dispute", action="store_true")
    parser.add_argument("--dispute-reason", default="")
    parser.add_argument("--member-assessment", default="")
    parser.add_argument("--evidence-ref", action="append", default=[])
    parser.add_argument("--server-timeout", type=float, default=3.0)
    parser.add_argument("--json", action="store_true")
    return parser


def search_arguments(args: argparse.Namespace) -> list[str]:
    values = ["--merge-confirmation", args.action]
    if args.confirmation_id:
        values.extend(("--merge-confirmation-id", args.confirmation_id))
    if args.send_dispute:
        values.append("--send-dispute")
    if args.dispute_reason:
        values.extend(("--dispute-reason", args.dispute_reason))
    if args.member_assessment:
        values.extend(("--member-assessment", args.member_assessment))
    for evidence_ref in args.evidence_ref:
        values.extend(("--evidence-ref", evidence_ref))
    values.extend(("--server-timeout", str(args.server_timeout)))
    if args.json:
        values.append("--json")
    return values


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    family = installed_plugin_family_status()
    if family.get("blocking"):
        raise SystemExit(str(family.get("message") or "target plugin family is not active"))
    return search_main(search_arguments(args))


if __name__ == "__main__":
    raise SystemExit(main())
