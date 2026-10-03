# android-change-workflow

> GitHub 说明页。Runtime Skill 位于 [../../../../plugins/android-engineering-ops/skills/android-change-workflow](../../../../plugins/android-engineering-ops/skills/android-change-workflow)。

Android 七层工程总流程。新任务先完成一次安装检查，再解析可选编排扩展；没有配置时
由当前任务直接实施。`jinny` 和 `custom` 都只返回一个 orchestrator Skill，核心不会
接收任务分类、模型路由、worker assignment/result 或第二次验收。

当前任务始终负责用户目标、工程集成和最终答复。扩展可按需组织真实子智能体，但
Android policy、源码权威、远端通道、构建/设备安全、capture 和提交授权仍由核心 Skill
控制。显式选择的扩展异常时在源码工作前失败关闭，未选择的插件不会被检查。

Extension 按项目配置优先于本地配置解析；选择 provider 后只从 Codex active installed+enabled inventory 取得固定插件根，异常 fail closed，能力缺失或不适用才回 core。

知识搜索 Gate 2 先按功能目标找到 Case，再选择具体 Implementation，读取完整实现、
精确绑定原件和分别声明的环境。查询构造只遵守 `akbs-knowledge-search` 的唯一规则：
保留完整行为与字面锚点，不将整段对话当查询；目标环境和明确分类单独传递。
版本、芯片和项目不设全局固定优先级；HAL/BSP 的平台差异
可能比 Framework 更大，最终仍需目标环境验证。服务失败或单次空结果不能宣称库里没有解法。
将搜索回执路径/hash传给 capture，保留“开发前判断”与“实施后验证”的区别。

产品源码通常在远端服务器通过 SSH 定制，独立 App 通常在本地开发后推至 SysPros。先按实际 Git 仓库判断改动是否属于目标 Android 产品源码；是否连过 SSH 不单独决定 Patch 资格。七层只给合格的 Patch 分类，独立 App 源码即使生成的 APK 被产品采用，也不能改标为 `application` 后上传。

Canonical layer 只有 application/platform/native/hal/kernel/device/build。合格且已验证的产品源码变更先由 `android-patch-capture` 生成逐仓 patch 与工程证据，再由 `akbs-patch-submit` 构造、检查、准备并提交唯一的 `knowledge-incoming-package/2/android_change` 最终包。产品侧 APK 集成配置改动可按其产品源码仓单独采集；来源未明确时保留本地材料。七层分类由最终包的 `components[].layer` 记录，工程 capture 不另设上传格式或生命周期。

既有且经核实的产品 Git 补丁可走 capture 的 `manual_import`，单份补丁也可走 submit 的 `manual_import` 直入；两者仍是同一个最终包合同。既有手工补丁不要求远端 snapshot，但应核对真实产品仓、可取得的 Git 修订和原补丁修改路径；混入独立 App 源码的原件不得静默删段后上传。本次由 Codex 在本地产品 Git 中做的改动不能仅因源码在本地就改标 `manual_import`；现行采集器还不支持该来源的 `current_codex_skill` 路线，材料暂留本地。
