from __future__ import annotations

import re
import urllib.parse
import urllib.request
from typing import Any

from akbs_member_ops.knowledge.member import require_member_alias
from akbs_member_ops.knowledge.merge_confirmation.client import (
    fetch_merge_confirmation_payload,
    member_request_headers,
    merge_api_error,
    post_merge_dispute,
)
from akbs_member_ops.http_client import (
    failure_result,
    invalid_success_response,
    request_json,
)

from akbs_member_ops.knowledge_search.config import (
    akbs_endpoint_env_value,
    member_api_base_url,
    member_search_endpoint_url,
)


SEARCH_RESPONSE_SCHEMA = "akbs-member-knowledge-search-v2"
SEARCH_CONTRACT_HEADER = "X-AKBS-Member-Search-Contract"
SEARCH_MODE = "two_stage_structured_lexical"
RESULT_TYPES = frozenset({"case", "implementation", "patch", "symbol"})
REUSE_GRADES = frozenset(
    {"direct_reuse_candidate", "adaptation_candidate", "reference_only"}
)
RESULT_STATES = frozenset({"matches", "empty_for_this_query", "indeterminate"})
COMPLETENESS_STATES = frozenset({"complete", "partial", "indeterminate"})
ANDROID_LAYERS = frozenset(
    {"application", "platform", "native", "hal", "kernel", "device", "build"}
)


