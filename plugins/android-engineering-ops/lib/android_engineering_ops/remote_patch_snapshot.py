from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import tempfile
import time
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit, urlunsplit


REMOTE_PATCH_SNAPSHOT_SCHEMA = "android-remote-patch-snapshot-v1"
REMOTE_PATCH_SNAPSHOT_VERSION = 1

_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_WORKSPACE_RE = re.compile(r"[0-9a-f]{16}")
_GIT_HEAD_RE = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_TOP_FIELDS = frozenset(
    {
        "schema",
        "version",
        "workspace_id",
        "command_id",
        "remote_root",
        "generated_at_ns",
        "repo_status",
        "repositories",
        "snapshot_sha256",
    }
)
_REPOSITORY_FIELDS = frozenset(
    {
        "repo_path",
        "root",
        "head",
        "branch",
        "remotes",
        "status",
        "staged_diff",
        "unstaged_diff",
        "head_diff",
        "untracked_diff",
        "untracked",
        "changed_files",
    }
)
_BLOB_FIELDS = frozenset({"encoding", "size", "sha256", "data"})
_REPO_STATUS_FIELDS = frozenset({"available", "reason", "output"})
_REMOTE_FIELDS = frozenset({"name", "fetch_urls", "push_urls"})
_UNTRACKED_FIELDS = frozenset({"path", "kind", "size", "mtime_ns", "sha256"})


class RemotePatchSnapshotError(ValueError):
    """A remote patch snapshot is incomplete, stale, or untrusted."""


def _canonical_json_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _blob(data: bytes) -> dict[str, str | int]:
    return {
        "encoding": "base64",
        "size": len(data),
        "sha256": _sha256(data),
        "data": base64.b64encode(data).decode("ascii"),
    }


def decode_snapshot_blob(value: Mapping[str, Any], *, field: str) -> bytes:
    if not isinstance(value, Mapping) or frozenset(value) != _BLOB_FIELDS:
        raise RemotePatchSnapshotError(f"snapshot field {field} is not a closed binary blob")
    if value.get("encoding") != "base64":
        raise RemotePatchSnapshotError(f"snapshot field {field} has an unsupported encoding")
    size = value.get("size")
    digest = value.get("sha256")
    data = value.get("data")
    if type(size) is not int or size < 0:
        raise RemotePatchSnapshotError(f"snapshot field {field}.size is invalid")
    if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
        raise RemotePatchSnapshotError(f"snapshot field {field}.sha256 is invalid")
    if not isinstance(data, str):
        raise RemotePatchSnapshotError(f"snapshot field {field}.data is invalid")
    try:
        decoded = base64.b64decode(data, validate=True)
    except (ValueError, TypeError) as exc:
        raise RemotePatchSnapshotError(f"snapshot field {field}.data is not valid base64") from exc
    if len(decoded) != size:
        raise RemotePatchSnapshotError(f"snapshot field {field} size does not match its bytes")
    if _sha256(decoded) != digest:
        raise RemotePatchSnapshotError(f"snapshot field {field} sha256 does not match its bytes")
    return decoded


