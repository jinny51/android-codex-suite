"""Submit final Android change v2 bytes through the common patch upload lifecycle."""

from __future__ import annotations

import gzip
import hashlib
import io
import tarfile
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from akbs_member_ops.http_client import request_json_with_metadata, sanitize_public_text
from akbs_member_ops.incoming_v1.contract import (
    error_reason_codes,
    patch_package_id_from_upload_response,
    validate_success_response,
)

from .validation import (
    AndroidChangeV2Error,
    _archive_inventory,
    _load_manifest,
    _stream_regular_file,
    canonical_json_sha256,
    check_package,
)


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
            raise ValueError(
                "submission endpoint must be an HTTP(S) API base URL without credentials"
            )
    except (SystemExit, ValueError) as exc:
        raise SubmissionError(
            "android_change_v2_profile_invalid",
            sanitize_public_text(
                str(exc), "member profile or submission endpoint is invalid"
            ),
        ) from exc
    return config["member_alias"], endpoint + "/member/me/uploads/patch"


def _declared_inventory(
    manifest_raw: bytes, manifest: dict[str, Any]
) -> dict[str, tuple[str, int]]:
    rows = [manifest["readme"], *manifest["patches"], *manifest["evidence"]]
    return {
        "manifest.json": (hashlib.sha256(manifest_raw).hexdigest(), len(manifest_raw)),
        **{row["path"]: (row["sha256"], row["size_bytes"]) for row in rows},
    }


def _archive_bytes(package: Path, checked: dict[str, Any]) -> bytes:
    """Freeze checked bytes, then encode deterministically."""
    manifest_path, manifest_raw, manifest = _load_manifest(package)
    if hashlib.sha256(manifest_raw).hexdigest() != checked["manifest_sha256"]:
        raise SubmissionError(
            "android_change_v2_payload_changed", "manifest changed after validation"
        )
    inventory = _declared_inventory(manifest_raw, manifest)
    if canonical_json_sha256(
        [[name, sha256, size] for name, (sha256, size) in sorted(inventory.items())]
    ) != checked["archive_inventory_sha256"]:
        raise SubmissionError(
            "android_change_v2_payload_changed", "archive inventory changed after validation"
        )

    with tempfile.TemporaryDirectory(prefix="akbs-v2-submit-") as temporary:
        snapshot = Path(temporary)
        for name, facts in sorted(inventory.items()):
            copied = _stream_regular_file(
                manifest_path.parent / name, destination=snapshot / name
            )
            if copied != facts:
                raise SubmissionError(
                    "android_change_v2_payload_changed",
                    "archive bytes changed after validation",
                )
        if _archive_inventory(manifest_path.parent) != inventory:
            raise SubmissionError(
                "android_change_v2_payload_changed",
                "source inventory changed while packaging",
            )
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
                        if (
                            reader.digest.hexdigest() != sha256
                            or reader.size != size
                            or source.read(1)
                        ):
                            raise SubmissionError(
                                "android_change_v2_payload_changed",
                                "archive bytes changed while encoding",
                            )
        return archive.getvalue()


def _validate_response(receipt: dict[str, Any], checked: dict[str, Any]) -> str:
    try:
        validate_success_response(receipt)
        patch_package_id = patch_package_id_from_upload_response(receipt)
    except RuntimeError as exc:
        raise SubmissionError(
            "android_change_v2_response_invalid",
            sanitize_public_text(str(exc), "server upload response is invalid"),
        ) from exc
    package = receipt.get("package")
    if not isinstance(package, dict) or package.get("package_key") != checked["source_package_key"]:
        raise SubmissionError(
            "android_change_v2_response_invalid",
            "server upload response does not bind the submitted source package",
        )
    return patch_package_id


def submit_package(package: Path, *, profile: str | None = None) -> dict[str, Any]:
    member_alias, endpoint = _submission_identity(profile)
    checked = check_package(package)
    _manifest_path, manifest_raw, manifest = _load_manifest(package)
    if hashlib.sha256(manifest_raw).hexdigest() != checked["manifest_sha256"]:
        raise SubmissionError(
            "android_change_v2_payload_changed", "manifest changed after validation"
        )
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
    receipt, metadata = request_json_with_metadata(
        request, timeout=30, contract_codes=error_reason_codes()
    )
    patch_package_id = _validate_response(receipt, checked)
    return {
        "status": "PASS",
        "operation": "submit",
        "contract": checked["contract"],
        "source_package_key": checked["source_package_key"],
        "manifest_sha256": checked["manifest_sha256"],
        "archive_inventory_sha256": checked["archive_inventory_sha256"],
        "archive_sha256": hashlib.sha256(archive).hexdigest(),
        "idempotency_key": idempotency_key,
        "patch_package_id": patch_package_id,
        "network_requests": 1,
        "request_id": metadata["request_id"],
        "receipt": receipt,
    }
