from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

import pytest


ROOT = Path(__file__).resolve().parents[1]
DRAFT = "https://json-schema.org/draft/2020-12/schema"
SCHEMAS = {
    "provider": ROOT / "contracts/android-practices-provider/v1/provider.schema.json",
    "coding_decision": ROOT / "contracts/android-practices-provider/v1/coding-policy-decision.schema.json",
    "execution_decision": ROOT / "contracts/android-practices-provider/v1/execution-policy-decision.schema.json",
    "stage_snapshot": ROOT / "contracts/android-change-workflow/v1/stage-snapshot.schema.json",
    "worker_assignment": ROOT / "contracts/android-change-workflow/v1/worker-assignment.schema.json",
    "worker_result": ROOT / "contracts/android-change-workflow/v1/worker-result.schema.json",
    "android_change_package": ROOT / "contracts/incoming/v1/knowledge-incoming-package.schema.json",
}
FIXTURES = {
    "provider.valid": ("provider", ROOT / "contracts/android-practices-provider/v1/fixtures/provider.valid.json", True),
    "provider.invalid-authority": ("provider", ROOT / "contracts/android-practices-provider/v1/fixtures/provider.invalid-authority.json", False),
    "coding.valid": ("coding_decision", ROOT / "contracts/android-practices-provider/v1/fixtures/coding-decision.valid.json", True),
    "coding.invalid-override": ("coding_decision", ROOT / "contracts/android-practices-provider/v1/fixtures/coding-decision.invalid-override.json", False),
    "execution.valid": ("execution_decision", ROOT / "contracts/android-practices-provider/v1/fixtures/execution-decision.valid.json", True),
    "execution.invalid-extra-authority": ("execution_decision", ROOT / "contracts/android-practices-provider/v1/fixtures/execution-decision.invalid-extra-authority.json", False),
    "snapshot.valid": ("stage_snapshot", ROOT / "contracts/android-change-workflow/v1/fixtures/stage-snapshot.valid.json", True),
    "snapshot.invalid-disposition": ("stage_snapshot", ROOT / "contracts/android-change-workflow/v1/fixtures/stage-snapshot.invalid-disposition.json", True),
    "assignment.valid": ("worker_assignment", ROOT / "contracts/android-change-workflow/v1/fixtures/worker-assignment.valid.json", True),
    "assignment.invalid-authority": ("worker_assignment", ROOT / "contracts/android-change-workflow/v1/fixtures/worker-assignment.invalid-authority.json", False),
    "result.valid": ("worker_result", ROOT / "contracts/android-change-workflow/v1/fixtures/worker-result.valid.json", True),
    "result.invalid-acceptance": ("worker_result", ROOT / "contracts/android-change-workflow/v1/fixtures/worker-result.invalid-acceptance.json", False),
    "package.valid": ("android_change_package", ROOT / "contracts/incoming/v1/fixtures/patch.manifest.json", True),
}
SUPPORTING_FIXTURES: set[Path] = {
    ROOT / "contracts/incoming/v1/fixtures/daily.manifest.json",
    ROOT / "contracts/incoming/v1/fixtures/weekly.manifest.json",
}
KEYWORDS = {
    "$schema", "$id", "$ref", "$defs", "title", "description", "type", "const", "enum",
    "required", "properties", "additionalProperties", "unevaluatedProperties", "propertyNames",
    "pattern", "format", "minLength", "maxLength", "minimum", "maximum", "minItems",
    "maxItems", "uniqueItems", "items", "contains", "minProperties", "maxProperties",
    "allOf", "anyOf", "oneOf", "not", "if", "then", "else", "default", "examples",
}
TYPE_NAMES = {"object", "array", "string", "integer", "number", "boolean", "null"}


class ContractError(ValueError):
    pass


def load(path: Path):
    def strict(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ContractError(f"duplicate JSON key {key}: {path}")
            value[key] = item
        return value

    def reject_constant(value: str):
        raise ContractError(f"non-finite JSON number {value}: {path}")

    return json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=strict,
        parse_constant=reject_constant,
    )


