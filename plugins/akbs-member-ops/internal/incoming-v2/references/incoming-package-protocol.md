# Incoming Package Protocol

## Public v1 compatibility gate

The member Skill and the server share the same incoming v2 schema, three golden manifests, public reason-code contract, and `akbs-error-envelope-v1` schema. The member Skill owns package completeness before upload; the server independently repeats the deterministic contract only as a final safety boundary and never repairs or re-authors a package. The plugin mirror and its exact content-digest compatibility pin live in `contracts/incoming/v2/` at the marketplace repository root. The recorded system Git commit is audit provenance only: an unrelated server commit with identical contract/schema/fixture bytes remains compatible, while any public-contract, reason-code, schema, fixture, or error-envelope digest drift fails closed. Production plugin code and server runtime code do not import each other.

Before publishing an incoming-contract change on WSL, run:

```bash
python3 scripts/validate_incoming_contract_gate.py \
  --system-root /home/jinny/akbs/linux/system
```

The gate uses the real member CLI to build daily, weekly and one complete patch package, plus an isolated same-package queue information completion. It checks positive paths, rejects legacy package shapes, and proves rejected requests leave package, queue, asset, envelope and upload storage unchanged. Local completeness is the primary packaging decision; the server acceptance check is an independent fail-safe, not a second package-authoring pipeline.

Upload, search, and merge clients consume the shared error envelope by stable `code`, `request_id`, optional sanitized `details`, typed category, and safe message. Legacy free-text errors are explicitly marked and cannot drive retry or business decisions. Client errors and logs must not expose credentials, cookies, request bodies, session text, paths, or underlying exception text.

Member-side Codex creates only `incoming` packages and sends them through the AKBS HTTP API. It does not clone, pull, directly search, or push a database repository, and it does not write final knowledge views such as reports, patches, index, or site. The HTTP API stores accepted packages in active SQLite and exposes them to the curation flow. Member configuration contains only the current HTTP endpoint contract.

Only the user's local `akbs-curation-maintainer` skill and the AI knowledge loop can decide whether and how an uploaded package enters the knowledge repository.

The server does not run Codex and must not be the main AI reasoning layer. Project inference, patch problem summary, risk, verification status, search-before-change evidence, remote build evidence, and local device delivery evidence are produced by member-side Codex. Curation decisions, materialization plans, and knowledge validity are produced by the user's local curation maintainer skill, not by the member-side intake skill.

## Server Merge Anchors

Member-side packages must provide enough deterministic anchors for the server to merge without AI inference:

- package identity: `date + member_alias + run_id`
- framework identity: `case_id` and `variant_id`
- variant natural key: `case_id + platform + android_version + project + repo_paths`
- patch identity: patch file content `sha1`
- optional report link: `related_report_run_ids`
- pre-change knowledge use evidence when it happened: search queries, matched object ids, decision (`reuse`, `adapt`, `reference_only`, `not_applicable`, `not_found`, or `unknown`), match/mismatch points, reason, and outcome
- cross-machine verification evidence: remote build host/source root/profile/artifact plus local transfer, adb serial, device push/install/restart, and verification result

The server may merge two android_change packages into the same variant when the natural key matches, even when the incoming `variant_id` differs. The server must not let a later `failed` or `blocked` package overwrite stronger existing evidence; that later package is retained as evidence.

## Path

```text
incoming/YYYYMMDD/member_alias/run_id/
```

Rules:

- path date uses `YYYYMMDD`
- manifest date uses `YYYY-MM-DD`
- `member_alias` in path must match manifest
- `run_id` must start with `YYYYMMDD-HHMMSS`
- package identity is `date + member_alias + run_id`
- `schema_version` is internal and must not be used as a workflow name

## Common Manifest Fields

```json
{
  "schema": "knowledge-incoming-package",
  "schema_version": "2",
  "package_kind": "android_change",
  "member_alias": "lincong",
  "member_name": "林聪",
  "date": "2026-06-01",
  "run_id": "20260601-213000-framework-change",
  "tool": "android-knowledge-intake",
  "summary": "..."
}
```

