# akbs-member-setup

> GitHub 说明页。Runtime Skill 位于
> [plugins/akbs-member-ops/skills/akbs-member-setup](../../../../plugins/akbs-member-ops/skills/akbs-member-setup)。

成员首次启用与客户端健康检查入口。它负责成员 profile、`member_alias`、插件版本、当前
会话缓存和 AKBS endpoint doctor，不生成日报、周报或补丁 incoming。适用对象包括
Android 各工程层、GMS 和仅填报成员，不限定为 Framework 开发成员。

首次写入配置前先运行只读安装族门禁；只有返回码 0 且 JSON `status=PASS` 才能继续：

```bash
python3 "scripts/akbs_member_setup.py" preflight-install-family
```

```bash
python3 "scripts/akbs_member_setup.py" doctor \
  --profile <member_alias> \
  --strict \
  --check-remote
```

Doctor 以 `codex plugin list --json` 为 active-install 权威，并要求唯一启用的
`akbs-member-ops@android-codex-suite` 条目把绝对 marketplace
`source.path` 绑定到当前进程的精确 versioned Codex cache。两个目录应不同，但两边
直接 `.codex-plugin/plugin.json` 的字节、name/version 与完整发布内容及
regular-file executable-bit 的规范化树 hash 必须一致；仅排除 `__pycache__`
与 `.pyc` 运行缓存。命令失败、JSON/version 畸形、
symlink、重复、source/cache/内容不符、从 checkout 执行业务或新旧代混装均阻断；
checkout 只能作为开发证据。

本地配置了 alias 只会显示 `configured_unverified`，不代表服务器已登记或认可该身份。
`--check-remote` 通过只读身份接口确认本人，且服务器 alias 与本地一致后，才显示
`server_confirmed`；旧服务器不支持、网络失败、身份拒绝或不一致都不能当作确认成功。
服务器返回的在职状态、是否参与排名、日报/周报是否应交分别展示，不推断成全部业务权限。
管理员参与开发、检索和贡献时也使用普通成员 profile，不需要管理员权限。

首次配置读取共享内核中的唯一
`plugins/akbs-member-ops/internal/incoming-v1/references/member-setup-prompt.md`，避免维护两套身份和 endpoint 规则。
现行入口统一为 `akbs-member-setup doctor`。

只要 `$CODEX_HOME/akbs-member-ops.toml` 存在，它就是唯一 AKBS 配置权威；此时不探测、
不解析、也不为冲突检查读取任何旧成员/搜索/报告配置。仅当 target 文件缺失时才读取旧配置。
`$CODEX_HOME/android-engineering-ops.toml` 的 `[identity].member_alias` 只是在没有 AKBS
profile 时供 Android 归档署名使用的严格 standalone fallback，不是第二套 AKBS profile。
