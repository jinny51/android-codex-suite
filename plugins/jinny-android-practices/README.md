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
investigator、implementer、verifier 或 reviewer；不会建立常驻主控、模拟 worker、
assignment/result JSON、stage snapshot 或第二套验收流程。多个文件或任务描述很长，
本身不构成委派理由。

模型只是 Jinny 实现的默认偏好，不属于公共扩展协议。用户选择永远优先；不可用时
继承当前模型，不自动轮换或执行 fallback 链。Android policy、source access、远程
构建/设备、patch capture 和提交授权仍由 `android-engineering-ops` 负责。

成员可以实现自己的插件并使用同一最小协议，但角色和模型可以完全不同。
