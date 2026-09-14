from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "scripts/validate_active_plugin_topology.py"
CORE_LIB = ROOT / "plugins/android-engineering-ops/lib"
if str(CORE_LIB) not in sys.path:
    sys.path.insert(0, str(CORE_LIB))

from android_engineering_ops.json_contract import (  # noqa: E402
    ContractValidationError,
    validate_instance,
)


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_active_topology_validator_passes() -> None:
    result = subprocess.run(
        [sys.executable, str(VALIDATOR)], text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    assert result.returncode == 0, result.stderr


def test_extension_schema_is_deliberately_minimal() -> None:
    schema = load(ROOT / "contracts/android-orchestration-extension/v1/extension.schema.json")
    manifest = load(
        ROOT / "plugins/jinny-android-practices/contracts/android-orchestration-extension/v1/extension.json"
    )
    validate_instance(manifest, schema, schema)
    assert set(manifest) == {
        "schema", "provider_id", "provider_version",
        "compatible_core_contracts", "orchestrator",
    }
    for obsolete in ("roles", "models", "capabilities", "decision_entrypoint", "worker_profiles"):
        invalid = copy.deepcopy(manifest)
        invalid[obsolete] = {}
        with pytest.raises(ContractValidationError):
            validate_instance(invalid, schema, schema)


def test_compatibility_matrix_explains_every_changed_surface() -> None:
    matrix = load(ROOT / "contracts/plugin-topology/v3/compatibility-matrix.json")
    required = set(matrix["required_behavior_keys"])
    rows = {row["surface_id"]: row for row in matrix["rows"]}
    assert {
        "plugin.android-engineering-ops",
        "plugin.jinny-android-practices",
        "contract.android-orchestration-extension",
        "config.android-engineering.extension",
        "runtime.controller-worker-protocol",
        "contract.knowledge-incoming-package-v1",
    } == set(rows)
    assert all(required.issubset(row) for row in rows.values())


def test_removed_runtime_is_physically_absent() -> None:
    assert not (ROOT / "contracts/android-practices-provider").exists()
    assert not (ROOT / "contracts/android-change-workflow").exists()
    assert not (ROOT / "contracts/incoming/v2").exists()
    assert not (
        ROOT / "plugins/android-engineering-ops/lib/android_engineering_ops/workflow"
    ).exists()
    assert not (
        ROOT / "plugins/jinny-android-practices/skills/jinny-android-execution-policy"
    ).exists()
