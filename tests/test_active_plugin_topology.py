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


def test_android_v2_writer_contract_stays_exactly_gated() -> None:
    module = validator_module()
    current = load(CURRENT)
    topology = load(TOPOLOGY)
    matrix = load(MATRIX)
    android_v2 = next(
        item for item in matrix["rows"] if item["surface_id"] == "package.android-change-v2"
    )
    android_v2["write"]["current"] = "enabled"
    with pytest.raises(module.TopologyError, match="writer is not default-off"):
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


def test_patch_v2_uses_orthogonal_components_and_keeps_v1_read_only() -> None:
    package = load(ROOT / "contracts/incoming/v2/akbs-android-change-package.schema.json")
    assert package["properties"]["schema"]["const"] == "akbs-android-change-package-v2"
    assert package["properties"]["package_kind"]["const"] == "android_change"
    component = package["$defs"]["component"]
    assert set(component["required"]) == {"id", "layer", "type", "partition", "ownership"}
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
    profiles = load(ROOT / "contracts/incoming/v2/component-evidence-profiles.json")
    assert profiles["evaluation_scope"] == "per_component"
    assert profiles["all_components_must_qualify"] is True
    assert profiles["client_output_source"] == (
        "client_contract_adapter_output_file_untrusted_until_server_recalculation"
    )
    assert profiles["server_qualification_boundary"]["server_must_recalculate"] is True
    assert profiles["server_qualification_boundary"]["member_archive_rewrite"] is False
    assert profiles["writer_activation"]["phase1_state"] == "blocked"
    qualification = package["$defs"]["qualification"]
    assert set(qualification["required"]) == {
        "profile_id",
        "profile_artifact_sha256",
        "client_adapter_outputs_file_id",
        "component_evidence_bindings",
    }
    client_outputs = load(ROOT / "contracts/incoming/v2/client-adapter-outputs.schema.json")
    assert client_outputs["properties"]["authority"]["const"] == "untrusted_client_input"
    assert profiles["legacy_v1"]["projection"] == {
        "layer": "platform",
        "type": "framework",
        "partition": None,
        "ownership": None,
    }
    assert profiles["legacy_v1"]["normalized_read_projection"]["component_fields"]["partition"]["nullable"] is True
    assert profiles["legacy_v1"]["normalized_read_projection"]["component_fields"]["ownership"]["nullable"] is True
    assert profiles["legacy_v1"]["normalized_read_projection"]["write_back"] is False


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


def valid_patch_package() -> dict:
    return {
        "schema": "akbs-android-change-package-v2",
        "schema_version": "2",
        "package_kind": "android_change",
        "package_status": "validated",
        "identity": {"member_alias": "member1", "run_id": "20260901-000000-test", "created_at": "2026-09-01T00:00:00Z"},
        "subject": {
            "title": "System app change",
            "summary": "Change one application component",
            "primary_component_id": "component-1",
            "target": {"project": "generic", "platform": "generic", "android_version": "15"},
        },
        "workflow": {
            "contract": "current_codex_skill",
            "implementation_origins": ["codex"],
            "capture_tool": {"id": "android-patch-capture", "version": "1.0.0"},
        },
        "components": [
            {"id": "component-1", "layer": "application", "type": "system_app", "partition": "system_ext", "ownership": "aosp"}
        ],
        "sources": [
            {"id": "source-1", "kind": "git", "repo_path": ".", "base_revision": "1" * 40, "head_revision": "2" * 40}
        ],
        "files": [
            {"id": "patch-1", "role": "patch", "path": "patches/change.patch", "sha256": "3" * 64, "size_bytes": 10},
            {"id": "evidence-1-file", "role": "evidence", "path": "evidence/result.json", "sha256": "4" * 64, "size_bytes": 20},
            {"id": "qualification-client-output", "role": "metadata", "path": "metadata/client-adapter-outputs.json", "sha256": "0" * 64, "size_bytes": 1, "media_type": "application/json"},
        ],
        "changes": [
            {"id": "change-1", "component_ids": ["component-1"], "source_id": "source-1", "file_id": "patch-1", "format": "git_diff"}
        ],
        "evidence": [
            {"id": "evidence-1", "kind": "feature_acceptance", "component_ids": ["component-1"], "file_id": "evidence-1-file", "scope": "component", "result": "PASS", "contract": {"id": "feature-check", "version": "1"}, "declared_claims": ["source_integrity", "change_diff_facts", "risk_surface", "android_change_policy", "feature_acceptance", "regression", "rollback", "pre_change_search", "application_build", "application_runtime_or_integration", "permission_and_signing"]}
        ],
        "qualification": {
            "profile_id": "akbs-component-evidence-profiles-v1",
            "profile_artifact_sha256": "0" * 64,
            "client_adapter_outputs_file_id": "qualification-client-output",
            "component_evidence_bindings": [
                {"component_id": "component-1", "evidence_ids": ["evidence-1"]}
            ],
        },
    }


