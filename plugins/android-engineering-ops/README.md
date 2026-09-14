# Android Engineering Ops

`android-engineering-ops` 2.1.2 是可独立安装的 Android 工程核心。它不依赖
`akbs-member-ops` 或任何 practices provider；未配置扩展时始终使用 core-direct。

| Skill | 职责 |
| --- | --- |
| `android-change-policy` | Android 七层 component 强制 policy 和 patch 归档规则 |
| `android-change-workflow` | 唯一 controller，拥有阶段、Gate 和 requirement acceptance |
| `android-source-access` | 自动识别 WSL/macOS，并分派唯一平台 adapter |
| `android-remote-channel` | 远端 source/build 命令、锁、队列和恢复 |
| `android-remote-build-deploy` | 受控 build、artifact 校验和本地 adb 交付 |
| `android-patch-capture` | 直接生成七层 component 标注的 v1 `android_change` 包 |

## Task startup and updates

新工程任务开始时，六个 Skill 共用 `lib/android_engineering_ops/task_start.py` 检查和更新本插件；同一任务只联网检查一次，后续命令保持本地安装校验。更新成功后提示重启 Codex，不在执行中的编译/推送里切换版本。任务身份和失败重试见 [启动约定](references/task-start.md)。公共更新源码与成员插件共用，但没有运行时依赖；现有成员需要先安装含此机制的版本一次。

## Optional practices provider

扩展选择只读取以下两个位置，项目配置优先：

```text
<project>/.codex/android-engineering.toml
$CODEX_HOME/android-engineering-ops.toml
```

缺少配置等价于 `mode = "none"`。每种 mode 使用冻结的精确字段集合；`jinny` 和 `custom` 只保存 provider/plugin identity、
版本和 manifest SHA-256；公共配置不能指定任意文件路径。Resolver 以
`codex plugin list --json` 的 active installed+enabled inventory 精确取得已安装根，
然后只接受固定相对路径 `contracts/android-practices-provider/v1/provider.json`。
CLI 不可用、active identity 不唯一、路径含 symlink、读取不稳定或哈希/身份不一致时
均 fail closed。只有 capability 缺失或 applicability 不匹配时回退 core。

```toml
[extension]
mode = "jinny"
provider_version = "2.0.1"
provider_manifest_sha256 = "<64 lowercase hex>"
```

`none` 只允许 `mode`；`jinny` 的 plugin name/provider ID 均固定，只允许上述三个字段；
`custom` 必须再提供 `plugin_name` 与 `provider_id`。Inventory 的完整 `pluginId` 只作为
观测证据返回，不能进入公共配置。

可用以下只读入口查看最终解析：

```bash
python3 skills/android-change-workflow/scripts/resolve_android_practices.py \
  --project-root "$PWD" --workflow-action analysis --component-layer platform
```

Provider、coding decision 和 execution decision 均使用本插件内置 schema 验证，
运行时不读取仓库根 contracts。Provider 只给决策；assignment、实际执行、rollout
effect ceiling 与最终 requirement acceptance 始终由 `android-change-workflow` 掌握。

Provider Skill 是用户安装并信任的代码/指令，不是 OS sandbox。Core 绑定 active plugin
identity/version、manifest SHA、Skill/agent metadata/decision entrypoint 内容 hash，并在
使用前验证 closed output 与 controller expected run/stage/context。机器保证替换检测和
不授予 controller 权限，不声称任意 custom provider 进程在操作系统层面无副作用。

## Component 与提交边界

Canonical component layer 只有 application/platform/native/hal/kernel/device/build。
旧 `change_domain` 仅按冻结映射转换为 layer；`vendor` 不是 layer。

任何 layer 的完整验证结果都由 `android-patch-capture` 直接生成最终
`knowledge-incoming-package/1/android_change` 包，再交 `akbs-patch-submit` 做本地检查、
prepare 和提交。七层分类只增加 `components[].layer`，其余字段和上传生命周期保持
稳定 v1。既有 `framework_change` 历史包只读兼容，不改写归档字节。

Codex 编写的当前源码变更使用 `capture_remote_snapshot.py --package` 一步获取远端最新
快照并立即调用原子打包器。旧两步接口继续兼容，但不再是默认人工流程。

## Source access

公开入口只有 `android-source-access`。`android_source_access.py` 先以本机事实识别
WSL 或 macOS，再执行插件内对应 adapter；普通 Linux 和错误主机命令均在副作用前失败。
既有 `$HOME/.servers`、macOS Keychain 与 `$HOME/work` 身份原地读取，不复制凭据。
