from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CANONICAL = REPO_ROOT / "shared" / "akbs_plugin_state" / "atomic.py"
RUNTIME_COPIES = (
    REPO_ROOT / "plugins/android-engineering-ops/adapters/source-access/macos/lib/akbs_plugin_state/atomic.py",
    REPO_ROOT / "plugins/android-engineering-ops/adapters/source-access/wsl/lib/akbs_plugin_state/atomic.py",
)


def test_atomic_state_runtime_copies_match_the_repository_owner() -> None:
    expected = CANONICAL.read_bytes()
    assert expected
    for runtime_copy in RUNTIME_COPIES:
        assert runtime_copy.read_bytes() == expected, runtime_copy
