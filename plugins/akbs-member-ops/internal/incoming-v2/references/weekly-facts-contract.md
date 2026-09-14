# Weekly Work Facts Contract

Before creating or replacing any weekly facts file, run
`akbs_member_setup.py preflight-install-family` and require exit 0 with JSON
`status=PASS`. Do not write facts first and rely on a later prepare/doctor gate.

Use this contract only when effective AKBS daily reports and the previous weekly
ledger cannot prove every required project fact. Ask the member only for fields
listed by `weekly_fact_sources.missing_fields`.

Daily `tomorrow_plan` rows are plan context, not weekly progress evidence. A
scope present only in a daily tomorrow plan does not enter `projects[]`,
`documents[]`, or `standalone_work[]` in the weekly facts. The latest plan may
be attached only to a matching scope already proven by actual daily work or the
effective previous-week ledger.

Write the completed JSON under:

```text
$CODEX_HOME/artifacts/akbs-member-ops/weekly-facts/<week-range>.json
```

Do not write runtime facts into the plugin, skill, or plugin-cache directory.

```json
{
  "schema": "akbs-weekly-work-facts-v6",
  "week_range": "20260601-20260607",
  "projects": [
    {
      "project": "TVE1086U",
      "customer": "青鸾云",
      "work_type": "Patch",
      "project_role": "主责",
      "requirement_date": "2026-05-18",
      "requirement_source": "CR",
      "requirement_structure": {"demand": 7, "migration": 8, "bug": 15},
      "completed_this_week": {"demand": 3, "migration": 5},
      "remaining": {"demand": 4, "migration": 3, "bug": 12},
      "ledger": {
        "schema": "akbs-weekly-project-ledger-v2",
        "opening": true,
        "baseline_package_key": "",
        "baseline_week_range": "",
        "project_completed": {"demand": 3, "migration": 5},
        "changes": {
          "added": {},
          "reopened": {},
          "closed_without_change": {"bug": 1},
          "removed": {},
          "transferred_to_bsp": {"bug": 2},
          "bsp_closed": {}
        },
        "bsp_pending": {"bug": 2}
      },
      "completed_items": ["完成系统接口联调", "移植状态栏策略补丁"],
      "remaining_items": ["完成客户验收"],
      "key_points": ["1 项无需修改关闭，2 项 Bug 已转 BSP"],
      "risks": [],
      "dependencies": ["等待客户提供验收环境"],
      "next_week_plan": ["完成客户验收并关闭剩余问题"]
    },
    {
      "project": "TVI2343R",
      "customer": "海信",
      "work_type": "App",
      "app_name": "蓝牙播放器",
      "project_role": "主责",
      "requirement_date": "2026-06-25",
      "requirement_source": "CR",
      "work_total": 10,
      "completed_this_week": 3,
      "remaining": 7,
      "ledger": {
        "schema": "akbs-weekly-project-ledger-v2",
        "opening": true,
        "baseline_package_key": "",
        "baseline_week_range": "",
        "project_completed": 3,
        "changes": {
          "added": 0,
          "reopened": 0,
          "closed_without_change": 0,
          "removed": 0,
          "transferred_to_bsp": 0,
          "bsp_closed": 0
        },
        "bsp_pending": 0
      },
      "completed_items": ["完成串口协议接入"],
      "remaining_items": ["完成整机链路验证"],
      "key_points": ["完成原生服务和产品模块接入"],
      "risks": [],
      "dependencies": ["等待客户提供正式 API"],
      "next_week_plan": ["完成整机链路验证"]
    },
    {
      "project": "TVE1065M",
      "customer": "韩富友",
      "work_type": "GMS",
      "project_role": "主责",
      "requirement_date": "2026-06-01",
      "requirement_source": "TE",
      "gms_release_type": "IR",
      "gms_target": "A14",
      "gms_cycle_status": "active",
      "gms_current_stage": "submission",
      "gms_self_test_round": 3,
      "gms_self_test_result": "passed",
      "gms_submission_count": 1,
      "gms_submission_result": "under_review",
      "week_summary": "完成第 3 轮自测并发起第 1 次正式送测。",
      "completed_items": ["完成第 3 轮自测"],
      "remaining_items": ["等待正式送测结果"],
      "key_points": ["最新自测结果已通过"],
      "risks": [],
      "dependencies": [],
      "next_week_plan": ["跟进第 1 次正式送测结果"]
    }
  ],
  "documents": [
    {
      "work_type": "Doc",
      "document_name": "Android Framework Orchestrator 功能介绍文档",
      "week_summary": "本周完成功能介绍文档整理并进入评审。",
      "completed_this_week": 1,
      "remaining": 0,
      "completed_items": ["整理组件职责、调用流程和使用边界"],
      "remaining_items": [],
      "key_points": ["统一组件职责和使用边界"],
      "risks": [],
      "dependencies": [],
      "next_week_plan": []
    }
  ],
  "standalone_work": [
    {
      "work_type": "Other",
      "work_name": "团队共用 GMS ATS 环境搭建",
      "week_summary": "完成双 Worker 压测并进入分片联调。",
      "completed_this_week": 1,
      "remaining": 1,
      "completed_items": ["完成双 Worker 压测"],
      "remaining_items": ["跨 Worker 分片联调"],
      "key_points": ["无"],
      "risks": [],
      "dependencies": ["依赖分片能力"],
      "next_week_plan": ["继续联调"]
    }
  ]
}
```

