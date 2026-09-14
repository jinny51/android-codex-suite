from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "scripts/validate_active_plugin_topology.py"
CURRENT = ROOT / "contracts/plugin-topology/v1/active-topology.json"
TOPOLOGY = ROOT / "contracts/plugin-topology/v2/migration-topology.json"
MATRIX = ROOT / "contracts/plugin-topology/v2/compatibility-matrix.json"


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def validator_module():
    spec = importlib.util.spec_from_file_location("active_topology_validator", VALIDATOR)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def current_sha256() -> str:
    return hashlib.sha256(CURRENT.read_bytes()).hexdigest()


def row(matrix: dict, surface_id: str) -> dict:
    return next(item for item in matrix["rows"] if item["surface_id"] == surface_id)


def test_legacy_functional_split_contract_is_the_immutable_rollback_authority() -> None:
    contract = load(CURRENT)
    assert contract["state"] == "active"
    assert contract["canonical_core"] == "android-framework-ops"
    assert contract["implementation_owners"]["android-source-access"] == (
        "android-framework-ops:internal"
    )
    assert contract["public_entries"]["android-source-access"] == {
        "wsl": "android-wsl-ops",
        "macos": "android-mac-ops",
    }
    assert [item["id"] for item in contract["plugins"]] == [
        "android-framework-ops",
        "android-wsl-ops",
        "android-mac-ops",
        "jinny-android-practices",
        "codex-workspace-care",
    ]


def test_active_topology_validator_passes_the_repository() -> None:
    result = subprocess.run(
        [sys.executable, str(VALIDATOR)],
        cwd=ROOT,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert "Active plugin topology validation passed" in result.stdout
    assert "Android Codex Suite target-only topology validation passed" in result.stdout


def test_active_contract_loader_rejects_ambiguous_json(tmp_path: Path) -> None:
    module = validator_module()
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"schema":"one","schema":"two"}\n', encoding="utf-8")
    with pytest.raises(module.TopologyError, match="duplicate JSON key"):
        module.load_json(duplicate)
    nonfinite = tmp_path / "nonfinite.json"
    nonfinite.write_text('{"value":NaN}\n', encoding="utf-8")
    with pytest.raises(module.TopologyError, match="non-finite JSON number"):
        module.load_json(nonfinite)


def test_target_is_the_only_materialized_state() -> None:
    topology = load(TOPOLOGY)
    assert topology["architecture_binding"] == {
        "baseline_id": "akbs-2-architecture-baseline-v1",
        "baseline_run": "v1-20260901",
        "architecture_sha256": (
            "828eb97f6dbce441423f0a8c471d6c984187c8c1c846b91a3bfcbb6267a9b54b"
        ),
        "plugin_source_baseline": "c0840685911ef7e19dba3893e014a257727c54b6",
    }
    assert topology["physical_policy"] == {
        "default_state": "target",
        "materialized_states": ["target"],
        "declaration_only_states": ["current", "migration"],
        "current_authority": "contracts/plugin-topology/v1/active-topology.json",
        "current_authority_sha256": current_sha256(),
        "undeclared_mixed_behavior": "reject",
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("architecture_binding", None),
        ("architecture_sha256", "0" * 64),
        ("plugin_source_baseline", "0" * 40),
    ],
)
def test_rejects_missing_or_rebound_architecture_baseline(
    field: str, value: str | None,
) -> None:
    module = validator_module()
    current = load(CURRENT)
    topology = load(TOPOLOGY)
    matrix = load(MATRIX)
    if field == "architecture_binding":
        topology.pop(field)
    else:
        topology["architecture_binding"][field] = value
    with pytest.raises(module.TopologyError, match="architecture binding"):
        module.validate_contract_documents(
            current, topology, matrix, current_sha256=current_sha256()
        )


def test_target_fixture_matches_reviewed_three_plugin_baseline() -> None:
    topology = load(TOPOLOGY)
    target = next(item for item in topology["states"] if item["id"] == "target")
    plugins = {item["id"]: item for item in target["plugins"]}
    assert plugins["akbs-member-ops"]["skills"] == [
        "akbs-member-setup",
        "akbs-knowledge-search",
        "akbs-knowledge-merge-review",
        "akbs-daily-report",
        "akbs-weekly-report",
        "akbs-patch-submit",
    ]
    assert plugins["android-engineering-ops"]["skills"] == [
        "android-change-policy",
        "android-change-workflow",
        "android-source-access",
        "android-remote-channel",
        "android-remote-build-deploy",
        "android-patch-capture",
    ]
    assert plugins["jinny-android-practices"]["skills"] == [
        "jinny-android-coding-practices",
        "jinny-android-execution-policy",
    ]
    assert set(plugins) == {
        "akbs-member-ops",
        "android-engineering-ops",
        "jinny-android-practices",
    }


