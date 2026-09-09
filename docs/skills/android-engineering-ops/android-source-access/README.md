# android-source-access

新工程任务开始前统一调用 `task_start.py --task-id <本任务固定标识>` 检查并按需更新工程插件；同一任务的后续 Skill 复用结果。更新后需重启 Codex，执行中的编译/命令不做中途升级。任务标识由 Codex 管理，不需要成员填写。

> GitHub 说明页。Runtime Skill 位于 [../../../../plugins/android-engineering-ops/skills/android-source-access](../../../../plugins/android-engineering-ops/skills/android-source-access)。

统一的 Android source-access 入口。它先从本机事实识别 WSL 或 macOS，再只调用随插件安装的对应 adapter。普通 Linux、错误平台命令或缺失 adapter 在副作用前失败；不复制凭据，也不把 mounted source 当作 Codex 源码执行面。

纯帮助、主机识别和命令列表可以先运行；读取凭据/registry 或执行网络、挂载、登记、文件操作前必须通过 target-only install-family gate。
