# android-change-workflow

> GitHub 说明页。Runtime Skill 位于 [../../../../plugins/android-engineering-ops/skills/android-change-workflow](../../../../plugins/android-engineering-ops/skills/android-change-workflow)。

Android 七层工程总流程。新任务先完成一次安装检查，再解析可选编排扩展；没有配置时
由当前任务直接实施。`jinny` 和 `custom` 都只返回一个 orchestrator Skill，核心不会
接收任务分类、模型路由、worker assignment/result 或第二次验收。

当前任务始终负责用户目标、工程集成和最终答复。扩展可按需组织真实子智能体，但
Android policy、源码权威、远端通道、构建/设备安全、capture 和提交授权仍由核心 Skill
控制。显式选择的扩展异常时在源码工作前失败关闭，未选择的插件不会被检查。

Extension 按项目配置优先于本地配置解析；选择 provider 后只从 Codex active installed+enabled inventory 取得固定插件根，异常 fail closed，能力缺失或不适用才回 core。

产品源码通常在远端服务器通过 SSH 定制，独立 App 通常在本地开发后推至 SysPros。先按实际 Git 仓库判断改动是否属于目标 Android 产品源码；是否连过 SSH 不单独决定 Patch 资格。七层只给合格的 Patch 分类，独立 App 源码即使生成的 APK 被产品采用，也不能改标为 `application` 后上传。

Canonical layer 只有 application/platform/native/hal/kernel/device/build。合格且已验证的产品源码变更先由 `android-patch-capture` 生成逐仓 patch 与工程证据，再由 `akbs-patch-submit` 构造、检查、准备并提交唯一的 `knowledge-incoming-package/2/android_change` 最终包。产品侧 APK 集成配置改动可按其产品源码仓单独采集；来源未明确时保留本地材料。七层分类由最终包的 `components[].layer` 记录，工程 capture 不另设上传格式或生命周期。
