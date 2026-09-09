from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Any


PLUGIN_ROOT = Path(__file__).resolve().parents[2]
ERROR_CONTRACT_PATH = PLUGIN_ROOT / "contracts" / "http" / "error-envelope-v1.schema.json"
ERROR_ENVELOPE_SCHEMA = "akbs-error-envelope-v1"
REQUEST_ID_RE = re.compile(r"^req_[0-9a-f]{32}$")
CODE_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
CORRELATION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
SECRET_KEY_RE = re.compile(
    r"(?i)(?:password|passwd|pwd|token|secret|api[_-]?key|cookie|authorization|credential|private[_-]?key|"
    r"sshpass|session(?:_id)?|request[_-]?body|body|payload|clipboard|environment|密码|口令|凭据|剪贴板|会话|请求正文)"
)
SECRET_VALUE_RE = re.compile(
    r"(?i)\b(password|passwd|pwd|token|secret|api[_-]?key|cookie|authorization|credential|sshpass|"
    r"session(?:_id)?|request[_-]?body|body|payload|clipboard)\b"
    r"\s*[:=]\s*(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)"
)
CHINESE_SECRET_RE = re.compile(r"(密码|口令|凭据|剪贴板|会话|请求正文)\s*[:=：]\s*[^\s，,;；]+")
AUTH_VALUE_RE = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]+")
SECRET_OPTION_RE = re.compile(
    r"(?i)(--(?:password|passwd|pwd|token|secret|api[_-]?key|cookie|authorization|credential|sshpass))"
    r"(?:=|\s+)\S+"
)
ENV_ASSIGNMENT_RE = re.compile(r"\b([A-Z][A-Z0-9_]{2,})=([^\s]+)")
PATH_RE = re.compile(r"(?:[A-Za-z]:[\\/][^\s]+|(?:/[A-Za-z0-9_.@+-]+){2,})")
V2_DIAGNOSTIC_CONTRACT = "akbs-android-change-v2-error/v1"
V2_DIAGNOSTIC_PATH_RE = re.compile(
    r"\$/(?:components|evidence)/[A-Za-z0-9][A-Za-z0-9_.-]{0,127}"
    r"(?:/[A-Za-z0-9][A-Za-z0-9_.-]{0,127})?"
)
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class HttpErrorKind(str, Enum):
    RETRYABLE = "retryable"
    AUTHENTICATION = "authentication"
    RESOURCE_LIMIT = "resource_limit"
    CONTRACT = "contract"
    BUSINESS = "business"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class HttpErrorResult:
    status_code: int
    code: str
    request_id: str
    message: str
    details: dict[str, Any]
    kind: HttpErrorKind
    retryable: bool
    business_decision_allowed: bool
    legacy_fallback: bool
    envelope_valid: bool

    def safe_summary(self, context: str) -> str:
        parts = [context, f"HTTP {self.status_code}" if self.status_code else "transport", f"code={self.code}"]
        if self.request_id:
            parts.append(f"request_id={self.request_id}")
        parts.append(f"kind={self.kind.value}")
        if self.legacy_fallback:
            parts.append("legacy_fallback=true")
        parts.append(self.message)
        return " ".join(parts)


class HttpClientFailure(RuntimeError):
    def __init__(self, result: HttpErrorResult):
        super().__init__(result.safe_summary("AKBS request failed"))
        self.result = result


