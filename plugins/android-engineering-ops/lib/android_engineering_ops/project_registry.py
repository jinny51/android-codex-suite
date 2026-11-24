from __future__ import annotations

import json
import os
import posixpath
import re
import shlex
from pathlib import Path
from typing import Iterable


REGISTRY_FIELDS = (
    ("project_id", "source-access registry project_id"),
    ("sdk_name", "source-access registry sdk_name"),
    ("remote_root", "source-access registry remote_root"),
    ("share", "source-access registry share"),
    ("platform", "source-access registry platform"),
    ("ssh_host", "source-access registry ssh_host"),
)


def project_id_component(value: str) -> str:
    component = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip())
    return re.sub(r"-+", "-", component).strip("-._")


def derive_project_id(entry: dict[str, str]) -> str:
    explicit = project_id_component(entry.get("project_id", ""))
    if explicit:
        return explicit
    platform = project_id_component(entry.get("platform", "").lower()) or "android"
    project = project_id_component(entry.get("sdk_name", ""))
    if not project:
        project = project_id_component(Path(entry.get("local_path", "")).name) or "unknown"
    return f"{platform}-{project}"


def expand_home_path(value: str) -> str:
    home = os.path.expanduser("~")
    for marker in ("$HOME", "${HOME}", "~"):
        if value == marker:
            return home
        prefix = marker + "/"
        if value.startswith(prefix):
            return str(Path(home) / value[len(prefix) :])
    return value


def path_strings_overlap(left: str, right: str) -> bool:
    left = left.replace("\\", "/").rstrip("/")
    right = right.replace("\\", "/").rstrip("/")
    if os.name == "nt":
        left, right = left.casefold(), right.casefold()
    if not left or not right:
        return False
    return left == right or left.startswith(right + "/") or right.startswith(left + "/")


def parse_shell_array(text: str, name: str) -> list[str]:
    match = re.search(rf"^{re.escape(name)}=\((.*)\)$", text, re.M)
    if not match:
        return []
    try:
        return [item for item in shlex.split(match.group(1)) if item]
    except ValueError:
        return []


def env_registry_entries(path: Path) -> list[dict[str, str]]:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    arrays = {
        "local_path": parse_shell_array(text, "PROJECT_PATHS"),
        "share": parse_shell_array(text, "SAMBA_PROJECT_SHARES"),
        "ssh_host": parse_shell_array(text, "REMOTE_SSH_HOSTS"),
        "remote_root": parse_shell_array(text, "REMOTE_ROOTS"),
        "platform": parse_shell_array(text, "PLATFORMS"),
        "sdk_name": parse_shell_array(text, "SDK_NAMES"),
        "project_id": parse_shell_array(text, "PROJECT_IDS"),
    }
    entries: list[dict[str, str]] = []
    for index, local_path in enumerate(arrays["local_path"]):
        entry = {"local_path": local_path}
        for key, values in arrays.items():
            if key != "local_path" and index < len(values):
                entry[key] = values[index]
        entry["mount_transport"] = "cifs"
        entry["project_id"] = derive_project_id(entry)
        entries.append(entry)
    return entries


def json_registry_entries(path: Path) -> list[dict[str, str]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return []
    if not isinstance(payload, dict):
        return []
    ssh_host = str(payload.get("server") or path.stem).strip()
    shares = payload.get("shares")
    if not isinstance(shares, dict):
        return []
    entries: list[dict[str, str]] = []
    for share_name, raw_share in shares.items():
        if not isinstance(raw_share, dict):
            continue
        mount_point = expand_home_path(str(raw_share.get("mount_point") or "").strip())
        share_remote = str(raw_share.get("remote_path") or "").strip()
        projects = raw_share.get("projects")
        if not isinstance(projects, dict):
            continue
        for project_name, raw_project in projects.items():
            if not isinstance(raw_project, dict):
                continue
            entry = {
                "local_path": expand_home_path(
                    str(raw_project.get("local_path") or mount_point).strip()
                ),
                "remote_root": str(raw_project.get("remote_path") or share_remote).strip(),
                "share": str(share_name).strip(),
                "platform": str(raw_project.get("platform") or "").strip(),
                "sdk_name": str(project_name).strip(),
                "ssh_host": ssh_host,
                "project_id": str(raw_project.get("project_id") or "").strip(),
                "mount_transport": str(raw_project.get("mount_transport") or raw_share.get("mount_transport") or "smbfs").strip(),
            }
            entry["project_id"] = derive_project_id(entry)
            entries.append({key: value for key, value in entry.items() if value})
    return entries


def registry_entries(registry_dir: Path) -> list[dict[str, str]]:
    if not registry_dir.is_dir():
        return []
    entries: list[dict[str, str]] = []
    for path in sorted(registry_dir.glob("*.env")):
        for entry in env_registry_entries(path):
            entry["registry_path"] = str(path)
            entries.append(entry)
    for path in sorted(registry_dir.glob("*.json")):
        for entry in json_registry_entries(path):
            entry["registry_path"] = str(path)
            entries.append(entry)
    return entries


def resolve_project_mapping(
    source_path: str | Path,
    registry_dir: Path | None = None,
) -> dict[str, str]:
    target = Path(source_path).expanduser().resolve()
    root = registry_dir or (Path.home() / ".servers" / "projects")
    matches: list[tuple[int, dict[str, str], Path]] = []
    for entry in registry_entries(root):
        local_path = entry.get("local_path", "")
        if not local_path:
            continue
        local_root = Path(expand_home_path(local_path)).expanduser().resolve()
        try:
            target.relative_to(local_root)
        except ValueError:
            continue
        matches.append((len(local_root.parts), entry, local_root))
    if not matches:
        return {}

    _, selected, local_root = max(matches, key=lambda item: item[0])
    result = dict(selected)
    registered_remote_root = result.get("remote_root", "")
    relative = target.relative_to(local_root)
    working_subpath = relative.as_posix() if relative.parts else "."
    result["project_root"] = registered_remote_root
    result["artifact_bridge_path"] = str(local_root)
    result["working_subpath"] = working_subpath
    result["remote_working_path"] = (
        posixpath.join(registered_remote_root, *relative.parts)
        if registered_remote_root and relative.parts
        else registered_remote_root
    )
    result["project_id"] = derive_project_id(result)
    result["local_path"] = str(target)
    return result


def source_access_registry_clues(
    source_paths: Iterable[str | Path],
    registry_dir: Path | None = None,
) -> list[tuple[str, str]]:
    candidates = [str(path) for path in source_paths if str(path).strip()]
    if not candidates:
        return []
    root = registry_dir or (Path.home() / ".servers" / "projects")
    clues: list[tuple[str, str]] = []
    for entry in registry_entries(root):
        registry_paths = [entry.get("local_path", ""), entry.get("remote_root", "")]
        if not any(
            path_strings_overlap(candidate, registry_path)
            for candidate in candidates
            for registry_path in registry_paths
            if registry_path
        ):
            continue
        for key, label in REGISTRY_FIELDS:
            value = entry.get(key, "")
            if value:
                clues.append((label, value))
    return list(dict.fromkeys(clues))
