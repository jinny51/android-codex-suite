from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CANONICAL = REPO_ROOT / "plugins" / "android-engineering-ops" / "lib" / "android_engineering_ops" / "source_access" / "remote_inspector.sh"
INSPECTION_HELPER = REPO_ROOT / "plugins" / "android-engineering-ops" / "lib" / "android_engineering_ops" / "remote_source_inspection.py"
WSL_ENTRY = (
    REPO_ROOT
    / "plugins"
    / "android-engineering-ops"
    / "skills"
    / "android-source-access"
    / "scripts"
    / "inspect-android-sdk.sh"
)
MAC_ENTRY = (
    REPO_ROOT
    / "plugins"
    / "android-engineering-ops"
    / "skills"
    / "android-source-access"
    / "scripts"
    / "detect-projects.sh"
)


def init_branch(path: Path, branch: str) -> None:
    path.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["git", "init", "-q", "-b", branch, str(path)],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert result.returncode == 0, result.stderr


def run_inspector(
    script: Path,
    root: Path,
    *,
    platform: str = "",
    sdk_name: str = "",
    accept_platform_conflict: bool = False,
    accept_sdk_name_conflict: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "bash",
            str(script),
            str(root),
            platform,
            sdk_name,
            "1" if accept_platform_conflict else "0",
            "1" if accept_sdk_name_conflict else "0",
        ],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def parse_simple_env(stdout: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in stdout.splitlines():
        key, value = line.split("=", 1)
        result[key] = value
    return result


def test_remote_inspector_has_one_core_runtime_owner() -> None:
    assert CANONICAL.read_bytes()
    assert not (REPO_ROOT / "shared/android_source_access/remote_inspector.sh").exists()
    assert not (REPO_ROOT / "shared/android_source_access/remote_inspector.sh").exists()


def test_public_entries_use_one_host_dispatcher() -> None:
    wsl = WSL_ENTRY.read_text(encoding="utf-8")
    mac = MAC_ENTRY.read_text(encoding="utf-8")

    for source in (wsl, mac):
        assert "android_source_access.py" in source
        assert "score_rk=0" not in source
        assert "first_assignment()" not in source
        assert "ssh " not in source
    assert wsl == mac


def test_mtk_alias_and_project_branch_have_one_identity(tmp_path: Path) -> None:
    root = tmp_path / "mtk-sdk"
    (root / "build").mkdir(parents=True)
    (root / "vendor").mkdir()
    (root / "hardware").mkdir()
    (root / "device" / "mtk" / "common").mkdir(parents=True)
    (root / "device" / "mtk" / "common" / "BoardConfig.mk").write_text(
        "TARGET_BOARD_PLATFORM := mt8775\n",
        encoding="utf-8",
    )
    init_branch(root / "frameworks" / "base", "TVE1097M")

    outputs = []
    for script in (CANONICAL,):
        result = run_inspector(script, root)
        assert result.returncode == 0, result.stderr
        outputs.append(parse_simple_env(result.stdout))

    assert outputs[0]["PLATFORM"] == "mtk"
    assert outputs[0]["SDK_NAME"] == "TVE1097M"
    assert outputs[0]["SOURCE_SDK_SOURCE"] == "project_branch"
    assert outputs[0]["TARGET_BOARD_PLATFORM"] == "mt8775"
    assert outputs[0]["PLATFORM_SCORE_MTK"] == "50"


def test_core_helper_routes_inspection_through_fake_channel_v2(tmp_path: Path) -> None:
    fake_channel = tmp_path / "fake-channel.py"
    log = tmp_path / "args.txt"
    fake_channel.write_text(
        f"#!{sys.executable}\n"
        "import os, pathlib, sys\n"
        "pathlib.Path(os.environ['FAKE_CHANNEL_LOG']).write_text('\\n'.join(sys.argv[1:]), encoding='utf-8')\n"
        "print('COMMAND_STARTED id=fake session=codex-android-0123456789abcdef')\n"
        "print('REMOTE_ROOT=/srv/android/TVE1097M')\n"
        "print('PLATFORM=mtk')\n"
        "print('SDK_NAME=TVE1097M')\n"
        "print('SOURCE_PLATFORM=mtk')\n"
        "print('SOURCE_SDK_NAME=TVE1097M')\n"
        "print('SOURCE_SDK_SOURCE=project_branch')\n"
        "print('__CODEX_CMD_DONE id=fake state=completed rc=0')\n",
        encoding="utf-8",
    )
    fake_channel.chmod(0o755)

    result = subprocess.run(
        [
            sys.executable,
            str(INSPECTION_HELPER),
            "--channel-script",
            str(fake_channel),
            "--ssh-host",
            "builder",
            "--remote-root",
            "/srv/android/TVE1097M",
            "--command-id",
            "fake-inspection",
        ],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "FAKE_CHANNEL_LOG": str(log)},
    )

    assert result.returncode == 0, result.stderr
    values = parse_simple_env(result.stdout)
    assert values["PROJECT_IDENTITY_SCHEMA"] == "android-remote-project-identity-v1"
    assert values["PROJECT_ID"] == "mtk-TVE1097M"
    assert values["WORKSPACE_ID"] == "0123456789abcdef"
    assert values["INSPECTION_TRANSPORT"] == "android-remote-channel-v2"
    assert values["SSH_HOST"] == "builder"
    assert values["REMOTE_ROOT"] == "/srv/android/TVE1097M"
    logged = log.read_text(encoding="utf-8")
    args = logged.splitlines()
    assert args[:4] == ["--ssh-host", "builder", "--remote-root", "/srv/android/TVE1097M"]
    assert "run" in args
    assert "none" in args
    assert "bash -s --" in logged
    assert "score_rk=0" in logged


