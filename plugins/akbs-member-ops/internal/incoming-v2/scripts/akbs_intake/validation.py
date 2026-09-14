from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable


IncomingValidator = Callable[[Path, dict[str, Any]], dict[str, Any]]


def validate_package(package_dir: Path, *, incoming_schema_version: str, validate_incoming_package_fn: IncomingValidator) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    manifest_path = package_dir / "manifest.json"
    if not manifest_path.is_file():
        errors.append("缺少 manifest.json")
        return {"status": "FAIL", "errors": errors, "warnings": warnings}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        errors.append(f"manifest.json 解析失败: {exc}")
        return {"status": "FAIL", "errors": errors, "warnings": warnings}

    if manifest.get("schema_version") != incoming_schema_version:
        errors.append(f"schema_version 必须是 {incoming_schema_version}")
        return {"status": "FAIL", "errors": errors, "warnings": warnings}
    return validate_incoming_package_fn(package_dir, manifest)
