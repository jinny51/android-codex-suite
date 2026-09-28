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
HISTORICAL_SCOPE = "active_historical_case_snapshot"
IMPLEMENTATION_SCOPE = "active_implementation_originals"
SCOPES = frozenset({SCOPE, HISTORICAL_SCOPE, IMPLEMENTATION_SCOPE})
MAX_PATCH_BYTES = 64 * 1024 * 1024
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,255}\Z")
IMPLEMENTATION_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def _endpoint(case_id: str, asset_id: str = "", *, implementation_id: str | None = None) -> str:
    if not isinstance(case_id, str) or not IDENTIFIER.fullmatch(case_id) or not isinstance(asset_id, str) or (asset_id and not IDENTIFIER.fullmatch(asset_id)):
        raise ValueError("case_id and asset_id must be server-returned identifiers")
    base, _source = member_api_base_url()
    path = "/akbs/api/member/me/knowledge/" + urllib.parse.quote(case_id, safe="") + "/patches"
    if asset_id:
        path += "/" + urllib.parse.quote(asset_id, safe="")
    if implementation_id is not None:
        if not isinstance(implementation_id, str) or not IMPLEMENTATION_IDENTIFIER.fullmatch(implementation_id):
            raise ValueError("implementation_id must be a server-returned identifier")
        path += "?" + urllib.parse.urlencode({"implementation_id": implementation_id})
    return base + path


def fetch_case_patches(
    case_id: str, *, implementation_id: str | None = None, timeout: float = 3.0,
) -> dict[str, Any]:
    request = urllib.request.Request(_endpoint(case_id, implementation_id=implementation_id), headers=member_request_headers(), method="GET")
    payload = request_json(request, timeout=timeout)
    scope = payload.get("scope")
    if (payload.get("schema") != SCHEMA or not isinstance(scope, str) or scope not in SCOPES
            or payload.get("case_id") != case_id
            or not isinstance(payload.get("availability"), str)
            or payload.get("availability") not in {"available", "unavailable"}
            or not isinstance(payload.get("patches"), list)):
        raise invalid_success_response("case patch listing contract mismatch")
    if (
        (scope == IMPLEMENTATION_SCOPE and implementation_id is None)
        or (implementation_id is not None and (
            payload.get("implementation_id") != implementation_id or scope == SCOPE
        ))
    ):
        raise invalid_success_response("patch originals implementation selector mismatch")
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
        if implementation_id is not None:
            _validate_implementation_original(patch, implementation_id, scope)
    if bool(payload["patches"]) != (payload["availability"] == "available"):
        raise invalid_success_response("case patch availability mismatch")
    return {**payload, "source": "server_api"}


def _validate_implementation_original(patch: dict[str, Any], implementation_id: str, scope: str) -> None:
    authority = patch.get("authority")
    if (
        patch.get("implementation_id") != implementation_id
        or not isinstance(patch.get("implementation_summary"), str)
        or not isinstance(patch.get("review_state"), str)
        or patch["review_state"] not in {"migration_review_required", "reviewed"}
        or not isinstance(authority, str)
        or authority not in {"accepted_evidence_binding", "historical_case_snapshot"}
        or (scope == HISTORICAL_SCOPE and authority != "historical_case_snapshot")
    ):
        raise invalid_success_response("patch originals implementation metadata invalid")
    if authority == "accepted_evidence_binding":
        role = patch.get("binding_role")
        if not isinstance(role, str) or role not in {
            "implementation", "verification_only", "counter_evidence", "rollback_evidence",
        }:
            raise invalid_success_response("patch originals binding role invalid")
    elif "binding_role" in patch:
        raise invalid_success_response("historical original must not claim an accepted binding role")
    source = patch.get("source")
    if not isinstance(source, dict) or set(source) != {
        "patch_package_id", "manifest_revision", "manifest_sha256", "package_content_hash", "layer",
    }:
        raise invalid_success_response("patch originals source metadata invalid")
    if (
        not isinstance(source["patch_package_id"], str)
        or not IDENTIFIER.fullmatch(source["patch_package_id"])
        or type(source["manifest_revision"]) is not int or source["manifest_revision"] < 1
        or any(not isinstance(source[field], str) or not SHA256.fullmatch(source[field])
               for field in ("manifest_sha256", "package_content_hash"))
        or not isinstance(source["layer"], str)
        or source["layer"] not in {"application", "platform", "native", "hal", "kernel", "device", "build", "unknown"}
        or (source["layer"] == "unknown" and authority != "historical_case_snapshot")
    ):
        raise invalid_success_response("patch originals source identity invalid")
    environments = patch.get("environments")
    if not isinstance(environments, list) or (authority == "accepted_evidence_binding" and not environments):
        raise invalid_success_response("patch originals environments invalid")
    seen: set[tuple[str, str, str]] = set()
    for environment in environments:
        if not isinstance(environment, dict) or set(environment) != {
            "project", "platform", "android_version", "validation_state",
        } or any(not isinstance(environment[key], str) for key in environment):
            raise invalid_success_response("patch originals exact environment invalid")
        identity = tuple(environment[key] for key in ("project", "platform", "android_version"))
        if identity in seen or environment["validation_state"] not in {"unresolved", "review_required", "validated"}:
            raise invalid_success_response("patch originals exact environment invalid")
        seen.add(identity)


def download_case_patch(
    case_id: str, asset_id: str, output: Path, *, implementation_id: str | None = None, timeout: float = 3.0,
) -> dict[str, Any]:
    raw_output = Path(os.path.abspath(output.expanduser()))
    if raw_output.exists() or raw_output.is_symlink():
        raise ValueError("download output already exists; choose a new output file")
    target = require_safe_artifact_path(output, purpose="knowledge patch download")
    if target.exists() or target.is_symlink():
        raise ValueError("download output already exists; choose a new output file")
    listing = fetch_case_patches(case_id, implementation_id=implementation_id, timeout=timeout)
    patch = next((item for item in listing["patches"] if item["asset_id"] == asset_id), None)
    if patch is None:
        raise ValueError("patch original is not available for this case")
    # Build the case-bound URL ourselves, never follow a URL supplied in metadata.
    headers = member_request_headers()
    headers["Accept"] = "application/octet-stream"
    request = urllib.request.Request(_endpoint(case_id, asset_id, implementation_id=implementation_id), headers=headers, method="GET")
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
    receipt = {
        "schema": SCHEMA, "source": "server_api", "case_id": case_id, "asset_id": asset_id,
        "output": str(target), "sha256": patch["sha256"], "size_bytes": len(raw),
        "verified_original": True,
        "reuse_outcome": "not_started",
    }
    if implementation_id is not None:
        receipt.update({key: patch[key] for key in (
            "implementation_id", "authority", "review_state", "environments",
        )})
        receipt["original_source"] = patch["source"]
        if "binding_role" in patch:
            receipt["binding_role"] = patch["binding_role"]
    return receipt