`tool=android-knowledge-intake` is a frozen incoming v2 wire value. New user
invocations use the canonical `akbs-*` CLIs; changing this historical field would
break the pinned v2 package contract.

Allowed `package_kind`:

```text
daily_trace
weekly_trace
android_change
```

## Default Automation Policy

The default policy is:

```text
preserve automatically first
rank by package status later
```

Daily and weekly incoming automation should run even when no patch is complete. It must preserve session facts, git activity, discovered patch files, build or verification signals, WIP state, failed paths, blocked paths, and missing evidence.

Patch package status is evidence-quality based. A framework change must be `validated` before upload; other statuses stay local or in daily/weekly context.

```text
validated
  Clear scope, clean diff, build pass, and device or accepted equivalent verification pass.
  Project, platform, and Android version are traceable and non-conflicting.

candidate
  Clear implementation evidence, but validation or acceptance evidence is incomplete.
  Also used when project, platform, or Android version metadata is missing, unknown, or conflicting.

draft
  Partial or WIP implementation evidence.

failed
  Failed implementation or verification retained as negative evidence.

blocked
  Blocked work retained with cause and checked paths.
```

Non-`validated` patch packages stop ordinary upload. Daily or weekly trace should still record what happened, why it is incomplete, failed, or blocked, and what evidence remains missing.

Patch package project inference may use a same-member same-day daily package only when no explicit `related_report_run_ids` were provided and the daily context exposes exactly one TVD/TVE/TVA/TVI project candidate. This is a member-side generation convenience, not a server-side guess. Ambiguous daily context, multiple projects, or missing daily packages keep `project=unknown` and prevent validated upload until the member-side Skill collects a traceable value.

## Daily Or Weekly Trace

Required shape:

```text
manifest.json
reports/daily.md or reports/weekly.md
materials/display/report_view.json
materials/evidence/source.json
materials/evidence/codex_sessions.json
materials/evidence/work_findings.json
materials/evidence/weekly_fact_sources.json  # weekly_trace only
```

Manifest excerpt:

```json
{
  "package_kind": "daily_trace",
  "report_type": "daily",
  "report_path": "reports/daily.md",
  "files": {
    "display": [
      "materials/display/report_view.json"
    ],
    "evidence": [
      "materials/evidence/source.json",
      "materials/evidence/codex_sessions.json",
      "materials/evidence/work_findings.json"
    ]
  }
}
```

Report traces must not carry `case_id` or `variant_id`. `reports/daily.md` and `reports/weekly.md` are the primary human-readable products. `materials/display/report_view.json` is a UI read model for cards, lists, and detail panes; it is generated from the same report inputs and must not contain a separate fact set.

Report submission classification is identity-first. A first accepted package for
one member + daily date or member + weekly period is `initial`; if it arrives
after the report deadline, the server presents that upload as a late submission.
Once an effective report exists, every later accepted package is a `revision`
regardless of upload date. New packages record `report_submission_intent` as
`initial` or `revision`; a revision also binds the current effective package
through `replacement_for_run_id` and `supersedes`. The server rejects a silent
second ordinary package or a stale revision target before storage. Old versions
remain immutable history while the revision becomes current.

Real report generation requires explicit per-run consent before any session read or package creation. The report date/week is the exact authorized window; the caller selects only the derived fields needed for that run. `codex_sessions.json` stores only `source_session_ids`, `time_range`, `consent` (`version`, `scope`, `fields`, `granted`), and the memory-only retention policy. It must not store thread titles, cwd, raw messages, raw commands, clipboard/environment values, credentials, or unrelated session content. Temporary extraction state is removed after both success and failure, and a report package without valid consent/privacy evidence is rejected before HTTP.

`report_view` is required:

