#!/usr/bin/env python3
"""Read and normalize a legacy Framework capture without modifying or copying it."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any


def _codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser()


def _reject_symlinks(path: Path) -> Path:
    absolute = Path(os.path.abspath(os.fspath(path.expanduser())))
    current = Path(absolute.anchor)
    try:
        for part in absolute.parts[1:]:
            current = current / part
            if stat.S_ISLNK(current.lstat().st_mode):
                raise SystemExit(f"legacy capture path contains a symlink: {current}")
    except OSError as exc:
        raise SystemExit(f"cannot inspect legacy capture path {current}: {exc}") from exc
    return absolute


def _stable_bytes(path: Path) -> bytes:
    path = _reject_symlinks(path)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise SystemExit(f"cannot open legacy capture file {path}: {exc}") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise SystemExit(f"legacy capture item is not a regular file: {path}")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    identity = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns)
    if identity(before) != identity(after):
        raise SystemExit(f"legacy capture file changed while being read: {path}")
    return b"".join(chunks)


def _strict_json(raw: bytes, *, label: str) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in items:
            if key in value:
                raise SystemExit(f"legacy manifest has duplicate key {key}: {label}")
            value[key] = item
        return value

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"legacy manifest is not strict UTF-8 JSON: {label}") from exc
    if not isinstance(value, dict):
        raise SystemExit("legacy manifest must be an object")
    return value


def inspect_legacy_package(package: Path) -> dict[str, Any]:
    legacy_root = _reject_symlinks(
        _codex_home() / "artifacts" / "android-framework-patch-capture" / "packages"
    )
    package = _reject_symlinks(package)
    try:
        package.relative_to(legacy_root)
    except ValueError as exc:
        raise SystemExit(f"legacy package must remain under {legacy_root}: {package}") from exc
    if not package.is_dir():
        raise SystemExit(f"legacy package directory does not exist: {package}")
    manifest_path = package / "manifest.json"
    manifest_raw = _stable_bytes(manifest_path)
    manifest = _strict_json(manifest_raw, label=str(manifest_path))
    files: list[dict[str, Any]] = []
    tree = hashlib.sha256()
    for path in sorted(package.rglob("*")):
        _reject_symlinks(path)
        if path.is_dir():
            continue
        raw = _stable_bytes(path)
        relative = path.relative_to(package).as_posix()
        digest = hashlib.sha256(raw).hexdigest()
        tree.update(relative.encode("utf-8"))
        tree.update(b"\0")
        tree.update(digest.encode("ascii"))
        tree.update(b"\n")
        files.append({"path": relative, "sha256": digest, "size": len(raw)})
    return {
        "schema": "android-patch-capture-legacy-read-v1",
        "source_package": str(package),
        "source_manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(),
        "source_schema_version": manifest.get("schema_version"),
        "source_status": manifest.get("status"),
        "normalized_classification": {"layer": "platform"},
        "read_only": True,
        "history_rewritten": False,
        "copied_to_new_root": False,
        "tree_sha256": tree.hexdigest(),
        "files": files,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read a legacy android-framework-patch-capture package without modifying it."
    )
    parser.add_argument("--package", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    print(json.dumps(inspect_legacy_package(args.package), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
