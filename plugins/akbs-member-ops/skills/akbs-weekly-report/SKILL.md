---
name: akbs-weekly-report
description: "Use when generating, revising, checking, or submitting a member personal weekly report package through AKBS incoming. Do not use for daily reports, patch packages, administrator summaries, or knowledge curation decisions."
---

# AKBS Weekly Report

Use this member-facing skill for personal 周报包（weekly report package） work. It answers one question: 这一周完成多少、还剩多少、风险和依赖是什么.

This skill owns the weekly command entrypoint `scripts/akbs_weekly_report.py`.
It routes to the shared member incoming v2 kernel, so member identity, server submission,
manifest protocol, replacement metadata, plugin version gate, session cache gate,
duplicate guard, and local validation remain shared with daily and patch intake. The old
umbrella `android_knowledge_intake.py ... weekly` command remains compatible.

## Active Install Family

Before reading report inputs, writing direct weekly facts, generating a package, or
performing any check/prepare/submit action, run:

```bash
python3 "../akbs-member-setup/scripts/akbs_member_setup.py" preflight-install-family
```

Only pure `--help` may bypass it. Continue only on exit 0 with JSON `status=PASS`.
This check must happen before creating a weekly-facts file; a later CLI doctor cannot
retroactively authorize an earlier write.

## Boundary

- Owns personal weekly generation, `reports/weekly.md`, `materials/display/report_view.json`, weekly period rules, explicit weekly revision, and weekly submission.
- Does not generate daily reports, patch packages, administrator summaries, curation decisions, database writes, knowledge repository writes, or UI changes.
- Weekly reports are progress archives only. They do not become knowledge repository materialization candidates.

## Report Model

Daily reports record what happened today, how the member handled it, and the
current result. Weekly reports do not repeat daily execution details. They
summarize the week around project progress: completed count, remaining count,
risks, dependencies, and next-week closure plan.

The weekly period is Monday through Sunday, inclusive. Include current
effective daily reports on both boundary dates; a late upload date does not
redefine the reporting period or exclude Monday's report.

Weekly facts are resolved in this order:

1. Current effective daily reports for the target week from the member AKBS API.
2. The current effective previous-week report as the rolling project ledger.
3. Local `submitted` report packages as an offline fallback, with replacement
   leaves taking precedence over the reports they supersede.
4. Sanitized Codex session summaries only as supplementary evidence for work
   not represented by an effective daily report.

Current daily rows carry project-bound Patch/App/GMS/Doc/Other scopes,
non-project Doc scopes, and non-project Other scopes. Preserve the owning array when rolling daily work into the weekly ledger;
do not classify by item wording. Historical daily rows without scope remain
readable, but they do not prove `work_type`, so ask the member for that missing
weekly fact.

Current daily reports also carry a separate `tomorrow_plan`. It is planning
context only, never evidence that work started or progressed during the week.
A tomorrow-only project, document, or standalone scope must not create a weekly
progress row, completion count, remaining count, or project total. Its
`plan_items[]` may become the latest next-week plan only when the same work
scope already exists in this week's actual daily records or the effective
previous-week ledger. Historical nested `tomorrow_focus[]` is read only as the
legacy equivalent of that planning context.

Current daily rows also carry optional `key_points[]` and `dependencies[]`.
Merge and deduplicate this week's key points by work scope instead of carrying
last week's key points forward. Treat daily dependencies, legacy keyword
matches, and unresolved dependencies from the previous weekly report as
candidates only. List those candidates from
`weekly_fact_sources.attention_review_candidates` and ask the member which ones
remain valid before submission. The confirmed weekly `dependencies[]` wins;
never silently publish or discard an unconfirmed dependency. Historical daily
rows without these arrays may use the existing keyword scan only as a candidate
source.

Do not count Codex sessions as requirements. If the effective reports cannot
prove project role, requirement date, requirement source, project totals, or
remaining-item identity, local check must fail with exact missing fields. Ask the member
only for those facts, write an `akbs-weekly-work-facts-v6` JSON file under
`$CODEX_HOME/artifacts/akbs-member-ops/weekly-facts/`, and regenerate
with `--weekly-facts`. Read `../../internal/incoming-v2/references/weekly-facts-contract.md`
when this fact-completion path is needed. Do not ask the member to repair a
large part of generated Markdown manually.

Use `需求`, `移植`, and `Bug` as the three business categories; do not retain a
`定制` parent category. `需求` means a customer requirement the team has not
implemented before. `移植` means reusing or porting a requirement already
implemented before. A migrated Bug fix remains `Bug`. `BSP` is a responsibility
state, not a business category: keep the original `需求`、`移植` or `Bug` type,
remove transferred work from Android remaining, and show it separately as
`BSP 跟踪`. Old `定制` totals cannot be split automatically; ask the member to
confirm `需求` versus `移植`.

Generate `reports/weekly.md` with this required structure:

- 本周概况
- 项目详情
  - 本周完成
  - 当前剩余
  - 重点说明
  - 风险 / 依赖
- Doc / Other（仅存在非项目工作时）
  - 本周完成
  - 当前剩余
  - 重点说明
  - 风险 / 依赖
