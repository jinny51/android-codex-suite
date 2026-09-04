from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
CORE = ROOT / "plugins/android-engineering-ops"
PLUGIN = ROOT / "plugins/jinny-android-practices"
CORE_LIB = CORE / "lib"
if str(CORE_LIB) not in sys.path:
    sys.path.insert(0, str(CORE_LIB))

from android_engineering_ops.practices.schema import validate_document  # noqa: E402


PROVIDER = PLUGIN / "contracts/android-practices-provider/v1/provider.json"
CODING = (
    PLUGIN
    / "skills/jinny-android-coding-practices/scripts/jinny_coding_policy.py"
)
EXECUTION = (
    PLUGIN
    / "skills/jinny-android-execution-policy/scripts/jinny_execution_policy.py"
)


def run_json(command: list[str]) -> tuple[int, dict, str]:
    result = subprocess.run(
        command,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.returncode, json.loads(result.stdout), result.stderr


def test_provider_manifest_has_exact_durable_profiles_and_no_authority() -> None:
    provider = json.loads(PROVIDER.read_text(encoding="utf-8"))
    validate_document(
        provider,
        CORE / "contracts/android-practices-provider/v1/provider.schema.json",
    )
    profiles = provider["capabilities"]["execution"]["worker_profiles"]
    assert profiles == {
        "sol-analysis-review": {
            "dispatch": {"model_id": "gpt-5.6-sol", "reasoning_effort": "max"},
            "task_classes": ["analysis", "diagnosis", "review"],
            "effect_ceiling": "read_only",
        },
        "terra-implementation": {
            "dispatch": {"model_id": "gpt-5.6-terra", "reasoning_effort": "high"},
            "task_classes": ["implementation", "analysis", "diagnosis"],
            "effect_ceiling": "workspace_mutation",
        },
        "luna-verification-operation": {
            "dispatch": {"model_id": "gpt-5.6-luna", "reasoning_effort": "medium"},
            "task_classes": ["analysis", "diagnosis", "verification", "bounded_operation"],
            "effect_ceiling": "controlled_operation",
        },
    }
    authority = provider["authority"]
    assert authority["decision_only"] is True
    assert not any(value for key, value in authority.items() if key != "decision_only")
    for capability in ("coding", "execution"):
        declaration = provider["capabilities"][capability]
        skill_root = PLUGIN / "skills" / declaration["skill_id"]
        expected = {
            "skill_sha256": skill_root / "SKILL.md",
            "agent_metadata_sha256": skill_root / "agents/openai.yaml",
            "decision_entrypoint_sha256": PLUGIN / declaration["decision_entrypoint_path"],
        }
        for field, path in expected.items():
            assert declaration[field] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_plugin_metadata_is_neutral_read_only_interface() -> None:
    plugin = json.loads((PLUGIN / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
    assert plugin["version"] == "2.0.1"
    assert plugin["author"]["name"] == "Jinny Android Team"
    assert plugin["interface"]["developerName"] == "Jinny Android Team"
    assert plugin["interface"]["capabilities"] == ["Interactive", "Read"]


def test_all_behavioral_coding_guidance_is_in_the_hash_bound_skill() -> None:
    skill_root = PLUGIN / "skills/jinny-android-coding-practices"
    skill = (skill_root / "SKILL.md").read_text(encoding="utf-8")
    assert "references/" not in skill
    assert not list((skill_root / "references").glob("**/*"))
    assert "controller-resolved `member_alias`" in skill
    assert "Every returned rule uses `effect=recommend`" in skill


def test_each_entrypoint_binds_and_rejects_a_changed_shared_helper(
    tmp_path: Path,
) -> None:
    helper = PLUGIN / "lib/jinny_android_practices/decision.py"
    helper_sha = hashlib.sha256(helper.read_bytes()).hexdigest()
    for entrypoint in (CODING, EXECUTION):
        text = entrypoint.read_text(encoding="utf-8")
        match = re.search(r'^DECISION_HELPER_SHA256 = "([0-9a-f]{64})"$', text, re.MULTILINE)
        assert match is not None
        assert match.group(1) == helper_sha

    copied = tmp_path / "jinny-android-practices"
    shutil.copytree(PLUGIN, copied)
    copied_helper = copied / "lib/jinny_android_practices/decision.py"
    copied_helper.write_bytes(copied_helper.read_bytes() + b"\n# changed after resolution\n")
    commands = [
        [
            sys.executable,
            str(copied / "skills/jinny-android-coding-practices/scripts/jinny_coding_policy.py"),
            "--decision-id", "decision-helper-coding",
            "--run-id", "run-helper",
            "--stage-id", "stage-helper",
            "--context-sha256", "1" * 64,
            "--core-policy-sha256", "2" * 64,
            "--component-layer", "platform",
        ],
        [
            sys.executable,
            str(copied / "skills/jinny-android-execution-policy/scripts/jinny_execution_policy.py"),
            "--decision-id", "decision-helper-execution",
            "--run-id", "run-helper",
            "--stage-id", "stage-helper",
            "--context-sha256", "1" * 64,
            "--task-class", "analysis",
            "--requested-effect", "read_only",
        ],
    ]
    for command in commands:
        result = subprocess.run(
            command,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert result.returncode == 2
        assert not result.stdout
        assert "Jinny decision helper SHA-256 differs" in result.stderr


def test_coding_cli_emits_schema_and_hash_bound_decision() -> None:
    code, decision, stderr = run_json(
        [
            sys.executable,
            str(CODING),
            "--decision-id", "decision-1",
            "--run-id", "run-1",
            "--stage-id", "stage-1",
            "--context-sha256", "1" * 64,
            "--core-policy-sha256", "2" * 64,
            "--component-layer", "platform",
        ]
    )
    assert code == 0, stderr
    validate_document(
        decision,
        CORE / "contracts/android-practices-provider/v1/coding-policy-decision.schema.json",
    )
    assert decision["provider"]["provider_manifest_sha256"] == hashlib.sha256(
        PROVIDER.read_bytes()
    ).hexdigest()
    assert decision["provider"]["skill_sha256"] == hashlib.sha256(
        (PLUGIN / "skills/jinny-android-coding-practices/SKILL.md").read_bytes()
    ).hexdigest()
    assert decision["provider"]["agent_metadata_sha256"] == hashlib.sha256(
        (PLUGIN / "skills/jinny-android-coding-practices/agents/openai.yaml").read_bytes()
    ).hexdigest()
    assert decision["provider"]["decision_entrypoint_sha256"] == hashlib.sha256(
        CODING.read_bytes()
    ).hexdigest()
    assert {rule["effect"] for rule in decision["outcome"]["rules"]} == {"recommend"}


def execution_command(
    task_class: str,
    effect: str,
    ceiling: str | None = None,
    *,
    risk: str = "medium",
    shape: str = "normal",
    ambiguity: str = "medium",
    code_judgment: str = "ordinary",
) -> list[str]:
    value = [
        sys.executable,
        str(EXECUTION),
        "--decision-id", "decision-2",
        "--run-id", "run-1",
        "--stage-id", "stage-2",
        "--context-sha256", "3" * 64,
        "--task-class", task_class,
        "--requested-effect", effect,
        "--risk-level", risk,
        "--shape", shape,
        "--ambiguity", ambiguity,
        "--code-judgment", code_judgment,
    ]
    if ceiling:
        value.extend(["--rollout-effect-ceiling", ceiling])
    return value


def test_execution_cli_keeps_capability_but_phase2_rollout_fails_closed() -> None:
    code, blocked, stderr = run_json(
        execution_command("implementation", "workspace_mutation")
    )
    assert code == 0, stderr
    assert blocked["outcome"] == {
        "type": "blocked",
        "reason_codes": ["rollout-effect-ceiling-exceeded"],
    }
    code, delegated, stderr = run_json(
        execution_command("implementation", "workspace_mutation", "workspace_mutation")
    )
    assert code == 0, stderr
    assert delegated["outcome"]["worker_profile_id"] == "terra-implementation"
    assert delegated["outcome"]["requested_effect"] == "workspace_mutation"
    validate_document(
        delegated,
        CORE / "contracts/android-practices-provider/v1/execution-policy-decision.schema.json",
    )


def test_execution_routes_by_shape_risk_judgment_and_side_effect() -> None:
    _, luna_narrow, _ = run_json(
        execution_command(
            "analysis", "read_only", shape="narrow", ambiguity="low", code_judgment="none"
        )
    )
    assert luna_narrow["outcome"]["worker_profile_id"] == "luna-verification-operation"
    assert luna_narrow["outcome"]["reason_codes"] == [
        "explicit-repeated-or-narrow-extraction"
    ]
    _, terra_diagnosis, _ = run_json(
        execution_command("diagnosis", "read_only", ambiguity="medium")
    )
    assert terra_diagnosis["outcome"]["worker_profile_id"] == "terra-implementation"
    _, sol, _ = run_json(
        execution_command(
            "analysis", "read_only", risk="high", ambiguity="high",
            code_judgment="architecture",
        )
    )
    assert sol["outcome"]["worker_profile_id"] == "sol-analysis-review"
    _, final_review, _ = run_json(execution_command("review", "read_only"))
    assert final_review["outcome"]["worker_profile_id"] == "sol-analysis-review"
    _, terra_implementation, _ = run_json(
        execution_command("implementation", "workspace_mutation", "workspace_mutation")
    )
    assert terra_implementation["outcome"]["worker_profile_id"] == "terra-implementation"
    _, luna, _ = run_json(
        execution_command("bounded_operation", "controlled_operation", "controlled_operation")
    )
    assert luna["outcome"]["worker_profile_id"] == "luna-verification-operation"
    serialized = json.dumps(
        [luna_narrow, terra_diagnosis, sol, final_review, terra_implementation, luna],
        sort_keys=True,
    )
    assert "final_accept" not in serialized
    assert "accept_gate" not in serialized


def test_legacy_skill_wrapper_is_absent() -> None:
    wrapper = PLUGIN / "skills/jinny-framework-coding-standards"
    assert not wrapper.exists()