Rules:

- `week_range` must equal the generated weekly period.
- `projects[]`, `documents[]`, and `standalone_work[]` are arrays; at least one
  must be non-empty.
- `project` must be a recognized TVD/TVE/TVA/TVI company project.
- `customer` is the required direct customer. Optional `downstream_customer`
  means the direct customer's customer.
- Project `work_type` is `Patch`, `App`, `GMS`, `Doc`, or `Other`; the five visible categories
  are `Patch`, `App`, `GMS`, `Doc`, and `Other`. `App` requires `app_name`;
  other project types must not provide it. Current GMS rows require release
  type, target, cycle status, current stage, self-test round/result, and formal
  submission count/result.
  GMS/Doc/Other project rows do not carry Patch/App total or ledger fields.
- Each reporting scope appears exactly once. Patch identity is `project + direct
  customer + Patch`; App identity adds `app_name`; GMS identity adds
  `gms_release_type + gms_target`. The same project may contain one Patch row,
  multiple differently named App rows, separate IR/MR/SMR scopes, and one
  scope for each remaining category. Feature names remain
  work items and do not create additional rows.
- A non-project document belongs in `documents[]`, uses only `work_type=Doc`,
  and requires `document_name`. Other non-project work belongs in
  `standalone_work[]`, uses only `work_type=Other`, and requires `work_name`.
  GMS is always project-bound. Project/customer/App fields are forbidden in
  both non-project arrays. Historical `Document` normalizes to `Doc`.
  Non-project work does not require project
  role, requirement date, requirement source, or project total.
- Non-project `completed_this_week` and `remaining` are non-negative integer work
  counts. Positive counts require matching item arrays, and positive remaining
  requires `next_week_plan`.
- `project_role` is required and must be `主责` or `协作`.
- `requirement_date` is required and uses `YYYY-MM-DD`.
- `requirement_source` is required and must be one of `CR`, `TL`, `PM`, `TE`,
  or `BSP`. Do not infer it from daily-report wording.
- `completed_this_week` and `remaining` are required for every Patch/App member. For a
  Patch row they are category objects. For an App row they are non-negative
  integers without Patch categories. Project GMS/Doc/Other use progress and
  item arrays instead of Patch/App counts.
- GMS self-test rounds and formal submission attempts are independent
  cumulative facts. Several self-test rounds may precede one submission; the
  first submission may pass. A submission stage requires the latest self-test
  result `passed`; when submission returns, switch the current stage back to
  `self_test`. Keep discovered problems and fixes in ordinary progress/item
  arrays rather than inventing a processing-cycle or issue subsystem.
