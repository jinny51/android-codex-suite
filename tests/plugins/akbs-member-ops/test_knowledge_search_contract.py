from __future__ import annotations

import copy
import json
import hashlib
from pathlib import Path
import sys
from types import SimpleNamespace
import urllib.parse

import pytest


ROOT = Path(__file__).resolve().parents[3]
LIB = ROOT / "plugins/akbs-member-ops/lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from akbs_member_ops.knowledge_search import api as search_api  # noqa: E402
from akbs_member_ops.knowledge_search.api import (  # noqa: E402
    SEARCH_CONTRACT_HEADER,
    SEARCH_RESPONSE_SCHEMA,
    fetch_server_results,
    normalize_server_results,
    server_search_url,
    validate_server_payload,
)
from akbs_member_ops.knowledge_search import cli as search_cli  # noqa: E402
from akbs_member_ops.knowledge_search.cli import (  # noqa: E402
    build_parser,
    combine_results,
    distinct_queries,
    healthy_multi_query_empty,
    healthy_server_search,
    validate_reuse_decision,
)
from akbs_member_ops.knowledge_search.formatting import format_markdown  # noqa: E402
from akbs_member_ops.knowledge_search.local_index import load_rows, search  # noqa: E402
from akbs_member_ops.json_io import write_json_once  # noqa: E402
from akbs_member_ops.http_client import HttpClientFailure  # noqa: E402


def _payload(*, empty: bool = False) -> dict:
    results = [] if empty else [
        {
            "type": "implementation",
            "case_id": "case-display",
            "implementation_id": "implementation-display-rk14",
            "title": "Keep display awake",
            "reuse_grade": "adaptation_candidate",
            "qualification_reason": "same Android version; different project",
            "requires_revalidation": True,
            "layers": ["platform"],
            "required_bindings": [
                {
                    "binding_id": "binding-display",
                    "patch_package_id": "patch-package-display",
                    "patch_asset_id": "asset-display",
                    "patch_sha256": "a" * 64,
                    "binding_role": "implementation",
                    "binding_state": "accepted",
                    "acceptance_ref": "acceptance-display",
                    "scope": {},
                    "verification_refs": [],
                    "relative_path": "patches/frameworks-base@display.patch",
                    "display_name": "frameworks-base@display.patch",
                    "size_bytes": 1024,
                    "layer": "platform",
                    "repository": "frameworks/base",
                    "repository_source": "manifest",
                    "closure_state": "closed",
                    "closure_reason": "reviewed",
                    "manifest_revision": 1,
                    "manifest_sha256": "b" * 64,
                    "environment_evidence": [
                        {
                            "project": "TVI2343R",
                            "platform": "rk",
                            "android_version": "14",
                            "source_lineage_ref": "fixture:reviewed",
                            "constraints": [],
                            "constraints_state": "known_empty",
                            "validation_state": "validated",
                            "content_hash": "e" * 64,
                        }
                    ],
                }
            ],
            "evidence_gaps": [
                {
                    "reason": "target_environment_not_covered",
                    "binding_id": "binding-display",
                    "context": {
                        "target_environment": {
                            "project": "TVE8402M",
                            "platform": "rk",
                            "android_version": "14",
                        }
                    },
                }
            ],
            "environment_comparison": {
                "mode": "adaptation",
                "alternative_tuples": [
                    {
                        "environment": {
                            "project": "TVI2343R",
                            "platform": "rk",
                            "android_version": "14",
                        },
                        "matched_dimensions": ["android_version", "platform"],
                        "different_dimensions": ["project"],
                        "source_lineage_ref": "fixture:reviewed",
                        "constraints": [],
                        "constraints_evaluation": "not_applicable",
                    }
                ],
                "non_dominated_matched_dimension_sets": [["android_version", "platform"]],
            },
        }
    ]
    return {
        "schema": "akbs-member-knowledge-search-v2",
        "search_mode": "two_stage_structured_lexical",
        "query": "display power",
        "result_type": "all",
        "result_state": "empty_for_this_query" if empty else "matches",
        "completeness": "complete",
        "reason_code": "",
        "not_found": False,
        "target_environment": {
            "project": "TVE8402M",
            "platform": "rk",
            "android_version": "14",
        },
        "filters": {"component": {"component_layer": ["platform"]}},
        "pagination": {
            "offset": 0,
            "limit": 8,
            "total": len(results),
            "has_more": False,
        },
        "projection": {
            "ready": True,
            "complete": True,
            "reason_code": "",
            "generation": 4,
            "eligible_count": 100,
            "indexed_count": 100,
            "isolated_count": 0,
        },
        "results": results,
    }


def test_server_types_and_explicit_local_archive_types_remain_distinct() -> None:
    parser = build_parser()
    assert parser.parse_args(["display", "--type", "implementation"]).type == "implementation"
    for legacy_type in ("variant", "report", "event", "evidence"):
        assert parser.parse_args(["display", "--type", legacy_type]).type == legacy_type
        with pytest.raises(SystemExit, match="require local text search"):
            search_cli.main(["display", "--source", "server", "--type", legacy_type, "--no-record-usage"])


def test_vendored_v2_contract_matches_client_identity() -> None:
    contract = json.loads(
        (
            ROOT
            / "plugins/akbs-member-ops/contracts/knowledge/member-knowledge-search-v2.schema.json"
        ).read_text(encoding="utf-8")
    )
    assert contract["$id"] == SEARCH_RESPONSE_SCHEMA
    assert contract["properties"]["schema"]["const"] == SEARCH_RESPONSE_SCHEMA
    assert contract["x-akbs-request-contract"] == {
        "header": SEARCH_CONTRACT_HEADER,
        "value": SEARCH_RESPONSE_SCHEMA,
        "missing_or_different_status": 426,
    }