def test_migration_catalog_coexistence_is_not_install_coexistence() -> None:
    topology = load(TOPOLOGY)
    migration = next(item for item in topology["states"] if item["id"] == "migration")
    assert "android-framework-ops" in migration["catalog_plugins"]
    assert "android-engineering-ops" in migration["catalog_plugins"]
    assert migration["installation_families"]["legacy_rollback"]["coinstall_with_target"] is False
    assert migration["installation_families"]["legacy_rollback"]["source"] == (
        "frozen_rollback_release"
    )
    assert migration["installation_families"]["target_candidate"]["coinstall_with_legacy"] is False
    assert migration["installation_families"]["ordered_cutover"] == {
        "forward": [
            "refresh_marketplace", "remove_legacy_family", "add_target_family",
            "verify_target_only",
        ],
        "rollback": [
            "remove_target_family", "add_exact_legacy_release", "verify_legacy_only",
        ],
        "mixed_state": "invalid_no_business_actions",
    }


def test_compatibility_rows_cover_every_declared_surface_and_behavior() -> None:
    topology = load(TOPOLOGY)
    matrix = load(MATRIX)
    coverage = {
        surface
        for surfaces in topology["compatibility_coverage"].values()
        for surface in surfaces
    }
    assert {item["surface_id"] for item in matrix["rows"]} == coverage
    for item in matrix["rows"]:
        assert set(matrix["required_behavior_keys"]).issubset(item)
        assert set(item["read"]) == {"current", "migration", "target"}
        assert set(item["write"]) == {"current", "migration", "target"}
        assert set(item["default"]) == {"current", "migration", "target"}
        assert item["test"]["required_ids"]
        assert item["test"]["negative_ids"]


def test_android_change_v1_uses_the_common_patch_lifecycle() -> None:
    module = validator_module()
    current = load(CURRENT)
    topology = load(TOPOLOGY)
    matrix = load(MATRIX)
    android_v1 = next(
        item for item in matrix["rows"] if item["surface_id"] == "package.android-change-v1"
    )
    android_v1["write"]["target"] = "independent control plane"
    with pytest.raises(module.TopologyError, match="common patch lifecycle"):
        module.validate_contract_documents(
            current, topology, matrix, current_sha256=current_sha256()
        )


def test_rejects_missing_or_extra_compatibility_row() -> None:
    module = validator_module()
    current = load(CURRENT)
    topology = load(TOPOLOGY)
    matrix = load(MATRIX)
    missing = copy.deepcopy(matrix)
    missing["rows"].pop()
    with pytest.raises(module.TopologyError, match="exactly cover"):
        module.validate_contract_documents(
            current, topology, missing, current_sha256=current_sha256()
        )
    extra = copy.deepcopy(matrix)
    duplicate = copy.deepcopy(extra["rows"][0])
    duplicate["surface_id"] = "plugin.undeclared-extra"
    extra["rows"].append(duplicate)
    with pytest.raises(module.TopologyError, match="exactly cover"):
        module.validate_contract_documents(
            current, topology, extra, current_sha256=current_sha256()
        )


def test_rejects_undeclared_physical_target_plugin() -> None:
    module = validator_module()
    topology = load(TOPOLOGY)
    current_state = next(item for item in topology["states"] if item["id"] == "current")
    sources = set(current_state["source_plugins"])
    sources.add("android-engineering-ops")
    with pytest.raises(module.TopologyError, match="undeclared mixed"):
        module.validate_materialized_plugin_ids(
            sources, set(current_state["marketplace_plugins"]), topology
        )


def test_rejects_legacy_target_install_family_coexistence() -> None:
    module = validator_module()
    current = load(CURRENT)
    topology = load(TOPOLOGY)
    matrix = load(MATRIX)
    migration = next(item for item in topology["states"] if item["id"] == "migration")
    migration["installation_families"]["target_candidate"]["coinstall_with_legacy"] = True
    with pytest.raises(module.TopologyError, match="mutually exclusive"):
        module.validate_contract_documents(
            current, topology, matrix, current_sha256=current_sha256()
        )


def test_rejects_install_family_cutover_reordering() -> None:
    module = validator_module()
    current = load(CURRENT)
    topology = load(TOPOLOGY)
    matrix = load(MATRIX)
    migration = next(item for item in topology["states"] if item["id"] == "migration")
    forward = migration["installation_families"]["ordered_cutover"]["forward"]
    forward[1], forward[2] = forward[2], forward[1]
    with pytest.raises(module.TopologyError, match="ordered cutover"):
        module.validate_contract_documents(
            current, topology, matrix, current_sha256=current_sha256()
        )