- 下周计划

In `reports/weekly.md`, bold the project name every time it appears, not only in
project headings. This applies to overview prose, completed and remaining items,
risks, dependencies, and next-week plans. Bold only the project name; keep the
customer chain unbolded. `report_view.json` remains structured plain text and
must not contain Markdown markers.

The weekly Markdown baseline is the project block format:

```markdown
## 一、本周概况

### **TVE1086U** 青鸾云

本周围绕 **TVE1086U** 青鸾云 项目推进：完成系统接口联调。

- 类型：Patch
- 项目角色：主责
- 需求时间：2026-06-18
- 需求来源：CR
- 本周变化：新增 2 项（需求 2）、转 BSP 2 项（Bug 2）
- 共 18 项：需求 6、移植 4、Bug 8
- 本周完成 5 项：需求 2、移植 2、Bug 1
- 当前剩余 3 项：需求 1、移植 2
- BSP 跟踪 2 项：Bug 2
```

The visible Markdown keeps one compact `本周变化` line and omits zero change
types. The structured v6 facts bind the current effective previous-week package
and carry `added`, `reopened`, `closed_without_change`, `removed`,
`transferred_to_bsp`, and `bsp_closed`. Members confirm the business facts;
Codex writes the ledger JSON and computes the displayed total and remaining.
An explicit facts file must never reset an existing project's total.

The five visible categories are exactly `Patch`, `App`, `GMS`, `Doc`, and
`Other`. Every project-bound category uses `projects[]`. `Patch` means system
source customization normally delivered as patches. `App` means application
or demo development and requires `App 名称`. Preserve the scope resolved by the
daily workflow; do not reclassify it from weekly item wording. Ask the member
only when the daily source is missing, conflicting, or lacks the App name.

GMS project rows use release type + Android target version in `A<digits>` form
(for example `A14`), cycle status, current self-test or
submission stage, cumulative self-test round/result, cumulative formal
submission count/result, weekly progress, key points, risks, dependencies, and
plans without Patch/App totals. Self-test rounds and submissions are
independent; submission requires the latest self-test result to have passed,
and a returned submission moves the stage back to self-test. Keep problems and
fixes in normal weekly progress items. Project-bound Doc and Other
also use progress details without Patch/App totals. A non-project Doc uses
`documents[]/document_name`; a non-project Other uses
`standalone_work[]/work_name`. GMS is never non-project. Historical `Document`
remains readable as `Doc`.
Never add GMS, Doc, or Other counts to Patch/App project demand totals.

`项目角色` is required and must be `主责` or `协作`. `需求时间` is required
and uses `YYYY-MM-DD`. `需求来源` is required and must be exactly one of
`CR`, `TL`, `PM`, `TE`, or `BSP`. Every member provides `本周完成` and
`当前剩余`; only `协作` may omit the `共 N 项` total line.

The main owns the initial project total and every later total or responsibility
change. A collaborator reports personal completion and personal remaining only;
non-zero project ledger changes from a collaborator are rejected. When a
collaborator completes an out-of-list item, keep it pending until the main
confirms whether it is added, reopened, or already covered by the project
ledger.

For `Patch`, the main member uses the existing category breakdown. Positive
count lines must contain at least one `需求`, `移植`, or `Bug`. Omit zero
categories; the displayed total must equal the category sum. For `App`, use
plain counts without Patch categories:

```markdown
### **TVI2343R** 海信｜App：蓝牙播放器

- 类型：App
- App 名称：蓝牙播放器
- 项目角色：主责
- 需求时间：2026-06-25
- 需求来源：CR
- 共 10 项
- 本周完成 3 项
- 当前剩余 7 项
```

Under each project in `项目详情`, use exactly these numbered subsections:
`1. 本周完成`, `2. 当前剩余`, `3. 重点说明`, and `4. 风险 / 依赖`.
`重点说明` records project external news, scope changes, or a key difficulty
overcome this week; write `无` when there is none. Keep risks and dependencies
as separate unordered lists under subsection 4.

For multiple projects, repeat the same block per project. Do not use a large
overview table in `本周概况`.

Generate the same-source UI read model at `materials/display/report_view.json`. Required weekly payload fields include `schema=akbs-report-view-human-v1`, `report_type=weekly`, `week_range`, `display_date`, `material_name`, `material_summary`, `projects[]`, `documents[]`, and `standalone_work[]`; at least one array must be non-empty. Patch/App project rows keep the existing total and ledger fields. GMS project rows carry the minimal release/target, cycle, self-test, and submission facts plus progress, key points, risks, dependencies, and plans without Patch/App totals. Project GMS titles are `项目 + 客户｜GMS：送测类别（目标版本）`; `display_name`, `model`, `work_name`, and `document_name` never replace that identity. Project-bound Doc/Other are progress scopes. Non-project Doc and Other use their own arrays and concrete names.