def test_negative_offset_is_rejected_before_search() -> None:
    with pytest.raises(SystemExit, match="--offset must be zero or greater"):
        search_cli.main(["display", "--offset", "-1", "--no-record-usage"])


def test_limit_and_query_count_are_bounded() -> None:
    with pytest.raises(SystemExit, match="--limit must be between 1 and 50"):
        search_cli.main(["display", "--limit", "51", "--no-record-usage"])
    with pytest.raises(SystemExit, match="at most 8"):
        distinct_queries("q0", [f"q{index}" for index in range(1, 9)])


def test_local_search_cannot_authorize_reuse(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(search_cli, "find_root", lambda _value: tmp_path)
    monkeypatch.setattr(search_cli, "load_rows", lambda *_args, **_kwargs: [])
    with pytest.raises(SystemExit, match="cannot authorize reuse"):
        search_cli.main(
            [
                "display",
                "--source",
                "local",
                "--reuse-decision",
                "reuse",
                "--no-record-usage",
            ]
        )


def test_server_url_passes_environment_pagination_and_layers(monkeypatch) -> None:
    monkeypatch.setattr(
        "akbs_member_ops.knowledge_search.api.member_search_endpoint_url",
        lambda: ("https://akbs.invalid/member/knowledge-search", "fixture"),
    )
    args = SimpleNamespace(
        limit=8,
        offset=16,
        type="implementation",
        project="TVE8402M",
        platform="rk",
        android_version="14",
        component_layer=["platform", "device"],
    )
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(server_search_url(args, "display")).query)
    assert query == {
        "q": ["display"],
        "limit": ["8"],
        "offset": ["16"],
        "type": ["implementation"],
        "project": ["TVE8402M"],
        "platform": ["rk"],
        "android_version": ["14"],
        "component_layer": ["platform", "device"],
    }


def test_server_response_is_strict_and_preserves_reuse_evidence() -> None:
    payload = _payload()
    validate_server_payload(payload)
    result = normalize_server_results(payload)[0]
    assert result["kind"] == "implementation"
    assert result["id"] == "implementation-display-rk14"
    assert result["reuse_grade"] == "adaptation_candidate"
    assert result["layers"] == ["platform"]
    assert result["required_bindings"][0]["repository"] == "frameworks/base"

    invalid = dict(payload)
    invalid.pop("projection")
    with pytest.raises(Exception, match="projection"):
        validate_server_payload(invalid)


def test_server_result_layers_are_search_scope_not_qualification_binding_alias() -> None:
    case_payload = copy.deepcopy(_payload())
    case_payload["results"][0].update(
        {
            "type": "case",
            "implementation_id": "",
            "layers": ["device", "platform"],
        }
    )
    case_payload["filters"]["component"]["component_layer"] = ["device"]
    validate_server_payload(case_payload)

    patch_payload = copy.deepcopy(_payload())
    patch_payload["results"][0].update(
        {
            "type": "patch",
            "implementation_id": "implementation-display-rk14",
            "evidence_binding_id": "binding-verification-device",
            "patch_asset_id": "asset-verification-device",
            "patch_id": "patch-verification-device",
            "layer": "device",
            "layers": ["device", "platform"],
        }
    )
    patch_payload["filters"]["component"]["component_layer"] = ["device"]
    validate_server_payload(patch_payload)


def _unknown_layer_reference_payload(result_kind: str = "case") -> dict:
    payload = copy.deepcopy(_payload())
    payload["filters"]["component"]["component_layer"] = ["application"]
    payload["results"][0].update(
        {
            "type": result_kind,
            "implementation_id": "implementation-display-rk14" if result_kind == "implementation" else "",
            "layers": [],
            "reuse_grade": "reference_only",
            "qualification_reason": "implementation_layer_unknown",
            "requires_revalidation": True,
            "required_bindings": [],
            "environment_comparison": {
                "mode": "not_qualified",
                "alternative_tuples": [],
                "non_dominated_matched_dimension_sets": [],
            },
            "evidence_gaps": [
                {
                    "reason": "implementation_layer_unknown",
                    "binding_id": "",
                    "context": {},
                }
            ],
        }
    )
    return payload


def test_unknown_layer_case_is_only_a_reference_not_a_layer_match() -> None:
    payload = _unknown_layer_reference_payload()
    validate_server_payload(payload)
    result = normalize_server_results(payload)[0]
    assert result["kind"] == "case"
    assert result["layers"] == []
    assert result["reuse_grade"] == "reference_only"
    assert result["requires_revalidation"] is True
    assert result["required_bindings"] == []
    assert result["qualification_reason"] == "implementation_layer_unknown"


@pytest.mark.parametrize("result_type", ["all", "implementation"])
def test_unknown_layer_implementation_is_only_a_reference_not_a_layer_match(result_type: str) -> None:
    payload = _unknown_layer_reference_payload("implementation")
    payload["result_type"] = result_type
    args = SimpleNamespace(
        type=result_type, project="TVE8402M", platform="rk", android_version="14",
        component_layer=["application"], offset=0, limit=8,
    )
    validate_server_payload(payload, args=args, query="display power")
    result = normalize_server_results(payload)[0]
    assert result["kind"] == "implementation"
    assert result["implementation_id"] == "implementation-display-rk14"
    assert result["layers"] == []
    assert result["reuse_grade"] == "reference_only"
    assert result["requires_revalidation"] is True
    assert result["required_bindings"] == []
    assert result["qualification_reason"] == "implementation_layer_unknown"
    assert result["environment_comparison"] == {
        "mode": "not_qualified", "alternative_tuples": [],
        "non_dominated_matched_dimension_sets": [],
    }


