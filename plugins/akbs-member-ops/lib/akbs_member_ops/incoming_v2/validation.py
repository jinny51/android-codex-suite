"""Read, check, and byte-preserve final Android change v2 packages."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import stat
from pathlib import Path, PurePosixPath
from typing import Any

from akbs_member_ops.artifact_paths import require_safe_artifact_path
from akbs_member_ops.member_config import default_codex_home

from .schema import SchemaError, load_json_bytes, validate_document


PLUGIN_ROOT = Path(__file__).resolve().parents[3]
PACKAGE_SCHEMA_PATH = (
    PLUGIN_ROOT / "contracts" / "incoming" / "v2" / "akbs-android-change-package.schema.json"
)
PACKAGE_SCHEMA_SHA256 = "b546e67e530c51f8318885da38d88c0bcc3850464048b49e270fd052ad1834dc"
PACKAGE_IDENTITY = ("akbs-android-change-package-v2", "2", "android_change")
PACKAGE_CONTRACT = "akbs-android-change-package-v2/2/android_change"


class AndroidChangeV2Error(ValueError):
    """A v2 package is unsafe, malformed, or internally incoherent."""


def _raise_schema(exc: SchemaError) -> None:
    raise AndroidChangeV2Error(str(exc)) from exc


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _manifest_path(value: Path, *, allow_descriptor_root: bool = False) -> Path:
    path = value.expanduser()
    if path.is_symlink() and not allow_descriptor_root:
        raise AndroidChangeV2Error(
            f"Android change v2 package path cannot be a symbolic link: {path}"
        )
    if path.is_dir():
        path = path / "manifest.json"
    elif path.parent.is_symlink() and not allow_descriptor_root:
        raise AndroidChangeV2Error(
            f"Android change v2 package directory cannot be a symbolic link: {path.parent}"
        )
    if path.name != "manifest.json":
        raise AndroidChangeV2Error(
            "Android change v2 input must be a package directory or manifest.json"
        )
    if path.is_symlink() or not path.is_file():
        raise AndroidChangeV2Error(
            f"Android change v2 manifest is not a regular file: {path}"
        )
    return path


def _load_contract() -> dict[str, Any]:
    try:
        raw = PACKAGE_SCHEMA_PATH.read_bytes()
    except OSError as exc:
        raise AndroidChangeV2Error(
            f"cannot read bundled Android change v2 contract: {exc}"
        ) from exc
    if _sha256(raw) != PACKAGE_SCHEMA_SHA256:
        raise AndroidChangeV2Error("bundled Android change v2 contract hash differs")
    try:
        return load_json_bytes(raw, label=str(PACKAGE_SCHEMA_PATH))
    except SchemaError as exc:
        _raise_schema(exc)
    raise AssertionError("unreachable")


def _load_manifest(
    value: Path, *, validate_schema: bool = True, allow_descriptor_root: bool = False
) -> tuple[Path, bytes, dict[str, Any]]:
    path = _manifest_path(value, allow_descriptor_root=allow_descriptor_root)
    try:
        raw = path.read_bytes()
        package = load_json_bytes(raw, label=str(path))
    except OSError as exc:
        raise AndroidChangeV2Error(
            f"cannot read Android change v2 manifest: {path}: {exc}"
        ) from exc
    except SchemaError as exc:
        _raise_schema(exc)
    identity = (
        package.get("schema"),
        package.get("schema_version"),
        package.get("package_kind"),
    )
    if identity != PACKAGE_IDENTITY:
        raise AndroidChangeV2Error(
            "package identity is not akbs-android-change-package-v2/2/android_change"
        )
    if validate_schema:
        try:
            validate_document(package, _load_contract())
        except SchemaError as exc:
            _raise_schema(exc)
    return path, raw, package


def _normalized_archive_path(value: object) -> str:
    if not isinstance(value, str) or not value or value.startswith("/") or "\\" in value:
        raise AndroidChangeV2Error("Android change v2 archive path is unsafe")
    path = PurePosixPath(value)
    normalized = path.as_posix()
    if (
        value != normalized
        or value in {".", ".."}
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise AndroidChangeV2Error("Android change v2 archive path is not canonical")
    return normalized


def _require_canonical_json_domain(value: Any, *, path: str = "$") -> None:
    if value is None or isinstance(value, (bool, str, int)):
        return
    if isinstance(value, float):
        raise AndroidChangeV2Error(
            f"AKBS canonical JSON v1 forbids floating-point numbers: {path}"
        )
    if isinstance(value, list):
        for index, item in enumerate(value):
            _require_canonical_json_domain(item, path=f"{path}/{index}")
        return
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise AndroidChangeV2Error(
                f"AKBS canonical JSON v1 requires text object keys: {path}"
            )
        for key, item in value.items():
            _require_canonical_json_domain(item, path=f"{path}/{key}")
        return
    raise AndroidChangeV2Error(f"AKBS canonical JSON v1 unsupported value: {path}")


def canonical_json_sha256(value: Any) -> str:
    _require_canonical_json_domain(value)
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return _sha256(raw)


def source_package_key(package: dict[str, Any]) -> str:
    identity = package.get("identity") or {}
    member_alias = identity.get("member_alias")
    run_id = identity.get("run_id")
    if not isinstance(member_alias, str) or not isinstance(run_id, str):
        raise AndroidChangeV2Error("Android change v2 source package identity differs")
    return f"{run_id[:8]}/{member_alias}/{run_id}"


def _stream_regular_file(
    path: Path, *, destination: Path | None = None
) -> tuple[str, int]:
    """Hash, and optionally copy, one no-follow regular file with bounded memory."""
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        source_fd = os.open(path, flags)
    except OSError as exc:
        raise AndroidChangeV2Error(
            f"cannot securely open Android change v2 archive entry: {path}: {exc}"
        ) from exc
    try:
        before = os.fstat(source_fd)
        if not stat.S_ISREG(before.st_mode):
            raise AndroidChangeV2Error(
                f"Android change v2 archive entry is not a regular file: {path}"
            )
        digest = hashlib.sha256()
        size = 0
        output = None
        try:
            if destination is not None:
                destination.parent.mkdir(parents=True, exist_ok=True)
                output = destination.open("xb")
            while True:
                chunk = os.read(source_fd, 1024 * 1024)
                if not chunk:
                    break
                if output is not None:
                    output.write(chunk)
                digest.update(chunk)
                size += len(chunk)
        except OSError as exc:
            raise AndroidChangeV2Error(
                f"cannot stream Android change v2 archive entry: {path}: {exc}"
            ) from exc
        finally:
            if output is not None:
                output.close()
        after = os.fstat(source_fd)
        try:
            rebound = os.stat(path, follow_symlinks=False)
        except OSError as exc:
            raise AndroidChangeV2Error(
                f"Android change v2 archive entry changed while reading: {path}: {exc}"
            ) from exc
        if (
            not os.path.samestat(before, after)
            or not os.path.samestat(before, rebound)
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or before.st_ctime_ns != after.st_ctime_ns
            or size != before.st_size
        ):
            raise AndroidChangeV2Error(
                f"Android change v2 archive entry changed while reading: {path}"
            )
        return digest.hexdigest(), size
    finally:
        os.close(source_fd)


def _archive_inventory(package_dir: Path) -> dict[str, tuple[str, int]]:
    inventory: dict[str, tuple[str, int]] = {}
    try:
        candidates = list(package_dir.rglob("*"))
    except OSError as exc:
        raise AndroidChangeV2Error(
            f"cannot enumerate Android change v2 package: {exc}"
        ) from exc
    for path in candidates:
        relative = path.relative_to(package_dir).as_posix()
        if path.is_symlink():
            raise AndroidChangeV2Error(
                f"Android change v2 archive contains a symlink: {relative}"
            )
        try:
            mode = path.stat(follow_symlinks=False).st_mode
        except OSError as exc:
            raise AndroidChangeV2Error(
                f"cannot stat Android change v2 archive entry: {relative}: {exc}"
            ) from exc
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode):
            raise AndroidChangeV2Error(
                f"Android change v2 archive contains a special entry: {relative}"
            )
        normalized = _normalized_archive_path(relative)
        if normalized in inventory:
            raise AndroidChangeV2Error(
                f"duplicate Android change v2 archive path: {normalized}"
            )
        inventory[normalized] = _stream_regular_file(path)
    return inventory


def _objects_by_id(package: dict[str, Any], name: str) -> dict[str, dict[str, Any]]:
    rows = package.get(name)
    if not isinstance(rows, list) or not rows or any(not isinstance(row, dict) for row in rows):
        raise AndroidChangeV2Error(f"Android change v2 {name} must be non-empty objects")
    identifiers = [row.get("id") for row in rows]
    if any(not isinstance(item, str) for item in identifiers) or len(identifiers) != len(
        set(identifiers)
    ):
        raise AndroidChangeV2Error(f"Android change v2 {name} IDs must be unique")
    return {str(row["id"]): row for row in rows}


def _validate_semantics(
    package_dir: Path,
    manifest_bytes: bytes,
    package: dict[str, Any],
    inventory: dict[str, tuple[str, int]],
) -> dict[str, Any]:
    components = _objects_by_id(package, "components")
    sources = _objects_by_id(package, "sources")
    patches = _objects_by_id(package, "patches")
    evidence = _objects_by_id(package, "evidence")
    primary = (package.get("subject") or {}).get("primary_component_id")
    if primary not in components:
        raise AndroidChangeV2Error("Android change v2 primary component does not resolve")

    patch_components: set[str] = set()
    evidence_components: set[str] = set()
    used_sources: set[str] = set()
    for patch in patches.values():
        component_ids = set(patch.get("component_ids") or [])
        source_id = patch.get("source_id")
        if not component_ids or not component_ids.issubset(components):
            raise AndroidChangeV2Error("Android change v2 patch component references differ")
        if source_id not in sources:
            raise AndroidChangeV2Error("Android change v2 patch source reference differs")
        patch_components.update(component_ids)
        used_sources.add(str(source_id))
    for item in evidence.values():
        component_ids = set(item.get("component_ids") or [])
        if not component_ids or not component_ids.issubset(components):
            raise AndroidChangeV2Error("Android change v2 evidence component references differ")
        evidence_components.update(component_ids)
        relative = _normalized_archive_path(item.get("path"))
        evidence_path = package_dir / relative
        if evidence_path.is_symlink() or not evidence_path.is_file():
            raise AndroidChangeV2Error(f"declared JSON evidence is missing or unsafe: {relative}")
        try:
            payload = load_json_bytes(evidence_path.read_bytes(), label=str(evidence_path))
        except OSError as exc:
            raise AndroidChangeV2Error(
                f"cannot read declared JSON evidence: {relative}: {exc}"
            ) from exc
        except SchemaError as exc:
            _raise_schema(exc)
        if payload.get("kind") != item.get("kind"):
            raise AndroidChangeV2Error(
                f"Android change v2 evidence kind differs from manifest: {relative}"
            )
    component_ids = set(components)
    if patch_components != component_ids:
        raise AndroidChangeV2Error("every Android change v2 component must have a patch")
    if evidence_components != component_ids:
        raise AndroidChangeV2Error("every Android change v2 component must have evidence")
    if used_sources != set(sources):
        raise AndroidChangeV2Error("every Android change v2 source must be used by a patch")

    descriptors = [package["readme"], *patches.values(), *evidence.values()]
    paths = [_normalized_archive_path(row.get("path")) for row in descriptors]
    if len(paths) != len(set(paths)):
        raise AndroidChangeV2Error("Android change v2 payload paths must be unique")
    expected = {
        "manifest.json": (_sha256(manifest_bytes), len(manifest_bytes)),
        **{
            _normalized_archive_path(row["path"]): (row.get("sha256"), row.get("size_bytes"))
            for row in descriptors
        },
    }
    if inventory != expected:
        raise AndroidChangeV2Error(
            "Android change v2 archive inventory or file integrity differs"
        )
    return {
        "schema": "akbs-android-change-package-coherence-v2",
        "schema_validation_complete": True,
        "reference_integrity_valid": True,
        "archive_inventory_binding_valid": True,
    }


def read_package(value: Path) -> dict[str, Any]:
    """Read and schema-check a v2 manifest without walking its payload."""
    path, raw, package = _load_manifest(value)
    return {
        "status": "PASS",
        "operation": "read",
        "contract": PACKAGE_CONTRACT,
        "manifest": str(path),
        "manifest_sha256": _sha256(raw),
        "source_package_key": source_package_key(package),
        "package_status": package["package_status"],
        "primary_component_id": package["subject"]["primary_component_id"],
        "component_layers": sorted({item["layer"] for item in package["components"]}),
        "target": package["subject"]["target"],
    }


def check_package(value: Path, *, _allow_descriptor_root: bool = False) -> dict[str, Any]:
    """Validate schema, references, JSON evidence, and exact package bytes."""
    manifest_path, manifest_bytes, package = _load_manifest(
        value, allow_descriptor_root=_allow_descriptor_root
    )
    inventory = _archive_inventory(manifest_path.parent)
    coherence = _validate_semantics(
        manifest_path.parent, manifest_bytes, package, inventory
    )
    return {
        "status": "PASS",
        "operation": "check",
        "contract": PACKAGE_CONTRACT,
        "package": str(manifest_path.parent),
        "manifest_sha256": _sha256(manifest_bytes),
        "archive_inventory_sha256": canonical_json_sha256(
            [[path, digest, size] for path, (digest, size) in sorted(inventory.items())]
        ),
        "source_package_key": source_package_key(package),
        "component_layers": sorted({item["layer"] for item in package["components"]}),
        "target": package["subject"]["target"],
        "coherence": coherence,
    }


def _target_pending_root() -> Path:
    return require_safe_artifact_path(
        Path(default_codex_home())
        / "artifacts"
        / "akbs-member-ops"
        / "android-change-v2"
        / "pending",
        purpose="Android change v2 pending root",
    )


def _secure_directory_flags() -> int:
    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    return flags


def _require_secure_dirfd_support() -> None:
    required = (os.open, os.mkdir, os.rename, os.stat)
    if (
        not hasattr(os, "O_DIRECTORY")
        or not hasattr(os, "O_NOFOLLOW")
        or any(function not in os.supports_dir_fd for function in required)
    ):
        raise AndroidChangeV2Error(
            "Android change v2 prepare requires no-follow directory descriptor support"
        )


def _open_real_child_directory(parent_fd: int, name: str) -> int:
    try:
        os.mkdir(name, mode=0o700, dir_fd=parent_fd)
    except FileExistsError:
        pass
    except OSError as exc:
        raise AndroidChangeV2Error(
            f"cannot create Android change v2 pending directory {name}: {exc}"
        ) from exc
    try:
        child_fd = os.open(name, _secure_directory_flags(), dir_fd=parent_fd)
    except OSError as exc:
        raise AndroidChangeV2Error(
            f"Android change v2 pending path is a symlink or not a real directory: {name}"
        ) from exc
    try:
        entry = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        opened = os.fstat(child_fd)
        if not stat.S_ISDIR(entry.st_mode) or not os.path.samestat(entry, opened):
            raise AndroidChangeV2Error(
                f"Android change v2 pending path is a symlink or not a real directory: {name}"
            )
    except BaseException:
        os.close(child_fd)
        raise
    return child_fd


def _open_directory_view(directory_fd: int) -> Path:
    for base in (Path("/proc/self/fd"), Path("/dev/fd")):
        candidate = base / str(directory_fd)
        if candidate.is_dir():
            return candidate
    raise AndroidChangeV2Error(
        "Android change v2 prepare cannot expose a descriptor-anchored pending directory"
    )


def _entry_matches_open_directory(parent_fd: int, name: str, child_fd: int) -> bool:
    try:
        entry = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except (FileNotFoundError, OSError):
        return False
    return stat.S_ISDIR(entry.st_mode) and os.path.samestat(entry, os.fstat(child_fd))


def _entry_matches_stat(parent_fd: int, name: str, expected: os.stat_result) -> bool:
    try:
        entry = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except (FileNotFoundError, OSError):
        return False
    return stat.S_ISDIR(entry.st_mode) and os.path.samestat(entry, expected)


def _entry_exists(parent_fd: int, name: str) -> bool:
    try:
        os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return True


def prepare_package(value: Path, *, pending_root: Path | None = None) -> dict[str, Any]:
    """Check and byte-preserve a final package under the member pending root."""
    checked = check_package(value)
    source = _manifest_path(value).parent
    _manifest, manifest_bytes, package = _load_manifest(source)
    if (
        _sha256(manifest_bytes) != checked["manifest_sha256"]
        or source_package_key(package) != checked["source_package_key"]
    ):
        raise AndroidChangeV2Error("Android change v2 source changed after validation")
    source_inventory = _archive_inventory(source)
    source_inventory_sha256 = canonical_json_sha256(
        [[path, digest, size] for path, (digest, size) in sorted(source_inventory.items())]
    )
    if source_inventory_sha256 != checked["archive_inventory_sha256"]:
        raise AndroidChangeV2Error("Android change v2 source inventory changed after validation")

    identity = package["identity"]
    _require_secure_dirfd_support()
    root_input = pending_root.expanduser() if pending_root is not None else _target_pending_root()
    if root_input.is_symlink():
        raise AndroidChangeV2Error("Android change v2 pending root cannot be a symbolic link")
    try:
        root_input.mkdir(parents=True, mode=0o700, exist_ok=True)
    except OSError as exc:
        raise AndroidChangeV2Error(
            f"cannot create Android change v2 pending root: {exc}"
        ) from exc
    if root_input.is_symlink() or not root_input.is_dir():
        raise AndroidChangeV2Error("Android change v2 pending root is not a real directory")
    root = root_input.resolve(strict=True)
    destination = root / identity["member_alias"] / identity["run_id"]
    source_resolved = source.resolve()
    try:
        root.relative_to(source_resolved)
    except ValueError:
        pass
    else:
        raise AndroidChangeV2Error(
            "Android change v2 pending root cannot be inside the source package"
        )
    try:
        source_resolved.relative_to(root)
    except ValueError:
        pass
    else:
        raise AndroidChangeV2Error(
            "Android change v2 source package cannot be inside its pending root"
        )

    root_fd = os.open(root, _secure_directory_flags())
    member_fd = -1
    temporary_fd = -1
    temporary_name = f".{identity['run_id']}.{secrets.token_hex(16)}"
    try:
        member_fd = _open_real_child_directory(root_fd, identity["member_alias"])
        if not _entry_matches_open_directory(root_fd, identity["member_alias"], member_fd):
            raise AndroidChangeV2Error("Android change v2 member directory changed during prepare")
        if _entry_exists(member_fd, identity["run_id"]):
            raise AndroidChangeV2Error(
                f"Android change v2 pending package already exists: {destination}"
            )
        os.mkdir(temporary_name, mode=0o700, dir_fd=member_fd)
        temporary_fd = os.open(temporary_name, _secure_directory_flags(), dir_fd=member_fd)
        temporary = _open_directory_view(temporary_fd)
        for relative_text, (expected_sha, expected_size) in sorted(source_inventory.items()):
            observed_sha, observed_size = _stream_regular_file(
                source / Path(relative_text), destination=temporary / Path(relative_text)
            )
            if observed_size != expected_size or observed_sha != expected_sha:
                raise AndroidChangeV2Error(
                    f"Android change v2 source changed while copying: {relative_text}"
                )
        if _archive_inventory(source) != source_inventory:
            raise AndroidChangeV2Error("Android change v2 source changed while copying")
        copied = check_package(temporary, _allow_descriptor_root=True)
        if any(
            copied[key] != checked[key]
            for key in ("manifest_sha256", "archive_inventory_sha256", "source_package_key")
        ):
            raise AndroidChangeV2Error("Android change v2 copied package identity differs")
        if not _entry_matches_open_directory(root_fd, identity["member_alias"], member_fd):
            raise AndroidChangeV2Error("Android change v2 member directory changed during prepare")
        if _entry_exists(member_fd, identity["run_id"]):
            raise AndroidChangeV2Error(
                f"Android change v2 pending package already exists: {destination}"
            )
        temporary_stat = os.fstat(temporary_fd)
        os.close(temporary_fd)
        temporary_fd = -1
        if not _entry_matches_stat(member_fd, temporary_name, temporary_stat):
            raise AndroidChangeV2Error("Android change v2 temporary directory changed during prepare")
        os.rename(
            temporary_name,
            identity["run_id"],
            src_dir_fd=member_fd,
            dst_dir_fd=member_fd,
        )
        if (
            not _entry_matches_open_directory(root_fd, identity["member_alias"], member_fd)
            or not _entry_matches_stat(member_fd, identity["run_id"], temporary_stat)
        ):
            raise AndroidChangeV2Error("Android change v2 pending path changed during publication")
        published = check_package(destination)
        if any(
            published[key] != checked[key]
            for key in ("manifest_sha256", "archive_inventory_sha256", "source_package_key")
        ):
            raise AndroidChangeV2Error("Android change v2 published package identity differs")
    finally:
        if temporary_fd >= 0:
            os.close(temporary_fd)
        if member_fd >= 0:
            os.close(member_fd)
        os.close(root_fd)
    return {
        "status": "PASS",
        "operation": "prepare",
        "contract": PACKAGE_CONTRACT,
        "package": str(destination),
        "source_package_key": checked["source_package_key"],
        "manifest_sha256": checked["manifest_sha256"],
        "archive_inventory_sha256": checked["archive_inventory_sha256"],
        "bytes_preserved": True,
        "coherence": published["coherence"],
    }
