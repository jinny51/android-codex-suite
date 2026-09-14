#!/usr/bin/env python3
"""Print the optional Android orchestration extension selected for this project."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[3]
LIB = PLUGIN_ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from android_engineering_ops.orchestration import (  # noqa: E402
    ExtensionResolutionError,
    resolve_extension,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    try:
        result = resolve_extension(args.project_root.resolve())
    except ExtensionResolutionError as exc:
        print(f"ANDROID_ORCHESTRATION_EXTENSION_INVALID: {exc}", file=sys.stderr)
        return 78
    print(json.dumps(result.as_dict(), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