```json
{
  "kind": "report_view",
  "payload": {
    "schema": "akbs-report-view-human-v1",
    "report_type": "daily",
    "report_date": "2026-07-01",
    "week_range": "",
    "member_alias": "member01",
    "member_name": "成员",
    "material_name": "TVE1086U（青鸾云）",
    "material_summary": "TVE1086U：今日处理锁屏鼠标位置刷新，已完成基础验证。",
    "projects": [
      {
        "project": "TVE1086U",
        "customer": "青鸾云",
        "work_type": "Patch",
        "today_topic": "锁屏鼠标位置刷新",
        "current_result": "已完成基础验证",
        "work_items": [
          {"name": "锁屏鼠标位置刷新", "did": ["完成属性映射处理"], "how": ["按输入链路排查"], "result": "基础验证通过", "status": "已完成"}
        ],
        "key_points": ["关键输入链路已经验证通过"],
        "dependencies": []
      }
    ],
    "documents": [],
    "standalone_work": [],
    "tomorrow_plan": {
      "projects": [
        {
          "project": "TVE1065M",
          "customer": "韩富友",
          "work_type": "GMS",
          "gms_release_type": "IR",
          "gms_target": "A14",
          "plan_items": ["开始执行完整 GMS 测试"]
        }
      ],
      "documents": [],
      "standalone_work": []
    }
  }
}
```

`customer` is the direct customer. `downstream_customer` is optional and means
the direct customer's customer. Thus `TVE1091U AOC 福建移动高清` is represented
as `project=TVE1091U`, `customer=AOC`, and
`downstream_customer=福建移动高清`; direct and downstream names are not aliases.

Daily and weekly report payloads contain parallel `projects[]`, `documents[]`,
and `standalone_work[]` arrays, with at least one non-empty. Every project-bound
row uses `projects[]`, may use `Patch`, `App`, `GMS`, `Doc`, or `Other`, and
retains canonical project/customer identity. Non-project documents use
`documents[]/Doc/document_name`; other non-project work uses
`standalone_work[]/Other/work_name`. GMS is always project-bound. For
`report_type=daily`, every project and non-project scope carries
`key_points[]` and `dependencies[]`; both arrays may be empty. For
daily reports, `tomorrow_plan` is a separate three-container plan model. Its
rows carry scope identity plus `plan_items[]`, never today-progress fields, and
tomorrow-only scopes do not satisfy the requirement for actual daily work. For
`report_type=weekly`, Patch/App project rows contain ledger fields, while
GMS project rows contain release type + target, cycle status, independent
self-test round/result, independent formal-submission count/result, and normal
progress fields. GMS/Doc/Other do not carry Patch/App totals; each non-project row contains `week_summary`, plain
completed/remaining counts and item arrays, `key_points`, `risks`,
`dependencies`, and `next_week_plan`. `display_date` is the last workday of the
week range, not the upload day. Daily work items use fixed-enum status
`已完成`, `处理中`, `待验证`, or `阻塞`.

Every new `weekly_trace` also carries `weekly_fact_sources` evidence with
`schema=akbs-weekly-fact-sources-v3`, the exact `week_range`, source package
keys, unique project, document, and standalone-work counts, work-scope count, a sanitized facts hash, and `missing_fields`. The member
client resolves current AKBS daily reports and the current previous-week report
before local submitted replacement leaves; sessions only supplement missing
daily coverage. Non-empty `missing_fields` fails local upload validation and is
closed through an explicit `akbs-weekly-work-facts-v6` artifact. V6 binds the
effective previous-week package and records main-owned project changes, so
explicit facts cannot reset an existing total or hide a remaining-count gap.
Current daily `dependencies[]`, legacy keyword matches, and previous-week dependencies
are exposed in `attention_review_candidates`; the member must confirm the
current weekly dependency list before submission. Current daily `key_points[]`
are merged and deduplicated by scope. Old `custom`/`定制` counts are rejected
because they cannot be split into `需求`
and `移植` without member confirmation.

`work_findings` is required:

```json
{
  "kind": "work_findings",
  "payload": {
    "scanned_sources": ["codex_sessions", "git_activity", "patch_files", "build_or_verification_records"],
    "items": [
      {
        "title": "锁屏永不休眠策略调整",
        "kind": "possible_android_change",
        "work_status": "candidate",
        "basis": ["会话提到修复锁屏永不休眠", "frameworks/base 存在 diff"],
        "missing_evidence": ["缺少设备或等价验证"],
        "recommended_action": "补验证后可升级为 android_change"
      }
    ],
    "blocked_or_failed": []
  }
}
```

## Framework Change

Required shape:

