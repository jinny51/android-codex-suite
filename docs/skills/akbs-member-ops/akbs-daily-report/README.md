# akbs-daily-report

> GitHub 说明页。Runtime skill 文件位于 [../../../../plugins/akbs-member-ops/skills/akbs-daily-report](../../../../plugins/akbs-member-ops/skills/akbs-daily-report)。

成员个人日报入口。它只负责生成、修订、检查和提交日报包（daily report package），通过日报专用入口复用共享 incoming v2 内核和上传协议。

读取日报输入、创建/修改 daily-facts 或执行生成、检查、提交前，必须先运行
`akbs_member_setup.py preflight-install-family`；只有返回码 0 且 JSON
`status=PASS` 才继续，后续 doctor 不能补授权此前的 facts 写入。

日报回答“今天干了什么、怎么干的、结果是什么”。新日报包生成：

- `reports/daily.md`
- `materials/display/report_view.json`
- `materials/evidence/work_findings.json`

`report_view.json` 是同一份日报正文的 UI 读模型。五个分类固定为 `Patch`、`App`、`GMS`、`Doc`、`Other`。凡挂靠项目的今日工作都进入 `projects[]` 并保留项目和客户链；无项目文档进入 `documents[]/Doc`，其他无项目工作进入 `standalone_work[]/Other`。GMS 必须挂靠项目。历史 `Document` 兼容读取为 `Doc`。今日范围包含 `today_topic`、`current_result`、`work_items[]`、`key_points[]` 和 `dependencies[]`。

当前 GMS 项目范围还必须明确送测类别（IR/MR/SMR/ESMR/EMR/LR）和 Android 主版本；目标版本固定写成 `A<数字>`（例如 `A14`），并记录周期状态、当前自测/送测阶段、累计自测轮次与最新结果、累计正式送测次数与最新结果。自测轮次和送测次数独立累计：可以多轮自测后才第 1 次送测，第 1 次送测也可以直接通过；进入送测前最新自测必须通过，送测退回后回到自测。发现的问题和修复仍写普通 `work_items[]`，不额外建立“处理周期”或问题子系统。同一项目下不同送测类别或目标版本是不同 GMS 范围。

“明日计划”使用独立的 `tomorrow_plan`，其下同样有 `projects[]`、`documents[]` 和 `standalone_work[]`。计划行只包含范围身份和 `plan_items[]`，不包含今日主题、今日结果、工作项或状态；GMS 计划只额外写送测类别和目标版本，不预填周期、阶段、轮次或次数。明日计划可以是全新的项目、文档或其他独立工作，不要求今天已经做过；反过来，今日未完成也不会自动生成明日计划。“尚未开始，明日执行”只能归入明日计划，不能为了挂靠范围伪造一条今日工作。

日报 Markdown 在“今日工作”后增加“重点说明”和“依赖 / 需协调”。`key_points[]` 只记录明确的项目外部消息、范围变化或当天攻克的关键难点，`dependencies[]` 只记录明确的外部依赖或协调事项。两个数组均允许为空；当全部范围为空时，对应 Markdown 章节显示“无”，不会阻止提交。

日报卡片不使用日期当标题。`material_name` 写项目 + 客户链路，例如 `TVE1086U（青鸾云）` 或 `TVE1091U（AOC → 福建移动高清）`；多项目时每个项目都带自己的客户链路。`material_summary` 写今日主题，例如 `TVE1086U：今日处理锁屏鼠标位置刷新、云电脑崩溃排查。`。新包不得再写已废弃的 `report_view` 字段，例如 `display_title`、`ui_card`、`one_line_summary`、顶层 `work_items`、`risks` 或 `outputs`。

日报项目行必须同时有公司项目名和直接客户。成员可以说 `TVE1086U 青鸾云`，也可以说 `TVE1091U AOC 福建移动高清`；后者识别为项目 `TVE1091U`、直接客户 `AOC`、客户的客户 `福建移动高清`。结构化 `customer` 只允许直接客户，客户的客户必须写入 `downstream_customer`；把两级客户拼进一个 `customer` 会被本地校验拦住。直接客户和下游客户不得跨层当作别名。缺项目或直接客户时提交同样会被拦住。