@pytest.mark.parametrize("result_kind", ["case", "implementation"])
@pytest.mark.parametrize(
    "changes",
    [
        {"type": "patch"},
        {"type": "symbol"},
        {"layers": ["hal"]},
        {"reuse_grade": "direct_reuse_candidate"},
        {"reuse_grade": "adaptation_candidate"},
        {"qualification_reason": "migration_review_required"},
        {"requires_revalidation": False},
        {"required_bindings": _payload()["results"][0]["required_bindings"]},
        {"environment_comparison": _payload()["results"][0]["environment_comparison"]},
        {"environment_comparison": {"mode": "exact", "alternative_tuples": [], "non_dominated_matched_dimension_sets": []}},
        {"evidence_gaps": []},
        {"evidence_gaps": [{"reason": "implementation_layer_unknown", "binding_id": "binding-display", "context": {}}]},
        {"evidence_gaps": [{"reason": "implementation_layer_unknown", "binding_id": "", "context": {"claimed": True}}]},
    ],
)
def test_unknown_layer_exception_does_not_relax_other_results(changes: dict, result_kind: str) -> None:
    payload = _unknown_layer_reference_payload(result_kind)
    payload["results"][0].update(copy.deepcopy(changes))
    with pytest.raises(HttpClientFailure):
        validate_server_payload(payload)


def test_server_response_accepts_one_closed_exact_tuple_for_direct_reuse() -> None:
    payload = copy.deepcopy(_payload())
    item = payload["results"][0]
    item["reuse_grade"] = "direct_reuse_candidate"
    item["requires_revalidation"] = False
    evidence = item["required_bindings"][0]["environment_evidence"][0]
    evidence.update(payload["target_environment"])
    comparison = item["environment_comparison"]
    comparison["mode"] = "exact"
    comparison["alternative_tuples"][0].update(
        {
            "environment": dict(payload["target_environment"]),
            "matched_dimensions": ["android_version", "platform", "project"],
            "different_dimensions": [],
        }
    )
    comparison["non_dominated_matched_dimension_sets"] = [
        ["android_version", "platform", "project"]
    ]
    validate_server_payload(payload)

    invalid_constraints = copy.deepcopy(payload)
    alternative = invalid_constraints["results"][0]["environment_comparison"][
        "alternative_tuples"
    ][0]
    alternative["constraints"] = [{"kind": "product", "value": "tv"}]
    alternative["constraints_evaluation"] = "not_evaluated"
    invalid_constraints["results"][0]["required_bindings"][0][
        "environment_evidence"
    ][0]["constraints"] = list(alternative["constraints"])
    invalid_constraints["results"][0]["required_bindings"][0][
        "environment_evidence"
    ][0]["constraints_state"] = "typed"
    with pytest.raises(Exception, match="inconsistent"):
        validate_server_payload(invalid_constraints)


SAME_ENVIRONMENT_ADAPTATION_REASONS = (
    "exact_environment_conditions_require_review",
    "validated_source_requires_semantic_review",
)


def _same_environment_adaptation_payload(reason: str) -> dict:
    payload = copy.deepcopy(_payload())
    item = payload["results"][0]
    item["qualification_reason"] = reason
    alternative = item["environment_comparison"]["alternative_tuples"][0]
    alternative.update(
        {
            "environment": dict(payload["target_environment"]),
            "matched_dimensions": ["android_version", "platform", "project"],
            "different_dimensions": [],
        }
    )
    item["environment_comparison"]["non_dominated_matched_dimension_sets"] = [
        ["android_version", "platform", "project"]
    ]
    evidence = item["required_bindings"][0]["environment_evidence"][0]
    evidence.update(payload["target_environment"])
    if reason == "exact_environment_conditions_require_review":
        constraints = [{"kind": "product", "value": "tv"}]
        alternative["constraints"] = copy.deepcopy(constraints)
        alternative["constraints_evaluation"] = "not_evaluated"
        evidence["constraints"] = copy.deepcopy(constraints)
        evidence["constraints_state"] = "typed"
    item["evidence_gaps"] = [{"reason": reason, "binding_id": "", "context": {}}]
    return payload


@pytest.mark.parametrize("reason", SAME_ENVIRONMENT_ADAPTATION_REASONS)
def test_server_response_accepts_closed_same_environment_adaptation(reason: str) -> None:
    payload = _same_environment_adaptation_payload(reason)
    before = copy.deepcopy(payload)
    validate_server_payload(payload)
    assert payload == before
    result = normalize_server_results(payload)[0]
    assert result["reuse_grade"] == "adaptation_candidate"
    assert result["qualification_reason"] == reason
    assert result["requires_revalidation"] is True
    for field in ("required_bindings", "environment_comparison", "evidence_gaps"):
        assert result[field] == before["results"][0][field]


@pytest.mark.parametrize("reason", SAME_ENVIRONMENT_ADAPTATION_REASONS)
def test_same_environment_adaptation_compares_environment_case_insensitively(reason: str) -> None:
    payload = _same_environment_adaptation_payload(reason)
    alternative = payload["results"][0]["environment_comparison"]["alternative_tuples"][0]
    evidence = payload["results"][0]["required_bindings"][0]["environment_evidence"][0]
    alternative["environment"]["project"] = "tve8402m"
    alternative["environment"]["platform"] = "RK"
    evidence["project"] = "TvE8402m"
    evidence["platform"] = "Rk"
    validate_server_payload(payload)


def test_same_environment_fix_preserves_cross_environment_free_reason() -> None:
    payload = _payload()
    payload["results"][0]["qualification_reason"] = "existing free-text cross-environment reason"
    before = copy.deepcopy(payload)
    validate_server_payload(payload)
    assert payload == before


