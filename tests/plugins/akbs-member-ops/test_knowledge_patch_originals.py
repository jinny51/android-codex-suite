from __future__ import annotations

import hashlib
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
from akbs_member_ops.knowledge_search import cli, originals  # noqa: E402


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
        bad = [listing(case_id="another"), listing(schema="wrong"), listing(scope="wrong"), listing(availability="unavailable"), listing(patches=[None])]
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


if __name__ == "__main__":
    unittest.main()
