# Knowledge Search Contract

## Data Sources

Default member search first calls the AKBS member search endpoint:

```text
GET /akbs/api/member/knowledge-search?q=<query>&limit=<limit>&offset=<offset>
  &type=<all|case|implementation|patch|symbol>
  &project=<project>&platform=<platform>&android_version=<version>
  &component_layer=<repeatable-layer>
```

The request includes `X-AKBS-User=<member_alias>`, the fixed
`X-AKBS-Member-Search-Contract=akbs-member-knowledge-search-v2` header and content
negotiation. The endpoint comes from the resolver defaults or controlled admin/test
overrides. Ordinary members need no server/database path. The server verifies the
fixed workstation source IP. Never send role, token, cookie or client-IP claims.

When the endpoint is unavailable, unauthorized, times out, or returns an incompatible response, search falls back to generated JSONL indexes from the knowledge repository worktree:

```text
index/
├── case-index.jsonl
├── variant-index.jsonl
├── symbol-index.jsonl
├── search-docs.jsonl
└── evidence-index.jsonl
```

Local repositories remain Case/Implementation-first. `variant-index.jsonl` is a
historical storage filename; its rows are exposed as `implementation`. Default local
search loads patches and generated AI indexes, not residual SQLite or patch/report
indexes. Reports/events/raw evidence are loaded only for explicit local archive filters.
This is text fallback, not a second server protocol or reuse authority.

Search must not automatically use the database repository or member incoming worktree. It may inspect those only when an administrator passes an explicit `--root`.

Merge confirmation review is a separate member API path and must remain server-backed. It reads:

```text
GET /akbs/api/member/me/merge-confirmations
GET /akbs/api/member/me/merge-confirmations/{confirmation_id}
GET /akbs/api/member/me/merge-confirmations/{confirmation_id}/target
GET /akbs/api/member/me/merge-confirmations/{confirmation_id}/compare
```

The path identifier is the causal `confirmation_id` returned by the list or notification. `patch_package_id` remains the patch-package business subject across queue and main, while any returned `package_key` is source provenance only. The client must not fall back from `confirmation_id` to a review or source-package identifier.

These reads do not fall back to local JSONL and must not fabricate merge basis when the API is unavailable. Dispute submission is the only write action and requires an explicit send command:

```text
POST /akbs/api/member/me/merge-confirmations/{confirmation_id}/dispute
```

Read-only analysis must not call the dispute endpoint.

When `search-docs.jsonl` includes replacement fields, case results must preserve and display them:

```text
replacement_case_id
replacement_title
replaces_case_ids
```

These fields mean the local curation skill has marked an old case as obsolete or contradicted and linked a recommended replacement case. Search should surface the relationship as guidance, not as a final reuse decision.

Server `--type all` returns `case`, `implementation`, `patch` and `symbol`. Its evidence
is available through the selected Implementation's bindings. Default local text search
also preserves these prior AI evidence hints:

- `patch_diff_facts`
- `patch_problem_summary`
- `project_inference`
- `risk_surface`
- `build_result`
- `verification_result`
- `search_before_change`

Default `--type all` must not return report rows, event rows, or human/archive evidence kinds such as `source`, `work_findings`, `report_context`, or `package_check`. These archive records remain available only through explicit type filters for administrator trace-back and debugging.

## Result Types

- `case`: a stable Android engineering problem or requirement.
- `implementation`: a concrete approach under a Case, with separately declared exact applicability tuples and bound source/verification evidence.
- `patch`: archived patch assets, readme path, status hints, modified files, modules, search anchors, patch-derived explanation, validation notes, and rollback hint.
- `report`: explicit local archive filter only; not a server result type.
- `symbol`: reverse index from modified files, SystemProperties, Settings keys, string/resource keys, FrameworkLog keys, modules, and patch-derived anchors to patch IDs.
- `event`: explicit local archive filter only; not a server result type.
- `evidence`: local AI hints or explicit local archive evidence, not a standalone server result type. `--type variant` is also local-only and reads the historical Implementation index.