@lru_cache(maxsize=1)
def error_contract_schema() -> dict[str, Any]:
    payload = json.loads(ERROR_CONTRACT_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("type") != "object":
        raise RuntimeError("packaged AKBS error envelope schema is invalid")
    required = payload.get("required")
    properties = payload.get("properties")
    if required != ["schema", "code", "message", "request_id"] or not isinstance(properties, dict):
        raise RuntimeError("packaged AKBS error envelope schema fields drifted")
    if properties.get("schema", {}).get("const") != ERROR_ENVELOPE_SCHEMA:
        raise RuntimeError("packaged AKBS error envelope schema name drifted")
    return payload


def error_contract_sha256() -> str:
    error_contract_schema()
    return hashlib.sha256(ERROR_CONTRACT_PATH.read_bytes()).hexdigest()


def sanitize_public_text(value: Any, fallback: str, *, limit: int = 240) -> str:
    text = str(value or "")
    text = AUTH_VALUE_RE.sub(lambda match: f"{match.group(1)} [REDACTED]", text)
    text = SECRET_OPTION_RE.sub(lambda match: f"{match.group(1)} [REDACTED]", text)
    text = SECRET_VALUE_RE.sub(lambda match: f"{match.group(1)}=[REDACTED]", text)
    text = CHINESE_SECRET_RE.sub(lambda match: f"{match.group(1)}=[REDACTED]", text)
    text = ENV_ASSIGNMENT_RE.sub(lambda match: f"{match.group(1)}=[REDACTED]", text)
    text = PATH_RE.sub("[PATH]", text)
    text = " ".join(text.split())
    if not text:
        return fallback
    if len(text) > limit:
        text = text[: limit - 3] + "..."
    return text


def sanitize_details(value: Any, *, depth: int = 0) -> Any:
    if depth >= 3:
        return "[TRUNCATED]"
    if isinstance(value, dict):
        validator_code = value.get("validator_code")
        v2_diagnostic = (
            depth == 0
            and value.get("contract") == V2_DIAGNOSTIC_CONTRACT
            and isinstance(validator_code, str)
            and validator_code.startswith("android_change_v2_")
            and CODE_RE.fullmatch(validator_code) is not None
            and SECRET_KEY_RE.search(validator_code) is None
        )
        result: dict[str, Any] = {}
        for index, (raw_key, raw_value) in enumerate(list(value.items())[:20]):
            raw_key_text = str(raw_key)
            key = raw_key_text if re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,63}", raw_key_text) else f"field_{index}"
            if SECRET_KEY_RE.search(str(raw_key)):
                result[f"field_{index}"] = "[REDACTED]"
            elif v2_diagnostic and key in {"contract", "validator_code"}:
                result[key] = raw_value
            elif (
                v2_diagnostic
                and key == "path"
                and isinstance(raw_value, str)
                and V2_DIAGNOSTIC_PATH_RE.fullmatch(raw_value) is not None
                and ".." not in raw_value
                and SECRET_KEY_RE.search(raw_value) is None
            ):
                # This is a bounded component/evidence locator, not a file path.
                # No URLs, drive prefixes, escapes, arbitrary nesting or prose.
                result[key] = raw_value
            else:
                result[key] = sanitize_details(raw_value, depth=depth + 1)
        return result
    if isinstance(value, list):
        return [sanitize_details(item, depth=depth + 1) for item in value[:20]]
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    # Error details are server-controlled and can echo arbitrary request/session
    # text. Outside the explicit root v2 diagnostic fields above, preserve useful
    # structure and numeric limits but never retain a raw string leaf.
    return "[REDACTED]"


def _valid_envelope(payload: Any, headers: Any) -> bool:
    schema = error_contract_schema()
    if not isinstance(payload, dict):
        return False
    allowed = set(schema["properties"])
    if set(payload) - allowed or any(field not in payload for field in schema["required"]):
        return False
    if payload.get("schema") != ERROR_ENVELOPE_SCHEMA:
        return False
    if not isinstance(payload.get("code"), str) or CODE_RE.fullmatch(payload["code"]) is None:
        return False
    message = payload.get("message")
    if not isinstance(message, str) or not message or len(message) > 240:
        return False
    request_id = payload.get("request_id")
    if not isinstance(request_id, str) or REQUEST_ID_RE.fullmatch(request_id) is None:
        return False
    correlation_id = payload.get("correlation_id")
    if correlation_id is not None and (
        not isinstance(correlation_id, str) or CORRELATION_ID_RE.fullmatch(correlation_id) is None
    ):
        return False
    if "details" in payload and not isinstance(payload["details"], dict):
        return False
    if "detail" in payload and (not isinstance(payload["detail"], str) or len(payload["detail"]) > 512):
        return False
    header_request_id = ""
    if headers is not None:
        try:
            header_request_id = str(headers.get("X-Request-ID") or "")
        except Exception:
            header_request_id = ""
    if header_request_id and header_request_id != request_id:
        return False
    return True


def classify_error(status_code: int, code: str, contract_codes: frozenset[str]) -> HttpErrorKind:
    if code in {"auth_required", "authentication_required", "forbidden", "authorization_error"}:
        return HttpErrorKind.AUTHENTICATION
    if code.startswith("archive_") or code in {"payload_too_large", "resource_exhausted"}:
        return HttpErrorKind.RESOURCE_LIMIT
    if code in contract_codes:
        return HttpErrorKind.CONTRACT
    if code in {"internal_error", "service_unavailable", "timeout", "rate_limited", "temporarily_unavailable"}:
        return HttpErrorKind.RETRYABLE
    if code in {
        "bad_request",
        "not_found",
        "conflict",
        "validation_error",
        "invalid_correlation_id",
    }:
        return HttpErrorKind.BUSINESS
    return HttpErrorKind.UNKNOWN


def _generic_message(kind: HttpErrorKind) -> str:
    return {
        HttpErrorKind.RETRYABLE: "service is temporarily unavailable",
        HttpErrorKind.AUTHENTICATION: "authentication or authorization failed",
        HttpErrorKind.RESOURCE_LIMIT: "request exceeded a server resource limit",
        HttpErrorKind.CONTRACT: "request was rejected by the server contract",
        HttpErrorKind.BUSINESS: "request was rejected by a business rule",
        HttpErrorKind.UNKNOWN: "request failed",
    }[kind]


