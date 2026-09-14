from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path, PurePosixPath
import urllib.parse
import urllib.request
from typing import Any

from akbs_member_ops.http_client import HttpClientFailure, request_json_with_metadata

from ..config import submission_api_base_url
from ..incoming_contract import (
    error_reason_codes,
    patch_information_attachment_limits,
    patch_information_completion_fields,
)


COMPLETION_SCHEMA = "akbs-patch-package-information-completion/v1"
ALLOWED_COMPLETION_FIELDS = patch_information_completion_fields()
MAX_ATTACHMENT_BYTES, MAX_ATTACHMENT_TOTAL_BYTES = patch_information_attachment_limits()


def inspect_information_request(config: dict[str, str], request_id: str) -> dict[str, Any]:
    member = require_member(config)
    request_id = require_text(request_id, "information request id")
    base_url = submission_api_base_url(config).rstrip("/")
    url = (
        f"{base_url}/member/{urllib.parse.quote(member, safe='')}/information-requests/"
        f"{urllib.parse.quote(request_id, safe='')}"
    )
    request = urllib.request.Request(url, method="GET", headers={"X-AKBS-User": member})
    try:
        payload, metadata = request_json_with_metadata(
            request,
            timeout=30,
            contract_codes=error_reason_codes(),
        )
    except HttpClientFailure as error:
        raise SystemExit(error.result.safe_summary("补丁包补充资料请求读取失败")) from None
    information = payload.get("information_request") if isinstance(payload.get("information_request"), dict) else {}
    if str(payload.get("request_id") or "") != request_id or str(information.get("request_id") or "") != request_id:
        raise SystemExit("[information_request_identity_mismatch] 服务端返回了不同的补充资料请求。")
    patch_package_id = require_text(payload.get("patch_package_id"), "patch_package_id")
    source_package_key = require_text(payload.get("package_key"), "source package_key")
    if patch_package_id == source_package_key:
        raise SystemExit("[patch_package_identity_mismatch] package_key 只能标识物理来源，不能充当补丁包业务身份。")
    if str(payload.get("queue_state") or "") != "information_required":
        raise SystemExit("[queue_state_not_reviewable] 补充资料请求不在 information_required 阶段。")
    patch_sha = str(information.get("patch_set_sha256") or "")
    if len(patch_sha) != 64:
        raise SystemExit("[patch_set_proof_missing] 补充资料请求没有绑定不可变补丁集合。")
    payload["lookup_request_id"] = str(metadata.get("request_id") or "")
    return payload


def complete_information_request(config: dict[str, str], response_path: Path) -> dict[str, Any]:
    response = read_response(response_path)
    request_id = require_text(response.get("request_id"), "request_id")
    detail = inspect_information_request(config, request_id)
    information = detail["information_request"]
    patch_package_id = str(detail["patch_package_id"])
    body = {
        "statement": str(response.get("statement") or "").strip(),
        "fields": completion_fields(response.get("fields")),
        "attachments": completion_attachments(response.get("attachments"), response_path.parent),
        "patch_set_sha256": str(information["patch_set_sha256"]),
    }
    if not body["statement"] and not body["fields"] and not body["attachments"]:
        raise SystemExit("[information_completion_empty] 至少填写说明、字段或非补丁附件之一。")
    member = require_member(config)
    base_url = submission_api_base_url(config).rstrip("/")
    url = (
        f"{base_url}/member/{urllib.parse.quote(member, safe='')}/information-requests/"
        f"{urllib.parse.quote(request_id, safe='')}/complete"
    )
    encoded = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    idempotency_key = str(response.get("idempotency_key") or "").strip()
    if not idempotency_key:
        idempotency_key = "patch-info-" + hashlib.sha256(encoded).hexdigest()[:32]
    request = urllib.request.Request(
        url,
        data=encoded,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-AKBS-User": member,
            "Idempotency-Key": idempotency_key,
        },
    )
    try:
        result, metadata = request_json_with_metadata(
            request,
            timeout=30,
            contract_codes=error_reason_codes(),
        )
    except HttpClientFailure as error:
        raise SystemExit(error.result.safe_summary("补丁包补充资料提交失败")) from None
    if str(result.get("request_id") or "") != request_id:
        raise SystemExit("[information_request_identity_mismatch] 服务端补充结果没有绑定原请求。")
    if str(result.get("patch_package_id") or "") != patch_package_id:
        raise SystemExit("[patch_package_identity_mismatch] 服务端补充结果改变了补丁包业务身份。")
    if str(result.get("queue_state") or "") != "information_review":
        raise SystemExit("[queue_state_not_reviewable] 补充资料提交后未进入 information_review 阶段。")
    result.setdefault("request_id", request_id)
    result.setdefault("server_request_id", str(metadata.get("request_id") or ""))
    return result


