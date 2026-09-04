# Jinny Android Practices

`jinny-android-practices` 2.0.1 是 `android-practices-provider-v1` 的可选实现。
只有 `android-engineering-ops` 的 extension 配置显式选择它时才生效；安装本插件本身
不会改变 core-direct 默认行为。

| Skill | 用途 |
| --- | --- |
| `jinny-android-coding-practices` | 返回不放宽 `android-change-policy` 的 coding 决策 |
| `jinny-android-execution-policy` | 返回受 controller rollout ceiling 约束的 worker profile 决策 |

Provider manifest 固定在：

```text
contracts/android-practices-provider/v1/provider.json
```

Jinny mode 配置只含 `mode`、`provider_version=2.0.1` 和文件 SHA-256；插件名与
Provider ID 固定为 `jinny-android-practices`。Core 从 `codex plugin list --json` 的
active installed+enabled inventory 取得物理根，再验证固定相对路径、plugin manifest
和 provider manifest；不扫描缓存最高版本、插件描述或 Skill catalog 猜测 provider。

Execution profiles 是合同能力，不等于当前 rollout 已授权：明确重复/窄提取默认走
Luna read-only；源码探索、日志诊断、普通方案与普通实现走 Terra；架构、高风险、高歧义
或最终 review 走 Sol（read-only，reasoning max）；verification/bounded operation 走
Luna。路由显式消费 ambiguity、risk、code judgment、task shape 与 requested side effect，
而不是只看 stage 名。Phase 2 默认 rollout ceiling 仍是 read-only；越界决定必须 fail
closed。Provider 不执行这些动作。

本插件只能产生 `coding-policy-decision-v1` 和
`execution-policy-decision-v1`。它不能 spawn、写源码、取锁、执行副作用、上传、接受
Gate 或宣布最终验收；controller 必须独立验证决定并保留最终工程状态所有权。

信任边界必须如实理解：Codex Skill 不是 OS sandbox。Jinny 或 custom provider 是用户
主动安装并选择的代码与指令，因此用户信任其进程内行为。机器门禁保证 active plugin
identity/version/manifest hash、Skill/agent metadata/decision entrypoint 内容 hash、closed
decision schema、run/stage/context 绑定，以及 provider 输出不会获得 controller 权限；
它不证明任意第三方 provider 在操作系统层面无副作用。

Jinny 的两个 decision entrypoint 还会在执行共享 helper 前校验其固定 SHA-256，并直接
执行已校验的同一组 bytes。Coding advisory 全部位于已声明 `skill_sha256` 的 `SKILL.md`
中，不从未绑定的 runtime reference 引入行为规则。