def _read_error_body(error: urllib.error.HTTPError) -> bytes:
    try:
        body = error.read(MAX_RESPONSE_BYTES + 1)
    except TypeError:
        try:
            body = error.read()
        except Exception:
            return b""
    except Exception:
        return b""
    return body if isinstance(body, bytes) and len(body) <= MAX_RESPONSE_BYTES else b""


def parse_http_error(
    error: urllib.error.HTTPError,
    *,
    contract_codes: frozenset[str] = frozenset(),
) -> HttpErrorResult:
    body = _read_error_body(error)
    try:
        payload = json.loads(body.decode("utf-8")) if body else None
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = None
    status = int(getattr(error, "code", 0) or 0)
    headers = getattr(error, "headers", None)
    if not _valid_envelope(payload, headers):
        request_id = ""
        if headers is not None:
            try:
                candidate = str(headers.get("X-Request-ID") or "")
            except Exception:
                candidate = ""
            if REQUEST_ID_RE.fullmatch(candidate):
                request_id = candidate
        return HttpErrorResult(
            status_code=status,
            code="legacy_http_error",
            request_id=request_id,
            message="server returned a legacy HTTP error",
            details={},
            kind=HttpErrorKind.UNKNOWN,
            retryable=False,
            business_decision_allowed=False,
            legacy_fallback=True,
            envelope_valid=False,
        )
    assert isinstance(payload, dict)
    code = str(payload["code"])
    kind = classify_error(status, code, contract_codes)
    # The envelope message is validated for shape but is still server-controlled.
    # A code-derived message cannot echo a request body, session, path, or secret.
    message = _generic_message(kind)
    details = sanitize_details(payload.get("details", {}))
    return HttpErrorResult(
        status_code=status,
        code=code,
        request_id=str(payload["request_id"]),
        message=message,
        details=details if isinstance(details, dict) else {},
        kind=kind,
        retryable=kind == HttpErrorKind.RETRYABLE,
        business_decision_allowed=kind
        in {HttpErrorKind.AUTHENTICATION, HttpErrorKind.RESOURCE_LIMIT, HttpErrorKind.CONTRACT, HttpErrorKind.BUSINESS},
        legacy_fallback=False,
        envelope_valid=True,
    )


def failure_result(error: BaseException) -> HttpErrorResult:
    if isinstance(error, HttpClientFailure):
        return error.result
    if isinstance(error, urllib.error.HTTPError):
        return parse_http_error(error)
    if isinstance(error, (urllib.error.URLError, TimeoutError, ConnectionError, OSError)):
        return HttpErrorResult(
            status_code=0,
            code="transport_unavailable",
            request_id="",
            message="service is temporarily unavailable",
            details={},
            kind=HttpErrorKind.RETRYABLE,
            retryable=True,
            business_decision_allowed=False,
            legacy_fallback=False,
            envelope_valid=False,
        )
    return HttpErrorResult(
        status_code=0,
        code="client_error",
        request_id="",
        message="request failed in the client",
        details={},
        kind=HttpErrorKind.UNKNOWN,
        retryable=False,
        business_decision_allowed=False,
        legacy_fallback=False,
        envelope_valid=False,
    )


def invalid_success_response(message: str = "server response did not match the expected schema") -> HttpClientFailure:
    return HttpClientFailure(
        HttpErrorResult(
            status_code=0,
            code="invalid_success_response",
            request_id="",
            message=sanitize_public_text(message, "server response did not match the expected schema"),
            details={},
            kind=HttpErrorKind.CONTRACT,
            retryable=False,
            business_decision_allowed=False,
            legacy_fallback=False,
            envelope_valid=False,
        )
    )


def request_json_with_metadata(
    request: urllib.request.Request,
    *,
    timeout: float,
    contract_codes: frozenset[str] = frozenset(),
) -> tuple[dict[str, Any], dict[str, str]]:
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            headers = getattr(response, "headers", None)
            try:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
            except TypeError:
                raw = response.read()
    except urllib.error.HTTPError as error:
        raise HttpClientFailure(parse_http_error(error, contract_codes=contract_codes)) from None
    except Exception as error:
        raise HttpClientFailure(failure_result(error)) from None
    if not isinstance(raw, bytes) or len(raw) > MAX_RESPONSE_BYTES:
        raise invalid_success_response("server response exceeded the client limit")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise invalid_success_response("server response was not UTF-8 JSON") from None
    if not isinstance(payload, dict):
        raise invalid_success_response("server response was not a JSON object")
    request_id = ""
    if headers is not None:
        try:
            request_id = str(headers.get("X-Request-ID") or "").strip()
        except Exception:
            request_id = ""
    return payload, {"request_id": request_id if REQUEST_ID_RE.fullmatch(request_id) else ""}


def request_json(
    request: urllib.request.Request,
    *,
    timeout: float,
    contract_codes: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    payload, _metadata = request_json_with_metadata(
        request,
        timeout=timeout,
        contract_codes=contract_codes,
    )
    return payload