- A Patch main requires `requirement_structure`; an App main requires integer
  `work_total`. Collaborators may omit the corresponding total.
- Patch 项目总量、本周完成和 Android 当前剩余只使用 `demand`、
  `migration`、`bug`。`BSP` 是责任状态，不是事项类型；转 BSP 后保留原始
  需求/移植/Bug 分类，并写入 `ledger.bsp_pending`。
- `需求` means a customer requirement the team has not implemented before;
  `移植` means reusing or porting a requirement already implemented before;
  `Bug` means defect work.
- `ledger` is required for every v6 Patch/App project row. Codex writes it; members do not
  hand-edit the JSON.
- A main row for a new scope uses `opening=true`, provides the initial total,
  and leaves the baseline fields empty. Existing scopes use `opening=false`
  and bind `baseline_package_key` and `baseline_week_range` to the current
  effective previous-week report.
- Only the main may provide non-zero ledger changes. Collaborators keep every
  change at zero and report only personal completion and remaining work.
- `project_completed` is the main-confirmed sum of this week's completion from
  the main and every collaborator. It cannot be smaller than the main's own
  completion. The team reporter independently checks it against submitted
  member rows.
- `added` increases project total and Android remaining. `reopened` increases
  Android remaining without changing total. `closed_without_change` closes an
  accepted item without counting it as Android completion. `removed` removes
  an open duplicate/invalid/out-of-scope item from both total and remaining.
  `transferred_to_bsp` removes work from Android remaining and adds it to BSP
  tracking. `bsp_closed` closes BSP-tracked work without adding Android
  completion.
- Existing-scope totals and remaining counts are calculated and checked as:

```text
current total = previous total + added - removed
current Android remaining = previous Android remaining + added + reopened
                            - project_completed - closed without change
                            - removed - transferred to BSP
current BSP pending = previous BSP pending + transferred to BSP - BSP closed
```

- Explicit legacy v5/v4/v3 facts may open a scope only when no previous scope exists.
  They cannot replace an existing scope because that would bypass the previous
  baseline; use v5 instead.
- Automatic rolling writes unmatched existing-scope work to
  `weekly_fact_sources.scope_change_candidates`. Ask the main to classify only
  those exact items before producing v6 facts.
- A positive count line must contain at least one positive `demand`,
  `migration`, or `bug`.
- Zero categories may be omitted. The generated line total is always the sum
  of its categories.
- Old `custom` or `定制` counts are invalid. They cannot be split into
  `需求` and `移植` automatically; the member must confirm the split.
- App ledger changes use non-negative integers. App does not support BSP
  tracking. Patch and App quantities are never added together.
- `key_points` records external project news, scope changes, or a key
  difficulty overcome this week. Use `["无"]` when there is none.
- Current effective daily `key_points[]` are merged and deduplicated by work
  scope as the default weekly key points. Do not carry the previous week's key
  points forward.
- Daily `dependencies[]`, legacy dependency keyword matches, and unresolved
  dependencies from the previous weekly report are candidates, not final
  weekly facts. Review `weekly_fact_sources.attention_review_candidates` with
  the member and write the confirmed current dependencies here. An explicit
  empty `dependencies[]` means the member confirmed that none remain.
- When completed or remaining count is positive, provide the corresponding
  item list. When remaining is positive, provide `next_week_plan`.
- When there is no next-week action, use `next_week_plan: []`. Do not use `无`,
  `暂无`, or another placeholder; the Markdown plan section omits that project.
- Markdown and `report_view.json` are regenerated from this same object. Do not
  repair only one representation.

Generate with:

```bash
python3 "<akbs-weekly-report skill>/scripts/akbs_weekly_report.py" \
  --profile <member_alias> weekly \
  --session-consent --session-field work_summary \
  --weekly-facts "$CODEX_HOME/artifacts/akbs-member-ops/weekly-facts/<week-range>.json" \
  --prepare
```

If an earlier local package for the same week already exists, use the explicit
replacement option named by the duplicate guard.
