# android-patch-capture

新工程任务开始前统一调用 `task_start.py --task-id <本任务固定标识>` 检查并按需更新工程插件；同一任务的后续 Skill 复用结果。更新后需重启 Codex，执行中的编译/命令不做中途升级。任务标识由 Codex 管理，不需要成员填写。

> GitHub 说明页。Runtime Skill 位于 [../../../../plugins/android-engineering-ops/skills/android-patch-capture](../../../../plugins/android-engineering-ops/skills/android-patch-capture)。

把已完整验证的 Android 变更直接封装为 `$CODEX_HOME/artifacts/android-patch-capture/packages` 下的最终 v2 包。新包记录 `components[]`（application/platform/native/hal/kernel/device/build layer 与独立 type/partition/ownership）、`primary_component_id`，以及每个 source/patch/evidence 的显式绑定；不从路径猜 layer。未完成、失败或仅部分验证的工作留在工程任务或报告中，不生成上传包。

读取 snapshot/patch/package/identity/evidence 或写入材料前必须通过 target-only install-family gate；入口必须从 inventory 绑定的目标 cache 执行。

当前 Codex 编写的变更优先使用 `capture_remote_snapshot.py --package -- <capture 参数>`：工具在远端通道独占锁内生成最新源码快照，传回后立即调用本地原子打包器，不再要求成员或智能体在 15 分钟内手工衔接两个命令。省略 `--package` 的旧两步接口继续兼容，并保留快照时效门禁。

远程构建投递工具生成的 `build_delivery/unverified` 回执可直接通过 `--build-result` 传入，原内容保存在独立辅助材料中，不会替代需求验收。跨组件时用 `--evidence-component build-delivery:COMPONENT_ID` 声明关联，不修改原回执。

旧 `android-framework-patch-capture` 包只读检查并规范显示为 platform/framework（未知 facet 为 null），不复制或改写历史。任何 layer 的已验证变更都由 capture 直接生成最终 `akbs-android-change-package-v2`，整个目录可直接交给 `akbs-patch-submit` 做 check、prepare 或 submit。V1 与 v2 走同一补丁上传生命周期，不存在中间转换，也不回落 v1。