def resolve_ref(root: Mapping[str, Any], reference: str):
    if not reference.startswith("#/"):
        raise ContractError(f"non-local ref: {reference}")
    value: Any = root
    for raw in reference[2:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if not isinstance(value, Mapping) or token not in value:
            raise ContractError(f"unresolved ref: {reference}")
        value = value[token]
    return value


def validate_definition(schema: Any, root: Mapping[str, Any], path: str = "$") -> None:
    if isinstance(schema, bool):
        return
    if not isinstance(schema, Mapping):
        raise ContractError(f"schema is not an object/boolean: {path}")
    unknown = set(schema) - KEYWORDS
    if unknown:
        raise ContractError(f"unsupported keyword at {path}: {sorted(unknown)}")
    if "$ref" in schema:
        if not isinstance(schema["$ref"], str):
            raise ContractError(f"$ref is not text at {path}")
        resolve_ref(root, str(schema["$ref"]))
    for key in ("$schema", "$id", "title", "description"):
        if key in schema and not isinstance(schema[key], str):
            raise ContractError(f"{key} is not text at {path}")
    if "type" in schema:
        raw_types = [schema["type"]] if isinstance(schema["type"], str) else schema["type"]
        if (
            not isinstance(raw_types, list)
            or not raw_types
            or not all(isinstance(item, str) for item in raw_types)
            or len(raw_types) != len(set(raw_types))
            or not set(raw_types).issubset(TYPE_NAMES)
        ):
            raise ContractError(f"invalid type at {path}")
    if "pattern" in schema:
        if not isinstance(schema["pattern"], str):
            raise ContractError(f"pattern is not text at {path}")
        re.compile(schema["pattern"])
    if "format" in schema and schema["format"] not in {"date", "date-time"}:
        raise ContractError(f"unsupported format at {path}: {schema['format']!r}")
    if "enum" in schema:
        enum = schema["enum"]
        if (
            not isinstance(enum, list)
            or not enum
            or any(
                json_equal(value, previous)
                for index, value in enumerate(enum)
                for previous in enum[:index]
            )
            or any(
                isinstance(value, float) and not math.isfinite(value)
                for value in enum
            )
        ):
            raise ContractError(f"invalid enum at {path}")
    for key in (
        "minLength", "maxLength", "minItems", "maxItems", "minProperties", "maxProperties"
    ):
        if key in schema and (
            not isinstance(schema[key], int)
            or isinstance(schema[key], bool)
            or schema[key] < 0
        ):
            raise ContractError(f"invalid {key} at {path}")
    for minimum, maximum in (
        ("minLength", "maxLength"),
        ("minItems", "maxItems"),
        ("minProperties", "maxProperties"),
    ):
        if minimum in schema and maximum in schema and schema[minimum] > schema[maximum]:
            raise ContractError(f"{minimum} exceeds {maximum} at {path}")
    for key in ("minimum", "maximum"):
        if key in schema and (
            not isinstance(schema[key], (int, float)) or isinstance(schema[key], bool)
            or not math.isfinite(schema[key])
        ):
            raise ContractError(f"invalid {key} at {path}")
    if "minimum" in schema and "maximum" in schema and schema["minimum"] > schema["maximum"]:
        raise ContractError(f"minimum exceeds maximum at {path}")
    if "uniqueItems" in schema and not isinstance(schema["uniqueItems"], bool):
        raise ContractError(f"uniqueItems is not boolean at {path}")
    if "required" in schema and (
        not isinstance(schema["required"], list)
        or len(schema["required"]) != len(set(schema["required"]))
        or not all(isinstance(item, str) for item in schema["required"])
    ):
        raise ContractError(f"invalid required at {path}")
    for key in ("properties", "$defs"):
        if key in schema:
            if not isinstance(schema[key], Mapping):
                raise ContractError(f"{key} is not an object at {path}")
            for name, child in schema[key].items():
                if not isinstance(name, str):
                    raise ContractError(f"{key} name is not text at {path}")
                validate_definition(child, root, f"{path}/{key}/{name}")
    for key in ("additionalProperties", "unevaluatedProperties", "propertyNames", "items", "contains", "not", "if", "then", "else"):
        if key in schema:
            if not isinstance(schema[key], (Mapping, bool)):
                raise ContractError(f"invalid {key} at {path}")
            validate_definition(schema[key], root, f"{path}/{key}")
    for key in ("allOf", "anyOf", "oneOf"):
        if key in schema:
            if not isinstance(schema[key], list) or not schema[key]:
                raise ContractError(f"invalid {key} at {path}")
            for index, child in enumerate(schema[key]):
                validate_definition(child, root, f"{path}/{key}/{index}")


def type_matches(value: Any, expected: str) -> bool:
    return {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
            and float(value).is_integer()
        ),
        "number": (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
        ),
        "boolean": isinstance(value, bool),
        "null": value is None,
    }[expected]


