# android-change-workflow

> GitHub 说明页。Runtime Skill 位于 [../../../../plugins/android-engineering-ops/skills/android-change-workflow](../../../../plugins/android-engineering-ops/skills/android-change-workflow)。

Android 七层工程总流程。新任务先完成一次安装检查，再解析可选编排扩展；没有配置时
由当前任务直接实施。`jinny` 和 `custom` 都只返回一个 orchestrator Skill，核心不会
接收任务分类、模型路由、worker assignment/result 或第二次验收。

当前任务始终负责用户目标、工程集成和最终答复。扩展可按需组织真实子智能体，但
Android policy、源码权威、远端通道、构建/设备安全、capture 和提交授权仍由核心 Skill
控制。显式选择的扩展异常时在源码工作前失败关闭，未选择的插件不会被检查。

Extension 按项目配置优先于本地配置解析；选择 provider 后只从 Codex active installed+enabled inventory 取得固定插件根，异常 fail closed，能力缺失或不适用才回 core。

Canonical layer 只有 application/platform/native/hal/kernel/device/build。任何 layer 的已验证变更都由 `android-patch-capture` 直接生成 `knowledge-incoming-package/2/android_change` 包，再把同一目录交给 `akbs-patch-submit` 检查、准备或提交。七层分类只增加 `components[].layer`，不存在第二套补丁格式或生命周期。
