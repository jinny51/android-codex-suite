from __future__ import annotations

import contextlib
import gzip
import hashlib
import importlib.util
import io
import json
import os
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
PLUGIN = ROOT / "plugins" / "akbs-member-ops"
LIB = PLUGIN / "lib"
SCRIPT = PLUGIN / "skills" / "akbs-patch-submit" / "scripts" / "akbs_patch_submit.py"
sys_path = __import__("sys").path
sys_path.insert(0, str(LIB))
sys_path.insert(0, str(PLUGIN / "internal" / "incoming-v1" / "scripts"))

from akbs_member_ops.incoming_v1.contract import public_contract, success_reason_codes  # noqa: E402
from akbs_member_ops.incoming_v2 import cli as incoming_v2_cli  # noqa: E402
from akbs_member_ops.incoming_v2 import submission  # noqa: E402
from akbs_member_ops.incoming_v2.validation import (  # noqa: E402
    AndroidChangeV2Error,
    check_package,
    prepare_package,
    read_package,
)


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def build_package(root: Path, *, platform: str = "rk", alias: str = "member1") -> Path:
    readme = b"# Example Android change\n"
    patch = b"diff --git a/A.java b/A.java\n--- a/A.java\n+++ b/A.java\n"
    evidence = _json_bytes({"kind": "verification_result", "result": "PASS"})
    payloads = {
        "README.md": readme,
        "patches/change.patch": patch,
        "evidence/result.json": evidence,
    }
    for relative, raw in payloads.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)

    def descriptor(relative: str) -> dict[str, object]:
        raw = payloads[relative]
        return {
            "path": relative,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "size_bytes": len(raw),
        }

    manifest = {
        "schema": "akbs-android-change-package-v2",
        "schema_version": "2",
        "package_kind": "android_change",
        "package_status": "validated",
        "identity": {
            "member_alias": alias,
            "run_id": "20260910-120000-example",
            "created_at": "2026-09-10T12:00:00+08:00",
        },
        "subject": {
            "title": "Example Android change",
            "summary": "Example Android change",
            "primary_component_id": "app",
            "target": {
                "project": "TVE8402M",
                "platform": platform,
                "android_version": "14",
            },
        },
        "workflow": {
            "contract": "current_codex_skill",
            "implementation_origins": ["codex"],
            "capture_tool": {"id": "android-patch-capture", "version": "2"},
        },
        "components": [
            {
                "id": "app",
                "layer": "application",
                "type": "system_app",
                "partition": "system_ext",
                "ownership": "product",
            }
        ],
        "sources": [
            {
                "id": "source-1",
                "kind": "git",
                "repo_path": "packages/apps/Settings",
                "base_revision": "1" * 40,
                "head_revision": "1" * 40,
            }
        ],
        "readme": descriptor("README.md"),
        "patches": [
            {
                "id": "patch-001",
                "component_ids": ["app"],
                "source_id": "source-1",
                **descriptor("patches/change.patch"),
                "format": "git_diff",
            }
        ],
        "evidence": [
            {
                "id": "evidence-1",
                "kind": "verification_result",
                "component_ids": ["app"],
                **descriptor("evidence/result.json"),
                "scope": "feature",
                "result": "PASS",
            }
        ],
    }
    (root / "manifest.json").write_bytes(_json_bytes(manifest))
    return root


@contextlib.contextmanager
def submission_environment(workspace: Path, *, alias: str = "member1"):
    codex_home = workspace / "codex-home"
    codex_home.mkdir()
    (codex_home / "akbs-member-ops.toml").write_text(
        'default_profile = "selected"\n[profiles.selected]\n'
        f'member_alias = "{alias}"\nmember_name = "Test Member"\n'
        'allowed_modes = "patch,daily,weekly"\n',
        encoding="utf-8",
    )
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("CODEX_REPORT_", "CODEX_WORK_REPORT_"))
    }
    environment.update(
        CODEX_HOME=str(codex_home),
        CODEX_REPORT_AKBS_ENDPOINT_SUBMISSION_API_BASE_URL="http://akbs.invalid/akbs/api",
    )
    with mock.patch.dict(os.environ, environment, clear=True):
        yield


def success_response(source_key: str) -> dict[str, object]:
    return {
        "package": {
            "package_key": source_key,
            "patch_package_id": "patch-test-0001",
        },
        "agent_context": {
            "incoming_contract": {
                "version": str(public_contract()["schema_version"]),
                "authority": "akbs-server",
                "reason_codes": list(success_reason_codes()),
            }
        },
    }


