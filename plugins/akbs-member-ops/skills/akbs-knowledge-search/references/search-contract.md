# Knowledge Search Contract

## Data Sources

Default member search first calls the AKBS member search endpoint:

```text
GET /akbs/api/member/knowledge-search?q=<query>&limit=<limit>
```

The request includes `X-AKBS-User=<member_alias>` and standard content-negotiation/type headers only. The endpoint comes from the AKBS endpoint resolver defaults or controlled admin/test overrides such as `CODEX_REPORT_AKBS_ENDPOINT_MEMBER_SEARCH_URL` and `CODEX_REPORT_AKBS_ENDPOINT_API_BASE_URL`; ordinary member profiles must not require hard-coded `test35`, server paths, submit commands, or database repository paths. The server verifies the fixed workstation source IP. Never send role, token, cookie, or client-IP claims.

When the endpoint is unavailable, unauthorized, times out, or returns an incompatible response, search falls back to generated JSONL indexes from the knowledge repository worktree:

```text
index/
├── case-index.jsonl
├── variant-index.jsonl
├── symbol-index.jsonl
├── search-docs.jsonl
└── evidence-index.jsonl
```

Current repositories are case/variant first. `case-index.jsonl` and `variant-index.jsonl` are the primary search sources. Default search loads `patches/by-id` plus generated AI indexes only. `reports/by-id`, `events/by-id`, and raw `evidence/by-id` are loaded only for explicit archive filters. It must not read residual generated SQLite or residual patch/report indexes.

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

Default `--type all` is the AI reuse view. It returns only `case`, `variant`, `patch`, `symbol`, and AI evidence kinds:

- `patch_diff_facts`
- `patch_problem_summary`
- `project_inference`
- `risk_surface`
- `build_result`
- `verification_result`
- `search_before_change`

Default `--type all` must not return report rows, event rows, or human/archive evidence kinds such as `source`, `work_findings`, `report_context`, or `package_check`. These archive records remain available only through explicit type filters for administrator trace-back and debugging.

## Result Types

- `case`: primary Android engineering problem, requirement, or change scenario across App or GMS, platform, native, HAL, kernel, device, or build layers.
- `variant`: one implementation for a platform, Android version, project, branch, source tree, repo path, patch list, reports, and verification status.
- `patch`: archived patch assets, readme path, status hints, modified files, modules, search anchors, patch-derived explanation, validation notes, and rollback hint.
- `report`: member daily or weekly report entries and report item summaries. Explicit filter only; not part of default AI reuse search.
- `symbol`: reverse index from modified files, SystemProperties, Settings keys, string/resource keys, FrameworkLog keys, modules, and patch-derived anchors to patch IDs.
- `event`: archived records such as `framework_change`, `daily_trace`, or `weekly_trace`, including member, date, project, platform, and package status when applicable. Explicit filter only; not part of default AI reuse search.
- `evidence`: evidence records. Default AI search includes only patch facts, patch problem explanation, project inference, risk surface, build result, device/equivalent verification, and search-before-change. Source metadata, work findings, report context, and package checks require explicit `--type evidence`.

## Case-Bound Original Reads

The search CLI also supports two server-only read actions:

```text
akbs_knowledge_search.py --case-patches <case_id> [--json]
akbs_knowledge_search.py --case-patches <case_id> --download-patch <asset_id> --out <new-file> [--json]
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

No URL in returned metadata controls the request destination. The client builds
the case/asset path from the configured endpoint, validates response identity and
size/hash, and creates the selected output exclusively. These reads do not use
local fallback or write search-use/curation/feedback facts. A successful download
means `verified_original=true`, `reuse_outcome=not_started`, not a reuse verdict.

## Judgment Boundary

The search result is evidence, not a final reuse decision.

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

Use several searches when needed:

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
akbs_knowledge_search.py <query> [--root PATH] [--type all|case|variant|patch|report|symbol|event|evidence] [--limit N] [--json] [--refresh]
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

Full type filter:

```text
--type all|case|variant|patch|report|symbol|event|evidence
```

Markdown output is for humans and Codex final reports. JSON output is for other scripts or workflows.

Every output includes:

```text
source=server_api | local_jsonl_fallback
search_mode=<server-returned mode> | local_jsonl
fallback_reason=<reason when fallback happened>
```

Server results must preserve `search_mode`, `reuse_grade`, `matched_channels`, `matched_anchors`, `case_id`, `package_id`, and other service fields. Human output maps `reuse_grade` directly:

- `reusable`: `可复用候选`
- `reference_only`: `仅参考`
- `insufficient_evidence`: `证据不足`
- `different_function`: `功能不同`
- `duplicate_source`: `重复来源线索`
- `unknown`: `未知分级`

Only `reuse_grade=reusable` may be displayed as `可复用候选`. Local fallback output must say `本地文本搜索，未经过服务端复用分级` so downstream workflows do not treat a local text hit as a server reuse decision. The client must never relabel `structured_lexical` as hybrid or semantic.

Human-facing `case` and `variant` results must surface knowledge validity directly when available:

```text
知识有效度
可信度（confidence）
证据等级（evidence_level）
风险等级（risk_level）
复用分（reuse_score）
```

This is required because a matching case may be only static review, contested, obsolete, or otherwise risky. Search output must not make members infer those limits only from raw evidence rows.
