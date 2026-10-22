"""Prepare and submit independent Doc originals through the shared incoming channel."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path, PurePosixPath
from typing import Any

from akbs_intake.config import expanded_path, load_config, parse_date_arg, require_config, submission_api_base_url
from akbs_intake.submit import package_tar_gz_bytes
from akbs_intake.version_gate import installed_plugin_family_status, plugin_version_gate_check
from akbs_member_ops.artifact_paths import OwnedArtifactDirectory, require_safe_artifact_path
from akbs_member_ops.http_client import HttpClientFailure, invalid_success_response, request_json_with_metadata
from akbs_member_ops.json_io import write_json, write_json_once
from akbs_member_ops.member_config import expand_codex_path


KIND = "technical_document"
RUN_ID = re.compile(r"[0-9]{8}-[0-9]{6}(?:-[A-Za-z0-9_.-]+)?\Z")
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,255}\Z")
LOCAL_CHECK = "local-check.json"
RECEIPT = ".akbs-upload-receipt.json"


def input_path(value: str | Path) -> Path:
    text = str(value)
    if text.lower().startswith("file:"):
        parsed = urllib.parse.urlsplit(text)
        text = urllib.parse.unquote(("//" + parsed.netloc if parsed.netloc else "") + parsed.path)
        if re.match(r"^/[A-Za-z]:[/\\]", text):
            text = text[1:]
    is_windows_path = bool(re.match(r"^[A-Za-z]:[/\\]", text) or text.startswith(("\\\\", "//wsl$", "//wsl.localhost")))
    if os.name != "nt" and "microsoft" in platform.release().lower() and is_windows_path:
        result = subprocess.run(["wslpath", "-u", text], check=True, text=True, capture_output=True)
        text = result.stdout.strip()
    return expand_codex_path(text, resolve=False)


def read_manifest(package: Path) -> dict[str, Any]:
    source = package / "manifest.json"
    if source.is_symlink() or not source.is_file():
        raise ValueError("manifest.json must be a regular file")
    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("manifest.json must contain an object")
    return payload


def file_digest(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(64 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def document_file(package: Path, manifest: dict[str, Any]) -> Path:
    files = manifest.get("files")
    relative = files.get("document") if isinstance(files, dict) else None
    if not isinstance(relative, str):
        raise ValueError("files.document must name the actual document original")
    parts = PurePosixPath(relative)
    if (parts.is_absolute() or ".." in parts.parts or len(parts.parts) != 3
            or parts.parts[:2] != ("materials", "document") or "\\" in relative):
        raise ValueError("files.document must be materials/document/<original filename>")
    current = package
    for part in parts.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("document original path must not contain symlinks")
    if not current.is_file():
        raise ValueError("files.document original is missing or is not a regular file")
    return current


def validate_package(package: Path) -> dict[str, Any]:
    errors: list[str] = []
    original: dict[str, Any] = {}
    manifest: dict[str, Any] = {}
    try:
        if package.is_symlink() or not package.is_dir():
            raise ValueError("prepared document must be a regular directory")
        manifest = read_manifest(package)
        for key, expected in (("schema", "knowledge-incoming-package"), ("schema_version", "2"),
                              ("package_kind", KIND), ("tool", "akbs-doc-submit")):
            if manifest.get(key) != expected:
                errors.append(f"{key} must be {expected}")
        for key in ("member_alias", "member_name", "title", "summary"):
            if not isinstance(manifest.get(key), str) or not manifest[key].strip():
                errors.append(f"{key} must not be empty")
        if not isinstance(manifest.get("member_alias"), str) or not IDENTIFIER.fullmatch(manifest["member_alias"]):
            errors.append("member_alias is invalid")
        try:
            date = manifest.get("date")
            if not isinstance(date, str) or dt.date.fromisoformat(date).isoformat() != date:
                raise ValueError
        except ValueError:
            errors.append("date must be the outcome date YYYY-MM-DD")
        run = manifest.get("run_id")
        if not isinstance(run, str) or not RUN_ID.fullmatch(run):
            errors.append("run_id must be YYYYMMDD-HHMMSS[-suffix]")
        identifier = manifest.get("document_id")
        revision = manifest.get("expected_revision")
        if identifier is not None or revision is not None:
            if not isinstance(identifier, str) or not IDENTIFIER.fullmatch(identifier):
                errors.append("document_id must be the existing server document identity")
            if type(revision) is not int or revision < 1:
                errors.append("expected_revision must be the positive current document revision")
        path = document_file(package, manifest)
        sha256, size = file_digest(path)
        if not size:
            errors.append("document original is empty")
        original = {"filename": path.name, "sha256": sha256, "size": size}
        check = package / LOCAL_CHECK
        if check.is_file() and not check.is_symlink():
            prior = json.loads(check.read_text(encoding="utf-8"))
            pinned = prior.get("original") if isinstance(prior, dict) else None
            if isinstance(pinned, dict) and pinned != original:
                errors.append("document original changed after preparation; prepare the intended revision explicitly")
    except (OSError, ValueError, TypeError) as exc:
        errors.append(str(exc))
    return {"status": "FAIL" if errors else "PASS", "errors": errors, "original": original,
            "package_kind": manifest.get("package_kind", ""), "package": str(package)}


def prepare_document(
    *, source: Path, title: str, summary: str, date: dt.date, config: dict[str, str],
    run_id: str | None = None, document_id: str | None = None, expected_revision: int | None = None,
) -> Path:
    require_config(config)
    if not title.strip() or not summary.strip():
        raise ValueError("title and summary must describe the independent document")
    if not source.is_file() or not source.name or source.name in {".", ".."}:
        raise ValueError("--file must point to the actual readable document original")
    run = run_id or (dt.datetime.now().strftime("%Y%m%d-%H%M%S") + "-doc")
    if not RUN_ID.fullmatch(run):
        raise ValueError("run_id must be YYYYMMDD-HHMMSS[-suffix]")
    root = require_safe_artifact_path(expanded_path(config["out_dir"]), purpose="Doc artifact root")
    package = root / "pending" / date.strftime("%Y%m%d") / config["member_alias"] / run
    owner = OwnedArtifactDirectory.create(package, purpose="independent Doc preparation")
    try:
        relative = "materials/document/" + source.name
        target = package / relative
        target.parent.mkdir(parents=True)
        before = source.stat()
        with source.open("rb") as stream, target.open("xb") as output:
            shutil.copyfileobj(stream, output, length=64 * 1024)
        after = source.stat()
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            raise ValueError("document source changed during preparation; retry the intended original")
        manifest: dict[str, Any] = {"schema": "knowledge-incoming-package", "schema_version": "2",
            "package_kind": KIND, "member_alias": config["member_alias"], "member_name": config["member_name"],
            "date": date.isoformat(), "run_id": run, "tool": "akbs-doc-submit",
            "title": title.strip(), "summary": summary.strip(), "files": {"document": relative}}
        if document_id is not None or expected_revision is not None:
            manifest.update(document_id=document_id, expected_revision=expected_revision)
        write_json(package / "manifest.json", manifest)
        result = validate_package(package)
        if result["status"] != "PASS":
            raise ValueError("; ".join(result["errors"]))
        write_json(package / LOCAL_CHECK, result)
        owner.commit()
        return package
    except BaseException:
        owner.cleanup()
        raise


def latest_prepared(config: dict[str, str], run_id: str | None = None) -> Path:
    root = expanded_path(config["out_dir"]) / "pending"
    found: list[Path] = []
    for path in root.glob("*/" + config["member_alias"] + "/*/manifest.json"):
        try:
            manifest = read_manifest(path.parent)
        except (OSError, ValueError):
            continue
        if (manifest.get("package_kind") == KIND and manifest.get("member_alias") == config["member_alias"]
                and (run_id is None or manifest.get("run_id") == run_id)):
            found.append(path.parent)
    if run_id is not None and len(found) != 1:
        raise ValueError("requested Doc run_id is missing or ambiguous; no latest-package fallback")
    if not found:
        raise ValueError("no prepared independent document was found for the selected profile")
    return sorted(found, key=lambda item: (item.parent.parent.name, item.name))[-1]


def submit_document(package: Path, config: dict[str, str]) -> dict[str, Any]:
    require_config(config)
    if str(config.get("role") or "").strip().lower() == "admin":
        raise ValueError("administrator Doc submission must continue through the management Skill, not the member upload route")
    result = validate_package(package)
    if result["status"] != "PASS":
        raise ValueError("; ".join(result["errors"]))
    manifest = read_manifest(package)
    if (manifest["member_alias"], manifest["member_name"]) != (config["member_alias"], config["member_name"]):
        raise ValueError("prepared document author conflicts with the selected existing profile")
    body = package_tar_gz_bytes(package, selected_paths=("manifest.json", manifest["files"]["document"]))
    # gzip timestamps are not business identity; the server binds the original manifest/files.
    url = submission_api_base_url(config).rstrip("/") + "/member/" + urllib.parse.quote(config["member_alias"], safe="") + "/uploads/doc"
    request = urllib.request.Request(url, data=body, method="POST",
        headers={"Content-Type": "application/gzip", "X-AKBS-User": config["member_alias"]})
    payload, metadata = request_json_with_metadata(request, timeout=30)
    identifier = payload.get("document_id")
    revision = payload.get("revision")
    if (not isinstance(identifier, str) or not IDENTIFIER.fullmatch(identifier)
            or type(revision) is not int or revision < 1 or not isinstance(payload.get("package_key"), str)
            or not payload["package_key"]):
        raise invalid_success_response("Doc upload receipt is missing document identity, revision or package_key")
    if manifest.get("document_id") and (identifier != manifest["document_id"] or revision != manifest["expected_revision"] + 1):
        raise invalid_success_response("Doc revision receipt differs from the prepared document revision")
    view = payload.get("document_view")
    if (not isinstance(view, dict) or view.get("document_id") != identifier or view.get("revision") != revision
            or any(view.get(key) != result["original"][key] for key in ("filename", "sha256", "size"))):
        raise invalid_success_response("Doc upload receipt does not identify the prepared original bytes")
    receipt = {**payload, "submitted": True, "server_request_id": metadata.get("request_id", ""),
               "package": str(package), "original": result["original"]}
    existing = package / RECEIPT
    if existing.exists():
        prior = json.loads(existing.read_text(encoding="utf-8"))
        if any(prior.get(key) != receipt.get(key) for key in ("document_id", "revision", "package_key", "original")):
            raise ValueError("Doc retry receipt conflicts with the earlier submission identity")
    else:
        write_json_once(existing, receipt)
    return receipt


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare, validate and submit an independent technical document to AKBS.")
    parser.add_argument("--profile", help="Select one existing AKBS member profile")
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--prepare", action="store_true", help="Prepare locally; do not upload")
    actions.add_argument("--validate", metavar="PACKAGE", help="Read-only validation of prepared Doc")
    actions.add_argument("--submit-latest", action="store_true", help="Submit one prepared Doc through the member route")
    parser.add_argument("--file", help="Real independent document original")
    parser.add_argument("--title", default="", help="Document title")
    parser.add_argument("--summary", default="", help="Purpose and scope of the document")
    parser.add_argument("--date", help="Actual outcome date YYYY-MM-DD")
    parser.add_argument("--run-id", help="Prepared run identity; exact selection for retries")
    parser.add_argument("--document-id", help="Existing server document identity when submitting a revision")
    parser.add_argument("--expected-revision", type=int, help="Current server revision for a document update")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.validate:
            gate = installed_plugin_family_status()
            if gate.get("blocking", True):
                raise ValueError(str(gate.get("message") or "member plugin install-family check failed"))
            result = validate_package(input_path(args.validate))
        else:
            config, _loaded = load_config(args.profile)
            gate = plugin_version_gate_check(config, fetch=True, require=True)
            if gate.get("blocking", True):
                raise ValueError(str(gate.get("message") or "member plugin version check failed"))
            if args.prepare:
                if not args.file:
                    raise ValueError("--prepare requires --file pointing to the real document")
                package = prepare_document(source=input_path(args.file), title=args.title, summary=args.summary,
                    date=parse_date_arg(args.date, config), config=config, run_id=args.run_id,
                    document_id=args.document_id, expected_revision=args.expected_revision)
                result = {"package": str(package), "local_check": validate_package(package), "submitted": False}
            else:
                if args.file or args.title or args.summary or args.document_id or args.expected_revision is not None or args.date:
                    raise ValueError("submission reuses the prepared original; content/revision options belong to --prepare")
                result = submit_document(latest_prepared(config, args.run_id), config)
    except HttpClientFailure as exc:
        raise SystemExit(exc.result.safe_summary("Doc request failed")) from None
    except (ValueError, OSError) as exc:
        raise SystemExit(str(exc)) from None
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status", "PASS") == "PASS" else 1
