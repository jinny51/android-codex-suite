# Jinny Android Practices

`jinny-android-practices` 3.0.0 是 Android Engineering Ops 的一个可选编排实现。
安装本插件不会改变核心行为；只有配置 `mode="jinny"` 或用户明确调用时才生效。

| Skill | 用途 |
| --- | --- |
| `jinny-android-orchestrator` | 简单任务直接做，复杂任务按需创建真实子智能体 |
| `jinny-android-coding-practices` | 可选的 Jinny 命名、helper 组织与 review 建议 |

扩展 manifest 固定在：

```text
contracts/android-orchestration-extension/v1/extension.json
```

Jinny 编排由当前用户任务主持。它先判断委派是否真的省时或提高质量，再按需创建
investigator、implementer、verifier、researcher 或 reviewer；每种角色都带有可直接
注入真实子智能体的边界与交付合同。它不会建立常驻主控、模拟 worker、
assignment/result JSON、stage snapshot 或第二套验收流程。多个文件或任务描述很长，
本身不构成委派理由。

根任务模型保持用户在当前任务中的人工选择。用户没有另行指定子智能体模型时，四种
执行角色默认使用 Luna high，独立 reviewer 默认使用 Astra low；不可用时不探测候选链，
不自动轮换。Android policy、source access、远程
构建/设备、patch capture 和提交授权仍由 `android-engineering-ops` 负责。

这一实现参考了
[`codex-astra-luna-orchestrator`](https://github.com/donvito/codex-astra-luna-orchestrator)
的“当前根任务负责架构与集成、专业子智能体承担有界工作”模式。不同之处是 Jinny 作为
可选插件随 Skill 携带角色合同，不覆盖成员项目的 `.codex` 配置，也不固定根任务模型。

成员可以实现自己的插件并使用同一最小协议，但角色和模型可以完全不同。
