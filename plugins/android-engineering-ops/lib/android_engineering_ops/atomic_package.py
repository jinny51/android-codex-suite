"""Publish a completed local package with no partial destination state."""

from __future__ import annotations

import ctypes
import errno
import os
import secrets
import shutil
import tempfile
import stat
from pathlib import Path


class AtomicPackageError(RuntimeError):
    """A package could not be copied and committed without path races."""


_DIRECTORY_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)
_FILE_READ_FLAGS = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
_FILE_WRITE_FLAGS = (
    os.O_WRONLY
    | os.O_CREAT
    | os.O_EXCL
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)


def _identity(info: os.stat_result) -> tuple[int, int, int, int]:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _open_directory_chain(path: Path, *, create: bool) -> int:
    absolute = Path(os.path.abspath(os.fspath(path.expanduser())))
    if not absolute.is_absolute() or not absolute.anchor:
        raise AtomicPackageError(f"package directory must be absolute: {path}")
    descriptor = os.open(absolute.anchor, _DIRECTORY_FLAGS)
    try:
        for part in absolute.parts[1:]:
            if create:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
            child = os.open(part, _DIRECTORY_FLAGS, dir_fd=descriptor)
            info = os.fstat(child)
            if not stat.S_ISDIR(info.st_mode):
                os.close(child)
                raise AtomicPackageError(
                    f"package directory chain contains a non-directory: {absolute}"
                )
            os.close(descriptor)
            descriptor = child
    except OSError as exc:
        os.close(descriptor)
        raise AtomicPackageError(
            f"package directory chain is missing, replaced, or symbolic: {absolute}: {exc}"
        ) from exc
    return descriptor


def _assert_path_identity(path: Path, descriptor: int) -> None:
    observed = _open_directory_chain(path, create=False)
    try:
        if _identity(os.fstat(observed)) != _identity(os.fstat(descriptor)):
            raise AtomicPackageError(f"package parent path identity changed: {path}")
    finally:
        os.close(observed)


def _copy_regular_file(source_fd: int, destination_fd: int, name: str) -> None:
    source = os.open(name, _FILE_READ_FLAGS, dir_fd=source_fd)
    target = -1
    try:
        before = os.fstat(source)
        if not stat.S_ISREG(before.st_mode):
            raise AtomicPackageError(f"package source entry is not a regular file: {name}")
        target = os.open(name, _FILE_WRITE_FLAGS, mode=0o600, dir_fd=destination_fd)
        while True:
            chunk = os.read(source, 1024 * 1024)
            if not chunk:
                break
            view = memoryview(chunk)
            while view:
                written = os.write(target, view)
                view = view[written:]
        os.fsync(target)
        after = os.fstat(source)
        if _identity(before) != _identity(after):
            raise AtomicPackageError(f"package source file changed while copied: {name}")
    finally:
        if target >= 0:
            os.close(target)
        os.close(source)


def _copy_tree_fd(source_fd: int, destination_fd: int) -> None:
    before = os.fstat(source_fd)
    for name in sorted(os.listdir(source_fd)):
        info = os.stat(name, dir_fd=source_fd, follow_symlinks=False)
        if stat.S_ISLNK(info.st_mode):
            raise AtomicPackageError(f"package source contains a symlink: {name}")
        if stat.S_ISREG(info.st_mode):
            _copy_regular_file(source_fd, destination_fd, name)
            continue
        if not stat.S_ISDIR(info.st_mode):
            raise AtomicPackageError(f"package source contains a special entry: {name}")
        os.mkdir(name, mode=0o700, dir_fd=destination_fd)
        source_child = os.open(name, _DIRECTORY_FLAGS, dir_fd=source_fd)
        destination_child = os.open(name, _DIRECTORY_FLAGS, dir_fd=destination_fd)
        try:
            _copy_tree_fd(source_child, destination_child)
            os.fsync(destination_child)
        finally:
            os.close(destination_child)
            os.close(source_child)
    after = os.fstat(source_fd)
    if _identity(before) != _identity(after):
        raise AtomicPackageError("package source directory changed while copied")


