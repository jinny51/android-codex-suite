# Report Rules

## Session Filtering

- Read sessions only after explicit consent for this report run. The report date or week is the exact time window, and only the selected `work_summary`, `project_hint`, `work_scope_hint`, `command_summary`, or `patch_discovery` fields may be derived.
- Keep only sanitized derived summaries and minimal source session IDs. Do not retain thread titles, cwd, raw messages, raw commands, clipboard content, environment values, credentials, paths, or temporary extraction files.
- Skip automation sessions, empty sessions, report self-test sessions, and pure report-maintenance sessions.
- Keep cross-day sessions when messages on the target date contain real work.
- Discover cross-day sessions by message timestamp and file activity, not only
  by the directory in which a session was originally created.
- Keep sessions that produced patches, completed diagnosis, finished validation, or delivered a usable conclusion.
- Daily and weekly reports are project-work summaries, not chat title summaries.
- Skip or downgrade Codex system text, approval history, plugin install/update chatter, report-generation chatter, SSH key prompts, file-upload captions, and pure local workspace maintenance unless the same session contains a clear project anchor and real engineering work.
- Do not use local Codex worktree names, plugin cache paths, `.codex` paths, generic Android branch names, or temporary file labels as project names.

## Project Anchors

- Prefer project anchors in this order:
  1. remote Android source path used in the session,
  2. remote git branch or local mounted source branch,
  3. build or lunch target,
  4. patch file name, patch readme, or changed source path,
  5. explicit TVD/TVE/TVA/TVI project number in the conversation.
- Normalize TVD/TVE/TVA/TVI project numbers before grouping report items.
- Keep trailing business text out of the project name:
  - `TVE1086U整体项目交接` -> project `TVE1086U`, item `整体项目交接`.
  - `TVE1091UAOC移动高清` -> project `TVE1091U`, item or label `AOC移动高清`.
  - `TVA10A2R_camera_fix` -> project `TVA10A2R`, source context `camera_fix`.
- If no project anchor can be identified, use `未识别项目` only when the content is still real work. Do not invent a project from directory names.
- For daily generation, ask only for the missing project + direct customer
  identity, then continue after the member supplies it. Also remind the member
  of the normal Codex workflow: create the project first, then create development
  sessions under that project. Do not ask for weekly ledger fields during daily
  generation.
- Daily project `work_type` is `Patch`, `App`, `GMS`, `Doc`, or `Other`; App
  additionally requires `app_name`, while current GMS requires release type,
  target, cycle, self-test, and submission facts. Resolve it from explicit member context first, then from
  high-confidence development evidence such as source/module paths, changed
  files, build commands, patch artifacts, and APK/AAB outputs. Never infer it
  from vague item wording. Ask only when evidence conflicts or remains
  incomplete. This is a daily work-scope field, not a weekly ledger field.

## Report Summary

- Build the daily or weekly overview from accepted project items.
- Do not form the overview by concatenating session names, last user messages, or command output.
- The overview should mention main project numbers, main work areas, completion state, patch output, and risk or missing evidence.

## Progress

- `已完成`: code completed, patch generated, verified, submitted, or delivered.
- `已解决`: issue located and fixed with validation.
- `待验证`: change or plan is ready, waiting for device/customer validation.
- `处理中`: still diagnosing or implementing.
- `阻塞`: blocked by device, logs, permissions, requirements, or dependency.
- `已归档`: historical/documentation work that needs no follow-up.
- Preserve explicit progress when available, such as `80%`, `0%->100%`, or `40%~60%`.
- For incomplete work, include the current progress and reason, for example `80%，待验证` or `40%，处理中`.

## Daily Template