def test_rejects_duplicate_default_owner_in_one_install_family() -> None:
    module = validator_module()
    current = load(CURRENT)
    topology = load(TOPOLOGY)
    matrix = load(MATRIX)
    first = matrix["rows"][0]
    first["default"]["target"]["target_only"].append("second-owner")
    with pytest.raises(module.TopologyError, match="duplicate default owners"):
        module.validate_contract_documents(
            current, topology, matrix, current_sha256=current_sha256()
        )


def test_rejects_unknown_matrix_family_owner_coinstall_and_empty_fallback() -> None:
    module = validator_module()
    current = load(CURRENT)
    topology = load(TOPOLOGY)
    base = load(MATRIX)
    ghost_family = copy.deepcopy(base)
    ghost_family["rows"][0]["default"]["current"]["ghost_family"] = []
    with pytest.raises(module.TopologyError, match="default families"):
        module.validate_contract_documents(
            current, topology, ghost_family, current_sha256=current_sha256()
        )
    ghost_owner = copy.deepcopy(base)
    ghost_owner["rows"][0]["default"]["target"]["target_only"] = ["android-engineering-ops"]
    with pytest.raises(module.TopologyError, match="default owner binding"):
        module.validate_contract_documents(
            current, topology, ghost_owner, current_sha256=current_sha256()
        )
    ghost_coinstall = copy.deepcopy(base)
    ghost_coinstall["rows"][0]["activation"]["forbidden_coinstall"] = ["ghost-plugin"]
    with pytest.raises(module.TopologyError, match="coinstall reference"):
        module.validate_contract_documents(
            current, topology, ghost_coinstall, current_sha256=current_sha256()
        )
    empty_fallback = copy.deepcopy(base)
    empty_fallback["rows"][0]["fallback"]["mode"] = ""
    with pytest.raises(module.TopologyError, match="fallback fields"):
        module.validate_contract_documents(
            current, topology, empty_fallback, current_sha256=current_sha256()
        )
    nonsense_removal = copy.deepcopy(base)
    nonsense_removal["rows"][0]["removal"]["gates"] = ["per_nonsense"]
    with pytest.raises(module.TopologyError, match="removal binding"):
        module.validate_contract_documents(
            current, topology, nonsense_removal, current_sha256=current_sha256()
        )


def test_source_access_preserves_state_and_rejects_mixed_or_wrong_host() -> None:
    source_access = row(load(MATRIX), "skill.android-source-access")
    assert set(source_access["activation"]["forbidden_coinstall"]) == {
        "android-framework-ops",
        "android-wsl-ops",
        "android-mac-ops",
    }
    assert set(source_access["test"]["negative_ids"]) >= {
        "wrong-host",
        "third-default-skill",
        "mixed-install",
    }
    state = row(load(MATRIX), "state.source-access")
    assert state["target"] == ["same_paths_and_identities"]
    assert state["removal"]["legacy_reader_retention"] == "permanent"


def test_v1_package_and_historical_inputs_remain_permanent_reads() -> None:
    matrix = load(MATRIX)
    legacy = row(matrix, "package.framework-change-v1")
    assert legacy["removal"]["legacy_reader_retention"] == "permanent"
    assert legacy["removal"]["history_rewrite"] is False
    for surface_id in (
        "artifact.akbs-member",
        "artifact.android-patch-capture",
        "artifact.android-remote-build-deploy",
    ):
        assert row(matrix, surface_id)["removal"]["legacy_reader_retention"] == "permanent"


def test_provider_is_decision_only_and_failure_semantics_are_explicit() -> None:
    provider = load(ROOT / "contracts/android-practices-provider/v1/provider.schema.json")
    authority = provider["properties"]["authority"]["properties"]
    assert authority["decision_only"] == {"const": True}
    assert all(
        authority[field] == {"const": False}
        for field in (
            "can_spawn",
            "can_write_source",
            "can_acquire_lock",
            "can_execute_side_effects",
            "can_upload",
            "can_accept_gate",
            "can_final_accept",
        )
    )
    fallback = provider["properties"]["fallback"]["properties"]
    assert fallback["capability_absent"] == {"const": "core"}
    assert fallback["applicability_miss"] == {"const": "core"}
    assert fallback["provider_missing_or_invalid"] == {"const": "fail_closed"}
    assert fallback["declared_capability_broken"] == {"const": "fail_closed"}


def test_core_execution_contract_has_no_model_or_controller_authority_fields() -> None:
    module = validator_module()
    decision = load(
        ROOT / "contracts/android-practices-provider/v1/execution-policy-decision.schema.json"
    )
    assert not module._property_names(decision) & {
        "model",
        "model_id",
        "spawn",
        "assignment",
        "workspace_path",
        "lock",
        "lease",
        "raw_command",
        "upload",
        "write_authorized",
        "gate_acceptance",
        "final_acceptance",
    }
    assignment = load(
        ROOT / "contracts/android-change-workflow/v1/worker-assignment.schema.json"
    )
    assert assignment["properties"]["permissions"]["properties"]["may_final_accept"] == {
        "const": False
    }
    result = load(ROOT / "contracts/android-change-workflow/v1/worker-result.schema.json")
    assert set(result["properties"]["outcome"]["enum"]) == {
        "completed",
        "partial",
        "blocked",
        "failed",
    }


