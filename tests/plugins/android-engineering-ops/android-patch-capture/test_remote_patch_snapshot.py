from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest


REPO_ROOT = Path(__file__).resolve().parents[4]
LIB_ROOT = REPO_ROOT / "plugins" / "android-engineering-ops" / "lib"
if str(LIB_ROOT) not in sys.path:
    sys.path.insert(0, str(LIB_ROOT))

from android_engineering_ops.remote_patch_snapshot import (  # noqa: E402
    RemotePatchSnapshotError,
    create_remote_patch_snapshot,
    decode_snapshot_blob,
    validate_remote_patch_snapshot,
)


SKILL_ROOT = (
    REPO_ROOT
    / "plugins"
    / "android-engineering-ops"
    / "skills"
    / "android-patch-capture"
)
CAPTURE = SKILL_ROOT / "scripts" / "capture_android_patch.py"
HANDOFF = SKILL_ROOT / "scripts" / "capture_remote_snapshot.py"
INSTALLED_RUNTIME_ENTRYPOINTS = ("CAPTURE", "HANDOFF")
SNAPSHOT_MODULE = LIB_ROOT / "android_engineering_ops" / "remote_patch_snapshot.py"
PLUGIN_SOURCE = REPO_ROOT / "plugins" / "android-engineering-ops"
PLUGIN_VERSION = json.loads((PLUGIN_SOURCE / ".codex-plugin/plugin.json").read_text())["version"]


