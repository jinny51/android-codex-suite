from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .artifact_paths import require_safe_artifact_path


def write_json(path: Path, payload: Any, *, sort_keys: bool = True) -> None:
    path = require_safe_artifact_path(path, purpose="JSON output")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=sort_keys) + "\n",
        encoding="utf-8",
    )


def write_json_once(path: Path, payload: Any, *, sort_keys: bool = True) -> str:
    """Atomically create one immutable JSON artifact and return its SHA256."""

    import hashlib

    raw_output = Path(os.path.abspath(path.expanduser()))
    if raw_output.exists() or raw_output.is_symlink():
        raise FileExistsError("JSON output already exists")
    path = require_safe_artifact_path(path, purpose="JSON output")
    path.parent.mkdir(parents=True, exist_ok=True)
    content = (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=sort_keys) + "\n"
    ).encode("utf-8")
    digest = hashlib.sha256(content).hexdigest()
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
        try:
            directory_descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        except OSError:
            pass
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return digest
