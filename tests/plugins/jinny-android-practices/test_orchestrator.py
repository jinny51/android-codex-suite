from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
PLUGIN = ROOT / "plugins/jinny-android-practices"


def test_manifest_exposes_only_the_minimal_orchestrator_binding() -> None:
    plugin = json.loads((PLUGIN / ".codex-plugin/plugin.json").read_text())
    extension = json.loads(
        (PLUGIN / "contracts/android-orchestration-extension/v1/extension.json").read_text()
    )
    assert plugin["version"] == "3.0.0"
    assert extension == {
        "schema": "android-orchestration-extension-v1",
        "provider_id": "jinny-android-practices",
        "provider_version": "3.0.0",
        "compatible_core_contracts": ["android-engineering-orchestration-v1"],
        "orchestrator": {"skill_id": "jinny-android-orchestrator"},
    }


def test_orchestrator_is_real_but_bounded_and_old_runtime_is_absent() -> None:
    skill = (PLUGIN / "skills/jinny-android-orchestrator/SKILL.md").read_text()
    assert "real subagents" in skill
    assert "Multiple files" in skill
    assert "one writer per file or subsystem" in skill
    assert "user's explicit model or reasoning choice wins" in skill
    assert not (PLUGIN / "skills/jinny-android-execution-policy").exists()
    assert not (PLUGIN / "lib/jinny_android_practices/decision.py").exists()
    assert not (PLUGIN / "contracts/android-practices-provider").exists()
