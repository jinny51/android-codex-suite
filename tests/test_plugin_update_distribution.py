from __future__ import annotations

import importlib.util
import io
import json
import socket
import ssl
import subprocess
import urllib.error
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


@pytest.mark.parametrize("failure", [
    TimeoutError(), ConnectionError("offline"), socket.gaierror(-2, "no host"),
    urllib.error.URLError(TimeoutError()),
])
def test_only_unavailable_manifest_transport_is_warning_eligible(failure: Exception) -> None:
    assert update.manifest_transport_unavailable(failure) is True


@pytest.mark.parametrize("failure", [
    ValueError("invalid manifest"), OSError("not known to be a network error"),
    urllib.error.HTTPError("https://example.test", 404, "missing", {}, None),
    urllib.error.URLError("unclassified reason"),
    ssl.SSLCertVerificationError("untrusted certificate"),
    urllib.error.URLError(ssl.SSLCertVerificationError("untrusted certificate")),
])
def test_manifest_integrity_http_and_unknown_failures_remain_blocking(failure: Exception) -> None:
    assert update.manifest_transport_unavailable(failure) is False


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
    pytest.param(b' ' * (update.MAX_MANIFEST_BYTES + 1), id="oversized"),
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


MEMBER = "akbs-member-ops"
ENGINEERING = "android-engineering-ops"
BEFORE = {MEMBER: "2.0.1", ENGINEERING: "3.0.1"}
AFTER = {MEMBER: "2.0.3", ENGINEERING: "3.0.2"}
OLD_REF = "a" * 40
NEW_REF = "b" * 40


class FakeMarketplace:
    def __init__(self, home: Path, *, enabled: dict[str, str] | None = None):
        self.root = home / "market"
        (self.root / ".git").mkdir(parents=True)
        self.home = home
        self.installed = (enabled or BEFORE).copy()
        self.remote = AFTER.copy()
        self.ref = OLD_REF
        self.origin = update.OFFICIAL_REPOSITORY
        self.actions: list[tuple[str, str]] = []
        self.preflights: list[str] = []
        self.fail_plugin = ""

    def __call__(self, argv, *, timeout, env=None):
        assert timeout == update.UPDATE_COMMAND_TIMEOUT
        def okay(output=""):
            return subprocess.CompletedProcess(argv, 0, stdout=output, stderr="")
        if argv[:4] == ["codex", "plugin", "marketplace", "list"]:
            return okay(json.dumps({"marketplaces": [] if not self.ref else [{
                "name": update.MARKETPLACE, "root": str(self.root),
                "marketplaceSource": {"sourceType": "git", "source": self.origin},
            }]}))
        if argv[:3] == ["codex", "plugin", "list"]:
            return okay(json.dumps({"installed": [
                {"pluginId": name + "@" + update.MARKETPLACE, "version": version, "installed": True, "enabled": True}
                for name, version in self.installed.items()
            ]}))
        if argv[0] == "git":
            if argv[1] == "init":
                return okay()
            args = argv[3:]
            if args[0] == "fetch":
                return okay()
            target = args[1]
            if target == "refs/heads/previous":
                return okay(OLD_REF)
            if target == "refs/heads/release":
                return okay(NEW_REF)
            ref, path = target.split(":")
            name = path.split("/")[1]
            version = (BEFORE if ref == OLD_REF else self.remote)[name]
            return okay(json.dumps({"name": name, "version": version}) if args[0] == "show" else version)
        if argv[0] != "codex":
            self.preflights.append("member" if argv[-1] == "preflight-install-family" else "engineering")
            return okay()
        assert env and env["GIT_ALLOW_PROTOCOL"] == "file"
        if argv[:4] == ["codex", "plugin", "marketplace", "remove"]:
            self.actions.append(("remove", self.ref))
            self.ref = ""
        elif argv[:4] == ["codex", "plugin", "marketplace", "add"]:
            self.ref = argv[argv.index("--ref") + 1]
            self.actions.append(("pin", self.ref))
        else:
            assert argv[:3] == ["codex", "plugin", "add"]
            name = argv[3].split("@")[0]
            self.actions.append(("install", name))
            if self.ref == NEW_REF and name == self.fail_plugin:
                return subprocess.CompletedProcess(argv, 1, stdout="", stderr="simulated failed second install")
            self.installed[name] = (BEFORE if self.ref == OLD_REF else self.remote)[name]
        return okay("{}")

    def update(self, name=MEMBER, *, verify_install=None, expected_version=None):
        return update.update_plugin(
            name, run_command=self, verify_install=verify_install or (
                lambda: verified_install(installed_plugin_version=self.installed[name])
            ), expected_version=expected_version or AFTER.get(name), codex_home=self.home,
        )


