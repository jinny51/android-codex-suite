from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable

from akbs_member_ops.json_io import write_json


MATERIALS_DIR = "materials"


def safe_id(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip("-._")
    return value or "item"


def sha1_text(value: str, length: int = 10) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:length]


def stable_slug_id(value: str, fallback: str, limit: int, hash_source: str | None = None) -> str:
    base = safe_id(value).lower()
    if base == "item":
        base = safe_id(fallback).lower()
    digest = sha1_text(hash_source if hash_source is not None else value)
    head_limit = max(1, limit - len(digest) - 1)
    head = base[:head_limit].strip("-._") or safe_id(fallback).lower()
    return f"{head}-{digest}"[:limit].strip("-._")


def sha1_file(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def list_string_values(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if value:
        return [str(value).strip()]
    return []


def unique_strings(values: Iterable[Any]) -> list[str]:
    return sorted(dict.fromkeys(str(item) for item in values if str(item)))


def read_json_file(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise SystemExit(f"读取 JSON 失败: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SystemExit(f"JSON 必须是对象: {path}")
    return payload


def read_optional_json_object(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def reference_path(package_dir: Path, rel: str) -> Path:
    path = (package_dir / rel).resolve()
    root = package_dir.resolve()
    if path != root and root not in path.parents:
        raise ValueError(f"引用路径越界: {rel}")
    return path


def read_referenced_json(package_dir: Path, rel: str) -> dict[str, Any] | None:
    try:
        path = reference_path(package_dir, rel)
    except ValueError:
        return None
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def read_text_sample(path: Path, limit: int = 12000) -> str:
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="ignore")[:limit]
    except OSError:
        return ""


def materials_rel(*parts: str) -> str:
    clean = [part.strip("/") for part in parts if part]
    return "/".join([MATERIALS_DIR, *clean])
