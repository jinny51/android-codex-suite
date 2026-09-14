# Daily Work Facts Contract

Before creating or replacing any daily facts file, run
`akbs_member_setup.py preflight-install-family` and require exit 0 with JSON
`status=PASS`. Do not write facts first and rely on a later prepare/doctor gate.

Use this contract only when authorized development evidence cannot resolve a
daily work scope, when the inferred scope needs correction, when explicit key
points or dependencies must be preserved, or when the member explicitly
overrides it. Normal high-confidence Patch/App/GMS/Doc/Other inference with no
explicit attention facts does not require this file. Write it under:

```text
$CODEX_HOME/artifacts/akbs-member-ops/daily-facts/<report-date>.json
```

Do not write runtime facts into the plugin source, skill, or plugin-cache directory.

```json
{
  "schema": "akbs-daily-work-facts-v4",
  "report_date": "2026-08-10",
  "projects": [
    {
      "project": "TVE1086U",
      "customer": "青鸾云",
      "work_type": "Patch",
      "key_points": ["客户确认新增验收范围"],
      "dependencies": ["等待 BSP 提供联调固件"]
    },
    {
      "project": "TVE1086U",
      "customer": "青鸾云",
      "work_type": "App",
      "app_name": "设备管理工具",
      "key_points": [],
      "dependencies": []
    },
    {
      "project": "TVE1065M",
      "customer": "韩富友",
      "work_type": "GMS",
      "gms_release_type": "IR",
      "gms_target": "A14",
      "gms_cycle_status": "active",
      "gms_current_stage": "self_test",
      "gms_self_test_round": 2,
      "gms_self_test_result": "passed",
      "gms_submission_count": 0,
      "gms_submission_result": "not_submitted",
      "key_points": [],
      "dependencies": []
    }
  ],
  "documents": [
    {
      "work_type": "Doc",
      "document_name": "Android Framework Orchestrator 功能介绍文档",
      "work_items": [
        {
          "name": "完成功能介绍文档",
          "did": ["整理组件职责、调用流程和使用边界"],
          "how": ["核对实现代码和现有说明后按功能模块重写"],
          "result": "文档已完成并可供评审",
          "status": "已完成"
        }
      ],
      "key_points": [],
      "dependencies": []
    }
  ],
  "standalone_work": [
    {
      "work_type": "Other",
      "work_name": "团队共用 GMS ATS 环境搭建",
      "work_items": [
        {
          "name": "完成双 Worker 验证",
          "did": ["完成 CTS 多机协同验证"],
          "how": ["运行测试并核对 Worker 状态"],
          "result": "基础链路通过，分片联调中",
          "status": "处理中"
        }
      ],
      "key_points": ["双 Worker 基础链路已通过"],
      "dependencies": []
    }
  ],
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
    "documents": [
      {
        "work_type": "Doc",
        "document_name": "测试计划文档",
        "plan_items": ["补齐测试范围说明"]
      }
    ],
    "standalone_work": [
      {
        "work_type": "Other",
        "work_name": "团队共用 GMS ATS 环境搭建",
        "plan_items": ["继续跨 Worker 分片联调"]
      }
    ]
  }
}
```

Rules:

- `report_date` must equal the generated daily date.
- `projects[]`, `documents[]`, and `standalone_work[]` describe today's actual
  work; at least one must be non-empty. A plan alone cannot replace a daily
  work record.
- In `projects[]`, `project` and direct `customer` are required. Optional
  `downstream_customer` means the direct customer's customer. `customer`
  contains only the direct customer. Do not flatten a two-level chain into one
  whitespace-separated `customer` value; local validation rejects it until the
  two fields are separated.
- Project `work_type` is `Patch`, `App`, `GMS`, `Doc`, or `Other`. The five visible categories
  are `Patch`, `App`, `GMS`, `Doc`, and `Other`.
  Explicit facts override automatic inference. Never infer from vague work-item
  wording alone.
