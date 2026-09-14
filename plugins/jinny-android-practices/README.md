# Jinny Android Practices

`jinny-android-practices` 3.0.0 是 Android Engineering Ops 的一个可选编排实现。
安装本插件不会改变核心行为；只有配置 `mode="jinny"` 或用户明确调用时才生效。

| Skill | 用途 |
| --- | --- |
| `jinny-android-orchestrator` | 简单任务直接做，复杂任务按需创建真实子智能体 |
| `jinny-android-coding-practices` | 可选的 Jinny 命名、helper 组织与 review 建议 |

## 安装和启用

Jinny 依赖已经安装的 Android 工程核心，但不是核心的默认依赖：

```bash
codex plugin marketplace add jinny51/android-codex-suite --ref main --json
codex plugin add android-engineering-ops@android-codex-suite --json
codex plugin add jinny-android-practices@android-codex-suite --json
codex plugin list --json
```

然后在用户配置 `$CODEX_HOME/android-engineering-ops.toml`，或优先级更高的项目配置
`<project>/.codex/android-engineering.toml` 中显式选择：

```toml
[extension]
mode = "jinny"
```

仅安装插件而不写这项配置，仍然是 `mode="none"`，Android Engineering Ops 会在当前任务
直接执行。更新或首次安装后请在新任务中使用，已经打开的任务不会热加载新版 Skill。

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

## 怎么判断要不要创建子智能体

| 情况 | Jinny 的处理 |
| --- | --- |
| 已知文件中的一个小修改，验证路径明确 | 当前任务直接做 |
| 原因未知，需要同时查日志和多条源码链路 | 按需要创建 investigator |
| 方案已确定，存在互不重叠的模块修改 | 每个文件或子系统只交给一个 implementer |
| 构建、设备行为或回归证据适合独立收集 | 创建 verifier |
| 需要核对当前 Android/API/依赖事实 | 创建 researcher，并使用权威来源 |
| Binder、并发、SELinux、HAL、kernel 等高风险修改 | 完成实现后按价值创建 reviewer |

多个文件、任务描述很长或“有空闲模型”都不是委派理由；验证是必须的，但不一定要交给
子智能体。只有独立上下文、并行取证、专业执行或独立复核能显著提高效率或质量时才委派。

## 两个完整例子

简单任务：“把一个已定位的资源字符串改掉，并运行对应测试。”当前任务已经知道文件、修改
方式和验证命令，因此直接完成，不创建 investigator、implementer 或 reviewer。

复杂任务：“某项目偶现开机卡死，日志同时指向 system_server 和 SystemUI。”合理流程是：

1. 让一个 investigator 只读分析启动日志和 system_server 调用链；必要时让另一个
   investigator 独立检查 SystemUI 侧，二者不修改代码。
2. 当前任务比较证据、确定根因、修改方向和验收标准。
3. 将明确且互不重叠的写入范围交给一个或多个 implementer；同一文件或子系统只有一个 writer。
4. 实现完成后让 verifier 复现原问题并收集构建、启动和回归证据。
5. 只有涉及 Binder 锁或高风险并发时才增加 reviewer；reviewer 只报告问题，不成为第二审批人。
6. 当前任务处理发现、整合代码并向用户给出最终结果。

每次委派必须包含具体目标、范围、必要上下文、约束、交付物和验收方式，不能只写“帮我查
一下”或“修复这个问题”。角色的完整合同位于
`skills/jinny-android-orchestrator/references/`。