```markdown
# YYYYMMDD_成员名_日报

日报目的：讲清楚今天干了什么、怎么干的、结果是什么。日报不是周总结，不要写成项目总账。

## 一、今日概况

### **项目名** 客户名 [客户的客户]｜Patch

- 类型：Patch / App / GMS 中选择一个
- App 名称：仅 App 必填
- 今日主题：今天主要处理了什么。
- 当前结果：今天处理到什么程度。

## 二、今日工作

### **项目名** 客户名 [客户的客户]｜Patch / App：App 名称

#### 1. 事项名称

做了什么：
- 具体做了哪些工作。

怎么做的：
- 通过什么方式排查、修改、验证或推进。

结果：
- 当前结果是什么，是否还要继续。

状态：
- 已完成 / 处理中 / 待验证 / 阻塞（只选一个）

## 三、重点说明

### **项目名** 客户名 [客户的客户]｜Patch / App：App 名称
- 项目外部消息、范围变化或者今天攻克的关键难点。

## 四、依赖 / 需协调

### **项目名** 客户名 [客户的客户]｜Patch / App：App 名称
- 明确依赖对象、卡点或者需要协调的事项。

## 五、明日计划

### **项目名** 客户名 [客户的客户]｜Patch / App：App 名称
- 明天计划处理什么。

### 文档：文档名称
- 明天计划处理什么。
```

日报按工作范围分块。五个分类固定为 Patch、App、GMS、Doc、Other。Patch
表示系统源码定制，App 表示应用或 demo 开发，GMS 表示项目认证测试，Doc 表示
文档，Other 表示其他具体工作。分类不决定数组归属：凡挂靠项目都写入 `projects[]`。
同一项目可有一个 Patch 和多个不同 App；App 必须写 App 名称。明日计划与
今日工作独立，使用另一组 `projects[]`、`documents[]` 和
`standalone_work[]`，每行只写身份与 `plan_items[]`。明日项目或事项无需在
今日工作中出现；“尚未开始，明日执行”只能写入明日计划，不能伪造一条今日
工作。未完成状态也不会自动生成计划。三个计划数组都为空时显示 `无。`。日报
不填写项目角色、需求时间、需求来源、项目总量或剩余量。

每个范围都包含 `key_points[]` 和 `dependencies[]`。前者对应“重点说明”，
记录明确的项目外部消息、范围变化或当天攻克的关键难点；后者对应“依赖 /
需协调”，记录明确的外部依赖或协调事项。两个数组均允许为空；不得因为普通
未完成事项而臆测内容。某一章节全部为空时，Markdown 写 `无。`。

类型判定优先使用成员明确说明，其次使用高置信度开发证据。SystemUI、Launcher、
Settings 和 Framework 服务等系统源码修改属于 Patch；独立交付的应用或 demo
属于 App。证据一致时自动填写并可按范围拆分；证据冲突、仅有模糊事项文字或
无法确定 App 名称或非项目工作名称时才询问成员。无项目 Doc 使用
`documents[]`；无项目、非文档工作使用 `standalone_work[]/Other`。GMS 必须
挂靠项目，不得把共享 ATS 环境写成独立 GMS。周报继承日报范围，不重新猜测。

## Weekly Template

Weekly project facts are not derived by counting sessions. Resolve current
effective daily reports from AKBS, carry the current previous-week project
ledger forward, apply report replacement chains, and use sessions only to
supplement work absent from effective daily reports. `weekly_fact_sources`
records this provenance. Missing ledger fields block upload and are completed
through the explicit `akbs-weekly-work-facts-v6` contract. Requirement
source is created when the member receives the demand and must not be inferred
from daily-report wording.

For current-format daily reports, merge `key_points[]` by work scope as this
week's key-point candidates. Collect `dependencies[]` by scope, but require the
member to confirm which dependencies remain active before weekly submission.
Legacy keyword matches and previous-week dependencies are compatibility
candidates only; they are never silently accepted as current weekly facts.