@pytest.mark.parametrize("reason", SAME_ENVIRONMENT_ADAPTATION_REASONS)
@pytest.mark.parametrize(
    "failure",
    [
        "unknown_reason", "swapped_reason", "constraints_evaluation", "comparison_mode",
        "no_alternative", "no_revalidation", "incomplete_target", "no_bindings",
        "open_binding", "unknown_layer", "wrong_role", "wrong_state", "no_acceptance_ref",
        "bad_patch_hash", "bad_manifest_hash", "bad_manifest_revision", "no_environment_evidence",
        "evidence_environment", "evidence_lineage", "evidence_constraints", "evidence_constraints_state",
        "evidence_validation_state", "evidence_content_hash",
    ],
)
def test_same_environment_adaptation_rejects_unclosed_or_unexplained_evidence(
    reason: str, failure: str,
) -> None:
    payload = _same_environment_adaptation_payload(reason)
    item = payload["results"][0]
    comparison = item["environment_comparison"]
    alternative = comparison["alternative_tuples"][0]
    binding = item["required_bindings"][0]
    evidence = binding["environment_evidence"][0]
    if failure == "unknown_reason":
        item["qualification_reason"] = "unknown_same_environment_reason"
    elif failure == "swapped_reason":
        item["qualification_reason"] = next(value for value in SAME_ENVIRONMENT_ADAPTATION_REASONS if value != reason)
    elif failure == "constraints_evaluation":
        alternative["constraints_evaluation"] = (
            "not_applicable" if alternative["constraints"] else "not_evaluated"
        )
    elif failure == "comparison_mode":
        comparison["mode"] = "not_qualified"
    elif failure == "no_alternative":
        comparison["alternative_tuples"] = []
        comparison["non_dominated_matched_dimension_sets"] = []
    elif failure == "no_revalidation":
        item["requires_revalidation"] = False
    elif failure == "incomplete_target":
        payload["target_environment"]["project"] = ""
    elif failure == "no_bindings":
        item["required_bindings"] = []
    elif failure.startswith("evidence_"):
        evidence_changes = {
            "evidence_environment": {"project": "OTHER"},
            "evidence_lineage": {"source_lineage_ref": "fixture:other"},
            "evidence_constraints": {"constraints": [{"kind": "product", "value": "other"}]},
            "evidence_constraints_state": {"constraints_state": "invalid"},
            "evidence_validation_state": {"validation_state": "unverified"},
            "evidence_content_hash": {"content_hash": "not-a-sha256"},
        }
        evidence.update(evidence_changes[failure])
    else:
        binding_changes = {
            "open_binding": {"closure_state": "invalid"},
            "unknown_layer": {"layer": ""},
            "wrong_role": {"binding_role": "verification_only"},
            "wrong_state": {"binding_state": "revoked"},
            "no_acceptance_ref": {"acceptance_ref": ""},
            "bad_patch_hash": {"patch_sha256": "bad"},
            "bad_manifest_hash": {"manifest_sha256": "bad"},
            "bad_manifest_revision": {"manifest_revision": 0},
            "no_environment_evidence": {"environment_evidence": []},
        }
        binding.update(binding_changes[failure])
    with pytest.raises(HttpClientFailure):
        validate_server_payload(payload)


@pytest.mark.parametrize("reason", SAME_ENVIRONMENT_ADAPTATION_REASONS)
@pytest.mark.parametrize("split_alternatives", [False, True])
def test_same_environment_adaptation_requires_one_tuple_covering_every_binding(
    reason: str, split_alternatives: bool,
) -> None:
    payload = _same_environment_adaptation_payload(reason)
    item = payload["results"][0]
    second_binding = copy.deepcopy(item["required_bindings"][0])
    second_binding.update({"binding_id": "binding-second", "patch_asset_id": "asset-second"})
    second_binding["environment_evidence"][0]["source_lineage_ref"] = "fixture:second-source"
    item["required_bindings"].append(second_binding)
    if split_alternatives:
        second_alternative = copy.deepcopy(item["environment_comparison"]["alternative_tuples"][0])
        second_alternative["source_lineage_ref"] = "fixture:second-source"
        item["environment_comparison"]["alternative_tuples"].append(second_alternative)
    with pytest.raises(HttpClientFailure, match="lacks one closed alternative tuple"):
        validate_server_payload(payload)


@pytest.mark.parametrize("reason", SAME_ENVIRONMENT_ADAPTATION_REASONS)
def test_same_environment_adaptation_allows_adapt_but_not_direct_reuse(reason: str) -> None:
    payload = _same_environment_adaptation_payload(reason)
    validate_server_payload(payload)
    result = normalize_server_results(payload)[0]
    before = copy.deepcopy(result)
    validate_reuse_decision(decision="adapt", targets=[result["id"]], results=[result])
    with pytest.raises(SystemExit, match="direct_reuse_candidate"):
        validate_reuse_decision(decision="reuse", targets=[result["id"]], results=[result])
    assert result == before


def test_server_response_must_match_request_environment_and_pagination() -> None:
    args = SimpleNamespace(
        type="all",
        project="TVE8402M",
        platform="rk",
        android_version="14",
        component_layer=["platform"],
        offset=0,
        limit=8,
    )
    validate_server_payload(_payload(), args=args, query="display power")
    stale = _payload()
    stale["target_environment"] = {
        **stale["target_environment"],
        "project": "OTHER",
    }
    with pytest.raises(Exception, match="does not match request"):
        validate_server_payload(stale, args=args, query="display power")

    uppercase_platform = SimpleNamespace(**{**vars(args), "platform": "RK"})
    validate_server_payload(
        _payload(), args=uppercase_platform, query="display power"
    )


@pytest.mark.parametrize("empty", [False, True])
def test_server_without_layer_filter_matches_unfiltered_request(empty: bool) -> None:
    payload = _payload(empty=empty)
    payload["filters"]["component"]["component_layer"] = []
    args = SimpleNamespace(
        type="all", project="TVE8402M", platform="rk", android_version="14",
        component_layer=[], offset=0, limit=8,
    )
    validate_server_payload(payload, args=args, query="display power")


