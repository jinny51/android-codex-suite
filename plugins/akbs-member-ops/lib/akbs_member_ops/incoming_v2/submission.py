"""Profile-bound Android-change-v2 HTTP submission, independent of v1 contracts."""

from __future__ import annotations

import gzip
import hashlib
import io
import re
import tarfile
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from akbs_member_ops.http_client import (
    invalid_success_response,
    request_json_with_metadata,
    sanitize_public_text,
)

from .validation import (
    AndroidChangeV2Error,
    _archive_inventory,
    _load_manifest,
    _stream_regular_file,
    canonical_json_sha256,
    check_package,
)


RECEIPT_SCHEMA = "akbs-android-change-v2-upload-receipt-v1"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
CONTRACT_ERROR_CODES = frozenset({
    "android_change_v2_writer_off",
    "android_change_v2_writer_not_activated",
    "android_change_v2_layer_not_enabled",
    "android_change_v2_runtime_generation_missing",
    "android_change_v2_idempotency_key_invalid",
    "android_change_v2_member_mismatch",
    "android_change_v2_replay_drift",
})


class SubmissionError(AndroidChangeV2Error):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


class _HashingReader:
    def __init__(self, source: Any) -> None:
        self.source = source
        self.digest = hashlib.sha256()
        self.size = 0

    def read(self, size: int) -> bytes:
        chunk = self.source.read(size)
        self.digest.update(chunk)
        self.size += len(chunk)
        return chunk


def _submission_identity(profile: str | None) -> tuple[str, str]:
    # Reuse the existing profile/endpoint readers, not any v1 upload, tar, or
    # contract gate. The public entry point already loads this internal path.
    from akbs_intake.config import enforce_mode_allowed, load_config, submission_api_base_url

    try:
        config, _paths = load_config(profile)
        enforce_mode_allowed(config, "patch")
        endpoint = submission_api_base_url(config).rstrip("/")
        parsed = urllib.parse.urlsplit(endpoint)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or any(ord(character) < 33 for character in endpoint)
        ):
            raise ValueError("submission endpoint must be an HTTP(S) API base URL without credentials")
    except (SystemExit, ValueError) as exc:
        raise SubmissionError(
            "android_change_v2_profile_invalid",
            sanitize_public_text(str(exc), "member profile or submission endpoint is invalid"),
        ) from exc
    return config["member_alias"], endpoint + "/member/me/uploads/patch"


