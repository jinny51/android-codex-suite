from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
TOPOLOGY_PATH = ROOT / "contracts/plugin-topology/v2/migration-topology.json"
MATRIX_PATH = ROOT / "contracts/plugin-topology/v2/compatibility-matrix.json"
CURRENT_PATH = ROOT / "contracts/plugin-topology/v1/active-topology.json"
VALIDATOR_PATH = ROOT / "scripts/validate_active_plugin_topology.py"


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def validator_module():
    spec = importlib.util.spec_from_file_location("phase2_topology_validator", VALIDATOR_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MATRIX = load(MATRIX_PATH)
REQUIRED_CASES = [
    pytest.param(row["surface_id"], test_id, id=f"{row['surface_id']}:{test_id}")
    for row in MATRIX["rows"]
    for test_id in row["test"]["required_ids"]
]
NEGATIVE_CASES = [
    pytest.param(row["surface_id"], test_id, id=f"{row['surface_id']}:{test_id}")
    for row in MATRIX["rows"]
    for test_id in row["test"]["negative_ids"]
]


@pytest.mark.parametrize(("surface_id", "test_id"), REQUIRED_CASES)
def test_required_compatibility_id_inventory(surface_id: str, test_id: str) -> None:
    """Inventory every positive case without pretending that collection proves it."""
    row = next(item for item in MATRIX["rows"] if item["surface_id"] == surface_id)
    assert test_id in row["test"]["required_ids"]
    assert set(MATRIX["required_behavior_keys"]).issubset(row)


@pytest.mark.parametrize(("surface_id", "test_id"), NEGATIVE_CASES)
def test_negative_compatibility_id_inventory(surface_id: str, test_id: str) -> None:
    """Inventory every negative case; behavioral proof comes from its owning phase."""
    row = next(item for item in MATRIX["rows"] if item["surface_id"] == surface_id)
    assert test_id in row["test"]["negative_ids"]


def test_compatibility_inventory_explicitly_is_not_behavioral_proof() -> None:
    proof_map = load(ROOT / "contracts/plugin-topology/v2/compatibility-test-map.json")
    assert proof_map["semantics"] == {
        "purpose": "case_inventory_and_phase_planning_only",
        "behavioral_proof": False,
        "inventory_green_satisfies_phase_gate": False,
        "proof_source": "separate_executable_tests_and_hash_bound_phase_receipts",
    }
    assert set(proof_map["proof_requirements"]) == {
        "phase2", "phase3", "phase4", "phase5", "phase6",
    }


def test_engineering_identity_resolution_is_closed_and_hash_bound() -> None:
    module = validator_module()
    topology = load(TOPOLOGY_PATH)
    contract = topology["engineering_identity_resolution"]
    semantics = contract["semantics"]
    assert contract["schema"] == "android-engineering-identity-resolution-v1"
    assert contract["canonicalization"] == "akbs-canonical-json-sha256-v1"
    assert (
        module.canonical_json_sha256_v1(semantics)
        == contract["semantics_sha256"]
        == module.ENGINEERING_IDENTITY_RESOLUTION_SHA256
    )

    akbs = semantics["akbs_profile"]
    assert akbs["when_authoritative_config_exists"] == "sole_akbs_authority"
    assert akbs["invalid_or_unselected_authoritative_config"] == (
        "fail_closed_without_fallback"
    )
    assert akbs["legacy_read_condition"] == "authoritative_config_missing_only"
    assert akbs["legacy_alias_agreement"] == (
        "same_alias_compatible_conflict_fail_closed"
    )
    assert akbs["repository_report_identity_role"] == (
        "cannot_supply_or_override_member_alias"
    )

    standalone = semantics["standalone_identity"]
    assert standalone["config"] == "$CODEX_HOME/android-engineering-ops.toml"
    assert standalone["required_fields"] == ["member_alias"]
    assert standalone["project_identity_table"] == "forbidden"
    assert standalone["akbs_coexistence"] == "resolved_member_alias_must_match"
    assert semantics["profile_selection"] == {
        "selectors": [
            "--profile", "CODEX_REPORT_PROFILE", "CODEX_WORK_REPORT_PROFILE",
        ],
        "scope": "select_existing_akbs_profile_only",
        "standalone_identity_creation": "forbidden",
        "missing_selected_profile": "fail_closed",
    }


@pytest.mark.parametrize(
    "mutation",
    [
        "target-fallback",
        "legacy-read-while-target-present",
        "project-report-alias",
        "project-engineering-identity",
        "profile-invents-standalone",
        "akbs-standalone-conflict-allowed",
    ],
)
def test_topology_validator_rejects_identity_semantic_rewrite(mutation: str) -> None:
    module = validator_module()
    topology = load(TOPOLOGY_PATH)
    current = load(CURRENT_PATH)
    changed = copy.deepcopy(topology)
    contract = changed["engineering_identity_resolution"]
    semantics = contract["semantics"]
    if mutation == "target-fallback":
        semantics["akbs_profile"]["invalid_or_unselected_authoritative_config"] = (
            "fall_back_to_legacy"
        )
    elif mutation == "legacy-read-while-target-present":
        semantics["akbs_profile"]["legacy_read_condition"] = "always"
    elif mutation == "project-report-alias":
        semantics["akbs_profile"]["repository_report_identity_role"] = (
            "project_override"
        )
    elif mutation == "project-engineering-identity":
        semantics["closed_config_shapes"]["project"]["allowed_tables"].append(
            "identity"
        )
    elif mutation == "profile-invents-standalone":
        semantics["profile_selection"]["standalone_identity_creation"] = "allowed"
    elif mutation == "akbs-standalone-conflict-allowed":
        semantics["standalone_identity"]["conflict_behavior"] = "prefer_akbs"
    contract["semantics_sha256"] = module.canonical_json_sha256_v1(semantics)
    with pytest.raises(module.TopologyError, match="identity resolution semantics drifted"):
        module.validate_contract_documents(
            current,
            changed,
            MATRIX,
            current_sha256=hashlib.sha256(CURRENT_PATH.read_bytes()).hexdigest(),
        )


@pytest.mark.parametrize(
    ("surface_id", "negative_id"),
    [
        ("config.member-profile", "target-invalid-no-fallback"),
        ("config.member-profile", "target-profile-missing-no-fallback"),
        ("config.member-profile", "legacy-identity-conflict"),
        ("config.member-profile", "repository-report-alias-override"),
        ("config.member-profile", "akbs-standalone-identity-conflict"),
        ("config.member-profile", "profile-selector-invents-standalone"),
        ("config.android-engineering-extension", "user-unknown-table"),
        ("config.android-engineering-extension", "user-identity-extra-key"),
        ("config.android-engineering-extension", "project-identity"),
    ],
)
def test_identity_negative_case_cannot_be_removed(
    surface_id: str, negative_id: str,
) -> None:
    module = validator_module()
    topology = load(TOPOLOGY_PATH)
    current = load(CURRENT_PATH)
    changed = copy.deepcopy(MATRIX)
    config = next(
        row for row in changed["rows"] if row["surface_id"] == surface_id
    )
    config["test"]["negative_ids"].remove(negative_id)
    with pytest.raises(
        module.TopologyError,
        match="identity compatibility semantics drifted",
    ):
        module.validate_contract_documents(
            current,
            topology,
            changed,
            current_sha256=hashlib.sha256(CURRENT_PATH.read_bytes()).hexdigest(),
        )


def test_identity_test_map_binds_contract_and_matrix_semantics() -> None:
    module = validator_module()
    topology = load(TOPOLOGY_PATH)
    proof_map = load(
        ROOT / "contracts/plugin-topology/v2/compatibility-test-map.json"
    )
    identity_rows = [
        next(row for row in MATRIX["rows"] if row["surface_id"] == surface_id)
        for surface_id in module.ENGINEERING_IDENTITY_SURFACES
    ]
    assert proof_map["identity_resolution"] == {
        "source": (
            "migration-topology.json#/engineering_identity_resolution/semantics"
        ),
        "canonicalization": "akbs-canonical-json-sha256-v1",
        "sha256": topology["engineering_identity_resolution"]["semantics_sha256"],
        "matrix_surfaces": list(module.ENGINEERING_IDENTITY_SURFACES),
        "matrix_semantics_sha256": module.canonical_json_sha256_v1(identity_rows),
    }
    assert proof_map["matrix"]["sha256"] == hashlib.sha256(
        MATRIX_PATH.read_bytes()
    ).hexdigest()


def test_phase_lifecycle_separates_materialization_experiment_validation_and_migration() -> None:
    phases = MATRIX["phase_contract"]
    assert list(phases) == [
        "phase0", "phase1", "phase2", "phase3", "phase4", "phase5", "phase6",
    ]
    assert {
        "real_member_rollout", "real_remote_build_adb_or_upload",
        "real_mount_or_keychain_mutation", "server_submit",
        "provider_worker_dispatch", "legacy_removal",
    } <= set(phases["phase2"]["forbidden"])
    assert {
        "fake_remote_build_adb_upload", "readonly_provider_worker_routing",
        "core_direct_comparison",
    } <= set(phases["phase3"]["allowed"])
    assert {
        "real_wsl_engineering_member", "real_macos_engineering_member",
        "real_gms_report_only_member", "none_jinny_custom_modes",
        "v1_v2_common_patch_upload_lifecycle",
        "capture_submit_queue_curation_knowledge_search_loop",
    } <= set(phases["phase4"]["required"])

    rows = {row["surface_id"]: row for row in MATRIX["rows"]}
    for surface_id in (
        "plugin.akbs-member-ops", "plugin.android-engineering-ops",
        "plugin.jinny-android-practices", "skill.android-source-access",
        "cli.akbs-member", "cli.android-engineering",
        "cli.android-practices-provider", "cli.source-access",
        "state.source-access", "artifact.android-remote-build-deploy",
    ):
        activation = rows[surface_id]["activation"]
        assert activation["materialization_phase"] == "phase2"
        assert activation["real_activation_phase"] == "phase4"
    for surface_id in ("artifact.android-patch-capture", "package.android-change-v2"):
        activation = rows[surface_id]["activation"]
        assert activation["materialization_phase"] == "phase2"
        assert activation["real_activation_phase"] == "phase2"
    assert rows["config.android-engineering-extension"]["activation"]["real_activation_phase"] == "phase3"
    assert rows["cache.legacy-installations"]["activation"]["real_activation_phase"] == "phase5"
    assert rows["marketplace.entries"]["activation"]["real_activation_phase"] == "phase5"


def test_topology_validator_rejects_phase2_real_activation_confusion() -> None:
    module = validator_module()
    topology = load(TOPOLOGY_PATH)
    current = load(CURRENT_PATH)
    changed = copy.deepcopy(MATRIX)
    source = next(row for row in changed["rows"] if row["surface_id"] == "skill.android-source-access")
    source["activation"]["real_activation_phase"] = "phase2"
    with pytest.raises(module.TopologyError, match="materialization or real activation phase"):
        module.validate_contract_documents(
            current,
            topology,
            changed,
            current_sha256=hashlib.sha256(CURRENT_PATH.read_bytes()).hexdigest(),
        )


def test_topology_validator_rejects_contradictory_phase_permissions() -> None:
    module = validator_module()
    topology = load(TOPOLOGY_PATH)
    current = load(CURRENT_PATH)
    changed = copy.deepcopy(MATRIX)
    changed["phase_contract"]["phase2"]["allowed"].append("real_member_rollout")
    with pytest.raises(module.TopologyError, match="permits and forbids"):
        module.validate_contract_documents(
            current,
            topology,
            changed,
            current_sha256=hashlib.sha256(CURRENT_PATH.read_bytes()).hexdigest(),
        )


def test_topology_validator_rejects_extra_real_effect_in_phase3() -> None:
    module = validator_module()
    topology = load(TOPOLOGY_PATH)
    current = load(CURRENT_PATH)
    changed = copy.deepcopy(MATRIX)
    changed["phase_contract"]["phase3"]["allowed"].append(
        "real_remote_build_adb_upload"
    )
    with pytest.raises(module.TopologyError, match="Phase 0-6 semantics drifted"):
        module.validate_contract_documents(
            current,
            topology,
            changed,
            current_sha256=hashlib.sha256(CURRENT_PATH.read_bytes()).hexdigest(),
        )


def test_topology_validator_rejects_rewritten_surface_phase_actions() -> None:
    module = validator_module()
    topology = load(TOPOLOGY_PATH)
    current = load(CURRENT_PATH)
    changed = copy.deepcopy(MATRIX)
    capture = next(
        row for row in changed["rows"]
        if row["surface_id"] == "artifact.android-patch-capture"
    )
    capture["activation"]["gates_by_phase"]["phase2"] = [
        "real_capture_to_submit_adapter"
    ]
    capture["activation"]["ordered_actions_by_phase"]["phase2"] = [
        "capture_real_change", "submit_real_package"
    ]
    with pytest.raises(module.TopologyError, match="activation semantics drifted"):
        module.validate_contract_documents(
            current,
            topology,
            changed,
            current_sha256=hashlib.sha256(CURRENT_PATH.read_bytes()).hexdigest(),
        )


def test_migration_and_target_are_both_recognized_without_allowing_mixed_state() -> None:
    module = validator_module()
    topology = load(TOPOLOGY_PATH)
    assert module.validate_materialized_plugin_ids(
        set(module.MIGRATION_SOURCE_PLUGINS),
        set(module.MIGRATION_MARKETPLACE),
        topology,
        require_materialized=False,
    ) == "migration"
    assert module.validate_materialized_plugin_ids(
        set(module.TARGET_SOURCE_PLUGINS),
        set(module.TARGET_MARKETPLACE),
        topology,
    ) == "target"
    with pytest.raises(module.TopologyError, match="undeclared mixed"):
        module.validate_materialized_plugin_ids(
            set(module.MIGRATION_SOURCE_PLUGINS),
            set(module.TARGET_MARKETPLACE),
            topology,
            require_materialized=False,
        )


def test_marketplace_order_is_a_contract_not_a_set() -> None:
    module = validator_module()
    marketplace = load(ROOT / ".agents/plugins/marketplace.json")
    module.validate_marketplace_entries(marketplace, module.TARGET_MARKETPLACE)
    swapped = copy.deepcopy(marketplace)
    swapped["plugins"][0], swapped["plugins"][1] = (
        swapped["plugins"][1],
        swapped["plugins"][0],
    )
    with pytest.raises(module.TopologyError, match="order or identity"):
        module.validate_marketplace_entries(swapped, module.TARGET_MARKETPLACE)


def test_legacy_rollback_release_is_commit_version_and_hash_bound() -> None:
    module = validator_module()
    topology = load(TOPOLOGY_PATH)
    migration = next(row for row in topology["states"] if row["id"] == "migration")
    commit = migration["legacy_release"]["git_commit"]
    assert commit == (
        "79b3665393089ce2bdfb8db4021d03bcac84c8ad"
    )
    assert migration["legacy_release"]["jinny_android_practices"] == {
        "version": "1.0.3",
        "plugin_manifest_sha256": (
            "9face744fe5006039d79462e4b0ea9f7cd65a6aa22df9c5d85ecca803c423030"
        ),
        "git_tree_oid": "3790b3ccc1e708442fa81d1b2883761904765ea8",
    }
    expected_trees = {
        "android-framework-ops": "e810e7d2637d31b91e2102c55c6b3279c7eb926b",
        "android-wsl-ops": "03dc46cb3d69365611aa08414ee6049883e21868",
        "android-mac-ops": "489e64443a9d962fbd45afc556f8c844bfb3cbc9",
        "jinny-android-practices": "3790b3ccc1e708442fa81d1b2883761904765ea8",
    }
    legacy_rows = {row["id"]: row for row in migration["legacy_entries"]}
    for plugin, tree_oid in expected_trees.items():
        if plugin != "jinny-android-practices":
            assert legacy_rows[plugin]["git_tree_oid"] == tree_oid
        assert module.git_plugin_tree_oid(ROOT, commit, plugin) == tree_oid


def test_current_compatibility_source_is_separate_from_frozen_rollback() -> None:
    topology = load(TOPOLOGY_PATH)
    migration = next(row for row in topology["states"] if row["id"] == "migration")
    current_rows = {row["id"]: row for row in migration["compatibility_source_entries"]}
    rollback_rows = {row["id"]: row for row in migration["legacy_entries"]}
    assert set(current_rows) == set(rollback_rows)
    assert {row["mode"] for row in current_rows.values()} == {
        "physical_compatibility_source"
    }
    assert {row["mode"] for row in rollback_rows.values()} == {
        "immutable_release_catalog_entry"
    }
    assert "legacy_release_is_preserved_in_separate_legacy_repository_current_branch" in (
        topology["invariants"]
    )


def test_topology_validator_rejects_current_compatibility_drift() -> None:
    module = validator_module()
    topology = load(TOPOLOGY_PATH)
    current = load(CURRENT_PATH)
    changed = copy.deepcopy(topology)
    migration = next(row for row in changed["states"] if row["id"] == "migration")
    migration["compatibility_source_entries"][0]["git_tree_oid"] = "0" * 40
    with pytest.raises(module.TopologyError, match="current compatibility identity differs"):
        module.validate_contract_documents(
            current,
            changed,
            MATRIX,
            current_sha256=hashlib.sha256(CURRENT_PATH.read_bytes()).hexdigest(),
        )


def test_topology_validator_rejects_legacy_tree_oid_drift() -> None:
    module = validator_module()
    topology = load(TOPOLOGY_PATH)
    current = load(CURRENT_PATH)
    changed = copy.deepcopy(topology)
    migration = next(row for row in changed["states"] if row["id"] == "migration")
    migration["legacy_entries"][0]["git_tree_oid"] = "0" * 40
    with pytest.raises(module.TopologyError, match="legacy rollback identity differs"):
        module.validate_contract_documents(
            current,
            changed,
            MATRIX,
            current_sha256=hashlib.sha256(CURRENT_PATH.read_bytes()).hexdigest(),
        )


def test_provider_discovery_is_fixed_path_and_explicit_mode_only() -> None:
    topology = load(TOPOLOGY_PATH)
    migration = next(row for row in topology["states"] if row["id"] == "migration")
    discovery = migration["provider_discovery"]
    assert discovery["manifest_relative_path"] == (
        "contracts/android-practices-provider/v1/provider.json"
    )
    assert discovery["selection_modes"] == ["none", "jinny", "custom"]
    assert discovery["precedence"] == "project_then_user_then_none"
    assert discovery["active_inventory"] == "codex plugin list --json"
    assert discovery["historical_cache_role"] == "evidence_only"
    assert discovery["provider_root_authority"] == "active_inventory_versioned_cache"
    assert discovery["arbitrary_manifest_path_allowed"] is False
    assert discovery["config_bindings"] == {
        "none": ["mode"],
        "jinny": ["mode", "provider_version", "provider_manifest_sha256"],
        "custom": [
            "mode", "plugin_name", "provider_id", "provider_version",
            "provider_manifest_sha256",
        ],
    }
    assert discovery["selected_invalid_behavior"] == "fail_closed"


def test_standalone_plugins_package_the_frozen_contract_bytes() -> None:
    validator_module().validate_packaged_contract_parity(ROOT)