def test_server_missing_layer_filter_array_remains_a_contract_error() -> None:
    payload = _payload()
    payload["filters"]["component"] = {}
    with pytest.raises(HttpClientFailure, match="component filters are invalid"):
        validate_server_payload(payload)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload["results"][0].pop("qualification_reason"),
        lambda payload: payload["results"][0]["layers"].append("vendor"),
        lambda payload: payload["results"][0]["required_bindings"][0].update(
            {"patch_sha256": "bad"}
        ),
        lambda payload: payload["results"][0]["required_bindings"][0].update(
            {"closure_state": "open"}
        ),
        lambda payload: payload["results"][0]["required_bindings"][0].update(
            {"binding_role": "verification_only"}
        ),
        lambda payload: payload["results"][0]["required_bindings"][0].update(
            {"binding_state": "revoked"}
        ),
        lambda payload: payload["results"][0]["required_bindings"][0].update(
            {"acceptance_ref": ""}
        ),
        lambda payload: payload["results"][0].update(
            {"reuse_grade": "direct_reuse_candidate", "requires_revalidation": False}
        )
        or payload["results"][0]["required_bindings"][0].update(
            {"closure_state": "invalid"}
        ),
        lambda payload: payload["results"][0].update(
            {"requires_revalidation": False}
        ),
        lambda payload: payload["results"][0]["environment_comparison"].update(
            {"alternative_tuples": []}
        ),
        lambda payload: payload["results"][0]["required_bindings"][0].update(
            {"environment_evidence": []}
        ),
        lambda payload: payload["results"][0]["required_bindings"][0][
            "environment_evidence"
        ][0].update({"project": "OTHER"}),
        lambda payload: payload["target_environment"].update({"project": ""}),
        lambda payload: payload["results"][0]["environment_comparison"][
            "alternative_tuples"
        ][0].update({"constraints_evaluation": "unsatisfied"}),
        lambda payload: payload["results"][0]["environment_comparison"][
            "alternative_tuples"
        ][0].update({"source_lineage_ref": ""})
        or payload["results"][0]["required_bindings"][0][
            "environment_evidence"
        ][0].update({"source_lineage_ref": ""}),
        lambda payload: payload["results"][0]["environment_comparison"][
            "alternative_tuples"
        ][0].update(
            {
                "matched_dimensions": ["android_version", "platform", "project"],
                "different_dimensions": [],
            }
        ),
        lambda payload: payload["results"][0]["environment_comparison"].update(
            {"non_dominated_matched_dimension_sets": [["project"]]}
        ),
        lambda payload: payload["results"][0].update({"layers": ["hal"]}),
        lambda payload: payload["filters"]["component"].update(
            {"component_layer": ["device"]}
        ),
        lambda payload: payload["results"][0]["required_bindings"][0].update(
            {"layer": ""}
        ),
        lambda payload: payload["results"][0]["evidence_gaps"][0].pop("context"),
        lambda payload: payload["results"][0].update({"variant_id": "legacy"}),
        lambda payload: payload["results"][0].update({"reuse_score": 99}),
        lambda payload: payload["results"][0]["required_bindings"][0].pop(
            "manifest_revision"
        ),
        lambda payload: payload.update({"result_type": "case"}),
        lambda payload: payload.update({"result_state": "empty_for_this_query"}),
        lambda payload: payload["pagination"].update({"has_more": True}),
    ],
)
def test_server_response_rejects_malformed_or_inconsistent_reuse_evidence(
    mutate,
) -> None:
    payload = copy.deepcopy(_payload())
    mutate(payload)
    with pytest.raises(Exception):
        validate_server_payload(payload)


@pytest.mark.parametrize("path,value", [
    (("results", 0, "case_id"), True),
    (("results", 0, "implementation_id"), {"id": "implementation-display-rk14"}),
    (("results", 0, "required_bindings", 0, "binding_id"), 123),
    (("results", 0, "required_bindings", 0, "acceptance_ref"), ["acceptance-display"]),
    (("results", 0, "environment_comparison", "alternative_tuples", 0, "environment", "android_version"), 14),
    (("results", 0, "reuse_grade"), []),
    (("results", 0, "layers"), [{}]),
    (("filters", "component", "component_layer"), [["platform"]]),
    (("result_state",), {}),
])
def test_dto_identity_and_environment_types_fail_as_contract_errors(path, value) -> None:
    payload = _payload()
    parent = payload
    for key in path[:-1]:
        parent = parent[key]
    parent[path[-1]] = value
    with pytest.raises(HttpClientFailure) as captured:
        validate_server_payload(payload)
    assert captured.value.result.envelope_valid is False


def test_server_request_sends_the_v2_contract_header(monkeypatch) -> None:
    monkeypatch.setattr(search_api, "require_member_alias", lambda: "wick")
    monkeypatch.setattr(
        search_api,
        "member_search_endpoint_url",
        lambda: ("https://akbs.invalid/member/knowledge-search", "fixture"),
    )
    observed = {}

    def request_json(request, *, timeout):
        observed["contract"] = {
            key.casefold(): value for key, value in request.header_items()
        }.get(SEARCH_CONTRACT_HEADER.casefold())
        observed["timeout"] = timeout
        return _payload()

    monkeypatch.setattr(search_api, "request_json", request_json)
    args = SimpleNamespace(
        limit=8,
        offset=0,
        type="all",
        project="TVE8402M",
        platform="RK",
        android_version="14",
        component_layer=["platform"],
        server_timeout=3.0,
    )
    fetch_server_results(args, "display power")
    assert observed == {"contract": SEARCH_RESPONSE_SCHEMA, "timeout": 3.0}


