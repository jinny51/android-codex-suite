# akbs-weekly-report

> GitHub 说明页。Runtime skill 文件位于 [../../../../plugins/akbs-member-ops/skills/akbs-weekly-report](../../../../plugins/akbs-member-ops/skills/akbs-weekly-report)。

成员个人周报入口。它只负责生成、修订、检查和提交周报包（weekly report package），通过周报专用入口复用共享 incoming v2 内核和上传协议。成员侧不生成、不理解团队汇总报告；管理端汇总是管理员侧能力。

读取周报输入、创建/修改 weekly-facts 或执行生成、检查、提交前，必须先运行
`akbs_member_setup.py preflight-install-family`；只有返回码 0 且 JSON
`status=PASS` 才继续，后续 doctor 不能补授权此前的 facts 写入。

周报回答“这一周完成多少、还剩多少、风险和依赖是什么”。日报记录当天做了什么、怎么做、结果是什么；周报不复述每天流水，只做一周总结。新周报包生成：

- `reports/weekly.md`
- `materials/display/report_view.json`
- `materials/evidence/work_findings.json`
- `materials/evidence/weekly_fact_sources.json`

周报统计周期固定为周一至周日，起止日期都纳入统计；首次补交日期或后续修订日期都不改变报告所属周期，也不能导致周一日报被漏掉。周报事实优先读取 AKBS 中本周当前有效日报和上一周当前有效周报；API 不可用时回退到本机 `submitted` 包，并按修订链只选当前叶节点。Codex session 只补充有效日报未覆盖的事项，不能再把“一段会话”统计成“一项需求”。跨日长会话按消息日期和文件活跃时间补扫。

当前日报已携带项目范围、无项目 Doc 和无项目 Other 的数组归属。周报按该归属归并，不再根据事项文字重新猜类型；GMS 始终是项目范围。

日报顶层 `tomorrow_plan` 只提供计划参考，不代表项目已经开工，也不构成本周进展。仅出现在明日计划中的项目、文档或独立工作不会生成周报范围、完成量、剩余量或项目总量；只有同一范围已经由本周实际日报或上一周有效台账证明存在时，最新计划才可作为下周计划参考。

新版日报还为每个范围提供可为空的 `key_points[]` 和 `dependencies[]`。周报按范围汇总并去重本周重点说明，不再沿用上周重点说明；日报依赖、旧日报关键词命中和上周未解除依赖只作为候选，写入 `weekly_fact_sources.attention_review_candidates`，由成员确认仍然有效后再形成最终周报依赖。

如果类型、App 名称（仅 App）、GMS 送测类别/Android 主版本/阶段事实（仅 GMS，目标版本固定为 `A<数字>`，例如 `A14`）、文档名称（仅 Doc）、项目角色、需求时间、需求来源、主责总量、项目流转或剩余事项身份仍缺失，本地检查会列出准确字段并阻止上传。成员只确认缺失事实，Codex 将其写入 `$CODEX_HOME/artifacts/akbs-member-ops/weekly-facts/` 下的 `akbs-weekly-work-facts-v6` JSON，再通过 `--weekly-facts <path>` 重新生成；不要求成员手改 JSON 或 Markdown。v6 显式事实必须绑定上一份有效周报，不能绕过上周台账重新填写总量。

`report_view.json` 是同一份周报正文的 UI 读模型。Patch/App 项目继续使用项目台账；项目 GMS 使用送测类别 + 目标、周期状态、自测轮次/结果、正式送测次数/结果和普通进展信息，项目 Doc/Other 也保留项目客户身份并使用进展信息；无项目 Doc 使用 `documents[]`，无项目 Other 使用 `standalone_work[]`。GMS、Doc、Other 不并入 Patch/App 数量。周报包只归档，不进入知识库沉淀候选。

