# android-patch-capture

新工程任务开始前统一调用 `task_start.py --task-id <本任务固定标识>` 检查并按需更新工程插件；同一任务的后续 Skill 复用结果。更新后需重启 Codex，执行中的编译/命令不做中途升级。任务标识由 Codex 管理，不需要成员填写。

> GitHub 说明页。Runtime Skill 位于 [../../../../plugins/android-engineering-ops/skills/android-patch-capture](../../../../plugins/android-engineering-ops/skills/android-patch-capture)。

把已完整验证的 Android 变更整理为 `$CODEX_HOME/artifacts/android-patch-capture/packages` 下的工程 capture，再由 `akbs-patch-submit` 构造 `knowledge-incoming-package/2/android_change` 最终包。`components[]` 只记录 application/platform/native/hal/kernel/device/build layer 与对应 patch 路径；每个 patch 必须恰好分类一次。未完成、失败或仅部分验证的工作留在工程任务或报告中，不生成上传包。

一个功能可以同时涉及多个 layer 和多个 Git 仓库，并继续作为一个功能包归档；但 patch 必须按实际 Git 仓库分别生成。跨仓库 patch 可以使用相同的 `@change-id`，文件名前半段按各自仓库区分。手工或历史导入也必须逐仓库提供 patch 与仓库路径，不能用 Android 源码顶层 `.` 代替仓库。

手工导入必须保留原 patch 字节；如果捕获器需要过滤权限变化等内容，会拒绝导入并保留原文件。

读取 snapshot/patch/package/identity/evidence 或写入材料前必须通过 target-only install-family gate；入口必须从 inventory 绑定的目标 cache 执行。

在已登记的远端产品源码树中，当前 Codex 编写的变更优先使用 `capture_remote_snapshot.py --package -- <capture 参数>`：工具在远端通道独占锁内生成最新源码快照，传回后立即调用本地原子打包器，不再要求成员或智能体在 15 分钟内手工衔接两个命令。省略 `--package` 的旧两步接口继续兼容，并保留快照时效门禁。真实本地产品 Git 仓库的既有补丁需核实仓库与修订后才能按 `manual_import` 处理；本次 Codex 在本地做的新改动不因此改标为导入，现行采集器尚无该来源的 `current_codex_skill` 路线。

捕获前逐仓确认改动属于目标 Android 产品源码。独立开发的 App 源码不因 APK 被产品使用就成为 Patch；产品侧 APK 集成配置如在产品源码仓内修改，可按该仓单独采集。手工补丁和远端快照遵守同一来源边界。
捕获脚本不能从 Git 地址、路径或 `application` layer 自动判定仓库的业务归属；当前由工程任务在调用前核实并记录，不能把脚本出包成功当作来源证明。

远程构建投递工具生成的 `build_delivery/unverified` 回执可直接通过 `--build-result` 传入，原内容保存在独立辅助材料中，不会替代需求验收。

旧 `android-framework-patch-capture` 和 `framework_change` 历史材料只读兼容，不复制或改写。当前 capture 生成工程材料目录；将整个目录交给 `akbs-patch-submit`，由后者构造、检查、准备或提交唯一的 `knowledge-incoming-package/2/android_change` 最终包。