def read_response(path: Path) -> dict[str, Any]:
    source = Path(path).expanduser().resolve()
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"补充资料响应 JSON 读取失败: {source}: {error}") from error
    if not isinstance(payload, dict) or payload.get("schema") != COMPLETION_SCHEMA:
        raise SystemExit(f"补充资料响应必须使用 schema={COMPLETION_SCHEMA}")
    return payload


def completion_fields(value: Any) -> dict[str, Any]:
    if value in (None, {}):
        return {}
    if not isinstance(value, dict):
        raise SystemExit("补充资料 fields 必须是对象。")
    result: dict[str, Any] = {}
    for key, item in value.items():
        field = str(key).strip()
        if field not in ALLOWED_COMPLETION_FIELDS:
            raise SystemExit(f"[information_fields_unsupported] 不支持补充字段：{field or '<empty>'}")
        normalized = item.strip() if isinstance(item, str) else item
        if not field or normalized in (None, "", [], {}):
            continue
        result[field] = normalized
    return result


def completion_attachments(value: Any, base_dir: Path) -> list[dict[str, str]]:
    if value in (None, []):
        return []
    if not isinstance(value, list):
        raise SystemExit("补充资料 attachments 必须是数组。")
    result: list[dict[str, str]] = []
    total = 0
    for item in value:
        if not isinstance(item, dict):
            raise SystemExit("补充资料附件项必须是对象。")
        relative_path = safe_relative_path(item.get("relative_path"))
        source_text = require_text(item.get("source_path"), "attachment.source_path")
        source = Path(source_text).expanduser()
        if not source.is_absolute():
            source = base_dir / source
        source = source.resolve()
        try:
            content = source.read_bytes()
        except OSError as error:
            raise SystemExit(f"补充资料附件读取失败: {source}: {error}") from error
        if len(content) > MAX_ATTACHMENT_BYTES:
            raise SystemExit(f"补充资料单个附件超过 {MAX_ATTACHMENT_BYTES} bytes: {relative_path}")
        total += len(content)
        if total > MAX_ATTACHMENT_TOTAL_BYTES:
            raise SystemExit(f"补充资料附件总量超过 {MAX_ATTACHMENT_TOTAL_BYTES} bytes")
        result.append(
            {
                "relative_path": relative_path,
                "content_base64": base64.b64encode(content).decode("ascii"),
            }
        )
    return result


def safe_relative_path(value: Any) -> str:
    raw = require_text(value, "attachment.relative_path").replace("\\", "/")
    path = PurePosixPath(raw)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise SystemExit(f"补充资料附件路径不安全: {raw}")
    normalized = path.as_posix()
    if normalized in {"", "."}:
        raise SystemExit(f"补充资料附件路径不安全: {raw}")
    if normalized.lower().endswith((".patch", ".diff")) or normalized.lower().startswith("patches/"):
        raise SystemExit("[patch_asset_immutable] 补充资料不能修改或新增补丁文件。")
    return normalized


def require_member(config: dict[str, str]) -> str:
    member = str(config.get("member_alias") or "").strip()
    if not member or member == "unknown":
        raise SystemExit("member_alias 不能为空或不能使用 unknown")
    return member


def require_text(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise SystemExit(f"{label} 不能为空")
    return text
