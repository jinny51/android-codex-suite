from __future__ import annotations

from pathlib import Path
from typing import Any

from akbs_member_ops.knowledge_rules import (
    VALID_FRAMEWORK_PLATFORMS,
    is_valid_android_version_value,
)

from akbs_intake.io_utils import write_json


def write_default_evidence(package_dir: Path, rel: str, payload: dict[str, Any]) -> str:
    write_json(package_dir / rel, payload)
    return rel


def framework_package_status_from_patch_statuses(statuses: set[str], has_pass_verification: bool) -> str:
    clean = {item for item in statuses if item in {"validated", "candidate", "draft", "failed", "blocked"}}
    if has_pass_verification and "validated" in clean:
        return "validated"
    if "candidate" in clean or ("validated" in clean and not has_pass_verification):
        return "candidate"
    if "draft" in clean:
        return "draft"
    if "failed" in clean:
        return "failed"
    if "blocked" in clean:
        return "blocked"
    return "candidate"


def downgrade_validated_patch_entries(patch_entries: list[dict[str, Any]], note: str) -> None:
    for item in patch_entries:
        if item.get("status") == "validated":
            item["status"] = "candidate"
            item["reuse_hint"] = False
            previous_note = str(item.get("note") or "").strip()
            item["note"] = f"{previous_note}；{note}" if previous_note else note


def framework_metadata_is_traceable(project: str, platform: str, android_version: str) -> bool:
    return (
        project not in {"", "unknown"}
        and platform in VALID_FRAMEWORK_PLATFORMS
        and is_valid_android_version_value(android_version)
        and android_version != "unknown"
    )
