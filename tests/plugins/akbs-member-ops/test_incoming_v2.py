from __future__ import annotations

import copy
import contextlib
import hashlib
import http.server
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tarfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
PLUGIN = ROOT / "plugins" / "akbs-member-ops"
PLUGIN_VERSION = json.loads((PLUGIN / ".codex-plugin" / "plugin.json").read_text())["version"]
LIB = PLUGIN / "lib"
SCRIPT = PLUGIN / "skills" / "akbs-patch-submit" / "scripts" / "akbs_patch_submit.py"
FIXTURES = ROOT / "contracts" / "incoming" / "v2" / "fixtures"
sys.path.insert(0, str(LIB))
sys.path.insert(0, str(PLUGIN / "internal" / "incoming-v1" / "scripts"))

from akbs_member_ops.incoming_v2.validation import (  # noqa: E402
    AndroidChangeV2Error,
    check_package,
    prepare_package,
    qualification_input_sha256,
    read_package,
)
from akbs_member_ops.incoming_v2 import validation as incoming_v2_validation  # noqa: E402
from akbs_member_ops.incoming_v2 import submission as incoming_v2_submission  # noqa: E402
from akbs_member_ops.incoming_v2 import cli as incoming_v2_cli  # noqa: E402
from akbs_member_ops.http_client import HttpClientFailure  # noqa: E402
from akbs_intake import version_gate  # noqa: E402


def write_json(path: Path, value: object) -> bytes:
    raw = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return raw


def build_package(root: Path) -> Path:
    package = json.loads((FIXTURES / "package.application.valid.json").read_text(encoding="utf-8"))
    outputs = json.loads(
        (FIXTURES / "client-adapter-outputs.application.valid.json").read_text(encoding="utf-8")
    )
    patch_bytes = b"diff --git a/A.java b/A.java\n"
    evidence_bytes = b'{"result":"PASS"}\n'
    (root / "patches").mkdir(parents=True)
    (root / "evidence").mkdir(parents=True)
    (root / "patches" / "change.patch").write_bytes(patch_bytes)
    (root / "evidence" / "result.json").write_bytes(evidence_bytes)
    rows = {row["id"]: row for row in package["files"]}
    rows["patch-1"].update(sha256=hashlib.sha256(patch_bytes).hexdigest(), size_bytes=len(patch_bytes))
    evidence_sha = hashlib.sha256(evidence_bytes).hexdigest()
    rows["evidence-1-file"].update(sha256=evidence_sha, size_bytes=len(evidence_bytes))
    profile_bytes = (PLUGIN / "contracts" / "incoming" / "v2" / "component-evidence-profiles.json").read_bytes()
    profile_sha = hashlib.sha256(profile_bytes).hexdigest()
    package["qualification"]["profile_artifact_sha256"] = profile_sha
    outputs["profile_artifact_sha256"] = profile_sha
    for component in outputs["components"]:
        for output in component["outputs"]:
            output["source_evidence_sha256"] = evidence_sha
    outputs["qualification_input_sha256"] = qualification_input_sha256(package)
    output_bytes = write_json(root / "metadata" / "client-adapter-outputs.json", outputs)
    rows["qualification-client-output"].update(
        sha256=hashlib.sha256(output_bytes).hexdigest(),
        size_bytes=len(output_bytes),
    )
    write_json(root / "manifest.json", package)
    return root


@contextlib.contextmanager
def submission_environment(workspace: Path, *, alias: str = "member1", modes: str = "patch,daily,weekly"):
    codex_home = workspace / "codex-home"
    codex_home.mkdir(exist_ok=True)
    (codex_home / "akbs-member-ops.toml").write_text(
        'default_profile = "selected"\n[profiles.selected]\n'
        f'member_alias = "{alias}"\nmember_name = "Test Member"\nallowed_modes = "{modes}"\n',
        encoding="utf-8",
    )
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("CODEX_REPORT_", "CODEX_WORK_REPORT_"))
    }
    environment.update(
        CODEX_HOME=str(codex_home),
        CODEX_REPORT_AKBS_ENDPOINT_SUBMISSION_API_BASE_URL="http://akbs.invalid/akbs/api",
    )
    with mock.patch.dict(os.environ, environment, clear=True):
        yield