def client_adapter_outputs_for(
    module, package: dict, profiles: dict, profile_artifact_sha256: str,
) -> dict:
    files = {item["id"]: item for item in package["files"]}
    evidence = {item["id"]: item for item in package["evidence"]}
    bindings = {
        item["component_id"]: item["evidence_ids"]
        for item in package["qualification"]["component_evidence_bindings"]
    }
    registry = profiles["evidence_group_registry"]["groups"]
    components = []
    for component in package["components"]:
        component_id = component["id"]
        source_evidence_id = bindings[component_id][0]
        source_file = files[evidence[source_evidence_id]["file_id"]]
        groups = module.required_evidence_groups(
            component, package["workflow"]["contract"], profiles
        )
        outputs = [
            {
                "schema": "akbs-client-adapter-output-v1",
                "component_id": component_id,
                "group_id": group_id,
                "source_evidence_id": source_evidence_id,
                "source_evidence_sha256": source_file["sha256"],
                "adapter_contract": registry[group_id]["adapter_contract"],
                "adapter_version": registry[group_id]["adapter_version"],
                "claim": registry[group_id]["claim"],
                "adapter_result": next(
                    item for item in registry[group_id]["allowed_adapter_results"]
                    if item != "NOT_APPLICABLE"
                ),
            }
            for group_id in sorted(groups)
        ]
        components.append({"component_id": component_id, "outputs": outputs})
    return {
        "schema": "akbs-client-adapter-outputs-v1",
        "authority": "untrusted_client_input",
        "source_package_key": module.source_package_key(package),
        "qualification_input_sha256": module.qualification_input_sha256(package),
        "profile_id": profiles["schema"],
        "profile_artifact_sha256": profile_artifact_sha256,
        "declared_package_status": package["package_status"],
        "components": components,
    }


