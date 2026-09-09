from __future__ import annotations

import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
PLUGIN_SOURCE = ROOT / "plugins/android-engineering-ops"
RUNTIME_ENTRYPOINT = (
    PLUGIN_SOURCE
    / "skills/android-change-workflow/scripts/extract_video_frames.py"
)
INSTALLED_RUNTIME_ENTRYPOINTS = ("RUNTIME_ENTRYPOINT",)


def test_source_and_runtime_fixture_boundaries_are_explicit() -> None:
    expected_runtime = (
        Path(os.environ["CODEX_HOME"])
        / "plugins/cache/android-codex-suite/android-engineering-ops/2.0.2"
    )
    assert PLUGIN_SOURCE == ROOT / "plugins/android-engineering-ops"
    assert RUNTIME_ENTRYPOINT == (
        expected_runtime
        / "skills/android-change-workflow/scripts/extract_video_frames.py"
    )
    assert PLUGIN_SOURCE != expected_runtime
    assert RUNTIME_ENTRYPOINT.is_file()
