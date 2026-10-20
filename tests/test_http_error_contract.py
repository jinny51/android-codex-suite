from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from email.message import Message
from pathlib import Path
from unittest.mock import patch

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = REPO_ROOT / "plugins" / "akbs-member-ops"
PLUGIN_LIB = PLUGIN_ROOT / "lib"
INTAKE_SCRIPTS = PLUGIN_ROOT / "internal" / "incoming-v2" / "scripts"
SCRIPTS_ROOT = REPO_ROOT / "scripts"
for path in (PLUGIN_LIB, INTAKE_SCRIPTS, SCRIPTS_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from akbs_member_ops.http_client import (  # noqa: E402
    HttpClientFailure,
    HttpErrorKind,
    error_contract_sha256,
    failure_result,
    parse_http_error,
    request_json,
)
from akbs_intake.incoming_contract import error_reason_codes, patch_queue_reason_codes  # noqa: E402
from akbs_intake.patch import information_completion  # noqa: E402
from validate_incoming_contract_gate import verify_public_contract  # noqa: E402


ERROR_SCHEMA_SHA256 = "82840edf68f219c52b3b031d3d789d22400bedbb1785dfa855722f30dec77c94"
PUBLIC_CONTRACT_SHA256 = "510141ce1ba9083b7952128c228bf3c08d7f36899b0da39dd85bf9ad667eed6b"
REQUEST_ID = "req_0123456789abcdef0123456789abcdef"


def make_http_error(
    *,
    status: int,
    code: str,
    message: str = "request rejected",
    details: dict[str, object] | None = None,
    body: bytes | None = None,
    request_id: str = REQUEST_ID,
) -> urllib.error.HTTPError:
    headers = Message()
    headers["Content-Type"] = "application/json"
    headers["X-Request-ID"] = request_id
    if body is None:
        payload: dict[str, object] = {
            "schema": "akbs-error-envelope-v1",
            "code": code,
            "message": message,
            "request_id": request_id,
        }
        if details is not None:
            payload["details"] = details
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return urllib.error.HTTPError(
        "http://akbs.invalid/member/upload",
        status,
        "synthetic failure",
        headers,
        io.BytesIO(body),
    )


def test_vendored_error_schema_and_incoming_pin_are_exact() -> None:
    pin = json.loads((REPO_ROOT / "contracts" / "incoming" / "v2" / "contract-pin.json").read_text(encoding="utf-8"))
    public = json.loads(
        (
            PLUGIN_ROOT
            / "internal"
            / "incoming-v2"
            / "references"
            / "incoming-public-contract-v2.json"
        ).read_text(encoding="utf-8")
    )

    assert error_contract_sha256() == ERROR_SCHEMA_SHA256
    assert pin["compatibility"] == "strict-content-hash-equality"
    assert pin["source_provenance"]["compatibility_condition"] is False
    assert pin["public_contract"]["sha256"] == PUBLIC_CONTRACT_SHA256
    assert pin["error_envelope"]["sha256"] == ERROR_SCHEMA_SHA256
    assert len(pin["reason_codes"]) == 97
    assert pin["reason_codes"] == sorted(error_reason_codes())
    assert len(public["reason_code_families"]["archive"]) == 12


def test_client_only_gate_builds_the_current_v2_package_flow() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS_ROOT / "validate_incoming_contract_gate.py"),
            "--mode",
            "client-only",
        ],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout)
    assert payload == {
        "status": "PASS",
        "contract": "incoming-v2-client",
        "execution": "local-fixture-only",
        "packages": ["daily", "patch", "weekly"],
    }