def _archive_bytes(package: Path, checked: dict[str, Any]) -> bytes:
    """Freeze checked bytes, then encode without host names, modes, or timestamps."""
    manifest_path, manifest_raw, manifest = _load_manifest(package)
    if hashlib.sha256(manifest_raw).hexdigest() != checked["manifest_sha256"]:
        raise SubmissionError("android_change_v2_payload_changed", "manifest changed after validation")
    inventory = {
        row["path"]: (row["sha256"], row["size_bytes"])
        for row in manifest["files"]
    }
    inventory["manifest.json"] = (checked["manifest_sha256"], len(manifest_raw))
    if canonical_json_sha256([
        [name, sha256, size] for name, (sha256, size) in sorted(inventory.items())
    ]) != checked["archive_inventory_sha256"]:
        raise SubmissionError("android_change_v2_payload_changed", "archive inventory changed after validation")

    with tempfile.TemporaryDirectory(prefix="akbs-v2-submit-") as temporary:
        snapshot = Path(temporary)
        for name, facts in sorted(inventory.items()):
            copied = _stream_regular_file(manifest_path.parent / name, destination=snapshot / name)
            if copied != facts:
                raise SubmissionError("android_change_v2_payload_changed", "archive bytes changed after validation")
        if _archive_inventory(manifest_path.parent) != inventory:
            raise SubmissionError("android_change_v2_payload_changed", "source inventory changed while packaging")
        archive = io.BytesIO()
        with gzip.GzipFile(fileobj=archive, mode="wb", filename="", mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as tar:
                for name, (sha256, size) in sorted(inventory.items()):
                    entry = tarfile.TarInfo(name)
                    entry.size = size
                    entry.mode = 0o644
                    entry.mtime = entry.uid = entry.gid = 0
                    entry.uname = entry.gname = ""
                    with (snapshot / name).open("rb") as source:
                        reader = _HashingReader(source)
                        tar.addfile(entry, reader)
                        if reader.digest.hexdigest() != sha256 or reader.size != size or source.read(1):
                            raise SubmissionError(
                                "android_change_v2_payload_changed", "archive bytes changed while encoding"
                            )
        return archive.getvalue()


def _validate_receipt(
    receipt: dict[str, Any], checked: dict[str, Any], member_alias: str, idempotency_key: str
) -> None:
    package = receipt.get("package")
    operation = receipt.get("operation")
    qualification = receipt.get("qualification")
    if not all(isinstance(value, dict) for value in (package, operation, qualification)):
        raise invalid_success_response("v2 upload receipt is missing package, operation, or qualification")
    if (
        receipt.get("schema") != RECEIPT_SCHEMA
        or receipt.get("accepted") is not True
        or receipt.get("upload_type") != "patch"
        or receipt.get("server_qualified") is not True
        or package.get("package_key") != checked["source_package_key"]
        or not isinstance(package.get("patch_package_id"), str)
        or not package["patch_package_id"].strip()
        or type(package.get("revision")) is not int
        or package["revision"] < 1
        or package.get("status") != "received"
        or operation.get("manifest_sha256") != checked["manifest_sha256"]
        or operation.get("directory_payload_sha256") != checked["archive_inventory_sha256"]
        or operation.get("idempotency_key_sha256") != hashlib.sha256(idempotency_key.encode("ascii")).hexdigest()
    ):
        raise invalid_success_response("v2 upload receipt does not bind the submitted package and retry key")
    expected_qualification = {
        "schema": "akbs-server-qualification-decision-v1",
        "authority": "server_authoritative",
        "authority_scope": "incoming_contract_qualification",
        "decision": "accept",
        "authenticated_actor": member_alias,
        "source_package_key": checked["source_package_key"],
        "patch_package_id": package["patch_package_id"],
        "revision": package["revision"],
        "manifest_sha256": checked["manifest_sha256"],
        "directory_payload_sha256": checked["archive_inventory_sha256"],
        "qualification_input_sha256": checked["coherence"]["qualification_input_sha256"],
    }
    if any(qualification.get(key) != value for key, value in expected_qualification.items()):
        raise invalid_success_response("v2 qualification does not bind the submitted package and member")
    supplied_hash = receipt.get("receipt_sha256")
    try:
        digest = canonical_json_sha256({key: value for key, value in receipt.items() if key != "receipt_sha256"})
    except AndroidChangeV2Error:
        raise invalid_success_response("v2 receipt is not canonical JSON") from None
    if not isinstance(supplied_hash, str) or SHA256_RE.fullmatch(supplied_hash) is None or digest != supplied_hash:
        raise invalid_success_response("v2 upload receipt hash is invalid")


def submit_package(package: Path, *, profile: str | None = None) -> dict[str, Any]:
    member_alias, endpoint = _submission_identity(profile)
    checked = check_package(package)
    _manifest_path, manifest_raw, manifest = _load_manifest(package)
    if hashlib.sha256(manifest_raw).hexdigest() != checked["manifest_sha256"]:
        raise SubmissionError("android_change_v2_payload_changed", "manifest changed after validation")
    if manifest["identity"]["member_alias"] != member_alias:
        raise SubmissionError(
            "android_change_v2_member_mismatch",
            "package member_alias differs from the selected member profile; the package was not rewritten",
        )
    try:
        archive = _archive_bytes(package, checked)
    except (OSError, tarfile.TarError) as exc:
        raise SubmissionError(
            "android_change_v2_archive_failed",
            sanitize_public_text(str(exc), "cannot create the v2 upload archive"),
        ) from exc
    # The server binds member + key + exact directory inventory. Never silently
    # rotate the key or fall back to v1 after a refusal or ambiguous transport.
    idempotency_key = "android-change-v2:" + checked["archive_inventory_sha256"]
    request = urllib.request.Request(
        endpoint,
        data=archive,
        headers={
            "Content-Type": "application/gzip",
            "X-AKBS-User": member_alias,
            "Idempotency-Key": idempotency_key,
        },
        method="POST",
    )
    receipt, metadata = request_json_with_metadata(request, timeout=30, contract_codes=CONTRACT_ERROR_CODES)
    _validate_receipt(receipt, checked, member_alias, idempotency_key)
    return {
        "status": "PASS",
        "operation": "submit",
        "contract": checked["contract"],
        "source_package_key": checked["source_package_key"],
        "manifest_sha256": checked["manifest_sha256"],
        "archive_inventory_sha256": checked["archive_inventory_sha256"],
        "platform_compatibility": checked["platform_compatibility"],
        "archive_sha256": hashlib.sha256(archive).hexdigest(),
        "idempotency_key": idempotency_key,
        "patch_package_id": receipt["package"]["patch_package_id"],
        "revision": receipt["package"]["revision"],
        "server_qualified": True,
        "v1_fallback": False,
        "network_requests": 1,
        "request_id": metadata["request_id"],
        "receipt": receipt,
    }
