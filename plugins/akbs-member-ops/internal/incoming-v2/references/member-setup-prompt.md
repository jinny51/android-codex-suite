# AKBS 成员首次启用提示词

把下面整段交给成员端 Codex。成员只需提供自己的成员标识和姓名；不要收集
token、cookie、客户端 IP 声明、服务器路径或数据库地址。

```text
请帮我启用 AKBS Member Ops。

1. 在创建或修改任何成员配置前，先运行只读机器门禁：

   python3 "<akbs-member-setup>/scripts/akbs_member_setup.py" \
     preflight-install-family

   只有返回码为 0 且 JSON `status=PASS` 才继续。它必须证明唯一 target family；
   `akbs-member-ops` 不能与旧 `android-framework-ops`/`android-wsl-ops`/
   `android-mac-ops` 同时启用。失败时不要创建、覆盖或修补配置。
2. 插件更新后检查当前会话加载的 Skill 缓存版本。若会话仍使用旧缓存，停止
   生成和上传，并让我开启新会话。
3. 仅在第 1 步门禁通过后创建 `$CODEX_HOME/akbs-member-ops.toml`，至少写入：

   default_profile = "<member_alias>"

   [profiles.<member_alias>]
   member_alias = "<member_alias>"
   member_name = "<member_name>"
   role = "member"
   allowed_modes = ["daily", "weekly", "patch"]
   timezone = "Asia/Shanghai"
   knowledge_repo_worktree = "$CODEX_HOME/worktrees/knowledge"
   synthetic_data = false

   新产物固定写入 `$CODEX_HOME/artifacts/akbs-member-ops`。只要 target 配置存在，
   它就是唯一 AKBS 配置权威，不探测、解析或为冲突检查读取旧配置。仅当 target
   缺失时，旧 `android-knowledge-intake.toml`、`android-knowledge-search.toml` 和
   `report/config.toml` 才可提供兼容成员身份。项目 `.codex/report.toml` 只可继续
   提供非身份的旧项目设置，永远不能提供或覆盖 `member_alias`、成员姓名或 profile；
   始终不覆盖、搬移或删除旧文件。
4. 运行：

   python3 "<akbs-member-setup>/scripts/akbs_member_setup.py" doctor \
     --profile <member_alias> --strict --check-remote

5. doctor 通过前不要生成日报、周报或补丁包。本地知识工作树不存在时不创建、
   不克隆；AKBS API 仍是默认检索和上传入口。
   本地 alias 配置仅是 configured_unverified；必须在 --check-remote 下取得
   server_confirmed，才能报告服务器已确认身份。网络失败、旧服务器不支持接口、
   身份拒绝或 alias 不一致时如实报告，不修改成员标识或伪造来源 IP 来通过检查。
   管理员本人参与开发也用普通 member profile；是否排名及日报/周报是否应交读取
   服务器策略，不通过本地 role 或 allowed_modes 猜测。

完成后只报告插件/会话版本、目标配置读取状态、endpoint 状态、成员 alias 状态、
可选离线索引状态和 doctor 结果。
```
