from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[4]
PLUGIN = ROOT / "plugins/android-engineering-ops"
LIB = PLUGIN / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from android_engineering_ops.source_access import (  # noqa: E402
    ADAPTER_COMMANDS,
    UnsupportedSourceAccessHost,
    adapter_environment,
    adapter_root,
    detect_source_access_host,
    dispatch_adapter_command,
)


def test_host_detection_selects_wsl_or_macos_without_user_choice() -> None:
    assert detect_source_access_host(
        system="Linux",
        release="6.6.87.2-microsoft-standard-WSL2",
        version="#1 SMP",
        environ={},
        kernel_osrelease="6.6.87.2-microsoft-standard-WSL2",
        kernel_version="#1 SMP",
    ).host == "wsl"
    assert detect_source_access_host(
        system="Darwin", release="25.0.0", version="Darwin Kernel", environ={}
    ).host == "macos"


@pytest.mark.parametrize("system", ["Linux", "Windows", "FreeBSD", ""])
def test_wrong_host_fails_closed_before_an_adapter(system: str) -> None:
    with pytest.raises(UnsupportedSourceAccessHost, match="supports WSL and macOS only"):
        detect_source_access_host(
            system=system,
            release="6.8.0-generic",
            version="#1 SMP",
            environ={},
            kernel_osrelease="6.8.0-generic",
            kernel_version="#1 SMP",
        )


def test_generic_linux_with_forged_wsl_environment_fails_closed() -> None:
    with pytest.raises(UnsupportedSourceAccessHost, match="supports WSL and macOS only"):
        detect_source_access_host(
            system="Linux",
            release="6.8.0-generic",
            version="#1 SMP",
            environ={"WSL_INTEROP": "/run/forged", "WSL_DISTRO_NAME": "forged"},
            kernel_osrelease="6.8.0-generic",
            kernel_version="#1 SMP Ubuntu",
        )


def test_contract_and_public_entry_have_one_owner() -> None:
    contract = json.loads(
        (PLUGIN / "contracts/source-access/v1/adapter-contract.json").read_text(
            encoding="utf-8"
        )
    )
    assert contract["implementation_owner"] == "android-engineering-ops"
    assert contract["core_public_skill"] is True
    assert contract["public_entry_plugins"] == {
        "wsl": "android-engineering-ops",
        "macos": "android-engineering-ops",
    }
    assert (PLUGIN / "skills/android-source-access/SKILL.md").is_file()


@pytest.mark.parametrize("host", ["wsl", "macos"])
def test_packaged_adapter_contains_every_declared_command(host: str) -> None:
    root = adapter_root(PLUGIN, host)
    scripts = root / "skills/android-source-access/scripts"
    for command in ADAPTER_COMMANDS[host]:
        assert (scripts / command).is_file()


def test_public_shell_entries_are_thin_identical_dispatchers() -> None:
    scripts = PLUGIN / "skills/android-source-access/scripts"
    entry = (scripts / "_command_entry.sh").read_bytes()
    for command in sorted(set().union(*ADAPTER_COMMANDS.values())):
        assert (scripts / command).read_bytes() == entry


def test_manual_recovery_redirects_only_after_machine_install_gate() -> None:
    references = (
        PLUGIN / "skills/android-source-access/references/manual-recovery.md",
        PLUGIN
        / "adapters/source-access/wsl/skills/android-source-access/references/manual-recovery.md",
    )
    for reference in references:
        text = reference.read_text(encoding="utf-8")
        for block in text.split("```bash")[1:]:
            block = block.split("```", 1)[0]
            if "> /tmp/" not in block:
                continue
            assert block.index("install_family.py") < block.index("> /tmp/"), reference
            assert "--plugin-root" in block, reference


def test_recovery_uses_mount_metadata_and_channel_identity_not_mounted_source_markers() -> None:
    roots = (
        PLUGIN / "skills/android-source-access/references",
        PLUGIN / "adapters/source-access/wsl/skills/android-source-access/references",
    )
    for root in roots:
        manual = (root / "manual-recovery.md").read_text(encoding="utf-8")
        verify = manual.split("## Verify Project", 1)[1].split("\n## ", 1)[0]
        assert 'findmnt -T "$LOCAL_PROJECT"' in verify
        assert "inspect-android-sdk.sh" in verify
        assert "android-remote-channel" in verify
        for source_dir in ("build", "frameworks", ".repo"):
            assert f"$LOCAL_PROJECT/{source_dir}" not in verify
        design = (root / "design.md").read_text(encoding="utf-8")
        assert "Mount, verify Android markers" not in design
        assert "verify mount metadata without probing mounted source" in design