def json_equal(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left is right
    if (
        isinstance(left, (int, float))
        and not isinstance(left, bool)
        and isinstance(right, (int, float))
        and not isinstance(right, bool)
    ):
        return left == right
    if type(left) is not type(right):
        return False
    if isinstance(left, list):
        return len(left) == len(right) and all(
            json_equal(item, right[index]) for index, item in enumerate(left)
        )
    if isinstance(left, dict):
        return set(left) == set(right) and all(
            json_equal(left[key], right[key]) for key in left
        )
    return left == right


def declared_properties(schema: Any, root: Mapping[str, Any]) -> set[str]:
    if not isinstance(schema, Mapping):
        return set()
    result = set((schema.get("properties") or {}).keys())
    if "$ref" in schema:
        result.update(declared_properties(resolve_ref(root, str(schema["$ref"])), root))
    for key in ("allOf", "anyOf", "oneOf"):
        for child in schema.get(key) or []:
            result.update(declared_properties(child, root))
    return result


def is_valid(value: Any, schema: Any, root: Mapping[str, Any]) -> bool:
    try:
        validate_instance(value, schema, root)
        return True
    except ContractError:
        return False


def validate_instance(value: Any, schema: Any, root: Mapping[str, Any], path: str = "$") -> None:
    if schema is False:
        raise ContractError(f"false schema at {path}")
    if schema is True:
        return
    if "$ref" in schema:
        validate_instance(value, resolve_ref(root, str(schema["$ref"])), root, path)
    if "type" in schema:
        expected = [schema["type"]] if isinstance(schema["type"], str) else schema["type"]
        if not any(type_matches(value, item) for item in expected):
            raise ContractError(f"type mismatch at {path}")
    if "const" in schema and not json_equal(value, schema["const"]):
        raise ContractError(f"const mismatch at {path}")
    if "enum" in schema and not any(json_equal(value, item) for item in schema["enum"]):
        raise ContractError(f"enum mismatch at {path}")
    if isinstance(value, str):
        if len(value) < int(schema.get("minLength", 0)):
            raise ContractError(f"minLength at {path}")
        if "maxLength" in schema and len(value) > int(schema["maxLength"]):
            raise ContractError(f"maxLength at {path}")
        if "pattern" in schema and re.search(str(schema["pattern"]), value) is None:
            raise ContractError(f"pattern mismatch at {path}")
        if schema.get("format") == "date-time":
            match = re.fullmatch(
                r"(?P<year>[0-9]{4})-(?P<month>[0-9]{2})-(?P<day>[0-9]{2})[Tt]"
                r"(?P<hour>[0-9]{2}):(?P<minute>[0-9]{2}):(?P<second>[0-9]{2})"
                r"(?:\.(?P<fraction>[0-9]+))?"
                r"(?P<zone>[Zz]|(?P<sign>[+-])(?P<offset_hour>[0-9]{2}):(?P<offset_minute>[0-9]{2}))",
                value,
            )
            if match is None:
                raise ContractError(f"date-time mismatch at {path}")
            try:
                datetime(
                    int(match["year"]),
                    int(match["month"]),
                    int(match["day"]),
                )
            except ValueError as error:
                raise ContractError(f"date-time mismatch at {path}") from error
            if (
                int(match["hour"]) > 23
                or int(match["minute"]) > 59
                or int(match["second"]) > 60
                or (
                    match["zone"] not in {"Z", "z"}
                    and (
                        int(match["offset_hour"]) > 23
                        or int(match["offset_minute"]) > 59
                    )
                )
            ):
                raise ContractError(f"date-time mismatch at {path}")
        elif schema.get("format") == "date":
            try:
                datetime.strptime(value, "%Y-%m-%d")
            except ValueError as error:
                raise ContractError(f"date mismatch at {path}") from error
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(value):
            raise ContractError(f"non-finite number at {path}")
        if "minimum" in schema and value < schema["minimum"]:
            raise ContractError(f"minimum at {path}")
        if "maximum" in schema and value > schema["maximum"]:
            raise ContractError(f"maximum at {path}")
    if isinstance(value, list):
        if len(value) < int(schema.get("minItems", 0)):
            raise ContractError(f"minItems at {path}")
        if "maxItems" in schema and len(value) > int(schema["maxItems"]):
            raise ContractError(f"maxItems at {path}")
        if schema.get("uniqueItems") and len({json.dumps(item, sort_keys=True) for item in value}) != len(value):
            raise ContractError(f"uniqueItems at {path}")
        if "items" in schema:
            for index, item in enumerate(value):
                validate_instance(item, schema["items"], root, f"{path}/{index}")
        if "contains" in schema and not any(is_valid(item, schema["contains"], root) for item in value):
            raise ContractError(f"contains at {path}")
    object_keys = {"required", "properties", "additionalProperties", "unevaluatedProperties", "propertyNames", "minProperties", "maxProperties"}
    if isinstance(value, dict) and object_keys & set(schema):
        required = set(schema.get("required") or [])
        if not required.issubset(value):
            raise ContractError(f"required missing at {path}")
        properties = schema.get("properties") or {}
        for name, child in properties.items():
            if name in value:
                validate_instance(value[name], child, root, f"{path}/{name}")
        if "propertyNames" in schema:
            for name in value:
                validate_instance(name, schema["propertyNames"], root, f"{path}/<name>")
        unknown = set(value) - set(properties)
        additional = schema.get("additionalProperties", True)
        if additional is False and unknown:
            raise ContractError(f"additional properties at {path}")
        if isinstance(additional, Mapping):
            for name in unknown:
                validate_instance(value[name], additional, root, f"{path}/{name}")
        if schema.get("unevaluatedProperties") is False:
            if set(value) - declared_properties(schema, root):
                raise ContractError(f"unevaluated properties at {path}")
        if len(value) < int(schema.get("minProperties", 0)):
            raise ContractError(f"minProperties at {path}")
        if "maxProperties" in schema and len(value) > int(schema["maxProperties"]):
            raise ContractError(f"maxProperties at {path}")
    for child in schema.get("allOf") or []:
        validate_instance(value, child, root, path)
    if "anyOf" in schema and not any(is_valid(value, child, root) for child in schema["anyOf"]):
        raise ContractError(f"anyOf at {path}")
    if "oneOf" in schema and sum(is_valid(value, child, root) for child in schema["oneOf"]) != 1:
        raise ContractError(f"oneOf at {path}")
    if "not" in schema and is_valid(value, schema["not"], root):
        raise ContractError(f"not at {path}")
    if "if" in schema:
        branch = schema.get("then") if is_valid(value, schema["if"], root) else schema.get("else")
        if branch is not None:
            validate_instance(value, branch, root, path)


def active_validator():
    path = ROOT / "scripts/validate_active_plugin_topology.py"
    spec = importlib.util.spec_from_file_location("akbs_active_contracts", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixture_inventory() -> set[Path]:
    roots = {
        ROOT / "contracts/android-practices-provider/v1/fixtures",
        ROOT / "contracts/android-change-workflow/v1/fixtures",
        ROOT / "contracts/incoming/v1/fixtures",
    }
    return {path for root in roots for path in root.glob("*.json")}


def test_schema_subset_meta_gate_and_disk_fixture_expectations() -> None:
    expected = {path for _, path, _ in FIXTURES.values()} | SUPPORTING_FIXTURES
    assert fixture_inventory() == expected
    schemas = {name: load(path) for name, path in SCHEMAS.items()}
    for schema in schemas.values():
        assert schema["$schema"] == DRAFT
        validate_definition(schema, schema)
    results = {}
    for fixture_id, (schema_id, path, expected_valid) in FIXTURES.items():
        actual = is_valid(load(path), schemas[schema_id], schemas[schema_id])
        assert actual is expected_valid, fixture_id
        results[fixture_id] = actual
    assert sum(results.values()) == 8
    assert len(results) - sum(results.values()) == 5


def test_schema_meta_gate_rejects_unknown_keyword_and_broken_ref(tmp_path: Path) -> None:
    schema = load(SCHEMAS["provider"])
    unknown = copy.deepcopy(schema)
    unknown["unsupportedKeyword"] = True
    with pytest.raises(ContractError, match="unsupported"):
        validate_definition(unknown, unknown)
    broken = copy.deepcopy(schema)
    broken["properties"]["authority"] = {"$ref": "#/$defs/missing"}
    with pytest.raises(ContractError, match="unresolved"):
        validate_definition(broken, broken)
    invalid_definitions = [
        {"type": "string", "minLength": -1},
        {"type": "string", "minLength": "1"},
        {"type": {"string": True}},
        {"type": ["string", "string"]},
        {"enum": []},
        {"enum": [1, 1.0]},
        {"type": "array", "uniqueItems": "yes"},
        {"type": "string", "format": "unknown-format"},
        {"type": "number", "minimum": float("inf")},
    ]
    for invalid in invalid_definitions:
        with pytest.raises(ContractError):
            validate_definition(invalid, invalid)
    bounded_object = {"type": "object", "maxProperties": 1}
    validate_definition(bounded_object, bounded_object)
    with pytest.raises(ContractError, match="maxProperties"):
        validate_instance({"one": 1, "two": 2}, bounded_object, bounded_object)
    nonfinite = tmp_path / "nonfinite.json"
    nonfinite.write_text('{"value": NaN}\n', encoding="utf-8")
    with pytest.raises(ContractError, match="non-finite"):
        load(nonfinite)
    assert type_matches(float("nan"), "number") is False


def test_json_equality_is_boolean_safe_and_coding_decisions_execute_schema() -> None:
    assert json_equal(False, 0) is False
    assert json_equal(True, 1) is False
    assert type_matches(1.0, "integer") is True
    assert type_matches(True, "integer") is False
    assignment_schema = load(SCHEMAS["worker_assignment"])
    numeric_authority = load(FIXTURES["assignment.invalid-authority"][1])
    with pytest.raises(ContractError):
        validate_instance(numeric_authority, assignment_schema, assignment_schema)
    boolean_escalation = load(FIXTURES["assignment.valid"][1])
    boolean_escalation["constraints"]["max_automatic_escalations"] = True
    with pytest.raises(ContractError):
        validate_instance(boolean_escalation, assignment_schema, assignment_schema)
    date_time = {"format": "date-time"}
    for value in (
        "2026-09-01T00:00:00.1Z",
        "2026-09-01T00:00:00.12Z",
        "2026-09-01T00:00:00.123Z",
        "2026-09-01T00:00:00.1234Z",
        "2026-09-01T00:00:00.1234567Z",
        "2026-09-01t00:00:00z",
        "2026-09-01T08:00:00+08:00",
        "1990-12-31T23:59:60Z",
    ):
        validate_instance(value, date_time, date_time)
    for invalid in (
        "2026-09-01",
        "2026-02-30T00:00:00Z",
        "2026-09-01T24:00:00Z",
        "2026-09-01T00:00:61Z",
        "2026-09-01T00:00:00+24:00",
    ):
        with pytest.raises(ContractError, match="date-time"):
            validate_instance(invalid, date_time, date_time)
    schema = load(SCHEMAS["coding_decision"])
    valid = {
        "schema": "coding-policy-decision-v1",
        "decision_id": "coding-1",
        "run_id": "run-1",
        "stage_id": "stage-1",
        "context_sha256": "1" * 64,
        "core_policy_sha256": "2" * 64,
        "provider": {
            "provider_id": "jinny-android-practices",
            "provider_version": "1.0.0",
                "provider_manifest_sha256": "3" * 64,
                "skill_id": "jinny-android-coding-practices",
                "skill_version": "1.0.0",
                "skill_sha256": "4" * 64,
                "agent_metadata_sha256": "5" * 64,
                "decision_entrypoint_sha256": "6" * 64,
            },
        "outcome": {
            "type": "applied",
            "rules": [
                {
                    "rule_id": "rule-1",
                    "effect": "recommend",
                    "statement": "Prefer a clear local name",
                    "scope": {
                        "component_layers": ["application"],
                        "path_patterns": ["src/**"],
                        "languages": ["java"],
                    },
                    "evidence_requirements": [],
                }
            ],
        },
        "created_at": "2026-09-01T00:00:00Z",
    }
    validate_instance(valid, schema, schema)
    invalid = copy.deepcopy(valid)
    invalid["outcome"]["rules"][0]["effect"] = "override_core"
    with pytest.raises(ContractError):
        validate_instance(invalid, schema, schema)


def test_v1_package_components_are_simple_layer_patch_rows() -> None:
    schema = load(SCHEMAS["android_change_package"])
    package = load(FIXTURES["package.valid"][1])
    validate_instance(package, schema, schema)
    assert package["components"] == [
        {"layer": "platform", "patches": ["patches/frameworks-base.patch"]}
    ]
    invalid = copy.deepcopy(package)
    invalid["components"] = ["platform"]
    with pytest.raises(ContractError):
        validate_instance(invalid, schema, schema)


def test_cross_document_semantics_accept_valid_and_reject_invalid_fixtures() -> None:
    active = active_validator()
    provider = load(FIXTURES["provider.valid"][1])
    decision = load(FIXTURES["execution.valid"][1])
    active.validate_provider_execution_decision(
        provider, "a" * 64, decision, rollout_effect_ceiling="read_only"
    )
    active.validate_stage_snapshot_semantics(load(FIXTURES["snapshot.valid"][1]))
    with pytest.raises(active.TopologyError):
        active.validate_stage_snapshot_semantics(
            load(FIXTURES["snapshot.invalid-disposition"][1])
        )
    assignment = load(FIXTURES["assignment.valid"][1])
    active.validate_assignment_semantics(assignment)
    active.validate_worker_result_semantics(
        load(FIXTURES["result.valid"][1]), assignment, assignment_sha256="e" * 64
    )
    package_path = FIXTURES["package.valid"][1]
    package = load(package_path)
    manifest_bytes = package_path.read_bytes()
    declared = []
    for value in package["files"].values():
        declared.extend(value if isinstance(value, list) else [value])
    result = active.validate_android_change_package_semantics(
        manifest_bytes,
        {path: ("0" * 64, 0) for path in declared},
    )
    assert result["reference_integrity_valid"] is True
    assert result["archive_inventory_binding_valid"] is True
    invalid_package = copy.deepcopy(package)
    invalid_package["components"][0]["patches"] = ["patches/missing.patch"]
    invalid_manifest_bytes = (
        json.dumps(
            invalid_package,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    with pytest.raises(active.TopologyError):
        active.validate_android_change_package_semantics(
            invalid_manifest_bytes,
            {path: ("0" * 64, 0) for path in declared},
        )
