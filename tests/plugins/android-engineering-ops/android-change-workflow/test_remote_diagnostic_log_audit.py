from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[4]
SCRIPT = REPO_ROOT / "plugins/android-engineering-ops/skills/android-change-workflow/scripts/diagnostic_log_audit.py"
PLUGIN_SOURCE = REPO_ROOT / "plugins/android-engineering-ops"


class RemoteDiagnosticAuditTests(unittest.TestCase):
    def make_fake_channel(self, root: Path, *, result: int = 0) -> tuple[Path, Path]:
        log = root / "channel-args.json"
        channel = root / "remote-channel"
        channel.write_text(
            "#!" + sys.executable + "\n"
            "import json, os, pathlib, sys\n"
            "pathlib.Path(os.environ['CHANNEL_LOG']).write_text(json.dumps(sys.argv[1:]))\n"
            f"raise SystemExit({result})\n",
            encoding="utf-8",
        )
        channel.chmod(channel.stat().st_mode | stat.S_IXUSR)
        return channel, log

    def install_fixture(self, root: Path, channel: Path) -> tuple[Path, Path, Path]:
        codex_home = root / "codex-home"
        source = root / "marketplace-source/plugins/android-engineering-ops"
        shutil.copytree(
            PLUGIN_SOURCE,
            source,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        bundled = source / "skills/android-remote-channel/scripts/remote-channel.sh"
        bundled.write_bytes(channel.read_bytes())
        bundled.chmod(bundled.stat().st_mode | stat.S_IXUSR)
        runtime = (
            codex_home
            / "plugins/cache/android-codex-suite/android-engineering-ops/2.0.2"
        )
        runtime.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, runtime)
        bin_dir = root / "bin"
        bin_dir.mkdir()
        inventory = {
            "installed": [
                {
                    "pluginId": "android-engineering-ops@android-codex-suite",
                    "name": "android-engineering-ops",
                    "marketplaceName": "android-codex-suite",
                    "version": "2.0.2",
                    "installed": True,
                    "enabled": True,
                    "source": {"source": "local", "path": str(source)},
                }
            ]
        }
        codex = bin_dir / "codex"
        codex.write_text(
            "#!/usr/bin/env python3\n"
            "import json\n"
            f"print(json.dumps({inventory!r}, sort_keys=True))\n",
            encoding="utf-8",
        )
        codex.chmod(codex.stat().st_mode | stat.S_IXUSR)
        script = runtime / "skills/android-change-workflow/scripts/diagnostic_log_audit.py"
        return script, bin_dir, codex_home

    def run_audit(
        self, script: Path, bin_dir: Path, codex_home: Path, log: Path, *extra: str
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["CHANNEL_LOG"] = str(log)
        env["CODEX_HOME"] = str(codex_home)
        env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return subprocess.run(
            [
                sys.executable,
                str(script),
                "--ssh-host",
                "builder",
                "--remote-root",
                "/srv/android/project",
                *extra,
            ],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )

    def test_routes_bounded_scan_through_nonexclusive_channel(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            channel, log = self.make_fake_channel(root)
            script, bin_dir, codex_home = self.install_fixture(root, channel)
            result = self.run_audit(
                script,
                bin_dir,
                codex_home,
                log,
                "--path",
                "frameworks/base/services/core/java/Test.java",
                "--include-logs",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            arguments = json.loads(log.read_text(encoding="utf-8"))
            self.assertEqual(arguments[:4], ["--ssh-host", "builder", "--remote-root", "/srv/android/project"])
            self.assertIn("run", arguments)
            self.assertEqual(arguments[arguments.index("--lock") + 1], "none")
            self.assertRegex(arguments[arguments.index("--command-id") + 1], r"^diagnostic-audit-")
            payload = arguments[-1]
            self.assertIn("frameworks/base/services/core/java/Test.java", payload)
            self.assertIn("include_logs = sys.argv[2] == \"1\"", payload)

    def test_rejects_absolute_and_parent_paths_before_channel(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            channel, log = self.make_fake_channel(root)
            script, bin_dir, codex_home = self.install_fixture(root, channel)
            for value in ("/etc/passwd", "../frameworks/base", "frameworks/../base"):
                with self.subTest(value=value):
                    result = self.run_audit(
                        script, bin_dir, codex_home, log, "--path", value
                    )
                    self.assertNotEqual(result.returncode, 0)
            self.assertFalse(log.exists())

    def test_propagates_remote_audit_finding_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            channel, log = self.make_fake_channel(root, result=1)
            script, bin_dir, codex_home = self.install_fixture(root, channel)
            result = self.run_audit(
                script,
                bin_dir,
                codex_home,
                log,
                "--path",
                "frameworks/base/Test.java",
            )
            self.assertEqual(result.returncode, 1)

    def test_rejects_public_channel_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            channel, log = self.make_fake_channel(root)
            script, bin_dir, codex_home = self.install_fixture(root, channel)
            result = self.run_audit(
                script,
                bin_dir,
                codex_home,
                log,
                "--path",
                "frameworks/base/Test.java",
                "--channel-script",
                "/bin/true",
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("cannot override the bundled", result.stderr)
            self.assertFalse(log.exists())


if __name__ == "__main__":
    unittest.main()
