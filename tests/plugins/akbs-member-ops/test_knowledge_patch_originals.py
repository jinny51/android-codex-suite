from __future__ import annotations

import hashlib
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import urllib.error

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "plugins/akbs-member-ops/lib"))
from akbs_member_ops.http_client import HttpClientFailure  # noqa: E402
from akbs_member_ops.knowledge_search import api, cli, originals  # noqa: E402


CONTENT = b"diff --git a/file b/file\n"
CASE = "case-example"
ASSET = "asset-example"


def listing(**changes):
    payload = {
        "schema": originals.SCHEMA, "scope": originals.SCOPE, "case_id": CASE,
        "availability": "available", "reason_code": "",
        "patches": [{"asset_id": ASSET, "display_name": "原补丁.patch",
                     "sha256": hashlib.sha256(CONTENT).hexdigest(), "size_bytes": len(CONTENT),
                     "download_url": "http://untrusted.invalid/never-use-this"}],
    }
    payload.update(changes)
    return payload


def implementation_listing(*, historical=False):
    payload = listing(scope=originals.HISTORICAL_SCOPE if historical else originals.IMPLEMENTATION_SCOPE)
    payload["implementation_id"] = "implementation-example"
    item = payload["patches"][0]
    item.update({
        "implementation_id": payload["implementation_id"],
        "implementation_summary": "真实方案", "review_state": "migration_review_required" if historical else "reviewed",
        "authority": "historical_case_snapshot" if historical else "accepted_evidence_binding",
        "environments": [{"project": "TVI2343R", "platform": "rk", "android_version": "12", "validation_state": "review_required"}],
        "source": {"patch_package_id": "patch-package-example", "manifest_revision": 1,
                   "manifest_sha256": "a" * 64, "package_content_hash": "b" * 64,
                   "layer": "unknown" if historical else "platform"},
    })
    if not historical:
        item["binding_role"] = "implementation"
    return payload