```markdown
# YYYYMMDD-YYYYMMDD_成员名_周报

周报目的：按项目汇总这一周完成多少、还剩多少、风险和依赖是什么、下周怎么收敛。周报不复述每天流水。

## 一、本周概况

### **项目名** 客户名 [客户的客户]

本周围绕 **项目名** 客户名 [客户的客户] 项目推进：简短说明本周主要处理的方向。

- 类型：Patch / App / GMS 中选择一个
- App 名称：仅 App 必填
- GMS 送测类别：仅 GMS 必填，IR / MR / SMR / ESMR / EMR / LR
- GMS 目标版本：仅 GMS 必填
- GMS 周期/阶段：进行中时为自测或送测；结束后阶段为空
- GMS 自测：累计轮次 + 最新结果
- GMS 送测：累计次数 + 最新结果
- 项目角色：主责 / 协作 中选择一个
- 需求时间：2026-06-18
- 需求来源：CR / TL / PM / TE / BSP 中选择一个
- 本周变化：新增 2 项（需求 2）、转 BSP 2 项（Bug 2）（仅主责确认）
- 共 18 项：需求 6、移植 4、Bug 8（主责显示，协作省略）
- 本周完成 5 项：需求 2、移植 2、Bug 1
- 当前剩余 3 项：需求 1、移植 2
- BSP 跟踪 2 项：Bug 2

`需求`表示团队以前没有做过的客户需求，`移植`表示复用或移植团队以前做过的
客户需求，`Bug`表示缺陷处理。不要保留`定制`父分类。Bug 修复补丁的移植仍计
`Bug`。`BSP`是责任状态，不是事项类型；转 BSP 后保留原需求/移植/Bug 分类，
从 Android 当前剩余中扣除并单独跟踪，不得作为本周完成项。
旧`定制`计数不能自动拆分，必须由成员确认。正数计数行至少包含一项`需求`、
`移植`或`Bug`，为 0 的分类省略，行总数必须等于分类之和。

新项目初始总量及后续新增、重新打开、无需修改关闭、移出、转 BSP 和 BSP 关闭
只能由主责确认。协作只填写个人完成和个人剩余；发现文档外事项时先标记待主责
确认，不得自行修改项目总量。Codex 绑定上一份有效周报并自动计算当前总量和剩余；
显式事实也不得绕过上周基线。主责在机器台账中同时确认项目全员本周完成量，
管理端必须再与主责、协作各自完成量之和核对；成员 Markdown 仍只显示个人完成量。

以上分类只用于 `Patch`。`App` 使用独立的简单计数，不填写需求、移植、Bug
或 BSP 分类：主责填写`共 N 项`，所有成员填写`本周完成 N 项`和`当前剩余 N 项`。

多项目时，在“本周概况”下按项目重复上述块；不要使用“总盘子”这类说法，也不要用大表格堆字段。

结构化 `projects[]` 按统计对象唯一：Patch 使用“项目 + 直接客户 + Patch”，App
使用“项目 + 直接客户 + App + App 名称”，GMS 使用“项目 + 直接客户 + GMS +
送测类别 + 目标版本”。同一正式项目可以有一个 Patch、多个不同 App，以及独立的
IR/MR/SMR 等 GMS 范围。GMS 自测轮次与正式送测次数独立累计；送测前最新自测必须
通过，退回后回到自测。问题与修复仍写普通工作项，不新增处理周期或问题子系统。

无项目文档写入 `documents[]/Doc` 并使用 `document_name`；其他无项目工作写入
`standalone_work[]/Other` 并使用 `work_name`。二者不填写项目、客户、项目角色、
需求时间、需求来源或项目总量，并使用简单的
本周完成/当前剩余计数，并沿用完成详情、剩余详情、重点说明、风险/依赖和下周计划。
GMS、Doc、Other 数量不得并入 Patch/App 项目需求统计。

## 二、项目详情

### **项目编号** 客户名称 [客户的客户]

#### 1. 本周完成

列出本周已完成事项。

#### 2. 当前剩余

列出当前剩余事项。

#### 3. 重点说明

说明项目外部消息、范围变化或本周攻克的关键难点；没有则写“无”。

#### 4. 风险 / 依赖

风险：超过 3 天无进展的事项；没有则写“无超过 3 天无进展事项。”
依赖：依赖外部推进的事项；没有则写“无外部依赖事项。”

## 三、下周计划

按项目说明下周优先处理什么、剩余问题预计哪周完成。
没有下周动作的项目不在本节生成项目块，禁止用“无”或“暂无”占位。
```

