from __future__ import annotations

import copy
import json
import os
import socket
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Event
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[3]
PLUGIN = ROOT / "plugins/android-engineering-ops"
LIB = PLUGIN / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from android_engineering_ops import install_family, task_start  # noqa: E402


CURRENT = "2.0.3"
NEXT = "2.0.10"
TASK = "engineering-task-a"
UPGRADE = ["codex", "plugin", "marketplace", "upgrade", "android-codex-suite", "--json"]
ADD = ["codex", "plugin", "add", "android-engineering-ops@android-codex-suite", "--json"]
LIST = ["codex", "plugin", "list", "--json"]


class FakeInstallation:
    """A real source/cache identity with fake network and Codex boundaries."""

    def __init__(self, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.home = home
        self.source = home / ".tmp/marketplaces/android-codex-suite/plugins/android-engineering-ops"
        self.cache = home / "plugins/cache/android-codex-suite/android-engineering-ops"
        self.inventory: dict[str, Any] = {"installed": []}
        self.remote: dict[str, Any] = self.manifest(CURRENT)
        self.fetches = 0
        self.commands: list[list[str]] = []
        self.command_failure: list[str] | None = None
        self.update_mode = "valid"
        self.runtime = self.install(CURRENT)
        monkeypatch.setenv("CODEX_HOME", str(home))
        monkeypatch.setattr(install_family, "_read_active_inventory", self.read_inventory)
        monkeypatch.setattr(task_start, "_read_active_inventory", self.read_inventory)
        monkeypatch.setattr(task_start, "fetch_remote_manifest", self.fetch)
        monkeypatch.setattr(task_start, "run_codex", self.run)
        monkeypatch.setattr(socket, "create_connection", self.unexpected_network)

    @staticmethod
    def unexpected_network(*_args: Any, **_kwargs: Any) -> None:
        pytest.fail("startup tests must use the fake manifest boundary")

    @staticmethod
    def manifest(version: str) -> dict[str, Any]:
        return {
            "name": "android-engineering-ops",
            "version": version,
            "repository": "https://github.com/jinny51/android-codex-suite",
        }

    def install(self, version: str) -> Path:
        runtime = self.cache / version
        manifest = json.dumps(self.manifest(version), sort_keys=True)
        for root in (self.source, runtime):
            (root / ".codex-plugin").mkdir(parents=True, exist_ok=True)
            (root / ".codex-plugin/plugin.json").write_text(manifest, encoding="utf-8")
            (root / "README.md").write_text(f"Engineering core {version}\n", encoding="utf-8")
        self.inventory = {
            "installed": [
                {
                    "pluginId": "android-engineering-ops@android-codex-suite",
                    "name": "android-engineering-ops",
                    "marketplaceName": "android-codex-suite",
                    "version": version,
                    "installed": True,
                    "enabled": True,
                    "source": {"source": "local", "path": str(self.source)},
                }
            ]
        }
        return runtime

    def read_inventory(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return copy.deepcopy(self.inventory)

    def fetch(self) -> dict[str, Any]:
        self.fetches += 1
        return copy.deepcopy(self.remote)

    def run(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        self.commands.append(list(command))
        assert command in (UPGRADE, ADD, LIST), f"unexpected Codex command: {command}"
        if command == LIST:
            return subprocess.CompletedProcess(command, 0, json.dumps(self.inventory), "")
        if command == self.command_failure:
            return subprocess.CompletedProcess(command, 1, "", "simulated plugin command failure")
        if command == ADD and self.update_mode != "no_effect":
            runtime = self.install(str(self.remote["version"]))
            if self.update_mode == "hash_mismatch":
                (runtime / "README.md").write_text("stale cached content\n", encoding="utf-8")
            elif self.update_mode == "inactive":
                self.inventory["installed"][0]["enabled"] = False
            elif self.update_mode == "wrong_marketplace":
                self.inventory["installed"][0]["marketplaceName"] = "untrusted-marketplace"
        return subprocess.CompletedProcess(command, 0, "{}", "")

    @property
    def mutations(self) -> list[list[str]]:
        return [command for command in self.commands if command != LIST]

    def start(self, task_id: str = TASK, *, retry: bool = False, root: Path | None = None) -> dict[str, Any]:
        return task_start.ensure_task_started(
            root or self.runtime, task_id, retry=retry, codex_home=self.home
        )

    def state_path(self, task_id: str = TASK) -> Path:
        return self.home / "artifacts/android-engineering-ops/startup" / f"{task_id}.json"


@pytest.fixture
def installed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeInstallation:
    return FakeInstallation(tmp_path / "codex-home", monkeypatch)


def assert_result(result: dict[str, Any], status: str, *, reused: bool = False) -> None:
    assert result["status"] == status
    assert result["blocking"] is (status != "PASS")
    assert result["reused"] is reused


def test_current_plugin_passes_once_per_task_and_persists_the_result(installed: FakeInstallation) -> None:
    assert_result(installed.start(), "PASS")
    assert_result(installed.start(), "PASS", reused=True)
    assert_result(installed.start("engineering-task-b"), "PASS")

    assert installed.fetches == 2
    assert installed.mutations == []
    state = json.loads(installed.state_path().read_text(encoding="utf-8"))
    assert state["status"] == "PASS"
    assert installed.state_path("engineering-task-b").is_file()


def test_pass_retry_does_not_refresh_or_upgrade_in_the_middle_of_a_task(installed: FakeInstallation) -> None:
    assert_result(installed.start(), "PASS")
    installed.remote = installed.manifest(NEXT)

    assert_result(installed.start(retry=True), "PASS", reused=True)
    assert installed.fetches == 1
    assert installed.mutations == []


def test_locally_newer_plugin_is_not_downgraded(installed: FakeInstallation) -> None:
    installed.remote = installed.manifest("2.0.2")
    assert_result(installed.start(), "PASS")
    assert installed.mutations == []


def test_outdated_plugin_updates_only_engineering_then_requires_restart(installed: FakeInstallation) -> None:
    installed.remote = installed.manifest(NEXT)

    assert_result(installed.start(), "UPDATED_RESTART_REQUIRED")
    assert installed.mutations == [UPGRADE, ADD]
    assert installed.inventory["installed"][0]["version"] == NEXT
    assert json.loads(installed.state_path().read_text())["status"] == "UPDATED_RESTART_REQUIRED"
    # The old process is no longer bound to the active installation. It may
    # report the installation mismatch before reading its restart record.
    for retry in (False, True):
        old_runtime = installed.start(retry=retry)
        assert old_runtime["blocking"] is True
        assert old_runtime["status"] in {"UPDATED_RESTART_REQUIRED", "INSTALL_FAMILY_INVALID"}
    assert installed.fetches == 1
    assert installed.mutations == [UPGRADE, ADD]


def test_restarted_updated_runtime_can_recheck_the_same_task(installed: FakeInstallation) -> None:
    installed.remote = installed.manifest(NEXT)
    assert_result(installed.start(), "UPDATED_RESTART_REQUIRED")

    assert_result(installed.start(root=installed.cache / NEXT), "PASS")
    assert_result(installed.start(root=installed.cache / NEXT), "PASS", reused=True)
    assert installed.fetches == 2
    assert installed.mutations == [UPGRADE, ADD]
    assert installed.start()["blocking"] is True
    assert installed.fetches == 2


@pytest.mark.parametrize("failure", [TimeoutError("manifest request timed out"), OSError("offline")])
def test_manifest_failure_blocks_and_is_reused_until_explicit_retry(
    installed: FakeInstallation, monkeypatch: pytest.MonkeyPatch, failure: Exception,
) -> None:
    def unavailable() -> dict[str, Any]:
        installed.fetches += 1
        raise failure

    monkeypatch.setattr(task_start, "fetch_remote_manifest", unavailable)
    assert_result(installed.start(), "CHECK_FAILED")
    monkeypatch.setattr(task_start, "fetch_remote_manifest", installed.fetch)
    assert_result(installed.start(), "CHECK_FAILED", reused=True)
    assert installed.fetches == 1
    assert_result(installed.start(retry=True), "PASS")
    assert installed.fetches == 2
    assert installed.mutations == []


@pytest.mark.parametrize(
    "remote",
    [
        {"name": "akbs-member-ops", "version": NEXT},
        {"name": "android-engineering-ops", "version": "not-a-version"},
        {"name": "android-engineering-ops"},
    ],
)
def test_invalid_remote_manifest_blocks_before_plugin_commands(
    installed: FakeInstallation, remote: dict[str, Any],
) -> None:
    installed.remote = remote
    assert_result(installed.start(), "CHECK_FAILED")
    assert installed.mutations == []


@pytest.mark.parametrize("command", [UPGRADE, ADD], ids=["marketplace-upgrade", "plugin-add"])
def test_upgrade_command_failure_blocks_and_retry_is_explicit(
    installed: FakeInstallation, command: list[str],
) -> None:
    installed.remote = installed.manifest(NEXT)
    installed.command_failure = command

    assert_result(installed.start(), "UPDATE_FAILED")
    expected = [UPGRADE] if command == UPGRADE else [UPGRADE, ADD]
    assert installed.mutations == expected
    installed.command_failure = None
    assert_result(installed.start(), "UPDATE_FAILED", reused=True)
    assert installed.mutations == expected
    assert_result(installed.start(retry=True), "UPDATED_RESTART_REQUIRED")
    assert installed.mutations == expected + [UPGRADE, ADD]


def test_plugin_command_timeout_blocks_without_dispatching_add(
    installed: FakeInstallation, monkeypatch: pytest.MonkeyPatch,
) -> None:
    installed.remote = installed.manifest(NEXT)

    def timed_out(command: list[str]) -> subprocess.CompletedProcess[str]:
        installed.commands.append(command)
        raise subprocess.TimeoutExpired(command, 30)

    monkeypatch.setattr(task_start, "run_codex", timed_out)
    assert_result(installed.start(), "UPDATE_FAILED")
    assert installed.mutations == [UPGRADE]


@pytest.mark.parametrize("update_mode", ["no_effect", "hash_mismatch", "inactive", "wrong_marketplace"])
def test_successful_commands_cannot_replace_active_identity_and_content_verification(
    installed: FakeInstallation, update_mode: str,
) -> None:
    installed.remote = installed.manifest(NEXT)
    installed.update_mode = update_mode

    assert_result(installed.start(), "UPDATE_FAILED")
    assert installed.mutations == [UPGRADE, ADD]
    assert json.loads(installed.state_path().read_text())["status"] == "UPDATE_FAILED"


def test_invalid_local_installation_blocks_before_remote_check(installed: FakeInstallation) -> None:
    installed.inventory["installed"].append(
        {"name": "android-framework-ops", "installed": True, "enabled": True}
    )

    assert_result(installed.start(), "INSTALL_FAMILY_INVALID")
    assert installed.fetches == 0
    assert installed.mutations == []


def test_same_task_concurrent_starts_fetch_only_once(
    installed: FakeInstallation, monkeypatch: pytest.MonkeyPatch,
) -> None:
    callers = 5
    ready = Barrier(callers)
    fetching = Event()
    release = Event()

    def held_fetch() -> dict[str, Any]:
        fetching.set()
        assert release.wait(timeout=5), "concurrent test did not release manifest check"
        return installed.fetch()

    def start_together() -> dict[str, Any]:
        ready.wait(timeout=5)
        return installed.start()

    monkeypatch.setattr(task_start, "fetch_remote_manifest", held_fetch)
    with ThreadPoolExecutor(max_workers=callers) as executor:
        futures = [executor.submit(start_together) for _ in range(callers)]
        try:
            assert fetching.wait(timeout=5), "startup never reached manifest check"
        finally:
            release.set()
        results = [future.result(timeout=10) for future in futures]

    assert all(result["status"] in {"PASS", "STARTUP_BUSY"} for result in results)
    passed = [result for result in results if result["status"] == "PASS"]
    assert all(result["blocking"] is False for result in passed)
    assert all(result["blocking"] is True for result in results if result["status"] == "STARTUP_BUSY")
    assert sum(result["reused"] is False for result in passed) == 1
    assert_result(installed.start(), "PASS", reused=True)
    assert installed.fetches == 1
    assert installed.mutations == []
    assert json.loads(installed.state_path().read_text())["status"] == "PASS"


def test_concurrent_update_returns_busy_without_reentering_install(
    installed: FakeInstallation, monkeypatch: pytest.MonkeyPatch,
) -> None:
    installed.remote = installed.manifest(NEXT)
    checking = Event()
    release = Event()

    def held_fetch() -> dict[str, Any]:
        checking.set()
        assert release.wait(timeout=5), "concurrent test did not release update check"
        return installed.fetch()

    monkeypatch.setattr(task_start, "fetch_remote_manifest", held_fetch)
    with ThreadPoolExecutor(max_workers=1) as executor:
        first = executor.submit(installed.start)
        try:
            assert checking.wait(timeout=5)
            assert_result(installed.start(), "STARTUP_BUSY")
            assert installed.mutations == []
        finally:
            release.set()
        assert_result(first.result(timeout=10), "UPDATED_RESTART_REQUIRED")

    assert installed.fetches == 1
    assert installed.mutations == [UPGRADE, ADD]
    assert installed.start(retry=True)["blocking"] is True
    assert installed.fetches == 1
    assert installed.mutations == [UPGRADE, ADD]


def test_other_task_cannot_update_while_a_startup_check_holds_the_shared_lock(
    installed: FakeInstallation, monkeypatch: pytest.MonkeyPatch,
) -> None:
    checking = Event()
    release = Event()

    def held_fetch() -> dict[str, Any]:
        checking.set()
        assert release.wait(timeout=5)
        return installed.fetch()

    monkeypatch.setattr(task_start, "fetch_remote_manifest", held_fetch)
    with ThreadPoolExecutor(max_workers=1) as executor:
        first = executor.submit(installed.start)
        try:
            assert checking.wait(timeout=5)
            assert_result(installed.start("parallel-task"), "STARTUP_BUSY")
            assert not installed.state_path("parallel-task").exists()
        finally:
            release.set()
        assert_result(first.result(timeout=10), "PASS")

    assert_result(installed.start("parallel-task"), "PASS")
    assert installed.fetches == 2
    assert installed.mutations == []


def test_version_change_during_passed_task_requires_a_new_task(installed: FakeInstallation) -> None:
    assert_result(installed.start(), "PASS")
    updated_runtime = installed.install(NEXT)
    installed.remote = installed.manifest(NEXT)

    assert_result(installed.start(root=updated_runtime), "TASK_VERSION_CHANGED")
    assert installed.start(root=updated_runtime, retry=True)["blocking"] is True
    assert installed.fetches == 1
    assert installed.mutations == []
    assert_result(installed.start("new-task-after-external-update", root=updated_runtime), "PASS")
    assert installed.fetches == 2


def test_pass_reuse_still_rejects_local_source_cache_drift(installed: FakeInstallation) -> None:
    assert_result(installed.start(), "PASS")
    (installed.source / "README.md").write_text("unexpected source modification\n", encoding="utf-8")

    result = installed.start()
    assert result["blocking"] is True
    assert result["status"] == "INSTALL_FAMILY_INVALID"
    assert installed.fetches == 1
    assert installed.mutations == []


@pytest.mark.parametrize("task_id", ["", "../escape", "a/b", "a\\b", ".hidden", "a" * 129])
def test_invalid_task_id_never_checks_or_writes_startup_state(
    installed: FakeInstallation, task_id: str,
) -> None:
    assert_result(installed.start(task_id), "INVALID_TASK_ID")
    assert installed.fetches == 0
    assert installed.mutations == []
    assert not installed.state_path().parent.exists()


def test_corrupt_persisted_task_state_blocks_without_rechecking(installed: FakeInstallation) -> None:
    assert_result(installed.start(), "PASS")
    installed.state_path().write_text("{broken JSON", encoding="utf-8")

    assert_result(installed.start(), "STARTUP_STATE_INVALID")
    assert installed.fetches == 1
    assert installed.mutations == []


@pytest.mark.parametrize("remote_version", [CURRENT, NEXT])
def test_cli_returns_json_and_nonzero_for_a_blocked_start(
    installed: FakeInstallation, capsys: pytest.CaptureFixture[str], remote_version: str,
) -> None:
    installed.remote = installed.manifest(remote_version)
    exit_code = task_start.main(["--task-id", TASK, "--plugin-root", str(installed.runtime)])
    output = capsys.readouterr()
    result = json.loads(output.out)

    assert exit_code == (0 if remote_version == CURRENT else 78)
    assert_result(result, "PASS" if remote_version == CURRENT else "UPDATED_RESTART_REQUIRED")
    assert output.err == ""


def test_low_level_family_guard_remains_local_and_does_not_create_startup_state(
    installed: FakeInstallation, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*_args: Any, **_kwargs: Any) -> None:
        pytest.fail("the local install-family guard must not check releases or update plugins")

    monkeypatch.setattr(task_start, "fetch_remote_manifest", forbidden)
    monkeypatch.setattr(task_start, "run_codex", forbidden)
    install_family.assert_target_install_family(
        installed.runtime, inventory=installed.inventory, codex_home=installed.home
    )

    assert not installed.state_path().parent.exists()
    assert installed.commands == []


def test_help_has_no_inventory_network_update_or_state_side_effects(tmp_path: Path) -> None:
    script = PLUGIN / "lib/android_engineering_ops/task_start.py"
    home = tmp_path / "empty-codex-home"
    # Run the actual CLI import and parser while making accidental external calls
    # fail immediately, before they can contact a service or run a plugin command.
    runner = (
        "import runpy, socket, subprocess, sys, urllib.request\n"
        "def forbidden(*args, **kwargs):\n"
        "    raise AssertionError('help attempted an external operation')\n"
        "socket.create_connection = forbidden\n"
        "urllib.request.urlopen = forbidden\n"
        "subprocess.run = forbidden\n"
        "sys.argv = [sys.argv[1], '--help']\n"
        "runpy.run_path(sys.argv[0], run_name='__main__')\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", runner, str(script)],
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
        env={**os.environ, "CODEX_HOME": str(home), "PYTHONDONTWRITEBYTECODE": "1"},
    )

    assert completed.returncode == 0, completed.stderr
    assert "--task-id" in completed.stdout
    assert "--retry" in completed.stdout
    assert "--plugin-root" not in completed.stdout
    assert not home.exists()
