#!/usr/bin/env python3
"""Fail closed unless this exact engineering plugin is the sole active family.

The migration contract permits either the immutable legacy install family or the
current target family, never both. Real engineering entry points call this module
before reading source, creating state, dispatching remotely, or writing artifacts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping


TARGET_PLUGIN = "android-engineering-ops"
OFFICIAL_MARKETPLACE = "android-codex-suite"
LEGACY_FAMILY = frozenset({"android-framework-ops", "android-wsl-ops", "android-mac-ops"})
PLUGIN_VERSION_RE = re.compile(r"^[0-9]+(?:\.[0-9]+){1,3}(?:[-+][0-9A-Za-z.-]+)?$")


class InstallFamilyError(RuntimeError):
    """The active Codex plugin inventory cannot prove one engineering owner."""


def _strict_inventory_json(raw: bytes) -> Mapping[str, Any]:
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise InstallFamilyError(f"plugin inventory repeats key: {key}")
            value[key] = item
        return value

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InstallFamilyError("Codex plugin inventory is not strict UTF-8 JSON") from exc
    if not isinstance(value, dict) or not isinstance(value.get("installed"), list):
        raise InstallFamilyError("Codex plugin inventory has no installed list")
    if any(not isinstance(item, dict) for item in value["installed"]):
        raise InstallFamilyError("Codex plugin inventory contains a non-object entry")
    return value


def _read_active_inventory(codex_executable: str = "codex") -> Mapping[str, Any]:
    try:
        completed = subprocess.run(
            [codex_executable, "plugin", "list", "--json"],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise InstallFamilyError(f"Codex active plugin inventory is unavailable: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise InstallFamilyError(
            "Codex active plugin inventory failed"
            + (f": {detail[:500]}" if detail else "")
        )
    return _strict_inventory_json(completed.stdout)


def _strict_json_file(path: Path, *, label: str) -> dict[str, Any]:
    try:
        raw = _stable_bytes(path, label=label)

        def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, item in pairs:
                if key in result:
                    raise InstallFamilyError(f"{label} repeats key: {key}")
                result[key] = item
            return result

        def reject_constant(value: str) -> None:
            raise InstallFamilyError(f"{label} contains non-finite number: {value}")

        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InstallFamilyError(f"{label} is not strict UTF-8 JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise InstallFamilyError(f"{label} must be a JSON object: {path}")
    return payload


def _absolute_without_symlinks(path: Path, *, label: str) -> Path:
    absolute = Path(os.path.abspath(os.fspath(path.expanduser())))
    current = Path(absolute.anchor)
    try:
        for part in absolute.parts[1:]:
            current = current / part
            info = current.lstat()
            if stat.S_ISLNK(info.st_mode):
                raise InstallFamilyError(f"{label} contains a symlink: {current}")
    except OSError as exc:
        raise InstallFamilyError(f"cannot inspect {label}: {current}: {exc}") from exc
    return absolute


def _stable_bytes(path: Path, *, label: str) -> bytes:
    safe = _absolute_without_symlinks(path, label=label)
    descriptor = -1
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(safe, flags)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise InstallFamilyError(f"{label} is not a regular file: {safe}")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
    except OSError as exc:
        raise InstallFamilyError(f"cannot read {label}: {safe}: {exc}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    identity = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns)
    if identity(before) != identity(after):
        raise InstallFamilyError(f"{label} changed while being read: {safe}")
    return b"".join(chunks)


def _tree_digest_once(root: Path, *, label: str) -> tuple[str, tuple[int, int, int]]:
    safe_root = _absolute_without_symlinks(root, label=label)
    root_before = safe_root.stat()
    digest = hashlib.sha256()
    for path in sorted(
        safe_root.rglob("*"), key=lambda item: item.relative_to(safe_root).as_posix()
    ):
        relative = path.relative_to(safe_root)
        if "__pycache__" in relative.parts or path.suffix == ".pyc":
            continue
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise InstallFamilyError(f"{label} contains a symlink: {relative.as_posix()}")
        if stat.S_ISDIR(info.st_mode):
            digest.update(b"D\0" + relative.as_posix().encode("utf-8") + b"\0")
            continue
        if not stat.S_ISREG(info.st_mode):
            raise InstallFamilyError(f"{label} contains a non-regular entry: {relative.as_posix()}")
        raw = _stable_bytes(path, label=f"{label} file")
        digest.update(b"F\0" + relative.as_posix().encode("utf-8") + b"\0")
        # Source and execution cache must agree on whether a file is executable.
        # Normalizing to one bit keeps the identity stable across filesystems that
        # represent the remaining POSIX permission bits differently (for example
        # a Windows-backed Codex cache mounted through DrvFS).
        executable = b"1" if info.st_mode & (
            stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
        ) else b"0"
        digest.update(b"X" + executable + b"\0")
        digest.update(hashlib.sha256(raw).digest())
    root_after = safe_root.stat()
    identity = lambda item: (item.st_dev, item.st_ino, item.st_mtime_ns)
    if identity(root_before) != identity(root_after):
        raise InstallFamilyError(f"{label} root changed while being hashed")
    return digest.hexdigest(), identity(root_after)


def _tree_content_sha256(root: Path, *, label: str) -> str:
    """Hash twice, binding stable roots/entries and excluding only Python caches."""
    first_digest, first_identity = _tree_digest_once(root, label=label)
    second_digest, second_identity = _tree_digest_once(root, label=label)
    if (first_digest, first_identity) != (second_digest, second_identity):
        raise InstallFamilyError(f"{label} changed between content-hash scans")
    return first_digest


def _cache_root(codex_home: Path, marketplace: str, name: str, version: str) -> Path:
    return codex_home / "plugins" / "cache" / marketplace / name / version


def _bind_inventory_plugin(
    row: Mapping[str, Any],
    *,
    expected_name: str,
    execution_root: Path | None = None,
    codex_home: Path,
) -> tuple[Path, dict[str, Any], str]:
    """Bind name@marketplace, version, installed root, and on-disk manifest."""
    name = row.get("name")
    version = row.get("version")
    marketplace = row.get("marketplaceName")
    plugin_id = row.get("pluginId")
    if name != expected_name:
        raise InstallFamilyError(f"active plugin name differs: {name!r}")
    if not isinstance(version, str) or not PLUGIN_VERSION_RE.fullmatch(version):
        raise InstallFamilyError(f"active {expected_name} version is missing or malformed")
    if not isinstance(marketplace, str) or not marketplace.strip():
        raise InstallFamilyError(f"active {expected_name} marketplaceName is missing")
    if expected_name == TARGET_PLUGIN and marketplace != OFFICIAL_MARKETPLACE:
        raise InstallFamilyError(
            f"active {expected_name} marketplaceName must be {OFFICIAL_MARKETPLACE}"
        )
    if plugin_id != f"{expected_name}@{marketplace}":
        raise InstallFamilyError(
            f"active {expected_name} pluginId must equal name@marketplaceName"
        )
    source = row.get("source")
    if (
        not isinstance(source, Mapping)
        or source.get("source") != "local"
        or not isinstance(source.get("path"), str)
        or not source["path"]
    ):
        raise InstallFamilyError(f"active {expected_name} has no local installed root")
    raw_root = Path(source["path"]).expanduser()
    if not raw_root.is_absolute():
        raise InstallFamilyError(f"active {expected_name} root must be absolute")
    observed = _absolute_without_symlinks(
        raw_root, label=f"active {expected_name} inventory source root"
    )
    if not observed.is_dir():
        raise InstallFamilyError(f"active {expected_name} root is not a directory")

    source_manifest_path = observed / ".codex-plugin/plugin.json"
    manifest = _strict_json_file(
        source_manifest_path, label=f"active {expected_name} source manifest"
    )
    if manifest.get("name") != expected_name or manifest.get("version") != version:
        raise InstallFamilyError(
            f"active {expected_name} inventory identity differs from its plugin manifest"
        )

    expected_cache = _cache_root(codex_home, marketplace, expected_name, version)
    try:
        runtime = _absolute_without_symlinks(
            expected_cache, label=f"active {expected_name} runtime cache root"
        )
    except InstallFamilyError as exc:
        raise InstallFamilyError(
            f"active {expected_name} runtime cache is unavailable: {exc}"
        ) from exc
    if not runtime.is_dir():
        raise InstallFamilyError(
            f"active {expected_name} runtime cache is not a directory"
        )
    if runtime == observed:
        raise InstallFamilyError(
            f"active {expected_name} inventory source and runtime cache must be different roots"
        )

    if execution_root is not None:
        expected = _absolute_without_symlinks(
            execution_root, label=f"executing {expected_name} root"
        )
        if expected != runtime:
            raise InstallFamilyError(
                f"executing {expected_name} root is not the inventory-bound runtime cache"
            )

    runtime_manifest_path = runtime / ".codex-plugin/plugin.json"
    runtime_manifest = _strict_json_file(
        runtime_manifest_path, label=f"active {expected_name} runtime manifest"
    )
    if runtime_manifest != manifest or _stable_bytes(
        runtime_manifest_path, label=f"active {expected_name} runtime manifest"
    ) != _stable_bytes(
        source_manifest_path, label=f"active {expected_name} source manifest"
    ):
        raise InstallFamilyError(
            f"active {expected_name} source and runtime manifest bytes differ"
        )
    if _tree_content_sha256(observed, label=f"{expected_name} source") != _tree_content_sha256(
        runtime, label=f"{expected_name} runtime cache"
    ):
        raise InstallFamilyError(
            f"active {expected_name} source and runtime content hashes differ"
        )
    return runtime, manifest, version


def assert_target_install_family(
    plugin_root: Path,
    *,
    inventory: Mapping[str, Any] | None = None,
    codex_executable: str = "codex",
    codex_home: Path | None = None,
) -> None:
    """Require one active target rooted at this plugin and no active legacy peer."""
    payload = inventory if inventory is not None else _read_active_inventory(codex_executable)
    entries = payload.get("installed") if isinstance(payload, Mapping) else None
    if not isinstance(entries, list) or any(not isinstance(item, Mapping) for item in entries):
        raise InstallFamilyError("Codex plugin inventory has no valid installed list")
    active = [
        item for item in entries
        if item.get("installed") is True and item.get("enabled") is True
    ]
    legacy = sorted(
        str(item.get("name")) for item in active if item.get("name") in LEGACY_FAMILY
    )
    target = [item for item in active if item.get("name") == TARGET_PLUGIN]
    if legacy:
        raise InstallFamilyError(
            "target and legacy Android engineering plugins are co-installed: "
            + ", ".join(legacy)
        )
    if len(target) != 1:
        raise InstallFamilyError(
            "exactly one active android-engineering-ops plugin is required"
        )
    home = (
        codex_home.expanduser()
        if codex_home is not None
        else Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser()
    )
    _root, _manifest, _core_version = _bind_inventory_plugin(
        target[0], expected_name=TARGET_PLUGIN, execution_root=plugin_root, codex_home=home
    )


def require_target_install_family(plugin_root: Path) -> None:
    """CLI boundary that preserves one concise, stable fail-closed diagnostic."""
    try:
        assert_target_install_family(plugin_root)
    except InstallFamilyError as exc:
        raise SystemExit(f"ANDROID_ENGINEERING_INSTALL_FAMILY_INVALID: {exc}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--plugin-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args(argv)
    try:
        assert_target_install_family(args.plugin_root)
    except InstallFamilyError as exc:
        print(f"ANDROID_ENGINEERING_INSTALL_FAMILY_INVALID: {exc}", file=sys.stderr)
        return 78
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