- `App` requires `app_name`; other project types must not provide it.
- A current GMS row requires `gms_release_type` (`IR`, `MR`, `SMR`, `ESMR`,
  `EMR`, or `LR`) and an Android major-version `gms_target` written as
  `A<digits>` (for example `A14`). It also records cycle status,
  current stage, cumulative self-test round/result, and cumulative formal
  submission count/result. Self-test rounds and submissions are independent:
  several self-test rounds may lead to the first submission, and that first
  submission may pass. Entering `submission` requires the latest self-test
  result to be `passed`; a returned submission goes back to `self_test`.
  Problems and fixes remain ordinary `work_items[]`; do not create a separate
  processing-cycle or issue subsystem.
- In `documents[]`, `work_type` is only `Doc` and a concrete `document_name` is
  required. In `standalone_work[]`, `work_type` is only `Other` and a concrete
  `work_name` is required. Neither array carries project/customer/App fields.
  GMS is always project-bound. Historical `Document` normalizes to `Doc`.
  Do not use “文档” as a fake company project code.
- Scope identity is project/customer + work type; App additionally includes its
  name, and GMS additionally includes release type + target. The same project
  may therefore carry separate IR, MR, or SMR scopes without merging them.
- Every scope carries `key_points[]` and `dependencies[]`. Both fields are
  arrays and may be empty. `key_points` records explicit external project news,
  scope changes, or a key difficulty overcome today. `dependencies` records
  explicit external dependencies or coordination needs. Do not infer either
  field merely because work is unfinished.
- Document scope identity is its concrete document name. Merge repeated work on
  the same document instead of creating duplicate rows.
- When a project has only one scope, `work_items`, `today_topic`, and
  `current_result` may be omitted; the generator fills them from the authorized
  session-derived daily work.
- When the same project has multiple scopes, each scope must explicitly carry
  its own `work_items[]` so work cannot be assigned to the wrong Patch or App.
- Every work item contains `name`, non-empty `did[]`, non-empty `how[]`,
  `result`, and `status`. Status is exactly `已完成`, `处理中`, `待验证`, or
  `阻塞`.
- `tomorrow_plan` is required and contains parallel `projects[]`, `documents[]`,
  and `standalone_work[]` arrays. These arrays may all be empty.
- Every tomorrow-plan row has a non-empty `plan_items[]`. Project plans use the
  same project/customer/work-type identity rules as today; Doc and Other plans
  use the same non-project name rules. A plan does not need a matching today
  scope.
- A GMS tomorrow-plan row includes only `gms_release_type` and `gms_target`
  beside its ordinary project identity and `plan_items[]`. It does not claim a
  cycle status, stage, self-test round, or submission count before work occurs.
- Tomorrow-plan rows must not contain `today_topic`, `current_result`,
  `work_items`, `key_points`, `dependencies`, `tomorrow_focus`, or `status`.
  Likewise, current v4 today rows must not contain `tomorrow_focus`.
- Do not derive plans automatically from an unfinished status. “尚未开始，明日
  执行” belongs only in `tomorrow_plan`; it must never be represented as a
  completed, in-progress, or otherwise fabricated today work item.
- Codex writes this JSON from the current development evidence and only asks the
  member for facts that remain ambiguous. Do not ask the member to hand-edit
  JSON, Markdown, or `report_view.json`.
- Markdown and `report_view.json` are regenerated from the normalized object.
  Editing only one representation invalidates the render binding and blocks
  submission.

Generate with:

```bash
python3 "<akbs-daily-report skill>/scripts/akbs_daily_report.py" \
  --profile <member_alias> daily \
  --session-consent --session-field work_summary --session-field command_summary \
  --session-field project_hint --session-field work_scope_hint \
  --daily-facts "$CODEX_HOME/artifacts/akbs-member-ops/daily-facts/<report-date>.json" \
  --prepare
```