def _run_bytes(command: Sequence[str], cwd: Path, *, expected: tuple[int, ...] = (0,)) -> bytes:
    result = subprocess.run(
        list(command),
        cwd=str(cwd),
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode not in expected:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise RemotePatchSnapshotError(
            f"remote snapshot command failed ({result.returncode}): {' '.join(command)}: {detail}"
        )
    return result.stdout


def _canonical_repo_path(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise RemotePatchSnapshotError("repository path must be a non-empty POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or value != path.as_posix() or any(part in {"", ".", ".."} for part in path.parts):
        if value != ".":
            raise RemotePatchSnapshotError(f"repository path is not canonical and relative: {value}")
    return value


def _safe_relative_path(value: str, *, field: str) -> str:
    if not value or "\\" in value or any(ord(character) < 32 for character in value):
        raise RemotePatchSnapshotError(f"{field} is not a safe relative POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or value != path.as_posix() or any(part in {"", ".", ".."} for part in path.parts):
        raise RemotePatchSnapshotError(f"{field} is not a canonical relative POSIX path: {value}")
    return value


def _canonical_remote_root(value: str) -> str:
    if not isinstance(value, str) or not value.startswith("/") or value.startswith("//"):
        raise RemotePatchSnapshotError("remote root must be an absolute POSIX path")
    path = PurePosixPath(value)
    if value != path.as_posix() or any(ord(character) < 32 for character in value):
        raise RemotePatchSnapshotError("remote root must be canonical")
    return value


def _redact_remote_url(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    if "://" not in value:
        return value
    parts = urlsplit(value)
    hostname = parts.hostname or ""
    if not hostname:
        return value
    netloc = hostname
    if parts.port is not None:
        netloc += f":{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def parse_repository_files(
    repository_paths: Sequence[str], values: Sequence[str],
) -> dict[str, list[str]]:
    """Select exact files inside real Git repositories, never shell/Git globs."""
    repositories = {_canonical_repo_path(item) for item in repository_paths}
    selected: dict[str, list[str]] = {}
    for value in values:
        repo_path, separator, filename = value.partition("=")
        if not separator:
            raise RemotePatchSnapshotError("--repo-file requires REPO_PATH=EXACT_FILE")
        repo_path = _canonical_repo_path(repo_path)
        filename = _safe_relative_path(filename, field="selected file")
        if repo_path not in repositories:
            raise RemotePatchSnapshotError(f"selected file references an unrequested repository: {repo_path}")
        files = selected.setdefault(repo_path, [])
        if filename not in files:
            files.append(filename)
    if selected and set(selected) != repositories:
        missing = ", ".join(sorted(repositories - selected.keys()))
        raise RemotePatchSnapshotError(f"file-scoped capture requires files for every repository: {missing}")
    return selected


def _literal_paths(files: Sequence[str]) -> list[str]:
    return [f":(literal){name}" for name in files]


def _validate_rename_scope(root: Path, files: Sequence[str], status: bytes) -> None:
    """Do not silently turn an identifiable rename into a scoped add/delete."""
    if not files:
        return
    selected = {name.encode("utf-8") for name in files}
    entries = iter(status.split(b"\0"))
    for entry in entries:
        if entry.startswith(b"##") or len(entry) < 4:
            continue
        if b"R" in entry[:2] or b"C" in entry[:2]:
            destination, source = entry[3:], next(entries, b"")
            if source not in selected or destination not in selected:
                raise RemotePatchSnapshotError("file selection splits a rename; select both old and new paths")

    # A pathspec can make Git report one half of a staged rename as just A/D.
    # Ask Git about HEAD/index renames, including edited staged destinations.
    # This may inspect staged Git blobs, never unselected worktree output (out/).
    raw = _run_bytes(
        ["git", "diff", "--cached", "--raw", "-z", "--no-abbrev", "--find-renames", "HEAD", "--"],
        root,
    )
    entries = iter(raw.split(b"\0"))
    for header in entries:
        if not header:
            continue
        fields = header.split()
        name = next(entries, b"")
        if len(fields) != 5:
            raise RemotePatchSnapshotError("cannot read staged rename metadata")
        if fields[4].startswith((b"R", b"C")):
            destination = next(entries, b"")
            if not name or not destination:
                raise RemotePatchSnapshotError("cannot read staged rename paths")
            if (name in selected) != (destination in selected):
                raise RemotePatchSnapshotError("file selection splits a rename; select both old and new paths")


def _validate_selected_files(root: Path, files: Sequence[str]) -> None:
    for name in files:
        _safe_relative_path(name, field="selected file")
        parent = root
        for part in PurePosixPath(name).parts[:-1]:
            parent /= part
            if parent.is_symlink():
                raise RemotePatchSnapshotError(f"selected file has a symlink parent: {name}")
        path = root / name
        if path.is_symlink():
            continue  # Git captures the link itself, not its target.
        if path.exists():
            if not path.is_file():
                raise RemotePatchSnapshotError(f"selected path is not an exact file: {name}")
            continue
        # Deleted tracked files are legitimate selections; typos and directories are not.
        entries = _run_bytes(["git", "ls-tree", "-z", "HEAD", "--", *_literal_paths([name])], root)
        if not any(
            entry.partition(b"\t")[0].split()[1:2] == [b"blob"]
            and entry.partition(b"\t")[2] == name.encode("utf-8")
            for entry in entries.split(b"\0") if entry
        ):
            raise RemotePatchSnapshotError(f"selected file is missing or is not a tracked deletion: {name}")


def _remote_inventory(root: Path, files: Sequence[str] = ()) -> list[dict[str, Any]]:
    names = _run_bytes(
        ["git", "ls-files", "--others", "--exclude-standard", "-z", "--", *_literal_paths(files)],
        root,
    ).split(b"\0")
    inventory: list[dict[str, Any]] = []
    for raw_name in names:
        if not raw_name:
            continue
        try:
            name = raw_name.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise RemotePatchSnapshotError("untracked path is not valid UTF-8") from exc
        name = _safe_relative_path(name, field="untracked path")
        path = root / name
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise RemotePatchSnapshotError(f"untracked path disappeared during capture: {name}") from exc
        if stat.S_ISREG(metadata.st_mode):
            content = path.read_bytes()
            kind = "file"
        elif stat.S_ISLNK(metadata.st_mode):
            content = os.readlink(path).encode("utf-8")
            kind = "symlink"
        else:
            raise RemotePatchSnapshotError(f"unsupported untracked path type: {name}")
        final = path.lstat()
        if (metadata.st_size, metadata.st_mtime_ns, metadata.st_ino) != (
            final.st_size,
            final.st_mtime_ns,
            final.st_ino,
        ):
            raise RemotePatchSnapshotError(f"untracked path changed during capture: {name}")
        inventory.append(
            {
                "path": name,
                "kind": kind,
                "size": len(content),
                "mtime_ns": final.st_mtime_ns,
                "sha256": _sha256(content),
            }
        )
    return sorted(inventory, key=lambda item: str(item["path"]))


def _untracked_diff(root: Path, inventory: Sequence[Mapping[str, Any]]) -> bytes:
    chunks: list[bytes] = []
    for item in inventory:
        name = str(item["path"])
        chunks.append(
            _run_bytes(
                ["git", "diff", "--no-index", "--binary", "--full-index", "--", "/dev/null", name],
                root,
                expected=(0, 1),
            )
        )
    return b"".join(chunks)


def _changed_paths(
    root: Path, inventory: Sequence[Mapping[str, Any]], files: Sequence[str] = (),
) -> list[str]:
    values: set[str] = {str(item["path"]) for item in inventory}
    commands = (
        ["git", "diff", "--name-only", "-z", "--cached", "HEAD", "--", *_literal_paths(files)],
        ["git", "diff", "--name-only", "-z", "--", *_literal_paths(files)],
    )
    for command in commands:
        for raw_name in _run_bytes(command, root).split(b"\0"):
            if not raw_name:
                continue
            try:
                name = raw_name.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise RemotePatchSnapshotError("changed path is not valid UTF-8") from exc
            values.add(_safe_relative_path(name, field="changed path"))
    return sorted(values)


def _git_remotes(root: Path) -> list[dict[str, Any]]:
    remotes: list[dict[str, Any]] = []
    for raw_name in _run_bytes(["git", "remote"], root).decode("utf-8").splitlines():
        name = raw_name.strip()
        if not _ID_RE.fullmatch(name):
            raise RemotePatchSnapshotError(f"git remote name is unsafe: {name}")
        fetch = _run_bytes(["git", "remote", "get-url", "--all", name], root).decode("utf-8")
        push = _run_bytes(
            ["git", "remote", "get-url", "--push", "--all", name],
            root,
        ).decode("utf-8")
        remotes.append(
            {
                "name": name,
                "fetch_urls": [_redact_remote_url(item) for item in fetch.splitlines() if item.strip()],
                "push_urls": [_redact_remote_url(item) for item in push.splitlines() if item.strip()],
            }
        )
    return remotes


def _capture_repository(
    remote_root: Path, repo_path: str, files: Sequence[str] = (),
) -> dict[str, Any]:
    repo_path = _canonical_repo_path(repo_path)
    candidate = remote_root if repo_path == "." else remote_root / repo_path
    try:
        root = candidate.resolve(strict=True)
    except OSError as exc:
        raise RemotePatchSnapshotError(f"snapshot repository does not exist: {repo_path}") from exc
    try:
        root.relative_to(remote_root)
    except ValueError as exc:
        raise RemotePatchSnapshotError(f"snapshot repository escapes remote root: {repo_path}") from exc
    git_root = Path(
        _run_bytes(["git", "rev-parse", "--show-toplevel"], root).decode("utf-8").strip()
    ).resolve()
    if git_root != root:
        raise RemotePatchSnapshotError(
            f"snapshot repository path is not its Git root: {repo_path} -> {git_root}"
        )

    _validate_selected_files(root, files)
    paths = _literal_paths(files)
    head = _run_bytes(["git", "rev-parse", "HEAD"], root).decode("ascii").strip()
    branch = _run_bytes(["git", "branch", "--show-current"], root).decode("utf-8").strip()
    status = _run_bytes(["git", "status", "--porcelain=v1", "-z", "--branch", "--", *paths], root)
    _validate_rename_scope(root, files, status)
    staged = _run_bytes(["git", "diff", "--cached", "--binary", "--full-index", "HEAD", "--", *paths], root)
    unstaged = _run_bytes(["git", "diff", "--binary", "--full-index", "--", *paths], root)
    head_diff = _run_bytes(["git", "diff", "--binary", "--full-index", "HEAD", "--", *paths], root)
    untracked = _remote_inventory(root, files)
    changed_files = _changed_paths(root, untracked, files)
    if files and not changed_files:
        raise RemotePatchSnapshotError(f"selected files contain no changes: {repo_path}")
    if files and not set(changed_files).issubset(files):
        raise RemotePatchSnapshotError(f"snapshot includes files outside the requested selection: {repo_path}")
    final_head = _run_bytes(["git", "rev-parse", "HEAD"], root).decode("ascii").strip()
    final_status = _run_bytes(["git", "status", "--porcelain=v1", "-z", "--branch", "--", *paths], root)
    _validate_rename_scope(root, files, final_status)
    final_staged = _run_bytes(
        ["git", "diff", "--cached", "--binary", "--full-index", "HEAD", "--", *paths],
        root,
    )
    final_unstaged = _run_bytes(["git", "diff", "--binary", "--full-index", "--", *paths], root)
    final_head_diff = _run_bytes(
        ["git", "diff", "--binary", "--full-index", "HEAD", "--", *paths],
        root,
    )
    final_untracked = _remote_inventory(root, files)
    if (
        final_head != head
        or final_status != status
        or final_staged != staged
        or final_unstaged != unstaged
        or final_head_diff != head_diff
        or final_untracked != untracked
    ):
        raise RemotePatchSnapshotError(
            f"repository changed while remote snapshot was being generated: {repo_path}"
        )
    return {
        "repo_path": repo_path,
        "root": root.as_posix(),
        "head": head,
        "branch": branch,
        "remotes": _git_remotes(root),
        "status": _blob(status),
        "staged_diff": _blob(staged),
        "unstaged_diff": _blob(unstaged),
        "head_diff": _blob(head_diff),
        "untracked_diff": _blob(_untracked_diff(root, untracked)),
        "untracked": untracked,
        "changed_files": changed_files,
    }


def _repo_status(remote_root: Path) -> dict[str, Any]:
    if not (remote_root / ".repo").is_dir():
        return {"available": False, "reason": "not-repo-workspace", "output": _blob(b"")}
    candidates = [["repo", "status"]]
    private_repo = remote_root / ".repo" / "repo" / "repo"
    if private_repo.is_file():
        candidates.append([str(private_repo), "status"])
    last_error: RemotePatchSnapshotError | None = None
    for command in candidates:
        try:
            return {
                "available": True,
                "reason": "",
                "output": _blob(_run_bytes(command, remote_root)),
            }
        except (OSError, RemotePatchSnapshotError) as exc:
            last_error = RemotePatchSnapshotError(str(exc))
    raise last_error or RemotePatchSnapshotError("repo status is unavailable")


def _snapshot_digest(payload: Mapping[str, Any]) -> str:
    unsigned = {key: value for key, value in payload.items() if key != "snapshot_sha256"}
    return _sha256(_canonical_json_bytes(unsigned))


def create_remote_patch_snapshot(
    *,
    remote_root: str | Path,
    workspace_id: str,
    command_id: str,
    repository_paths: Sequence[str],
    repository_files: Sequence[str] = (),
    generated_at_ns: int | None = None,
) -> dict[str, Any]:
    if not _WORKSPACE_RE.fullmatch(workspace_id):
        raise RemotePatchSnapshotError("workspace id must be the channel v2 16-character hash")
    if not _ID_RE.fullmatch(command_id):
        raise RemotePatchSnapshotError("command id does not match the channel v2 contract")
    if not repository_paths:
        raise RemotePatchSnapshotError("at least one repository path is required")
    try:
        root = Path(remote_root).expanduser().resolve(strict=True)
    except OSError as exc:
        raise RemotePatchSnapshotError(f"remote root does not exist: {remote_root}") from exc
    canonical_root = _canonical_remote_root(root.as_posix())
    selected = parse_repository_files(repository_paths, repository_files)
    repositories = [
        _capture_repository(root, item, selected.get(item, ()))
        for item in dict.fromkeys(repository_paths)
    ]
    timestamp = time.time_ns() if generated_at_ns is None else generated_at_ns
    if type(timestamp) is not int or timestamp <= 0:
        raise RemotePatchSnapshotError("generated_at_ns must be a positive integer")
    payload: dict[str, Any] = {
        "schema": REMOTE_PATCH_SNAPSHOT_SCHEMA,
        "version": REMOTE_PATCH_SNAPSHOT_VERSION,
        "workspace_id": workspace_id,
        "command_id": command_id,
        "remote_root": canonical_root,
        "generated_at_ns": timestamp,
        "repo_status": (
            {"available": False, "reason": "file-scoped-capture", "output": _blob(b"")}
            if selected else _repo_status(root)
        ),
        "repositories": repositories,
    }
    payload["snapshot_sha256"] = _snapshot_digest(payload)
    return payload


def _require_exact_fields(value: Mapping[str, Any], expected: frozenset[str], field: str) -> None:
    if not isinstance(value, Mapping) or frozenset(value) != expected:
        raise RemotePatchSnapshotError(f"snapshot field {field} does not match its closed schema")


def _validate_string_list(value: Any, *, field: str, path_values: bool = False) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise RemotePatchSnapshotError(f"snapshot field {field} must be a string list")
    if len(value) != len(set(value)):
        raise RemotePatchSnapshotError(f"snapshot field {field} contains duplicates")
    if path_values:
        for item in value:
            _safe_relative_path(item, field=field)
    return value


def validate_remote_patch_snapshot(
    payload: Mapping[str, Any],
    *,
    expected_workspace_id: str,
    expected_command_id: str,
    expected_remote_root: str,
    expected_sha256: str,
    now_ns: int | None = None,
    max_age_ns: int | None = None,
) -> dict[str, Any]:
    _require_exact_fields(payload, _TOP_FIELDS, "root")
    if payload.get("schema") != REMOTE_PATCH_SNAPSHOT_SCHEMA:
        raise RemotePatchSnapshotError("snapshot schema is not supported")
    if type(payload.get("version")) is not int or payload.get("version") != REMOTE_PATCH_SNAPSHOT_VERSION:
        raise RemotePatchSnapshotError("snapshot version is not supported")
    if payload.get("workspace_id") != expected_workspace_id or not _WORKSPACE_RE.fullmatch(expected_workspace_id):
        raise RemotePatchSnapshotError("snapshot workspace identity does not match the channel")
    if payload.get("command_id") != expected_command_id or not _ID_RE.fullmatch(expected_command_id):
        raise RemotePatchSnapshotError("snapshot command identity does not match the channel")
    remote_root = _canonical_remote_root(str(payload.get("remote_root") or ""))
    if remote_root != _canonical_remote_root(expected_remote_root):
        raise RemotePatchSnapshotError("snapshot remote root does not match the channel workspace")
    digest = payload.get("snapshot_sha256")
    if not isinstance(digest, str) or digest != expected_sha256 or digest != _snapshot_digest(payload):
        raise RemotePatchSnapshotError("snapshot sha256 does not match the channel handoff")
    generated = payload.get("generated_at_ns")
    if type(generated) is not int or generated <= 0:
        raise RemotePatchSnapshotError("snapshot generated_at_ns is invalid")
    if max_age_ns is not None and now_ns is None:
        raise RemotePatchSnapshotError("snapshot max age requires a trusted current time")
    if now_ns is not None:
        if type(now_ns) is not int or now_ns <= 0 or generated > now_ns:
            raise RemotePatchSnapshotError("snapshot generation time is in the future")
        if max_age_ns is not None:
            if type(max_age_ns) is not int or max_age_ns < 0:
                raise RemotePatchSnapshotError("snapshot max age is invalid")
            if now_ns - generated > max_age_ns:
                raise RemotePatchSnapshotError("snapshot is stale")

    repo_status = payload.get("repo_status")
    _require_exact_fields(repo_status, _REPO_STATUS_FIELDS, "repo_status")
    if type(repo_status.get("available")) is not bool or not isinstance(repo_status.get("reason"), str):
        raise RemotePatchSnapshotError("snapshot repo_status metadata is invalid")
    decode_snapshot_blob(repo_status["output"], field="repo_status.output")

    repositories = payload.get("repositories")
    if not isinstance(repositories, list) or not repositories:
        raise RemotePatchSnapshotError("snapshot repositories must be a non-empty list")
    seen: set[str] = set()
    root_path = PurePosixPath(remote_root)
    for index, repository in enumerate(repositories):
        _require_exact_fields(repository, _REPOSITORY_FIELDS, f"repositories[{index}]")
        repo_path = _canonical_repo_path(repository.get("repo_path"))
        if repo_path in seen:
            raise RemotePatchSnapshotError("snapshot contains duplicate repository paths")
        seen.add(repo_path)
        expected_root = root_path if repo_path == "." else root_path / repo_path
        if repository.get("root") != expected_root.as_posix():
            raise RemotePatchSnapshotError("snapshot repository root does not match its repo_path")
        head = repository.get("head")
        if not isinstance(head, str) or not _GIT_HEAD_RE.fullmatch(head):
            raise RemotePatchSnapshotError("snapshot Git HEAD is invalid")
        if not isinstance(repository.get("branch"), str):
            raise RemotePatchSnapshotError("snapshot Git branch is invalid")
        remotes = repository.get("remotes")
        if not isinstance(remotes, list):
            raise RemotePatchSnapshotError("snapshot Git remotes are invalid")
        for remote in remotes:
            _require_exact_fields(remote, _REMOTE_FIELDS, "remote")
            if not isinstance(remote.get("name"), str) or not _ID_RE.fullmatch(remote["name"]):
                raise RemotePatchSnapshotError("snapshot Git remote name is invalid")
            _validate_string_list(remote.get("fetch_urls"), field="remote.fetch_urls")
            _validate_string_list(remote.get("push_urls"), field="remote.push_urls")
        for blob_name in ("status", "staged_diff", "unstaged_diff", "head_diff", "untracked_diff"):
            decode_snapshot_blob(repository[blob_name], field=f"repositories[{index}].{blob_name}")
        changed_files = _validate_string_list(
            repository.get("changed_files"),
            field="changed_files",
            path_values=True,
        )
        untracked = repository.get("untracked")
        if not isinstance(untracked, list):
            raise RemotePatchSnapshotError("snapshot untracked inventory is invalid")
        untracked_paths: list[str] = []
        for item in untracked:
            _require_exact_fields(item, _UNTRACKED_FIELDS, "untracked item")
            untracked_paths.append(_safe_relative_path(item.get("path"), field="untracked path"))
            if item.get("kind") not in {"file", "symlink"}:
                raise RemotePatchSnapshotError("snapshot untracked kind is invalid")
            if type(item.get("size")) is not int or item["size"] < 0:
                raise RemotePatchSnapshotError("snapshot untracked size is invalid")
            if type(item.get("mtime_ns")) is not int or item["mtime_ns"] < 0:
                raise RemotePatchSnapshotError("snapshot untracked mtime is invalid")
            if not isinstance(item.get("sha256"), str) or not _SHA256_RE.fullmatch(item["sha256"]):
                raise RemotePatchSnapshotError("snapshot untracked sha256 is invalid")
        if len(untracked_paths) != len(set(untracked_paths)):
            raise RemotePatchSnapshotError("snapshot untracked inventory contains duplicates")
        if not set(untracked_paths).issubset(changed_files):
            raise RemotePatchSnapshotError("snapshot changed_files omits untracked paths")
    return dict(payload)


def write_immutable_remote_snapshot(payload: Mapping[str, Any]) -> Path:
    workspace_id = str(payload.get("workspace_id") or "")
    command_id = str(payload.get("command_id") or "")
    if not _WORKSPACE_RE.fullmatch(workspace_id) or not _ID_RE.fullmatch(command_id):
        raise RemotePatchSnapshotError("snapshot identity is invalid")
    directory = (
        Path.home()
        / ".codex"
        / "android-remote-sessions"
        / workspace_id
        / "snapshots"
        / command_id
    )
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    os.chmod(directory, 0o700)
    target = directory / "snapshot.json"
    if target.exists():
        raise RemotePatchSnapshotError(f"immutable snapshot already exists: {target}")
    data = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    descriptor, temporary_name = tempfile.mkstemp(prefix=".snapshot.", dir=directory)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o400)
        try:
            os.link(temporary, target)
        except FileExistsError as exc:
            raise RemotePatchSnapshotError(f"immutable snapshot already exists: {target}") from exc
    finally:
        temporary.unlink(missing_ok=True)
    return target


def load_remote_patch_snapshot(
    path: str | Path,
    **validation: Any,
) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RemotePatchSnapshotError(f"cannot read remote patch snapshot: {path}") from exc
    if not isinstance(payload, dict):
        raise RemotePatchSnapshotError("remote patch snapshot must be a JSON object")
    return validate_remote_patch_snapshot(payload, **validation)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate an immutable remote patch snapshot.")
    parser.add_argument("generate", nargs="?")
    parser.add_argument("--remote-root", required=True)
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--command-id", required=True)
    parser.add_argument("--repo-path", action="append", required=True)
    parser.add_argument("--repo-file", action="append", default=[], help="REPO_PATH=EXACT_FILE; repeat for this feature's files.")
    return parser.parse_args()


def _main() -> int:
    args = _parse_args()
    payload = create_remote_patch_snapshot(
        remote_root=args.remote_root,
        workspace_id=args.workspace_id,
        command_id=args.command_id,
        repository_paths=args.repo_path,
        repository_files=args.repo_file,
    )
    path = write_immutable_remote_snapshot(payload)
    print(f"SNAPSHOT_REMOTE_PATH={path}")
    print(f"SNAPSHOT_SHA256={payload['snapshot_sha256']}")
    print(f"SNAPSHOT_WORKSPACE_ID={payload['workspace_id']}")
    print(f"SNAPSHOT_COMMAND_ID={payload['command_id']}")
    print(f"SNAPSHOT_REMOTE_ROOT={payload['remote_root']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
