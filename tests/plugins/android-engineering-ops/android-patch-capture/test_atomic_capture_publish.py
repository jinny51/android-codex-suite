from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import errno
import os
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[4]
PLUGIN = ROOT / "plugins/android-engineering-ops"
LIB = PLUGIN / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from android_engineering_ops.atomic_package import (  # noqa: E402
    AtomicPackageError,
    publish_tree_atomic,
)


def source_tree(root: Path, text: str) -> Path:
    source = root
    (source / "patches").mkdir(parents=True)
    (source / "evidence").mkdir()
    (source / "README.md").write_text(text, encoding="utf-8")
    (source / "patches/change.patch").write_text("diff fixture\n", encoding="utf-8")
    (source / "evidence/check.json").write_text("{}\n", encoding="utf-8")
    return source


def test_atomic_publish_commits_whole_tree_once_and_never_overwrites(tmp_path: Path) -> None:
    source = source_tree(tmp_path / "source", "first\n")
    destination = tmp_path / "packages/run-1"
    assert publish_tree_atomic(source, destination) == destination
    assert (destination / "README.md").read_text(encoding="utf-8") == "first\n"
    assert not list(destination.parent.glob(".run-1.staging-*"))

    replacement = source_tree(tmp_path / "replacement", "replacement\n")
    with pytest.raises(AtomicPackageError, match="already exists"):
        publish_tree_atomic(replacement, destination)
    assert (destination / "README.md").read_text(encoding="utf-8") == "first\n"
    assert not list(destination.parent.glob(".run-1.staging-*"))


def test_atomic_publish_rejects_symbolic_or_special_source_without_half_package(
    tmp_path: Path,
) -> None:
    source = source_tree(tmp_path / "source", "source\n")
    (source / "escape").symlink_to(tmp_path)
    destination = tmp_path / "packages/run-symlink"
    with pytest.raises(AtomicPackageError, match="symlink"):
        publish_tree_atomic(source, destination)
    assert not destination.exists()
    assert not list(destination.parent.glob(".run-symlink.staging-*"))

    (source / "escape").unlink()
    try:
        os.mkfifo(source / "unexpected.fifo")
    except OSError as exc:
        if exc.errno in {errno.EOPNOTSUPP, errno.ENOSYS}:
            pytest.skip("temporary filesystem does not support FIFO fixtures")
        raise
    failed = tmp_path / "packages/run-special"
    with pytest.raises(AtomicPackageError, match="special entry"):
        publish_tree_atomic(source, failed)
    assert not failed.exists()
    assert not list(failed.parent.glob(".run-special.staging-*"))


def test_atomic_publish_rejects_symlinked_destination_parent(tmp_path: Path) -> None:
    source = source_tree(tmp_path / "source", "source\n")
    real = tmp_path / "real-packages"
    real.mkdir()
    link = tmp_path / "linked-packages"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(AtomicPackageError, match="symbolic"):
        publish_tree_atomic(source, link / "run-1")
    assert not (real / "run-1").exists()


def test_concurrent_publish_has_one_complete_winner(tmp_path: Path) -> None:
    first = source_tree(tmp_path / "first", "first\n")
    second = source_tree(tmp_path / "second", "second\n")
    destination = tmp_path / "packages/concurrent"

    def attempt(source: Path) -> str:
        try:
            publish_tree_atomic(source, destination)
        except AtomicPackageError:
            return "rejected"
        return "published"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = sorted(pool.map(attempt, (first, second)))
    assert outcomes == ["published", "rejected"]
    assert (destination / "README.md").read_text(encoding="utf-8") in {
        "first\n", "second\n"
    }
    assert (destination / "patches/change.patch").is_file()
    assert (destination / "evidence/check.json").is_file()
    assert not list(destination.parent.glob(".concurrent.staging-*"))


def test_capture_uses_single_v1_handoff_contract() -> None:
    package_schema = PLUGIN / "contracts/incoming/v2/akbs-android-change-package.schema.json"
    assert not package_schema.exists()
    script = (
        PLUGIN
        / "skills/android-patch-capture/scripts/capture_android_patch.py"
    ).read_text(encoding="utf-8")
    assert '"package_type": "android_feature_patch"' in script
    assert '"components": component_rows(patch_layers)' in script
    assert "qualification" not in script
    reference = (
        PLUGIN
        / "skills/android-patch-capture/references/package-contract.md"
    ).read_text(encoding="utf-8")
    assert "knowledge-incoming-package / schema_version 1 / android_change" in reference
    assert "adapt-capture" not in reference
