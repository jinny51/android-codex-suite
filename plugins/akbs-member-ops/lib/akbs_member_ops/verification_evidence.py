from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from .verification_acceptance import authoritative_requirement_result_error


VERIFICATION_ACCEPTANCE_CONTRACT_PATH = (
    Path(__file__).resolve().parents[2]
    / "internal"
    / "incoming-v2"
    / "references"
    / "verification-acceptance-v2.json"
)


@lru_cache(maxsize=1)
def load_verification_contract() -> dict[str, Any]:
    payload = json.loads(VERIFICATION_ACCEPTANCE_CONTRACT_PATH.read_text(encoding="utf-8"))
    if payload.get("schema") != "akbs-verification-acceptance-contract-v2":
        raise RuntimeError("verification acceptance contract schema is invalid")
    acceptance = payload.get("current_acceptance")
    if not isinstance(acceptance, dict) or not isinstance(
        acceptance.get("result_acceptance"),
        dict,
    ):
        raise RuntimeError("verification acceptance contract is incomplete")
    return payload


_VERIFICATION_CONTRACT = load_verification_contract()
VERIFICATION_EVIDENCE_CONTRACT_VERSION = str(
    _VERIFICATION_CONTRACT["evidence_contract_version"]
)
BUILD_DELIVERY_SCOPE = "build_delivery"
REQUIREMENT_SCOPE = str(_VERIFICATION_CONTRACT["current_acceptance"]["scope"])
REQUIREMENT_ACCEPTED = str(
    _VERIFICATION_CONTRACT["current_acceptance"]["result_acceptance"]["PASS"]
)
REQUIREMENT_REJECTED = str(
    _VERIFICATION_CONTRACT["current_acceptance"]["result_acceptance"]["FAIL"]
)
REQUIREMENT_UNVERIFIED = str(
    _VERIFICATION_CONTRACT["non_authoritative_evidence"]["scopes"][
        BUILD_DELIVERY_SCOPE
    ]["requirement_acceptance"]
)


def build_delivery_contract_fields() -> dict[str, str]:
    return {
        "contract_version": VERIFICATION_EVIDENCE_CONTRACT_VERSION,
        "scope": BUILD_DELIVERY_SCOPE,
        "requirement_acceptance": REQUIREMENT_UNVERIFIED,
    }


def requirement_contract_fields(result: str) -> dict[str, str]:
    normalized = str(result or "").upper()
    if normalized == "PASS":
        acceptance = REQUIREMENT_ACCEPTED
    elif normalized == "FAIL":
        acceptance = REQUIREMENT_REJECTED
    else:
        acceptance = REQUIREMENT_UNVERIFIED
    return {
        "contract_version": VERIFICATION_EVIDENCE_CONTRACT_VERSION,
        "scope": REQUIREMENT_SCOPE,
        "requirement_acceptance": acceptance,
    }


def has_authoritative_requirement_result(
    payload: Mapping[str, Any],
    *,
    expected_result: str | None = None,
) -> bool:
    return not authoritative_requirement_result_error(
        payload,
        _VERIFICATION_CONTRACT,
        expected_result=expected_result,
    )
