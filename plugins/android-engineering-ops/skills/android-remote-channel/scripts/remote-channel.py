#!/usr/bin/env python3
"""Use the existing Android remote channel from the current workstation."""

from pathlib import Path
import sys

SCRIPT = Path(__file__).resolve()
sys.path.insert(0, str(SCRIPT.parents[3] / "lib"))

from android_engineering_ops.local_host import run_channel  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(run_channel(SCRIPT.with_suffix(".sh"), *sys.argv[1:]).returncode)
