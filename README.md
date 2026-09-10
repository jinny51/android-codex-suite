# Android Knowledge Base & Engineering Suite

AKBS 的正式名称是 **Android Knowledge Base System**。这是其成员端和 Android 工程端共用的
Codex 插件仓库 `android-codex-suite`。工程能力覆盖 Android 的 application、platform、native、HAL、kernel、
device 与 build 层；type、partition（包括 vendor）和 ownership 作为正交属性表达，而不是再混成一张领域列表。

## 目标插件

| 插件 | 职责 | 是否必需 |
| --- | --- | --- |
| [akbs-member-ops](plugins/akbs-member-ops/README.md) | 成员设置、知识检索、合并复核、日报、周报和 Android 补丁包提交 | 使用 AKBS 的成员安装 |
| [android-engineering-ops](plugins/android-engineering-ops/README.md) | Android 变更规范、总工作流、跨平台源码接入、远程执行、构建交付和本地补丁采集 | 处理 Android 工程任务的成员安装 |
| [jinny-android-practices](plugins/jinny-android-practices/README.md) | 可选编码实践和执行策略 Provider；只能给出决策，不能取代核心验收权 | 按成员选择安装 |

## Skill 结构

```text
akbs-member-ops
├── akbs-member-setup
├── akbs-knowledge-search
├── akbs-knowledge-merge-review
├── akbs-daily-report
├── akbs-weekly-report
└── akbs-patch-submit

android-engineering-ops
├── android-change-policy
├── android-change-workflow
├── android-source-access
├── android-remote-channel
├── android-remote-build-deploy
└── android-patch-capture

jinny-android-practices
├── jinny-android-coding-practices
└── jinny-android-execution-policy
```

## 边界

- `android-change-workflow` 是 Android 工程总流程和最终验收者。
- `android-change-policy` 保存所有成员都不能绕过的身份、patch 溯源、真实证据和领域安全底线。
- `akbs-patch-submit` 面向所有受支持的 Android change domain，不把补丁业务限定为 Framework。
- `android-source-access` 自动识别 WSL 或 macOS，并选择插件内对应适配器；成员不再按操作系统安装两个入口插件。
- 日报、周报、知识检索和补丁提交属于 AKBS 成员能力；源码修改、构建和本地材料采集属于 Android 工程能力。
- 当前源码材料默认由远端通道一步生成最新快照并原子打包，避免人工衔接命令导致快照仅因等待而过期。
- 旧 v1 配置、材料和历史包永久可读，不改写、不搬家；新写入使用新路径。
- 七层补丁提交需要匹配的成员适配器与服务端 qualification 合同。先部署服务端再发布成员更新；服务端保留旧两层合同和旧包重试身份，客户端不能用改名绕开服务器能力门禁。

## 可选扩展

成员有三种合法选择：

1. `none`：不安装扩展，由核心直接执行。
2. `jinny`：安装 `jinny-android-practices`，使用其编码实践和 Sol/Terra/Luna 执行策略。
3. `custom`：安装成员自己的 Provider，并按 `android-practices-provider-v1` 合同声明能力。

选择写在 `$CODEX_HOME/android-engineering-ops.toml`；项目可用
`<project>/.codex/android-engineering.toml` 覆盖。Provider 必须由固定 manifest 路径、
ID、版本和 SHA-256 精确定位，不能靠扫描描述猜测。显式选择的 Provider 缺失、损坏或越权时
fail closed；没有声明某能力或当前任务不适用时才回到核心。

`jinny-android-execution-policy` 只决定执行建议：Sol 负责需求分析、风险判断和独立复核，
Terra 负责常规源码实现，Luna 负责边界明确的编译、推送、证据收集和材料整理。
模型 worker 只能返回结果和证据，不能自行宣布任务完成；最终结论仍由
`android-change-workflow` 结合真实状态作出。

## 安装与升级

成员端和工程端共用一份通用更新源码，分别随插件打包并校验一致性，不新增更新插件或运行时依赖。日报、周报、补丁继续使用成员内核的版本门禁；工程任务由六个 Skill 共用的启动入口检查更新，同一任务后续步骤不反复联网或中途切换版本。自动更新后按提示重启 Codex。旧工程版本需先升级一次，才具备这一启动检查能力。

当前 marketplace 只发布三个正式插件。旧 `android-framework-codex-suite` 安装族不与新安装族混用；
成员升级时先添加 `android-codex-suite`、安装并确认所需的新插件可用，再卸载旧插件和旧 marketplace，随后重启 Codex。
旧配置、v1 材料和历史包继续兼容读取；旧仓库的当前分支保留稳定 1.x 版本，仅作为参考和回退来源，不再承载新版本发布。

Android change v2 服务端 writer 的启用仍是独立的服务端发布事项，仓库改名不会绕过该门禁。

## 配置和身份

成员身份来自已选择 profile 的 `member_alias`，不是 Git author、示例人名或事项号。新 Codex
代码在支持 `//` 注释的文件里使用成对标记：

```text
//<member_alias> <yyyyMMdd>@{
...
//<member_alias> <yyyyMMdd>@}
```

新成员配置写入 `$CODEX_HOME/akbs-member-ops.toml`。只要这个权威文件存在，解析器就不再
打开或合并任何旧成员配置；文件损坏、没有选中 profile 或 profile 不存在都会 fail closed。
只有权威文件完全不存在时，才读取用户目录下旧
`android-knowledge-intake.toml`、`android-knowledge-search.toml` 和报告配置作为永久只读
兼容输入，且多个旧来源的 alias 必须一致。仓库内 `.codex/report.toml` 永远不能提供或覆盖
成员身份。

不使用 AKBS 的独立工程成员可以在 `$CODEX_HOME/android-engineering-ops.toml` 的
`[identity].member_alias` 提供身份；项目级 `.codex/android-engineering.toml` 只允许选择
extension，不能声明身份。AKBS 身份与独立工程身份同时存在时必须一致；命令行或环境中的
profile 只能选择已经存在的 AKBS profile，不能凭空生成身份。凭据、token、cookie、私钥和
本地成员配置不得提交到本仓库。
成员请求只发送 `member_alias` 作为业务身份，不把个人长期 token 写入插件配置；服务端访问
授权仍以部署侧的固定工作站来源 IP 和服务端策略为准，插件不能自行扩大授权范围。

## 维护和验证

普通成员通过 marketplace 安装和更新，不手工同步 Skill。维护者只在隔离 worktree 中修改，
并通过统一验证入口检查插件元数据、Skill/agent/docs 一致性、迁移拓扑、混装负例、脚本回归和
服务端合同兼容：

```bash
python3 /home/jinny/akbs/maintainer/scripts/run_akbs_validation.py \
  plugin-full --akbs-root /home/jinny/akbs --allow-dirty
```

每个公开 Skill 都必须同时存在：

```text
plugins/<plugin>/skills/<skill>/SKILL.md
plugins/<plugin>/skills/<skill>/agents/openai.yaml
docs/skills/<plugin>/<skill>/README.md
```

Runtime Skill 目录不放 README；GitHub 说明统一放在 `docs/skills/`。发布前还要在真实 WSL、
macOS、构建服务器和设备上完成相应 Pilot，不能用 fake fixture 替代外部事实。
