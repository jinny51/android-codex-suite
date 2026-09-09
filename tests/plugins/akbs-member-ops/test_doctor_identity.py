from __future__ import annotations

import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import urllib.error


ROOT = Path(__file__).resolve().parents[3]
PLUGIN = ROOT / "plugins" / "akbs-member-ops"
sys.path.insert(0, str(PLUGIN / "lib"))
sys.path.insert(0, str(PLUGIN / "internal" / "incoming-v1" / "scripts"))

from akbs_intake import config, doctor  # noqa: E402
from akbs_member_ops import http_client  # noqa: E402


def server_identity(**changes: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema": "akbs-member-identity-v1",
        "authenticated": True,
        "member_alias": "contributor1",
        "member_name": "Contributor One",
        "member_status": "active",
        "ranking_eligible": False,
        "daily_required": False,
        "weekly_required": False,
    }
    payload.update(changes)
    return payload


class Response:
    headers: dict[str, str] = {}

    def __init__(self, payload: object):
        self.raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _size: int = -1) -> bytes:
        return self.raw


class DoctorIdentityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.environment = mock.patch.dict(os.environ, {
            "CODEX_HOME": str(self.home),
            "AKBS_ROOT": str(self.home / "separate-admin-root"),
        }, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.configuration = {
            **config.CONFIG_DEFAULTS,
            "profile": "contributor1",
            "member_alias": "contributor1",
            "member_name": "Contributor One",
            "role": "member",
            "allowed_modes": "daily,weekly,patch",
        }
        self.gate = mock.Mock(return_value={"status": "PASS", "blocking": False})
        self.run_command = mock.Mock(return_value=subprocess.CompletedProcess(
            ["git", "--version"], 0, stdout="git version test\n", stderr=""
        ))
        self.transport = mock.patch.object(http_client.urllib.request, "urlopen")
        self.open = self.transport.start()
        self.addCleanup(self.transport.stop)
        self.open.return_value = Response(server_identity())

    def run_doctor(self, *, check_remote: bool = False, strict: bool = True):
        return doctor.doctor(
            self.configuration,
            [self.home / "akbs-member-ops.toml"],
            strict=strict,
            check_remote=check_remote,
            plugin_root=PLUGIN,
            run_command=self.run_command,
            plugin_gate_check=self.gate,
        )

    def test_local_configuration_is_not_server_authentication(self) -> None:
        result = self.run_doctor()
        self.assertEqual(result["status"], "PASS", result)
        self.assertEqual(result["member_identity"]["status"], "configured_unverified")
        self.assertFalse(result["member_identity"]["authenticated"])
        self.assertNotIn("ranking_eligible", result["member_identity"])
        self.open.assert_not_called()

    def test_remote_confirms_member_without_admin_role_or_ranking_requirement(self) -> None:
        result = self.run_doctor(check_remote=True)
        self.assertEqual(result["status"], "PASS")
        identity = result["member_identity"]
        self.assertEqual(identity["status"], "server_confirmed")
        self.assertTrue(identity["authenticated"])
        self.assertEqual(identity["member_alias"], "contributor1")
        for key in ("ranking_eligible", "daily_required", "weekly_required"):
            self.assertIs(identity[key], False)
        self.assertEqual(self.open.call_count, 1)
        self.gate.assert_any_call(self.configuration, fetch=True, require=False)
        self.gate.assert_any_call(self.configuration, fetch=True, require=True)

    def test_get_uses_only_alias_and_configured_api_base(self) -> None:
        with mock.patch.dict(os.environ, {
            "CODEX_REPORT_AKBS_ENDPOINT_SUBMISSION_API_BASE_URL": "https://akbs.example/akbs/api/"
        }):
            self.run_doctor(check_remote=True)
        request = self.open.call_args.args[0]
        self.assertEqual(request.full_url, "https://akbs.example/akbs/api/member/me/identity")
        self.assertEqual(request.get_method(), "GET")
        self.assertIsNone(request.data)
        self.assertEqual(dict(request.header_items()), {
            "Accept": "application/json", "X-akbs-user": "contributor1"
        })
        self.assertEqual(self.open.call_args.kwargs, {"timeout": 6})

    def test_missing_invalid_or_placeholder_alias_does_not_contact_server(self) -> None:
        for alias, status in (("", "alias_required"), ("invalid alias", "alias_invalid"), ("unknown", "alias_invalid")):
            with self.subTest(alias=alias):
                self.configuration["member_alias"] = alias
                result = self.run_doctor(check_remote=True)
                self.assertEqual(result["status"], "FAIL")
                self.assertEqual(result["member_identity"]["status"], status)
        self.open.assert_not_called()

    def test_remote_alias_mismatch_never_rewrites_profile(self) -> None:
        original = dict(self.configuration)
        self.open.return_value = Response(server_identity(member_alias="another-member"))
        result = self.run_doctor(check_remote=True)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["member_identity"]["status"], "identity_mismatch")
        self.assertFalse(result["member_identity"]["authenticated"])
        self.assertEqual(self.configuration, original)
        self.assertNotIn("member_alias", result["member_identity"])

    def test_alias_comparison_is_exact_not_case_or_whitespace_normalization(self) -> None:
        for alias in ("Contributor1", "contributor1 "):
            with self.subTest(alias=alias):
                self.open.return_value = Response(server_identity(member_alias=alias))
                result = self.run_doctor(check_remote=True)
                self.assertEqual(result["member_identity"]["status"], "identity_mismatch")

    def test_401_403_and_old_server_404_are_distinct_unconfirmed_failures(self) -> None:
        for code, status in ((401, "authentication_failed"), (403, "authentication_failed"), (404, "server_unsupported"), (503, "server_unavailable")):
            with self.subTest(code=code):
                self.open.side_effect = urllib.error.HTTPError(
                    "https://example.invalid", code, "failed", {}, io.BytesIO(b'{"detail":"private failure text"}')
                )
                result = self.run_doctor(check_remote=True)
                self.assertEqual(result["status"], "FAIL")
                self.assertEqual(result["member_identity"]["status"], status)
                self.assertEqual(result["member_identity"]["http_status"], code)
                self.assertFalse(result["member_identity"]["authenticated"])
                self.assertNotIn("private failure text", json.dumps(result))

    def test_network_failure_is_not_identity_rejection(self) -> None:
        for error in (TimeoutError("private timeout text"), urllib.error.URLError("private network text")):
            with self.subTest(error=type(error).__name__):
                self.open.side_effect = error
                result = self.run_doctor(check_remote=True)
                self.assertEqual(result["status"], "FAIL")
                self.assertEqual(result["member_identity"]["status"], "server_unavailable")
                self.assertEqual(result["member_identity"]["error_code"], "transport_unavailable")
                self.assertNotIn("private", json.dumps(result))

    def test_contract_requires_server_schema_authenticated_true_and_typed_policy(self) -> None:
        cases = [
            server_identity(schema="wrong"), server_identity(authenticated=False),
            server_identity(authenticated=1), server_identity(member_alias=""),
            server_identity(member_name=""), server_identity(member_status=None),
            server_identity(ranking_eligible=0), server_identity(daily_required="false"),
            server_identity(weekly_required=None),
        ]
        for payload in cases:
            with self.subTest(payload=payload):
                self.open.return_value = Response(payload)
                result = self.run_doctor(check_remote=True)
                self.assertEqual(result["status"], "FAIL")
                self.assertEqual(result["member_identity"]["status"], "invalid_response")
                self.assertFalse(result["member_identity"]["authenticated"])

    def test_non_json_and_non_object_success_fail_closed(self) -> None:
        for payload in (b"<html>login</html>", [], None):
            with self.subTest(payload=payload):
                self.open.return_value = Response(payload)
                result = self.run_doctor(check_remote=True)
                self.assertEqual(result["status"], "FAIL")
                self.assertEqual(result["member_identity"]["status"], "invalid_response")

    def test_server_status_is_reported_without_inventing_business_permissions(self) -> None:
        self.open.return_value = Response(server_identity(member_status="inactive"))
        result = self.run_doctor(check_remote=True)
        identity = result["member_identity"]
        self.assertEqual(identity["status"], "server_confirmed")
        self.assertEqual(identity["member_status"], "inactive")
        self.assertNotIn("can_upload", identity)
        self.assertNotIn("admin", identity)

    def test_only_identity_contract_fields_are_published(self) -> None:
        self.open.return_value = Response(server_identity(ip_mapping="private", token="secret"))
        identity = self.run_doctor(check_remote=True)["member_identity"]
        self.assertNotIn("ip_mapping", identity)
        self.assertNotIn("token", identity)
        self.assertNotIn("private", json.dumps(identity))
        self.assertNotIn("secret", json.dumps(identity))

    def test_server_confirmation_cannot_hide_existing_local_or_install_failure(self) -> None:
        self.configuration["member_name"] = ""
        self.gate.return_value = {"status": "FAIL", "blocking": True, "message": "installation invalid"}
        result = self.run_doctor(check_remote=True)
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("installation invalid", result["strict"]["errors"])
        self.assertTrue(any("member_name" in error for error in result["strict"]["errors"]))

    def test_non_strict_remote_failure_still_reports_failure(self) -> None:
        self.open.side_effect = TimeoutError()
        result = self.run_doctor(check_remote=True, strict=False)
        self.assertEqual(result["status"], "FAIL")

    def test_direct_strict_checks_also_verify_server_identity(self) -> None:
        result = doctor.doctor_strict_checks(
            self.configuration, [], True, False,
            run_command=self.run_command, plugin_gate_check=self.gate,
        )
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(self.open.call_count, 1)

    def test_doctor_never_writes_local_configuration_or_artifacts(self) -> None:
        profile = self.home / "akbs-member-ops.toml"
        profile.write_text("unchanged member settings\n")
        original = {path.relative_to(self.home): path.read_bytes() for path in self.home.rglob("*") if path.is_file()}
        self.run_doctor(check_remote=True)
        after = {path.relative_to(self.home): path.read_bytes() for path in self.home.rglob("*") if path.is_file()}
        self.assertEqual(after, original)
        self.assertEqual(list(self.home.iterdir()), [profile])


if __name__ == "__main__":
    unittest.main()