def test_patch_v1_adds_only_simple_component_classification() -> None:
    package = load(ROOT / "contracts/incoming/v1/knowledge-incoming-package.schema.json")
    assert package["properties"]["schema"]["const"] == "knowledge-incoming-package"
    assert package["properties"]["schema_version"]["const"] == "1"
    assert package["properties"]["package_kind"]["enum"] == [
        "daily_trace", "weekly_trace", "android_change"
    ]
    component = package["properties"]["components"]["items"]
    assert set(component["required"]) == {"layer", "patches"}
    assert set(component["properties"]) == {"layer", "patches"}
    assert set(component["properties"]["layer"]["enum"]) == {
        "application",
        "platform",
        "native",
        "hal",
        "kernel",
        "device",
        "build",
    }
    assert "system_app" not in component["properties"]["layer"]["enum"]
    assert "files" in package["properties"]
    assert not (ROOT / "contracts/incoming/v2/component-evidence-profiles.json").exists()
    assert not (ROOT / "contracts/incoming/v2/client-adapter-outputs.schema.json").exists()


def test_migration_target_candidate_owns_legacy_skill_wrappers() -> None:
    topology = load(TOPOLOGY)
    migration = next(item for item in topology["states"] if item["id"] == "migration")
    plugins = {item["id"]: item for item in migration["canonical_plugins"]}
    assert set(plugins["akbs-member-ops"]["compatibility_skills"]) >= {
        "android-member-setup",
        "android-knowledge-search",
        "android-framework-patch-intake",
        "android-knowledge-intake",
    }
    assert set(plugins["android-engineering-ops"]["compatibility_skills"]) == {
        "android-framework-change-workflow",
        "android-framework-patch-capture",
    }
    assert plugins["jinny-android-practices"]["compatibility_skills"] == [
        "jinny-framework-coding-standards"
    ]


def test_member_config_compatibility_excludes_project_report_identity() -> None:
    topology = load(TOPOLOGY)
    identity = topology["engineering_identity_resolution"]["semantics"]
    member_config = row(load(MATRIX), "config.member-profile")
    assert set(member_config["legacy"]) >= {
        "$CODEX_HOME/android-knowledge-intake.toml",
        "$CODEX_HOME/android-knowledge-search.toml",
        "$CODEX_HOME/report/config.toml",
        "<project>/.codex/report.toml",
    }
    assert identity["akbs_profile"]["legacy_user_configs"] == [
        "$CODEX_HOME/android-knowledge-intake.toml",
        "$CODEX_HOME/android-knowledge-search.toml",
        "$CODEX_HOME/report/config.toml",
    ]
    assert identity["akbs_profile"]["repository_report_config"] == (
        "<project>/.codex/report.toml"
    )
    assert identity["akbs_profile"]["repository_report_identity_role"] == (
        "cannot_supply_or_override_member_alias"
    )
    assert identity["closed_config_shapes"] == {
        "user": {
            "path": "$CODEX_HOME/android-engineering-ops.toml",
            "allowed_tables": ["extension", "identity"],
            "additional_tables": "forbidden",
        },
        "project": {
            "path": "<project>/.codex/android-engineering.toml",
            "allowed_tables": ["extension"],
            "required_tables": ["extension"],
            "additional_tables": "forbidden",
        },
    }


def valid_provider() -> dict:
    return {
        "schema": "android-practices-provider-v1",
        "provider_id": "jinny-android-practices",
        "provider_version": "1.0.0",
        "compatible_core_contracts": ["android-engineering-ops-v1"],
        "capabilities": {
            "execution": {
                "contract": "android-execution-policy-provider-v1",
                "skill_id": "jinny-android-execution-policy",
                "skill_version": "1.0.0",
                "decision_schema": "execution-policy-decision-v1",
                "applicability": {"workflow_actions": [], "component_layers": []},
                "worker_profiles": {
                    "analysis-reader": {
                        "dispatch": {"model_id": "provider-owned-model", "reasoning_effort": "high"},
                        "task_classes": ["analysis", "diagnosis"],
                        "effect_ceiling": "read_only",
                    },
                    "implementation-worker": {
                        "dispatch": {"model_id": "provider-owned-model", "reasoning_effort": "high"},
                        "task_classes": ["implementation"],
                        "effect_ceiling": "workspace_mutation",
                    },
                },
            }
        },
        "fallback": {
            "capability_absent": "core",
            "applicability_miss": "core",
            "provider_missing_or_invalid": "fail_closed",
            "declared_capability_broken": "fail_closed",
            "invalid_decision": "fail_closed",
        },
        "authority": {
            "decision_only": True,
            "can_spawn": False,
            "can_write_source": False,
            "can_acquire_lock": False,
            "can_execute_side_effects": False,
            "can_upload": False,
            "can_accept_gate": False,
            "can_final_accept": False,
        },
    }


