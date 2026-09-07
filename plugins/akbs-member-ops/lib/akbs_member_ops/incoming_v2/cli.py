"""Command-line surface for local Android change v2 handling."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from akbs_member_ops.member.profile import MemberProfileError, load_member_profile
from akbs_member_ops.http_client import HttpClientFailure

from .capture_adapter import capture_schema_version, preflight_capture
from .materializer import materialize_capture
from .submission import SubmissionError, submit_package
from .validation import (
    AndroidChangeV2Error,
    check_package,
    prepare_package,
    read_package,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="akbs_patch_submit.py android-change-v2",
        description=(
            "Read, strictly check, or byte-preserve an Android change v2 package, or "
            "preflight capture 2.0 or offline-materialize capture 2.1. Submit uses the "
            "independent v2 HTTP path with server-authoritative qualification."
        ),
    )
    subparsers = parser.add_subparsers(dest="action", required=True)
    for action in ("read", "check", "prepare", "submit"):
        sub = subparsers.add_parser(action)
        sub.add_argument("package", type=Path, help="package directory or manifest.json")
        if action == "submit":
            sub.add_argument("--profile", default="", help="configured AKBS member profile")
    adapt = subparsers.add_parser(
        "adapt-capture",
        help="preflight capture 2.0 or offline-materialize capture 2.1",
    )
    adapt.add_argument("capture", type=Path, help="capture package directory or manifest.json")
    adapt.add_argument("--profile", default="", help="configured AKBS member profile")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.action == "read":
            result = read_package(args.package)
        elif args.action == "check":
            result = check_package(args.package)
        elif args.action == "prepare":
            result = prepare_package(args.package)
        elif args.action == "adapt-capture":
            if capture_schema_version(args.capture) == "2.0":
                result = preflight_capture(args.capture)
                print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
                return 1
            try:
                profile = load_member_profile(args.profile or None)
            except MemberProfileError as exc:
                raise AndroidChangeV2Error(f"member profile cannot bind capture: {exc}") from exc
            result = materialize_capture(
                args.capture,
                member_alias=profile.member_alias,
            )
        else:
            result = submit_package(args.package, profile=args.profile or None)
    except HttpClientFailure as exc:
        error = exc.result
        print(json.dumps({
            "status": "FAIL",
            "operation": args.action,
            "reason_code": error.code,
            "message": error.safe_summary("Android change v2 submission was not confirmed"),
            "http_status": error.status_code,
            "request_id": error.request_id,
            "error_kind": error.kind.value,
            "retryable": error.retryable,
            "server_qualified": False,
            "v1_fallback": False,
            "network_requests": 1,
        }, ensure_ascii=False, indent=2, sort_keys=True))
        return 1
    except AndroidChangeV2Error as exc:
        print(
            json.dumps(
                {
                    "status": "FAIL",
                    "operation": args.action,
                    "reason_code": exc.reason_code if isinstance(exc, SubmissionError) else (
                        "android_patch_capture_v2_invalid"
                        if args.action == "adapt-capture"
                        else "android_change_v2_payload_invalid"
                    ),
                    "message": str(exc),
                    "server_qualified": False,
                    "v1_fallback": False,
                    "network_requests": 0,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