def upload_receipt(source: Path) -> dict[str, object]:
    checked = check_package(source)
    identity = json.loads((source / "manifest.json").read_text())["identity"]
    key = "android-change-v2:" + checked["archive_inventory_sha256"]
    package = {
        "package_key": checked["source_package_key"],
        "patch_package_id": "patch-test-0001", "revision": 1,
        "review_id": "review-test-0001", "status": "received",
    }
    operation = {
        "write_operation_id": "upload-test-0001", "grant_id": "",
        "idempotency_key_sha256": hashlib.sha256(key.encode()).hexdigest(),
        "manifest_sha256": checked["manifest_sha256"],
        "directory_payload_sha256": checked["archive_inventory_sha256"],
        "contract_pin_sha256": "c" * 64, "runtime_generation_sha256": "d" * 64,
        "accepted_at": "2026-09-07T00:00:00Z",
    }
    qualification = {
        "schema": "akbs-server-qualification-decision-v1",
        "authority": "server_authoritative", "authority_scope": "incoming_contract_qualification",
        "decision": "accept", "qualification_id": "qualification-test-0001",
        "patch_package_id": package["patch_package_id"], "revision": package["revision"],
        "source_package_key": checked["source_package_key"], "authenticated_actor": identity["member_alias"],
        "manifest_sha256": checked["manifest_sha256"],
        "directory_payload_sha256": checked["archive_inventory_sha256"],
        "contract_pin_sha256": operation["contract_pin_sha256"],
        "runtime_generation_sha256": operation["runtime_generation_sha256"],
        "qualified_at": operation["accepted_at"],
        "qualification_input_sha256": checked["coherence"]["qualification_input_sha256"],
    }
    receipt = {
        "schema": incoming_v2_submission.RECEIPT_SCHEMA,
        "accepted": True, "upload_type": "patch", "server_qualified": True,
        "package": package, "operation": operation, "qualification": qualification,
        "seal": {
            "seal_id": "seal-test-0001", "summary_algorithm": "sha256",
            "component_count": 1, "component_set_sha256": "e" * 64,
            "qualification_claim_count": 11, "qualification_claim_set_sha256": "f" * 64,
            "sealed_at": operation["accepted_at"],
        },
        "asset_set_sha256": "a" * 64,
    }
    receipt["receipt_sha256"] = incoming_v2_validation.canonical_json_sha256(receipt)
    return receipt


def receipt_response(receipt: dict[str, object]) -> io.BytesIO:
    response = io.BytesIO(json.dumps(receipt).encode("utf-8"))
    response.headers = {"X-Request-ID": "req_" + "a" * 32}
    return response


