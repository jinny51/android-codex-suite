from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import textwrap
import unittest


REPO_ROOT = Path(__file__).resolve().parents[4]
SKILL_SCRIPTS = (
    REPO_ROOT / "plugins" / "android-engineering-ops" / "skills" / "android-remote-build-deploy" / "scripts"
)
ENTRY = SKILL_SCRIPTS / "remote-build-v2.py"
LEGACY_GENERATOR = SKILL_SCRIPTS / "generate-build-push.sh"
PLUGIN_SOURCE = REPO_ROOT / "plugins" / "android-engineering-ops"


def make_fake_channel(root: Path) -> Path:
    script = root / "fake-remote-channel.py"
    script.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env python3
            import hashlib
            import os
            from pathlib import Path
            import subprocess
            import sys

            values = sys.argv[1:]
            ssh_host = values[values.index("--ssh-host") + 1]
            requested = values[values.index("--remote-root") + 1]
            root = Path(requested).resolve(strict=True)
            workspace = hashlib.sha256(f"fake-machine\\n501\\n{root}".encode()).hexdigest()[:16]
            action_index = next(i for i, value in enumerate(values) if value in {"check", "run"})
            action = values[action_index]
            if action == "check":
                print(f"SSH_OK host={ssh_host}")
                print(f"REMOTE_ROOT_OK requested={requested} canonical={root}")
                print(f"WORKSPACE_OK id={workspace} server_id_sha256={'a' * 64} uid=501")
                raise SystemExit(0)

            tail = values[action_index + 1 :]
            command_id = tail[tail.index("--command-id") + 1]
            command = tail[tail.index("--") + 1]
            if os.environ.get("FAKE_CHANNEL_TIMEOUT_ALWAYS") == "1":
                timeout_log = root / ".fake-channel-timeouts"
                with timeout_log.open("a", encoding="utf-8") as handle:
                    handle.write(command_id + "\\n")
                raise SystemExit(124)
            state = root / ".fake-channel" / command_id
            state.mkdir(parents=True, exist_ok=True)
            request = hashlib.sha256(command.encode()).hexdigest()
            request_file = state / "request.sha256"
            if request_file.exists() and request_file.read_text().strip() != request:
                print(f"COMMAND_ID_CONFLICT id={command_id}", file=sys.stderr)
                raise SystemExit(4)
            request_file.write_text(request + "\\n")
            log = state / "log"
            exit_file = state / "exit"
            if exit_file.exists():
                print(f"COMMAND_ATTACHED id={command_id}")
                if log.exists():
                    print(log.read_text(), end="")
                rc = int(exit_file.read_text())
                print(f"__CODEX_CMD_DONE id={command_id} rc={rc}")
                raise SystemExit(rc)
            result = subprocess.run(
                ["bash", "-c", command],
                cwd=root,
                env=os.environ.copy(),
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            log.write_text(result.stdout)
            exit_file.write_text(str(result.returncode))
            disconnect = root / ".fake-channel-disconnect-once"
            if os.environ.get("FAKE_CHANNEL_DISCONNECT_ONCE") == "1" and not disconnect.exists():
                disconnect.write_text(command_id + "\\n")
                raise SystemExit(255)
            print(f"COMMAND_STARTED id={command_id}")
            print(result.stdout, end="")
            print(f"__CODEX_CMD_DONE id={command_id} rc={result.returncode}")
            raise SystemExit(result.returncode)
            """
        ),
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script


class RemoteBuildV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.remote = self.root / "remote project"
        self.remote.mkdir()
        self.channel = make_fake_channel(self.root)
        self.codex_home = self.root / "codex-home"
        self.plugin_source = (
            self.root / "marketplace-source/plugins/android-engineering-ops"
        )
        shutil.copytree(
            PLUGIN_SOURCE,
            self.plugin_source,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        bundled_channel = (
            self.plugin_source
            / "skills/android-remote-channel/scripts/remote-channel.sh"
        )
        bundled_channel.write_bytes(self.channel.read_bytes())
        bundled_channel.chmod(bundled_channel.stat().st_mode | stat.S_IXUSR)
        self.plugin = (
            self.codex_home
            / "plugins/cache/android-codex-suite/android-engineering-ops/2.0.2"
        )
        self.plugin.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(self.plugin_source, self.plugin)
        self.entry = (
            self.plugin
            / "skills/android-remote-build-deploy/scripts/remote-build-v2.py"
        )
        inventory_bin = self.root / "inventory-bin"
        inventory_bin.mkdir()
        codex = inventory_bin / "codex"
        inventory = {
            "installed": [
                {
                    "pluginId": "android-engineering-ops@android-codex-suite",
                    "name": "android-engineering-ops",
                    "marketplaceName": "android-codex-suite",
                    "version": "2.0.2",
                    "installed": True,
                    "enabled": True,
                    "source": {"source": "local", "path": str(self.plugin_source)},
                }
            ]
        }
        codex.write_text(
            "#!/usr/bin/env python3\n"
            "import json\n"
            f"print(json.dumps({inventory!r}, sort_keys=True))\n",
            encoding="utf-8",
        )
        codex.chmod(codex.stat().st_mode | stat.S_IXUSR)
        self.env = {
            **os.environ,
            "CODEX_HOME": str(self.codex_home),
            "PATH": f"{inventory_bin}{os.pathsep}{os.environ['PATH']}",
            "PYTHONDONTWRITEBYTECODE": "1",
        }

        (self.remote / "build").mkdir()
        (self.remote / "frameworks/base/services/core/java/com/example").mkdir(parents=True)
        (self.remote / "frameworks/base/services/core/java/com/example/Example.java").write_text(
            "class Example {}\n", encoding="utf-8"
        )
        (self.remote / "debug.sh").write_text(
            "source build/envsetup.sh\nlunch test-userdebug\n# out/target/product/test\nmake -j8\n",
            encoding="utf-8",
        )
        (self.remote / "debug_secondary.sh").write_text(
            "source build/envsetup.sh\nlunch ignored-userdebug\n",
            encoding="utf-8",
        )
        (self.remote / "build/envsetup.sh").write_text(
            textwrap.dedent(
                """\
                lunch() { export REMOTE_TEST_LUNCH="$1"; }
                m() {
                  count_file="$PWD/.build-count"
                  count=0
                  [ ! -f "$count_file" ] || count=$(cat "$count_file")
                  count=$((count + 1))
                  printf '%s\\n' "$count" > "$count_file"
                  artifact="$PWD/out/target/product/test/system/framework/services.jar"
                  mkdir -p "$(dirname "$artifact")"
                  printf 'services-build-%s\\n' "$count" > "$artifact"
                }
                """
            ),
            encoding="utf-8",
        )
        subprocess.run(["git", "init", "-q", str(self.remote)], check=True)
        subprocess.run(["git", "-C", str(self.remote), "config", "user.email", "fixture@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(self.remote), "config", "user.name", "Fixture"], check=True)
        subprocess.run(["git", "-C", str(self.remote), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.remote), "commit", "-qm", "fixture"], check=True)
        untracked = self.remote / "untracked" / "配置.txt"
        untracked.parent.mkdir()
        untracked.write_text("checkpoint me\n", encoding="utf-8")

        legacy_dir = self.remote / ".codex"
        legacy_dir.mkdir()
        self.legacy = legacy_dir / "build-push.sh"
        self.legacy.write_text(
            "ARTIFACT_MTIME_BEFORE=1\nTOUCH_TARGET=1\ndevice_dir=/system/framework/\n",
            encoding="utf-8",
        )
        self.legacy_sha = hashlib.sha256(self.legacy.read_bytes()).hexdigest()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def run_v2(
        self,
        action: str,
        *arguments: str,
        preserve: bool = True,
        project_id: str = "fixture-project",
    ) -> subprocess.CompletedProcess[str]:
        command = [
            sys.executable,
            str(self.entry),
            "--ssh-host",
            "fixture-alias",
            "--project-root",
            str(self.remote),
            "--project-id",
            project_id,
            "--artifacts-root",
            str(self.root / "handoff"),
        ]
        if preserve:
            command.append("--preserve-legacy")
        command.extend([action, *arguments])
        return subprocess.run(
            command,
            env=self.env,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def test_local_generator_is_a_hard_failure(self) -> None:
        result = subprocess.run(
            [str(LEGACY_GENERATOR), "--repo", str(self.remote)],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(result.returncode, 64)
        self.assertIn("retired by remote workspace protocol v2", result.stderr)

    def test_public_channel_override_fails_before_external_execution(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(self.entry),
                "--ssh-host", "fixture-alias",
                "--project-root", str(self.remote),
                "--project-id", "fixture-project",
                "--channel", str(self.channel),
                "check",
            ],
            env=self.env,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cannot override the bundled", result.stderr)

    def test_ensure_actions_repair_deleted_runtime_config_and_profile_state(self) -> None:
        installed = self.run_v2("install")
        self.assertEqual(installed.returncode, 0, installed.stderr)
        runtime_root = self.remote / ".codex" / "remote-v2"
        self.assertTrue((runtime_root / "current" / "session.sh").is_file())

        shutil.rmtree(runtime_root)
        reinstalled = self.run_v2("install")
        self.assertEqual(reinstalled.returncode, 0, reinstalled.stderr)
        self.assertIn("REMOTE_V2_INSTALL_OK", reinstalled.stdout)
        self.assertTrue((runtime_root / "current" / "session.sh").is_file())

        configure_args = (
            "--lunch",
            "test-userdebug",
            "--product-out",
            "out/target/product/test",
            "--build-entry",
            "debug.sh",
        )
        configured = self.run_v2("configure", *configure_args)
        self.assertEqual(configured.returncode, 0, configured.stderr)
        config = runtime_root / "config.env"
        self.assertTrue(config.is_file())
        config.unlink()
        repaired_config = self.run_v2("configure", *configure_args)
        self.assertEqual(repaired_config.returncode, 0, repaired_config.stderr)
        self.assertIn("REMOTE_V2_CONFIG_OK", repaired_config.stdout)
        self.assertTrue(config.is_file())

        profile_args = (
            "--profile",
            "framework-services",
            "--modules",
            "services",
            "--artifact",
            "services=out/target/product/test/system/framework/services.jar|/system/framework/services.jar",
        )
        profiled = self.run_v2("profile-set", *profile_args)
        self.assertEqual(profiled.returncode, 0, profiled.stderr)
        profile = runtime_root / "profiles" / "framework-services.env"
        self.assertTrue(profile.is_file())
        profile.unlink()
        repaired_profile = self.run_v2("profile-set", *profile_args)
        self.assertEqual(repaired_profile.returncode, 0, repaired_profile.stderr)
        self.assertIn("REMOTE_V2_PROFILE_OK", repaired_profile.stdout)
        self.assertTrue(profile.is_file())

        states = self.remote / ".fake-channel"
        self.assertGreaterEqual(len(list(states.glob("remote-v2-install-preserve-*"))), 2)
        self.assertEqual(len(list(states.glob("remote-v2-configure-*"))), 2)
        self.assertEqual(len(list(states.glob("remote-v2-profile-set-*"))), 2)

    def test_single_uncertain_disconnect_attaches_to_same_ensure_command(self) -> None:
        self.env["FAKE_CHANNEL_DISCONNECT_ONCE"] = "1"
        installed = self.run_v2("install")
        self.assertEqual(installed.returncode, 0, installed.stderr)
        self.assertIn("COMMAND_ATTACHED", installed.stdout)
        self.assertIn("REMOTE_V2_INSTALL_OK", installed.stdout)
        states = list((self.remote / ".fake-channel").glob("remote-v2-install-preserve-*"))
        self.assertEqual(len(states), 1)
        self.assertEqual((self.remote / ".fake-channel-disconnect-once").read_text().strip(), states[0].name)

    def test_finite_wait_timeout_is_returned_without_silent_second_wait(self) -> None:
        self.env["FAKE_CHANNEL_TIMEOUT_ALWAYS"] = "1"
        result = self.run_v2("install")
        self.assertEqual(result.returncode, 124)
        self.assertIn("remote channel failed with 124", result.stderr)
        calls = (self.remote / ".fake-channel-timeouts").read_text().splitlines()
        self.assertEqual(len(calls), 1)
        self.assertFalse((self.remote / ".codex/remote-v2/current").exists())

    def test_long_project_id_keeps_safe_fixed_read_command_prefix(self) -> None:
        long_project_id = "A" + "." * 127
        result = self.run_v2("discover", project_id=long_project_id)
        self.assertEqual(result.returncode, 0, result.stderr)
        states = list((self.remote / ".fake-channel").iterdir())
        read_ids = [path.name for path in states if path.name.startswith("remote-v2-read-discover-")]
        self.assertEqual(len(read_ids), 1)
        self.assertRegex(read_ids[0], r"^remote-v2-read-discover-[0-9a-f]{24}$")

    def test_content_addressed_install_rejects_tampered_existing_release(self) -> None:
        installed = self.run_v2("install")
        self.assertEqual(installed.returncode, 0, installed.stderr)
        current = self.remote / ".codex/remote-v2/current"
        release = current.resolve()
        session = release / "session.sh"
        session.write_text(session.read_text(encoding="utf-8") + "# tampered\n", encoding="utf-8")
        tampered_sha = hashlib.sha256(session.read_bytes()).hexdigest()

        repeated = self.run_v2("install")
        self.assertEqual(repeated.returncode, 43)
        self.assertIn("REMOTE_V2_RELEASE_TAMPERED", repeated.stderr)
        self.assertEqual(hashlib.sha256(session.read_bytes()).hexdigest(), tampered_sha)
        self.assertEqual(current.resolve(), release)

    def test_content_addressed_install_rejects_payload_mode_tamper(self) -> None:
        installed = self.run_v2("install")
        self.assertEqual(installed.returncode, 0, installed.stderr)
        current = self.remote / ".codex/remote-v2/current"
        session = current.resolve() / "session.sh"
        session.chmod(0o600)

        repeated = self.run_v2("install")
        self.assertEqual(repeated.returncode, 43)
        self.assertIn("REMOTE_V2_RELEASE_TAMPERED", repeated.stderr)
        self.assertEqual(stat.S_IMODE(session.stat().st_mode), 0o600)

    def test_content_addressed_install_rejects_extra_release_inventory(self) -> None:
        installed = self.run_v2("install")
        self.assertEqual(installed.returncode, 0, installed.stderr)
        release = (self.remote / ".codex/remote-v2/current").resolve()
        extra = release / "sitecustomize.py"
        extra.write_text("raise RuntimeError('must never load')\n", encoding="utf-8")
        extra.chmod(0o600)

        repeated = self.run_v2("install")
        self.assertEqual(repeated.returncode, 43)
        self.assertIn("REMOTE_V2_RELEASE_TAMPERED", repeated.stderr)
        self.assertTrue(extra.is_file())

    def test_remote_v2_full_module_build_and_manifest_handoff(self) -> None:
        strict = self.run_v2("install", preserve=False)
        self.assertNotEqual(strict.returncode, 0)
        self.assertIn("LEGACY_WRAPPER_REVIEW_REQUIRED", strict.stderr)
        self.assertEqual(hashlib.sha256(self.legacy.read_bytes()).hexdigest(), self.legacy_sha)

        installed = self.run_v2("install")
        self.assertEqual(installed.returncode, 0, installed.stderr)
        self.assertIn("LEGACY_WRAPPER_PRESERVED", installed.stdout)
        self.assertEqual(hashlib.sha256(self.legacy.read_bytes()).hexdigest(), self.legacy_sha)
        capabilities = self.remote / ".codex/remote-v2/legacy-capabilities.env"
        self.assertIn("CAP_ARTIFACT_FRESHNESS=1", capabilities.read_text())

        discovered = self.run_v2("discover")
        self.assertEqual(discovered.returncode, 0, discovered.stderr)
        self.assertIn("LUNCH_TARGET=test-userdebug", discovered.stdout)
        self.assertIn("PRODUCT_OUT_DIR_REL=out/target/product/test", discovered.stdout)

        configured = self.run_v2(
            "configure",
            "--lunch",
            "test-userdebug",
            "--product-out",
            "out/target/product/test",
            "--build-entry",
            "debug.sh",
        )
        self.assertEqual(configured.returncode, 0, configured.stderr)

        inferred = subprocess.run(
            [
                sys.executable,
                str(self.entry),
                "--ssh-host",
                "fixture-alias",
                "--project-root",
                str(self.remote),
                "--working-subpath",
                "frameworks/base",
                "--project-id",
                "fixture-project",
                "--preserve-legacy",
                "infer-profile",
                "--path",
                "services/core/java/com/example/Example.java",
            ],
            env=self.env,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(inferred.returncode, 0, inferred.stderr)
        self.assertIn('"modules":["services"]', inferred.stdout)
        self.assertIn('"working_subpath":"frameworks/base"', inferred.stdout)

        profile = self.run_v2(
            "profile-set",
            "--profile",
            "framework-services",
            "--modules",
            "services",
            "--artifact",
            "services=out/target/product/test/system/framework/services.jar|/system/framework/services.jar",
        )
        self.assertEqual(profile.returncode, 0, profile.stderr)

        plan = self.run_v2("plan", "--profile", "framework-services")
        self.assertEqual(plan.returncode, 0, plan.stderr)
        self.assertIn("MODULES=services", plan.stdout)

        checkpoint = self.run_v2("checkpoint", "--name", "before-build", "--purpose", "fixture")
        self.assertEqual(checkpoint.returncode, 0, checkpoint.stderr)
        checkpoint_dir = self.remote / ".codex/remote-v2/checkpoints/before-build"
        self.assertTrue((checkpoint_dir / "staged.patch").is_file())
        self.assertTrue((checkpoint_dir / "unstaged.patch").is_file())
        with tarfile.open(checkpoint_dir / "untracked.tgz", "r:gz") as archive:
            self.assertIn("untracked/配置.txt", archive.getnames())

        built = self.run_v2(
            "build",
            "--profile",
            "framework-services",
            "--command-id",
            "fixture-build-001",
        )
        self.assertEqual(built.returncode, 0, built.stderr)
        self.assertIn("BUILD_OK", built.stdout)
        manifests = list((self.root / "handoff/manifests/fixture-build-001").glob("*.json"))
        self.assertEqual(len(manifests), 1)
        payload = json.loads(manifests[0].read_text())
        self.assertEqual(payload["module"], "services")
        self.assertEqual(payload["profile"], "framework-services")
        self.assertEqual(payload["command_id"], "fixture-build-001")
        self.assertEqual(payload["sha256"], hashlib.sha256((self.remote / "out/target/product/test/system/framework/services.jar").read_bytes()).hexdigest())

        attached = self.run_v2(
            "build",
            "--profile",
            "framework-services",
            "--command-id",
            "fixture-build-001",
        )
        self.assertEqual(attached.returncode, 0, attached.stderr)
        self.assertEqual((self.remote / ".build-count").read_text().strip(), "1")
        self.assertFalse(
            any(
                path.name == "__pycache__" or path.suffix == ".pyc"
                for path in (self.remote / ".codex/remote-v2/releases").rglob("*")
            )
        )


if __name__ == "__main__":
    unittest.main()
