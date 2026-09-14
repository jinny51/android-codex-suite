# jinny-android-orchestrator

> GitHub 说明页。Runtime Skill 位于 [../../../../plugins/jinny-android-practices/skills/jinny-android-orchestrator](../../../../plugins/jinny-android-practices/skills/jinny-android-orchestrator)。

Jinny 的可选 Android 多智能体编排入口。简单任务留在当前任务直接完成；只有复杂任务
确实存在可分离调查、单一写入、独立验证或高风险复核价值时，才创建真实子智能体。

当前任务拥有目标、架构、集成和最终答复。子智能体使用普通消息和实际文件变更返回
结果，不维护 controller/worker 状态机或额外审批。用户模型选择优先，角色默认模型只在
未指定时作为建议。