def valid_execution_decision(profile: str = "analysis-reader", effect: str = "read_only") -> dict:
    return {
        "schema": "execution-policy-decision-v1",
        "decision_id": "decision-1",
        "run_id": "run-1",
        "stage_id": "stage-1",
        "context_sha256": "b" * 64,
        "provider": {
            "provider_id": "jinny-android-practices",
            "provider_version": "1.0.0",
            "provider_manifest_sha256": "a" * 64,
            "skill_id": "jinny-android-execution-policy",
            "skill_version": "1.0.0",
        },
        "outcome": {
            "type": "delegate",
            "worker_profile_id": profile,
            "task_class": "analysis" if profile == "analysis-reader" else "implementation",
            "requested_effect": effect,
            "reason_codes": ["bounded-task"],
            "independent_review_requested": False,
        },
        "created_at": "2026-09-01T00:00:00Z",
    }


def test_provider_decision_checks_profile_task_effect_and_rollout() -> None:
    module = validator_module()
    provider = valid_provider()
    module.validate_provider_execution_decision(
        provider, "a" * 64, valid_execution_decision(), rollout_effect_ceiling="read_only"
    )
    unknown = valid_execution_decision(profile="missing")
    with pytest.raises(module.TopologyError, match="unknown worker profile"):
        module.validate_provider_execution_decision(
            provider, "a" * 64, unknown, rollout_effect_ceiling="read_only"
        )
    mutation = valid_execution_decision(
        profile="implementation-worker", effect="workspace_mutation"
    )
    with pytest.raises(module.TopologyError, match="rollout ceiling"):
        module.validate_provider_execution_decision(
            provider, "a" * 64, mutation, rollout_effect_ceiling="read_only"
        )
    forged = valid_execution_decision()
    forged["outcome"]["upload"] = True
    with pytest.raises(module.TopologyError, match="controller authority"):
        module.validate_provider_execution_decision(
            provider, "a" * 64, forged, rollout_effect_ceiling="read_only"
        )
    numeric_false = valid_provider()
    numeric_false["authority"]["can_spawn"] = 0
    with pytest.raises(module.TopologyError, match="over-authorized"):
        module.validate_provider_execution_decision(
            numeric_false,
            "a" * 64,
            valid_execution_decision(),
            rollout_effect_ceiling="read_only",
        )
    authority_list = valid_provider()
    authority_list["authority"] = list(authority_list["authority"])
    with pytest.raises(module.TopologyError, match="over-authorized"):
        module.validate_provider_execution_decision(
            authority_list,
            "a" * 64,
            valid_execution_decision(),
            rollout_effect_ceiling="read_only",
        )


def valid_assignment(effect: str = "read_only") -> dict:
    assignment = {
        "schema": "worker-assignment-v1",
        "assignment_id": "assignment-1",
        "run_id": "run-1",
        "attempt": 1,
        "issued_at": "2026-09-01T00:00:00Z",
        "source_snapshot_sha256": "c" * 64,
        "controller": "android-change-workflow",
        "assignee": {"profile_source": "core", "worker_profile_id": "analysis-reader"},
        "effect": effect,
        "objective": "Inspect one file",
        "input_bindings": {"requirement_sha256": "d" * 64},
        "scope": {
            "repositories": ["plugin"],
            "paths": [{"repository_id": "plugin", "kind": "file", "path": "README.md"}],
        },
        "constraints": {
            "max_automatic_escalations": 1,
            "environment_failure_escalates_model": False,
        },
        "permissions": {
            "may_acquire_authority": False,
            "may_expand_scope": False,
            "may_upload": False,
            "may_accept_gate": False,
            "may_final_accept": False,
        },
        "required_evidence": ["readback"],
        "result_schema": "worker-result-v1",
    }
    if effect == "workspace_mutation":
        assignment["assignee"]["worker_profile_id"] = "implementation-worker"
        assignment["constraints"].update(
            {
                "authority_ref": "lease-1",
                "workspace_bindings": [
                    {
                        "repository_id": "plugin",
                        "workspace_id": "workspace-1",
                        "base_revision": "1" * 40,
                        "target_expected_head": "1" * 40,
                        "paths": [{"repository_id": "plugin", "kind": "file", "path": "README.md"}],
                        "authority_ref": "lease-1",
                    }
                ],
            }
        )
    return assignment