class AndroidChangeV2Test(unittest.TestCase):
    def test_bundled_v2_contracts_are_exact_copies_of_root_contracts(self) -> None:
        for name in (
            "akbs-android-change-package.schema.json",
            "client-adapter-outputs.schema.json",
            "component-evidence-profiles.json",
        ):
            self.assertEqual(
                (PLUGIN / "contracts" / "incoming" / "v2" / name).read_bytes(),
                (ROOT / "contracts" / "incoming" / "v2" / name).read_bytes(),
                name,
            )

    def test_read_check_and_byte_preserving_prepare(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            source = build_package(workspace / "source")
            read = read_package(source)
            self.assertEqual(read["contract"], "akbs-android-change-package-v2/2/android_change")
            self.assertEqual(read["component_layers"], ["application"])
            check = check_package(source)
            self.assertTrue(check["coherence"]["client_semantic_coherence_valid"])
            self.assertFalse(check["coherence"]["server_qualified"])

            before = {
                path.relative_to(source).as_posix(): path.read_bytes()
                for path in source.rglob("*")
                if path.is_file()
            }
            prepared = prepare_package(source, pending_root=workspace / "target-pending")
            destination = Path(prepared["package"])
            after = {
                path.relative_to(destination).as_posix(): path.read_bytes()
                for path in destination.rglob("*")
                if path.is_file()
            }
            self.assertEqual(after, before)
            self.assertTrue(prepared["bytes_preserved"])
            self.assertEqual(prepared["writer"]["state"], "server-controlled")
            self.assertEqual(prepared["writer"]["scope"], "submission_only")

    def test_prepare_rejects_member_and_run_symlink_escape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            source = build_package(workspace / "source")
            identity = json.loads((source / "manifest.json").read_text(encoding="utf-8"))["identity"]
            pending = workspace / "pending"
            outside = workspace / "outside"
            pending.mkdir()
            outside.mkdir()

            member = pending / identity["member_alias"]
            member.symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(AndroidChangeV2Error, "symlink or not a real directory"):
                prepare_package(source, pending_root=pending)
            self.assertEqual(list(outside.iterdir()), [])

            member.unlink()
            member.mkdir()
            run = member / identity["run_id"]
            run.symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(AndroidChangeV2Error, "pending package already exists"):
                prepare_package(source, pending_root=pending)
            self.assertEqual(list(outside.iterdir()), [])

    def test_prepare_inode_mismatch_never_deletes_swapped_entry(self) -> None:
        for replacement_kind in ("directory", "symlink"):
            with self.subTest(replacement_kind=replacement_kind), tempfile.TemporaryDirectory() as temporary:
                workspace = Path(temporary)
                source = build_package(workspace / "source")
                identity = json.loads((source / "manifest.json").read_text(encoding="utf-8"))["identity"]
                pending = workspace / "pending"
                outside = workspace / "outside"
                outside.mkdir()
                outside_sentinel = outside / "outside-sentinel"
                outside_sentinel.write_text("outside", encoding="utf-8")
                original_match = incoming_v2_validation._entry_matches_stat
                match_calls = 0
                replacement_sentinel: Path | None = None

                def exchange_then_mismatch(parent_fd: int, name: str, expected: os.stat_result) -> bool:
                    nonlocal match_calls, replacement_sentinel
                    match_calls += 1
                    if match_calls == 1:
                        return original_match(parent_fd, name, expected)
                    os.rename(
                        name,
                        name + ".original",
                        src_dir_fd=parent_fd,
                        dst_dir_fd=parent_fd,
                    )
                    parent = Path("/proc/self/fd") / str(parent_fd)
                    if replacement_kind == "directory":
                        os.mkdir(name, mode=0o700, dir_fd=parent_fd)
                        (parent / name / "replacement-sentinel").write_text(
                            "replacement", encoding="utf-8"
                        )
                        replacement_sentinel = (
                            pending / identity["member_alias"] / name / "replacement-sentinel"
                        )
                    else:
                        os.symlink(str(outside), name, dir_fd=parent_fd)
                    return False

                with mock.patch.object(
                    incoming_v2_validation,
                    "_entry_matches_stat",
                    side_effect=exchange_then_mismatch,
                ):
                    with self.assertRaisesRegex(AndroidChangeV2Error, "pending path changed"):
                        prepare_package(source, pending_root=pending)

                member = pending / identity["member_alias"]
                run = member / identity["run_id"]
                if replacement_kind == "directory":
                    self.assertIsNotNone(replacement_sentinel)
                    self.assertEqual(replacement_sentinel.read_text(encoding="utf-8"), "replacement")
                    self.assertTrue(run.is_dir())
                else:
                    self.assertTrue(run.is_symlink())
                    self.assertEqual(run.resolve(), outside.resolve())
                self.assertEqual(outside_sentinel.read_text(encoding="utf-8"), "outside")

    def test_schema_and_inventory_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            source = build_package(workspace / "source")
            package = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
            invalid = copy.deepcopy(package)
            invalid["components"][0]["layer"] = "system_app"
            write_json(source / "manifest.json", invalid)
            with self.assertRaisesRegex(AndroidChangeV2Error, "enum mismatch"):
                check_package(source)

            write_json(source / "manifest.json", package)
            (source / "unexpected.txt").write_text("extra", encoding="utf-8")
            with self.assertRaisesRegex(AndroidChangeV2Error, "inventory"):
                check_package(source)

    def test_legacy_change_domain_is_rejected_at_top_level_and_component(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            source = build_package(workspace / "source")
            package = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
            top_level = copy.deepcopy(package)
            top_level["change_domain"] = "framework"
            write_json(source / "manifest.json", top_level)
            with self.assertRaisesRegex(AndroidChangeV2Error, "additional properties at \\$"):
                check_package(source)

            component = copy.deepcopy(package)
            component["components"][0]["change_domain"] = "system_app"
            write_json(source / "manifest.json", component)
            with self.assertRaisesRegex(AndroidChangeV2Error, "additional properties at \\$/components/0"):
                check_package(source)

    def test_submit_without_member_profile_has_no_output_or_v1_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            source = build_package(workspace / "source")
            codex_home = workspace / "codex-home"
            marketplace_plugin = (
                codex_home
                / ".tmp"
                / "marketplaces"
                / "android-codex-suite"
                / "plugins"
                / "akbs-member-ops"
            )
            execution_plugin = (
                codex_home
                / "plugins"
                / "cache"
                / "android-codex-suite"
                / "akbs-member-ops"
                / PLUGIN_VERSION
            )
            for target in (marketplace_plugin, execution_plugin):
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(
                    PLUGIN,
                    target,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
                )
            fake_bin = workspace / "bin"
            fake_bin.mkdir()
            fake_codex = fake_bin / "codex"
            fake_codex.write_text(
                "#!/usr/bin/env python3\n"
                "import json\n"
                "print(json.dumps({'installed':[{'pluginId':'akbs-member-ops@android-codex-suite',"
                "'name':'akbs-member-ops','marketplaceName':'android-codex-suite','version':"
                + repr(PLUGIN_VERSION) + ","
                "'installed':True,'enabled':True,'source':{'source':'local','path':"
                + repr(str(marketplace_plugin))
                + "}}]}))\n",
                encoding="utf-8",
            )
            fake_codex.chmod(0o755)
            env = os.environ.copy()
            env.update(
                CODEX_HOME=str(codex_home),
                PYTHONDONTWRITEBYTECODE="1",
                PATH=str(fake_bin) + os.pathsep + env.get("PATH", ""),
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    str(
                        execution_plugin
                        / "skills"
                        / "akbs-patch-submit"
                        / "scripts"
                        / "akbs_patch_submit.py"
                    ),
                    "android-change-v2",
                    "submit",
                    str(source),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                check=False,
            )
            self.assertEqual(completed.returncode, 1)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["reason_code"], "android_change_v2_profile_invalid")
            self.assertFalse(payload["v1_fallback"])
            self.assertEqual(payload["network_requests"], 0)
            self.assertFalse((codex_home / "artifacts" / "akbs-member-ops").exists())

    def test_submit_dispatch_uses_v2_profile_and_never_reaches_v1(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            source = build_package(workspace / "source")
            before = {
                path.relative_to(workspace).as_posix(): path.read_bytes()
                for path in workspace.rglob("*")
                if path.is_file()
            }
            spec = importlib.util.spec_from_file_location("akbs_patch_submit_test", SCRIPT)
            self.assertIsNotNone(spec)
            self.assertIsNotNone(spec.loader)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            output = io.StringIO()
            with (
                mock.patch.object(
                    module,
                    "installed_plugin_family_status",
                    return_value={"status": "PASS", "blocking": False},
                ) as family_gate,
                mock.patch.object(module, "incoming_main") as v1_main,
                mock.patch.object(module, "route_arguments") as v1_router,
                mock.patch.object(incoming_v2_cli, "submit_package", return_value={"status": "PASS"}) as v2_submit,
                mock.patch.object(urllib.request, "urlopen") as urlopen,
                mock.patch.object(tarfile, "open") as tar_open,
                mock.patch.object(Path, "write_bytes", side_effect=AssertionError("unexpected write_bytes")),
                mock.patch.object(Path, "write_text", side_effect=AssertionError("unexpected write_text")),
                mock.patch.object(Path, "mkdir", side_effect=AssertionError("unexpected mkdir")),
                mock.patch("os.replace", side_effect=AssertionError("unexpected replace")),
                contextlib.redirect_stdout(output),
            ):
                result = module.main(["android-change-v2", "submit", str(source), "--profile", "member1"])
            self.assertEqual(result, 0)
            self.assertEqual(json.loads(output.getvalue())["status"], "PASS")
            v2_submit.assert_called_once_with(source, profile="member1")
            family_gate.assert_called_once_with()
            v1_main.assert_not_called()
            v1_router.assert_not_called()
            urlopen.assert_not_called()
            tar_open.assert_not_called()
            after = {
                path.relative_to(workspace).as_posix(): path.read_bytes()
                for path in workspace.rglob("*")
                if path.is_file()
            }
            self.assertEqual(after, before)

    def test_every_real_v2_action_requires_target_only_install_family(self) -> None:
        spec = importlib.util.spec_from_file_location("akbs_patch_submit_family_test", SCRIPT)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for action in ("read", "check", "prepare", "submit", "adapt-capture"):
            with self.subTest(action=action), mock.patch.object(
                module,
                "installed_plugin_family_status",
                return_value={"status": "TARGET_NOT_ACTIVE", "blocking": True, "message": "target not active"},
            ) as family_gate, mock.patch.object(module, "incoming_v2_main") as v2_main:
                with self.assertRaisesRegex(SystemExit, "target not active"):
                    module.main(["android-change-v2", action, "/not/read"])
                family_gate.assert_called_once_with()
                v2_main.assert_not_called()

    def test_v2_submit_has_deterministic_tar_retry_key_and_unchanged_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            source = build_package(workspace / "source")
            before = {path.relative_to(source).as_posix(): path.read_bytes() for path in source.rglob("*") if path.is_file()}
            receipt = upload_receipt(source)
            with submission_environment(workspace), mock.patch.object(
                urllib.request, "urlopen", side_effect=lambda *args, **kwargs: receipt_response(receipt)
            ) as urlopen:
                first = incoming_v2_submission.submit_package(source, profile="selected")
                for path in source.rglob("*"):
                    if path.is_file():
                        os.utime(path, (1000, 1000))
                        path.chmod(0o600)
                second = incoming_v2_submission.submit_package(source, profile="selected")
            requests = [call.args[0] for call in urlopen.call_args_list]
            self.assertEqual(len(requests), 2)
            self.assertEqual(requests[0].data, requests[1].data)
            self.assertEqual(first["archive_sha256"], second["archive_sha256"])
            self.assertEqual(first["idempotency_key"], second["idempotency_key"])
            self.assertTrue(first["server_qualified"])
            self.assertFalse(first["v1_fallback"])
            self.assertEqual(first["receipt"], receipt)
            for request in requests:
                self.assertEqual(request.full_url, "http://akbs.invalid/akbs/api/member/me/uploads/patch")
                self.assertEqual(request.method, "POST")
                headers = {key.lower(): value for key, value in request.header_items()}
                self.assertEqual(headers["x-akbs-user"], "member1")
                self.assertEqual(headers["idempotency-key"], first["idempotency_key"])
                self.assertEqual(headers["content-type"], "application/gzip")
                self.assertNotIn("x-akbs-pilot-grant", headers)
                with tarfile.open(fileobj=io.BytesIO(request.data), mode="r:gz") as archive:
                    self.assertEqual(archive.getnames(), sorted(before))
                    for item in archive.getmembers():
                        self.assertTrue(item.isfile())
                        self.assertEqual((item.uid, item.gid, item.mtime, item.mode), (0, 0, 0, 0o644))
                        self.assertEqual(archive.extractfile(item).read(), before[item.name])
            after = {path.relative_to(source).as_posix(): path.read_bytes() for path in source.rglob("*") if path.is_file()}
            self.assertEqual(before, after)
            self.assertFalse((workspace / "codex-home" / "artifacts").exists())

    def test_v2_submit_profile_mismatch_and_report_only_never_send_or_rewrite(self) -> None:
        for alias, modes, expected in (
            ("member2", "patch", "android_change_v2_member_mismatch"),
            ("member1", "daily,weekly", "android_change_v2_profile_invalid"),
        ):
            with self.subTest(alias=alias, modes=modes), tempfile.TemporaryDirectory() as temporary:
                workspace = Path(temporary)
                source = build_package(workspace / "source")
                before = (source / "manifest.json").read_bytes()
                with submission_environment(workspace, alias=alias, modes=modes), mock.patch.object(
                    urllib.request, "urlopen"
                ) as urlopen, mock.patch.object(incoming_v2_submission, "_archive_bytes") as archive:
                    with self.assertRaises(incoming_v2_submission.SubmissionError) as caught:
                        incoming_v2_submission.submit_package(source, profile="selected")
                self.assertEqual(caught.exception.reason_code, expected)
                self.assertEqual(before, (source / "manifest.json").read_bytes())
                urlopen.assert_not_called()
                archive.assert_not_called()

    def test_v2_submit_real_http_uses_existing_member_patch_route(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            source = build_package(workspace / "source")
            receipt = upload_receipt(source)
            received = []

            class Handler(http.server.BaseHTTPRequestHandler):
                def log_message(self, *args):
                    pass

                def do_POST(self):
                    body = self.rfile.read(int(self.headers["Content-Length"]))
                    received.append((self.path, dict(self.headers), body))
                    raw = json.dumps(receipt).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(raw)))
                    self.send_header("X-Request-ID", "req_" + "a" * 32)
                    self.end_headers()
                    self.wfile.write(raw)

            with http.server.HTTPServer(("127.0.0.1", 0), Handler) as server:
                server.timeout = 5
                thread = threading.Thread(target=server.handle_request, daemon=True)
                thread.start()
                with submission_environment(workspace), mock.patch.dict(os.environ, {
                    "CODEX_REPORT_AKBS_ENDPOINT_SUBMISSION_API_BASE_URL":
                    f"http://127.0.0.1:{server.server_port}/akbs/api",
                    "NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1",
                }):
                    result = incoming_v2_submission.submit_package(source, profile="selected")
                thread.join(timeout=5)
                self.assertFalse(thread.is_alive())
            self.assertEqual(result["status"], "PASS")
            self.assertEqual(result["request_id"], "req_" + "a" * 32)
            self.assertEqual(len(received), 1)
            path, headers, body = received[0]
            self.assertEqual(path, "/akbs/api/member/me/uploads/patch")
            self.assertEqual(headers["X-Akbs-User"], "member1")
            self.assertEqual(headers["Idempotency-Key"], result["idempotency_key"])
            self.assertEqual(hashlib.sha256(body).hexdigest(), result["archive_sha256"])

    def test_v2_submit_checks_the_bytes_written_to_tar(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            source = build_package(workspace / "source")
            original_addfile = tarfile.TarFile.addfile

            def substitute_encoded_file(archive, entry, reader=None):
                if entry.name == "patches/change.patch":
                    reader.source = io.BytesIO(b"x" * entry.size)
                return original_addfile(archive, entry, reader)

            with submission_environment(workspace), mock.patch.object(
                tarfile.TarFile, "addfile", new=substitute_encoded_file
            ), mock.patch.object(urllib.request, "urlopen") as urlopen:
                with self.assertRaises(incoming_v2_submission.SubmissionError) as caught:
                    incoming_v2_submission.submit_package(source)
            self.assertEqual(caught.exception.reason_code, "android_change_v2_payload_changed")
            urlopen.assert_not_called()

    def test_v2_submit_rechecks_bytes_after_check_before_http(self) -> None:
        for mutate_manifest in (False, True):
            with self.subTest(manifest=mutate_manifest), tempfile.TemporaryDirectory() as temporary:
                workspace = Path(temporary)
                source = build_package(workspace / "source")
                original_check = incoming_v2_submission.check_package
                def change_after_check(path):
                    result = original_check(path)
                    target = source / ("manifest.json" if mutate_manifest else "patches/change.patch")
                    target.write_bytes(target.read_bytes() + b"\n")
                    return result
                with submission_environment(workspace), mock.patch.object(
                    incoming_v2_submission, "check_package", side_effect=change_after_check
                ), mock.patch.object(urllib.request, "urlopen") as urlopen:
                    with self.assertRaises(incoming_v2_submission.SubmissionError) as caught:
                        incoming_v2_submission.submit_package(source)
                self.assertEqual(caught.exception.reason_code, "android_change_v2_payload_changed")
                urlopen.assert_not_called()

    def test_v2_submit_server_off_auth_conflict_and_transport_are_explicit_no_fallback(self) -> None:
        for status, code in (
            (503, "android_change_v2_writer_off"), (403, "forbidden"),
            (409, "conflict"), (413, "payload_too_large"), (0, "transport_error"),
        ):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as temporary:
                workspace = Path(temporary)
                source = build_package(workspace / "source")
                if status:
                    envelope = {
                        "schema": "akbs-error-envelope-v1", "code": code,
                        "message": "request rejected", "request_id": "req_" + "a" * 32,
                    }
                    error = urllib.error.HTTPError(
                        "http://akbs.invalid", status, "rejected",
                        {"X-Request-ID": envelope["request_id"]}, io.BytesIO(json.dumps(envelope).encode()),
                    )
                else:
                    error = urllib.error.URLError("test timeout")
                output = io.StringIO()
                with submission_environment(workspace), mock.patch.object(
                    urllib.request, "urlopen", side_effect=error
                ) as urlopen, contextlib.redirect_stdout(output):
                    exit_code = incoming_v2_cli.main(["submit", str(source), "--profile", "selected"])
                result = json.loads(output.getvalue())
                self.assertEqual(exit_code, 1)
                self.assertEqual(result["http_status"], status)
                if status:
                    self.assertEqual(result["reason_code"], code)
                else:
                    self.assertTrue(result["retryable"])
                self.assertFalse(result["v1_fallback"])
                self.assertFalse(result["server_qualified"])
                self.assertEqual(result["network_requests"], 1)
                urlopen.assert_called_once()

    def test_v2_submit_rejects_wrong_or_unbound_success_receipts(self) -> None:
        mutations = (
            lambda value: value.update(schema="incoming-v1"),
            lambda value: value.update(accepted=False),
            lambda value: value["package"].update(package_key="other/package"),
            lambda value: value["package"].update(revision=True),
            lambda value: value["operation"].update(manifest_sha256="0" * 64),
            lambda value: value["operation"].update(directory_payload_sha256="0" * 64),
            lambda value: value["operation"].update(idempotency_key_sha256="0" * 64),
            lambda value: value["qualification"].update(authenticated_actor="member2"),
            lambda value: value["qualification"].update(decision="reject"),
            lambda value: value["qualification"].update(qualification_input_sha256="0" * 64),
        )
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            source = build_package(workspace / "source")
            good = upload_receipt(source)
            for index, mutate in enumerate(mutations):
                receipt = copy.deepcopy(good)
                mutate(receipt)
                receipt["receipt_sha256"] = incoming_v2_validation.canonical_json_sha256(
                    {key: value for key, value in receipt.items() if key != "receipt_sha256"}
                )
                with self.subTest(index=index), submission_environment(workspace), mock.patch.object(
                    urllib.request, "urlopen", return_value=receipt_response(receipt)
                ):
                    with self.assertRaises(HttpClientFailure) as caught:
                        incoming_v2_submission.submit_package(source)
                self.assertEqual(caught.exception.result.code, "invalid_success_response")
            for raw in (b"not JSON", b"[]", json.dumps({**good, "receipt_sha256": "0" * 64}).encode()):
                with self.subTest(raw=raw[:30]), submission_environment(workspace), mock.patch.object(
                    urllib.request, "urlopen", return_value=io.BytesIO(raw)
                ):
                    with self.assertRaises(HttpClientFailure) as caught:
                        incoming_v2_submission.submit_package(source)
                self.assertEqual(caught.exception.result.code, "invalid_success_response")

    def test_real_v2_actions_fail_closed_when_active_inventory_is_unavailable(self) -> None:
        spec = importlib.util.spec_from_file_location("akbs_patch_submit_inventory_test", SCRIPT)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        cases = (
            subprocess.CompletedProcess(
                ["codex", "plugin", "list", "--json"], 9, stdout="", stderr="unavailable"
            ),
            subprocess.CompletedProcess(
                ["codex", "plugin", "list", "--json"], 0, stdout="{not-json", stderr=""
            ),
        )
        for response in cases:
            for action in ("read", "check", "prepare", "submit", "adapt-capture"):
                version_gate.PLUGIN_LIST_CACHE = None
                with self.subTest(response=response, action=action), mock.patch.object(
                    version_gate, "run", return_value=response
                ), mock.patch.object(module, "incoming_v2_main") as v2_main:
                    with self.assertRaisesRegex(SystemExit, "无法读取 Codex active plugin 列表"):
                        module.main(["android-change-v2", action, "/must/not/be/read"])
                    v2_main.assert_not_called()


if __name__ == "__main__":
    unittest.main()