def test_reuse_decision_must_bind_selected_result_and_server_grade() -> None:
    result = normalize_server_results(_payload())[0]
    validate_reuse_decision(
        decision="adapt",
        targets=["implementation-display-rk14"],
        results=[result],
    )
    with pytest.raises(SystemExit, match="direct_reuse_candidate"):
        validate_reuse_decision(
            decision="reuse",
            targets=["implementation-display-rk14"],
            results=[result],
        )
    with pytest.raises(SystemExit, match="not present"):
        validate_reuse_decision(
            decision="adapt",
            targets=["other"],
            results=[result],
        )
    case_result = {
        **result,
        "kind": "case",
        "type": "case",
        "id": "case-display",
    }
    with pytest.raises(SystemExit, match="must bind an implementation"):
        validate_reuse_decision(
            decision="adapt",
            targets=["case-display"],
            results=[case_result],
        )
    invalid_binding = copy.deepcopy(result)
    invalid_binding["required_bindings"][0]["binding_state"] = "revoked"
    with pytest.raises(SystemExit, match="closed accepted implementation evidence"):
        validate_reuse_decision(
            decision="adapt",
            targets=["implementation-display-rk14"],
            results=[invalid_binding],
        )


def test_reference_only_can_use_incomplete_case_or_local_hint_but_not_unknown_target() -> None:
    hint = {"kind": "case", "case_id": "case-local", "id": "case-local", "source": "local_jsonl_fallback"}
    validate_reuse_decision(decision="reference_only", targets=["case-local"], results=[hint])
    with pytest.raises(SystemExit, match="not present"):
        validate_reuse_decision(decision="reference_only", targets=["case-other"], results=[hint])
    with pytest.raises(SystemExit, match="must bind an implementation"):
        validate_reuse_decision(decision="reuse", targets=["case-local"], results=[hint])


def test_adapt_can_be_more_conservative_than_direct_server_grade() -> None:
    result = normalize_server_results(_payload())[0]
    result["reuse_grade"] = "direct_reuse_candidate"
    result["requires_revalidation"] = False
    before = copy.deepcopy(result)
    validate_reuse_decision(decision="adapt", targets=[result["id"]], results=[result])
    assert result == before


def test_multi_query_keeps_distinct_patch_and_symbol_results() -> None:
    patch_rows = [
        {
            "kind": "patch",
            "case_id": "case-a",
            "implementation_id": "implementation-a",
            "evidence_binding_id": binding,
        }
        for binding in ("binding-a", "binding-b")
    ]
    symbol_rows = [
        {
            "kind": "symbol",
            "case_id": "case-a",
            "implementation_id": "implementation-a",
            "evidence_binding_id": "binding-a",
            "symbol": symbol,
        }
        for symbol in ("DisplayPowerController", "setWakeLock")
    ]
    combined = combine_results([("display", patch_rows + symbol_rows)])
    assert [
        item["evidence_binding_id"]
        for item in combined
        if item["kind"] == "patch"
    ] == ["binding-a", "binding-b"]
    assert [item["symbol"] for item in combined if item["kind"] == "symbol"] == [
        "DisplayPowerController",
        "setWakeLock",
    ]


def test_not_found_requires_complete_multi_query_server_evidence() -> None:
    first = _payload(empty=True)
    second = {**_payload(empty=True), "query": "DisplayPowerController"}
    assert healthy_multi_query_empty(
        source="server_api",
        queries=["display power", "DisplayPowerController"],
        payloads=[first, second],
        results=[],
    )
    assert not healthy_multi_query_empty(
        source="server_api",
        queries=["display power"],
        payloads=[first],
        results=[],
    )
    partial = {**second, "completeness": "partial", "result_state": "indeterminate"}
    assert not healthy_multi_query_empty(
        source="server_api",
        queries=["display power", "DisplayPowerController"],
        payloads=[first, partial],
        results=[],
    )


def test_reuse_decisions_require_complete_server_search_but_allow_more_pages() -> None:
    payload = _payload()
    assert healthy_server_search(
        source="server_api",
        queries=["display power"],
        payloads=[payload],
    )
    partial = {**payload, "completeness": "partial"}
    assert not healthy_server_search(
        source="server_api",
        queries=["display power"],
        payloads=[partial],
    )
    paginated = {
        **payload,
        "pagination": {**payload["pagination"], "has_more": True},
    }
    assert healthy_server_search(
        source="server_api",
        queries=["display power"],
        payloads=[paginated],
    )