def test_assignment_semantics_require_nonempty_machine_boundaries() -> None:
    module = validator_module()
    module.validate_assignment_semantics(valid_assignment())
    module.validate_assignment_semantics(valid_assignment("workspace_mutation"))
    integral_float = valid_assignment()
    integral_float["constraints"]["max_automatic_escalations"] = 1.0
    integral_float["attempt"] = 1.0
    module.validate_assignment_semantics(integral_float)
    huge_integer = valid_assignment()
    huge_integer["attempt"] = 10**1000
    module.validate_assignment_semantics(huge_integer)
    for invalid_attempt in (True, 0, -1, 1.5):
        invalid = valid_assignment()
        invalid["attempt"] = invalid_attempt
        with pytest.raises(module.TopologyError, match="attempt"):
            module.validate_assignment_semantics(invalid)
    empty = valid_assignment("workspace_mutation")
    empty["scope"]["paths"] = []
    with pytest.raises(module.TopologyError, match="scope must be non-empty"):
        module.validate_assignment_semantics(empty)
    drift = valid_assignment("workspace_mutation")
    drift["constraints"]["workspace_bindings"][0]["paths"][0]["path"] = "OTHER.md"
    with pytest.raises(module.TopologyError, match="path scope differs"):
        module.validate_assignment_semantics(drift)
    authority_drift = valid_assignment("workspace_mutation")
    authority_drift["constraints"]["workspace_bindings"][0]["authority_ref"] = "lease-2"
    with pytest.raises(module.TopologyError, match="authority or path scope"):
        module.validate_assignment_semantics(authority_drift)
    duplicate_repository = valid_assignment("workspace_mutation")
    duplicate_repository["constraints"]["workspace_bindings"].append(
        copy.deepcopy(duplicate_repository["constraints"]["workspace_bindings"][0])
    )
    with pytest.raises(module.TopologyError, match="repositories differ"):
        module.validate_assignment_semantics(duplicate_repository)
    ghost_path = valid_assignment("workspace_mutation")
    ghost_path["scope"]["paths"].append(
        {"repository_id": "ghost", "kind": "file", "path": "outside.txt"}
    )
    with pytest.raises(module.TopologyError, match="path repositories differ"):
        module.validate_assignment_semantics(ghost_path)
    numeric_false = valid_assignment()
    numeric_false["permissions"]["may_acquire_authority"] = 0
    with pytest.raises(module.TopologyError, match="permissions"):
        module.validate_assignment_semantics(numeric_false)
    boolean_one = valid_assignment()
    boolean_one["constraints"]["max_automatic_escalations"] = True
    with pytest.raises(module.TopologyError, match="escalation contract"):
        module.validate_assignment_semantics(boolean_one)
    permissions_list = valid_assignment()
    permissions_list["permissions"] = list(permissions_list["permissions"])
    with pytest.raises(module.TopologyError, match="permissions"):
        module.validate_assignment_semantics(permissions_list)


def valid_snapshot() -> dict:
    return {
        "schema": "stage-snapshot-v1",
        "snapshot_id": "snapshot-1",
        "run_id": "run-1",
        "sequence": 1,
        "created_at": "2026-09-01T00:00:00Z",
        "snapshot_reason": "delegating_worker",
        "controller": {"id": "android-change-workflow", "authority": "stage_gate_and_requirement_acceptance"},
        "stage": {"stage_id": "stage-1", "gate_index": 1, "state": "active", "risk_level": "low"},
        "input_bindings": {
            "requirement_sha256": "1" * 64,
            "core_policy_sha256": "2" * 64,
            "extension_config_sha256": "3" * 64,
        },
        "provider_resolution": {
            "selection_mode": "none",
            "coding": {"source": "core", "reason": "mode_none"},
            "execution": {"source": "core", "reason": "mode_none"},
        },
        "workspace_bindings": [],
        "event": {"type": "assignment_planned", "planned_assignment_id": "assignment-1"},
    }