## Existing Solution Detail and Exact Original Reads

`--case-detail <case_id> [--implementation-id <implementation_id>]` calls the
existing `GET /akbs/api/knowledge/{case_id}`, with the same fixed-IP member
identity. It preserves the Case's full functional boundary and concrete
Implementation approach, decisions, anchors, risks, rollback, tuples and
bindings. It is server-only and writes no search/reuse-success record.

The search CLI also supports two server-only read actions:

```text
akbs_knowledge_search.py --case-patches <case_id> [--json]
akbs_knowledge_search.py --case-patches <case_id> --implementation-id <implementation_id> [--json]
akbs_knowledge_search.py --case-patches <case_id> --implementation-id <implementation_id> --download-patch <asset_id> --out <new-file> [--json]
```

They call `GET /akbs/api/member/me/knowledge/{case_id}/patches` and the same path
with `/{asset_id}`. The listing schema is `akbs-knowledge-case-patch-originals/v1`,
scope `active_v2_case_source_evidence` or `active_historical_case_snapshot`; it
includes the case identity, availability, and exact opaque handles, filenames,
byte counts and SHA-256. Historical handles reuse the case-bound membership ID;
they do not mean a new `package_assets` row or rewritten history. Historical
reads require the case's current revision, immutable membership and original
source snapshot to agree. An active case that lacks closed provenance or
controlled bytes is `unavailable`, not thereby historically invalid. Inactive
cases, cross-case assets or invalid source/file bindings are not downloadable.

An explicit `implementation_id` is passed to both list and download and returns
`active_implementation_originals`. Each opaque handle has its own authority:
`accepted_evidence_binding` or `historical_case_snapshot`. Accepted entries
preserve their actual role; historical entries do not invent a binding role.
The selected Implementation identity and separately declared exact environments
must agree in both responses. No selector-removal retry, same-package fallback,
same-SHA substitution, or cross-environment combination is allowed.

The source `package_content_hash` preserves the original package identity: a
historical snapshot may carry its original SHA-1 or SHA-256, while an accepted
binding uses SHA-256. Manifest and downloaded patch digests always use SHA-256;
the historical identity is never rewritten or used instead of the byte digest.

No URL in returned metadata controls the request destination. The client builds
the case/asset path from the configured endpoint, validates response identity and
size/hash, and creates the selected output exclusively. These reads do not use
local fallback or write search-use/curation/feedback facts. A successful download
means `verified_original=true`, `reuse_outcome=not_started`, not a reuse verdict.

## Judgment Boundary

The search result is evidence, not a final reuse decision.

These reads support Android engineering across App/GMS, platform, native, HAL,
kernel, device and build work. Seven-layer filters classify product-source
Patch evidence; an independent App is not thereby a product-source Patch.

Server results preserve exact identities, `reuse_grade`, `requires_revalidation`,
`layers`, `required_bindings`, `environment_comparison` and evidence gaps. The response
also preserves `result_state`, `completeness`, `reason_code`, target environment,
filters, pagination and projection. Incomparable environment matches remain separate;
the client does not rank Android version, platform or project with a global priority.

Local rows strip derived server grades, bindings, environment comparisons and reuse
scores. Historical confidence/evidence/risk annotations stay as original source
annotations, explicitly not this query's server qualification. Local hints may support
`reference_only`, but cannot authorize `reuse`, `adapt` or `not_found`.

Do not treat these fields as absolute truth:

- `status`
- `package_status`
- `reuse_hint`
- platform labels
- author notes
- validation status
- evidence result

They are useful hints. The consuming workflow must compare the current requirement with stored facts such as modified files, modules, patch-derived anchors, affected artifacts, touched keys, readme details, build evidence, device verification, explanation basis, explanation limits, and rollback notes.

