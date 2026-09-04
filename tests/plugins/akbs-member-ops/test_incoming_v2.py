from __future__ import annotations

import copy
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tarfile
import unittest
import urllib.request
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
PLUGIN = ROOT / "plugins" / "akbs-member-ops"
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
            self.assertEqual(prepared["writer"]["state"], "blocked")
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

    def test_submit_writer_off_has_zero_output_and_no_v1_fallback(self) -> None:
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
                / "2.0.1"
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
                "'name':'akbs-member-ops','marketplaceName':'android-codex-suite','version':'2.0.1',"
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
            self.assertEqual(payload["reason_code"], "android_change_v2_writer_off")
            self.assertFalse(payload["writer"]["v1_fallback"])
            self.assertEqual(payload["writer"]["network_requests"], 0)
            self.assertEqual(payload["writer"]["files_written"], 0)
            self.assertFalse((codex_home / "artifacts" / "akbs-member-ops").exists())

    def test_submit_dispatch_never_reaches_v1_network_tar_or_writes(self) -> None:
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
                mock.patch.object(urllib.request, "urlopen") as urlopen,
                mock.patch.object(tarfile, "open") as tar_open,
                mock.patch.object(Path, "write_bytes", side_effect=AssertionError("unexpected write_bytes")),
                mock.patch.object(Path, "write_text", side_effect=AssertionError("unexpected write_text")),
                mock.patch.object(Path, "mkdir", side_effect=AssertionError("unexpected mkdir")),
                mock.patch("os.replace", side_effect=AssertionError("unexpected replace")),
                contextlib.redirect_stdout(output),
            ):
                result = module.main(["android-change-v2", "submit", str(source)])
            self.assertEqual(result, 1)
            self.assertEqual(json.loads(output.getvalue())["reason_code"], "android_change_v2_writer_off")
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