def test_stage_snapshot_semantics_reject_hash_and_provider_contradictions() -> None:
    module = validator_module()
    module.validate_stage_snapshot_semantics(valid_snapshot())
    integral_float = valid_snapshot()
    integral_float["sequence"] = 1.0
    module.validate_stage_snapshot_semantics(integral_float)
    missing = valid_snapshot()
    missing["event"].pop("planned_assignment_id")
    with pytest.raises(module.TopologyError, match="planned assignment"):
        module.validate_stage_snapshot_semantics(missing)
    chain = valid_snapshot()
    chain["sequence"] = 2
    with pytest.raises(module.TopologyError, match="hash chain"):
        module.validate_stage_snapshot_semantics(chain)
    contradiction = valid_snapshot()
    contradiction["provider_resolution"]["provider_id"] = "jinny-android-practices"
    with pytest.raises(module.TopologyError, match="mode none"):
        module.validate_stage_snapshot_semantics(contradiction)
    wrong_jinny = valid_snapshot()
    wrong_jinny["provider_resolution"] = {
        "selection_mode": "jinny",
        "provider_id": "someone-android-practices",
        "provider_version": "1.0.0",
        "provider_manifest_sha256": "4" * 64,
        "coding": {"source": "core", "reason": "capability_absent"},
        "execution": {"source": "provider", "reason": "provider_capability"},
    }
    with pytest.raises(module.TopologyError, match="Jinny provider"):
        module.validate_stage_snapshot_semantics(wrong_jinny)
    high_risk = valid_snapshot()
    high_risk["snapshot_reason"] = "entering_high_risk_mutation"
    high_risk["event"] = {"type": "mutation_authority_bound"}
    with pytest.raises(module.TopologyError, match="authority bindings"):
        module.validate_stage_snapshot_semantics(high_risk)
    accepted_active = valid_snapshot()
    accepted_active["snapshot_reason"] = "gate_transition"
    accepted_active["event"] = {"type": "gate_changed"}
    accepted_active["requirement_disposition"] = "accepted"
    with pytest.raises(module.TopologyError, match="stage state differ"):
        module.validate_stage_snapshot_semantics(accepted_active)
    boolean_sequence = valid_snapshot()
    boolean_sequence["sequence"] = True
    with pytest.raises(module.TopologyError, match="hash chain"):
        module.validate_stage_snapshot_semantics(boolean_sequence)


def valid_result() -> dict:
    return {
        "schema": "worker-result-v1",
        "result_id": "result-1",
        "run_id": "run-1",
        "assignment_id": "assignment-1",
        "assignment_sha256": "e" * 64,
        "attempt": 1,
        "worker_binding": {"worker_task_id": "worker-1", "worker_profile_id": "analysis-reader"},
        "started_at": "2026-09-01T00:00:00Z",
        "completed_at": "2026-09-01T00:01:00Z",
        "outcome": "completed",
        "observed_workspaces": [],
        "reported_changes": [],
        "commands": [],
        "checks": [{"check_id": "readback", "status": "passed", "receipt_sha256": "f" * 64}],
        "evidence": [{"evidence_id": "evidence-1", "kind": "readback", "uri": "artifact:1", "sha256": "0" * 64, "size_bytes": 1}],
        "reported_scope_deviations": [],
        "summary": "Read-only assignment completed",
    }


def test_worker_result_semantics_bind_assignment_and_receipts() -> None:
    module = validator_module()
    assignment = valid_assignment()
    module.validate_worker_result_semantics(
        valid_result(), assignment, assignment_sha256="e" * 64
    )
    integral_float = valid_result()
    integral_float["attempt"] = 1.0
    module.validate_worker_result_semantics(
        integral_float, assignment, assignment_sha256="e" * 64
    )
    replay = valid_result()
    replay["assignment_sha256"] = "0" * 64
    with pytest.raises(module.TopologyError, match="exact assignment"):
        module.validate_worker_result_semantics(
            replay, assignment, assignment_sha256="e" * 64
        )
    impossible = valid_result()
    impossible["checks"][0]["receipt_sha256"] = None
    with pytest.raises(module.TopologyError, match="receipt semantics"):
        module.validate_worker_result_semantics(
            impossible, assignment, assignment_sha256="e" * 64
        )
    boolean_attempt = valid_result()
    boolean_attempt["attempt"] = True
    with pytest.raises(module.TopologyError, match="exact assignment"):
        module.validate_worker_result_semantics(
            boolean_attempt, assignment, assignment_sha256="e" * 64
        )
    for invalid_attempt in (0, -1, 1.5):
        invalid = valid_result()
        invalid["attempt"] = invalid_attempt
        with pytest.raises(module.TopologyError, match="exact assignment"):
            module.validate_worker_result_semantics(
                invalid, assignment, assignment_sha256="e" * 64
            )
    invalid_assignment_attempt = valid_assignment()
    invalid_assignment_attempt["attempt"] = 0
    with pytest.raises(module.TopologyError, match="exact assignment"):
        module.validate_worker_result_semantics(
            valid_result(), invalid_assignment_attempt, assignment_sha256="e" * 64
        )
    readonly_drift = valid_result()
    readonly_drift["reported_changes"] = [
        {
            "repository_id": "plugin",
            "operation": "modify",
            "path": "README.md",
            "before_sha256": "1" * 64,
            "after_sha256": "2" * 64,
        }
    ]
    with pytest.raises(module.TopologyError, match="read-only"):
        module.validate_worker_result_semantics(
            readonly_drift, assignment, assignment_sha256="e" * 64
        )
    mutation_assignment = valid_assignment("workspace_mutation")
    mutation = valid_result()
    mutation["worker_binding"]["worker_profile_id"] = "implementation-worker"
    mutation["observed_workspaces"] = [
        {
            "repository_id": "plugin",
            "workspace_id": "workspace-1",
            "base_revision": "1" * 40,
            "start_head": "1" * 40,
            "end_head": "2" * 40,
        }
    ]
    mutation["reported_changes"] = [
        {
            "repository_id": "plugin",
            "operation": "modify",
            "path": "README.md",
            "before_sha256": "1" * 64,
            "after_sha256": "2" * 64,
        }
    ]
    module.validate_worker_result_semantics(
        mutation, mutation_assignment, assignment_sha256="e" * 64
    )
    escaped = copy.deepcopy(mutation)
    escaped["reported_changes"][0]["path"] = "OTHER.md"
    with pytest.raises(module.TopologyError, match="escapes assignment scope"):
        module.validate_worker_result_semantics(
            escaped, mutation_assignment, assignment_sha256="e" * 64
        )