def test_search_json_returns_immutable_receipt_path_and_sha(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    payload = _payload()
    rows = normalize_server_results(payload)
    monkeypatch.setattr(search_cli, "should_try_server", lambda _args: True)
    monkeypatch.setattr(
        search_cli, "fetch_server_results", lambda _args, _query: (rows, payload)
    )
    monkeypatch.setattr(search_cli, "search_usage_root", lambda: tmp_path / "usage")
    monkeypatch.setattr(
        search_cli, "selected_member_alias", lambda: ("default", "wick")
    )
    assert search_cli.main(
        [
            "display power",
            "--json",
            "--project",
            "TVE8402M",
            "--platform",
            "RK",
            "--android-version",
            "14",
            "--component-layer",
            "platform",
            "--reuse-decision",
            "adapt",
            "--reuse-target",
            "implementation-display-rk14",
        ]
    ) == 0
    output = json.loads(capsys.readouterr().out)
    receipt = Path(output["usage_receipt"])
    assert receipt.is_file()
    assert output["usage_receipt_sha256"] == hashlib.sha256(
        receipt.read_bytes()
    ).hexdigest()
    recorded = json.loads(receipt.read_text(encoding="utf-8"))
    assert recorded["target_environment"]["platform"] == "rk"
    assert recorded["results"][0]["evidence_gaps"][0]["binding_id"] == "binding-display"


def test_local_reference_receipt_does_not_claim_server_validation(monkeypatch, tmp_path: Path, capsys) -> None:
    rows = [{"kind": "case", "case_id": "case-local", "title": "display power hint"}]
    monkeypatch.setattr(search_cli, "find_root", lambda _value: tmp_path)
    monkeypatch.setattr(search_cli, "load_rows", lambda *_args, **_kwargs: rows)
    monkeypatch.setattr(search_cli, "search_usage_root", lambda: tmp_path / "usage")
    monkeypatch.setattr(search_cli, "selected_member_alias", lambda: ("default", "wick"))
    assert search_cli.main(["display power", "--source", "local", "--json", "--reuse-decision", "reference_only", "--reuse-target", "case-local"]) == 0
    output = json.loads(capsys.readouterr().out)
    recorded = json.loads(Path(output["usage_receipt"]).read_text(encoding="utf-8"))
    assert recorded["source"] == "local_jsonl_fallback"
    assert recorded["decision"] == "reference_only"
    assert recorded["server_search_health"] == []
    assert recorded["reuse_grades"] == []
    assert "required_bindings" not in recorded["results"][0]


@pytest.mark.parametrize("mode", ["invalid_target", "single_empty", "partial_empty"])
def test_no_record_usage_still_checks_explicit_decision(monkeypatch, mode: str) -> None:
    payload = _payload(empty=mode != "invalid_target")
    if mode == "partial_empty":
        payload.update(completeness="partial", result_state="indeterminate")
    monkeypatch.setattr(search_cli, "should_try_server", lambda _args: True)
    monkeypatch.setattr(search_cli, "fetch_server_results",
                        lambda _args, query: (normalize_server_results(payload), {**payload, "query": query}))
    def forbidden_profile():
        raise AssertionError("no-record validation must not load a member profile")
    monkeypatch.setattr(search_cli, "selected_member_alias", forbidden_profile)
    argv = ["display power", "--json", "--no-record-usage", "--reuse-decision"]
    if mode == "invalid_target":
        argv += ["adapt", "--reuse-target", "implementation-not-in-results"]
    else:
        argv += ["not_found"]
        if mode == "partial_empty":
            argv += ["--additional-query", "DisplayPowerController"]
    with pytest.raises(SystemExit):
        search_cli.main(argv)


def test_no_record_usage_valid_decision_checks_without_profile_or_file(monkeypatch, capsys) -> None:
    payload = _payload()
    monkeypatch.setattr(search_cli, "should_try_server", lambda _args: True)
    monkeypatch.setattr(search_cli, "fetch_server_results",
                        lambda _args, _query: (normalize_server_results(payload), payload))
    def forbidden_profile():
        raise AssertionError("no-record search must not load a profile or create an output path")
    monkeypatch.setattr(search_cli, "selected_member_alias", forbidden_profile)
    monkeypatch.setattr(search_cli, "search_usage_root", forbidden_profile)
    assert search_cli.main(["display power", "--json", "--no-record-usage",
                            "--reuse-decision", "adapt", "--reuse-target",
                            "implementation-display-rk14"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert not output.get("usage_receipt")


def test_local_variant_reader_exposes_implementation_without_reuse_grade(
    tmp_path: Path,
) -> None:
    index = tmp_path / "index"
    index.mkdir()
    (index / "variant-index.jsonl").write_text(
        json.dumps(
            {
                "type": "variant",
                "variant_id": "variant-legacy-rk14",
                "case_id": "case-display",
                "implementation_scope": "legacy local text record",
                "reuse_grade": "reusable",
                "qualification_reason": "legacy guess",
                "required_bindings": [{"binding_id": "legacy"}],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    rows = load_rows(tmp_path)
    assert rows == [
        {
            "case_id": "case-display",
            "implementation_scope": "legacy local text record",
            "kind": "implementation",
            "type": "implementation",
            "id": "variant-legacy-rk14",
            "implementation_id": "variant-legacy-rk14",
        }
    ]
    assert "reuse_grade" not in rows[0]
    assert "variant_id" not in rows[0]
    assert "qualification_reason" not in rows[0]
    assert "required_bindings" not in rows[0]
    assert rows[0]["type"] == "implementation"


def test_local_rows_strip_reuse_authority_but_preserve_source_annotations(
    tmp_path: Path,
) -> None:
    index = tmp_path / "index"
    patch_dir = tmp_path / "patches/by-id/patch-legacy"
    index.mkdir(parents=True)
    patch_dir.mkdir(parents=True)
    qualification = {
        "reuse_grade": "direct_reuse_candidate",
        "qualification_reason": "legacy",
        "requires_revalidation": False,
        "required_bindings": [{"binding_id": "legacy"}],
        "evidence_gaps": [],
        "environment_comparison": {"mode": "exact"},
        "target_environment": {"project": "OLD"},
        "knowledge_validity": {"reuse_score": 99, "evidence_level": "production"},
        "confidence": "high",
        "case_confidence": "high",
        "evidence_level": "production",
        "risk_level": "low",
        "reuse_score": 99,
        "reuse_hint": True,
    }
    (index / "case-index.jsonl").write_text(
        json.dumps({"case_id": "case-legacy", "title": "legacy", **qualification}) + "\n",
        encoding="utf-8",
    )
    (index / "variant-index.jsonl").write_text("", encoding="utf-8")
    (index / "evidence-index.jsonl").write_text("", encoding="utf-8")
    (index / "symbol-index.jsonl").write_text(
        json.dumps(
            {
                "symbol_id": "symbol-legacy",
                "case_id": "case-legacy",
                "variant_id": "variant-symbol-legacy",
                "patch_id": "patch-legacy",
                "value": "LegacySymbol",
                **qualification,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (patch_dir / "patch.json").write_text(
        json.dumps(
            {
                "patch_id": "patch-legacy",
                "case_id": "case-legacy",
                "variant_ids": ["variant-patch-legacy"],
                "summary": "legacy patch",
                **qualification,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    rows = load_rows(tmp_path)
    assert {row["kind"] for row in rows} == {"case", "patch", "symbol"}
    for row in rows:
        assert not {"reuse_grade", "qualification_reason", "requires_revalidation", "required_bindings", "evidence_gaps", "environment_comparison", "target_environment", "reuse_score", "reuse_hint"} & set(row)
        assert row["knowledge_validity"] == {"evidence_level": "production"}
        assert row["confidence"] == "high"
        assert row["evidence_level"] == "production"
        assert "variant_id" not in row
        assert "variant_ids" not in row
    patch = next(row for row in rows if row["kind"] == "patch")
    symbol = next(row for row in rows if row["kind"] == "symbol")
    assert patch["implementation_ids"] == ["variant-patch-legacy"]
    assert symbol["implementation_id"] == "variant-symbol-legacy"
    local_text = format_markdown(
        tmp_path,
        "legacy",
        rows,
        None,
        source="local_jsonl_fallback",
        fallback_reason="test",
    )
    assert "来源原有标注（非本次服务端分级）" in local_text
    assert "reuse_score" not in local_text
    assert "reuse_hint" not in local_text


def test_local_default_keeps_ai_evidence_hints_and_excludes_archive_sources() -> None:
    rows = [
        {"kind": "case", "case_id": "case-display", "title": "display power"},
        {
            "kind": "evidence",
            "evidence_kind": "verification_result",
            "id": "evidence-display",
            "summary": "display power verified",
        },
        {"kind": "evidence", "evidence_kind": "source", "id": "source-private", "summary": "display power original source"},
    ]
    results = search(rows, "display power", "all", 10, False)
    assert {row.get("id") or row.get("case_id") for row in results} == {"case-display", "evidence-display"}


def test_human_output_keeps_projection_and_local_text_warning() -> None:
    server_text = format_markdown(
        None,
        "display power",
        normalize_server_results(_payload()),
        None,
        source="server_api",
        search_mode="two_stage_structured_lexical",
        server_payloads=[_payload()],
    )
    assert "projection_ready=True" in server_text
    assert "target=TVE8402M/rk/14" in server_text
    assert "projection_reason=(none)" in server_text
    assert "需适配候选" in server_text
    assert "必需证据绑定: 1" in server_text
    assert "binding=binding-display" in server_text
    assert "asset=asset-display" in server_text
    assert f"sha256={'a' * 64}" in server_text
    assert "closure_reason=reviewed" in server_text
    assert "role=implementation" in server_text
    assert "state=accepted" in server_text
    assert "revision=1" in server_text
    assert f"manifest_sha256={'b' * 64}" in server_text
    assert "证据缺口: 1" in server_text
    assert "Android 层级: platform" in server_text
    assert "已匹配环境维度: android_version+platform" in server_text

    local_text = format_markdown(
        tmp_path := Path("/tmp/local-knowledge"),
        "display power",
        [],
        None,
        source="local_jsonl_fallback",
        fallback_reason="explicit local text search",
    )
    assert str(tmp_path) in local_text
    assert "本地文本搜索，未经过服务端复用分级" in local_text

    partial_payload = _payload(empty=True)
    partial_payload.update(
        {"result_state": "indeterminate", "completeness": "partial"}
    )
    partial_payload["projection"] = {
        **partial_payload["projection"],
        "ready": False,
        "complete": False,
        "reason_code": "projection_incomplete",
    }
    partial_text = format_markdown(
        None,
        "display power",
        [],
        None,
        source="server_api",
        search_mode="two_stage_structured_lexical",
        server_payloads=[partial_payload],
    )
    assert "当前无法确定" in partial_text
    assert "不得记为 not_found" in partial_text


def test_incomparable_environment_alternatives_keep_both_without_global_priority() -> None:
    payload = _payload()
    item = payload["results"][0]
    alternative = {
        "environment": {"project": "TVE8402M", "platform": "mtk", "android_version": "14"},
        "matched_dimensions": ["android_version", "project"],
        "different_dimensions": ["platform"],
        "source_lineage_ref": "fixture:reviewed-mtk",
        "constraints": [],
        "constraints_evaluation": "not_applicable",
    }
    item["environment_comparison"]["alternative_tuples"].append(alternative)
    item["environment_comparison"]["non_dominated_matched_dimension_sets"].append(["android_version", "project"])
    item["required_bindings"][0]["environment_evidence"].append({
        **alternative["environment"], "source_lineage_ref": alternative["source_lineage_ref"],
        "constraints": [], "constraints_state": "known_empty", "validation_state": "validated", "content_hash": "f" * 64,
    })
    validate_server_payload(payload)
    assert len(normalize_server_results(payload)[0]["environment_comparison"]["non_dominated_matched_dimension_sets"]) == 2


def test_usage_json_creation_is_immutable_and_rejects_dangling_symlink(tmp_path: Path) -> None:
    destination = tmp_path / "usage.json"
    digest = write_json_once(destination, {"decision": "reference_only"})
    before = destination.read_bytes()
    assert hashlib.sha256(before).hexdigest() == digest
    with pytest.raises(FileExistsError):
        write_json_once(destination, {"decision": "reuse"})
    assert destination.read_bytes() == before
    target = tmp_path / "not-created.json"
    symlink = tmp_path / "linked-usage.json"
    symlink.symlink_to(target)
    with pytest.raises(FileExistsError):
        write_json_once(symlink, {"decision": "reference_only"})
    assert symlink.is_symlink()
    assert not target.exists()