@pytest.mark.parametrize("trigger", [MEMBER, ENGINEERING])
def test_update_uses_one_publication_for_both_enabled_plugins(tmp_path, trigger):
    market = FakeMarketplace(tmp_path)
    result = market.update(trigger)
    assert result["status"] == "PASS"
    assert result["restart_required"] is True
    assert result["plugins_updated"] == [MEMBER, ENGINEERING]
    assert market.actions == [("remove", OLD_REF), ("pin", NEW_REF), ("install", MEMBER), ("install", ENGINEERING)]
    assert market.installed == AFTER


def test_update_does_not_add_uninstalled_plugin(tmp_path):
    market = FakeMarketplace(tmp_path, enabled={MEMBER: BEFORE[MEMBER]})
    assert market.update()["plugins_updated"] == [MEMBER]
    assert market.installed == {MEMBER: AFTER[MEMBER]}


def test_single_change_retains_and_verifies_unchanged_companion(tmp_path):
    market = FakeMarketplace(tmp_path)
    market.remote[ENGINEERING] = BEFORE[ENGINEERING]
    assert market.update()["plugins_updated"] == [MEMBER]
    assert market.installed[ENGINEERING] == BEFORE[ENGINEERING]
    assert market.preflights == ["engineering"]


def test_update_restores_pinned_release_and_both_plugins_after_failure(tmp_path):
    market = FakeMarketplace(tmp_path)
    market.fail_plugin = ENGINEERING
    verify = mock.Mock()
    result = market.update(verify_install=verify)
    assert result["status"] == "FAIL"
    assert result["reason"] == "install_command_failed"
    assert result["rolled_back"] is True
    assert market.ref == OLD_REF
    assert market.installed == BEFORE
    assert market.preflights == ["member", "engineering"]
    verify.assert_not_called()


def test_wrong_source_or_stale_publication_never_switches_catalog(tmp_path):
    market = FakeMarketplace(tmp_path)
    market.origin = "https://github.com/some-fork/android-codex-suite.git"
    assert market.update()["reason"] == "unsupported_marketplace"
    market.origin = update.OFFICIAL_REPOSITORY
    assert market.update(expected_version="9.0.0")["reason"] == "release_not_ready"
    assert market.actions == []


@pytest.mark.parametrize("installation", [
    {}, verified_install(installed_plugin_active=False),
    verified_install(installed_plugin_version="2.0.2"),
    verified_install(installed_plugin_path=""),
    verified_install(installed_plugin_fallback=True),
    verified_install(installed_plugin_ambiguous=True),
    verified_install(blocking=True), verified_install(status="UNKNOWN"),
    verified_install(install_family={"status": "MIXED_INSTALL", "blocking": True}),
])
def test_zero_exit_codes_do_not_override_failed_installation_evidence(tmp_path, installation):
    market = FakeMarketplace(tmp_path)
    result = market.update(verify_install=lambda: installation)
    assert result["status"] == "FAIL"
    assert result["restart_required"] is False
    assert result["rolled_back"] is True
    assert market.installed == BEFORE


def test_concurrent_update_leaves_catalog_alone(tmp_path):
    market = FakeMarketplace(tmp_path)
    with update._update_lock(tmp_path):
        result = market.update()
    assert result["reason"] == "update_busy"
    assert market.actions == []


def test_unsupported_target_is_rejected_before_inspection(tmp_path):
    market = FakeMarketplace(tmp_path)
    result = market.update("jinny-android-practices")
    assert result["attempted"] is False
    assert market.actions == []


def test_windows_codex_resolves_native_npm_binary_when_only_cmd_is_on_path(monkeypatch, tmp_path):
    shim = tmp_path / "codex.cmd"
    native = tmp_path / (
        "node_modules/@openai/codex/node_modules/@openai/codex-win32-x64/"
        "vendor/x86_64-pc-windows-msvc/bin/codex.exe"
    )
    native.parent.mkdir(parents=True)
    native.touch()
    monkeypatch.setattr(update.platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(update.shutil, "which", lambda name: str(shim) if name == "codex.cmd" else None)
    assert update._windows_codex_executable() == str(native.resolve())
    native.unlink()
    with pytest.raises(FileNotFoundError, match="Native Windows codex.exe"):
        update._windows_codex_executable()


def test_wsl_codex_rejects_windows_npm_shim_and_uses_native_cli(monkeypatch, tmp_path):
    native = tmp_path / ".local/share/codex/desktop-cli/public/current/codex"
    native.parent.mkdir(parents=True)
    native.touch()
    native.chmod(0o700)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    shim = "/mnt/c/Users/member/AppData/Roaming/npm/codex"
    monkeypatch.setattr(update.shutil, "which", lambda name, path=None: shim if path is None else None)
    monkeypatch.setattr(update.os, "get_exec_path", lambda: [str(Path(shim).parent)])
    assert update._wsl_codex_executable("codex") == str(native.resolve())
    native.unlink()
    with pytest.raises(FileNotFoundError, match="native Linux Codex in WSL"):
        update._wsl_codex_executable("codex")