@pytest.mark.parametrize(
    ("status", "code", "contract_codes", "kind", "retryable"),
    [
        (503, "internal_error", frozenset(), HttpErrorKind.RETRYABLE, True),
        (401, "auth_required", frozenset(), HttpErrorKind.AUTHENTICATION, False),
        (413, "archive_compressed_bytes_exceeded", error_reason_codes(), HttpErrorKind.RESOURCE_LIMIT, False),
        (409, "package_already_exists", error_reason_codes(), HttpErrorKind.CONTRACT, False),
        (404, "not_found", frozenset(), HttpErrorKind.BUSINESS, False),
        (500, "unrecognized_server_code", frozenset(), HttpErrorKind.UNKNOWN, False),
    ],
)
def test_modern_envelope_produces_typed_safe_result(
    status: int,
    code: str,
    contract_codes: frozenset[str],
    kind: HttpErrorKind,
    retryable: bool,
) -> None:
    error = make_http_error(
        status=status,
        code=code,
        message=(
            "safe failure token=never-print Bearer abc.def --cookie raw-cookie "
            "session=raw-session request_body=raw-body /home/member/private/session.jsonl"
        ),
        details={
            "limit": 1,
            "token": "never-print-detail",
            "note": "arbitrary-session-echo",
            "nested": {"cookie": "raw-cookie", "path": "/home/member/private"},
        },
    )

    result = parse_http_error(error, contract_codes=contract_codes)
    rendered = result.safe_summary("synthetic request") + json.dumps(result.details, ensure_ascii=False)

    assert result.code == code
    assert result.request_id == REQUEST_ID
    assert result.kind is kind
    assert result.retryable is retryable
    assert result.envelope_valid is True
    assert result.legacy_fallback is False
    assert result.business_decision_allowed is (kind not in {HttpErrorKind.RETRYABLE, HttpErrorKind.UNKNOWN})
    for secret in (
        "never-print",
        "abc.def",
        "raw-cookie",
        "raw-session",
        "raw-body",
        "arbitrary-session-echo",
        "/home/member/private",
    ):
        assert secret not in rendered


def test_every_public_patch_queue_error_is_a_typed_contract_failure() -> None:
    for code in patch_queue_reason_codes():
        result = parse_http_error(
            make_http_error(status=409, code=code),
            contract_codes=error_reason_codes(),
        )
        assert result.code == code
        assert result.kind is HttpErrorKind.CONTRACT
        assert result.envelope_valid is True
        assert result.business_decision_allowed is True


def test_legacy_error_is_explicit_and_never_drives_retry_or_business_logic() -> None:
    legacy_body = json.dumps(
        {
            "detail": (
                "package_already_exists token=legacy-secret session=raw-session "
                "/home/member/session.jsonl"
            )
        }
    ).encode("utf-8")
    result = parse_http_error(
        make_http_error(status=409, code="unused", body=legacy_body),
        contract_codes=error_reason_codes(),
    )
    rendered = result.safe_summary("legacy request")

    assert result.code == "legacy_http_error"
    assert result.kind is HttpErrorKind.UNKNOWN
    assert result.legacy_fallback is True
    assert result.envelope_valid is False
    assert result.retryable is False
    assert result.business_decision_allowed is False
    assert "package_already_exists" not in rendered
    assert "legacy-secret" not in rendered
    assert "raw-session" not in rendered


def test_transport_and_bottom_exceptions_never_leak_their_text() -> None:
    transport = failure_result(TimeoutError("token=transport-secret body=raw-request"))
    unexpected = failure_result(RuntimeError("cookie=bottom-secret /home/member/session.jsonl"))

    assert transport.kind is HttpErrorKind.RETRYABLE
    assert transport.code == "transport_unavailable"
    assert unexpected.kind is HttpErrorKind.UNKNOWN
    assert unexpected.code == "client_error"
    combined = transport.safe_summary("upload") + unexpected.safe_summary("merge")
    for secret in ("transport-secret", "raw-request", "bottom-secret", "/home/member"):
        assert secret not in combined


def test_shared_request_client_preserves_modern_code_and_request_id() -> None:
    error = make_http_error(
        status=413,
        code="archive_total_bytes_exceeded",
        message="archive exceeded its total byte limit",
        details={"limit": 2},
    )
    request = urllib.request.Request("http://akbs.invalid/member/upload")

    with patch("urllib.request.urlopen", side_effect=error):
        with pytest.raises(HttpClientFailure) as caught:
            request_json(request, timeout=1, contract_codes=error_reason_codes())

    result = caught.value.result
    assert result.code == "archive_total_bytes_exceeded"
    assert result.request_id == REQUEST_ID
    assert result.details == {"limit": 2}
    assert result.kind is HttpErrorKind.RESOURCE_LIMIT