Codex 优先采用成员明确分类，否则根据源码、产物和工作语义进行高置信度判断；只有冲突或名称缺失时才询问。具体项目认证测试使用 GMS；团队共用 ATS 环境等无项目工作使用 `standalone_work[]/Other`。明日计划按同样的挂靠规则分类，但与今日工作独立。

正常生成先由共享内核自动识别并按范围绑定工作项。`$CODEX_HOME/artifacts/akbs-member-ops/daily-facts/` 下的 `akbs-daily-work-facts-v4` 只用于补齐未决字段、纠正判定、记录独立明日计划或成员显式覆盖；显式事实优先于自动判定。一个混合会话若不能可靠绑定各事项，必须补齐范围事实，不得猜测拆分。

`reports/daily.md` 中项目名每次出现都加粗，不限于项目标题；只加粗项目名，例如 `**TVE1091U** AOC 福建移动高清`，客户链保持普通文字。`report_view.json` 是结构化数据，项目及摘要字段不写 Markdown 标记。

日报按独立事项拆分同一会话中的多项工作，并合并不同会话里重复推进的同一事项。`怎么做的`来自授权会话中的实际排查、修改、构建、部署或验证信息；命令只转换成方法摘要，不展示原始命令，也不再使用“根据 Codex 会话记录整理”之类固定套话。

Markdown、`report_view.json` 和日报事实由 `report_render_binding` 绑定。只手改其中一份会导致校验失败；应修改结构化事实后重新生成。`--prepare` 本地校验失败时返回非零，`--submit-latest` 在 HTTP 前再次执行相同校验。

提交分类先看同一成员、同一日期是否已有有效日报：没有旧日报时，截止前首次提交是正常提交，截止后首次提交才是补交；已有有效日报后再次提交一律叫修订，不再叫补交。修订必须明确指向当前有效版本，新版生效后旧版保留在历史记录中。

执行生成或提交前，Codex 应先从当前请求和可见会话上下文里找项目名 + 直接客户；找不到时只要求成员补这两个最小字段，并提示正确流程：`当前会话未关联项目，请补充项目名和客户名。例如：TVE1086U 青鸾云；如有客户的客户：TVE1091U AOC 福建移动高清。建议后续先创建项目，再在项目下创建开发会话。` 不要把项目角色、需求时间、需求来源、项目总量或剩余量等周报字段塞进日报追问。

成员本次明确要求生成日报，只授权本次日报日期和本次选择的派生字段。默认使用 `work_summary`、`command_summary`、`project_hint` 和 `work_scope_hint`，以便提取工作内容、处理方法和安全的源码范围提示；原始路径不进入报告或证据文件。只有确有需要时才增加 `patch_discovery`，且它要求 `project_hint`。没有本次明确请求时，必须在读取 session、打包和 HTTP 前停止；不得复用旧授权或给定时任务长期授权。

常用命令：

```bash
python3 "scripts/akbs_daily_report.py" --profile <member_alias> --session-consent --session-field work_summary --session-field command_summary --session-field project_hint --session-field work_scope_hint --prepare
python3 "scripts/akbs_daily_report.py" --profile <member_alias> --submit-latest
python3 "scripts/akbs_daily_report.py" --profile <member_alias> --session-consent --session-field work_summary --session-field command_summary --session-field project_hint --session-field work_scope_hint --daily-facts "$CODEX_HOME/artifacts/akbs-member-ops/daily-facts/<report-date>.json" --prepare
python3 "scripts/akbs_daily_report.py" --profile <member_alias> --session-consent --session-field work_summary --session-field command_summary --session-field project_hint --session-field work_scope_hint --prepare --replace-daily-run-id <old_run_id>
```