## Recommended Query Terms

The Skill's **Functional Query Construction** rule owns query semantics. The CLI
accepts the already constructed query; it does not extract a function or translate
natural language. Target options remain separate. Several complete queries may
be selected before one bounded invocation; their results form a union, so splitting
required conjuncts across queries cannot prove a complete match.

Use source-grounded terms when needed:

- user-facing feature words
- subsystem: `WindowManager`, `ActivityTaskManager`, `PackageManager`, `SystemUI`, `Launcher3`
- file or class name
- artifact: `services.jar`, `framework.jar`, `framework-res.apk`, `SystemUI.apk`
- system property: `persist.sys.*`
- Settings key
- resource or string key
- visible log keyword
- patch-derived problem keyword

## Patch Explanation Boundary

Historical patches may be searchable even when their old readme is weak or missing because patch content can provide problem/solution leads, keywords, and risk surface.

Human-facing search output should present these as patch problem/solution leads, not as verified facts. A consuming workflow must not treat them as proof of:

- original customer requirement
- device verification
- release state
- final acceptance

Use these leads for reuse analysis, not as final conclusions.

## Output Contract

The CLI must support:

```text
akbs_knowledge_search.py <query> [--additional-query QUERY] [--root PATH] [--type all|case|implementation|patch|symbol] [--limit N] [--offset N] [--project P] [--platform P] [--android-version V] [--component-layer LAYER] [--json] [--refresh]
```

The same script also supports merge confirmation review:

```text
akbs_knowledge_search.py --merge-confirmation list
akbs_knowledge_search.py --merge-confirmation detail --merge-confirmation-id <confirmation_id>
akbs_knowledge_search.py --merge-confirmation target --merge-confirmation-id <confirmation_id>
akbs_knowledge_search.py --merge-confirmation compare --merge-confirmation-id <confirmation_id>
akbs_knowledge_search.py --merge-confirmation analyze --merge-confirmation-id <confirmation_id>
akbs_knowledge_search.py --merge-confirmation dispute --merge-confirmation-id <confirmation_id> --send-dispute --dispute-reason <reason>
```

`analyze` output must separate human summary from Codex evidence and include target knowledge, merge basis, matched anchors, counter evidence, recommendation, and a dispute reason draft when the backend says dispute is allowed.

Additional local-only filters (never sent to the server):

```text
--type variant|report|event|evidence --source local
```

Markdown output is for humans and Codex final reports. JSON output is for other scripts or workflows.

Every output includes:

```text
source=server_api | local_jsonl_fallback
search_mode=<server-returned mode> | local_jsonl
fallback_reason=<reason when fallback happened>
```

Server results preserve `search_mode`, grade, matched channels/anchors and exact
Case/Implementation/binding identities. Human output maps the candidate grade exactly:

- `direct_reuse_candidate`: `可直接复用候选`
- `adaptation_candidate`: `需适配候选`
- `reference_only`: `仅参考`

No other server grade is accepted. `reuse` requires a direct candidate; `adapt` may
select a direct or adaptation candidate conservatively. Both require a concrete
Implementation and closed accepted implementation evidence in healthy complete server
responses. `reference_only` needs a target actually present in this result, not complete
reuse proof. It may reference a Case or local hint without claiming application success.

`not_found` is a member usage conclusion, never a server decision. One bounded run
must contain at least two distinct queries, each `empty_for_this_query`, complete,
projection ready/complete and total zero. Other empty/failure states remain unknown.
Local fallback says `本地文本搜索，未经过服务端复用分级`; it never fabricates a grade.
The two-stage structured lexical mode is not relabeled hybrid or semantic.

JSON output returns the immutable usage receipt path and SHA256. It preserves each
query's health, target environment, returned binding evidence and the chosen use
decision. Later capture/submission must preserve the selected receipt instead of
flattening it to same-day text. This is pre-change evidence, not a verified reuse event.