def _clear_tree_fd(descriptor: int) -> None:
    for name in os.listdir(descriptor):
        info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        if stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode):
            child = os.open(name, _DIRECTORY_FLAGS, dir_fd=descriptor)
            try:
                _clear_tree_fd(child)
            finally:
                os.close(child)
            os.rmdir(name, dir_fd=descriptor)
        else:
            os.unlink(name, dir_fd=descriptor)


def _rename_noreplace(parent_fd: int, source: str, destination: str) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    encoded_source = os.fsencode(source)
    encoded_destination = os.fsencode(destination)
    if hasattr(libc, "renameat2"):
        operation = libc.renameat2
        operation.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        operation.restype = ctypes.c_int
        result = operation(parent_fd, encoded_source, parent_fd, encoded_destination, 1)
    elif hasattr(libc, "renameatx_np"):
        operation = libc.renameatx_np
        operation.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        operation.restype = ctypes.c_int
        result = operation(parent_fd, encoded_source, parent_fd, encoded_destination, 0x00000004)
    else:
        raise AtomicPackageError("OS has no atomic no-replace directory rename")
    if result == 0:
        return
    error = ctypes.get_errno()
    if error in {errno.EEXIST, errno.ENOTEMPTY}:
        raise AtomicPackageError(f"package destination already exists: {destination}")
    if error in {errno.EINVAL, errno.ENOSYS, errno.EOPNOTSUPP}:
        # DrvFS and a few older filesystems reject the no-replace flag even
        # though a same-directory rename is atomic.  The caller holds an
        # exclusive lock on this exact parent descriptor.  Re-check through
        # that descriptor immediately before the fallback rename so concurrent
        # plugin publishers still have one winner and an existing entry is
        # never intentionally replaced.
        try:
            os.stat(destination, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise AtomicPackageError(
                f"package destination already exists: {destination}"
            )
        try:
            os.rename(
                source,
                destination,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
            )
        except OSError as fallback_error:
            if fallback_error.errno in {errno.EEXIST, errno.ENOTEMPTY}:
                raise AtomicPackageError(
                    f"package destination already exists: {destination}"
                ) from fallback_error
            raise AtomicPackageError(
                "atomic package rename fallback failed: "
                f"{fallback_error.strerror or fallback_error}"
            ) from fallback_error
        return
    raise AtomicPackageError(
        f"atomic no-replace package rename failed: {os.strerror(error)}"
    )


def _windows_path_info(path: Path) -> os.stat_result:
    info = path.lstat()
    if info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        raise AtomicPackageError(f"package path contains a reparse point: {path}")
    return info


def _windows_directory_chain(path: Path, *, create: bool) -> os.stat_result:
    current = Path(path.anchor)
    info = _windows_path_info(current)
    if not stat.S_ISDIR(info.st_mode):
        raise AtomicPackageError(f"package directory anchor is invalid: {current}")
    for part in path.parts[1:]:
        current /= part
        if create:
            current.mkdir(exist_ok=True)
        info = _windows_path_info(current)
        if not stat.S_ISDIR(info.st_mode):
            raise AtomicPackageError(f"package directory chain is invalid: {current}")
    return info


def _copy_windows_tree(source: Path, stage: Path) -> None:
    before = _windows_path_info(source)
    for child in source.iterdir():
        info = _windows_path_info(child)
        target = stage / child.name
        if stat.S_ISDIR(info.st_mode):
            target.mkdir()
            _copy_windows_tree(child, target)
        elif stat.S_ISREG(info.st_mode):
            with child.open("rb") as current, target.open("xb") as destination:
                if _identity(info) != _identity(os.fstat(current.fileno())):
                    raise AtomicPackageError(f"package source was replaced before copy: {child}")
                shutil.copyfileobj(current, destination, length=1024 * 1024)
                destination.flush()
                os.fsync(destination.fileno())
                if _identity(info) != _identity(os.fstat(current.fileno())):
                    raise AtomicPackageError(f"package source changed during copy: {child}")
            if _identity(info) != _identity(_windows_path_info(child)):
                raise AtomicPackageError(f"package source was replaced during copy: {child}")
        else:
            raise AtomicPackageError(f"package contains a non-regular source entry: {child}")
    if _identity(before) != _identity(_windows_path_info(source)):
        raise AtomicPackageError(f"package source directory changed during copy: {source}")


def _publish_windows(source: Path, destination: Path) -> Path:
    _windows_directory_chain(source, create=False)
    parent = _windows_directory_chain(destination.parent, create=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{destination.name}.staging-", dir=destination.parent))
    staged = _windows_path_info(stage)
    try:
        _copy_windows_tree(source, stage)
        if not os.path.samestat(parent, _windows_directory_chain(destination.parent, create=False)):
            raise AtomicPackageError(f"package parent path identity changed: {destination.parent}")
        if not os.path.samestat(staged, _windows_path_info(stage)):
            raise AtomicPackageError(f"package staging path identity changed: {stage}")
        # Windows rename without replacement is atomic within the same directory.
        os.rename(stage, destination)
        return destination
    except FileExistsError as exc:
        raise AtomicPackageError(f"package destination already exists: {destination}") from exc
    finally:
        if stage.exists() and os.path.samestat(staged, _windows_path_info(stage)):
            shutil.rmtree(stage)


def publish_tree_atomic(source: Path, destination: Path) -> Path:
    """Copy `source` into same-parent staging, fsync, then atomically install it."""
    source_path = Path(os.path.abspath(os.fspath(source.expanduser())))
    destination_path = Path(os.path.abspath(os.fspath(destination.expanduser())))
    if not destination_path.name or destination_path.name in {".", ".."}:
        raise AtomicPackageError(f"invalid package destination: {destination_path}")
    if os.name == "nt":
        if destination_path.is_relative_to(source_path):
            raise AtomicPackageError("package destination must be outside the source tree")
        try:
            return _publish_windows(source_path, destination_path)
        except OSError as exc:
            raise AtomicPackageError(f"Windows package publication failed: {exc}") from exc

    import fcntl

    source_fd = _open_directory_chain(source_path, create=False)
    parent_fd = _open_directory_chain(destination_path.parent, create=True)
    stage_fd = -1
    stage_name = f".{destination_path.name}.staging-{os.getpid()}-{secrets.token_hex(8)}"
    try:
        fcntl.flock(parent_fd, fcntl.LOCK_EX)
        _assert_path_identity(destination_path.parent, parent_fd)
        try:
            os.stat(destination_path.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise AtomicPackageError(
                f"package destination already exists: {destination_path}"
            )
        os.mkdir(stage_name, mode=0o700, dir_fd=parent_fd)
        stage_fd = os.open(stage_name, _DIRECTORY_FLAGS, dir_fd=parent_fd)
        _copy_tree_fd(source_fd, stage_fd)
        os.fsync(stage_fd)
        _assert_path_identity(destination_path.parent, parent_fd)
        stage_identity = _identity(os.fstat(stage_fd))
        _rename_noreplace(parent_fd, stage_name, destination_path.name)
        os.fsync(parent_fd)
        # DrvFS keeps the rename pending while the moved directory descriptor
        # remains open.  Capture its identity first, then close that descriptor
        # exactly once so the completed destination becomes observable.
        os.close(stage_fd)
        stage_fd = -1
        try:
            installed = os.open(
                destination_path.name, _DIRECTORY_FLAGS, dir_fd=parent_fd
            )
        except FileNotFoundError:
            # DrvFS can make a completed same-directory rename visible through a
            # freshly walked path before the already-open parent descriptor sees
            # the new name.  Re-walk with O_NOFOLLOW and re-bind the parent before
            # accepting the package; do not retry or repeat the rename.
            _assert_path_identity(destination_path.parent, parent_fd)
            installed = _open_directory_chain(destination_path, create=False)
        try:
            if _identity(os.fstat(installed)) != stage_identity:
                raise AtomicPackageError("published package identity differs from staging")
        finally:
            os.close(installed)
        return destination_path
    except BaseException:
        if stage_fd >= 0:
            try:
                stage_info = os.fstat(stage_fd)
                entry_info = os.stat(stage_name, dir_fd=parent_fd, follow_symlinks=False)
                if _identity(stage_info) == _identity(entry_info):
                    _clear_tree_fd(stage_fd)
                    os.rmdir(stage_name, dir_fd=parent_fd)
            except OSError:
                pass
        raise
    finally:
        if stage_fd >= 0:
            os.close(stage_fd)
        os.close(parent_fd)
        os.close(source_fd)
