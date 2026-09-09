# android-patch-capture

> GitHub 说明页。Runtime Skill 位于 [../../../../plugins/android-engineering-ops/skills/android-patch-capture](../../../../plugins/android-engineering-ops/skills/android-patch-capture)。

把既有 Android 变更封装为 `$CODEX_HOME/artifacts/android-patch-capture/packages` 下的本地不可变材料。新包记录 `components[]`（application/platform/native/hal/kernel/device/build layer 与独立 type/partition/ownership）、`primary_component_id`，以及每个 repository/patch 的显式 `component_ids[]`；不从路径猜 layer。capture 只能保留或降级声明状态，不能把 draft/candidate 升为 validated。

读取 snapshot/patch/package/identity/evidence 或写入材料前必须通过 target-only install-family gate；入口必须从 inventory 绑定的目标 cache 执行。

远程构建投递工具生成的 `build_delivery/unverified` 回执可直接通过 `--build-result` 传入，原内容保存在独立辅助材料中，不会替代需求验收。跨组件时用 `--evidence-component build-delivery:COMPONENT_ID` 声明关联，不修改原回执。

旧 `android-framework-patch-capture` 包只读检查并规范显示为 platform/framework（未知 facet 为 null），不复制或改写历史。任何 layer 的 validated 新 capture 都先交 `akbs-patch-submit android-change-v2 adapt-capture` 转成 canonical 上传包，不能直接 `prepare`。成员适配器和服务端必须同时支持对应的 qualification 合同；本地采集、转换和服务器接收是三个独立结果。capture 不授予上传权限，不回落 v1。
