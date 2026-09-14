# jinny-android-orchestrator

> GitHub 说明页。Runtime Skill 位于 [../../../../plugins/jinny-android-practices/skills/jinny-android-orchestrator](../../../../plugins/jinny-android-practices/skills/jinny-android-orchestrator)。

Jinny 的可选 Android 多智能体编排入口。简单任务留在当前任务直接完成；复杂任务
确实存在独立价值时，按需创建 investigator、implementer、verifier、researcher 或
reviewer。每种角色都包含可直接用于真实子智能体的边界和交付合同。

当前任务拥有目标、架构、集成和最终答复。子智能体使用普通消息和实际文件变更返回
结果，不维护 controller/worker 状态机或额外审批。根任务模型不由 Skill 修改；用户未
指定子智能体模型时，执行角色默认 Luna high，独立审查默认 Astra low。

它借鉴了
[`codex-astra-luna-orchestrator`](https://github.com/donvito/codex-astra-luna-orchestrator)
的根任务与专业角色分工，但以可选插件内的 Skill 角色合同实现，不向成员项目写入全局
或项目级 Codex 配置。
