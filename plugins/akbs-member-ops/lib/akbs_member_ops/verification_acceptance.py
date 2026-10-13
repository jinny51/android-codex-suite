from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def _value_at(payload: Mapping[str, Any], path: str) -> Any:
    value: Any = payload
    for field in path.split("."):
        if not isinstance(value, Mapping):
            return None
        value = value.get(field)
    return value


def _nonempty(value: Any) -> bool:
    if value is None or value is False:
        return False
    if isinstance(value, (str, bytes, Mapping, list, tuple, set)):
        return bool(value)
    return True


def authoritative_requirement_result_error(
    payload: Mapping[str, Any],
    contract: Mapping[str, Any],
    *,
    expected_result: str | None = None,
) -> str:
    reasons = contract["failure_reason_codes"]
    acceptance = contract["current_acceptance"]
    if payload.get("contract_version") != contract["evidence_contract_version"]:
        return str(reasons["contract_version"])
    if payload.get("scope") != acceptance["scope"]:
        return str(reasons["scope"])

    result = str(payload.get("result") or "").upper()
    if expected_result is not None and result != str(expected_result).upper():
        return str(reasons["result"])
    expected_acceptance = acceptance["result_acceptance"].get(result)
    if not expected_acceptance:
        return str(reasons["result"])
    if payload.get("requirement_acceptance") != expected_acceptance:
        return str(reasons["acceptance"])

    method = str(payload.get("method") or "")
    method_contract = acceptance["methods"].get(method)
    if not isinstance(method_contract, Mapping):
        return str(reasons["method"])
    required = method_contract.get("required_nonempty")
    if not isinstance(required, list) or any(
        not isinstance(path, str) or not _nonempty(_value_at(payload, path))
        for path in required
    ):
        return str(reasons["required_fields"])
    return ""


def knowledge_evidence_level(
    payload: Mapping[str, Any],
    contract: Mapping[str, Any],
) -> str:
    policy = contract["knowledge_evidence"]
    expected_result = str(policy["promotion_requires_authoritative_result"])
    if authoritative_requirement_result_error(
        payload,
        contract,
        expected_result=expected_result,
    ):
        return str(policy["non_authoritative_level"])
    if (
        payload.get("method") == "equivalent"
        and str(payload.get("equivalent_type") or "").strip().lower()
        in policy["static_equivalent_types"]
    ):
        return str(policy["static_equivalent_level"])
    remote_required = policy["remote_device_required_nonempty"]
    if payload.get("method") == "device" and all(
        _nonempty(_value_at(payload, path)) for path in remote_required
    ):
        artifacts = _value_at(payload, "remote_build.artifacts")
        artifact_required = policy["remote_artifact_required_nonempty"]
        if isinstance(artifacts, list) and artifacts and all(
            isinstance(artifact, Mapping)
            and all(_nonempty(_value_at(artifact, path)) for path in artifact_required)
            for artifact in artifacts
        ):
            return str(policy["remote_device_level"])
    return str(policy["default_authoritative_level"])
