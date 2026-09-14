from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from ..io_utils import write_json


REPORT_RENDER_BINDING_SCHEMA = "akbs-report-render-binding-v1"


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_report_render_binding(
    package_dir: Path,
    *,
    report_type: str,
    report_path: str,
    report_view_path: str,
    fact_sources_path: str,
    facts_sha256: str,
    output_path: str,
) -> dict[str, Any]:
    payload = {
        "schema": REPORT_RENDER_BINDING_SCHEMA,
        "report_type": report_type,
        "report_path": report_path,
        "report_sha256": file_sha256(package_dir / report_path),
        "report_view_path": report_view_path,
        "report_view_sha256": file_sha256(package_dir / report_view_path),
        "fact_sources_path": fact_sources_path,
        "facts_sha256": facts_sha256,
    }
    write_json(package_dir / output_path, {"kind": "report_render_binding", "payload": payload})
    return payload
