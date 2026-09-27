"""Read case-bound patch originals from AKBS; no local fallback or reuse claim."""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

from akbs_member_ops.artifact_paths import require_safe_artifact_path
from akbs_member_ops.http_client import (
    HttpClientFailure, failure_result, invalid_success_response, parse_http_error, request_json,
)
from akbs_member_ops.knowledge.merge_confirmation.client import member_request_headers
from akbs_member_ops.knowledge_search.config import member_api_base_url


SCHEMA = "akbs-knowledge-case-patch-originals/v1"
SCOPE = "active_v2_case_source_evidence"
MAX_PATCH_BYTES = 64 * 1024 * 1024
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,255}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def _endpoint(case_id: str, asset_id: str = "") -> str:
    if not IDENTIFIER.fullmatch(case_id) or (asset_id and not IDENTIFIER.fullmatch(asset_id)):
        raise ValueError("case_id and asset_id must be server-returned identifiers")
    base, _source = member_api_base_url()
    path = "/akbs/api/member/me/knowledge/" + urllib.parse.quote(case_id, safe="") + "/patches"
    if asset_id:
        path += "/" + urllib.parse.quote(asset_id, safe="")
    return base + path


def fetch_case_patches(case_id: str, *, timeout: float = 3.0) -> dict[str, Any]:
    request = urllib.request.Request(_endpoint(case_id), headers=member_request_headers(), method="GET")
    payload = request_json(request, timeout=timeout)
    if (payload.get("schema") != SCHEMA or payload.get("scope") != SCOPE
            or payload.get("case_id") != case_id
            or payload.get("availability") not in {"available", "unavailable"}
            or not isinstance(payload.get("patches"), list)):
        raise invalid_success_response("case patch listing contract mismatch")
    seen: set[str] = set()
    for patch in payload["patches"]:
        if not isinstance(patch, dict):
            raise invalid_success_response("case patch metadata invalid")
        asset_id = patch.get("asset_id")
        size = patch.get("size_bytes")
        if (not isinstance(asset_id, str) or not IDENTIFIER.fullmatch(asset_id) or asset_id in seen
                or not isinstance(patch.get("sha256"), str) or not SHA256.fullmatch(patch["sha256"])
                or type(size) is not int or size < 0 or size > MAX_PATCH_BYTES
                or not isinstance(patch.get("display_name"), str)):
            raise invalid_success_response("case patch metadata invalid")
        seen.add(asset_id)
    if bool(payload["patches"]) != (payload["availability"] == "available"):
        raise invalid_success_response("case patch availability mismatch")
    return {**payload, "source": "server_api"}


def download_case_patch(
    case_id: str, asset_id: str, output: Path, *, timeout: float = 3.0,
) -> dict[str, Any]:
    raw_output = Path(os.path.abspath(output.expanduser()))
    if raw_output.exists() or raw_output.is_symlink():
        raise ValueError("download output already exists; choose a new output file")
    target = require_safe_artifact_path(output, purpose="knowledge patch download")
    if target.exists() or target.is_symlink():
        raise ValueError("download output already exists; choose a new output file")
    listing = fetch_case_patches(case_id, timeout=timeout)
    patch = next((item for item in listing["patches"] if item["asset_id"] == asset_id), None)
    if patch is None:
        raise ValueError("patch original is not available for this case")
    # Build the case-bound URL ourselves, never follow a URL supplied in metadata.
    headers = member_request_headers()
    headers["Accept"] = "application/octet-stream"
    request = urllib.request.Request(_endpoint(case_id, asset_id), headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(patch["size_bytes"] + 1)
    except urllib.error.HTTPError as error:
        raise HttpClientFailure(parse_http_error(error)) from None
    except Exception as error:
        raise HttpClientFailure(failure_result(error)) from None
    if (not isinstance(raw, bytes) or len(raw) != patch["size_bytes"]
            or hashlib.sha256(raw).hexdigest() != patch["sha256"]):
        raise invalid_success_response("patch original bytes do not match the case metadata")
    target.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation preserves existing work, including on a concurrent call.
    with target.open("xb") as stream:
        stream.write(raw)
    return {
        "schema": SCHEMA, "source": "server_api", "case_id": case_id, "asset_id": asset_id,
        "output": str(target), "sha256": patch["sha256"], "size_bytes": len(raw),
        "verified_original": True,
        "reuse_outcome": "not_started",
    }
