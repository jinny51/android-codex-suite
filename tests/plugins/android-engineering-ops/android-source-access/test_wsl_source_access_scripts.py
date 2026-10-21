from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[4]
ENTRY_SKILL_DIR = REPO_ROOT / "plugins" / "android-engineering-ops" / "skills" / "android-source-access"
SKILL_DIR = (
    REPO_ROOT
    / "plugins/android-engineering-ops/adapters/source-access/wsl/skills/android-source-access"
)
SCRIPT_DIR = SKILL_DIR / "scripts"
INSPECTION_HELPER = (
    REPO_ROOT / "plugins" / "android-engineering-ops" / "lib" / "android_engineering_ops" / "remote_source_inspection.py"
)


def run_script(script: str, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    return subprocess.run(
        [str(SCRIPT_DIR / script), *args],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=merged_env,
    )


@pytest.mark.parametrize("has_local", [False, True])
def test_mount_credentials_use_ssh_account_samba_account_and_local_sudo_separately(
    tmp_path: Path, has_local: bool,
) -> None:
    skill = tmp_path / "adapter/skills/android-source-access"
    shutil.copytree(SKILL_DIR, skill)
    shutil.copytree(SKILL_DIR.parents[1] / "lib", tmp_path / "adapter/lib")
    scripts = skill / "scripts"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    def executable(path: Path, body: str) -> None:
        path.write_text("#!/usr/bin/env bash\nset -eu\n" + body)
        path.chmod(0o700)
    executable(fake_bin / "ssh", "if [ \"${1:-}\" = -G ]; then printf 'user alice\\nhostname physical-server\\n'; fi\nexit 0\n")
    executable(fake_bin / "mountpoint", "exit 1\n")
    executable(scripts / "discover-samba-share.sh",
               "[ -f \"$FAKE_CONFIGURED\" ] || exit 1\nprintf 'SAMBA_PROJECT_URL=//share-server/project\\nSAMBA_SHARE_URL=//share-server/project\\n'\n")
    executable(scripts / "ensure-samba-share.sh",
               "[ \"$CODEX_REMOTE_SUDO_PASSWORD\" = remote-sudo ] || exit 71\ntouch \"$FAKE_CONFIGURED\"\nprintf 'REMOTE_SUDO_AUTH mode=password\\n'\n")
    executable(scripts / "mount-platform.sh",
               "[ \"$SAMBA_PASSWORD\" = samba-only ] || exit 72\n"
               "[ \"$CODEX_LOCAL_SUDO_PASSWORD\" = \"$EXPECTED_LOCAL\" ] || exit 73\n"
               "printf 'SAMBA_AUTH mode=password\\n'\n")
    executable(scripts / "restore-project-mount.sh", "exit 0\n")
    home = tmp_path / "home"
    credentials = home / ".servers/credentials"
    credentials.mkdir(parents=True)
    def save(account: str, body: str) -> None:
        (credentials / f"{hashlib.sha256(account.encode()).hexdigest()}.passwords.env").write_text(body)
    save("alice@physical-server", "SAVED_SSH_PASSWORD=ssh-only\nSAVED_REMOTE_SUDO_PASSWORD=remote-sudo\nSAVED_LOCAL_SUDO_PASSWORD=wrong-account-local\n")
    save("bob@share-server", "SAVED_SAMBA_PASSWORD=samba-only\nSAVED_REMOTE_SUDO_PASSWORD=wrong-samba-sudo\n")
    save("bob@alias", "SAVED_REMOTE_SUDO_PASSWORD=wrong-path-account\n")
    if has_local:
        (credentials / "local-sudo.env").write_text("SAVED_LOCAL_SUDO_PASSWORD=local-only\n")
    env = os.environ.copy()
    env.update({"HOME": str(home), "PATH": f"{fake_bin}:{env['PATH']}", "SERVER_PASSWORD": "explicit-remote-default",
                "FAKE_CONFIGURED": str(tmp_path / "configured"), "EXPECTED_LOCAL": "local-only" if has_local else ""})
    result = subprocess.run([str(scripts / "mount-from-remote-path.sh"), "--remote-root", "/home/bob/product",
                             "--ssh-host", "alias", "--server-name", "share-server", "--no-sdk-inspect",
                             "--local-platform", "rk", "--sdk-name", "TVE1086U", "--no-save-passwords",
                             "--no-save-credentials"], env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "MOUNT_FROM_REMOTE_PATH_OK" in result.stdout
    for value in ("remote-sudo", "samba-only", "local-only", "wrong-account-local", "explicit-remote-default"):
        assert value not in result.stdout + result.stderr


def install_python_instrumentation(fake_bin: Path, prelude: str) -> None:
    python = fake_bin / "python3"
    python.write_text(
        f"#!{sys.executable}\n"
        "import sys\n"
        "script_arg = sys.argv[1]\n"
        "sys.argv = sys.argv[1:]\n"
        "if script_arg == '-':\n"
        "    source = sys.stdin.read()\n"
        "    filename = '<stdin>'\n"
        "else:\n"
        "    with open(script_arg, encoding='utf-8') as handle:\n"
        "        source = handle.read()\n"
        "    filename = script_arg\n"
        f"{prelude}\n"
        "namespace = {'__name__': '__main__', '__file__': filename}\n"
        "exec(compile(source, filename, 'exec'), namespace)\n",
        encoding="utf-8",
    )
    python.chmod(python.stat().st_mode | stat.S_IXUSR)


def make_restore_environment(root: Path) -> tuple[dict[str, str], Path, Path, Path]:
    fake_bin = root / "bin"
    fake_bin.mkdir()
    findmnt = fake_bin / "findmnt"
    findmnt.write_text(
        "#!/usr/bin/env bash\n"
        "field=''\n"
        "while [ \"$#\" -gt 0 ]; do\n"
        "  if [ \"$1\" = -o ]; then field=\"$2\"; break; fi\n"
        "  shift\n"
        "done\n"
        "case \"$field\" in\n"
        "  SOURCE) printf '%s\\n' \"$FAKE_FINDMNT_SOURCE\" ;;\n"
        "  TARGET) printf '%s\\n' \"$FAKE_FINDMNT_TARGET\" ;;\n"
        "  FSTYPE) printf '%s\\n' cifs ;;\n"
        "  OPTIONS) printf '%s\\n' 'rw,username=member,vers=3.0' ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    findmnt.chmod(findmnt.stat().st_mode | stat.S_IXUSR)
    home = root / "home"
    target = home / "work" / "unisoc"
    project = target / "TVE1088U"
    (project / "build").mkdir(parents=True)
    registry_dir = home / ".servers" / "projects"
    credentials_dir = home / ".servers" / "credentials"
    key = hashlib.sha256(b"member@server").hexdigest()
    registry_file = registry_dir / f"{key}.env"
    credential_file = credentials_dir / f"{key}.cred"
    env = {
        "HOME": str(home),
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "FAKE_FINDMNT_SOURCE": "//server/share",
        "FAKE_FINDMNT_TARGET": str(target),
    }
    return env, project, registry_file, credential_file


def remember_args(project: Path, registry_file: Path, credential_file: Path) -> list[str]:
    return [
        "--project",
        str(project),
        "--remember-current",
        "--ssh-host",
        "builder",
        "--remote-root",
        f"/srv/android/{project.name}",
        "--platform",
        "unisoc",
        "--sdk-name",
        project.name,
        "--registry-dir",
        str(registry_file.parent),
        "--credentials-dir",
        str(credential_file.parent),
    ]


class WslSourceAccessScriptsTests(unittest.TestCase):
    def test_registry_replace_failure_preserves_previous_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env, project, registry_file, credential_file = make_restore_environment(root)
            install_python_instrumentation(
                root / "bin",
                "import os\n"
                "def injected_replace_failure(*_args, **_kwargs):\n"
                "    raise OSError('injected replace failure')\n"
                "os.replace = injected_replace_failure",
            )
            registry_file.parent.mkdir(parents=True)
            original = b"SAMBA_SERVER=server\nSAMBA_USER=member\nPROJECT_PATHS=()\n"
            registry_file.write_bytes(original)

            result = run_script(
                "restore-project-mount.sh",
                *remember_args(project, registry_file, credential_file),
                env=env,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(registry_file.read_bytes(), original)

    def test_credential_replace_failure_preserves_previous_secret_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env, project, registry_file, credential_file = make_restore_environment(root)
            install_python_instrumentation(
                root / "bin",
                "import os\n"
                "def injected_replace_failure(*_args, **_kwargs):\n"
                "    raise OSError('injected replace failure')\n"
                "os.replace = injected_replace_failure",
            )
            env["SAMBA_PASSWORD"] = "do-not-log-this-secret"
            registry_file.parent.mkdir(parents=True)
            credential_file.parent.mkdir(parents=True)
            registry_original = b"SAMBA_SERVER=server\nSAMBA_USER=member\nPROJECT_PATHS=()\n"
            credential_original = b"username=member\npassword=previous-secret\n"
            registry_file.write_bytes(registry_original)
            credential_file.write_bytes(credential_original)

            result = run_script(
                "restore-project-mount.sh",
                *remember_args(project, registry_file, credential_file),
                "--remember-password",
                env=env,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(registry_file.read_bytes(), registry_original)
            self.assertEqual(credential_file.read_bytes(), credential_original)
            self.assertNotIn("do-not-log-this-secret", result.stdout + result.stderr)

    @unittest.skipUnless(shutil.which("flock"), "flock is required for registry locking")
    def test_concurrent_registry_updates_keep_both_projects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env, first_project, registry_file, credential_file = make_restore_environment(root)
            second_project = first_project.parent / "TVA10A2R"
            (second_project / "build").mkdir(parents=True)
            barrier = root / "barrier"
            barrier.mkdir()
            env["AKBS_STATE_TEST_BARRIER"] = str(barrier)
            registry_file.parent.mkdir(parents=True)
            registry_file.write_text(
                "SAMBA_SERVER=server\n"
                "SAMBA_USER=member\n"
                "PROJECT_PATHS=()\n"
                "SAMBA_PROJECT_SHARES=()\n"
                "PREFERRED_VERS_LIST=()\n"
                "REMOTE_SSH_HOSTS=()\n"
                "REMOTE_ROOTS=()\n"
                "PLATFORMS=()\n"
                "SDK_NAMES=()\n"
                "touch \"$AKBS_STATE_TEST_BARRIER/$$\"\n"
                "for _akbs_wait in $(seq 1 100); do\n"
                "  [ \"$(find \"$AKBS_STATE_TEST_BARRIER\" -type f | wc -l)\" -ge 2 ] && break\n"
                "  sleep 0.01\n"
                "done\n",
                encoding="utf-8",
            )
            merged_env = os.environ.copy()
            merged_env.update(env)
            commands = [
                [
                    str(SCRIPT_DIR / "restore-project-mount.sh"),
                    *remember_args(project, registry_file, credential_file),
                ]
                for project in (first_project, second_project)
            ]

            processes = [
                subprocess.Popen(
                    command,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=merged_env,
                )
                for command in commands
            ]
            results = [process.communicate(timeout=10) for process in processes]

            self.assertEqual([process.returncode for process in processes], [0, 0], results)
            inspect = subprocess.run(
                [
                    "bash",
                    "-c",
                    'source "$1"; printf "%s\\n" "${PROJECT_PATHS[@]}"',
                    "bash",
                    str(registry_file),
                ],
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(inspect.returncode, 0, inspect.stderr)
            self.assertEqual(set(inspect.stdout.splitlines()), {str(first_project), str(second_project)})

    @unittest.skipUnless(shutil.which("flock"), "flock is required for registry locking")
    def test_remembered_registry_and_credentials_keep_existing_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env, project, registry_file, credential_file = make_restore_environment(root)
            env["SAMBA_PASSWORD"] = "stored-secret"

            result = run_script(
                "restore-project-mount.sh",
                *remember_args(project, registry_file, credential_file),
                "--remember-password",
                env=env,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                credential_file.read_text(encoding="utf-8"),
                "username=member\npassword=stored-secret\n",
            )
            registry = registry_file.read_text(encoding="utf-8")
            self.assertIn("SAMBA_SERVER=server\n", registry)
            self.assertIn("SAMBA_USER=member\n", registry)
            self.assertIn(f"PROJECT_PATHS=( {project} )\n", registry)
            self.assertIn("PROJECT_IDENTITY_SCHEMAS=( android-remote-project-identity-v1 )\n", registry)
            self.assertIn("PROJECT_IDS=( unisoc-TVE1088U )\n", registry)
            self.assertIn("MOUNT_TRANSPORTS=( cifs )\n", registry)
            self.assertIn(f"ARTIFACT_BRIDGE_PATHS=( {project} )\n", registry)
            self.assertIn(f"SAMBA_CREDENTIALS_FILE={credential_file}\n", registry)
            self.assertEqual(stat.S_IMODE(registry_file.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(credential_file.stat().st_mode), 0o600)
            self.assertNotIn("stored-secret", result.stdout + result.stderr)

    def test_runtime_scripts_are_executable_and_bash_valid(self) -> None:
        for script in sorted(SCRIPT_DIR.glob("*.sh")):
            self.assertTrue(script.stat().st_mode & stat.S_IXUSR, script)
            result = subprocess.run(
                ["bash", "-n", str(script)],
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(result.returncode, 0, f"{script}: {result.stderr}")

    def test_remote_path_does_not_invent_platform_or_project(self) -> None:
        result = run_script(
            "plan-from-remote-path.sh",
            "--remote-root",
            "/home/test55/work/unisoc/rk3576",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("REMOTE_ROOT=/home/test55/work/unisoc/rk3576", result.stdout)
        self.assertIn("REMOTE_USER=test55", result.stdout)
        self.assertRegex(result.stdout, r"(?m)^PLATFORM=(?:''|)$")
        self.assertRegex(result.stdout, r"(?m)^SDK_NAME=(?:''|)$")

    def test_explicit_platform_and_project_define_wsl_work_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            result = run_script(
                "plan-from-remote-path.sh",
                "--remote-root",
                "/home/test55/work/unisoc/rk3576",
                "--local-platform",
                "rk",
                "--sdk-name",
                "TVA10A2R",
                env={"HOME": str(home)},
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("PLATFORM=rk", result.stdout)
            self.assertIn("SDK_NAME=TVA10A2R", result.stdout)
            self.assertIn(f"LOCAL_PROJECT={home}/work/rk/TVA10A2R", result.stdout)

    def test_project_level_mount_is_default_and_conflicts_need_confirmation(self) -> None:
        ensure_help = run_script("ensure-samba-share.sh", "--help")
        mount_help = run_script("mount-from-remote-path.sh", "--help")
        invalid_accept = run_script(
            "inspect-android-sdk.sh",
            "--ssh-host",
            "dummy",
            "--remote-root",
            "/home/test55/work/unisoc/rk3576",
            "--accept-platform-conflict",
        )

        self.assertEqual(ensure_help.returncode, 0, ensure_help.stderr)
        self.assertIn("Default share plan:", ensure_help.stdout)
        self.assertIn("[rk3576] path = /home/test55/work/unisoc/rk3576", ensure_help.stdout)
        self.assertIn("Parent/platform shares are explicit exceptions", ensure_help.stdout)
        self.assertEqual(mount_help.returncode, 0, mount_help.stderr)
        self.assertIn("--accept-platform-conflict", mount_help.stdout)
        self.assertIn("--accept-sdk-name-conflict", mount_help.stdout)
        self.assertIn("This is the default", mount_help.stdout)
        self.assertEqual(invalid_accept.returncode, 2)
        self.assertIn("--accept-platform-conflict requires --platform", invalid_accept.stderr)

    def test_wsl_inspection_adapter_uses_fake_remote_channel(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            channel = root / "fake-channel.py"
            log = root / "channel.log"
            channel.write_text(
                f"#!{sys.executable}\n"
                "import os, pathlib, sys\n"
                "pathlib.Path(os.environ['FAKE_CHANNEL_LOG']).write_text('\\0'.join(sys.argv[1:]), encoding='utf-8')\n"
                "print('COMMAND_STARTED id=fake session=codex-android-0123456789abcdef')\n"
                "print('REMOTE_ROOT=/srv/android/TVA10A2R')\n"
                "print('PLATFORM=rk')\n"
                "print('SDK_NAME=TVA10A2R')\n"
                "print('SOURCE_PLATFORM=rk')\n"
                "print('SOURCE_SDK_NAME=TVA10A2R')\n"
                "print('SOURCE_SDK_SOURCE=project_branch')\n",
                encoding="utf-8",
            )
            channel.chmod(0o755)

            result = run_script(
                "inspect-android-sdk.sh",
                "--ssh-host",
                "builder",
                "--remote-root",
                "/srv/android/TVA10A2R",
                "--channel-script",
                str(channel),
                "--inspection-helper",
                str(INSPECTION_HELPER),
                env={"FAKE_CHANNEL_LOG": str(log)},
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("INSPECTION_TRANSPORT=android-remote-channel-v2", result.stdout)
            self.assertIn("PROJECT_ID=rk-TVA10A2R", result.stdout)
            self.assertIn("run", log.read_text(encoding="utf-8"))

    def test_project_level_dry_run_targets_home_work_platform_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            fake_mount = fake_bin / "mount.cifs"
            fake_mount.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
            fake_mount.chmod(fake_mount.stat().st_mode | stat.S_IXUSR)
            target = root / "home" / "work" / "rk" / "TVA10A2R"

            result = run_script(
                "mount-platform.sh",
                "--platform",
                "rk",
                "--share",
                "//192.168.100.6/TVA10A2R",
                "--target",
                str(target),
                "--user",
                "test55",
                "--dry-run",
                env={"PATH": f"{fake_bin}:{os.environ['PATH']}"},
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(str(target), result.stdout)
            self.assertIn("//192.168.100.6/TVA10A2R", result.stdout)

    def test_runtime_skill_has_no_embedded_test_or_stale_output_framework(self) -> None:
        stale_terms = (
            "initial platform mount",
            "SAMBA_PROJECT_URL is for reference",
            "share-or-sdk",
            "Use `SAMBA_SHARE_URL`",
            "Capability Capture Candidate",
            "Pattern:",
            "Store in:",
            "Persist?",
        )
        self.assertFalse((SCRIPT_DIR / "validate-skill.sh").exists())
        for path in SKILL_DIR.rglob("*"):
            if path.is_file():
                text = path.read_text(encoding="utf-8")
                for term in stale_terms:
                    self.assertNotIn(term, text, f"{path}: {term}")


if __name__ == "__main__":
    unittest.main()
