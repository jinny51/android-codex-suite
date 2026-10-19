# android-patch-capture

新工程任务开始前统一调用 `task_start.py --task-id <本任务固定标识>` 检查并按需更新工程插件；同一任务的后续 Skill 复用结果。更新后需重启 Codex，执行中的编译/命令不做中途升级。任务标识由 Codex 管理，不需要成员填写。

> GitHub 说明页。Runtime Skill 位于 [../../../../plugins/android-engineering-ops/skills/android-patch-capture](../../../../plugins/android-engineering-ops/skills/android-patch-capture)。

把已完整验证的 Android 变更整理为 `$CODEX_HOME/artifacts/android-patch-capture/packages` 下的工程 capture，再由 `akbs-patch-submit` 构造 `knowledge-incoming-package/2/android_change` 最终包。`components[]` 只记录 application/platform/native/hal/kernel/device/build layer 与对应 patch 路径；每个 patch 必须恰好分类一次。已有采集路线和身份配置可用时，未完成或仅部分验证的材料可按真实 `draft`/`candidate` 状态保留本地，或留在工程任务/报告中；它们不能冒称已验证上传包，不新增离线格式或采集能力。

一个功能可以同时涉及多个 layer 和多个 Git 仓库，并继续作为一个功能包归档；但 patch 必须按实际 Git 仓库分别生成。跨仓库 patch 可以使用相同的 `@change-id`，文件名前半段按各自仓库区分。手工或历史导入也必须逐仓库提供 patch 与仓库路径，不能用 Android 源码顶层 `.` 代替仓库。

手工导入必须保留原 patch 字节；如果捕获器需要过滤权限变化等内容，会拒绝导入并保留原文件。
手工／历史导入只整理现成 patch 文件，不再从当前 Git 改动现场生成导入包。
导入材料可本地准备，但须由管理员通过现有入口补录；普通成员新开发继续正常采集、提交。
历史导入是使用 Codex 前的量产遗留，不以今天的打包日期或工具版本判断实现发生时间。

入口必须从 inventory 绑定的目标 cache 执行；task-start 和实际远端通道负责安装完整性校验，不要在其间再手工重复运行 install-family gate。校验失败仍停止。

在已登记的远端产品源码树中，当前 Codex 编写的变更优先使用 `capture_remote_snapshot.py --package -- <capture 参数>`：工具在远端通道独占锁内生成最新源码快照，传回后立即调用本地原子打包器，不再要求成员或智能体在 15 分钟内手工衔接两个命令。省略 `--package` 的旧两步接口继续兼容，并保留快照时效门禁。真实本地产品 Git 仓库的既有补丁需核实仓库与修订后才能按 `manual_import` 处理；本次 Codex 在本地做的新改动不因此改标为导入，现行采集器尚无该来源的 `current_codex_skill` 路线。

仓内有无关改动时，可重复传入 `--repo-file 仓库路径=仓内准确文件路径`，每个所选仓库均需给出文件。它不是目录或通配符选择，不改变真实 Git 根与 HEAD，也不会读取未选中的 out/APK 等产物。重命名需同时选择新旧路径，已识别的跨范围重命名会被拒绝。选择精度是文件，不是 hunk：同文件内的无关修改仍需在采集前核查，不能声称已隔离，也无需为排除其他文件另建产品工作副本。

一步入口会先做便宜的本地参数预检，再获取远端快照。复用已完成的构建、设备验证与原始搜索回执，不重编译或重造证据。用户说“先生成包、不提交”时仍走正常 capture → `akbs-patch-submit --prepare` → 本地校验，停在上传之前，不另造审阅 ZIP 或临时打包流程。

捕获前逐仓确认改动属于目标 Android 产品源码。独立开发的 App 源码不因 APK 被产品使用就成为 Patch；产品侧 APK 集成配置如在产品源码仓内修改，可按该仓单独采集。手工补丁和远端快照遵守同一来源边界。
捕获脚本不能从 Git 地址、路径或 `application` layer 自动判定仓库的业务归属；当前由工程任务在调用前核实并记录，不能把脚本出包成功当作来源证明。

远程构建投递工具生成的 `build_delivery/unverified` 回执可直接通过 `--build-result` 传入，原内容保存在独立辅助材料中，不会替代需求验收。

当前工作流将知识搜索返回的 `usage_receipt` 与 `usage_receipt_sha256` 分别传入
`--search-receipt`、`--search-receipt-sha256`。采集及提交保留回执原字节和结构化查询结果，
不按同日日志合并或改写；回执不是产品源码归属证明，也不是复用成功证明。
真实开发前搜索的用途仍为 `unknown` 时保留事实并提示，不因此把已验证的补丁降为
`candidate`。开发前搜索未发生时仍停止当前工作流的已验证采集；本地阶段材料保留真实缺失事实。已提供的回执被伪造、hash 不符或环境冲突时，不因状态是草稿而放行。

旧 `android-framework-patch-capture` 和 `framework_change` 历史材料只读兼容，不复制或改写。当前 capture 生成工程材料目录；将整个目录交给 `akbs-patch-submit`，由后者构造、检查、准备或提交唯一的 `knowledge-incoming-package/2/android_change` 最终包。