Markdown 中项目名在所有出现位置都加粗，包括标题、概况正文、完成项、剩余项、
风险、依赖和计划；只加粗项目名，不连带客户或客户的客户。该规则只作用于
`reports/*.md`。`report_view.json` 的 `project`、`material_name`、
`material_summary` 和其他结构化字段保持纯文本，不写 `**`。

## UI Read Model

Daily and weekly reports are the primary human-readable product. The package also writes `materials/display/report_view.json` as a structured UI read model for cards, lists, and detail views. The read model is generated from the same report inputs as `reports/daily.md` or `reports/weekly.md`; it is not a separate evidence or AI layer and must not contradict the report body.

```json
{
  "kind": "report_view",
  "payload": {
    "schema": "akbs-report-view-human-v1",
    "report_type": "daily",
    "material_name": "TVE1086U（青鸾云）",
    "material_summary": "TVE1086U：今日处理锁屏鼠标位置刷新、云电脑崩溃排查。",
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
        "key_points": [],
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
the direct customer's customer. For example, `TVE1091U AOC 福建移动高清`
becomes `project=TVE1091U`, `customer=AOC`, and
`downstream_customer=福建移动高清`. Do not collapse the two customer levels
into aliases. A two-segment identity such as `TVE1086U 青鸾云` remains valid.

Report card fields are authoritative:

- `material_name`：项目 + 客户链路；独立文档写 `文档工作：<文档名称>`。多项目写 `TVE1086U（青鸾云）、TVE1091U（AOC → 福建移动高清）`，超过 3 个项目时只列前 3 个并追加 `等 N 个项目`。
- `material_summary`：日报写各项目或文档的“今日主题”；周报写各项目或文档的“本周完成、剩余、风险/依赖”。它是卡片小字，不要拿日期、成员名或包路径充当摘要。
- Daily today project rows contain `project/customer/[downstream_customer]/work_type/[app_name]/[gms_release_type/gms_target/gms_cycle_status/gms_current_stage/gms_self_test_round/gms_self_test_result/gms_submission_count/gms_submission_result]/today_topic/current_result/work_items/key_points/dependencies`; every work item contains `name/did/how/result/status` and status is one of `已完成/处理中/待验证/阻塞`.
- Daily `tomorrow_plan` contains parallel project, document, and standalone arrays. Plan rows carry only scope identity plus `plan_items[]`; tomorrow-only scopes never count as today progress or weekly progress.
- Weekly project rows contain `project/customer/[downstream_customer]/work_type/[app_name]/[GMS progress fields]/project_role/week_summary/requirement_date/requirement_source/[requirement_structure/work_total]/completed_this_week/remaining/completed_items/remaining_items/key_points/risks/dependencies/next_week_plan`.
- Daily and weekly non-project Doc rows live in `documents[]` and use `document_name`; non-project Other rows live in `standalone_work[]` and use `work_name`. GMS never lives outside `projects[]`.
- The current read model does not emit `display_title`, `ui_card`, `one_line_summary`, `project_ledgers`, `weekly_progress_summary`, or `weekly_detail_sections`.

Daily scope identity and work-item assignment flow into weekly history so the
weekly generator preserves the daily Patch/App/GMS/Doc/Other scope. Old daily rows without
scope fields remain readable as history, but they cannot prove the weekly type
and therefore leave an explicit fact gap.

## Submission Gates

- Classify by report identity before upload date. With no effective report, a
  first package is on-time or late according to the deadline. With an effective
  report, every later package is a revision regardless of date.
- A revision must explicitly target the current effective run. It writes
  `report_submission_intent=revision`, `replacement_for_run_id`, and
  `supersedes`; a first package writes `report_submission_intent=initial`.
  Never call a revision a late submission or silently replace an existing
  report.
- `--prepare` may leave a local package for diagnosis, but returns a non-zero
  exit code when `local-check.json` is `FAIL`; callers must not report success.
- `--upload` and `--submit-latest` run the same validation before HTTP. Invalid
  packages remain local.
- Daily facts, weekly facts, Markdown, and `report_view.json` are linked by a
  `report_render_binding`. Local validation also rerenders Markdown from
  `report_view.json` and requires exact equality.
- Correct structured facts and regenerate. Do not repair only Markdown or only
  `report_view.json`.
