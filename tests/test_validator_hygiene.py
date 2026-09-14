from __future__ import annotations

import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def validator_entrypoints() -> set[Path]:
    entries = {
        path.relative_to(REPO_ROOT)
        for path in (REPO_ROOT / "scripts").glob("validate*")
        if path.is_file()
    }
    entries.add(Path("scripts/test_validator_cleanup.py"))
    entries.update(
        path.relative_to(REPO_ROOT)
        for path in (REPO_ROOT / "plugins").rglob("doctor.py")
    )
    entries.update(
        path.relative_to(REPO_ROOT)
        for path in (REPO_ROOT / "plugins").rglob("self_test*.py")
    )
    return entries


def test_current_plugin_validator_inventory_is_explicit() -> None:
    assert validator_entrypoints() == {
        Path("scripts/validate_incoming_contract_gate.py"),
        Path("scripts/validate_active_plugin_topology.py"),
        Path("scripts/validate_macos_over_ssh.sh"),
        Path("scripts/validate_plugins.sh"),
        Path("scripts/validate_skill_layout.sh"),
        Path("scripts/test_validator_cleanup.py"),
        Path(
            "plugins/akbs-member-ops/internal/incoming-v2/"
            "scripts/akbs_intake/doctor.py"
        ),
    }


def test_shell_validators_install_exit_cleanup_or_remote_owned_cleanup() -> None:
    for relative in (
        "scripts/validate_plugins.sh",
        "scripts/validate_skill_layout.sh",
        "scripts/validate_macos_over_ssh.sh",
    ):
        source = (REPO_ROOT / relative).read_text(encoding="utf-8")
        assert "validator_cleanup_install" in source, relative
        assert "validator_cleanup.sh" in source, relative
    macos = (REPO_ROOT / "scripts/validate_macos_over_ssh.sh").read_text(encoding="utf-8")
    assert "validator_path_guard.py" in macos
    assert "create-private" in macos and "cleanup-private" in macos
    assert "trap 'cleanup_all" in macos
    assert "remote validator owned-directory cleanup failed" in macos
    assert 'cleanup-private' in macos and '>/dev/null 2>&1 || true' not in macos
    assert "pwd -P" in macos
    assert "android-change-workflow/scripts" in macos
    assert "plugins/android-engineering-ops" in macos
    assert ".agents/plugins/marketplace.json" in macos
    assert "manifests/android-engineering-ops.toml" in macos
    assert "contracts/source-access" in macos
    assert "adapters/source-access" in macos
    assert "skills/android-source-access/scripts/android_source_access.py" in macos
    assert "plugins/android-engineering-ops/lib" in macos
    assert "--expected-host macos" in macos


def test_validator_cleanup_supports_macos_system_bash() -> None:
    cleanup = REPO_ROOT / "scripts" / "validator_cleanup.sh"
    source = cleanup.read_text(encoding="utf-8")
    assert "mapfile" not in source
    syntax = subprocess.run(
        ["/bin/bash", "-n", str(cleanup)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert syntax.returncode == 0, syntax.stderr


def test_aggregate_declares_the_installed_plugin_creator_validator_after_cleanup_setup() -> None:
    aggregate = (REPO_ROOT / "scripts/validate_plugins.sh").read_text(encoding="utf-8")
    installed_validator = 'skills/.system/plugin-creator/scripts/validate_plugin.py'
    assert installed_validator in aggregate
    assert aggregate.index("validator_cleanup_install") < aggregate.index('for plugin in')
    assert aggregate.index("validator_cleanup_install") < aggregate.index('python3 "$validator"')
    assert "--import-mode=importlib" in aggregate
    assert 'tests/test_*.py' in aggregate
    assert 'tests/plugins/*' in aggregate


def test_python_validator_components_use_parent_or_finally_cleanup() -> None:
    incoming = (REPO_ROOT / "scripts/validate_incoming_contract_gate.py").read_text(encoding="utf-8")
    assert "repository_cleanup(REPO_ROOT)" in incoming
    assert "controlled-validation-output.sh" in incoming
    assert "akbs_validation_output_init" in incoming
    assert "mktemp -d /tmp" not in incoming

    aggregate = (REPO_ROOT / "scripts/validate_plugins.sh").read_text(encoding="utf-8")
    assert "validator_cleanup_install" in aggregate
    assert "validate_incoming_contract_gate.py\" --mode client-only" in aggregate
    assert "--mode remote-pilot" not in aggregate
    assert 'choices=("client-only", "remote-pilot")' in incoming
    assert not (REPO_ROOT / "scripts/android_change_v2_contract_gate.py").exists()
    assert 'default="client-only"' in incoming


def test_doctors_are_read_only_and_shared_guard_copies_are_declared() -> None:
    doctors = (
        REPO_ROOT
        / "plugins/akbs-member-ops/internal/incoming-v2/scripts/akbs_intake/doctor.py",
    )
    for doctor_path in doctors:
        doctor = doctor_path.read_text(encoding="utf-8")
        for writer in ("write_text(", "write_bytes(", "mkdir(", "tempfile.", 'open("w'):
            assert writer not in doctor, doctor_path
    hygiene = (REPO_ROOT / "scripts/validator_hygiene.py").read_text(encoding="utf-8")
    assert "from validator_path_guard import" in hygiene
    assert "guard_write_path(" in hygiene


def test_shared_guard_cli_creates_and_cleans_an_owned_private_directory(tmp_path: Path) -> None:
    guard = REPO_ROOT / "scripts/validator_path_guard.py"
    created = subprocess.run(
        [
            sys.executable,
            str(guard),
            "create-private",
            "--authority",
            str(tmp_path),
            "--prefix",
            "cli-roundtrip.",
            "--purpose",
            "plugin CLI roundtrip",
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert created.returncode == 0, created.stderr
    path_text, token = created.stdout.strip().split("\t", 1)
    claimed = Path(path_text)
    assert claimed.is_dir()

    cleaned = subprocess.run(
        [
            sys.executable,
            str(guard),
            "cleanup-private",
            "--authority",
            str(tmp_path),
            "--path",
            str(claimed),
            "--token",
            token,
            "--purpose",
            "plugin CLI roundtrip",
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert cleaned.returncode == 0, cleaned.stderr
    assert not claimed.exists()
