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
python3 "scripts/akbs_patch_submit.py" --profile <member_alias> --submit-latest
```

本地检查沿用 v1：manifest、README、patch 和 evidence 路径必须存在且安全；
`components` 只能使用七个 layer，并且 `files.patches` 中每个路径必须恰好出现一次。
Evidence 的 `kind` 只读取 evidence JSON 自身字段。

本地 PASS 只说明包结构、引用和字节一致。服务端仍负责鉴权、授权、入库、去重和
最终上传结果。网络结果不确定时重试同一个包，不要改名或重新生成身份。

`prepare` 把输入字节原样保存到：

```text
$CODEX_HOME/artifacts/akbs-member-ops/incoming/pending/<member_alias>/<run_id>/
```

准备和提交用法：

```bash
python3 "scripts/akbs_patch_submit.py" --profile <member_alias> --prepare \
  --patch-package /path/to/capture --project TVE8402M \
  --platform rk --android-version 14 --summary "功能补丁摘要" --status validated
python3 "scripts/akbs_patch_submit.py" --profile <member_alias> --submit-latest
```

需要修改源码或重新抓取时使用 `$android-patch-capture`，然后直接检查、准备或提交
它生成的整个目录。不要手工拼 manifest。历史 `framework_change` 只读兼容；当前
工具只写 `android_change`。管理员侧知识审核仍在上传后独立进行。
