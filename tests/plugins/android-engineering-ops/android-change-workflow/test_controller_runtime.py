from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[4]
PLUGIN = ROOT / "plugins/android-engineering-ops"
PLUGIN_LIB = PLUGIN / "lib"
if str(PLUGIN_LIB) not in sys.path:
    sys.path.insert(0, str(PLUGIN_LIB))

from android_engineering_ops.workflow import (  # noqa: E402
    ControllerValidationError,
    canonical_json_sha256,
    generate_stage_snapshot,
    generate_worker_assignment,
    generate_worker_result,
    stage_context_sha256,
    validate_stage_snapshot,
    validate_worker_assignment,
    validate_worker_result,
)


A = "a" * 40
B = "b" * 40
C = "c" * 64
D = "d" * 64
E = "e" * 64
PROVIDER_NONE = {
    "selection_mode": "none",
    "coding": {"source": "core", "reason": "mode_none"},
    "execution": {"source": "core", "reason": "mode_none"},
}


def mutation_chain() -> tuple[dict, dict, dict, str]:
    snapshot = generate_stage_snapshot(
        {
            "snapshot_id": "snapshot-1",
            "run_id": "run-1",
            "sequence": 1,
            "created_at": "2026-09-03T01:00:00Z",
            "snapshot_reason": "delegating_worker",
            "stage": {
                "stage_id": "stage-implementation",
                "gate_index": 3,
                "state": "active",
                "risk_level": "medium",
            },
            "input_bindings": {
                "requirement_sha256": "1" * 64,
                "core_policy_sha256": "2" * 64,
                "extension_config_sha256": "3" * 64,
            },
            "provider_resolution": PROVIDER_NONE,
            "workspace_bindings": [
                {
                    "repository_id": "frameworks-base",
                    "source_authority": "registered_remote_tree",
                    "workspace_id": "workspace-1",
                    "base_revision": A,
                    "target_expected_head": B,
                    "scope": ["services/core"],
                    "authority_ref": "authority-1",
                }
            ],
            "event": {
                "type": "assignment_planned",
                "planned_assignment_id": "assignment-1",
                "planned_worker_task_id": "worker-1",
            },
        }
    )
    context = stage_context_sha256(snapshot)
    assignment = generate_worker_assignment(
        {
            "assignment_id": "assignment-1",
            "worker_task_id": "worker-1",
            "run_id": "run-1",
            "attempt": 1,
            "issued_at": "2026-09-03T01:00:01Z",
            "source_snapshot_sha256": canonical_json_sha256(snapshot),
            "assignee": {
                "profile_source": "core",
                "worker_profile_id": "implementation-worker",
            },
            "effect": "workspace_mutation",
            "objective": "Modify the bounded service implementation",
            "input_bindings": {
                "requirement_sha256": "1" * 64,
                "context_sha256": context,
            },
            "scope": {
                "repositories": ["frameworks-base"],
                "paths": [
                    {
                        "repository_id": "frameworks-base",
                        "kind": "tree",
                        "path": "services/core",
                    }
                ],
            },
            "constraints": {
                "authority_ref": "authority-1",
                "workspace_bindings": [
                    {
                        "repository_id": "frameworks-base",
                        "workspace_id": "workspace-1",
                        "base_revision": A,
                        "target_expected_head": B,
                        "paths": [
                            {
                                "repository_id": "frameworks-base",
                                "kind": "tree",
                                "path": "services/core",
                            }
                        ],
                        "authority_ref": "authority-1",
                    }
                ],
            },
            "required_evidence": ["change-diff", "targeted-test"],
        }
    )
    result = generate_worker_result(
        {
            "result_id": "result-1",
            "run_id": "run-1",
            "assignment_id": "assignment-1",
            "assignment_sha256": canonical_json_sha256(assignment),
            "attempt": 1,
            "worker_binding": {
                "worker_task_id": "worker-1",
                "worker_profile_id": "implementation-worker",
            },
            "started_at": "2026-09-03T01:00:02Z",
            "completed_at": "2026-09-03T01:01:00Z",
            "outcome": "completed",
            "observed_workspaces": [
                {
                    "repository_id": "frameworks-base",
                    "workspace_id": "workspace-1",
                    "base_revision": A,
                    "start_head": A,
                    "end_head": B,
                }
            ],
            "reported_changes": [
                {
                    "repository_id": "frameworks-base",
                    "operation": "modify",
                    "path": "services/core/Feature.java",
                    "before_sha256": "4" * 64,
                    "after_sha256": "5" * 64,
                }
            ],
            "commands": [],
            "checks": [
                {"check_id": "unit", "status": "passed", "receipt_sha256": C}
            ],
            "evidence": [
                {
                    "evidence_id": "diff-1",
                    "kind": "change-diff",
                    "uri": "artifact:diff-1",
                    "sha256": D,
                    "size_bytes": 10,
                },
                {
                    "evidence_id": "test-1",
                    "kind": "targeted-test",
                    "uri": "artifact:test-1",
                    "sha256": E,
                    "size_bytes": 20,
                },
            ],
            "reported_scope_deviations": [],
            "summary": "Bounded implementation and verification complete",
        }
    )
    return snapshot, assignment, result, context


