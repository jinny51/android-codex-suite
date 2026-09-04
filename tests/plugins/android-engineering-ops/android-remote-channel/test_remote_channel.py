from __future__ import annotations

import json
import os
import shlex
import shutil
import signal
import stat
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]
SCRIPT = (
    REPO_ROOT
    / "plugins"
    / "android-engineering-ops"
    / "skills"
    / "android-remote-channel"
    / "scripts"
    / "remote-channel.sh"
)
INSTALLED_RUNTIME_ENTRYPOINTS = ("SCRIPT",)


def make_fake_remote(
    root: Path,
    *,
    remote_root_name: str = "android source",
) -> tuple[dict[str, str], Path]:
    fake_bin = root / "bin"
    fake_bin.mkdir()
    remote_home = root / "remote-home"
    remote_root = remote_home / remote_root_name
    remote_root.mkdir(parents=True)
    ssh_log = root / "ssh.log"
    tmux_log = root / "tmux.log"

    ssh = fake_bin / "ssh"
    ssh.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "command=''\n"
        "for argument in \"$@\"; do command=\"$argument\"; done\n"
        "printf '%s\\n' \"$command\" >> \"$FAKE_SSH_LOG\"\n"
        "cd \"$FAKE_REMOTE_HOME\"\n"
        "HOME=\"$FAKE_REMOTE_HOME\" PATH=\"$FAKE_REMOTE_BIN:$FAKE_BASE_PATH\" "
        "CODEX_REMOTE_CHANNEL_SERVER_ID=\"$FAKE_SERVER_ID\" bash -c \"$command\"\n",
        encoding="utf-8",
    )

    realpath = fake_bin / "realpath"
    realpath.write_text(
        "#!/usr/bin/env python3\n"
        "import pathlib, sys\n"
        "print(pathlib.Path(sys.argv[-1]).resolve(strict=True))\n",
        encoding="utf-8",
    )

    flock = fake_bin / "flock"
    flock.write_text(
        "#!/usr/bin/env bash\n"
        "if [ \"${1:-}\" = -n ] && [ -f \"$FAKE_REMOTE_HOME/.fake-runner-lock-held\" ]; then\n"
        "  exit 1\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )

    tmux = fake_bin / "tmux"
    tmux.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "state=\"$FAKE_REMOTE_HOME/.fake-tmux\"\n"
        "mkdir -p \"$state\"\n"
        "printf '%q ' \"$@\" >> \"$FAKE_TMUX_LOG\"; printf '\\n' >> \"$FAKE_TMUX_LOG\"\n"
        "session_arg() {\n"
        "  local previous='' value=''\n"
        "  for item in \"$@\"; do\n"
        "    if [ \"$previous\" = -t ] || [ \"$previous\" = -s ]; then value=$item; fi\n"
        "    previous=$item\n"
        "  done\n"
        "  value=${value%%:*}; printf '%s' \"$value\"\n"
        "}\n"
        "case \"${1:-}\" in\n"
        "  has-session)\n"
        "    name=$(session_arg \"$@\"); pid_file=\"$state/$name.pid\"\n"
        "    [ -f \"$pid_file\" ] || exit 1\n"
        "    pid=$(cat \"$pid_file\")\n"
        "    if kill -0 \"$pid\" 2>/dev/null; then exit 0; fi\n"
        "    rm -f \"$pid_file\"; exit 1\n"
        "    ;;\n"
        "  list-panes)\n"
        "    name=$(session_arg \"$@\"); pid_file=\"$state/$name.pid\"\n"
        "    [ -f \"$pid_file\" ] || exit 1\n"
        "    pid=$(cat \"$pid_file\")\n"
        "    kill -0 \"$pid\" 2>/dev/null || exit 1\n"
        "    runner_pid=$(find \"$FAKE_REMOTE_HOME/.codex/android-remote-sessions\" -name runner.pid -type f -print -quit 2>/dev/null || true)\n"
        "    [ -z \"$runner_pid\" ] || pid=$(cat \"$runner_pid\")\n"
        "    printf '0 %s\\n' \"$pid\"\n"
        "    ;;\n"
        "  new-session)\n"
        "    shift; name=''; cwd=''; start=''\n"
        "    while [ $# -gt 0 ]; do\n"
        "      case \"$1\" in\n"
        "        -d) shift ;;\n"
        "        -s) name=$2; shift 2 ;;\n"
        "        -n) shift 2 ;;\n"
        "        -c) cwd=$2; shift 2 ;;\n"
        "        *) start=$1; shift ;;\n"
        "      esac\n"
        "    done\n"
        "    [ -n \"$name\" ] && [ -n \"$start\" ]\n"
        "    (cd \"$cwd\" && HOME=\"$FAKE_REMOTE_HOME\" "
        "PATH=\"$FAKE_REMOTE_BIN:$FAKE_BASE_PATH\" bash -c \"$start\") "
        ">>\"$state/$name.out\" 2>&1 </dev/null &\n"
        "    echo $! > \"$state/$name.pid\"\n"
        "    ;;\n"
        "  kill-session)\n"
        "    name=$(session_arg \"$@\"); pid_file=\"$state/$name.pid\"\n"
        "    if [ -f \"$pid_file\" ]; then\n"
        "      pid=$(cat \"$pid_file\"); pkill -TERM -P \"$pid\" 2>/dev/null || true\n"
        "      kill -TERM \"$pid\" 2>/dev/null || true\n"
        "      i=0; while kill -0 \"$pid\" 2>/dev/null && [ $i -lt 50 ]; do sleep 0.02; i=$((i+1)); done\n"
        "      kill -KILL \"$pid\" 2>/dev/null || true; rm -f \"$pid_file\"\n"
        "    fi\n"
        "    ;;\n"
        "  *) exit 0 ;;\n"
        "esac\n",
        encoding="utf-8",
    )

    for path in (ssh, realpath, flock, tmux):
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    local_home = root / "local-home"
    local_home.mkdir()
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(local_home),
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "FAKE_REMOTE_HOME": str(remote_home),
            "FAKE_REMOTE_BIN": str(fake_bin),
            "FAKE_BASE_PATH": os.environ["PATH"],
            "FAKE_SSH_LOG": str(ssh_log),
            "FAKE_TMUX_LOG": str(tmux_log),
            "FAKE_SERVER_ID": "stable-fake-machine-id",
            "CODEX_REMOTE_CHANNEL_SSH_MUX": "0",
        }
    )
    return env, remote_root


