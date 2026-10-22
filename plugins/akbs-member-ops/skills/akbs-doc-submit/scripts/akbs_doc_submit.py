#!/usr/bin/env python3
"""Public independent Doc entry; runtime helpers remain in the member plugin."""
from __future__ import annotations

import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[3]
for path in (PLUGIN_ROOT / "lib", PLUGIN_ROOT / "internal" / "incoming-v2" / "scripts"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from akbs_member_ops.doc_submit import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