def run(command: list[str], cwd: Path, *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged_env = {**os.environ, **(env or {})}
    codex_home_value = merged_env.get("CODEX_HOME")
    if codex_home_value:
        fixture_bin = Path(codex_home_value) / "test-inventory-bin"
        if fixture_bin.is_dir():
            merged_env["PATH"] = f"{fixture_bin}{os.pathsep}{merged_env['PATH']}"
    return subprocess.run(
        command,
        cwd=str(cwd),
        env=merged_env,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


class RemotePatchSnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name).resolve()
        self.remote_root = self.root / "remote"
        self.repo = self.remote_root / "frameworks" / "base"
        self.repo.mkdir(parents=True)
        for command in (
            ["git", "init"],
            ["git", "config", "user.email", "snapshot@example.invalid"],
            ["git", "config", "user.name", "Snapshot Test"],
        ):
            result = run(command, self.repo)
            self.assertEqual(result.returncode, 0, result.stderr)
        tracked = self.repo / "services" / "core" / "DisplayPolicy.java"
        staged = self.repo / "core" / "Framework.java"
        tracked.parent.mkdir(parents=True)
        staged.parent.mkdir(parents=True)
        tracked.write_text("class DisplayPolicy {\n}\n", encoding="utf-8")
        staged.write_text("class Framework {\n}\n", encoding="utf-8")
        self.assertEqual(run(["git", "add", "."], self.repo).returncode, 0)
        self.assertEqual(run(["git", "commit", "-m", "initial"], self.repo).returncode, 0)

        staged.write_text(
            "class Framework {\n"
            "  //member01 20260826@{\n"
            "  static final boolean ENABLED = true;\n"
            "  //member01 20260826@}\n"
            "}\n",
            encoding="utf-8",
        )
        self.assertEqual(run(["git", "add", "core/Framework.java"], self.repo).returncode, 0)
        tracked.write_text(
            "class DisplayPolicy {\n"
            "  //member01 20260826@{\n"
            "  static final boolean REMOTE_POLICY = true;\n"
            "  //member01 20260826@}\n"
            "}\n",
            encoding="utf-8",
        )
        untracked = self.repo / "services" / "core" / "NewPolicy.java"
        untracked.write_text(
            "//member01 20260826@{\n"
            "class NewPolicy {\n"
            "  static final boolean NEW_POLICY = true;\n"
            "}\n"
            "//member01 20260826@}\n",
            encoding="utf-8",
        )

        private_repo = self.remote_root / ".repo" / "repo" / "repo"
        private_repo.parent.mkdir(parents=True)
        private_repo.write_text("#!/bin/sh\nprintf 'repo-status-fixture\\n'\n", encoding="utf-8")
        private_repo.chmod(0o755)
        self.workspace_id = "0123456789abcdef"
        self.command_id = "patch-snapshot-001"
        self.generated_at_ns = time.time_ns()
        self.snapshot = create_remote_patch_snapshot(
            remote_root=self.remote_root,
            workspace_id=self.workspace_id,
            command_id=self.command_id,
            repository_paths=["frameworks/base"],
            generated_at_ns=self.generated_at_ns,
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def validation(self, **overrides: object) -> dict[str, object]:
        arguments: dict[str, object] = {
            "expected_workspace_id": self.workspace_id,
            "expected_command_id": self.command_id,
            "expected_remote_root": self.remote_root.as_posix(),
            "expected_sha256": self.snapshot["snapshot_sha256"],
            "now_ns": self.generated_at_ns,
            "max_age_ns": 1_000_000_000,
        }
        arguments.update(overrides)
        return validate_remote_patch_snapshot(self.snapshot, **arguments)

    def write_snapshot(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def install_plugin(
        self,
        codex_home: Path,
        *,
        channel: Path | None = None,
        bin_dir: Path | None = None,
    ) -> Path:
        """Create separate marketplace source and exact versioned runtime trees."""
        source = codex_home / "test-marketplace/plugins/android-engineering-ops"
        runtime = (
            codex_home
            / "plugins/cache/android-codex-suite/android-engineering-ops" / PLUGIN_VERSION
        )
        for plugin in (source, runtime):
            plugin.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(
                PLUGIN_SOURCE,
                plugin,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
            if channel is not None:
                bundled = plugin / "skills/android-remote-channel/scripts/remote-channel.sh"
                bundled.write_bytes(channel.read_bytes())
                bundled.chmod(0o755)
        inventory = {
            "installed": [
                {
                    "pluginId": "android-engineering-ops@android-codex-suite",
                    "name": "android-engineering-ops",
                    "marketplaceName": "android-codex-suite",
                    "version": PLUGIN_VERSION,
                    "installed": True,
                    "enabled": True,
                    "source": {"source": "local", "path": str(source)},
                }
            ]
        }
        bin_dir = bin_dir or codex_home / "test-inventory-bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        codex = bin_dir / "codex"
        codex.write_text(
            "#!/usr/bin/env python3\n"
            "import json\n"
            f"print(json.dumps({inventory!r}, sort_keys=True))\n",
            encoding="utf-8",
        )
        codex.chmod(0o755)
        return runtime

    def capture_command(self, snapshot_path: Path, codex_home: Path) -> list[str]:
        codex_home.mkdir(parents=True, exist_ok=True)
        plugin = self.install_plugin(codex_home)
        (codex_home / "android-knowledge-intake.toml").write_text(
            "default_profile = \"member01\"\n\n"
            "[profiles.member01]\n"
            "member_alias = \"member01\"\n"
            "member_name = \"Member 01\"\n"
            "timezone = \"Asia/Shanghai\"\n",
            encoding="utf-8",
        )
        return [
            sys.executable,
            str(plugin / "skills/android-patch-capture/scripts/capture_android_patch.py"),
            "--remote-snapshot",
            str(snapshot_path),
            "--snapshot-workspace-id",
            self.workspace_id,
            "--snapshot-command-id",
            self.command_id,
            "--snapshot-sha256",
            str(self.snapshot["snapshot_sha256"]),
            "--snapshot-max-age-seconds",
            "86400",
            "--remote-source-root",
            self.remote_root.as_posix(),
            "--profile",
            "member01",
            "--out-dir",
            str(codex_home / "artifacts/android-patch-capture/packages"),
            "--run-id",
            "remote-snapshot-package",
            "--platform",
            "unisoc14",
            "--component-layer",
            "platform",
            "--component-type",
            "framework",
            "--component-partition",
            "system",
            "--component-ownership",
            "aosp",
            "--change-id",
            "remote-display-policy",
            "--summary",
            "TVE1088U remote display policy",
            "--project",
            "TVE1088U",
            "--status",
            "candidate",
            "--search-query",
            "display policy",
            "--search-result",
            "No candidate found",
            "--reuse-decision",
            "not_found",
        ]

    def test_snapshot_captures_git_repo_binary_diffs_and_untracked_inventory(self) -> None:
        validated = self.validation()
        repository = validated["repositories"][0]
        self.assertRegex(repository["head"], r"^[0-9a-f]{40}$")
        self.assertTrue(decode_snapshot_blob(repository["status"], field="status"))
        self.assertIn(b"Framework.java", decode_snapshot_blob(repository["staged_diff"], field="staged"))
        self.assertIn(b"DisplayPolicy.java", decode_snapshot_blob(repository["unstaged_diff"], field="unstaged"))
        self.assertIn(b"NewPolicy.java", decode_snapshot_blob(repository["untracked_diff"], field="untracked"))
        self.assertEqual(
            repository["changed_files"],
            ["core/Framework.java", "services/core/DisplayPolicy.java", "services/core/NewPolicy.java"],
        )
        self.assertEqual(repository["untracked"][0]["path"], "services/core/NewPolicy.java")
        self.assertTrue(self.snapshot["repo_status"]["available"])
        self.assertEqual(
            decode_snapshot_blob(self.snapshot["repo_status"]["output"], field="repo-status"),
            b"repo-status-fixture\n",
        )

    def test_snapshot_validation_rejects_missing_tampered_wrong_identity_and_stale(self) -> None:
        cases: list[tuple[dict[str, object], dict[str, object]]] = []
        missing = dict(self.snapshot)
        missing.pop("repositories")
        cases.append((missing, {}))
        wrong_workspace = dict(self.snapshot)
        cases.append((wrong_workspace, {"expected_workspace_id": "fedcba9876543210"}))
        stale = dict(self.snapshot)
        cases.append(
            (
                stale,
                {
                    "now_ns": self.generated_at_ns + 2_000_000_000,
                    "max_age_ns": 1_000_000_000,
                },
            )
        )
        tampered = json.loads(json.dumps(self.snapshot))
        tampered["repositories"][0]["head_diff"]["data"] = "AA=="
        cases.append((tampered, {}))
        for payload, overrides in cases:
            with self.subTest(overrides=overrides):
                arguments: dict[str, object] = {
                    "expected_workspace_id": self.workspace_id,
                    "expected_command_id": self.command_id,
                    "expected_remote_root": self.remote_root.as_posix(),
                    "expected_sha256": self.snapshot["snapshot_sha256"],
                    "now_ns": self.generated_at_ns,
                    "max_age_ns": 1_000_000_000,
                }
                arguments.update(overrides)
                with self.assertRaises(RemotePatchSnapshotError):
                    validate_remote_patch_snapshot(payload, **arguments)

    def test_remote_generator_cli_writes_once_with_read_only_mode(self) -> None:
        remote_home = self.root / "remote-home-cli"
        remote_home.mkdir()
        command = [
            sys.executable,
            str(SNAPSHOT_MODULE),
            "generate",
            "--remote-root",
            self.remote_root.as_posix(),
            "--workspace-id",
            self.workspace_id,
            "--command-id",
            "immutable-cli-snapshot",
            "--repo-path",
            "frameworks/base",
        ]
        first = run(command, self.root, env={"HOME": str(remote_home)})
        self.assertEqual(first.returncode, 0, first.stderr or first.stdout)
        line = next(item for item in first.stdout.splitlines() if item.startswith("SNAPSHOT_REMOTE_PATH="))
        snapshot_path = Path(line.split("=", 1)[1])
        self.assertTrue(snapshot_path.is_file())
        self.assertEqual(snapshot_path.stat().st_mode & 0o777, 0o400)

        second = run(command, self.root, env={"HOME": str(remote_home)})
        self.assertNotEqual(second.returncode, 0)
        self.assertIn("immutable snapshot already exists", second.stderr)

    def test_current_capture_consumes_snapshot_after_source_tree_disappears(self) -> None:
        snapshot_path = self.root / "handoff" / "snapshot.json"
        codex_home = self.root / "codex-home"
        self.write_snapshot(snapshot_path)
        shutil.rmtree(self.remote_root)

        result = run(
            self.capture_command(snapshot_path, codex_home),
            self.root,
            env={"CODEX_HOME": str(codex_home)},
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        package = Path(json.loads(result.stdout)["package"])
        manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
        patch = (package / manifest["patches"][0]["path"]).read_text(encoding="utf-8")
        self.assertIn("DisplayPolicy.java", patch)
        self.assertIn("Framework.java", patch)
        self.assertIn("NewPolicy.java", patch)
        self.assertEqual(manifest["source_snapshot"]["workspace_id"], self.workspace_id)
        self.assertEqual(manifest["source_snapshot"]["command_id"], self.command_id)
        self.assertTrue((package / "evidence" / "remote-source-snapshot.json").is_file())
        package.relative_to(codex_home / "artifacts")

    def test_current_capture_works_with_clean_engineering_only_identity(self) -> None:
        snapshot_path = self.root / "standalone-handoff" / "snapshot.json"
        codex_home = self.root / "standalone-codex-home"
        self.write_snapshot(snapshot_path)
        command = self.capture_command(snapshot_path, codex_home)
        (codex_home / "android-knowledge-intake.toml").unlink()
        (codex_home / "android-engineering-ops.toml").write_text(
            '[identity]\nmember_alias = "member01"\n', encoding="utf-8"
        )
        profile_index = command.index("--profile")
        del command[profile_index : profile_index + 2]

        result = run(command, self.root, env={"CODEX_HOME": str(codex_home)})

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        package = Path(json.loads(result.stdout)["package"])
        coding = json.loads(
            (package / "evidence/coding-standard-check.json").read_text(encoding="utf-8")
        )
        self.assertEqual(coding["expected_member_alias"], "member01")
        self.assertEqual(coding["identity_source"], "android-engineering-ops-identity")

    def test_current_capture_rejects_source_root_patch_artifact_and_external_output(self) -> None:
        base = [
            sys.executable,
            str(CAPTURE),
            "--platform",
            "unisoc14",
            "--feature",
            "invalid-current-input",
            "--summary",
            "invalid current input",
        ]
        source = run([*base, "--source-root", str(self.repo)], self.root)
        self.assertNotEqual(source.returncode, 0)
        self.assertIn("禁止 --source-root", source.stderr)

        patch_path = self.root / "manual.patch"
        patch_path.write_bytes(
            decode_snapshot_blob(self.snapshot["repositories"][0]["head_diff"], field="head")
        )
        patch = run(
            [*base, "--patch-artifact", str(patch_path), "--patch-repo-path", "frameworks/base"],
            self.root,
        )
        self.assertNotEqual(patch.returncode, 0)
        self.assertIn("不接受 caller patch artifact", patch.stderr)

        snapshot_path = self.root / "snapshot.json"
        self.write_snapshot(snapshot_path)
        codex_home = self.root / "codex-home"
        command = self.capture_command(snapshot_path, codex_home)
        output_index = command.index("--out-dir") + 1
        command[output_index] = str(self.root / "outside")
        output = run(command, self.root, env={"CODEX_HOME": str(codex_home)})
        self.assertNotEqual(output.returncode, 0)
        self.assertIn("$CODEX_HOME/artifacts", output.stderr)

    def test_manual_import_consumes_explicit_patch_without_source_tree(self) -> None:
        patch = self.root / "explicit.patch"
        repository = self.snapshot["repositories"][0]
        patch.write_bytes(
            decode_snapshot_blob(repository["head_diff"], field="head")
            + decode_snapshot_blob(repository["untracked_diff"], field="untracked")
        )
        shutil.rmtree(self.remote_root)
        codex_home = self.root / "manual-codex-home"
        plugin = self.install_plugin(codex_home)
        output_root = codex_home / "artifacts/android-patch-capture/packages"
        result = run(
            [
                sys.executable,
                str(plugin / "skills/android-patch-capture/scripts/capture_android_patch.py"),
                "--workflow-contract",
                "manual_import",
                "--implementation-origin",
                "manual",
                "--patch-artifact",
                str(patch),
                "--patch-repo-path",
                "frameworks/base",
                "--out-dir",
                str(output_root),
                "--run-id",
                "manual-package",
                "--platform",
                "unisoc14",
                "--component-layer",
                "platform",
                "--component-type",
                "framework",
                "--component-partition",
                "system",
                "--component-ownership",
                "aosp",
                "--feature",
                "manual-display-policy",
                "--summary",
                "manual display policy import",
            ],
            self.root,
            env={"CODEX_HOME": str(codex_home)},
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        manifest = json.loads(
            (Path(json.loads(result.stdout)["package"]) / "manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["workflow_contract"], "manual_import")
        self.assertNotIn("source_snapshot", manifest)
        self.assertIn("patch_artifact_sha256", manifest["git_repositories"][0]["git"])

    def test_handoff_uses_channel_exclusive_then_scp_and_validates(self) -> None:
        remote_snapshot = (
            self.root
            / "remote-home"
            / ".codex"
            / "android-remote-sessions"
            / self.workspace_id
            / "snapshots"
            / self.command_id
            / "snapshot.json"
        )
        self.write_snapshot(remote_snapshot)
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        channel_log = self.root / "channel-args.txt"
        fake_channel = bin_dir / "fake-channel"
        fake_channel.write_text(
            "#!/bin/sh\n"
            "printf '%s\\n' \"$@\" >\"$FAKE_CHANNEL_LOG\"\n"
            f"printf 'SNAPSHOT_REMOTE_PATH={remote_snapshot}\\n'\n"
            f"printf 'SNAPSHOT_SHA256={self.snapshot['snapshot_sha256']}\\n'\n"
            f"printf 'SNAPSHOT_WORKSPACE_ID={self.workspace_id}\\n'\n"
            f"printf 'SNAPSHOT_COMMAND_ID={self.command_id}\\n'\n"
            f"printf 'SNAPSHOT_REMOTE_ROOT={self.remote_root}\\n'\n",
            encoding="utf-8",
        )
        fake_channel.chmod(0o755)
        fake_scp = bin_dir / "scp"
        fake_scp.write_text(
            "#!/bin/sh\n"
            "source_path=${2#*:}\n"
            "cp \"$source_path\" \"$3\"\n",
            encoding="utf-8",
        )
        fake_scp.chmod(0o755)
        codex_home = self.root / "codex-home"
        plugin = self.install_plugin(codex_home, channel=fake_channel, bin_dir=bin_dir)
        handoff = plugin / "skills/android-patch-capture/scripts/capture_remote_snapshot.py"
        result = run(
            [
                sys.executable,
                str(handoff),
                "--ssh-host",
                "fake-host",
                "--remote-root",
                self.remote_root.as_posix(),
                "--repo-path",
                "frameworks/base",
                "--command-id",
                self.command_id,
            ],
            self.root,
            env={
                "CODEX_HOME": str(codex_home),
                "FAKE_CHANNEL_LOG": str(channel_log),
                "PATH": f"{bin_dir}:{os.environ['PATH']}",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        output = json.loads(result.stdout)
        snapshot_path = Path(output["snapshot"])
        self.assertTrue(snapshot_path.is_file())
        snapshot_path.relative_to(codex_home / "artifacts")
        channel_arguments = channel_log.read_text(encoding="utf-8").splitlines()
        self.assertIn("run", channel_arguments)
        self.assertIn("exclusive", channel_arguments)
        self.assertIn(self.command_id, channel_arguments)
        self.assertTrue(any("remote_patch_snapshot" in item for item in channel_arguments))

    def test_handoff_channel_failure_never_falls_back_to_transfer_or_source(self) -> None:
        bin_dir = self.root / "failure-bin"
        bin_dir.mkdir()
        fake_channel = bin_dir / "fake-channel"
        fake_channel.write_text(
            "#!/bin/sh\nprintf 'CHANNEL_LOST fixture\\n' >&2\nexit 125\n",
            encoding="utf-8",
        )
        fake_channel.chmod(0o755)
        scp_marker = self.root / "scp-was-called"
        fake_scp = bin_dir / "scp"
        fake_scp.write_text(
            f"#!/bin/sh\ntouch {scp_marker}\nexit 0\n",
            encoding="utf-8",
        )
        fake_scp.chmod(0o755)
        codex_home = self.root / "failure-codex-home"
        plugin = self.install_plugin(codex_home, channel=fake_channel, bin_dir=bin_dir)
        handoff = plugin / "skills/android-patch-capture/scripts/capture_remote_snapshot.py"
        result = run(
            [
                sys.executable,
                str(handoff),
                "--ssh-host",
                "fake-host",
                "--remote-root",
                self.remote_root.as_posix(),
                "--repo-path",
                "frameworks/base",
                "--command-id",
                self.command_id,
            ],
            self.root,
            env={
                "CODEX_HOME": str(codex_home),
                "PATH": f"{bin_dir}:{os.environ['PATH']}",
            },
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("禁止回退", result.stderr)
        self.assertFalse(scp_marker.exists())

    def test_handoff_rejects_public_channel_override(self) -> None:
        codex_home = self.root / "override-codex-home"
        plugin = self.install_plugin(codex_home)
        handoff = plugin / "skills/android-patch-capture/scripts/capture_remote_snapshot.py"
        result = run(
            [
                sys.executable,
                str(handoff),
                "--ssh-host",
                "fake-host",
                "--remote-root",
                self.remote_root.as_posix(),
                "--repo-path",
                "frameworks/base",
                "--command-id",
                self.command_id,
                "--channel-script",
                "/bin/true",
            ],
            self.root,
            env={"CODEX_HOME": str(codex_home)},
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cannot override the bundled", result.stderr)


if __name__ == "__main__":
    unittest.main()