def detail():
    return {
        "case_id": CASE, "title": "真实功能", "summary": "解决实际问题", "status": "active",
        "sections": [{"label": "功能边界", "kind": "list", "items": ["仅此功能"]}],
        "implementations": [{
            "implementation_id": "implementation-example", "case_id": CASE,
            "content_hash": "a" * 64, "status": "active", "review_state": "reviewed",
            "approach": "真实实现方案", "implementation_summary": "实施说明",
            "key_decisions": [{"decision": "关键选择"}], "code_anchors": [{"path": "services/Example.java"}],
            "risk_and_rollback": {"rollback": "回退本补丁"}, "applicability": [],
            "reuse_grade": "reference_only", "requires_revalidation": True,
        }],
    }


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class KnowledgePatchOriginalsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name) / "download.patch"
        self.base = mock.patch.object(originals, "member_api_base_url", return_value=("http://akbs.example", "test"))
        self.base.start()
        self.addCleanup(self.base.stop)
        self.headers = mock.patch.object(originals, "member_request_headers", return_value={"Accept": "application/json", "X-AKBS-User": "member1"})
        self.headers.start()
        self.addCleanup(self.headers.stop)

    def test_listing_case_bound_and_member_headers_only(self):
        with mock.patch.object(originals, "request_json", return_value=listing()) as request:
            result = originals.fetch_case_patches(CASE)
        sent = request.call_args.args[0]
        self.assertEqual(sent.full_url, "http://akbs.example/akbs/api/member/me/knowledge/case-example/patches")
        self.assertEqual(dict((k.lower(), v) for k, v in sent.header_items()), {"accept": "application/json", "x-akbs-user": "member1"})
        self.assertEqual(result["source"], "server_api")

    def test_download_verifies_bytes_ignores_remote_url_and_does_not_claim_reuse(self):
        with mock.patch.object(originals, "request_json", return_value=listing()), mock.patch.object(originals.urllib.request, "urlopen", return_value=Response(CONTENT)) as request:
            result = originals.download_case_patch(CASE, ASSET, self.output)
        self.assertEqual(self.output.read_bytes(), CONTENT)
        self.assertEqual(request.call_args.args[0].full_url, "http://akbs.example/akbs/api/member/me/knowledge/case-example/patches/asset-example")
        self.assertTrue(result["verified_original"])
        self.assertEqual(result["reuse_outcome"], "not_started")

    def test_wrong_case_or_contract_or_metadata_rejected(self):
        bad = [listing(case_id="another"), listing(schema="wrong"), listing(scope="wrong"), listing(scope={}), listing(scope=[]), listing(availability="unavailable"), listing(patches=[None])]
        for field, value in (("sha256", "bad"), ("size_bytes", True), ("size_bytes", -1), ("size_bytes", originals.MAX_PATCH_BYTES + 1), ("asset_id", "../another")):
            item = listing()
            item["patches"][0][field] = value
            bad.append(item)
        duplicate = listing()
        duplicate["patches"].append(dict(duplicate["patches"][0]))
        bad.append(duplicate)
        for payload in bad:
            with self.subTest(payload=payload), mock.patch.object(originals, "request_json", return_value=payload):
                with self.assertRaises(HttpClientFailure):
                    originals.fetch_case_patches(CASE)

    def test_unavailable_has_no_local_fallback_or_download(self):
        payload = listing(availability="unavailable", reason_code="legacy_case_originals_unavailable", patches=[])
        with mock.patch.object(originals, "request_json", return_value=payload), mock.patch.object(originals.urllib.request, "urlopen") as request:
            self.assertEqual(originals.fetch_case_patches(CASE)["availability"], "unavailable")
            with self.assertRaises(ValueError):
                originals.download_case_patch(CASE, ASSET, self.output)
        request.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_cross_case_asset_not_downloaded(self):
        with mock.patch.object(originals, "request_json", return_value=listing()), mock.patch.object(originals.urllib.request, "urlopen") as request:
            with self.assertRaises(ValueError):
                originals.download_case_patch(CASE, "asset-other", self.output)
        request.assert_not_called()

    def test_historical_scope_downloads_case_membership_handle_without_reuse_claim(self):
        handle = "patch-membership-21b57476-1bef-5ef0-96ed-91bed310ca35"
        payload = listing(scope=originals.HISTORICAL_SCOPE)
        payload["patches"][0]["asset_id"] = handle
        with mock.patch.object(originals, "request_json", return_value=payload), mock.patch.object(originals.urllib.request, "urlopen", return_value=Response(CONTENT)) as request:
            result = originals.download_case_patch(CASE, handle, self.output)
        self.assertEqual(self.output.read_bytes(), CONTENT)
        self.assertEqual(request.call_args.args[0].full_url, "http://akbs.example/akbs/api/member/me/knowledge/case-example/patches/" + handle)
        self.assertEqual(result["asset_id"], handle)
        self.assertTrue(result["verified_original"])
        self.assertEqual(result["reuse_outcome"], "not_started")

    def test_historical_scope_cannot_download_unlisted_membership(self):
        payload = listing(scope=originals.HISTORICAL_SCOPE)
        payload["patches"][0]["asset_id"] = "patch-membership-listed"
        with mock.patch.object(originals, "request_json", return_value=payload), mock.patch.object(originals.urllib.request, "urlopen") as request:
            with self.assertRaises(ValueError):
                originals.download_case_patch(CASE, "patch-membership-other", self.output)
        request.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_tamper_or_size_mismatch_never_written(self):
        for content in (b"x" * len(CONTENT), CONTENT + b"x", CONTENT[:-1]):
            with self.subTest(content=content), mock.patch.object(originals, "request_json", return_value=listing()), mock.patch.object(originals.urllib.request, "urlopen", return_value=Response(content)):
                with self.assertRaises(HttpClientFailure):
                    originals.download_case_patch(CASE, ASSET, self.output)
            self.assertFalse(self.output.exists())

    def test_existing_output_preserved_without_network(self):
        self.output.write_bytes(b"existing work")
        with mock.patch.object(originals, "request_json") as request:
            with self.assertRaises(ValueError):
                originals.download_case_patch(CASE, ASSET, self.output)
        request.assert_not_called()
        self.assertEqual(self.output.read_bytes(), b"existing work")

    def test_dangling_output_symlink_is_preserved_without_network(self):
        destination = self.output.parent / "never-create.patch"
        self.output.symlink_to(destination)
        with mock.patch.object(originals, "request_json") as request:
            with self.assertRaises(ValueError):
                originals.download_case_patch(CASE, ASSET, self.output)
        request.assert_not_called()
        self.assertTrue(self.output.is_symlink())
        self.assertFalse(destination.exists())

    def test_cli_preserves_raw_output_symlink_identity(self):
        destination = self.output.parent / "never-create.patch"
        self.output.symlink_to(destination)
        with mock.patch.object(cli, "download_case_patch", wraps=originals.download_case_patch), mock.patch.object(originals, "request_json") as request:
            with self.assertRaises(SystemExit) as failure:
                cli.main(["--case-patches", CASE, "--download-patch", ASSET, "--out", str(self.output)])
        self.assertIn("already exists", str(failure.exception))
        request.assert_not_called()
        self.assertTrue(self.output.is_symlink())
        self.assertFalse(destination.exists())

    def test_http_error_uses_sanitized_contract(self):
        error = urllib.error.HTTPError("http://akbs.example", 404, "secret", {}, io.BytesIO(b"private server path"))
        with mock.patch.object(originals, "request_json", return_value=listing()), mock.patch.object(originals.urllib.request, "urlopen", side_effect=error):
            with self.assertRaises(HttpClientFailure) as failure:
                originals.download_case_patch(CASE, ASSET, self.output)
        self.assertNotIn("private server path", str(failure.exception))
        self.assertFalse(self.output.exists())

    def test_cli_lists_and_downloads_without_search_usage_or_fallback(self):
        with mock.patch.object(cli, "fetch_case_patches", return_value=listing()), mock.patch.object(cli, "record_search_usage") as usage, mock.patch.object(cli, "find_root") as fallback, mock.patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(cli.main(["--case-patches", CASE, "--json"]), 0)
        self.assertEqual(json.loads(output.getvalue())["case_id"], CASE)
        usage.assert_not_called()
        fallback.assert_not_called()
        with mock.patch.object(cli, "download_case_patch", return_value={"output": str(self.output)}), mock.patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(cli.main(["--case-patches", CASE, "--download-patch", ASSET, "--out", str(self.output)]), 0)
        self.assertIn("不代表复用成功", output.getvalue())

    def test_cli_rejects_conflicting_actions(self):
        for options in (["--download-patch", ASSET], ["--case-patches", CASE, "--out", "x"], ["--case-patches", CASE, "--source", "local"], ["--case-patches", CASE, "a query"], ["--case-patches", CASE, "--merge-confirmation", "list"]):
            with self.subTest(options=options), mock.patch("sys.stderr", new_callable=io.StringIO):
                with self.assertRaises(SystemExit) as failure:
                    cli.main(options)
                self.assertEqual(failure.exception.code, 2)

    def test_selected_implementation_and_historical_pin_keep_exact_selector(self):
        for historical in (False, True):
            with self.subTest(historical=historical):
                payload = implementation_listing(historical=historical)
                output = self.output.with_name("historical.patch" if historical else "binding.patch")
                with mock.patch.object(originals, "request_json", return_value=payload) as listing_request, mock.patch.object(originals.urllib.request, "urlopen", return_value=Response(CONTENT)) as download:
                    result = originals.download_case_patch(CASE, ASSET, output, implementation_id="implementation-example")
                self.assertTrue(listing_request.call_args.args[0].full_url.endswith("?implementation_id=implementation-example"))
                self.assertTrue(download.call_args.args[0].full_url.endswith("?implementation_id=implementation-example"))
                self.assertEqual(result["implementation_id"], "implementation-example")
                self.assertEqual(result["reuse_outcome"], "not_started")
                self.assertEqual(output.read_bytes(), CONTENT)
                self.assertEqual("binding_role" in result, not historical)

    def test_bad_implementation_metadata_does_not_retry_case_or_write(self):
        mutations = [
            ("implementation_id", "implementation-other"), ("authority", []),
            ("binding_role", "invalid"), ("environments", [{}]),
            ("source", {"layer": "platform"}), ("review_state", {}),
        ]
        for field, value in mutations:
            payload = implementation_listing()
            payload["patches"][0][field] = value
            with self.subTest(field=field), mock.patch.object(originals, "request_json", return_value=payload) as request, mock.patch.object(originals.urllib.request, "urlopen") as download:
                with self.assertRaises(HttpClientFailure):
                    originals.download_case_patch(CASE, ASSET, self.output, implementation_id="implementation-example")
            self.assertEqual(request.call_count, 1)
            download.assert_not_called()
            self.assertFalse(self.output.exists())
        payload = implementation_listing(historical=True)
        payload["patches"][0]["binding_role"] = "implementation"
        with mock.patch.object(originals, "request_json", return_value=payload):
            with self.assertRaises(HttpClientFailure):
                originals.fetch_case_patches(CASE, implementation_id="implementation-example")

    def test_selected_listing_owner_and_scope_mismatch_never_fall_back(self):
        bad = []
        for changes in (
            {"case_id": "case-other"}, {"implementation_id": "implementation-other"},
            {"implementation_id": None}, {"scope": originals.SCOPE},
        ):
            payload = implementation_listing()
            payload.update(changes)
            bad.append(payload)
        payload = implementation_listing()
        del payload["implementation_id"]
        bad.append(payload)
        payload = implementation_listing()
        payload["patches"][0]["implementation_id"] = "implementation-other"
        bad.append(payload)
        payload = implementation_listing()
        payload["scope"] = originals.HISTORICAL_SCOPE
        bad.append(payload)
        for payload in bad:
            with self.subTest(payload=payload), mock.patch.object(originals, "request_json", return_value=payload) as request, mock.patch.object(originals.urllib.request, "urlopen") as download:
                with self.assertRaises(HttpClientFailure):
                    originals.download_case_patch(CASE, ASSET, self.output, implementation_id="implementation-example")
            self.assertEqual(request.call_count, 1)
            self.assertIn("?implementation_id=implementation-example", request.call_args.args[0].full_url)
            download.assert_not_called()
            self.assertFalse(self.output.exists())

    def test_implementation_scope_requires_explicit_selector(self):
        for patches in (implementation_listing()["patches"], []):
            payload = implementation_listing()
            payload.update(patches=patches, availability="available" if patches else "unavailable")
            with self.subTest(patches=patches), mock.patch.object(originals, "request_json", return_value=payload) as request, mock.patch.object(originals.urllib.request, "urlopen") as download:
                with self.assertRaises(HttpClientFailure):
                    originals.download_case_patch(CASE, ASSET, self.output)
            self.assertEqual(request.call_count, 1)
            download.assert_not_called()
            self.assertFalse(self.output.exists())

    def test_invalid_explicit_selector_is_rejected_before_network(self):
        for selector in ("", "../implementation-other", "implementation?another=1", False, []):
            with self.subTest(selector=selector), mock.patch.object(originals, "request_json") as request, mock.patch.object(originals.urllib.request, "urlopen") as download:
                with self.assertRaises(ValueError):
                    originals.download_case_patch(CASE, ASSET, self.output, implementation_id=selector)
            request.assert_not_called()
            download.assert_not_called()
            self.assertFalse(self.output.exists())

    def test_selected_unavailable_and_unlisted_handle_do_not_retry_case(self):
        for historical in (False, True):
            for unavailable in (False, True):
                payload = implementation_listing(historical=historical)
                if unavailable:
                    payload.update(availability="unavailable", patches=[], reason_code="implementation_originals_unavailable")
                with self.subTest(historical=historical, unavailable=unavailable), mock.patch.object(originals, "request_json", return_value=payload) as request, mock.patch.object(originals.urllib.request, "urlopen") as download:
                    with self.assertRaises(ValueError):
                        originals.download_case_patch(CASE, "asset-other", self.output, implementation_id="implementation-example")
                self.assertEqual(request.call_count, 1)
                self.assertTrue(request.call_args.args[0].full_url.endswith("?implementation_id=implementation-example"))
                download.assert_not_called()
                self.assertFalse(self.output.exists())

    def test_mixed_pending_authorities_preserve_both_handles_and_exact_environments(self):
        payload = implementation_listing(historical=True)
        payload["scope"] = originals.IMPLEMENTATION_SCOPE
        historical = payload["patches"][0]
        historical["asset_id"] = "patch-membership-initial"
        attached = implementation_listing()["patches"][0]
        attached.update(asset_id="binding-attached", review_state="migration_review_required", binding_role="verification_only")
        attached["environments"] = [
            {"project": "OtherProject", "platform": "mtk", "android_version": "14", "validation_state": "unresolved"},
        ]
        payload["patches"].append(attached)
        before = copy.deepcopy(payload)
        with mock.patch.object(originals, "request_json", return_value=payload):
            listed = originals.fetch_case_patches(CASE, implementation_id="implementation-example")
        # Equal bytes are not a license to collapse distinct authorities or environments.
        self.assertEqual([item["asset_id"] for item in listed["patches"]], ["patch-membership-initial", "binding-attached"])
        for item in (historical, attached):
            output = self.output.with_name(item["asset_id"] + ".patch")
            with mock.patch.object(originals, "request_json", return_value=payload) as request, mock.patch.object(originals.urllib.request, "urlopen", return_value=Response(CONTENT)) as download:
                receipt = originals.download_case_patch(CASE, item["asset_id"], output, implementation_id="implementation-example")
            self.assertEqual(receipt["asset_id"], item["asset_id"])
            self.assertEqual(receipt["authority"], item["authority"])
            self.assertEqual(receipt["environments"], item["environments"])
            self.assertEqual(receipt["original_source"], item["source"])
            self.assertEqual(receipt["review_state"], "migration_review_required")
            self.assertEqual(receipt["reuse_outcome"], "not_started")
            self.assertNotIn("reuse_grade", receipt)
            self.assertEqual("binding_role" in receipt, item is attached)
            self.assertEqual(output.read_bytes(), CONTENT)
            self.assertEqual(request.call_count, 1)
            self.assertEqual(download.call_count, 1)
            self.assertEqual(download.call_args.args[0].full_url, "http://akbs.example/akbs/api/member/me/knowledge/" + CASE + "/patches/" + item["asset_id"] + "?implementation_id=implementation-example")
            self.assertEqual(dict((key.lower(), value) for key, value in download.call_args.args[0].header_items()), {"accept": "application/octet-stream", "x-akbs-user": "member1"})
        self.assertEqual(payload, before)

    def test_source_identity_malformations_never_write_or_retry(self):
        mutations = (
            ("patch_package_id", "../private/package"), ("manifest_revision", True),
            ("manifest_revision", 0), ("manifest_sha256", "bad"),
            ("package_content_hash", "bad"), ("layer", "unknown"),
            ("layer", "not-a-layer"), ("source_path", "/private/source/path"),
        )
        for field, value in mutations:
            payload = implementation_listing()
            payload["patches"][0]["source"][field] = value
            with self.subTest(field=field, value=value), mock.patch.object(originals, "request_json", return_value=payload) as request, mock.patch.object(originals.urllib.request, "urlopen") as download:
                with self.assertRaises(HttpClientFailure):
                    originals.download_case_patch(CASE, ASSET, self.output, implementation_id="implementation-example")
            self.assertEqual(request.call_count, 1)
            download.assert_not_called()
            self.assertFalse(self.output.exists())

    def test_environment_and_review_malformations_do_not_claim_validation(self):
        valid = implementation_listing()["patches"][0]["environments"][0]
        bad_environments = (
            None, {}, [], [None], [{}], [dict(valid, validation_state="passed")],
            [dict(valid, project=False)], [dict(valid, tuple_id="private-id")],
            [valid, dict(valid, validation_state="validated")],
        )
        bad = [("environments", value) for value in bad_environments]
        bad += [("review_state", value) for value in ("", "validated", "unknown", [], True)]
        for field, value in bad:
            payload = implementation_listing()
            payload["patches"][0][field] = value
            with self.subTest(field=field, value=value), mock.patch.object(originals, "request_json", return_value=payload) as request, mock.patch.object(originals.urllib.request, "urlopen") as download:
                with self.assertRaises(HttpClientFailure):
                    originals.download_case_patch(CASE, ASSET, self.output, implementation_id="implementation-example")
            self.assertEqual(request.call_count, 1)
            download.assert_not_called()
            self.assertFalse(self.output.exists())
        # A historical locator may have no proven environment; do not fabricate one.
        payload = implementation_listing(historical=True)
        payload["patches"][0]["environments"] = []
        with mock.patch.object(originals, "request_json", return_value=payload):
            result = originals.fetch_case_patches(CASE, implementation_id="implementation-example")
        self.assertEqual(result["patches"][0]["environments"], [])

    def test_selected_download_404_never_retries_case_or_writes(self):
        error = urllib.error.HTTPError("http://akbs.example", 404, "missing", {}, io.BytesIO(b"private source path"))
        with mock.patch.object(originals, "request_json", return_value=implementation_listing()) as listing_request, mock.patch.object(originals.urllib.request, "urlopen", side_effect=error) as download:
            with self.assertRaises(HttpClientFailure) as failure:
                originals.download_case_patch(CASE, ASSET, self.output, implementation_id="implementation-example")
        self.assertEqual(listing_request.call_count, 1)
        self.assertEqual(download.call_count, 1)
        self.assertTrue(download.call_args.args[0].full_url.endswith("?implementation_id=implementation-example"))
        self.assertNotIn("private source path", str(failure.exception))
        self.assertFalse(self.output.exists())

    def test_selected_download_tamper_does_not_write_or_promote_pending_original(self):
        payload = implementation_listing()
        payload["patches"][0]["review_state"] = "migration_review_required"
        for raw in (CONTENT[:-1], CONTENT + b"x", b"x" * len(CONTENT)):
            with self.subTest(raw=raw), mock.patch.object(originals, "request_json", return_value=payload) as listing_request, mock.patch.object(originals.urllib.request, "urlopen", return_value=Response(raw)) as download:
                with self.assertRaises(HttpClientFailure):
                    originals.download_case_patch(CASE, ASSET, self.output, implementation_id="implementation-example")
            self.assertEqual(listing_request.call_count, 1)
            self.assertEqual(download.call_count, 1)
            self.assertFalse(self.output.exists())

    def test_selected_colon_identifier_is_encoded_in_both_requests(self):
        selector = "implementation:initial-history"
        payload = implementation_listing(historical=True)
        payload["implementation_id"] = selector
        payload["patches"][0]["implementation_id"] = selector
        with mock.patch.object(originals, "request_json", return_value=payload) as request, mock.patch.object(originals.urllib.request, "urlopen", return_value=Response(CONTENT)) as download:
            result = originals.download_case_patch(CASE, ASSET, self.output, implementation_id=selector)
        for sent in (request.call_args.args[0], download.call_args.args[0]):
            self.assertTrue(sent.full_url.endswith("?implementation_id=implementation%3Ainitial-history"))
        self.assertEqual(result["implementation_id"], selector)
        self.assertEqual(result["reuse_outcome"], "not_started")

    def test_detail_is_exact_existing_get_and_does_not_replace_search_grade(self):
        payload = detail()
        with mock.patch.object(api, "member_api_base_url", return_value=("http://akbs.example", "test")), mock.patch.object(api, "member_request_headers", return_value={"X-AKBS-User": "member1"}), mock.patch.object(api, "request_json", return_value=payload) as request:
            result = api.fetch_case_detail(CASE, implementation_id="implementation-example")
        self.assertEqual(request.call_args.args[0].full_url, "http://akbs.example/akbs/api/knowledge/" + CASE)
        self.assertEqual(result["implementations"][0]["approach"], "真实实现方案")
        self.assertEqual(result["implementations"][0]["reuse_grade"], "reference_only")
        self.assertEqual(payload, detail())
        with mock.patch.object(cli, "fetch_case_detail", return_value=result), mock.patch.object(cli, "record_search_usage") as usage, mock.patch.object(cli, "find_root") as fallback, mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            self.assertEqual(cli.main(["--case-detail", CASE, "--implementation-id", "implementation-example"]), 0)
        self.assertIn("真实实现方案", stdout.getvalue())
        self.assertIn("回退本补丁", stdout.getvalue())
        self.assertIn("不能替代搜索时", stdout.getvalue())
        usage.assert_not_called()
        fallback.assert_not_called()

    def test_detail_rejects_wrong_case_impl_or_incomplete_body(self):
        bad = []
        for field, value in (("case_id", "other"), ("sections", []), ("implementations", {})):
            payload = detail()
            payload[field] = value
            if field == "sections":
                payload[field] = [{"label": "边界", "kind": "list", "items": [False]}]
            bad.append(payload)
        for field, value in (("case_id", "other"), ("content_hash", False), ("key_decisions", [None]), ("risk_and_rollback", []), ("implementation_id", ["invalid"])):
            payload = detail()
            payload["implementations"][0][field] = value
            bad.append(payload)
        for payload in bad:
            with self.subTest(payload=payload), mock.patch.object(api, "member_api_base_url", return_value=("http://akbs.example", "test")), mock.patch.object(api, "member_request_headers", return_value={}), mock.patch.object(api, "request_json", return_value=payload):
                with self.assertRaises(HttpClientFailure):
                    api.fetch_case_detail(CASE)
        with mock.patch.object(api, "member_api_base_url", return_value=("http://akbs.example", "test")), mock.patch.object(api, "member_request_headers", return_value={}), mock.patch.object(api, "request_json", return_value=detail()):
            with self.assertRaises(ValueError):
                api.fetch_case_detail(CASE, implementation_id="implementation-other")

    def test_detail_preserves_string_object_and_mixed_solution_lists(self):
        long_note = "完整细节" * 60 + "\n正文尾部"
        typed_anchor = {"type": "source_file", "value": "services/Example.java"}
        legacy_anchor = {"type": "legacy_unclassified", "value": "原检索锚点", "review_state": "pending_review", "source": "schema041.search_anchors"}
        shapes = (
            ("strings", [long_note, "后续决策"], [f"锚点{index:02d}\n{long_note}" for index in (2, 0, 1, *range(3, 30))]),
            ("objects", [{"decision": "原关键选择", "reason": long_note}], [typed_anchor, legacy_anchor]),
            ("mixed", [long_note, {"decision": "原关键选择", "reason": "原理由"}], ["原字符串锚点", typed_anchor, legacy_anchor, long_note]),
        )
        for shape, decisions, anchors in shapes:
            payload = detail()
            payload["implementations"][0].update({
                "key_decisions": copy.deepcopy(decisions), "code_anchors": copy.deepcopy(anchors),
                "review_state": "migration_review_required",
                "applicability": [{"implementation_id": "implementation-example", "project": "TVI2343R", "platform": "rk", "android_version": "12", "source_lineage_ref": "", "constraints": [], "validation_state": "unresolved", "content_hash": "b" * 64}],
            })
            original = copy.deepcopy(payload)
            with self.subTest(shape=shape), mock.patch.object(api, "member_api_base_url", return_value=("http://akbs.example", "test")), mock.patch.object(api, "member_request_headers", return_value={"X-AKBS-User": "member1"}), mock.patch.object(api, "request_json", return_value=payload) as request:
                result = api.fetch_case_detail(CASE, implementation_id="implementation-example")
            self.assertEqual(request.call_count, 1)
            self.assertEqual(request.call_args.args[0].full_url, "http://akbs.example/akbs/api/knowledge/" + CASE)
            self.assertEqual(result["implementations"], original["implementations"])
            self.assertEqual(result["selected_implementation_id"], "implementation-example")
            self.assertEqual(result["implementations"][0]["reuse_grade"], "reference_only")
            self.assertIs(result["implementations"][0]["requires_revalidation"], True)
            self.assertEqual(payload, original)

    def test_detail_rejects_bad_solution_list_elements(self):
        for field in ("key_decisions", "code_anchors"):
            for item in (None, False, True, 0, 1, 1.5, [], ["nested"]):
                payload = detail()
                payload["implementations"][0][field] = ["合法字符串", {"value": "合法对象"}, item]
                original = copy.deepcopy(payload)
                with self.subTest(field=field, item=item), mock.patch.object(api, "member_api_base_url", return_value=("http://akbs.example", "test")), mock.patch.object(api, "member_request_headers", return_value={}), mock.patch.object(api, "request_json", return_value=payload) as request:
                    with self.assertRaises(HttpClientFailure):
                        api.fetch_case_detail(CASE)
                self.assertEqual(request.call_count, 1)
                self.assertEqual(payload, original)

    def test_detail_rejects_non_array_solution_fields(self):
        for field in ("key_decisions", "code_anchors"):
            for value in (None, False, 0, "not-an-array", {}):
                payload = detail()
                payload["implementations"][0][field] = value
                with self.subTest(field=field, value=value), mock.patch.object(api, "member_api_base_url", return_value=("http://akbs.example", "test")), mock.patch.object(api, "member_request_headers", return_value={}), mock.patch.object(api, "request_json", return_value=payload) as request:
                    with self.assertRaises(HttpClientFailure):
                        api.fetch_case_detail(CASE)
                self.assertEqual(request.call_count, 1)

    def test_detail_keeps_applicability_object_only(self):
        bad = [None, False, 0, "not-an-array", {}]
        bad += [[item] for item in ("not-an-object", None, False, 0, 1.5, [], ["nested"])]
        for value in bad:
            payload = detail()
            payload["implementations"][0]["applicability"] = value
            with self.subTest(value=value), mock.patch.object(api, "member_api_base_url", return_value=("http://akbs.example", "test")), mock.patch.object(api, "member_request_headers", return_value={}), mock.patch.object(api, "request_json", return_value=payload) as request:
                with self.assertRaises(HttpClientFailure):
                    api.fetch_case_detail(CASE)
            self.assertEqual(request.call_count, 1)


if __name__ == "__main__":
    unittest.main()
