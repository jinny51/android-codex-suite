from __future__ import annotations

import importlib.util
import io
import subprocess
from pathlib import Path
from unittest import mock

import pytest


ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / "shared/codex_plugin_update.py"
SPEC = importlib.util.spec_from_file_location("canonical_codex_plugin_update", CANONICAL)
assert SPEC is not None and SPEC.loader is not None
update = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(update)


def test_both_plugin_runtime_copies_match_the_shared_owner() -> None:
    expected = CANONICAL.read_bytes()
    assert expected
    for plugin in ("akbs-member-ops", "android-engineering-ops"):
        runtime = ROOT / "plugins" / plugin / "lib/codex_plugin_update.py"
        assert runtime.read_bytes() == expected, runtime


@pytest.mark.parametrize("repository", [
    "https://github.com/jinny51/android-codex-suite",
    "https://github.com/jinny51/android-codex-suite.git",
    "git@github.com:jinny51/android-codex-suite.git",
    "ssh://git@github.com/jinny51/android-codex-suite.git",
])
def test_manifest_url_preserves_the_selected_plugin(repository: str) -> None:
    assert update.github_manifest_url(repository, "android-engineering-ops") == (
        "https://raw.githubusercontent.com/jinny51/android-codex-suite/main/"
        "plugins/android-engineering-ops/.codex-plugin/plugin.json"
    )


@pytest.mark.parametrize(("repository", "plugin"), [
    ("https://example.com/github.com/jinny51/android-codex-suite", "akbs-member-ops"),
    ("https://github.com.evil/jinny51/android-codex-suite", "akbs-member-ops"),
    ("https://github.com/jinny51/android-codex-suite/tree/main", "akbs-member-ops"),
    ("https://github.com/jinny51/android-codex-suite", "../../other"),
])
def test_manifest_url_rejects_non_repository_and_traversal_paths(repository: str, plugin: str) -> None:
    assert update.github_manifest_url(repository, plugin) == ""


def test_version_comparison_preserves_member_release_number_rules() -> None:
    assert update.version_parts("2.0.3+build") == (2, 0, 3)
    assert update.compare_versions("2.0", "2.0.0") == 0
    assert update.compare_versions("2.0.10", "2.0.9") == 1
    assert update.compare_versions("2.0.0", "2.0.1") == -1
    with pytest.raises(ValueError):
        update.version_parts("2evil")


def test_manifest_fetch_uses_the_injected_opener_and_bounded_read() -> None:
    response = mock.MagicMock()
    response.__enter__.return_value.read.return_value = b'{"name":"akbs-member-ops","version":"2.0.3"}'
    opener = mock.Mock(return_value=response)
    result = update.fetch_manifest("https://example.test/manifest", 4, opener=opener)
    assert result["version"] == "2.0.3"
    opener.assert_called_once_with("https://example.test/manifest", timeout=4)
    response.__enter__.return_value.read.assert_called_once_with(update.MAX_MANIFEST_BYTES + 1)


@pytest.mark.parametrize("raw", [
    b'{"version":"2.0.2","version":"2.0.3"}',
    b'{"nested":{"name":1,"name":2}}',
    b'{"value":NaN}',
    b'{"value":Infinity}',
    b'[]',
    b'\xff',
    b' ' * (update.MAX_MANIFEST_BYTES + 1),
])
def test_manifest_fetch_rejects_invalid_or_oversized_json(raw: bytes) -> None:
    with pytest.raises((ValueError, UnicodeError)):
        update.fetch_manifest("https://example.test/manifest", opener=lambda *args, **kwargs: io.BytesIO(raw))


def verified_install(**changes: object) -> dict[str, object]:
    return {
        "installed_plugin_active": True,
        "installed_plugin_version": "2.0.3",
        "installed_plugin_path": "/codex/plugins/cache/android-codex-suite/selected/2.0.3",
        **changes,
    }


@pytest.mark.parametrize("plugin", ["akbs-member-ops", "android-engineering-ops"])
def test_update_installs_only_the_selected_plugin_then_verifies(plugin: str) -> None:
    events: list[object] = []

    def run(command: list[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
        events.append((command, timeout))
        return subprocess.CompletedProcess(command, 0, stdout="updated", stderr="")

    def verify() -> dict[str, object]:
        events.append("verify")
        return verified_install()

    result = update.update_plugin(plugin, run_command=run, verify_install=verify, expected_version="2.0.3")
    assert result["status"] == "PASS"
    assert result["restart_required"] is True
    assert result["installed_plugin_version"] == "2.0.3"
    assert events == [
        (["codex", "plugin", "marketplace", "upgrade", "android-codex-suite", "--json"], update.UPDATE_COMMAND_TIMEOUT),
        (["codex", "plugin", "add", f"{plugin}@android-codex-suite", "--json"], update.UPDATE_COMMAND_TIMEOUT),
        "verify",
    ]


@pytest.mark.parametrize("stage", [0, 1])
@pytest.mark.parametrize("failure", ["exit", "timeout", "missing-command"])
def test_failed_update_stops_before_verification(stage: int, failure: str) -> None:
    responses: list[object] = [subprocess.CompletedProcess([], 0, stdout="ok", stderr="")]
    if failure == "exit":
        failed: object = subprocess.CompletedProcess([], 1, stdout="", stderr="failed")
    elif failure == "timeout":
        failed = subprocess.TimeoutExpired("codex", update.UPDATE_COMMAND_TIMEOUT)
    else:
        failed = FileNotFoundError("codex")
    runner = mock.Mock(side_effect=responses[:stage] + [failed])
    verifier = mock.Mock()
    result = update.update_plugin("akbs-member-ops", run_command=runner, verify_install=verifier)
    assert result["status"] == "FAIL"
    assert result["restart_required"] is False
    assert runner.call_count == stage + 1
    verifier.assert_not_called()


@pytest.mark.parametrize("installation", [
    {},
    verified_install(installed_plugin_active=False),
    verified_install(installed_plugin_version=""),
    verified_install(installed_plugin_version="2evil"),
    verified_install(installed_plugin_version="2.0.2"),
    verified_install(installed_plugin_path=""),
    verified_install(installed_plugin_fallback=True),
    verified_install(installed_plugin_ambiguous=True),
    verified_install(blocking=True),
    verified_install(status="UNKNOWN"),
    verified_install(install_family={"status": "MIXED_INSTALL", "blocking": True}),
])
def test_zero_exit_codes_do_not_override_failed_installation_evidence(installation: dict[str, object]) -> None:
    runner = mock.Mock(return_value=subprocess.CompletedProcess([], 0, stdout="ok", stderr=""))
    result = update.update_plugin(
        "akbs-member-ops", run_command=runner, verify_install=lambda: installation, expected_version="2.0.3"
    )
    assert runner.call_count == 2
    assert result["status"] == "FAIL"
    assert result["restart_required"] is False


def test_verifier_exception_and_unsupported_target_are_not_successes() -> None:
    runner = mock.Mock(return_value=subprocess.CompletedProcess([], 0, stdout="ok", stderr=""))
    result = update.update_plugin(
        "akbs-member-ops", run_command=runner, verify_install=mock.Mock(side_effect=ValueError("hash mismatch"))
    )
    assert result["status"] == "FAIL"
    assert result["reason"] == "install_verification_failed"
    runner.reset_mock()
    result = update.update_plugin("jinny-android-practices", run_command=runner, verify_install=mock.Mock())
    assert result["attempted"] is False
    runner.assert_not_called()