五个分类固定为 `Patch`、`App`、`GMS`、`Doc`、`Other`。Patch/App 台账规则保持不变；GMS 项目不填写 Patch/App 总量，标题使用“项目 + 客户｜GMS：送测类别（`A<数字>` 目标版本）”。自测轮次和送测次数独立累计；送测前自测必须通过，送测退回后回到自测，问题/修复仍是普通进展事项。无项目 Doc/Other 使用各自数组，禁止独立 GMS。生成和 `--submit-latest` 都会在 HTTP 前重新执行校验。

Patch 不再保留“定制”父分类，直接使用需求、移植和 Bug：以前没做过的客户需求计需求，以前做过并复用或移植的客户需求计移植，缺陷处理计 Bug。BSP 是责任状态而不是事项类型；转 BSP 后保留原需求/移植/Bug 分类，从 Android 当前剩余中扣除，并单独显示 `BSP 跟踪`。App 使用简单总量、完成量和剩余量，不套用 Patch 分类；两类数量不得相加。

成员周报以项目块为基线。主责负责新项目初始总量及后续新增、重新打开、无需修改关闭、移出、转 BSP 和 BSP 关闭，并在机器台账中确认主责与所有协作成员的项目完成量合计；协作只报个人完成和个人剩余，不能修改项目总账。Markdown 只增加一行紧凑的 `本周变化`，仍只显示个人完成；总量、Android 当前剩余和 BSP 跟踪由上周基线自动计算。管理端再核对主责确认的项目完成量是否等于所有成员完成量之和。

`reports/weekly.md` 中项目名每次出现都加粗，包括概况正文、完成项、剩余项、风险、依赖和下周计划，不只处理标题；只加粗项目名，客户链保持普通文字。`report_view.json` 的项目与摘要字段保持纯文本，不写 Markdown 标记。

Markdown、`report_view.json` 和周报事实由 `report_render_binding` 绑定。只手改其中一份会导致校验失败；应修改结构化事实后重新生成。`--prepare` 本地校验失败时返回非零，提交前还会再次执行相同校验。

提交分类先看同一成员、同一周期是否已有有效周报：没有旧周报时，截止前首次提交是正常提交，截止后首次提交才是补交；已有有效周报后再次提交一律叫修订，不再叫补交。修订必须明确指向当前有效版本，新版生效后旧版保留在历史记录中。

周报卡片不使用周范围当标题。`material_name` 写项目 + 客户链路，例如 `TVE1086U（青鸾云）` 或 `TVE1091U（AOC → 福建移动高清）`；多项目时每个项目都带自己的客户链路。`material_summary` 写各项目本周完成、剩余、风险或依赖，例如 `TVE1086U：本周完成 5 项，剩余 3 项，有风险。`。

执行生成或提交前，Codex 应先从当前请求和可见会话上下文里找项目名 + 直接客户；找不到时先在会话里提示 `缺少项目名和客户名，请补充，例如：TVE1086U 青鸾云；如有客户的客户，继续写第三段，例如：TVE1091U AOC 福建移动高清。`，不要直接跑命令。

成员本次明确要求生成周报，只授权本次周范围和本次选择的派生字段。最小范围使用 `--session-consent --session-field work_summary`；只有确有需要时才增加 `project_hint`、`command_summary` 或 `patch_discovery`，其中 `patch_discovery` 还要求 `project_hint`。没有本次明确请求时，必须在读取 session、打包和 HTTP 前停止；不得复用旧授权或给定时任务长期授权。

常用命令：

```bash
python3 "scripts/akbs_weekly_report.py" --profile <member_alias> --session-consent --session-field work_summary --prepare
python3 "scripts/akbs_weekly_report.py" --profile <member_alias> --submit-latest
python3 "scripts/akbs_weekly_report.py" --profile <member_alias> --session-consent --session-field work_summary --prepare --replace-weekly-run-id <old_run_id>
python3 "scripts/akbs_weekly_report.py" --profile <member_alias> --session-consent --session-field work_summary --weekly-facts "$CODEX_HOME/artifacts/akbs-member-ops/weekly-facts/<week>.json" --prepare
```
