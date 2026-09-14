"""Small dependency-free validator for packaged JSON Schema contracts."""

from __future__ import annotations

import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping


class ContractValidationError(ValueError):
    """A document does not satisfy its packaged JSON Schema contract."""


def load_json_bytes(raw: bytes, *, label: str) -> Any:
    def strict(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ContractValidationError(f"duplicate JSON key {key}: {label}")
            value[key] = item
        return value

    def reject_constant(value: str) -> None:
        raise ContractValidationError(f"non-finite JSON number {value}: {label}")

    try:
        return json.loads(
            raw.decode("utf-8"), object_pairs_hook=strict, parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractValidationError(f"invalid UTF-8 JSON: {label}") from exc


def load_json(path: Path) -> Any:
    return load_json_bytes(path.read_bytes(), label=str(path))


def _resolve_ref(root: Mapping[str, Any], reference: str) -> Any:
    if not reference.startswith("#/"):
        raise ContractValidationError(f"only local schema refs are supported: {reference}")
    value: Any = root
    for raw in reference[2:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if not isinstance(value, Mapping) or token not in value:
            raise ContractValidationError(f"unresolved schema ref: {reference}")
        value = value[token]
    return value


def _type_matches(value: Any, expected: str) -> bool:
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
    }.get(expected, False)


def _json_equal(left: Any, right: Any) -> bool:
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
            _json_equal(item, right[index]) for index, item in enumerate(left)
        )
    if isinstance(left, dict):
        return set(left) == set(right) and all(_json_equal(left[key], right[key]) for key in left)
    return left == right


def _declared_properties(schema: Any, root: Mapping[str, Any]) -> set[str]:
    if not isinstance(schema, Mapping):
        return set()
    result = set((schema.get("properties") or {}).keys())
    if "$ref" in schema:
        result.update(_declared_properties(_resolve_ref(root, str(schema["$ref"])), root))
    for key in ("allOf", "anyOf", "oneOf"):
        for child in schema.get(key) or []:
            result.update(_declared_properties(child, root))
    return result


def _is_valid(value: Any, schema: Any, root: Mapping[str, Any]) -> bool:
    try:
        validate_instance(value, schema, root)
        return True
    except ContractValidationError:
        return False


def _validate_datetime(value: str, *, path: str) -> None:
    match = re.fullmatch(
        r"(?P<year>[0-9]{4})-(?P<month>[0-9]{2})-(?P<day>[0-9]{2})[Tt]"
        r"(?P<hour>[0-9]{2}):(?P<minute>[0-9]{2}):(?P<second>[0-9]{2})"
        r"(?:\.(?P<fraction>[0-9]+))?"
        r"(?P<zone>[Zz]|(?P<sign>[+-])(?P<offset_hour>[0-9]{2}):(?P<offset_minute>[0-9]{2}))",
        value,
    )
    if match is None:
        raise ContractValidationError(f"date-time mismatch at {path}")
    try:
        datetime(int(match["year"]), int(match["month"]), int(match["day"]))
    except ValueError as exc:
        raise ContractValidationError(f"date-time mismatch at {path}") from exc
    if (
        int(match["hour"]) > 23
        or int(match["minute"]) > 59
        or int(match["second"]) > 60
        or (
            match["zone"] not in {"Z", "z"}
            and (int(match["offset_hour"]) > 23 or int(match["offset_minute"]) > 59)
        )
    ):
        raise ContractValidationError(f"date-time mismatch at {path}")


def validate_instance(
    value: Any, schema: Any, root: Mapping[str, Any], path: str = "$",
) -> None:
    if schema is False:
        raise ContractValidationError(f"false schema at {path}")
    if schema is True:
        return
    if not isinstance(schema, Mapping):
        raise ContractValidationError(f"invalid schema node at {path}")
    if "$ref" in schema:
        validate_instance(value, _resolve_ref(root, str(schema["$ref"])), root, path)
    if "type" in schema:
        expected = [schema["type"]] if isinstance(schema["type"], str) else schema["type"]
        if not isinstance(expected, list) or not any(_type_matches(value, item) for item in expected):
            raise ContractValidationError(f"type mismatch at {path}")
    if "const" in schema and not _json_equal(value, schema["const"]):
        raise ContractValidationError(f"const mismatch at {path}")
    if "enum" in schema and not any(_json_equal(value, item) for item in schema["enum"]):
        raise ContractValidationError(f"enum mismatch at {path}")
    if isinstance(value, str):
        if len(value) < int(schema.get("minLength", 0)):
            raise ContractValidationError(f"minLength at {path}")
        if "maxLength" in schema and len(value) > int(schema["maxLength"]):
            raise ContractValidationError(f"maxLength at {path}")
        if "pattern" in schema and re.search(str(schema["pattern"]), value) is None:
            raise ContractValidationError(f"pattern mismatch at {path}")
        if schema.get("format") == "date-time":
            _validate_datetime(value, path=path)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(value):
            raise ContractValidationError(f"non-finite number at {path}")
        if "minimum" in schema and value < schema["minimum"]:
            raise ContractValidationError(f"minimum at {path}")
        if "maximum" in schema and value > schema["maximum"]:
            raise ContractValidationError(f"maximum at {path}")
    if isinstance(value, list):
        if len(value) < int(schema.get("minItems", 0)):
            raise ContractValidationError(f"minItems at {path}")
        if "maxItems" in schema and len(value) > int(schema["maxItems"]):
            raise ContractValidationError(f"maxItems at {path}")
        if schema.get("uniqueItems") and any(
            _json_equal(item, previous)
            for index, item in enumerate(value)
            for previous in value[:index]
        ):
            raise ContractValidationError(f"uniqueItems at {path}")
        if "items" in schema:
            for index, item in enumerate(value):
                validate_instance(item, schema["items"], root, f"{path}/{index}")
        if "contains" in schema and not any(_is_valid(item, schema["contains"], root) for item in value):
            raise ContractValidationError(f"contains at {path}")
    object_keys = {
        "required", "properties", "additionalProperties", "unevaluatedProperties",
        "propertyNames", "minProperties", "maxProperties",
    }
    if isinstance(value, dict) and object_keys & set(schema):
        required = set(schema.get("required") or [])
        if not required.issubset(value):
            raise ContractValidationError(f"required missing at {path}")
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
            raise ContractValidationError(f"additional properties at {path}: {sorted(unknown)}")
        if isinstance(additional, Mapping):
            for name in unknown:
                validate_instance(value[name], additional, root, f"{path}/{name}")
        if schema.get("unevaluatedProperties") is False:
            unknown = set(value) - _declared_properties(schema, root)
            if unknown:
                raise ContractValidationError(f"unevaluated properties at {path}: {sorted(unknown)}")
        if len(value) < int(schema.get("minProperties", 0)):
            raise ContractValidationError(f"minProperties at {path}")
        if "maxProperties" in schema and len(value) > int(schema["maxProperties"]):
            raise ContractValidationError(f"maxProperties at {path}")
    for child in schema.get("allOf") or []:
        validate_instance(value, child, root, path)
    if "anyOf" in schema and not any(_is_valid(value, child, root) for child in schema["anyOf"]):
        raise ContractValidationError(f"anyOf at {path}")
    if "oneOf" in schema and sum(_is_valid(value, child, root) for child in schema["oneOf"]) != 1:
        raise ContractValidationError(f"oneOf at {path}")
    if "not" in schema and _is_valid(value, schema["not"], root):
        raise ContractValidationError(f"not at {path}")
    if "if" in schema:
        branch = schema.get("then") if _is_valid(value, schema["if"], root) else schema.get("else")
        if branch is not None:
            validate_instance(value, branch, root, path)


def validate_document(value: Any, schema_path: Path) -> None:
    schema = load_json(schema_path)
    if not isinstance(schema, Mapping):
        raise ContractValidationError(f"schema root must be an object: {schema_path}")
    validate_instance(value, schema, schema)