def json_artifact_bytes(document: dict) -> bytes:
    return (
        json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def bind_client_adapter_outputs(package: dict, document: dict) -> bytes:
    raw = json_artifact_bytes(document)
    digest = hashlib.sha256(raw).hexdigest()
    file_id = package["qualification"]["client_adapter_outputs_file_id"]
    file_row = next(item for item in package["files"] if item["id"] == file_id)
    file_row["sha256"] = digest
    file_row["size_bytes"] = len(raw)
    return raw


def validate_client_contract(
    module,
    package: dict,
    profile_artifact_bytes: bytes,
    document: dict,
    *,
    archive_entries: list[tuple[str, str, int]] | None = None,
) -> dict:
    client_bytes = bind_client_adapter_outputs(package, document)
    manifest_bytes = json_artifact_bytes(package)
    selected_entries = archive_entries if archive_entries is not None else [
        ("manifest.json", hashlib.sha256(manifest_bytes).hexdigest(), len(manifest_bytes)),
        *[
            (item["path"], item["sha256"], item["size_bytes"])
            for item in package["files"]
        ],
    ]
    return module.validate_client_patch_package_semantics(
        manifest_bytes,
        profile_artifact_bytes,
        client_bytes,
        archive_entries=selected_entries,
    )


def test_qualification_input_hash_is_non_circular_and_package_bound() -> None:
    module = validator_module()
    assert module.canonical_json_sha256_v1(
        {"z": 1, "标题": "系统🔧", "a": [True, None, "/"]}
    ) == "61a4ce657524f70304ab18050766ad390d17d6bdac95eb61bbc73fbef830d007"
    with pytest.raises(module.TopologyError, match="floating-point"):
        module.canonical_json_sha256_v1({"value": 1.0})
    package = valid_patch_package()
    original = module.qualification_input_sha256(package)
    output_file_id = package["qualification"]["client_adapter_outputs_file_id"]
    output_file = next(item for item in package["files"] if item["id"] == output_file_id)
    output_file["sha256"] = "f" * 64
    output_file["size_bytes"] = 999
    assert module.qualification_input_sha256(package) == original

    changed_source = copy.deepcopy(package)
    changed_source["files"][0]["sha256"] = "e" * 64
    assert module.qualification_input_sha256(changed_source) != original
    changed_subject = copy.deepcopy(package)
    changed_subject["subject"]["title"] = "Another package"
    assert module.qualification_input_sha256(changed_subject) != original
    assert module.source_package_key(package) == (
        "20260901/member1/20260901-000000-test"
    )


def test_patch_semantics_resolve_refs_roles_inventory_and_repo_root() -> None:
    module = validator_module()
    package = valid_patch_package()
    profile_path = ROOT / "contracts/incoming/v2/component-evidence-profiles.json"
    profile_bytes = profile_path.read_bytes()
    profiles = load(profile_path)
    profile_sha256 = hashlib.sha256(profile_bytes).hexdigest()
    package["qualification"]["profile_artifact_sha256"] = profile_sha256
    client_outputs = client_adapter_outputs_for(
        module, package, profiles, profile_sha256
    )
    client_bytes = bind_client_adapter_outputs(package, client_outputs)
    manifest_bytes = json_artifact_bytes(package)
    archive_entries = [
        ("manifest.json", hashlib.sha256(manifest_bytes).hexdigest(), len(manifest_bytes)),
        *[
            (item["path"], item["sha256"], item["size_bytes"])
            for item in package["files"]
        ],
    ]
    result = module.validate_client_patch_package_semantics(
        manifest_bytes,
        profile_bytes,
        client_bytes,
        archive_entries=archive_entries,
    )
    assert result["authority"] == "untrusted_client_input"
    assert result["client_semantic_coherence_valid"] is True
    assert result["schema_validation_required"] is True
    assert result["archive_inventory_binding_valid"] is True
    assert result["archive_extractor_validation_required"] is True
    assert result["server_qualified"] is False
    duplicate = copy.deepcopy(package)
    duplicate["components"].append(copy.deepcopy(duplicate["components"][0]))
    with pytest.raises(module.TopologyError, match="IDs must be unique"):
        validate_client_contract(module, duplicate, profile_bytes, client_outputs)
    wrong_role = copy.deepcopy(package)
    wrong_role["files"][0]["role"] = "evidence"
    with pytest.raises(module.TopologyError, match="change references"):
        validate_client_contract(module, wrong_role, profile_bytes, client_outputs)
    bad_file_path = copy.deepcopy(package)
    bad_file_path["files"][0]["path"] = "."
    with pytest.raises(module.TopologyError, match="archive path"):
        validate_client_contract(module, bad_file_path, profile_bytes, client_outputs)
    for invalid_archive in (
        archive_entries[1:],
        [*archive_entries, ("extra.txt", "f" * 64, 1)],
        [*archive_entries, archive_entries[-1]],
    ):
        with pytest.raises(module.TopologyError, match="archive inventory"):
            module.validate_client_patch_package_semantics(
                manifest_bytes,
                profile_bytes,
                client_bytes,
                archive_entries=invalid_archive,
            )
    wrong_evidence_archive = list(archive_entries)
    evidence_index = next(
        index
        for index, item in enumerate(wrong_evidence_archive)
        if item[0] == "evidence/result.json"
    )
    wrong_evidence_archive[evidence_index] = ("evidence/result.json", "f" * 64, 20)
    with pytest.raises(module.TopologyError, match="archive inventory"):
        module.validate_client_patch_package_semantics(
            manifest_bytes,
            profile_bytes,
            client_bytes,
            archive_entries=wrong_evidence_archive,
        )
    normalized_collision = copy.deepcopy(package)
    normalized_collision["files"][0]["path"] = "patches/./change.patch"
    with pytest.raises(module.TopologyError, match="not canonical"):
        validate_client_contract(
            module, normalized_collision, profile_bytes, client_outputs
        )
    for field, value in (("role", "evidence"), ("media_type", "text/plain")):
        wrong_output_file = copy.deepcopy(package)
        output_id = wrong_output_file["qualification"]["client_adapter_outputs_file_id"]
        output_row = next(item for item in wrong_output_file["files"] if item["id"] == output_id)
        output_row[field] = value
        with pytest.raises(module.TopologyError, match="file binding differs"):
            validate_client_contract(
                module, wrong_output_file, profile_bytes, client_outputs
            )
    for field, value in (("sha256", "f" * 64), ("size_bytes", 999999)):
        wrong_output_file = copy.deepcopy(package)
        wrong_client_bytes = bind_client_adapter_outputs(
            wrong_output_file, client_outputs
        )
        output_id = wrong_output_file["qualification"]["client_adapter_outputs_file_id"]
        output_row = next(item for item in wrong_output_file["files"] if item["id"] == output_id)
        output_row[field] = value
        with pytest.raises(module.TopologyError, match="file binding differs"):
            module.validate_client_patch_package_semantics(
                json_artifact_bytes(wrong_output_file),
                profile_bytes,
                wrong_client_bytes,
                archive_entries=archive_entries,
            )
    with pytest.raises(module.TopologyError, match="qualification profile differs"):
        module.validate_client_patch_package_semantics(
            manifest_bytes,
            profile_bytes + b" ",
            client_bytes,
            archive_entries=archive_entries,
        )
    with pytest.raises(module.TopologyError, match="file binding differs"):
        module.validate_client_patch_package_semantics(
            manifest_bytes,
            profile_bytes,
            client_bytes + b" ",
            archive_entries=archive_entries,
        )
    missing_output = copy.deepcopy(client_outputs)
    missing_output["components"][0]["outputs"] = [
        item
        for item in missing_output["components"][0]["outputs"]
        if item["group_id"] != "feature_acceptance"
    ]
    missing_package = copy.deepcopy(package)
    with pytest.raises(module.TopologyError, match="do not satisfy"):
        validate_client_contract(module, missing_package, profile_bytes, missing_output)
    duplicate_component = copy.deepcopy(client_outputs)
    duplicate_component["components"].append(
        copy.deepcopy(duplicate_component["components"][0])
    )
    duplicate_component_package = copy.deepcopy(package)
    with pytest.raises(module.TopologyError, match="component outputs differ"):
        validate_client_contract(
            module, duplicate_component_package, profile_bytes, duplicate_component
        )
    duplicate_group = copy.deepcopy(client_outputs)
    duplicate_group["components"][0]["outputs"].append(
        copy.deepcopy(duplicate_group["components"][0]["outputs"][0])
    )
    duplicate_group_package = copy.deepcopy(package)
    with pytest.raises(module.TopologyError, match="groups do not satisfy"):
        validate_client_contract(
            module, duplicate_group_package, profile_bytes, duplicate_group
        )
    bad_na = copy.deepcopy(client_outputs)
    permission = next(
        item for item in bad_na["components"][0]["outputs"]
        if item["group_id"] == "permission_and_signing"
    )
    permission["adapter_result"] = "NOT_APPLICABLE"
    permission["not_applicable_basis"] = None
    bad_na_package = copy.deepcopy(package)
    with pytest.raises(module.TopologyError, match="N/A output"):
        validate_client_contract(module, bad_na_package, profile_bytes, bad_na)

    valid_na = copy.deepcopy(client_outputs)
    permission = next(
        item
        for item in valid_na["components"][0]["outputs"]
        if item["group_id"] == "permission_and_signing"
    )
    permission["adapter_result"] = "NOT_APPLICABLE"
    permission["not_applicable_basis"] = {
        "basis": "fixture is not privileged",
        "limits": "does not prove runtime permission behavior",
    }
    valid_na_result = validate_client_contract(
        module, copy.deepcopy(package), profile_bytes, valid_na
    )
    assert valid_na_result["server_qualified"] is False

    mandatory_na = copy.deepcopy(client_outputs)
    feature = next(
        item
        for item in mandatory_na["components"][0]["outputs"]
        if item["group_id"] == "feature_acceptance"
    )
    feature["adapter_result"] = "NOT_APPLICABLE"
    feature["not_applicable_basis"] = {"basis": "invalid", "limits": "invalid"}
    with pytest.raises(module.TopologyError, match="adapter output differs"):
        validate_client_contract(
            module, copy.deepcopy(package), profile_bytes, mandatory_na
        )

    pass_with_basis = copy.deepcopy(client_outputs)
    pass_with_basis["components"][0]["outputs"][0]["not_applicable_basis"] = {
        "basis": "invalid on PASS",
        "limits": "invalid on PASS",
    }
    with pytest.raises(module.TopologyError, match="N/A output"):
        validate_client_contract(
            module, copy.deepcopy(package), profile_bytes, pass_with_basis
        )

    for field, value in (
        ("source_package_key", "20260901/member2/20260901-000000-test"),
        ("qualification_input_sha256", "f" * 64),
    ):
        replay = copy.deepcopy(client_outputs)
        replay[field] = value
        replay_package = copy.deepcopy(package)
        with pytest.raises(module.TopologyError, match="document differs"):
            validate_client_contract(module, replay_package, profile_bytes, replay)

    source_mismatch = copy.deepcopy(client_outputs)
    source_mismatch["components"][0]["outputs"][0]["source_evidence_sha256"] = "f" * 64
    mismatch_package = copy.deepcopy(package)
    with pytest.raises(module.TopologyError, match="adapter output differs"):
        validate_client_contract(
            module, mismatch_package, profile_bytes, source_mismatch
        )

    undeclared_package = copy.deepcopy(package)
    undeclared_package["evidence"][0]["declared_claims"].remove("source_integrity")
    undeclared_output = client_adapter_outputs_for(
        module, undeclared_package, profiles, profile_sha256
    )
    with pytest.raises(module.TopologyError, match="adapter output differs"):
        validate_client_contract(
            module, undeclared_package, profile_bytes, undeclared_output
        )

    contradictory_package = copy.deepcopy(package)
    contradictory_package["evidence"][0]["result"] = "FAIL"
    contradictory_output = client_adapter_outputs_for(
        module, contradictory_package, profiles, profile_sha256
    )
    contradictory_result = validate_client_contract(
        module, contradictory_package, profile_bytes, contradictory_output
    )
    assert contradictory_result["client_semantic_coherence_valid"] is True
    assert contradictory_result["server_qualified"] is False


def test_evidence_registry_exactly_covers_profiles_and_adapter_claims() -> None:
    module = validator_module()
    profiles = load(ROOT / "contracts/incoming/v2/component-evidence-profiles.json")
    module.validate_evidence_profile_registry(profiles)
    missing = copy.deepcopy(profiles)
    missing["evidence_group_registry"]["groups"].pop("feature_acceptance")
    with pytest.raises(module.TopologyError, match="exactly cover"):
        module.validate_evidence_profile_registry(missing)
    failed_acceptance = copy.deepcopy(profiles)
    failed_acceptance["evidence_group_registry"]["groups"]["feature_acceptance"][
        "allowed_adapter_results"
    ] = ["FAIL"]
    with pytest.raises(module.TopologyError, match="adapter result"):
        module.validate_evidence_profile_registry(failed_acceptance)
    informational_acceptance = copy.deepcopy(profiles)
    informational_acceptance["evidence_group_registry"]["groups"]["feature_acceptance"][
        "allowed_adapter_results"
    ] = ["INFO"]
    with pytest.raises(module.TopologyError, match="result binding"):
        module.validate_evidence_profile_registry(informational_acceptance)
    wrong_server_authority = copy.deepcopy(profiles)
    wrong_server_authority["server_qualification_boundary"]["server_decision_contract"][
        "authority"
    ] = "client"
    with pytest.raises(module.TopologyError, match="qualification boundary"):
        module.validate_evidence_profile_registry(wrong_server_authority)
    missing_server_bindings = copy.deepcopy(profiles)
    missing_server_bindings["server_qualification_boundary"]["server_decision_contract"][
        "required_bindings"
    ] = []
    with pytest.raises(module.TopologyError, match="qualification boundary"):
        module.validate_evidence_profile_registry(missing_server_bindings)
    incomplete_writer_gate = copy.deepcopy(profiles)
    incomplete_writer_gate["writer_activation"]["required_per_group"] = []
    with pytest.raises(module.TopologyError, match="qualification boundary"):
        module.validate_evidence_profile_registry(incomplete_writer_gate)
    disabled_server_gate = copy.deepcopy(profiles)
    disabled_server_gate["server_qualification_boundary"][
        "writer_activation_requires_complete_adapter_input_contracts"
    ] = False
    with pytest.raises(module.TopologyError, match="qualification boundary"):
        module.validate_evidence_profile_registry(disabled_server_gate)
    wrong_recalculation_scope = copy.deepcopy(profiles)
    wrong_recalculation_scope["server_qualification_boundary"][
        "deterministic_recalculation_scope"
    ] = "trust_client"
    with pytest.raises(module.TopologyError, match="qualification boundary"):
        module.validate_evidence_profile_registry(wrong_recalculation_scope)
    wrong_hash_algorithm = copy.deepcopy(profiles)
    wrong_hash_algorithm["client_adapter_outputs_document_contract"][
        "qualification_input_hash"
    ]["algorithm"] = "sha512"
    with pytest.raises(module.TopologyError, match="qualification boundary"):
        module.validate_evidence_profile_registry(wrong_hash_algorithm)
    wrong_archive_hash = copy.deepcopy(profiles)
    wrong_archive_hash["archive_integrity"]["directory_payload_hash"] = "tar_sha256"
    with pytest.raises(module.TopologyError, match="qualification boundary"):
        module.validate_evidence_profile_registry(wrong_archive_hash)
