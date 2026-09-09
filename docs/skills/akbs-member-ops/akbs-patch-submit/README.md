# akbs-patch-submit

成员侧 Android change 入口，按精确合同分成三条不会互相降级的路径：

读取、检查、prepare、capture preflight、归档或服务端请求前，必须先运行
`akbs_member_setup.py preflight-install-family` 并取得 `status=PASS`。只有真正的
parser `--help` 可以跳过；`--` 后面的字面 `--help` 仍是业务输入。

- `knowledge-incoming-package/1/framework_change`：永久兼容读取，并继续通过 incoming v1 真实提交。
- `akbs-android-change-package-v2/2/android_change`：支持本地 read/check/prepare 和显式 submit，覆盖七层组件；每个组件仍须满足自己的证据规则。
- `android-patch-capture-package-v2/2.0/android_change_capture`：保留零网络、零写的兼容 preflight；不能把 capture 目录直接交给 `prepare`。
- `android-patch-capture-package-v2/2.1/android_change_capture`：离线 materializer 输入，按 hash-pinned 37 组 qualification 合同生成 canonical v2，支持 application/platform/native/hal/kernel/device/build；跨层包按组件逐项验证。

七层合同必须先在服务器部署，再发布成员插件。服务端同时保留旧两层合同，旧版本已生成但尚未上传的 application/platform 包无需重写。升级后重新适配同一 capture 会复用已经存在且匹配的旧包，保留原始 bytes、合同 hash 和重试身份。新包使用七层合同；不能修改层标签或回退 v1 绕过校验。报错应区分本地转换、包检查和服务器接收，不把本地失败写成服务器拒收。

v2 的本地 PASS 只代表 `client_semantic_coherence_valid`。客户端 adapter outputs 仍是 untrusted input，服务端必须重新计算资格。submit 使用成员已有 profile，通过现有 incoming HTTP 入口提交，普通成员不需要试点 grant。服务器未启用写入或合同不兼容时明确报错，不回退 v1。

所有 v2 真实动作先以 `codex plugin list --json` 证明 target-only active family，并严格绑定唯一 target 条目的 `pluginId`、version、absolute marketplace `source.path` 与当前进程的精确 versioned cache；两边 direct manifest 字节和完整发布内容及 regular-file executable-bit 的规范化树 hash 必须一致（只排除 `__pycache__`/`.pyc`）。命令失败、JSON/version 畸形、symlink、路径/身份/内容不符、混装或目标插件未激活时均 fail closed，`--help` 不受业务 gate 影响。组件只接受合同中的 canonical `layer`、`type`、`partition`、`ownership`；v1 的 `change_domain` 不会被用来推导这些 facet。

```bash
python3 "scripts/akbs_patch_submit.py" android-change-v2 read /path/to/package
python3 "scripts/akbs_patch_submit.py" android-change-v2 check /path/to/package
python3 "scripts/akbs_patch_submit.py" android-change-v2 prepare /path/to/package
python3 "scripts/akbs_patch_submit.py" android-change-v2 submit /path/to/package --profile <member_alias>
python3 "scripts/akbs_patch_submit.py" android-change-v2 adapt-capture /path/to/capture
```

对于 2.0，`adapt-capture` 先按插件内 hash-pinned Draft 2020-12 capture
schema 严格校验，再检查 identity/结构、validated
状态链、local-only authority、除 manifest 自身外的全量 regular-file
SHA-256 inventory、patch SHA-1、`components[]`、`primary_component_id`、每个
repository/patch 的 `component_ids[]`、evidence 与 qualification bindings。
检查成功仍返回非零的结构化 `BLOCKED`，且不创建 canonical 包、
client-adapter outputs、receipt 或伪 PASS。

对于 2.1，同一命令按 component 精确校验证据，并通过 machine-validated
versioned adapter input schema 生成确定性的 hash-bound canonical v2 包；
重复执行复用同一结果，原 capture 不改写。
client adapter 输出仍是 untrusted input，`server_qualified=false`，不发
HTTP，也不进入 v1 fallback；提交是单独的显式操作。

补丁文件名、路径和内容保持不变，包括文件名中的 `@`、`+`；canonical
内部编号单独生成。提交时包内成员身份必须与 profile 一致，重试同一包使用同一
幂等键，并校验服务器回执。网络超时后可重试原包，不要通过改名或重新生成包
编号绕过未知结果。

`prepare` 不生成或补写 adapter PASS，只在完整 schema、profile SHA、qualification hash、组件/证据绑定及目录 bytes 全部通过后，把输入原样保存到：

```text
$CODEX_HOME/artifacts/akbs-member-ops/android-change-v2/pending/<member_alias>/<run_id>/
```

legacy Framework v1-compatible capture 用法保持：

```bash
python3 "scripts/akbs_patch_submit.py" --profile <member_alias> --prepare \
  --patch-package /path/to/capture --project TVE8402M \
  --platform rk --android-version 14 --summary "功能补丁摘要" --status validated
python3 "scripts/akbs_patch_submit.py" --profile <member_alias> --submit-latest
```

需要修改源码或重新抓取补丁时使用 `$android-patch-capture`，随后显式运行
`adapt-capture`；不得把该 capture 直接 `prepare`。旧
`$android-framework-patch-intake` 与 umbrella CLI 仅作 v1 迁移薄转发；不能把
v2 或 `android_change_capture` 静默改写为 `framework_change`。