def expected(context: str) -> dict:
    return {
        "expected_run_id": "run-1",
        "expected_stage_id": "stage-implementation",
        "expected_context_sha256": context,
        "expected_provider_resolution": PROVIDER_NONE,
    }


def assignment_expected(context: str, worker_task_id: str = "worker-1") -> dict:
    return {**expected(context), "expected_worker_task_id": worker_task_id}


def controller_readback(result: dict, worker_task_id: str = "worker-1") -> dict:
    return {
        "expected_worker_task_id": worker_task_id,
        "expected_end_heads": {
            item["repository_id"]: item["end_head"]
            for item in result["observed_workspaces"]
        },
        "expected_changes": copy.deepcopy(result["reported_changes"]),
        "expected_evidence": copy.deepcopy(result["evidence"]),
        "expected_checks": copy.deepcopy(result["checks"]),
        "expected_commands": copy.deepcopy(result["commands"]),
    }


def test_runtime_generates_and_validates_exact_mutation_chain() -> None:
    snapshot, assignment, result, context = mutation_chain()
    validate_stage_snapshot(snapshot, **expected(context))
    validate_worker_assignment(
        assignment, source_snapshot=snapshot, **assignment_expected(context)
    )
    validate_worker_result(
        result,
        assignment=assignment,
        source_snapshot=snapshot,
        **controller_readback(result),
        **expected(context),
    )
    assert snapshot["controller"]["id"] == "android-change-workflow"
    assert assignment["permissions"] == {
        "may_acquire_authority": False,
        "may_expand_scope": False,
        "may_upload": False,
        "may_accept_gate": False,
        "may_final_accept": False,
    }


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("run", "old-run", "run_id differs"),
        ("stage", "old-stage", "stage_id differs"),
        ("context", "0" * 64, "context differs"),
    ],
)
def test_snapshot_replay_is_rejected(
    field: str, value: str, match: str,
) -> None:
    snapshot, _, _, context = mutation_chain()
    values = expected(context)
    values[f"expected_{field}_id" if field != "context" else "expected_context_sha256"] = value
    with pytest.raises(ControllerValidationError, match=match):
        validate_stage_snapshot(snapshot, **values)


def test_assignment_replay_and_provider_substitution_are_rejected() -> None:
    snapshot, assignment, _, context = mutation_chain()
    stale = copy.deepcopy(assignment)
    stale["source_snapshot_sha256"] = "0" * 64
    with pytest.raises(ControllerValidationError, match="source snapshot hash"):
        validate_worker_assignment(
            stale, source_snapshot=snapshot, **assignment_expected(context)
        )

    substituted = copy.deepcopy(assignment)
    substituted["assignee"] = {
        "profile_source": "provider",
        "worker_profile_id": "provider-worker",
        "provider_id": "acme-android-practices",
        "provider_version": "2.0.1",
        "provider_manifest_sha256": "9" * 64,
    }
    with pytest.raises(ControllerValidationError, match="cannot assign provider"):
        validate_worker_assignment(
            substituted, source_snapshot=snapshot, **assignment_expected(context)
        )

    wrong_worker = copy.deepcopy(assignment)
    wrong_worker["worker_task_id"] = "worker-old"
    with pytest.raises(ControllerValidationError, match="assignment/task was not planned"):
        validate_worker_assignment(
            wrong_worker, source_snapshot=snapshot, **assignment_expected(context)
        )


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        ("scope", "escapes assignment scope"),
        ("base", "workspace/base binding differs"),
        ("end", "end head differs"),
        ("deviation", "reports a scope deviation"),
        ("evidence", "lacks assignment-required evidence"),
    ],
)
def test_result_scope_workspace_target_evidence_and_deviation_fail_closed(
    mutation: str, match: str,
) -> None:
    snapshot, assignment, result, context = mutation_chain()
    invalid = copy.deepcopy(result)
    if mutation == "scope":
        invalid["reported_changes"][0]["path"] = "packages/Outside.java"
    elif mutation == "base":
        invalid["observed_workspaces"][0]["base_revision"] = "9" * 40
    elif mutation == "end":
        invalid["observed_workspaces"][0]["end_head"] = "9" * 40
    elif mutation == "deviation":
        invalid["reported_scope_deviations"] = ["touched generated file"]
    else:
        invalid["evidence"] = invalid["evidence"][:1]
    with pytest.raises(ControllerValidationError, match=match):
        validate_worker_result(
            invalid,
            assignment=assignment,
            source_snapshot=snapshot,
            **controller_readback(invalid),
            **expected(context),
        )


