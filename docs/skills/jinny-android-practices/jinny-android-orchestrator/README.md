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

## 启用

先安装 `android-engineering-ops` 和可选的 `jinny-android-practices`，再在
`$CODEX_HOME/android-engineering-ops.toml` 或项目的 `.codex/android-engineering.toml`
中显式选择：

```toml
[extension]
mode = "jinny"
```

没有这项配置时核心直接执行，不会检查 Jinny。安装、升级和解析检查命令见
[插件 README](../../../../plugins/jinny-android-practices/README.md)。

## 角色怎么选

| 角色 | 何时使用 | 主要交付 |
| --- | --- | --- |
| [investigator](../../../../plugins/jinny-android-practices/skills/jinny-android-orchestrator/references/investigator.md) | 根因或代码路径未知 | 证据、调用链和根因排序 |
| [implementer](../../../../plugins/jinny-android-practices/skills/jinny-android-orchestrator/references/implementer.md) | 方向和写入边界已确定 | 限定范围的代码修改 |
| [verifier](../../../../plugins/jinny-android-practices/skills/jinny-android-orchestrator/references/verifier.md) | 需要独立复现、构建或设备证据 | 可复核的验证结果 |
| [researcher](../../../../plugins/jinny-android-practices/skills/jinny-android-orchestrator/references/researcher.md) | 需要当前版本或兼容性事实 | 权威来源和适用结论 |
| [reviewer](../../../../plugins/jinny-android-practices/skills/jinny-android-orchestrator/references/reviewer.md) | 修改风险值得独立复核 | 按严重程度排列的发现 |

小改动不创建角色。复杂任务也只创建实际需要的角色，不固定走完整流水线。

例如，调查跨 system_server 与 SystemUI 的偶现开机卡死时，可以先并行委派两个只读
investigator；当前任务根据证据决定方案后，再把互不重叠的修改交给 implementer，并让
verifier 收集构建和启动证据。只有涉及 Binder 锁等高风险路径时才增加 reviewer。整个
过程中当前任务负责架构、冲突处理、集成和最终答复。