```text
manifest.json
materials/case.json
materials/variant.json
materials/display/patch_view.json
materials/evidence/source.json
materials/evidence/patch_diff_facts.json
materials/evidence/patch_ai_facts.json
materials/evidence/project_inference.json
materials/evidence/patch_problem_summary.json
materials/evidence/risk_surface.json
materials/evidence/verification_result.json
materials/evidence/search_before_change.json
patches/*.patch
```

Manifest excerpt:

```json
{
  "package_kind": "android_change",
  "case_id": "case-...",
  "variant_id": "variant-...",
  "package_status": "validated",
  "platform": "mtk",
  "android_version": "15",
  "project": "TVE8402M",
  "related_report_run_ids": ["20260601-210000-daily"],
  "files": {
    "case": "materials/case.json",
    "variant": "materials/variant.json",
    "display": [
      "materials/display/patch_view.json"
    ],
    "patches": ["patches/example.patch"],
    "evidence": [
      "materials/evidence/source.json",
      "materials/evidence/patch_diff_facts.json",
      "materials/evidence/patch_ai_facts.json",
      "materials/evidence/project_inference.json",
      "materials/evidence/patch_problem_summary.json",
      "materials/evidence/risk_surface.json",
      "materials/evidence/verification_result.json",
      "materials/evidence/search_before_change.json"
    ]
  }
}
```

`patch_view` is required for human-facing member/admin display. It is not an AI evidence layer:

```json
{
  "kind": "patch_view",
  "payload": {
    "material_kind_label": "补丁包",
    "display_title": "导航策略适配",
    "problem_summary": "要解决什么问题",
    "solution_summary": "补丁如何解决",
    "result_summary": "验证或处理结果",
    "project": "TVE8402M",
    "platform": "mtk",
    "android_version": "15",
    "member_alias": "member01",
    "member_name": "成员",
    "ui_card": {
      "title": "导航策略适配",
      "subtitle": "TVE8402M / mtk / Android 15",
      "summary": "要解决什么问题",
      "risk_or_gap": "暂无明确遗留风险"
    },
    "detail_sections": []
  }
}
```

`patch_ai_facts` is required for admin-side validation, curation review, search indexing, and merge judgement. It must not be the UI primary display source:

```json
{
  "kind": "patch_ai_facts",
  "case_id": "case-...",
  "variant_id": "variant-...",
  "payload": {
    "module": "frameworks/base/services/core",
    "feature_domain": "导航策略",
    "patch_behavior_goal": "用户可见或系统行为目标",
    "code_anchors": {
      "files": [],
      "symbols": [],
      "resource_keys": [],
      "settings_keys": [],
      "system_properties": [],
      "framework_log_keys": []
    },
    "patch_assets": [],
    "verification_targets": {},
    "search_usage": {},
    "search_match_class": {
      "decision": "adapt",
      "merge_hint": "reference_only",
      "explanation": "adapt 只能作为参考证据，不能直接触发合并。"
    },
    "merge_gate_inputs": {},
    "protocol_version": "patch-human-ai-evidence-v1",
    "plugin_version": "1.0.153"
  }
}
```

`package_status` must match `materials/variant.json` `package_status`.

`materials/evidence/source.json` carries two independent development provenance facts:

```json
{
  "kind": "source",
  "payload": {
    "implementation_origin": "manual",
    "implementation_origins": ["manual"],
    "workflow_contract": "current_codex_skill"
  }
}
```

`implementation_origin` identifies the code author. `workflow_contract` identifies how the patch entered AKBS and is one of `current_codex_skill`, `manual_import`, or `historical_import`. Only `workflow_contract` controls the pre-change search gate. A direct raw patch defaults to `current_codex_skill`; an older capture package without a workflow field must declare a truthful import contract explicitly. The member client must reject conflicting workflow values and must not infer one fact from the other.

`validated` requires all of:

- `verification_result.payload.contract_version = akbs-verification-evidence/v2`
- `verification_result.payload.scope = feature`
- `verification_result.payload.requirement_acceptance = accepted`
- `verification_result.payload.result = PASS`

Build delivery, adb push/restart, and legacy unscoped verification records remain evidence but
cannot upgrade a package to `validated`.
The normative method fields, failure reasons, evidence-rating limits, and conformance cases are
defined only in `verification-acceptance-v2.json`.