def test_macos_reference_describes_the_in_plugin_adapter_not_another_installation() -> None:
    reference = (PLUGIN / "skills/android-source-access/references/macos-source-access.md").read_text(
        encoding="utf-8",
    )
    assert "single public `android-source-access` Skill" in reference
    assert "No separate platform plugin is installed" in reference
    assert "Both in-plugin platform adapters" in reference
    assert "before this platform plugin" not in reference
    assert "requires version 1.0.167" not in reference


def test_dispatch_preserves_arguments_and_injects_target_environment() -> None:
    host = detect_source_access_host(
        system="Linux", release="microsoft-standard-WSL2", version="#1", environ={},
        kernel_osrelease="microsoft-standard-WSL2", kernel_version="#1",
    )
    calls: list[tuple[str, list[str], dict[str, str]]] = []

    def fake_execve(path: str, argv: list[str], env: dict[str, str]) -> object:
        calls.append((path, argv, env))
        return object()

    dispatch_adapter_command(
        PLUGIN,
        host,
        "restore-project-mount.sh",
        ["--list"],
        execve=fake_execve,
        base_environment={},
    )
    path, argv, env = calls[0]
    assert Path(path).name == "restore-project-mount.sh"
    assert argv[1:] == ["--list"]
    assert env["ANDROID_SOURCE_ACCESS_HOST"] == "wsl"
    assert env["ANDROID_SOURCE_ACCESS_ADAPTER_ROOT"].startswith(str(PLUGIN))
    assert env["ANDROID_REMOTE_CHANNEL_SCRIPT"].startswith(str(PLUGIN))
    assert env["ANDROID_REMOTE_SOURCE_INSPECTION_HELPER"].startswith(str(PLUGIN))


def test_dispatch_overwrites_legacy_routing_environment_and_rejects_cli_override() -> None:
    host = detect_source_access_host(
        system="Linux", release="microsoft-standard-WSL2", version="#1", environ={},
        kernel_osrelease="microsoft-standard-WSL2", kernel_version="#1",
    )
    calls: list[dict[str, str]] = []
    dispatch_adapter_command(
        PLUGIN,
        host,
        "inspect-android-sdk.sh",
        ["--ssh-host", "build", "--remote-root", "/src"],
        execve=lambda _path, _argv, env: calls.append(dict(env)),
        base_environment={
            "ANDROID_REMOTE_CHANNEL_SCRIPT": "/legacy/channel.sh",
            "ANDROID_REMOTE_SOURCE_INSPECTION_HELPER": "/legacy/inspect.py",
        },
    )
    assert calls[0]["ANDROID_REMOTE_CHANNEL_SCRIPT"] == str(
        PLUGIN / "skills/android-remote-channel/scripts/remote-channel.sh"
    )
    assert calls[0]["ANDROID_REMOTE_SOURCE_INSPECTION_HELPER"] == str(
        PLUGIN / "lib/android_engineering_ops/remote_source_inspection.py"
    )
    for override in ("--channel-script", "--channel-script=/tmp/old", "--inspection-helper"):
        with pytest.raises(UnsupportedSourceAccessHost, match="caller override is forbidden"):
            dispatch_adapter_command(
                PLUGIN,
                host,
                "inspect-android-sdk.sh",
                [override, "/tmp/old"],
                execve=lambda *_: object(),
                base_environment={},
            )


def test_cross_host_command_is_rejected_before_exec() -> None:
    wsl = detect_source_access_host(
        system="Linux", release="microsoft-standard-WSL2", version="#1", environ={},
        kernel_osrelease="microsoft-standard-WSL2", kernel_version="#1",
    )
    with pytest.raises(UnsupportedSourceAccessHost, match="not available on host wsl"):
        dispatch_adapter_command(
            PLUGIN,
            wsl,
            "keychain-read.sh",
            [],
            execve=lambda *_: object(),
            base_environment={},
        )
