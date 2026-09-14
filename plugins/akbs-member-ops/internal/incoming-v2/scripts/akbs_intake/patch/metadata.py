from __future__ import annotations

from pathlib import Path
from typing import Any

from akbs_member_ops.knowledge_rules import find_platform_tokens, parse_platform_token

from akbs_intake.io_utils import read_json_file


def evidence_text_values(value: Any) -> list[str]:
    values: list[str] = []
    if isinstance(value, dict):
        for item in value.values():
            values.extend(evidence_text_values(item))
    elif isinstance(value, list):
        for item in value:
            values.extend(evidence_text_values(item))
    elif isinstance(value, str) and value.strip():
        values.append(value)
    return values


def infer_platform_metadata(
    patch_entries: list[dict[str, Any]],
    evidence_entries: list[dict[str, Any]] | None = None,
    package_dir: Path | None = None,
) -> tuple[str, str]:
    platform, android_version = parse_platform_token(patch_entries)
    evidence_tokens: list[tuple[str, str]] = []
    for entry in evidence_entries or []:
        payload: Any = entry.get("payload") if isinstance(entry, dict) else None
        if package_dir and isinstance(entry, dict) and not payload:
            rel = entry.get("path")
            if isinstance(rel, str) and rel:
                payload = read_json_file(package_dir / rel)
        for value in evidence_text_values(payload):
            evidence_tokens.extend(find_platform_tokens(value))
    unique_evidence_tokens = sorted(set(evidence_tokens))
    if len(unique_evidence_tokens) == 1:
        evidence_platform, evidence_android_version = unique_evidence_tokens[0]
        if platform in {"", "unknown"}:
            platform = evidence_platform
        if android_version in {"", "unknown"}:
            android_version = evidence_android_version
    return platform or "unknown", android_version or "unknown"


def repo_paths_from_files(files: list[str]) -> list[str]:
    repos: list[str] = []
    for path in files:
        parts = path.split("/")
        if path.startswith(("services/", "core/", "data/etc/")):
            repos.append("frameworks/base")
        elif len(parts) >= 2:
            repos.append("/".join(parts[:2]))
    return sorted(dict.fromkeys(repos)) or ["unknown"]


def first_evidence_path(entries: list[dict[str, Any]], kind: str) -> str:
    for entry in entries:
        if entry.get("kind") == kind and isinstance(entry.get("path"), str):
            return str(entry["path"])
    return ""


def first_evidence_payload(package_dir: Path, entries: list[dict[str, Any]], kind: str) -> dict[str, Any]:
    rel = first_evidence_path(entries, kind)
    if not rel:
        return {}
    return read_json_file(package_dir / rel)