## Source Capability Versions

Generation-time plugin freshness and package capability validation are separate rules.

Member-side generation or upload must stop when `materials/evidence/source.json` records `plugin_version_check.blocking=true` or `plugin_version_check.status=SESSION_CACHE_STALE`. A stale Codex session cannot be treated as a valid package generator.

Admin-side processing validates the capabilities required by each package instead of requiring `plugin_version` or `skill_version` to equal the latest plugin version at processing time. It calls the shared deterministic rules layer:

```python
source_version_compatibility_matrix()
source_version_errors(source_payload, required_capabilities=[...])
```

Current capability minima:

```text
source_version_evidence = 1.0.60
report_view_v1 = 1.0.61
patch_view_v1 = 1.0.62
patch_ai_facts_v1 = 1.0.62
split_report_skills = 1.0.63
report_view_v2 = 1.0.63
patch_package_unification_v1 = 1.0.139
queue_information_completion_v1 = 1.0.139
patch_package_subject_v2 = 1.0.140
verification_acceptance_v2 = 1.0.142
weekly_project_ledger_v2 = 1.0.156
report_scope_containers_v1 = 1.0.158
daily_attention_fields_v1 = 1.0.159
daily_tomorrow_plan_v1 = 1.0.161
gms_report_progress_v1 = 1.0.163
```

The required capabilities come from package contents, not from the current plugin release.
Historical packages retain their generation-time capability evidence. A current validated patch
package requires the unified package, v2 subject, and verification acceptance v2 capabilities;
queue information completion requires the same-package completion capability.

`patch_packages.patch_package_id` is the only patch-package business identity in both queue and main. `packages.package_key` identifies an immutable physical upload source only. Notification, information-request, and merge-confirmation IDs remain causal event IDs and must not be used as replacement package subjects.

A `current_codex_skill` workflow must carry real pre-change knowledge search evidence in `materials/evidence/search_before_change.json`. If no reusable knowledge was found, record the explicit search result as `not_found` instead of omitting the evidence. When pre-change search did not really happen, preserve `payload.searched = false` but keep the current-workflow package out of `validated` and ordinary upload; do not fabricate search evidence or relabel its implementation origin. A truthful `manual_import` or `historical_import` may carry real verification and patch facts while recording `searched=false`; admin-side curation must then perform a post-change overlap check without awarding search-loop reuse score. `implementation_origin` does not control this gate.

`candidate`, `draft`, `failed`, and `blocked` are local/report-context states by default. They must not be uploaded as ordinary patch packages, and they must not be presented as knowledge entries or reuse-ready validated solutions by member-side tooling.

The queue branch has these current stages:

```text
received
under_review
information_required
information_review
closed
```

The exact machine state graph is not duplicated in Skill prose. It is consumed
from `patch_package_contract.queue` in the pinned incoming public contract. Its
current non-terminal states are `received`, `under_review`,
`information_required`, and `information_review`; its terminal state is
`closed`. Admission moves the same `patch_package_id` to main stage
`under_review`; the main branch continues through `pending_merge_confirmation`,
`dispute_open`, and `closed`.
The same contract publishes the `patch_queue` reason-code family, so a modern
queue rejection remains a typed contract failure instead of degrading to an
unknown client error. The public contract content hash is the compatibility
condition; the recorded system commit is provenance for audit only.

An open lightweight request is read with `--inspect-information-request`. A response uses:

```json
{
  "schema": "akbs-patch-package-information-completion/v1",
  "request_id": "information-request-...",
  "statement": "成员确认的补充说明",
  "fields": {
    "applicability": "TVE1086U / unisoc / Android 14 / default_display"
  },
  "attachments": [
    {
      "relative_path": "materials/requirements/display-mode-boundary.txt",
      "source_path": "./display-mode-boundary.txt"
    }
  ]
}
```

The completion client reads the server request again, verifies the same `patch_package_id`, and inserts the authoritative patch-set hash. The response file cannot define or override either identity. Text, allowed metadata and non-patch attachments may extend the current envelope; `.patch` files and `patches/` paths are rejected. Completion is addressed only by the causal `request_id` and must move the subject from `information_required` to `information_review`. If patch bytes or feature scope must change, reject intake and generate a new complete patch package instead. After admission, curation only decides new knowledge or planned merge.

