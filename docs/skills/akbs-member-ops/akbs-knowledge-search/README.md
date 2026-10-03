# akbs-knowledge-search

> GitHub 说明页。Runtime skill 文件位于 [../../../../plugins/akbs-member-ops/skills/akbs-knowledge-search](../../../../plugins/akbs-member-ops/skills/akbs-knowledge-search)；插件安装后的 skill 目录不包含本 README。文中的 `scripts/...`、`references/...` 指向该 runtime skill 目录。

团队知识库检索 skill。

任何搜索、本地索引读取、服务端请求、refresh 或 usage 记录写入前，必须先运行
`akbs_member_setup.py preflight-install-family` 并取得 `status=PASS`；只有纯
`--help` 可以跳过。

## 用途

该 skill 默认优先调用 AKBS 成员只读搜索接口：Case 描述功能目标和边界，Implementation 描述具体技术实现及分别声明的环境和绑定证据。服务端不可用、未授权、超时或合同不兼容时，会回退到本地知识库 JSONL 文本搜索，并明确标注 `source=local_jsonl_fallback`；本地结果不具有服务端复用分级权威。

服务端错误统一消费 `akbs-error-envelope-v1`，只显示稳定 `code`、`request_id`、类型和脱敏 message。旧版自由文本错误会明确标记为 legacy，不能据此做重试、fallback 业务判断或合并结论；错误输出不得包含 token、cookie、请求正文、session 原文、路径或底层异常文本。

它的价值是让成员或其他 skill 在重新分析、重新开发之前，先查团队是否已经保存过类似功能、补丁或问题处理记录。

如果某个旧案例已经被本地知识沉淀技能标记为过期或存在反证，搜索结果会显示推荐替代案例。成员侧 Codex 应优先检查替代案例，再判断是直接复用、适配复用、仅作参考、不适用还是未命中。

日报、周报、incoming 事件、原始来源和工作过程证据属于人看归档。它们保留显式查询能力，但不进入默认 AI 复用检索结果。

## 取得知识实际引用的完整补丁

搜索摘要只是线索。先按 `case_id` 和选定的 `implementation_id` 读取完整解法，
再列出该实现精确绑定的原件，选择返回的 `asset_id` 下载到本任务的新文件：

```bash
python3 "scripts/akbs_knowledge_search.py" --case-detail <case_id> \
  --implementation-id <implementation_id> --json
python3 "scripts/akbs_knowledge_search.py" --case-patches <case_id> \
  --implementation-id <implementation_id> --json
python3 "scripts/akbs_knowledge_search.py" --case-patches <case_id> \
  --implementation-id <implementation_id> \
  --download-patch <asset_id> --out /path/to/task-output/source.patch --json
```

沿用现有安装前检、成员身份和端点配置。此操作只读服务端，不回退本地索引，
不提交合并决定、不写复用成功记录；下载核对大小及 SHA-256，且不覆盖已有文件。
支持 active v2 案例自身 `source_evidence` 精确引用的原件，也支持当前修订、
不可变来源记录及原始哈希全部吻合的历史原件；后者的 `asset_id` 是本案例返回的
不透明下载标识，不会重写历史或补造资产。来源不能闭合或原件尚未受控保存时
明确返回不可取，不拿截断预览代替，也不因此否定历史量产验证。
取得原补丁后仍需判断目标环境、实施和验证；下载成功不是复用成功。
选定实现的 `accepted_evidence_binding` 与 `historical_case_snapshot` 权威分别保留。
只有 `implementation` 角色属于实现补丁集，验证、反证和回滚材料不能混作实现；
不按同名、同包或相同 SHA 替换下载标识，也不合并不同环境的适用性。

## 典型场景

- Android 工程新需求来了，无论属于 App/GMS、平台、native、HAL、kernel、device 还是 build 层，都先查有没有类似修改或既有补丁。
- 看到一个类名、属性、Settings key、资源 key，想知道以前哪个补丁改过。
- 管理员需要追溯日报、周报、incoming 事件或原始来源时，使用显式 `--type report`、`--type event` 或 `--type evidence`。
- 想确认某个 incoming 是否留下了可复用验证证据。
- 合并确认由 `akbs-knowledge-merge-review` 负责；本 Skill 的旧 merge 参数只作为兼容入口。
- `android-change-workflow` 在进入源码分析前，先查知识库作为参考材料。

