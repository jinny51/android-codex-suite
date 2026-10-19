# akbs-patch-submit

成员侧只负责最终补丁包的读取、检查、准备和上传。当前格式只有
`knowledge-incoming-package/2/android_change`；它使用正式 incoming v2 合同并沿用既有上传生命周期，
只增加 `components[].layer` 对每个 patch 做七层分类。

允许的 layer 是 `application`、`platform`、`native`、`hal`、`kernel`、`device`、`build`。

业务动作前必须运行 `akbs_member_setup.py preflight-install-family` 并取得
`status=PASS`。只有真正的 parser `--help` 可以跳过；`--` 后的字面
`--help` 仍是业务输入。

`android-patch-capture` 生成的目录可以原样交给以下命令：

```bash
python3 "scripts/akbs_patch_submit.py" --profile <member_alias> --check-package /path/to/package
python3 "scripts/akbs_patch_submit.py" --profile <member_alias> --prepare \
  --patch-package /path/to/package
```

提交前区分权限：普通成员继续用自身 profile 的 `--submit-latest`，不检查管理凭据。管理员提交本人 Patch 时，由当前会话可用的 `akbs-curation-maintainer` 用既有 `author-identity` 确认真实管理权限与服务端作者绑定；成员端只复用本地 prepare/validate，正式提交和随后的资格判断、知识比较、沉淀均在当前会话经管理 Skill 连续完成。不能凭 alias 或已安装 Skill 认定管理身份，也不能认证失败后悄悄降回成员上传。作者不符时保留原包，不改署名；处理中断沿用同一 `patch_package_id` 和 workflow 恢复，不转交其他会话或 UI。

仅制作、检查或暂存时，不提交也不写知识；明确要求“仅上传”时按已确认角色上传后停止，不处理队列、审核或写入知识。明确不入库的限制不得扩成沉淀授权。

本地检查沿用 v1：manifest、README、patch 和 evidence 路径必须存在且安全；
`components` 只能使用七个 layer，并且 `files.patches` 中每个路径必须恰好出现一次。
Evidence 的 `kind` 只读取 evidence JSON 自身字段。

本地 PASS 只说明包结构、引用和字节一致。服务端仍负责鉴权、授权、入库、去重和
最终上传结果。网络结果不确定时重试同一个包，不要改名或重新生成身份。
普通成员用 `--submit-latest --run-id <原包run_id>` 定位原包重试；不传
`--run-id` 时仍选择最新包。显式指定的原包不存在或不唯一时，不会改传其他包。

Patch 提交只适用于目标 Android 产品源码的改动；独立 App 源码即使生成 Git diff，也不能改标为 `application` 后提交。手工补丁和当前快照遵守同一来源边界，不因捕获方式不同而改变上传资格。单份既有且已核实的产品 Git 补丁可以按 `manual_import` 直接准备，不要求远端 snapshot；不能把本次 Codex 在本地产品 Git 做的新改动改标成导入。直入前须在工程任务核对原补丁修改路径、真实产品仓和可取得的 Git 修订。混有独立 App 源码或来源未明的原件留在本地，不静默过滤后提交。

`prepare` 把输入字节原样保存到：

```text
$CODEX_HOME/artifacts/akbs-member-ops/incoming/pending/<member_alias>/<run_id>/
```

准备和普通成员提交用法：

```bash
python3 "scripts/akbs_patch_submit.py" --profile <member_alias> --prepare \
  --patch-package /path/to/capture --project TVE8402M \
  --platform rk --android-version 14 --summary "功能补丁摘要" --status validated
python3 "scripts/akbs_patch_submit.py" --profile <member_alias> --submit-latest
```

需要修改源码或重新抓取时使用 `$android-patch-capture`，然后直接检查、准备或提交
它生成的整个目录。不要手工拼 manifest。历史 `framework_change` 只读兼容；当前
工具只写 `android_change`。普通成员上传后的管理员审核保持原流程；管理员本人提交按上面的管理通路连续处理，不制造一次自我确认。
