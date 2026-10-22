"""Read exact independent Doc versions and verify their stored original bytes."""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from akbs_member_ops.artifact_paths import require_safe_artifact_path
from akbs_member_ops.http_client import HttpClientFailure, failure_result, invalid_success_response, parse_http_error, request_json
from akbs_member_ops.knowledge.merge_confirmation.client import member_request_headers
from akbs_member_ops.knowledge_search.config import member_api_base_url

IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,255}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def _valid_revision(revision: int | None) -> None:
    if revision is not None and (type(revision) is not int or revision < 1):
        raise ValueError("document revision must be a positive server-returned revision")


def fetch_document(document_id: str, *, revision: int | None = None, timeout: float = 15.0) -> dict[str, Any]:
    if not isinstance(document_id, str) or not IDENTIFIER.fullmatch(document_id):
        raise ValueError("document_id must be a server-returned document identity")
    _valid_revision(revision)
    base, _source = member_api_base_url()
    endpoint = base.rstrip("/") + "/akbs/api/member/me/documents/" + urllib.parse.quote(document_id, safe="")
    if revision is not None:
        endpoint += "?" + urllib.parse.urlencode({"revision": revision})
    payload = request_json(urllib.request.Request(endpoint, headers=member_request_headers(), method="GET"), timeout=timeout)
    view = payload.get("document_view")
    if (not isinstance(view, dict) or view.get("document_id") != document_id
            or type(view.get("revision")) is not int or view["revision"] < 1
            or (revision is not None and view["revision"] != revision)):
        raise invalid_success_response("Doc detail identity or revision mismatch")
    if (not isinstance(view.get("filename"), str) or not view["filename"]
            or not isinstance(view.get("sha256"), str) or not SHA256.fullmatch(view["sha256"])
            or type(view.get("size")) is not int or view["size"] < 1
            or not isinstance(view.get("original_url"), str) or not view["original_url"]):
        raise invalid_success_response("Doc detail original metadata is invalid")
    knowledge = payload.get("knowledge")
    if knowledge is not None:
        if (not isinstance(knowledge, dict) or knowledge.get("revision") != view["revision"]
                or any(not isinstance(knowledge.get(key), str) for key in ("title", "summary", "content", "applicability"))):
            raise invalid_success_response("Doc knowledge does not match its original version")
    return {**payload, "source": "server_api"}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        return None


def _open_original(request: urllib.request.Request, timeout: float):
    # Stored Doc originals are served by AKBS, not an external redirect target.
    return urllib.request.build_opener(_NoRedirect()).open(request, timeout=timeout)


def download_document(document_id: str, output: Path, *, revision: int | None = None, timeout: float = 15.0) -> dict[str, Any]:
    raw = Path(os.path.abspath(output.expanduser()))
    if raw.exists() or raw.is_symlink():
        raise ValueError("document download output already exists")
    target = require_safe_artifact_path(output, purpose="knowledge document download")
    detail = fetch_document(document_id, revision=revision, timeout=timeout)
    view = detail["document_view"]
    base, _source = member_api_base_url()
    url = urllib.parse.urljoin(base.rstrip("/") + "/", view["original_url"])
    parsed, authority = urllib.parse.urlsplit(url), urllib.parse.urlsplit(base)
    if (parsed.scheme not in {"http", "https"} or (parsed.scheme, parsed.netloc) != (authority.scheme, authority.netloc)
            or parsed.fragment or parsed.username or parsed.password):
        raise invalid_success_response("Doc original URL is not on the configured AKBS origin")
    headers = member_request_headers()
    headers["Accept"] = "application/octet-stream"
    request = urllib.request.Request(url, headers=headers, method="GET")
    target.parent.mkdir(parents=True, exist_ok=True)
    size = 0
    digest = hashlib.sha256()
    with tempfile.TemporaryFile(mode="w+b", dir=target.parent) as staged:
        try:
            with _open_original(request, timeout) as response:
                while True:
                    requested = min(64 * 1024, view["size"] - size + 1)
                    chunk = response.read(requested)
                    if not isinstance(chunk, bytes) or len(chunk) > requested:
                        raise invalid_success_response("Doc original bytes differ from the exact version metadata")
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > view["size"]:
                        raise invalid_success_response("Doc original bytes exceed the exact version metadata")
                    digest.update(chunk)
                    staged.write(chunk)
        except urllib.error.HTTPError as exc:
            raise HttpClientFailure(parse_http_error(exc)) from None
        except HttpClientFailure:
            raise
        except Exception as exc:
            raise HttpClientFailure(failure_result(exc)) from None
        if size != view["size"] or digest.hexdigest() != view["sha256"]:
            raise invalid_success_response("Doc original bytes differ from the exact version metadata")
        staged.seek(0)
        created = None
        try:
            with target.open("xb") as stream:
                created = os.fstat(stream.fileno())
                shutil.copyfileobj(staged, stream, length=64 * 1024)
        except BaseException:
            if created is not None:
                current = target.lstat() if target.exists() else None
                if current is not None and (created.st_dev, created.st_ino) == (current.st_dev, current.st_ino):
                    target.unlink()
            raise
    return {"document_id": document_id, "revision": view["revision"], "output": str(target),
            "sha256": digest.hexdigest(), "size_bytes": size, "verified_original": True,
            "reuse_outcome": "not_started", "source": "server_api"}


def format_document_detail(payload: dict[str, Any]) -> str:
    view = payload["document_view"]
    knowledge = payload.get("knowledge") or {}
    return "\n".join([f"# {knowledge.get('title') or view.get('title') or view['filename']}", "",
        f"document_id={view['document_id']} / revision={view['revision']}", "",
        str(knowledge.get("summary") or view.get("summary") or ""), "", str(knowledge.get("content") or ""),
        "", "适用条件与限制：", str(knowledge.get("applicability") or "未声明额外条件"), "",
        f"原文件：{view['filename']} / SHA-256={view['sha256']} / bytes={view['size']}",
        "原文可取得不等于已在本次任务采用；按实际用途阅读和判断。"])