## 常用命令

调用前由 Codex 按 runtime Skill 的 **Functional Query Construction** 规则整理完整功能，
去掉与功能无关的询问话术；项目、平台、版本和明确分类用现有参数传递。行为条件、
否定和联合关系不能丢，代码锚点原样保留。普通功能可忠实换一种语言表达，不补造事实。
补充查询也必须完整，不能把必需同时成立的两个条件拆成两次查询后将并集当成完整命中。
服务端仍是词面检索，最终需读取解法及原件判断。

```bash
python3 "scripts/akbs_knowledge_search.py" \
  "电源键 frameworks/base" \
  --limit 8
```

默认 `--source auto` 会优先使用服务端搜索；请求携带 `X-AKBS-User=<member_alias>`、固定 `X-AKBS-Member-Search-Contract=akbs-member-knowledge-search-v2` 和内容协商头，服务器按固定来源 IP 验证身份。普通成员配置不需要写服务器路径，endpoint 由 AKBS endpoint resolver 提供；管理员/测试 override 可使用受控 `CODEX_REPORT_AKBS_ENDPOINT_*` 环境变量。

只搜补丁：

```bash
python3 "scripts/akbs_knowledge_search.py" \
  "persist.sys launcher" \
  --type patch
```

只搜主案例或平台实现：

```bash
python3 "scripts/akbs_knowledge_search.py" \
  "通知音量 SystemUI" \
  --type case

python3 "scripts/akbs_knowledge_search.py" \
  "通知音量 VolumeDialogImpl" \
  --type implementation --project TVE8402M --platform rk --android-version 14 \
  --component-layer platform
```

只搜归档记录或证据：

```bash
python3 "scripts/akbs_knowledge_search.py" \
  "电源键 rk3576" \
  --type event

python3 "scripts/akbs_knowledge_search.py" \
  "真机验证 services.jar" \
  --type evidence
```

默认 `--type all` 是 AI 复用视图，不返回 report/event，也不返回 `source`、`work_findings`、`report_context`、`package_check` 这类人看归档证据。默认搜索还会过滤已撤销（retracted）的案例、变体、补丁、符号和证据，并清理普通搜索证据负载里残留的已撤销对象引用，例如已废弃的 `search_before_change.results`。需要追溯来源或撤销证据时必须显式指定类型。

指定知识库仓库路径：

```bash
python3 "scripts/akbs_knowledge_search.py" \
  "WindowManager display" \
  --root /path/to/knowledge
```

离线强制本地 JSONL 搜索：

```bash
python3 "scripts/akbs_knowledge_search.py" \
  "WindowManager display" \
  --source local \
  --root /path/to/knowledge
```

本地 fallback 或显式 `--source local` 时，脚本会优先使用 `--root`、`CODEX_KNOWLEDGE_ROOT`、`CODEX_KNOWLEDGE_REPO_WORKTREE` 和 `$CODEX_HOME/akbs-member-ops.toml` 当前 profile 的 `knowledge_repo_worktree`。只要 target 文件存在，它就是唯一 AKBS 配置权威，搜索不会探测、解析或为冲突检查读取旧配置；仅当 target 缺失时才兼容读取旧配置。之后才尝试当前目录父级和通用 `worktrees/knowledge` 路径。成员端不依赖维护者本机路径。

它不会自动读取数据库仓库或成员 incoming 工作区。管理员要排查数据库仓库内部数据时，必须显式传 `--root`。

服务端结果原样保留 `reuse_grade`、证据缺口和环境比较。`direct_reuse_candidate` 才可考虑直接复用，`adaptation_candidate` 才可考虑适配；还须选择有闭合、已接受实现证据的 Implementation，并完成目标环境验证。Android 版本、芯片平台和项目按完整环境组合比较，不设全局固定优先级。本地 fallback 只能作文本参考。