def make_synthetic_system_root(root: Path) -> Path:
    system_root = root / "synthetic-system"
    incoming_root = system_root / "contracts" / "incoming" / "v2"
    incoming_root.mkdir(parents=True)
    shutil.copy2(
        PLUGIN_ROOT
        / "internal"
        / "incoming-v2"
        / "references"
        / "incoming-public-contract-v2.json",
        incoming_root / "public-contract.json",
    )
    plugin_contract_root = REPO_ROOT / "contracts" / "incoming" / "v2"
    shutil.copy2(plugin_contract_root / "knowledge-incoming-package.schema.json", incoming_root)
    shutil.copy2(plugin_contract_root / "verification-acceptance-v2.json", incoming_root)
    shutil.copytree(plugin_contract_root / "fixtures", incoming_root / "fixtures")
    evaluator_root = system_root / "akbs_active" / "upload_domains"
    evaluator_root.mkdir(parents=True)
    shutil.copy2(
        PLUGIN_ROOT / "lib" / "akbs_member_ops" / "verification_acceptance.py",
        evaluator_root,
    )
    error_root = system_root / "contracts" / "http"
    error_root.mkdir(parents=True)
    shutil.copy2(PLUGIN_ROOT / "contracts" / "http" / "error-envelope-v1.schema.json", error_root)
    shutil.copy2(REPO_ROOT / "contracts" / "contract-set.json", system_root / "contracts")
    (system_root / "unrelated.txt").write_text("synthetic unrelated source commit\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", "-b", "main", str(system_root)], check=True)
    subprocess.run(["git", "config", "user.email", "contract@example.invalid"], cwd=system_root, check=True)
    subprocess.run(["git", "config", "user.name", "Synthetic Contract"], cwd=system_root, check=True)
    subprocess.run(["git", "add", "."], cwd=system_root, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "synthetic unrelated system commit"],
        cwd=system_root,
        check=True,
    )
    return system_root


def test_unrelated_system_commit_with_identical_contract_content_passes(tmp_path: Path) -> None:
    system_root = make_synthetic_system_root(tmp_path)

    stats, _public = verify_public_contract(system_root, REPO_ROOT)

    assert stats["public_contract_sha256"] == PUBLIC_CONTRACT_SHA256
    assert stats["error_envelope_sha256"] == ERROR_SCHEMA_SHA256
    assert stats["reason_codes"] == 97
    assert stats["source_provenance_matches"] is False


@pytest.mark.parametrize(
    "drift",
    [
        "contract_hash",
        "reason_code",
        "manifest_schema",
        "verification_contract",
        "verification_evaluator",
        "fixture",
        "error_envelope",
    ],
)
def test_contract_content_drift_still_fails_closed(tmp_path: Path, drift: str) -> None:
    system_root = make_synthetic_system_root(tmp_path)
    incoming_root = system_root / "contracts" / "incoming" / "v2"
    if drift == "contract_hash":
        path = incoming_root / "public-contract.json"
        path.write_bytes(path.read_bytes() + b"\n")
    elif drift == "reason_code":
        path = incoming_root / "public-contract.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["reason_code_families"]["identity"].append("synthetic_reason_drift")
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    elif drift == "manifest_schema":
        path = incoming_root / "knowledge-incoming-package.schema.json"
        path.write_bytes(path.read_bytes() + b"\n")
    elif drift == "verification_contract":
        path = incoming_root / "verification-acceptance-v2.json"
        path.write_bytes(path.read_bytes() + b"\n")
    elif drift == "verification_evaluator":
        path = system_root / "akbs_active" / "upload_domains" / "verification_acceptance.py"
        path.write_bytes(path.read_bytes() + b"\n")
    elif drift == "fixture":
        path = incoming_root / "fixtures" / "daily.manifest.json"
        path.write_bytes(path.read_bytes() + b"\n")
    else:
        path = system_root / "contracts" / "http" / "error-envelope-v1.schema.json"
        path.write_bytes(path.read_bytes() + b"\n")

    with pytest.raises(AssertionError):
        verify_public_contract(system_root, REPO_ROOT)


