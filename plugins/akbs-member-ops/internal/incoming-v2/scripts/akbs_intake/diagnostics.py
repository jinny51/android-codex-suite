from __future__ import annotations

import json
import sys
from pathlib import Path


DIAGNOSTIC_PREFIX = "AKBS_LOCAL_INPUT_DIAGNOSTIC "


def warn_local_input(code: str, path: str | Path) -> None:
    payload = {
        "code": code,
        "level": "warning",
        "path": str(path),
    }
    print(
        DIAGNOSTIC_PREFIX + json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
        file=sys.stderr,
    )