“未命中”要求同一次有界调用至少两个独立查询、均完整且为空、检索投影就绪且完整；
单次空结果、部分响应、服务失败或本地 fallback 都只能记 `unknown`。
`--additional-query` 可在一次调用中补充独立措辞或代码锚点。

兼容入口：查看合并确认和依据（新任务应使用 `akbs-knowledge-merge-review`）：

```bash
python3 "scripts/akbs_knowledge_search.py" \
  --merge-confirmation list

python3 "scripts/akbs_knowledge_search.py" \
  --merge-confirmation analyze \
  --merge-confirmation-id merge-confirmation-20260703-member-patch
```

`--merge-confirmation-id` 只接受列表/通知返回的因果 `confirmation_id`，不接受来源 `package_key`。`list`、`detail`、`target`、`compare` 和 `analyze` 都是只读动作。`analyze` 会区分人看摘要和 Codex 分析证据；服务端不可用时会明确失败，不会伪造合并依据。只有成员明确要求发送异议时，才使用：

```bash
python3 "scripts/akbs_knowledge_search.py" \
  --merge-confirmation dispute \
  --merge-confirmation-id merge-confirmation-20260703-member-patch \
  --send-dispute \
  --dispute-reason "目标知识没有覆盖当前补丁的功能目标"
```

## 搜索使用证据

默认搜索会写入搜索使用证据（search usage evidence）到成员本地输出目录：

```text
$CODEX_HOME/artifacts/akbs-member-ops/search-usage/<YYYYMMDD>/*.json
```

旧配置中的 `out_dir` 不再改变写入位置。当前工作流将 JSON 输出的 `usage_receipt` 和
`usage_receipt_sha256` 传给 capture 的 `--search-receipt` 和 `--search-receipt-sha256`；
capture→submit 保留这份结构化回执的原字节、查询健康、确切实现及目标环境，不用同日文本汇总代替。
既有手工/历史材料沿用其真实使用记录，不补造新回执。

搜索使用证据会记录 `source`、`search_mode`、`reuse_grade`、`matched_channels`、`matched_anchors`，fallback 时还会记录 `fallback_reason`。

明确记录使用决策：

```bash
python3 "scripts/akbs_knowledge_search.py" \
  "电源键短按保持当前画面" \
  --project TVE8402M --platform rk3576 --android-version 14 \
  --reuse-decision adapt \
  --reuse-target implementation-power-key-rk14 \
  --reuse-reason "同类策略可参考，当前项目需适配"
```

取值包括 `reuse`、`adapt`、`reference_only`、`not_applicable`、`not_found` 和 `unknown`。这些只是成员侧开发证据，不是沉淀结论（curation decision）。
`reuse/adapt` 的目标必须是本次结果中的具体 Implementation；搜索意向和原件下载均不等于实际采用成功。
`--no-record-usage` 只取消本地记录写入，不能跳过显式复用判断的校验。

## 和其他 skill 的关系

```text
akbs-daily-report / akbs-weekly-report / akbs-patch-submit
  负责按材料类型把日报、周报、补丁包打包成 incoming 并发送到服务器上传入口

android-patch-capture
  负责把 Android 各工程层的修改整理成标准补丁资料

akbs-member-ops internal/incoming-v2
  提供唯一共享内核、当前配置诊断和插件更新检查

akbs-knowledge-search
  负责把知识库仓库里已有经验、补丁和验证结果搜出来

android-change-workflow
  处理需求前先搜索；搜不到或参考材料不足，再进入分析、修改、验证流程
```

## 文件入口

- [SKILL.md](../../../../plugins/akbs-member-ops/skills/akbs-knowledge-search/SKILL.md)：给 Codex 自动加载的执行说明。
- [references/search-contract.md](../../../../plugins/akbs-member-ops/skills/akbs-knowledge-search/references/search-contract.md)：知识库检索输入、输出和判断边界。
- [scripts/akbs_knowledge_search.py](../../../../plugins/akbs-member-ops/skills/akbs-knowledge-search/scripts/akbs_knowledge_search.py)：本地检索脚本。
