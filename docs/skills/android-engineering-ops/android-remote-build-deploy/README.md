# android-remote-build-deploy

新工程任务开始前统一调用 `task_start.py --task-id <本任务固定标识>` 检查并按需更新工程插件；同一任务的后续 Skill 复用结果。更新后需重启 Codex，执行中的编译/命令不做中途升级。任务标识由 Codex 管理，不需要成员填写。

> GitHub 说明页。Runtime Skill 位于 [../../../../plugins/android-engineering-ops/skills/android-remote-build-deploy](../../../../plugins/android-engineering-ops/skills/android-remote-build-deploy)。

在已登记的远端 AOSP/厂商工程中，通过 `android-remote-channel` 发现 profile、建立 checkpoint、构建并校验 artifact；支持时才执行受控本地 adb 交付。构建和传输 evidence 不能替代 requirement acceptance，最终状态仍由 `android-change-workflow` 判定。

发现、配置、构建、artifact bridge 读取、本地 `adb`、网络和写入前必须通过 target-only install-family gate；每个独立公开入口都不能接受旧新插件混装。
