# akbs-patch-submit

成员侧只负责最终补丁包的读取、检查、准备和上传。当前支持两种输入格式：

- legacy `knowledge-incoming-package/1/framework_change`；
- 最终 v2 `akbs-android-change-package-v2/2/android_change`，覆盖
  `application`、`platform`、`native`、`hal`、`kernel`、`device`、`build`。

两者走同一补丁上传生命周期：使用同一个成员 profile、补丁上传接口、幂等重试
规则和普通回执。V2 不再拥有转换器、中间状态、客户端资格结论、试点开关或另一套
服务器流程，也不会回退为 v1。

业务动作前必须运行 `akbs_member_setup.py preflight-install-family` 并取得
`status=PASS`。只有真正的 parser `--help` 可以跳过；`--` 后的字面
`--help` 仍是业务输入。

`android-patch-capture` 已经直接生成最终 v2 目录，可以原样交给以下命令：

```bash
python3 "scripts/akbs_patch_submit.py" android-change-v2 read /path/to/package
python3 "scripts/akbs_patch_submit.py" android-change-v2 check /path/to/package
python3 "scripts/akbs_patch_submit.py" android-change-v2 prepare /path/to/package
python3 "scripts/akbs_patch_submit.py" android-change-v2 submit /path/to/package \
  --profile <member_alias>
```

本地检查内容保持精确但简单：

- manifest 必须符合插件内置 v2 schema；
- README、每个 patch 和 evidence 的相对路径、SHA-256、字节数与实际文件一致；
- 清单必须等于目录里的全部 payload regular files，不允许 symlink 或额外文件；
- component/source/patch/evidence ID 唯一且引用有效；
- 每个 component 至少有一个 patch 和一项 evidence；每个 source 都被 patch 使用；
- 正式平台只能是 `mtk`、`rk`、`unisoc`，Android 版本单独存放。

Capture CLI 可临时接收 `mtk16`、`rk14`、`unisoc13`，生成 manifest 前会拆成
规范平台和 Android 版本。正式包中出现 `mtk16` 这类组合值会被拒绝；`sprd13`、
`u13` 等别名也不再接受。

本地 PASS 只说明包结构、引用和字节一致。服务端仍负责鉴权、授权、入库、去重和
最终上传结果。网络结果不确定时重试同一个包，不要改名或重新生成身份。

`prepare` 把输入字节原样保存到：

```text
$CODEX_HOME/artifacts/akbs-member-ops/android-change-v2/pending/<member_alias>/<run_id>/
```

Legacy Framework v1 用法保持：

```bash
python3 "scripts/akbs_patch_submit.py" --profile <member_alias> --prepare \
  --patch-package /path/to/capture --project TVE8402M \
  --platform rk --android-version 14 --summary "功能补丁摘要" --status validated
python3 "scripts/akbs_patch_submit.py" --profile <member_alias> --submit-latest
```

需要修改源码或重新抓取时使用 `$android-patch-capture`，然后直接检查、准备或提交
它生成的整个目录。不要手工拼 manifest，不要把 v2 或非 Framework 组件改写成
`framework_change`。管理员侧知识审核仍在上传后独立进行。