class AndroidChangeV2Test(unittest.TestCase):
    def test_read_check_and_byte_preserving_prepare(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            source = build_package(workspace / "source")
            read = read_package(source)
            self.assertEqual(read["target"], {
                "project": "TVE8402M", "platform": "rk", "android_version": "14"
            })
            self.assertNotIn("platform_token", json.loads((source / "manifest.json").read_text()))
            checked = check_package(source)
            self.assertTrue(checked["coherence"]["reference_integrity_valid"])
            before = {
                path.relative_to(source).as_posix(): path.read_bytes()
                for path in source.rglob("*") if path.is_file()
            }
            prepared = prepare_package(source, pending_root=workspace / "pending")
            destination = Path(prepared["package"])
            after = {
                path.relative_to(destination).as_posix(): path.read_bytes()
                for path in destination.rglob("*") if path.is_file()
            }
            self.assertEqual(after, before)
            self.assertTrue(prepared["bytes_preserved"])

    def test_formal_platform_rejects_versioned_input_token(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = build_package(Path(temporary) / "source", platform="mtk16")
            with self.assertRaisesRegex(AndroidChangeV2Error, "platform"):
                check_package(source)

    def test_check_rejects_extra_or_changed_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = build_package(Path(temporary) / "source")
            (source / "unexpected.txt").write_text("extra", encoding="utf-8")
            with self.assertRaisesRegex(AndroidChangeV2Error, "inventory"):
                check_package(source)
        with tempfile.TemporaryDirectory() as temporary:
            source = build_package(Path(temporary) / "source")
            (source / "patches/change.patch").write_text("changed", encoding="utf-8")
            with self.assertRaisesRegex(AndroidChangeV2Error, "integrity"):
                check_package(source)

    def test_check_rejects_evidence_kind_that_differs_from_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = build_package(Path(temporary) / "source")
            evidence_path = source / "evidence/result.json"
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            evidence["kind"] = "search_before_change"
            evidence_path.write_bytes(_json_bytes(evidence))
            manifest_path = source / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            raw = evidence_path.read_bytes()
            manifest["evidence"][0]["sha256"] = hashlib.sha256(raw).hexdigest()
            manifest["evidence"][0]["size_bytes"] = len(raw)
            manifest_path.write_bytes(_json_bytes(manifest))
            with self.assertRaisesRegex(AndroidChangeV2Error, "evidence kind differs"):
                check_package(source)

    def test_check_rejects_symlink_package_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            source = build_package(workspace / "source")
            link = workspace / "linked-package"
            link.symlink_to(source, target_is_directory=True)
            with self.assertRaisesRegex(AndroidChangeV2Error, "symbolic link"):
                check_package(link)

    def test_submit_uses_common_patch_endpoint_and_exact_archive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            source = build_package(workspace / "source")
            checked = check_package(source)
            observed: dict[str, object] = {}

            def request_json(request, **kwargs):
                observed["request"] = request
                observed["kwargs"] = kwargs
                return success_response(checked["source_package_key"]), {"request_id": "req-test"}

            with submission_environment(workspace), mock.patch.object(
                submission, "request_json_with_metadata", side_effect=request_json
            ):
                result = submission.submit_package(source)
            request = observed["request"]
            self.assertEqual(request.full_url, "http://akbs.invalid/akbs/api/member/me/uploads/patch")
            self.assertEqual(request.method, "POST")
            self.assertEqual(request.get_header("X-akbs-user"), "member1")
            self.assertEqual(
                request.get_header("Idempotency-key"),
                "android-change-v2:" + checked["archive_inventory_sha256"],
            )
            with gzip.GzipFile(fileobj=io.BytesIO(request.data), mode="rb") as compressed:
                with tarfile.open(fileobj=compressed, mode="r:") as archive:
                    self.assertEqual(
                        sorted(archive.getnames()),
                        ["README.md", "evidence/result.json", "manifest.json", "patches/change.patch"],
                    )
            self.assertEqual(result["patch_package_id"], "patch-test-0001")
            self.assertNotIn("qualification", result)
            self.assertNotIn("server_qualified", result)

    def test_submit_rejects_member_mismatch_without_network(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            source = build_package(workspace / "source", alias="member2")
            with submission_environment(workspace), mock.patch.object(
                submission, "request_json_with_metadata"
            ) as request:
                with self.assertRaisesRegex(submission.SubmissionError, "member_alias"):
                    submission.submit_package(source)
            request.assert_not_called()

    def test_cli_surface_has_no_capture_adapter(self) -> None:
        parser = incoming_v2_cli.build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["adapt-capture", "package"])
        for action in ("read", "check", "prepare", "submit"):
            parsed = parser.parse_args([action, "package"])
            self.assertEqual(parsed.action, action)

    def test_legacy_v1_arguments_still_route_to_v1(self) -> None:
        spec = importlib.util.spec_from_file_location("akbs_patch_submit_entry", SCRIPT)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with mock.patch.object(module, "route_arguments", return_value=["routed"]), mock.patch.object(
            module, "incoming_main", return_value=17
        ) as incoming_main:
            self.assertEqual(module.main(["--source", "legacy-package"]), 17)
        incoming_main.assert_called_once_with(["routed"])


if __name__ == "__main__":
    unittest.main()