def information_detail(queue_state: str) -> dict[str, object]:
    return {
        "request_id": "information-request-test",
        "patch_package_id": "patch-package-test",
        "package_key": "20261019/member/20261019-120000-patch",
        "queue_state": queue_state,
        "information_request": {
            "request_id": "information-request-test",
            "patch_set_sha256": "a" * 64,
        },
    }


@pytest.mark.parametrize("fields", [
    {"platform": "not-a-platform"},
    {"platform": {"oops": 1}},
    {"platform": {}},
    {"summary": []},
    {"summary": {"oops": 1}},
    {"project": 123},
    {"android_version": 14},
    {"projects": ["TVE1086U", {"oops": 1}]},
    {"projects": 123},
])
def test_information_completion_rejects_invalid_field_values(fields: dict[str, object]) -> None:
    with pytest.raises(SystemExit, match="information_fields_invalid"):
        information_completion.completion_fields(fields)


@pytest.mark.parametrize("platform", ["mtk", "rk", "unisoc"])
def test_information_completion_preserves_valid_text_and_projects(platform: str) -> None:
    assert information_completion.completion_fields({
        "platform": f" {platform} ", "project": " TVE1086U ",
        "android_version": " 14 ", "summary": " 新增现场证据 ",
        "projects": [" TVE1086U ", "", "TVE8402M"],
    }) == {
        "platform": platform, "project": "TVE1086U", "android_version": "14",
        "summary": "新增现场证据", "projects": ["TVE1086U", "TVE8402M"],
    }
    assert information_completion.completion_fields({"projects": " TVE1086U,TVE8402M "}) == {
        "projects": "TVE1086U,TVE8402M",
    }
    assert information_completion.completion_fields({"summary": "", "platform": None}) == {}


def information_response_file(tmp_path: Path, **changes: object) -> Path:
    path = tmp_path / "response.json"
    path.write_text(json.dumps({
        "schema": information_completion.COMPLETION_SCHEMA,
        "request_id": "information-request-test",
        "statement": "Existing source evidence clarification",
        **changes,
    }), encoding="utf-8")
    return path


def json_http_response(payload: dict[str, object]) -> io.BytesIO:
    response = io.BytesIO(json.dumps(payload).encode("utf-8"))
    response.headers = Message()
    response.headers["X-Request-ID"] = REQUEST_ID
    return response


def test_information_completion_retries_lost_response_with_exact_original_request(tmp_path: Path) -> None:
    response_path = information_response_file(tmp_path)
    state = "information_required"
    posts = []

    def transport(request, **_kwargs):
        nonlocal state
        assert request.get_header("X-akbs-user") == "member"
        if request.get_method() == "GET":
            return json_http_response(information_detail(state))
        posts.append((request.data, request.get_header("Idempotency-key")))
        assert json.loads(request.data)["patch_set_sha256"] == "a" * 64
        if len(posts) == 1:
            state = "information_review"
            raise TimeoutError("response lost after server acceptance")
        assert posts[1] == posts[0]
        return json_http_response({**information_detail(state), "replay": True})

    config = {"member_alias": "member", "submission_api_base_url": "http://akbs.invalid/akbs/api"}
    with patch("urllib.request.urlopen", side_effect=transport):
        with pytest.raises(SystemExit, match="transport_unavailable"):
            information_completion.complete_information_request(config, response_path)
        result = information_completion.complete_information_request(config, response_path)
    assert result["replay"] is True
    assert result["queue_state"] == "information_review"
    assert len(posts) == 2