Weekly card identity is not the week range. `material_name` must preserve the
customer chain, such as `TVE1086U（青鸾云）` or
`TVE1091U（AOC → 福建移动高清）`; multiple projects must keep each project
paired with its own customer chain. Standalone documentation uses
`Doc：<文档名称>`. `material_summary` must summarize each project's or
document's weekly completed and remaining state. The current read model emits
`material_name`, `material_summary`, `projects`, and `documents`; it does not
emit `display_title`, `ui_card`, `one_line_summary`, `project_ledgers`,
`weekly_progress_summary`, or `weekly_detail_sections`.

Each project row must include both a recognized company project name
and a direct customer. A third segment is optional and represents the direct
customer's customer: parse `TVE1091U AOC 福建移动高清` into project
`TVE1091U`, customer `AOC`, and downstream customer `福建移动高清`. Keep
`TVE1086U 青鸾云` compatible as a two-segment identity. If project or direct
customer is missing, generate the package with
`需成员补充项目名` or `需成员补充客户名` so the member can edit it, but local
submit must stop until both values are present. Do not repair Markdown alone.
Complete the structured weekly facts and regenerate so `reports/weekly.md` and
`report_view.json` remain the same-source views of one fact set. Any management-side
aggregation happens outside the member workflow and should not be exposed as a
member responsibility. Direct and downstream customer aliases must be resolved
within their own levels, never across the customer chain.

There must be exactly one `projects[]` row for each reporting scope. A Patch
scope is `project + direct customer + Patch`; an App scope is `project + direct
customer + App + app_name`; a GMS scope adds release type + target. The same
formal project may therefore contain one Patch row, multiple App rows, separate
IR/MR/SMR GMS rows, and project-bound Doc/Other rows; no
scope may be duplicated. Feature names remain work items and do not create more rows. The
gate is structural, not a vocabulary blacklist: it validates the canonical
project, customer chain, work scope, and same-source identity consistency.
Generation and `--submit-latest` reject conflicts before HTTP.
An absent next-week action is an empty `next_week_plan[]`; do not write `无` and
do not render that project's block under `下周计划`.

`reports/weekly.md` and `report_view.json` are deterministic views of the same
normalized weekly facts. `report_render_binding` binds both files to the weekly
fact hash. Never repair only one file. Update the facts and regenerate.
`--prepare` returns failure when local validation fails, and submission always
revalidates before HTTP.

For an existing main scope, the v6 gate calculates:

```text
current total = previous total + added - removed
current Android remaining = previous Android remaining + added + reopened
                            - project_completed - closed_without_change
                            - removed - transferred_to_bsp
```

`project_completed` is machine-only in the member Markdown: the main confirms
the project-wide total after collecting collaborator progress, while each
visible `本周完成` line remains that member's personal completion. Management
aggregation must reject a mismatch between the main-confirmed value and the sum
of submitted member rows.

The baseline package key and week range must match the current effective
previous-week report. A mismatch, an unexplained count gap, a total reset, or a
legacy explicit facts file targeting an existing scope blocks package creation
and therefore also blocks upload.
When automatic rolling finds work that does not match an item remaining from
last week, `weekly_fact_sources.scope_change_candidates` lists the exact items.
Ask the main only whether each candidate is added, reopened, removed, or already
covered; do not ask them to reconstruct the full report.

Before running `--prepare`, `--upload`, or `--submit-latest`, distinguish
project-bound work from non-project Doc and Other work. Every project row
requires a recognized project + customer pair. A non-project document requires
`document_name`; other non-project work requires `work_name`. Ask for project/customer only when
the unresolved work is project work, and reply:

```text
缺少项目名和客户名，请补充，例如：TVE1086U 青鸾云；如有客户的客户，继续写第三段，例如：TVE1091U AOC 福建移动高清。
```

If it is non-project documentation, ask only for the concrete document name and
use `Doc`. If it is non-project, non-document work, use `Other` with a concrete
work name. Never create GMS without a company project and customer.

The weekly display date is the last workday of the period. A late weekly submission still displays the weekly period date, not the upload day.

The member's explicit request to generate this weekly report authorizes only this run's derived week window and selected derived fields. Pass `--session-consent --session-field work_summary` for the minimum report input. Add `project_hint`, `command_summary`, or `patch_discovery` only when the request needs them; `patch_discovery` requires `project_hint`. If there is no explicit current-run request, stop before session read, package creation, and HTTP. Do not reuse consent from a previous run or recurring automation.

## Commands

```bash
python3 "scripts/akbs_weekly_report.py" --profile <member_alias> --session-consent --session-field work_summary --prepare
python3 "scripts/akbs_weekly_report.py" --profile <member_alias> --submit-latest
python3 "scripts/akbs_weekly_report.py" --profile <member_alias> --session-consent --session-field work_summary --prepare --replace-weekly-run-id <old_run_id>
python3 "scripts/akbs_weekly_report.py" --profile <member_alias> --session-consent --session-field work_summary --prepare --weekly-facts "$CODEX_HOME/artifacts/akbs-member-ops/weekly-facts/<week>.json"
```

Future periods are blocked. For a week with no existing effective report, the
first submission is ordinary when submitted by its deadline and is a late
submission when submitted after the deadline. If an effective weekly report
already exists, every later version is a revision, never a late submission,
even when revised in a later week. Require an explicit revision targeting the
current effective run with `--replace-weekly-run-id`; retain the old version in
history.