`materials/evidence/search_before_change.json` records member-side knowledge use before or during the change. It can come from an explicit patch capture package, or from same-day `akbs-knowledge-search` usage records (including the frozen legacy usage schema) only when those records match the current patch feature anchors. Same member and same day are not enough:

```json
{
  "kind": "search_before_change",
  "payload": {
    "result": "INFO",
    "method": "knowledge_search",
    "searched": true,
    "queries": ["电源键 rk3576"],
    "decision": "adapt",
    "reuse_decision": "adapt",
    "targets": ["case-power-key"],
    "reason": "同类策略可参考，当前项目需适配"
  }
}
```

This is development evidence only. It must not be treated as a curation decision.

`patch_diff_facts` should include the patch content hash. For a single patch, set top-level `content_sha1`; for multiple patches, fill `patches[]`:

```json
{
  "kind": "patch_diff_facts",
  "payload": {
    "content_sha1": "40-hex-sha1-for-single-patch",
    "patches": [
      {
        "path": "patches/mtk15-frameworks-base@feature.patch",
        "content_sha1": "40-hex-sha1"
      }
    ],
    "modified_files": ["frameworks/base/services/core/java/..."]
  }
}
```

## Project Inference

Project must come from traceable evidence:

```text
explicit incoming project containing a TVD/TVE/TVA/TVI model
capture package manifest or patch item project containing a TVD/TVE/TVA/TVI model
source_root, repo_path, git branch, git remote, local mount path, or WSL source-access registry
patch/feature README/diff/summary text
explicit related daily or weekly report context
```

Daily reports are not just prose. When a daily package context contains exactly one traceable TVD/TVE/TVA/TVI project model, `manifest.project` and `materials/evidence/project_inference.json` must carry it so AKBS active SQLite and member/admin views can show accurate member work context, and later curation can use it as evidence. If the daily context contains multiple projects, keep the single `project` field unknown and preserve the checked candidates and limits in project inference evidence.

If the daily context contains multiple traceable candidates that share one canonical project, such as `TVE1067M1` and `TVE1067M1_H031`, write the canonical project `TVE1067M1` to `manifest.project` and keep the full raw candidates in `project_inference`. Do not truncate it to `TVE1067M`, because `TVE1067M` and `TVE1067M1` are distinct projects.

The structured project field stores only the company model that matches the TVD/TVE/TVA/TVI naming rule: `TV[D/E/A/I] + two LCD-size digits + two sequence characters + platform M/R/U + optional digit`. Any text outside that model is evidence text, not part of `manifest.project` or `materials/variant.json project`: branch suffixes, customer suffixes, build branches, business labels, module labels, Chinese descriptions, and other non-standard trailing text must remain in `project_inference.raw_inputs` or `basis`. Examples: `TVE1067M1_H031` -> `TVE1067M1`, `TVE1086U_MAIN_HANGYAN` -> `TVE1086U`, `TVE1091U福建移动高清` -> `TVE1091U`. Confirmed historical alias `TVE8402` must normalize to `TVE8402M`; new packages must not write `TVE8402` as the structured project. This is conservative normalization for one project model, not permission to merge unrelated TVD/TVE/TVA/TVI models.

Patch packages should not depend on the UI or server to guess the project. Prefer explicit project evidence from the capture package or `--project`; otherwise attach the related daily run id so the patch can inherit that daily context. If no traceable project exists, the package can still be preserved, but curation must treat the missing project as an evidence gap and must not promote it to high-confidence reusable knowledge.

Current automatic project recognition scope:

```text
TVD
TVE
TVA
TVI
```

If the project cannot be identified from traceable evidence, use:

```json
{
  "project": "unknown"
}
```

and explain checked sources and limits in `project_inference`.

Generic labels such as `android16`, `Camera2`, or `mtk android16 Camera2` are not company project names. They must remain checked raw inputs in `project_inference`, but they must not be written to `manifest.project` or `materials/variant.json project`.