@pytest.mark.parametrize("state", ["information_review", "admitted", "closed"])
def test_information_completion_accepts_only_existing_server_replay_after_progress(
    tmp_path: Path, state: str,
) -> None:
    response_path = information_response_file(tmp_path, idempotency_key="same-key")
    config = {"member_alias": "member", "submission_api_base_url": "http://akbs.invalid/akbs/api"}
    for replay in (False, True):
        with patch("urllib.request.urlopen", side_effect=[
            json_http_response(information_detail(state)),
            json_http_response({**information_detail(state), "replay": replay}),
        ]) as transport:
            if replay:
                result = information_completion.complete_information_request(config, response_path)
                assert result["queue_state"] == state
            else:
                with pytest.raises(SystemExit, match="queue_state_not_reviewable"):
                    information_completion.complete_information_request(config, response_path)
        assert transport.call_count == 2
        post = transport.call_args_list[1].args[0]
        assert post.get_header("Idempotency-key") == "same-key"


@pytest.mark.parametrize(("code", "status"), [
    ("idempotency_conflict", 409),
    ("information_request_closed", 409),
    ("patch_set_proof_mismatch", 409),
    ("information_request_member_mismatch", 403),
])
def test_information_completion_replay_does_not_hide_server_rejection(
    tmp_path: Path, code: str, status: int,
) -> None:
    response_path = information_response_file(tmp_path, idempotency_key="original-key", statement="changed content")
    detail = information_detail("information_review")
    if code == "patch_set_proof_mismatch":
        detail["information_request"]["patch_set_sha256"] = "b" * 64
    with patch("urllib.request.urlopen", side_effect=[
        json_http_response(detail),
        make_http_error(status=status, code=code),
    ]) as transport:
        with pytest.raises(SystemExit, match=code):
            information_completion.complete_information_request({"member_alias": "member"}, response_path)
    assert transport.call_count == 2
    post = transport.call_args_list[1].args[0]
    assert post.get_header("Idempotency-key") == "original-key"
    assert json.loads(post.data)["statement"] == "changed content"
    assert json.loads(post.data)["patch_set_sha256"] == detail["information_request"]["patch_set_sha256"]


@pytest.mark.parametrize("replay", [None, False])
def test_information_completion_normal_first_submission_still_succeeds(tmp_path: Path, replay: bool | None) -> None:
    response_path = information_response_file(tmp_path)
    result = information_detail("information_review")
    if replay is not None:
        result["replay"] = replay
    with patch("urllib.request.urlopen", side_effect=[
        json_http_response(information_detail("information_required")), json_http_response(result),
    ]):
        actual = information_completion.complete_information_request({"member_alias": "member"}, response_path)
    assert actual["queue_state"] == "information_review"


def test_inspection_still_requires_open_request_and_failed_identity_never_posts(tmp_path: Path) -> None:
    config = {"member_alias": "member"}
    with patch("urllib.request.urlopen", return_value=json_http_response(information_detail("admitted"))) as transport:
        with pytest.raises(SystemExit, match="queue_state_not_reviewable"):
            information_completion.inspect_information_request(config, "information-request-test")
    assert transport.call_count == 1

    response_path = information_response_file(tmp_path)
    wrong_detail = information_detail("information_review")
    wrong_detail["request_id"] = "another-request"
    with patch("urllib.request.urlopen", return_value=json_http_response(wrong_detail)) as transport:
        with pytest.raises(SystemExit, match="information_request_identity_mismatch"):
            information_completion.complete_information_request(config, response_path)
    assert transport.call_count == 1


@pytest.mark.parametrize("field", ["request_id", "patch_package_id"])
def test_information_completion_replay_still_checks_returned_identity(tmp_path: Path, field: str) -> None:
    response_path = information_response_file(tmp_path)
    result = {**information_detail("admitted"), "replay": True, field: "another-identity"}
    with patch("urllib.request.urlopen", side_effect=[
        json_http_response(information_detail("admitted")), json_http_response(result),
    ]):
        with pytest.raises(SystemExit, match="identity_mismatch"):
            information_completion.complete_information_request({"member_alias": "member"}, response_path)