def _same_environment(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return all(
        str(left.get(key) or "").casefold()
        == str(right.get(key) or "").casefold()
        for key in ("project", "platform", "android_version")
    )


def _alternative_covers_bindings(
    alternative: dict[str, Any], bindings: list[dict[str, Any]]
) -> bool:
    environment = alternative["environment"]
    return all(
        any(
            _same_environment(evidence, environment)
            and evidence["source_lineage_ref"] == alternative["source_lineage_ref"]
            and evidence["constraints"] == alternative["constraints"]
            and evidence["constraints_state"] in {"known_empty", "typed"}
            and evidence["validation_state"] == "validated"
            and bool(re.fullmatch(r"[0-9a-f]{64}", evidence["content_hash"]))
            for evidence in binding["environment_evidence"]
        )
        for binding in bindings
    )
TOP_LEVEL_FIELDS = frozenset(
    {
        "schema",
        "search_mode",
        "query",
        "result_type",
        "target_environment",
        "result_state",
        "completeness",
        "reason_code",
        "results",
        "filters",
        "pagination",
        "projection",
        "not_found",
    }
)
RESULT_FIELDS = frozenset(
    {
        "type",
        "case_id",
        "implementation_id",
        "evidence_binding_id",
        "patch_package_id",
        "patch_asset_id",
        "patch_id",
        "symbol",
        "title",
        "summary",
        "relative_path",
        "display_name",
        "patch_sha256",
        "layer",
        "repository",
        "size_bytes",
        "reuse_grade",
        "qualification_reason",
        "requires_revalidation",
        "layers",
        "required_bindings",
        "evidence_gaps",
        "environment_comparison",
        "matched_channels",
        "search_anchors",
    }
)
BINDING_FIELDS = frozenset(
    {
        "binding_id",
        "patch_package_id",
        "patch_asset_id",
        "patch_sha256",
        "binding_role",
        "binding_state",
        "acceptance_ref",
        "scope",
        "verification_refs",
        "relative_path",
        "display_name",
        "size_bytes",
        "layer",
        "repository",
        "repository_source",
        "closure_state",
        "closure_reason",
        "manifest_revision",
        "manifest_sha256",
        "environment_evidence",
    }
)
ENVIRONMENT_EVIDENCE_FIELDS = frozenset(
    {
        "project",
        "platform",
        "android_version",
        "source_lineage_ref",
        "constraints",
        "constraints_state",
        "validation_state",
        "content_hash",
    }
)
ALTERNATIVE_TUPLE_FIELDS = frozenset(
    {
        "environment",
        "matched_dimensions",
        "different_dimensions",
        "source_lineage_ref",
        "constraints",
        "constraints_evaluation",
    }
)
ENVIRONMENT_FIELDS = frozenset({"project", "platform", "android_version"})


def should_try_server(args: Any) -> bool:
    if args.source == "local":
        return False
    if args.source == "server":
        return True
    if akbs_endpoint_env_value("MEMBER_SEARCH_URL"):
        return True
    return not bool(args.root)


def server_search_url(args: Any, query: str) -> str:
    endpoint, _source = member_search_endpoint_url()
    separator = "&" if "?" in endpoint else "?"
    params: list[tuple[str, str]] = [
        ("q", query),
        ("limit", str(max(args.limit, 1))),
        ("offset", str(max(args.offset, 0))),
    ]
    if args.type and args.type != "all":
        params.append(("type", args.type))
    for key in ("project", "platform", "android_version"):
        value = str(getattr(args, key, "") or "").strip()
        if value:
            params.append((key, value))
    for layer in getattr(args, "component_layer", []) or []:
        params.append(("component_layer", str(layer)))
    return endpoint + separator + urllib.parse.urlencode(params)


def _require_object(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise invalid_success_response(f"server search {key} must be an object")
    return value


def _has_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _in_enum(value: Any, values: frozenset[str] | set[str]) -> bool:
    return isinstance(value, str) and value in values


def _is_sha256(value: Any, *, allow_empty: bool = False) -> bool:
    return isinstance(value, str) and bool(
        re.fullmatch(r"(?:[0-9a-f]{64})?" if allow_empty else r"[0-9a-f]{64}", value)
    )


def validate_server_payload(
    payload: dict[str, Any],
    *,
    args: Any | None = None,
    query: str | None = None,
) -> None:
    if set(payload) != TOP_LEVEL_FIELDS:
        missing = sorted(TOP_LEVEL_FIELDS - set(payload))
        extra = sorted(set(payload) - TOP_LEVEL_FIELDS)
        raise invalid_success_response(
            "server search response fields mismatch"
            f" missing={missing} extra={extra}"
        )
    if payload.get("schema") != SEARCH_RESPONSE_SCHEMA:
        raise invalid_success_response("server search response schema mismatch")
    if payload.get("search_mode") != SEARCH_MODE:
        raise invalid_success_response("server search mode mismatch")
    if not _has_text(payload.get("query")):
        raise invalid_success_response("server search query is invalid")
    if not _in_enum(payload.get("result_state"), RESULT_STATES):
        raise invalid_success_response("server search result_state is invalid")
    if not _in_enum(payload.get("completeness"), COMPLETENESS_STATES):
        raise invalid_success_response("server search completeness is invalid")
    if not isinstance(payload.get("reason_code"), str):
        raise invalid_success_response("server search reason_code is invalid")
    if payload.get("not_found") is not False:
        raise invalid_success_response("server must not decide member not_found usage")
    if not _in_enum(payload.get("result_type"), RESULT_TYPES | {"all"}):
        raise invalid_success_response("server search result_type is invalid")

    target = _require_object(payload, "target_environment")
    if set(target) != {"project", "platform", "android_version"} or any(
        not isinstance(target.get(key), str)
        for key in ("project", "platform", "android_version")
    ):
        raise invalid_success_response("server search target environment is invalid")
    filters = _require_object(payload, "filters")
    if set(filters) != {"component"}:
        raise invalid_success_response("server search filters are invalid")
    component = filters.get("component")
    if not isinstance(component, dict) or not isinstance(
        component.get("component_layer", []), list
    ) or set(component) != {"component_layer"} or any(
        not _in_enum(value, ANDROID_LAYERS) for value in component.get("component_layer", [])
    ) or len(component.get("component_layer", [])) != len(
        set(component.get("component_layer", []))
    ):
        raise invalid_success_response("server search component filters are invalid")
    pagination = _require_object(payload, "pagination")
    if (
        set(pagination) != {"offset", "limit", "total", "has_more"}
        or any(type(pagination.get(key)) is not int for key in ("offset", "limit", "total"))
        or not isinstance(pagination.get("has_more"), bool)
        or pagination["offset"] < 0
        or pagination["limit"] < 1
        or pagination["total"] < 0
    ):
        raise invalid_success_response("server search pagination is invalid")
    projection = _require_object(payload, "projection")
    if (
        not isinstance(projection.get("ready"), bool)
        or not isinstance(projection.get("complete"), bool)
        or not isinstance(projection.get("reason_code"), str)
        or any(
            type(projection.get(key)) is not int or projection[key] < 0
            for key in ("generation", "eligible_count", "indexed_count", "isolated_count")
        )
    ):
        raise invalid_success_response("server search projection is invalid")

    results = payload.get("results")
    if not isinstance(results, list):
        raise invalid_success_response("server search results must be an array")
    for item in results:
        if not isinstance(item, dict):
            raise invalid_success_response("server search result must be an object")
        if not set(item) <= RESULT_FIELDS:
            raise invalid_success_response("server search result fields are invalid")
        if not _in_enum(item.get("type"), RESULT_TYPES):
            raise invalid_success_response("server search result type is invalid")
        if (
            payload["result_type"] != "all"
            and item.get("type") != payload["result_type"]
        ):
            raise invalid_success_response(
                "server search result type does not match result_type filter"
            )
        if not _has_text(item.get("case_id")):
            raise invalid_success_response("server search result case_id is missing")
        if not _in_enum(item.get("reuse_grade"), REUSE_GRADES):
            raise invalid_success_response("server search reuse grade is invalid")
        if not isinstance(item.get("qualification_reason"), str):
            raise invalid_success_response("server search qualification reason is invalid")
        if not isinstance(item.get("requires_revalidation"), bool):
            raise invalid_success_response("server search revalidation flag is invalid")
        comparison = item.get("environment_comparison")
        if (
            not isinstance(comparison, dict)
            or set(comparison)
            != {
                "mode",
                "alternative_tuples",
                "non_dominated_matched_dimension_sets",
            }
            or not _in_enum(comparison.get("mode"), {"exact", "adaptation", "not_qualified"})
            or not isinstance(comparison.get("alternative_tuples"), list)
            or not isinstance(
                comparison.get("non_dominated_matched_dimension_sets"), list
            )
        ):
            raise invalid_success_response("server search environment comparison is invalid")
        for alternative in comparison["alternative_tuples"]:
            environment = (
                alternative.get("environment")
                if isinstance(alternative, dict)
                else None
            )
            if (
                not isinstance(alternative, dict)
                or set(alternative) != ALTERNATIVE_TUPLE_FIELDS
                or not isinstance(environment, dict)
                or set(environment) != ENVIRONMENT_FIELDS
                or any(not _has_text(environment.get(key)) for key in ENVIRONMENT_FIELDS)
                or not isinstance(alternative.get("matched_dimensions"), list)
                or not isinstance(alternative.get("different_dimensions"), list)
                or any(
                    not _in_enum(dimension, ENVIRONMENT_FIELDS)
                    for dimension in [
                        *alternative["matched_dimensions"],
                        *alternative["different_dimensions"],
                    ]
                )
                or not _has_text(alternative.get("source_lineage_ref"))
                or not isinstance(alternative.get("constraints"), list)
                or not _in_enum(alternative.get("constraints_evaluation"), {"not_applicable", "not_evaluated"})
            ):
                raise invalid_success_response(
                    "server search alternative environment tuple is invalid"
                )
            expected_matched = sorted(
                dimension
                for dimension in ENVIRONMENT_FIELDS
                if str(environment[dimension]).casefold()
                == str(payload["target_environment"][dimension]).casefold()
            )
            expected_different = sorted(set(ENVIRONMENT_FIELDS) - set(expected_matched))
            if (
                sorted(alternative["matched_dimensions"]) != expected_matched
                or sorted(alternative["different_dimensions"]) != expected_different
                or (
                    alternative["constraints"]
                    and comparison["mode"] == "adaptation"
                    and alternative["constraints_evaluation"] != "not_evaluated"
                )
                or (
                    alternative["constraints"]
                    and comparison["mode"] == "exact"
                )
                or (
                    not alternative["constraints"]
                    and alternative["constraints_evaluation"] != "not_applicable"
                )
            ):
                raise invalid_success_response(
                    "server search alternative environment comparison is inconsistent"
                )
        for dimensions in comparison["non_dominated_matched_dimension_sets"]:
            if (
                not isinstance(dimensions, list)
                or any(not _in_enum(dimension, ENVIRONMENT_FIELDS) for dimension in dimensions)
                or len(dimensions) != len(set(dimensions))
            ):
                raise invalid_success_response(
                    "server search matched environment dimensions are invalid"
                )
        matched_sets = {
            frozenset(alternative["matched_dimensions"])
            for alternative in comparison["alternative_tuples"]
        }
        expected_front = {
            tuple(sorted(candidate))
            for candidate in matched_sets
            if not any(candidate < other for other in matched_sets)
        }
        actual_front = {
            tuple(sorted(dimensions))
            for dimensions in comparison["non_dominated_matched_dimension_sets"]
        }
        if actual_front != expected_front:
            raise invalid_success_response(
                "server search environment Pareto front is inconsistent"
            )
        bindings = item.get("required_bindings")
        if not isinstance(bindings, list):
            raise invalid_success_response("server search required bindings are invalid")
        for binding in bindings:
            if (
                not isinstance(binding, dict)
                or set(binding) != BINDING_FIELDS
                or not _has_text(binding.get("binding_id"))
                or not _has_text(binding.get("patch_package_id"))
                or not _has_text(binding.get("patch_asset_id"))
                or not _is_sha256(binding.get("patch_sha256"))
                or not _in_enum(binding.get("layer"), ANDROID_LAYERS | {""})
                or not isinstance(binding.get("repository"), str)
                or not _in_enum(binding.get("closure_state"), {"closed", "invalid"})
                or not isinstance(binding.get("closure_reason"), str)
                or binding.get("binding_role") != "implementation"
                or binding.get("binding_state") != "accepted"
                or not _has_text(binding.get("acceptance_ref"))
                or not isinstance(binding.get("scope"), dict)
                or not isinstance(binding.get("verification_refs"), list)
                or not isinstance(binding.get("relative_path"), str)
                or not isinstance(binding.get("display_name"), str)
                or type(binding.get("size_bytes")) is not int
                or binding["size_bytes"] < 0
                or not isinstance(binding.get("repository_source"), str)
                or type(binding.get("manifest_revision")) is not int
                or binding["manifest_revision"] < 0
                or not _is_sha256(binding.get("manifest_sha256"), allow_empty=True)
                or (
                    binding.get("closure_state") == "closed"
                    and (
                        binding["manifest_revision"] < 1
                        or not _is_sha256(binding.get("manifest_sha256"))
                    )
                )
                or not isinstance(binding.get("environment_evidence"), list)
            ):
                raise invalid_success_response("server search required binding is invalid")
            for evidence in binding["environment_evidence"]:
                if (
                    not isinstance(evidence, dict)
                    or set(evidence) != ENVIRONMENT_EVIDENCE_FIELDS
                    or any(
                        not isinstance(evidence.get(key), str)
                        for key in ENVIRONMENT_EVIDENCE_FIELDS
                        - {"constraints"}
                    )
                    or not isinstance(evidence.get("constraints"), list)
                    or any(
                        not _has_text(evidence.get(key))
                        for key in (
                            "project",
                            "platform",
                            "android_version",
                            "source_lineage_ref",
                        )
                    )
                    or not _is_sha256(evidence.get("content_hash"))
                    or not _in_enum(evidence.get("constraints_state"), {"known_empty", "typed", "invalid"})
                ):
                    raise invalid_success_response(
                        "server search binding environment evidence is invalid"
                    )
        grade = item["reuse_grade"]
        if grade in {"direct_reuse_candidate", "adaptation_candidate"} and not all(
            _has_text(payload["target_environment"].get(key))
            for key in ENVIRONMENT_FIELDS
        ):
            raise invalid_success_response(
                "server reusable candidate requires a complete target environment"
            )
        if grade in {"direct_reuse_candidate", "adaptation_candidate"} and (
            not bindings
            or any(
                binding["closure_state"] != "closed"
                or binding["layer"] not in ANDROID_LAYERS
                for binding in bindings
            )
        ):
            raise invalid_success_response(
                "server reusable candidate requires closed implementation bindings"
            )
        if grade == "direct_reuse_candidate" and item["requires_revalidation"]:
            raise invalid_success_response(
                "server direct reuse candidate must not require revalidation"
            )
        if grade == "adaptation_candidate" and not item["requires_revalidation"]:
            raise invalid_success_response(
                "server adaptation candidate must require revalidation"
            )
        if grade == "direct_reuse_candidate":
            alternatives = comparison["alternative_tuples"]
            if (
                comparison["mode"] != "exact"
                or not alternatives
                or not any(
                    _same_environment(alternative["environment"], payload["target_environment"])
                    and _alternative_covers_bindings(alternative, bindings)
                    for alternative in alternatives
                )
            ):
                raise invalid_success_response(
                    "server direct reuse candidate lacks one closed exact tuple"
                )
        if grade == "adaptation_candidate":
            alternatives = comparison["alternative_tuples"]
            if (
                comparison["mode"] != "adaptation"
                or not alternatives
                or not any(
                    not _same_environment(
                        alternative["environment"], payload["target_environment"]
                    )
                    and _alternative_covers_bindings(alternative, bindings)
                    for alternative in alternatives
                )
            ):
                raise invalid_success_response(
                    "server adaptation candidate lacks one closed alternative tuple"
                )
        evidence_gaps = item.get("evidence_gaps")
        if not isinstance(evidence_gaps, list) or any(
            not isinstance(gap, dict)
            or set(gap) != {"reason", "binding_id", "context"}
            or not _has_text(gap.get("reason"))
            or not isinstance(gap.get("binding_id"), str)
            or not isinstance(gap.get("context"), dict)
            for gap in evidence_gaps
        ):
            raise invalid_success_response("server search evidence gaps are invalid")
        layers = item.get("layers")
        if (
            not isinstance(layers, list)
            or any(not _in_enum(layer, ANDROID_LAYERS) for layer in layers)
            or len(layers) != len(set(layers))
        ):
            raise invalid_success_response("server search layers are invalid")
        requested_layers = set(component.get("component_layer", []))
        unknown_layer_reference = (
            item.get("type") in {"case", "implementation"}
            and layers == []
            and grade == "reference_only"
            and item.get("qualification_reason") == "implementation_layer_unknown"
            and item["requires_revalidation"] is True
            and bindings == []
            and comparison["mode"] == "not_qualified"
            and comparison["alternative_tuples"] == []
            and comparison["non_dominated_matched_dimension_sets"] == []
            and any(
                gap["reason"] == "implementation_layer_unknown"
                and gap["binding_id"] == ""
                and gap["context"] == {}
                for gap in evidence_gaps
            )
        )
        if (
            requested_layers
            and not requested_layers.intersection(layers)
            and not unknown_layer_reference
        ):
            raise invalid_success_response(
                "server search result does not match the requested component layer"
            )
        for list_field in ("matched_channels", "search_anchors"):
            values = item.get(list_field, [])
            if not isinstance(values, list) or any(
                not isinstance(value, str) for value in values
            ):
                raise invalid_success_response(
                    f"server search {list_field} is invalid"
                )
        result_type = str(item["type"])
        identity_fields = {
            "case": ("case_id",),
            "implementation": ("implementation_id",),
            "patch": ("evidence_binding_id", "patch_asset_id", "patch_id"),
            "symbol": ("symbol",),
        }[result_type]
        if any(
            not isinstance(item.get(key), str)
            for key in ("implementation_id", "evidence_binding_id", "patch_asset_id", "patch_id", "symbol")
            if key in item
        ) or not any(_has_text(item.get(key)) for key in identity_fields):
            raise invalid_success_response("server search result identity is missing")

    if (
        payload["result_state"] == "empty_for_this_query"
        and (results or pagination["total"] != 0 or pagination["has_more"] is not False)
    ):
        raise invalid_success_response("empty server search response is inconsistent")
    if payload["result_state"] == "matches" and pagination["total"] < 1:
        raise invalid_success_response("matching server search response is inconsistent")
    if pagination["total"] < len(results) or pagination["has_more"] is not (
        pagination["offset"] + len(results) < pagination["total"]
    ):
        raise invalid_success_response("server search pagination is inconsistent")

    if args is not None or query is not None:
        if args is None or query is None:
            raise invalid_success_response("server search request binding is incomplete")
        expected_type = str(getattr(args, "type", "all") or "all")
        expected_target = {
            key: str(getattr(args, key, "") or "").strip()
            for key in ("project", "platform", "android_version")
        }
        expected_target["platform"] = expected_target["platform"].lower()
        expected_layers = sorted(
            {str(value) for value in getattr(args, "component_layer", []) or []}
        )
        actual_layers = sorted(
            {str(value) for value in component.get("component_layer", [])}
        )
        if (
            payload.get("query") != query
            or payload.get("result_type") != expected_type
            or target != expected_target
            or actual_layers != expected_layers
            or pagination["offset"] != int(getattr(args, "offset", 0))
            or pagination["limit"] != int(getattr(args, "limit", 0))
        ):
            raise invalid_success_response("server search response does not match request")


def normalize_server_results(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw_results = payload["results"]
    normalized: list[dict[str, Any]] = []
    search_mode = str(payload.get("search_mode") or "unknown")
    for item in raw_results:
        row = dict(item)
        row["kind"] = row["type"]
        row.setdefault(
            "id",
            row.get("implementation_id")
            or row.get("evidence_binding_id")
            or row.get("patch_asset_id")
            or row.get("patch_id")
            or row.get("symbol")
            or row.get("case_id")
            or "",
        )
        row.setdefault(
            "title",
            row.get("title")
            or row.get("material_title")
            or row.get("display_name")
            or row.get("relative_path")
            or row.get("summary")
            or row.get("case_title")
            or "",
        )
        row["source"] = "server_api"
        row["search_mode"] = search_mode
        if not isinstance(row.get("matched_channels"), list):
            row["matched_channels"] = []
        matched_anchors = row.get("matched_anchors")
        if not isinstance(matched_anchors, list):
            matched_anchors = row.get("search_anchors")
        if not isinstance(matched_anchors, list):
            matched_anchors = row.get("matched_terms")
        row["matched_anchors"] = (
            [str(value) for value in matched_anchors if str(value)]
            if isinstance(matched_anchors, list)
            else []
        )
        normalized.append(row)
    return normalized


def fetch_server_results(args: Any, query: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    user = require_member_alias()
    request = urllib.request.Request(
        server_search_url(args, query),
        headers={
            "Accept": "application/json",
            "X-AKBS-User": user,
            SEARCH_CONTRACT_HEADER: SEARCH_RESPONSE_SCHEMA,
        },
        method="GET",
    )
    payload = request_json(request, timeout=args.server_timeout)
    validate_server_payload(payload, args=args, query=query)
    return normalize_server_results(payload), payload


def server_fallback_reason(exc: BaseException) -> str:
    return failure_result(exc).safe_summary("server search unavailable")


def fetch_case_detail(
    case_id: str, *, implementation_id: str | None = None, timeout: float = 3.0
) -> dict[str, Any]:
    """Read the existing member-visible solution, not an applicability decision."""
    for name, value in (("case_id", case_id), ("implementation_id", implementation_id)):
        if value is None and name == "implementation_id":
            continue
        if not _has_text(value) or value != value.strip() or any(
            token in value for token in ("/", "\\", "\x00")
        ) or value in {".", ".."}:
            raise ValueError(f"{name} must be one server-returned identifier")
    base, _source = member_api_base_url()
    request = urllib.request.Request(
        base.rstrip("/") + "/akbs/api/knowledge/" + urllib.parse.quote(case_id, safe=""),
        headers=member_request_headers(),
        method="GET",
    )
    payload = request_json(request, timeout=timeout)
    if (
        payload.get("case_id") != case_id
        or payload.get("status") != "active"
        or not isinstance(payload.get("title"), str)
        or not isinstance(payload.get("summary"), str)
        or not isinstance(payload.get("sections"), list)
        or not isinstance(payload.get("implementations"), list)
    ):
        raise invalid_success_response("knowledge detail identity or body is invalid")
    for section in payload["sections"]:
        if not isinstance(section, dict) or not isinstance(section.get("label"), str):
            raise invalid_success_response("knowledge detail section is invalid")
        kind = section.get("kind")
        if kind == "text" and isinstance(section.get("value"), str):
            continue
        if kind == "list" and isinstance(section.get("items"), list) and all(
            isinstance(item, str) for item in section["items"]
        ):
            continue
        raise invalid_success_response("knowledge detail section content is invalid")
    seen: set[str] = set()
    for implementation in payload["implementations"]:
        if not isinstance(implementation, dict):
            raise invalid_success_response("knowledge detail implementation is invalid")
        identifier = implementation.get("implementation_id")
        if (
            not _has_text(identifier)
            or identifier in seen
            or implementation.get("case_id") != case_id
            or implementation.get("status") != "active"
            or not _is_sha256(implementation.get("content_hash"))
            or any(
                not isinstance(implementation.get(field), str)
                for field in ("approach", "implementation_summary", "review_state")
            )
            or any(
                not isinstance(implementation.get(field), list)
                or any(not isinstance(item, (str, dict)) for item in implementation[field])
                for field in ("key_decisions", "code_anchors")
            )
            or not isinstance(implementation.get("applicability"), list)
            or any(not isinstance(item, dict) for item in implementation["applicability"])
            or not isinstance(implementation.get("risk_and_rollback"), dict)
        ):
            raise invalid_success_response("knowledge detail implementation identity or body is invalid")
        seen.add(identifier)
    result = {**payload, "source": "server_api"}
    if implementation_id is not None:
        if implementation_id not in seen:
            raise ValueError("implementation_id is not listed under the selected Case")
        result["implementations"] = [
            item for item in payload["implementations"]
            if item["implementation_id"] == implementation_id
        ]
        result["selected_implementation_id"] = implementation_id
    return result
