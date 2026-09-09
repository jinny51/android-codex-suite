# android-change-workflow

新工程任务开始前统一调用 `task_start.py --task-id <本任务固定标识>` 检查并按需更新工程插件；同一任务的后续 Skill 复用结果。更新后需重启 Codex，执行中的编译/命令不做中途升级。任务标识由 Codex 管理，不需要成员填写。

> GitHub 说明页。Runtime Skill 位于 [../../../../plugins/android-engineering-ops/skills/android-change-workflow](../../../../plugins/android-engineering-ops/skills/android-change-workflow)。

Android 工程 controller 的唯一入口，覆盖 application、platform、native、HAL、kernel、device 和 build。它拥有 requirement contract、阶段、Gate、assignment/result 校验与最终验收；可选 practices provider 只返回 schema/hash 绑定的决策，不能 spawn、写入、取锁或宣布验收。

任何项目/源码读取、本地或远端命令、设备操作、委派和写入前，都必须先通过当前安装插件的 target-only family gate；这个要求同样适用于 `local_project` 和直接 `adb`，旧新插件混装时失败关闭。

Extension 按项目配置优先于本地配置解析；选择 provider 后只从 Codex active installed+enabled inventory 取得固定插件根，异常 fail closed，能力缺失或不适用才回 core。

Canonical layer 只有 application/platform/native/hal/kernel/device/build；type、partition、ownership 正交且不互相推断。任何 layer 的 validated capture 均先通过 `akbs-patch-submit android-change-v2 adapt-capture` 转成 canonical 上传包，再检查和提交，不能将 capture 直接 `prepare`。成员适配器与服务器必须同时支持其 qualification 合同；采集、转换和服务器接收分别验收，失败时说明具体阶段，不回落 Framework v1。