def test_result_requires_every_controller_owned_readback_expectation() -> None:
    snapshot, assignment, result, context = mutation_chain()
    with pytest.raises(ControllerValidationError, match="expected worker_task_id is required"):
        validate_worker_result(
            result,
            assignment=assignment,
            source_snapshot=snapshot,
            **expected(context),
        )
    with pytest.raises(ControllerValidationError, match="expected evidence readback is required"):
        validate_worker_result(
            result,
            assignment=assignment,
            source_snapshot=snapshot,
            expected_worker_task_id="worker-1",
            **expected(context),
        )


@pytest.mark.parametrize(
    ("field", "match"),
    [
        ("worker", "exact assignment"),
        ("evidence_hash", "evidence bytes/hash/size"),
        ("evidence_size", "evidence bytes/hash/size"),
        ("check", "check status/receipt"),
        ("change", "path/hash changes"),
        ("end", "end heads"),
    ],
)
def test_worker_self_report_cannot_replace_controller_readback(
    field: str, match: str,
) -> None:
    snapshot, assignment, result, context = mutation_chain()
    reported = copy.deepcopy(result)
    if field == "worker":
        reported["worker_binding"]["worker_task_id"] = "worker-replay"
    elif field == "evidence_hash":
        reported["evidence"][0]["sha256"] = "9" * 64
    elif field == "evidence_size":
        reported["evidence"][0]["size_bytes"] = 999
    elif field == "check":
        reported["checks"][0]["status"] = "failed"
    elif field == "change":
        reported["reported_changes"][0]["after_sha256"] = "9" * 64
    else:
        reported["observed_workspaces"][0]["end_head"] = "9" * 40
    with pytest.raises(ControllerValidationError, match=match):
        validate_worker_result(
            reported,
            assignment=assignment,
            source_snapshot=snapshot,
            **controller_readback(result),
            **expected(context),
        )


def test_controlled_operation_binds_exact_command_request_and_receipt() -> None:
    snapshot = generate_stage_snapshot(
        {
            "snapshot_id": "snapshot-operation",
            "run_id": "run-operation",
            "sequence": 1,
            "created_at": "2026-09-03T02:00:00Z",
            "snapshot_reason": "delegating_worker",
            "stage": {
                "stage_id": "stage-operation",
                "gate_index": 4,
                "state": "active",
                "risk_level": "medium",
            },
            "input_bindings": {
                "requirement_sha256": "1" * 64,
                "core_policy_sha256": "2" * 64,
                "extension_config_sha256": "3" * 64,
            },
            "provider_resolution": PROVIDER_NONE,
            "workspace_bindings": [],
            "event": {
                "type": "assignment_planned",
                "planned_assignment_id": "assignment-operation",
                "planned_worker_task_id": "worker-operation",
            },
        }
    )
    context = stage_context_sha256(snapshot)
    request_sha = "6" * 64
    assignment = generate_worker_assignment(
        {
            "assignment_id": "assignment-operation",
            "worker_task_id": "worker-operation",
            "run_id": "run-operation",
            "attempt": 1,
            "issued_at": "2026-09-03T02:00:01Z",
            "source_snapshot_sha256": canonical_json_sha256(snapshot),
            "assignee": {
                "profile_source": "core",
                "worker_profile_id": "operation-worker",
            },
            "effect": "controlled_operation",
            "objective": "Run one frozen deployment command",
            "input_bindings": {
                "context_sha256": context,
                "command_request_sha256": request_sha,
            },
            "scope": {
                "repositories": ["device-target"],
                "paths": [
                    {"repository_id": "device-target", "kind": "file", "path": "artifact.bin"}
                ],
            },
            "constraints": {
                "authority_ref": "operation-authority",
                "controlled_operation": {
                    "skill_id": "android-remote-build-deploy",
                    "profile_id": "device-1",
                    "command_id": "command-1",
                    "artifact_sha256": "7" * 64,
                    "device_id": "device-1",
                    "rollback_id": "rollback-1",
                },
            },
            "required_evidence": ["operation-receipt"],
        }
    )
    result = generate_worker_result(
        {
            "result_id": "result-operation",
            "run_id": "run-operation",
            "assignment_id": "assignment-operation",
            "assignment_sha256": canonical_json_sha256(assignment),
            "attempt": 1,
            "worker_binding": {
                "worker_task_id": "worker-operation",
                "worker_profile_id": "operation-worker",
            },
            "started_at": "2026-09-03T02:00:02Z",
            "completed_at": "2026-09-03T02:00:03Z",
            "outcome": "completed",
            "observed_workspaces": [],
            "reported_changes": [],
            "commands": [
                {
                    "command_id": "command-1",
                    "request_sha256": request_sha,
                    "receipt_sha256": "8" * 64,
                    "exit_code": 0,
                }
            ],
            "checks": [
                {"check_id": "receipt", "status": "passed", "receipt_sha256": "8" * 64}
            ],
            "evidence": [
                {
                    "evidence_id": "operation-1",
                    "kind": "operation-receipt",
                    "uri": "artifact:operation-1",
                    "sha256": "8" * 64,
                    "size_bytes": 1,
                }
            ],
            "reported_scope_deviations": [],
            "summary": "Frozen command completed",
        }
    )
    values = {
        "expected_run_id": "run-operation",
        "expected_stage_id": "stage-operation",
        "expected_context_sha256": context,
        "expected_provider_resolution": PROVIDER_NONE,
    }
    validate_worker_result(
        result,
        assignment=assignment,
        source_snapshot=snapshot,
        **controller_readback(result, "worker-operation"),
        **values,
    )
    replay = copy.deepcopy(result)
    replay["commands"][0]["request_sha256"] = "0" * 64
    with pytest.raises(
        ControllerValidationError,
        match="command request/receipt/exit differs from controller readback",
    ):
        validate_worker_result(
            replay,
            assignment=assignment,
            source_snapshot=snapshot,
            **controller_readback(result, "worker-operation"),
            **values,
        )