def valid_patch_package(*, layers: tuple[str, ...] = ("application",)) -> tuple[dict, dict]:
    components = []
    patch_paths = []
    evidence_paths = []
    inventory: dict[str, tuple[str, int]] = {
        "README.md": ("1" * 64, 10),
        "materials/case.json": ("4" * 64, 40),
        "materials/variant.json": ("5" * 64, 50),
        "materials/display/patch_view.json": ("6" * 64, 60),
    }
    for index, layer in enumerate(layers, start=1):
        patch_path = f"patches/change-{index}.patch"
        evidence_path = f"materials/evidence/result-{index}.json"
        components.append({
            "layer": layer,
            "patches": [patch_path],
        })
        patch_paths.append(patch_path)
        evidence_paths.append(evidence_path)
        inventory[patch_path] = ("2" * 64, 20)
        inventory[evidence_path] = ("3" * 64, 30)
    package = {
        "schema": "knowledge-incoming-package",
        "schema_version": "1",
        "package_kind": "android_change",
        "member_alias": "member1",
        "member_name": "成员一",
        "date": "2026-09-10",
        "run_id": "20260910-120000-test",
        "tool": "akbs-patch-submit",
        "summary": "One coherent Android change",
        "case_id": "case-test",
        "variant_id": "variant-test",
        "package_status": "validated",
        "project": "TVE8402M",
        "platform": "rk",
        "android_version": "14",
        "workflow_contract": "current_codex_skill",
        "files": {
            "case": "materials/case.json",
            "variant": "materials/variant.json",
            "readme": "README.md",
            "patches": patch_paths,
            "display": ["materials/display/patch_view.json"],
            "evidence": evidence_paths,
        },
        "components": components,
    }
    return package, inventory


def manifest_bytes(package: dict) -> bytes:
    return (json.dumps(package, ensure_ascii=False, sort_keys=True) + "\n").encode()


def test_minimal_android_change_semantics_cover_all_seven_layers() -> None:
    module = validator_module()
    layers = ("application", "platform", "native", "hal", "kernel", "device", "build")
    package, inventory = valid_patch_package(layers=layers)
    result = module.validate_android_change_package_semantics(
        manifest_bytes(package), inventory
    )
    assert result["component_layers"] == sorted(layers)
    assert result["reference_integrity_valid"] is True
    assert result["archive_inventory_binding_valid"] is True
    assert result["source_package_key"] == "20260910/member1/20260910-120000-test"


def test_minimal_android_change_semantics_rejects_historical_kind_for_new_input() -> None:
    module = validator_module()
    package, inventory = valid_patch_package()
    package["package_kind"] = "framework_change"
    with pytest.raises(module.TopologyError, match="package identity"):
        module.validate_android_change_package_semantics(
            manifest_bytes(package), inventory
        )


@pytest.mark.parametrize(
    "mutation, message",
    [
        ("missing_component_patch", "classify every patch exactly once"),
        ("duplicate_classification", "classify every patch exactly once"),
        ("extra_file", "inventory"),
    ],
)
def test_minimal_android_change_semantics_fail_closed(
    mutation: str, message: str
) -> None:
    module = validator_module()
    package, inventory = valid_patch_package(layers=("application", "platform"))
    inventory = dict(inventory)
    if mutation == "missing_component_patch":
        package["components"].pop()
    elif mutation == "duplicate_classification":
        package["components"][1]["patches"] = package["components"][0]["patches"]
    else:
        inventory["unexpected.txt"] = ("4" * 64, 1)
    with pytest.raises(module.TopologyError, match=message):
        module.validate_android_change_package_semantics(
            manifest_bytes(package), inventory
        )


def test_minimal_android_change_semantics_reject_retired_control_plane_fields() -> None:
    module = validator_module()
    package, inventory = valid_patch_package()
    package["qualification"] = {}
    with pytest.raises(module.TopologyError, match="retired package-v2"):
        module.validate_android_change_package_semantics(
            manifest_bytes(package), inventory
        )