def run_channel(
    env: dict[str, str],
    remote_root: Path,
    action: str,
    *,
    host: str = "fake-alias",
    command_id: str = "",
    command: str = "true",
    lock: str = "none",
    no_wait: bool = False,
    wait_timeout: int = 10,
    script: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    script = SCRIPT if script is None else script
    arguments = [str(script), "--ssh-host", host, "--remote-root", str(remote_root), action]
    if action == "run":
        if no_wait:
            arguments.append("--no-wait")
        arguments.extend(["--lock", lock, "--wait-timeout", str(wait_timeout)])
        if command_id:
            arguments.extend(["--command-id", command_id])
        arguments.extend(["--", command])
    elif command_id and action in {"status", "tail"}:
        arguments.extend(["--command-id", command_id])
    return subprocess.run(
        arguments,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )


def state_root(root: Path) -> Path:
    roots = list((root / "remote-home" / ".codex" / "android-remote-sessions").iterdir())
    if len(roots) != 1:
        raise AssertionError(f"expected one canonical state root, got {roots}")
    return roots[0]


def wait_for(path: Path, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {path}")


def stop_runner(env: dict[str, str], remote_root: Path) -> None:
    run_channel(env, remote_root, "stop")


class RemoteChannelV2Tests(unittest.TestCase):
    def test_runner_waits_for_short_external_lock_holder_before_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env, remote_root = make_fake_remote(root)
            marker = root / "remote-home" / ".fake-runner-lock-held"
            marker.write_text("external holder\n", encoding="utf-8")
            release = threading.Timer(0.6, marker.unlink)
            release.start()
            started = time.monotonic()
            try:
                ensured = run_channel(env, remote_root, "ensure")
                elapsed = time.monotonic() - started
                self.assertEqual(ensured.returncode, 0, ensured.stderr)
                self.assertIn("reused=false", ensured.stdout)
                self.assertGreaterEqual(elapsed, 0.4)
                state = state_root(root)
                self.assertTrue((state / "runner.ready").is_file())
                self.assertTrue((state / "runner.active.sha256").is_file())
            finally:
                release.join(timeout=2)
                stop_runner(env, remote_root)

    def test_host_and_remote_root_single_quotes_are_data_not_remote_shell(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env, remote_root = make_fake_remote(
                root,
                remote_root_name="android '$(touch root-injected)' source",
            )
            host_marker = root / "host-injected"
            hostile_host = (
                f"fake';touch${{IFS}}{shlex.quote(str(host_marker))};printf${{IFS}}'"
            )
            try:
                checked = run_channel(env, remote_root, "check", host=hostile_host)
                ensured = run_channel(env, remote_root, "ensure", host=hostile_host)
                status = run_channel(env, remote_root, "status", host=hostile_host)
                self.assertEqual(checked.returncode, 0, checked.stderr)
                self.assertEqual(ensured.returncode, 0, ensured.stderr)
                self.assertEqual(status.returncode, 0, status.stderr)
                self.assertIn(hostile_host, checked.stdout)
                self.assertIn(str(remote_root.resolve()), checked.stdout)
                self.assertIn(str(remote_root.resolve()), ensured.stdout)
                self.assertIn(str(remote_root.resolve()), status.stdout)
                self.assertFalse(host_marker.exists())
                self.assertFalse((root / "remote-home" / "root-injected").exists())
            finally:
                stop_runner(env, remote_root)

    def test_local_host_and_root_control_characters_are_rejected_before_ssh(self) -> None:
        cases = (
            ("fake\nhost", None, "--ssh-host must not contain control characters"),
            ("-oProxyCommand=touch", None, "--ssh-host must be a host token"),
            ("two hosts", None, "--ssh-host must be a host token"),
            ("fake-host", "\tunsafe", "--remote-root must not contain control characters"),
            ("fake-host", "\x1bunsafe", "--remote-root must not contain control characters"),
        )
        for host, suffix, expected in cases:
            with self.subTest(host=host, suffix=repr(suffix)):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    env, remote_root = make_fake_remote(root)
                    candidate = remote_root if suffix is None else Path(str(remote_root) + suffix)
                    result = run_channel(env, candidate, "check", host=host)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(expected, result.stderr)
                    self.assertFalse((root / "ssh.log").exists())

    def test_canonical_root_control_character_from_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env, unsafe_root = make_fake_remote(
                root,
                remote_root_name="android\tcanonical",
            )
            alias_root = root / "safe-root-alias"
            alias_root.symlink_to(unsafe_root, target_is_directory=True)
            result = run_channel(env, alias_root, "check")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("REMOTE_ROOT_UNSAFE control-character", result.stderr)
            self.assertFalse((root / "remote-home" / ".codex").exists())

    def test_canonical_identity_ignores_alias_and_symlink_and_uses_private_modes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env, remote_root = make_fake_remote(root)
            alias_root = root / "remote-root-alias"
            alias_root.symlink_to(remote_root, target_is_directory=True)
            try:
                first = run_channel(env, remote_root, "ensure", host="friendly-name")
                second = run_channel(env, alias_root, "ensure", host="user@192.0.2.20")
                self.assertEqual(first.returncode, 0, first.stderr)
                self.assertEqual(second.returncode, 0, second.stderr)
                self.assertIn("runner=codex-android-", first.stdout)
                self.assertIn("reused=true", second.stdout)

                state = state_root(root)
                self.assertEqual(stat.S_IMODE(state.stat().st_mode), 0o700)
                self.assertEqual(stat.S_IMODE((state / "commands").stat().st_mode), 0o700)
                self.assertEqual(stat.S_IMODE((state / "session.env").stat().st_mode), 0o600)
                session_file = state / "session.env"
                session = session_file.read_text(encoding="utf-8")
                sourced = subprocess.run(
                    [
                        "bash",
                        "-c",
                        'source "$1"; printf "%s\\n%s\\n" "$CANONICAL_ROOT" "$STATE_DIR"',
                        "bash",
                        str(session_file),
                    ],
                    check=True,
                    text=True,
                    stdout=subprocess.PIPE,
                    env={**os.environ, "HOME": str(root / "remote-home")},
                ).stdout.splitlines()
                self.assertEqual(sourced[0], str(remote_root.resolve()))
                self.assertEqual(sourced[1], str(state))
                self.assertNotIn("friendly-name", session)
                locks = list((root / "remote-home" / ".codex" / "android-remote-locks").glob("*.lock"))
                self.assertEqual(len(locks), 1)
                self.assertEqual(stat.S_IMODE(locks[0].stat().st_mode), 0o600)
                self.assertNotIn("send-keys", (root / "tmux.log").read_text(encoding="utf-8"))
                self.assertIn("-n runner", (root / "tmux.log").read_text(encoding="utf-8"))
            finally:
                stop_runner(env, remote_root)

    def test_same_id_attaches_without_rerun_and_conflicting_payload_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env, remote_root = make_fake_remote(root)
            counter = remote_root / "counter"
            command = f"printf x >> {shlex.quote(str(counter))}; sleep 1; printf done"
            try:
                first = run_channel(env, remote_root, "run", command_id="stable-command", command=command, no_wait=True)
                attached = run_channel(env, remote_root, "run", command_id="stable-command", command=command, wait_timeout=5)
                conflict = run_channel(env, remote_root, "run", command_id="stable-command", command="printf different", no_wait=True)
                self.assertEqual(first.returncode, 0, first.stderr)
                self.assertEqual(attached.returncode, 0, attached.stderr)
                self.assertIn("COMMAND_ATTACHED id=stable-command", attached.stdout)
                self.assertIn("__CODEX_CMD_DONE id=stable-command state=completed rc=0", attached.stdout)
                self.assertEqual(counter.read_text(encoding="utf-8"), "x")
                self.assertEqual(conflict.returncode, 4)
                self.assertIn("COMMAND_ID_CONFLICT", conflict.stderr)

                commands = state_root(root) / "commands"
                for suffix in ("queued", "running", "completed", "terminal", "exit", "line"):
                    self.assertTrue((commands / f"stable-command.{suffix}").exists(), suffix)
                self.assertEqual((commands / "stable-command.terminal").read_text().strip(), "completed")
                self.assertEqual(stat.S_IMODE((commands / "stable-command.line").stat().st_mode), 0o600)
            finally:
                stop_runner(env, remote_root)

    def test_queue_sequence_preserves_registration_fifo_not_command_id_sort(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env, remote_root = make_fake_remote(root)
            order = remote_root / "queue-order"
            blocker_command = "sleep 2"
            first_command = f"printf 'z-first\\n' >> {shlex.quote(str(order))}"
            second_command = f"printf 'a-second\\n' >> {shlex.quote(str(order))}"
            try:
                blocker = run_channel(
                    env,
                    remote_root,
                    "run",
                    command_id="queue-blocker",
                    command=blocker_command,
                    no_wait=True,
                )
                self.assertEqual(blocker.returncode, 0, blocker.stderr)
                commands = state_root(root) / "commands"
                wait_for(commands / "queue-blocker.running")

                registered_first = run_channel(
                    env,
                    remote_root,
                    "run",
                    command_id="z-registered-first",
                    command=first_command,
                    no_wait=True,
                )
                registered_second = run_channel(
                    env,
                    remote_root,
                    "run",
                    command_id="a-registered-second",
                    command=second_command,
                    no_wait=True,
                )
                self.assertEqual(registered_first.returncode, 0, registered_first.stderr)
                self.assertEqual(registered_second.returncode, 0, registered_second.stderr)

                attached = run_channel(
                    env,
                    remote_root,
                    "run",
                    command_id="a-registered-second",
                    command=second_command,
                    wait_timeout=8,
                )
                self.assertEqual(attached.returncode, 0, attached.stderr)
                self.assertEqual(order.read_text(encoding="utf-8"), "z-first\na-second\n")
                first_sequence = int(
                    (commands / "z-registered-first.queue-seq").read_text().strip()
                )
                second_sequence = int(
                    (commands / "a-registered-second.queue-seq").read_text().strip()
                )
                self.assertLess(first_sequence, second_sequence)
            finally:
                stop_runner(env, remote_root)

    def test_incomplete_registration_is_never_run_and_same_id_recovers_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env, remote_root = make_fake_remote(root)
            stale_output = remote_root / "stale-output"
            counter = remote_root / "recovered-counter"
            try:
                ensured = run_channel(env, remote_root, "ensure")
                self.assertEqual(ensured.returncode, 0, ensured.stderr)
                commands = state_root(root) / "commands"
                command_id = "recover-half-registration"
                (commands / f"{command_id}.line").write_text(
                    f"printf stale > {shlex.quote(str(stale_output))}", encoding="utf-8"
                )
                (commands / f"{command_id}.queued").write_text("at=1\n", encoding="utf-8")
                (commands / f"{command_id}.request.sha256").write_text("partial\n", encoding="utf-8")
                for path in commands.glob(f"{command_id}.*"):
                    path.chmod(0o600)

                time.sleep(1.2)
                self.assertFalse(stale_output.exists(), "runner consumed an uncommitted request")

                command = f"printf x >> {shlex.quote(str(counter))}"
                recovered = run_channel(
                    env, remote_root, "run", command_id=command_id, command=command
                )
                attached = run_channel(
                    env, remote_root, "run", command_id=command_id, command=command
                )
                self.assertEqual(recovered.returncode, 0, recovered.stderr)
                self.assertEqual(attached.returncode, 0, attached.stderr)
                self.assertIn("COMMAND_ATTACHED", attached.stdout)
                self.assertEqual(counter.read_text(encoding="utf-8"), "x")
                self.assertEqual(
                    (commands / f"{command_id}.line").read_text(encoding="utf-8"), command
                )
                self.assertTrue((commands / f"{command_id}.request-complete").is_file())
                self.assertEqual(
                    stat.S_IMODE((commands / f"{command_id}.request-complete").stat().st_mode),
                    0o600,
                )
            finally:
                stop_runner(env, remote_root)

    def test_failed_command_has_immutable_failed_terminal_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env, remote_root = make_fake_remote(root)
            try:
                result = run_channel(env, remote_root, "run", command_id="known-failure", command="printf failure; exit 17", lock="exclusive")
                self.assertEqual(result.returncode, 17, result.stdout + result.stderr)
                self.assertIn("state=failed rc=17", result.stdout)
                commands = state_root(root) / "commands"
                self.assertEqual((commands / "known-failure.terminal").read_text().strip(), "failed")
                self.assertEqual((commands / "known-failure.exit").read_text().strip(), "17")
                self.assertLessEqual(
                    (commands / "known-failure.exit").stat().st_mtime_ns,
                    (commands / "known-failure.terminal").stat().st_mtime_ns,
                )
                self.assertTrue((commands / "known-failure.failed").is_file())
                self.assertFalse((state_root(root) / "busy").exists())
            finally:
                stop_runner(env, remote_root)

    def test_runner_environment_and_functions_persist_across_command_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env, remote_root = make_fake_remote(root)
            try:
                initialized = run_channel(
                    env,
                    remote_root,
                    "run",
                    command_id="initialize-shell",
                    command=(
                        "export CODEX_PERSIST=ok; "
                        "persist_fn() { printf 'fn:%s' \"$CODEX_PERSIST\"; }"
                    ),
                )
                reused = run_channel(
                    env,
                    remote_root,
                    "run",
                    command_id="reuse-shell",
                    command="printf '%s|' \"$CODEX_PERSIST\"; persist_fn",
                )
                self.assertEqual(initialized.returncode, 0, initialized.stderr)
                self.assertEqual(reused.returncode, 0, reused.stderr)
                self.assertIn("ok|fn:ok", reused.stdout)
                self.assertIn("state=completed rc=0", reused.stdout)
            finally:
                stop_runner(env, remote_root)

    def test_wait_timeout_can_later_attach_to_same_running_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env, remote_root = make_fake_remote(root)
            counter = remote_root / "timeout-counter"
            command = f"printf x >> {shlex.quote(str(counter))}; sleep 2; printf finished"
            try:
                timed_out = run_channel(env, remote_root, "run", command_id="slow-command", command=command, wait_timeout=1)
                attached = run_channel(env, remote_root, "run", command_id="slow-command", command=command, wait_timeout=5)
                self.assertEqual(timed_out.returncode, 124)
                self.assertIn("COMMAND_WAIT_TIMEOUT", timed_out.stderr)
                self.assertEqual(attached.returncode, 0, attached.stderr)
                self.assertIn("COMMAND_ATTACHED", attached.stdout)
                self.assertEqual(counter.read_text(encoding="utf-8"), "x")
            finally:
                stop_runner(env, remote_root)

    def test_stop_marks_running_command_aborted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env, remote_root = make_fake_remote(root)
            first = run_channel(env, remote_root, "run", command_id="abort-me", command="sleep 30", no_wait=True)
            self.assertEqual(first.returncode, 0, first.stderr)
            commands = state_root(root) / "commands"
            wait_for(commands / "abort-me.running")
            stopped = run_channel(env, remote_root, "stop")
            self.assertEqual(stopped.returncode, 0, stopped.stderr)
            wait_for(commands / "abort-me.terminal")
            self.assertEqual((commands / "abort-me.terminal").read_text().strip(), "aborted")
            self.assertEqual((commands / "abort-me.exit").read_text().strip(), "130")

    def test_stop_never_overwrites_an_existing_completed_terminal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env, remote_root = make_fake_remote(root)
            completed = run_channel(
                env, remote_root, "run", command_id="already-complete", command="true"
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            commands = state_root(root) / "commands"
            stopped = run_channel(env, remote_root, "stop")
            self.assertEqual(stopped.returncode, 0, stopped.stderr)
            self.assertEqual((commands / "already-complete.terminal").read_text().strip(), "completed")
            self.assertEqual((commands / "already-complete.exit").read_text().strip(), "0")

    def test_dead_runner_reconciles_running_command_to_lost(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env, remote_root = make_fake_remote(root)
            first = run_channel(env, remote_root, "run", command_id="lose-me", command="sleep 30", no_wait=True)
            self.assertEqual(first.returncode, 0, first.stderr)
            commands = state_root(root) / "commands"
            wait_for(commands / "lose-me.running")
            pid_files = list((root / "remote-home" / ".fake-tmux").glob("*.pid"))
            self.assertEqual(len(pid_files), 1)
            pid = int(pid_files[0].read_text().strip())
            os.kill(pid, signal.SIGKILL)
            pid_files[0].unlink(missing_ok=True)
            time.sleep(0.1)
            status_result = run_channel(env, remote_root, "status", command_id="lose-me")
            self.assertEqual(status_result.returncode, 0, status_result.stderr)
            self.assertIn("COMMAND_STATUS id=lose-me state=lost rc=125", status_result.stdout)
            self.assertEqual((commands / "lose-me.terminal").read_text().strip(), "lost")

    def test_tmux_session_with_stale_fixed_runner_pid_is_not_reported_running(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env, remote_root = make_fake_remote(root)
            command = "sleep 30"
            started = run_channel(
                env,
                remote_root,
                "run",
                command_id="stale-fixed-runner",
                command=command,
                no_wait=True,
            )
            self.assertEqual(started.returncode, 0, started.stderr)
            state = state_root(root)
            commands = state / "commands"
            wait_for(commands / "stale-fixed-runner.running")
            (state / "runner.pid").write_text("99999999\n", encoding="utf-8")

            status = run_channel(
                env,
                remote_root,
                "status",
                command_id="stale-fixed-runner",
            )
            self.assertEqual(status.returncode, 0, status.stderr)
            self.assertIn("SESSION_STATUS stopped", status.stdout)
            self.assertIn("state=lost rc=125", status.stdout)

            ensured = run_channel(env, remote_root, "ensure")
            self.assertEqual(ensured.returncode, 0, ensured.stderr)
            self.assertIn("reused=false", ensured.stdout)
            attached = run_channel(
                env,
                remote_root,
                "run",
                command_id="stale-fixed-runner",
                command=command,
                wait_timeout=2,
            )
            self.assertEqual(attached.returncode, 125)
            self.assertNotEqual(attached.returncode, 124)
            stop_runner(env, remote_root)

    def test_terminal_without_exit_is_repaired_before_attach_wait(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env, remote_root = make_fake_remote(root)
            command = "printf complete"
            try:
                completed = run_channel(
                    env,
                    remote_root,
                    "run",
                    command_id="repair-missing-exit",
                    command=command,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                commands = state_root(root) / "commands"
                exit_file = commands / "repair-missing-exit.exit"
                exit_file.unlink()
                self.assertTrue((commands / "repair-missing-exit.terminal").is_file())

                attached = run_channel(
                    env,
                    remote_root,
                    "run",
                    command_id="repair-missing-exit",
                    command=command,
                    wait_timeout=2,
                )
                self.assertEqual(attached.returncode, 0, attached.stderr)
                self.assertEqual(exit_file.read_text().strip(), "0")
                self.assertIn("COMMAND_ATTACHED", attached.stdout)
            finally:
                stop_runner(env, remote_root)

    def test_legacy_failed_terminal_without_exit_repairs_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env, remote_root = make_fake_remote(root)
            command = "exit 17"
            try:
                failed = run_channel(
                    env,
                    remote_root,
                    "run",
                    command_id="repair-failed-exit",
                    command=command,
                )
                self.assertEqual(failed.returncode, 17)
                commands = state_root(root) / "commands"
                (commands / "repair-failed-exit.exit").unlink()

                attached = run_channel(
                    env,
                    remote_root,
                    "run",
                    command_id="repair-failed-exit",
                    command=command,
                    wait_timeout=2,
                )
                self.assertEqual(attached.returncode, 1)
                self.assertIn("state=failed rc=1", attached.stdout)
                self.assertEqual(
                    (commands / "repair-failed-exit.exit").read_text().strip(),
                    "1",
                )
            finally:
                stop_runner(env, remote_root)

    def test_runner_payload_upgrade_blocks_active_then_restarts_when_idle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env, remote_root = make_fake_remote(root)
            command = "sleep 2; printf upgraded-safe"
            started = run_channel(
                env,
                remote_root,
                "run",
                command_id="upgrade-active-runner",
                command=command,
                no_wait=True,
            )
            self.assertEqual(started.returncode, 0, started.stderr)
            state = state_root(root)
            commands = state / "commands"
            wait_for(commands / "upgrade-active-runner.running")
            old_digest = (state / "runner.active.sha256").read_text().strip()

            upgraded_home = root / "upgraded-codex-home"
            upgraded_source = (
                root / "upgraded-marketplace/plugins/android-engineering-ops"
            )
            upgraded_cache = (
                upgraded_home
                / "plugins/cache/android-codex-suite/android-engineering-ops/2.0.1"
            )
            for upgraded_plugin in (upgraded_source, upgraded_cache):
                upgraded_plugin.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(
                    REPO_ROOT / "plugins/android-engineering-ops",
                    upgraded_plugin,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
                )
                upgraded_script = (
                    upgraded_plugin
                    / "skills/android-remote-channel/scripts/remote-channel.sh"
                )
                text = upgraded_script.read_text(encoding="utf-8").replace(
                    "PROTOCOL_VERSION=2\n",
                    "PROTOCOL_VERSION=2\n# isolated upgraded runner payload\n",
                    1,
                )
                upgraded_script.write_text(text, encoding="utf-8")
                upgraded_script.chmod(0o755)
                plugin_manifest = upgraded_plugin / ".codex-plugin/plugin.json"
                manifest = json.loads(plugin_manifest.read_text(encoding="utf-8"))
                manifest["version"] = "2.0.1"
                plugin_manifest.write_text(
                    json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            upgraded = (
                upgraded_cache
                / "skills/android-remote-channel/scripts/remote-channel.sh"
            )
            upgraded_bin = root / "upgraded-inventory-bin"
            upgraded_bin.mkdir()
            inventory = upgraded_bin / "codex"
            payload = {
                "installed": [
                    {
                        "pluginId": (
                            "android-engineering-ops@android-codex-suite"
                        ),
                        "name": "android-engineering-ops",
                        "marketplaceName": "android-codex-suite",
                        "version": "2.0.1",
                        "installed": True,
                        "enabled": True,
                        "source": {"source": "local", "path": str(upgraded_source)},
                    }
                ],
                "available": [],
            }
            inventory.write_text(
                "#!/usr/bin/env python3\n"
                "import json, sys\n"
                f"payload = {payload!r}\n"
                "if sys.argv[1:] != ['plugin', 'list', '--json']:\n"
                "    raise SystemExit(64)\n"
                "print(json.dumps(payload, sort_keys=True))\n",
                encoding="utf-8",
            )
            inventory.chmod(stat.S_IMODE(inventory.stat().st_mode) | stat.S_IXUSR)
            upgraded_env = dict(env)
            upgraded_env["CODEX_HOME"] = str(upgraded_home)
            upgraded_env["PATH"] = f"{upgraded_bin}{os.pathsep}{env['PATH']}"
            blocked = run_channel(
                upgraded_env, remote_root, "ensure", script=upgraded
            )
            self.assertEqual(blocked.returncode, 6)
            self.assertIn("RUNNER_UPGRADE_BLOCKED_ACTIVE", blocked.stderr)
            self.assertFalse((commands / "upgrade-active-runner.terminal").exists())
            self.assertEqual(
                (state / "runner.active.sha256").read_text().strip(),
                old_digest,
            )

            completed = run_channel(
                env,
                remote_root,
                "run",
                command_id="upgrade-active-runner",
                command=command,
                wait_timeout=6,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("upgraded-safe", completed.stdout)
            ensured = run_channel(
                upgraded_env, remote_root, "ensure", script=upgraded
            )
            self.assertEqual(ensured.returncode, 0, ensured.stderr)
            self.assertIn("reused=false", ensured.stdout)
            self.assertEqual(
                (commands / "upgrade-active-runner.terminal").read_text().strip(),
                "completed",
            )
            self.assertEqual((commands / "upgrade-active-runner.exit").read_text().strip(), "0")
            new_digest = (state / "runner.active.sha256").read_text().strip()
            self.assertNotEqual(new_digest, old_digest)
            self.assertEqual(
                new_digest,
                (state / "runner.expected.sha256").read_text().strip(),
            )
            stop_runner(upgraded_env, remote_root)

    def test_unsafe_command_id_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env, remote_root = make_fake_remote(root)
            result = run_channel(env, remote_root, "run", command_id="../escape", command="true", no_wait=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("unsafe --command-id", result.stderr)


if __name__ == "__main__":
    unittest.main()