def test_equal_platform_scores_keep_current_wsl_tie_break(tmp_path: Path) -> None:
    root = tmp_path / "tie-sdk"
    (root / "build").mkdir(parents=True)
    (root / "vendor").mkdir()
    (root / "hardware").mkdir()
    (root / "device" / "rockchip").mkdir(parents=True)
    (root / "device" / "sprd").mkdir(parents=True)
    (root / "device" / "rockchip" / "BoardConfig.mk").write_text(
        "BRANCH_BUILDTYPE := TIE-SDK\n",
        encoding="utf-8",
    )

    result = run_inspector(CANONICAL, root)

    assert result.returncode == 0, result.stderr
    values = parse_simple_env(result.stdout)
    assert values["PLATFORM"] == "rk"
    assert values["SDK_NAME"] == "TIE-SDK"
    assert values["PLATFORM_SCORE_RK"] == values["PLATFORM_SCORE_UNISOC"] == "30"


def test_explicit_platform_conflict_keeps_existing_exit_contract(tmp_path: Path) -> None:
    root = tmp_path / "rk-sdk"
    (root / "build").mkdir(parents=True)
    (root / "device" / "rockchip" / "rk3588").mkdir(parents=True)
    (root / "device" / "rockchip" / "rk3588" / "BoardConfig.mk").write_text(
        "TARGET_BOARD_PLATFORM := rk3588\nBRANCH_BUILDTYPE := TVA10A2R\n",
        encoding="utf-8",
    )

    rejected = run_inspector(CANONICAL, root, platform="unisoc")
    accepted = run_inspector(
        CANONICAL,
        root,
        platform="unisoc",
        sdk_name="TVA10A2R",
        accept_platform_conflict=True,
    )

    assert rejected.returncode == 7
    assert "PLATFORM_CONFLICT user_platform=unisoc source_platform=rk" in rejected.stderr
    assert accepted.returncode == 0, accepted.stderr
    assert parse_simple_env(accepted.stdout)["PLATFORM"] == "unisoc"


def test_missing_android_markers_and_sdk_name_fail_closed(tmp_path: Path) -> None:
    missing_markers = tmp_path / "not-android"
    missing_markers.mkdir()
    no_name = tmp_path / "no-name"
    (no_name / "build").mkdir(parents=True)
    (no_name / "vendor" / "sprd").mkdir(parents=True)

    marker_result = run_inspector(CANONICAL, missing_markers)
    name_result = run_inspector(CANONICAL, no_name)

    assert marker_result.returncode == 4
    assert "ANDROID_MARKERS_MISSING" in marker_result.stderr
    assert name_result.returncode == 6
    assert "SDK_NAME_REQUIRED" in name_result.stderr
