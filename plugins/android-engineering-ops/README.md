# Android Engineering Ops

`android-engineering-ops` 3.0.0 是独立的 Android 工程核心。默认不需要任何
编排插件：没有配置扩展时，当前任务直接使用核心 Skill 完成工作。

| Skill | 职责 |
| --- | --- |
| `android-change-policy` | 七层 component 强制 policy 和 patch 归档规则 |
| `android-change-workflow` | Android 工程总流程、集成和最终验收 |
| `android-source-access` | 自动识别 WSL/macOS 并分派平台 adapter |
| `android-remote-channel` | 远端 source/build 命令、锁和恢复 |
| `android-remote-build-deploy` | 受控 build、artifact 校验和 adb 交付 |
| `android-patch-capture` | 直接生成七层 component 标注的稳定 v1 `android_change` 包 |

## 可选编排扩展

扩展只决定“这个任务如何组织”，不改变 Android 工程规则。项目配置优先于用户配置：

```text
<project>/.codex/android-engineering.toml
$CODEX_HOME/android-engineering-ops.toml
```

没有配置或以下配置都表示核心直接执行：

```toml
[extension]
mode = "none"
```

选择官方 Jinny 实现：

```toml
[extension]
mode = "jinny"
```

选择成员自定义实现：

```toml
[extension]
mode = "custom"
plugin_name = "my-android-orchestrator"
```

自定义插件只需在固定路径
`contracts/android-orchestration-extension/v1/extension.json` 声明一个
orchestrator Skill。核心协议不规定角色、模型、任务分类、worker 文档或执行状态机。
显式选择的扩展缺失或损坏时失败关闭；未选择时核心不会读取或校验它。

可用只读入口查看解析结果：

```bash
python3 skills/android-change-workflow/scripts/resolve_android_orchestration.py \
  --project-root "$PWD"
```

旧配置中的 provider 版本和 hash 字段只为读取迁移而忽略，不再参与运行时协议。

## 工程与提交边界

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

六个核心 Skill 共用 `lib/android_engineering_ops/task_start.py` 做一次任务启动和
安装检查。可选编排扩展不参与核心插件安装族校验，也不会因安装而改变默认行为。