def test_cli_hash_and_validation_are_available_from_installed_skill(tmp_path: Path) -> None:
    snapshot, assignment, result, context = mutation_chain()
    snapshot_path = tmp_path / "snapshot.json"
    assignment_path = tmp_path / "assignment.json"
    result_path = tmp_path / "result.json"
    provider_path = tmp_path / "provider.json"
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
    assignment_path.write_text(json.dumps(assignment), encoding="utf-8")
    result_path.write_text(json.dumps(result), encoding="utf-8")
    provider_path.write_text(json.dumps(PROVIDER_NONE), encoding="utf-8")
    readback = controller_readback(result)
    end_heads_path = tmp_path / "end-heads.json"
    changes_path = tmp_path / "changes.json"
    evidence_path = tmp_path / "evidence.json"
    checks_path = tmp_path / "checks.json"
    commands_path = tmp_path / "commands.json"
    for path, value in (
        (end_heads_path, readback["expected_end_heads"]),
        (changes_path, readback["expected_changes"]),
        (evidence_path, readback["expected_evidence"]),
        (checks_path, readback["expected_checks"]),
        (commands_path, readback["expected_commands"]),
    ):
        path.write_text(json.dumps(value), encoding="utf-8")
    script = PLUGIN / "skills/android-change-workflow/scripts/android_change_controller.py"
    hashed = subprocess.run(
        [sys.executable, str(script), "hash", "--document", str(snapshot_path)],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert hashed.returncode == 0, hashed.stderr
    assert hashed.stdout.strip() == canonical_json_sha256(snapshot)
    validated = subprocess.run(
        [
            sys.executable,
            str(script),
            "validate-stage",
            "--document", str(snapshot_path),
            "--expected-run-id", "run-1",
            "--expected-stage-id", "stage-implementation",
            "--expected-context-sha256", context,
            "--expected-provider-resolution", str(provider_path),
        ],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert validated.returncode == 0, validated.stderr
    assert json.loads(validated.stdout)["status"] == "valid"

    result_command = [
        sys.executable,
        str(script),
        "validate-result",
        "--document", str(result_path),
        "--assignment", str(assignment_path),
        "--snapshot", str(snapshot_path),
        "--expected-worker-task-id", "worker-1",
        "--expected-end-heads", str(end_heads_path),
        "--expected-changes", str(changes_path),
        "--expected-evidence", str(evidence_path),
        "--expected-checks", str(checks_path),
        "--expected-commands", str(commands_path),
        "--expected-run-id", "run-1",
        "--expected-stage-id", "stage-implementation",
        "--expected-context-sha256", context,
        "--expected-provider-resolution", str(provider_path),
    ]
    result_validated = subprocess.run(
        result_command,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert result_validated.returncode == 0, result_validated.stderr
    assert json.loads(result_validated.stdout)["status"] == "valid"

    missing_readback = result_command[:]
    index = missing_readback.index("--expected-checks")
    del missing_readback[index:index + 2]
    rejected = subprocess.run(
        missing_readback,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert rejected.returncode != 0
    assert "--expected-checks" in rejected.stderr
