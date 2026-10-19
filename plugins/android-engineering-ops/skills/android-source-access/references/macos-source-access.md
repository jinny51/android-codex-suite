# macOS Source Access Adapter

This reference describes the macOS adapter inside `android-engineering-ops`, reached
through the single public `android-source-access` Skill and its command dispatcher.
No separate platform plugin is installed. The dispatcher verifies the actual host
before selecting the matching in-plugin adapter. Like the WSL adapter, it maps each
project to `$HOME/work/<platform>/<project>` by default and owns Samba share discovery,
native SMB mounting, mount metadata validation, and project mapping registration.

## Remote-Only Source Contract

The mounted Android path has exactly two consumers: a human performing source
CRUD, and the local artifact bridge reading confirmed product outputs for local
`adb`. It is not a Codex source workspace. Codex must not walk, inspect, search,
edit, diff, patch, or run `git`, `repo`, or builds through the SMB mount.

All Codex operations involving Android source or `REMOTE_ROOT` metadata,
including platform/project recognition, must run through the stable
`android-remote-channel` tmux session. Direct SSH is allowed here only for
infrastructure: resolving SSH configuration/reachability, installing a public
key, reading or updating Samba configuration, and reloading Samba. Infrastructure
SSH must not inspect or mutate `REMOTE_ROOT`.

## Boundary

This skill owns:

- Samba share discovery on remote build servers (read `/etc/samba/smb.conf` over SSH).
- macOS native SMB mount (`mount -t smbfs`), no extra software required.
- Mount metadata validation without reading mounted Android source contents.
- Registration of platform/project facts supplied by the user or by a remote-channel source inspection.
- Project mapping registry under `~/.servers/projects/<server>.json`.
- Remount/recovery from saved projects.
- Passwords in macOS Keychain, with password-free references under `~/.servers/credentials/`.

Do not use this skill for:

- Build/deploy: use `android-remote-build-deploy`.
- Framework diagnosis/verification: use `android-change-workflow`.
- Knowledge search: use `akbs-knowledge-search`.

## Platform Difference from WSL

Both in-plugin platform adapters use the same local project shape for the human/artifact mount: `$HOME/work/<platform>/<project>`. The differences are implementation details:

- WSL mounts with `mount.cifs` and local sudo; macOS mounts with native SMB and normally needs no sudo.
- WSL keeps verified local credentials in its platform state; macOS stores passwords in Keychain.
- Both receive platform and project facts from remote-channel source inspection, never from local mounted-tree inspection or remote path text alone.
- Project-level Samba shares are the default. A parent share is an explicit exception, not a separate macOS directory model.

## Flow

```
1. resolve SSH_HOST + explicit REMOTE_ROOT
2. ensure android-remote-channel and inspect platform/project remotely
3. discover-samba-share.sh  → 列出服务器 Samba 共享和远端路径
4. mount-share.sh           → 挂载项目 share 到 $HOME/work/<platform>/<project>
5. register-project.sh      → 注册已确认的 remote identity 到 ~/.servers/projects/<server>.json
```

`detect-projects.sh` is a compatibility-named remote-only adapter. It requires
the core inspection helper and `android-remote-channel`; it never walks the
mounted tree or invokes SSH directly.

When an explicit `--platform` or `--sdk-name` conflicts with remote inspection,
stop and show both values. Only after the user confirms that exact conflict may
the adapter be rerun with `--accept-platform-conflict` or
`--accept-sdk-name-conflict`. Each acceptance flag requires its matching explicit
value and is forwarded to the core inspector; it is not a general bypass.

恢复流程：

```
restore-mounts.sh → 从 registry 恢复所有已记录的项目挂载
```

## Credential Storage

密码只保存到 macOS Keychain；`~/.servers/credentials/` 只保存无密码的 Keychain 引用和状态。

```text
~/.servers/
├── credentials/
│   ├── <sha256(remote-user@server)>.keychain.env    # Keychain 引用（无密码）
│   └── local.keychain.env                             # 本机 sudo Keychain 引用
└── projects/
    └── <server>.json                                   # 项目和 share 映射（无密码）
```

### Keychain Service 命名

| 角色 | Service 格式 |
|---|---|
| SSH | `codex.android-mac-source-access.ssh.<hash>` |
| SMB/Samba | `codex.android-mac-source-access.smb.<hash>` |
| Remote sudo | `codex.android-mac-source-access.remote-sudo.<hash>` |
| Local sudo | `codex.android-mac-source-access.local.<local-hash>` |

### <hash>.keychain.env 内容

```bash
ACCOUNT_KEY=<sha256 hash>
REMOTE_USER=test55
SERVER=192.168.100.6

SSH_KEYCHAIN_SERVICE=codex.android-mac-source-access.ssh.<hash>
SMB_KEYCHAIN_SERVICE=codex.android-mac-source-access.smb.<hash>
REMOTE_SUDO_KEYCHAIN_SERVICE=codex.android-mac-source-access.remote-sudo.<hash>

SSH_PASSWORD_STATE=stored|missing|failed
SMB_PASSWORD_STATE=stored
REMOTE_SUDO_PASSWORD_STATE=missing

UPDATED_AT=2026-06-16T12:00:00+08:00
```

### 密码保存规则

"验证后才保存" — 不因为用户输入了密码就保存。只有对应角色实际操作成功后才写入 Keychain。

| 角色 | 验证条件 |
|---|---|
| SSH | SSH key bootstrap 成功后保存 |
| SMB | smbutil view / mount_smbfs 认证成功后保存 |
| Remote sudo | 远端 sudo 操作成功后保存 |
| Local sudo | 本机 sudo 操作成功后保存 |

### 密码读取优先级

```text
1. 本次运行时显式输入密码
2. Keychain 中已保存的密码
3. 本次 bare fallback 密码
4. 提示用户输入
```

Do not package credentials when distributing this skill.

## Platform Detection

Platform is determined by source evidence inspected on `REMOTE_ROOT` through `android-remote-channel`:
- `device/rockchip` → `rk`
- `vendor/sprd` or `device/sprd` → `unisoc`
- `vendor/mediatek` → `mtk`

Directory names (e.g., `unisoc/`, `rk3576/`) are NOT used as platform signals.

## Project Naming

Project/SDK name is determined by a remote-channel inspection of:
1. Key repo branches: `frameworks/base`, platform `device/...`, `vendor/.../common`, `kernel`
2. `BRANCH_BUILDTYPE` from build config
3. Ask user if neither works

`PRODUCT_NAME` / lunch target is a build product name, NOT the SDK/project name.

## Local Mount Path Convention

```text
$HOME/work/<platform>/<project>/   → 默认项目级挂载点
$HOME/work/<platform>/             → 仅在成员明确要求父 share 时使用
```

For test61 project `TVE1088U`:

```text
$HOME/work/unisoc/TVE1088U/  → project root
```

## Scripts

- `scripts/discover-samba-share.sh`: discover available Samba shares from remote server's `/etc/samba/smb.conf` over SSH.
- `scripts/mount-share.sh`: mount a Samba share via macOS native `mount -t smbfs`.
- `scripts/detect-projects.sh`: invoke the core inspector through `android-remote-channel` without reading the mount.
- `scripts/register-project.sh`: register project mapping in `~/.servers/projects/<server>.json`.
- `scripts/unmount-share.sh`: unmount a Samba share.
- `scripts/restore-mounts.sh`: remount all projects from the local registry (reboot/restart recovery).

## Registry Format

`~/.servers/projects/<server>.json`:

```json
{
  "server": "test61",
  "server_ip": "192.168.100.23",
  "smb_user": "test61",
  "identity_schema": "android-remote-project-identity-v1",
  "shares": {
    "TVE1088U": {
      "mount_point": "$HOME/work/unisoc/TVE1088U",
      "smb_path": "unisoc/huiwei_uis7885_5g",
      "remote_path": "/home/test61/unisoc/huiwei_uis7885_5g",
      "smb_user": "test61",
      "mount_transport": "smbfs",
      "projects": {
        "TVE1088U": {
          "identity_schema": "android-remote-project-identity-v1",
          "project_id": "unisoc-TVE1088U",
          "ssh_host": "test61",
          "platform": "unisoc",
          "local_path": "$HOME/work/unisoc/TVE1088U",
          "artifact_bridge_path": "$HOME/work/unisoc/TVE1088U",
          "mount_transport": "smbfs",
          "remote_path": "/home/test61/unisoc/huiwei_uis7885_5g",
          "remote_root": "/home/test61/unisoc/huiwei_uis7885_5g"
        }
      }
    }
  }
}
```

## Handoff

After successful remote-channel inspection, mount, and register, hand project
work to `android-change-workflow` and `android-remote-build-deploy`
with `SSH_HOST`, `REMOTE_ROOT`, `PLATFORM`, and `SDK_NAME`. Continue all Codex
source work through the same remote channel.

## Output

Report results in Chinese with technical identifiers in English:

```text
Samba 共享: //192.168.100.23/unisoc/huiwei_uis7885_5g
挂载点: $HOME/work/unisoc/TVE1088U
发现项目: TVE1088U (平台: unisoc)
远端路径: /home/test61/unisoc/huiwei_uis7885_5g
注册状态: 已记录
交接: 可交给 android-remote-build-deploy
```

## Safety Rules

- Store passwords only in macOS Keychain. Keychain reference files under `~/.servers/credentials/` must use mode `600`.
- Keep local paths portable in the registry as `$HOME/...`; `smb_path` records the server-relative Samba path and may include subdirectories below the top-level share.
- Use `$HOME/akbs` as the default `AKBS_ROOT` and `$HOME/work` as the default `ANDROID_WORK_ROOT`; never mount Android source below AKBS_ROOT.
- Never put credentials in skills, repo files, or build scripts.
- Do not unmount or replace an existing mount unless the user explicitly asks.
- Do not use the SMB mount for any Codex source read, write, search, edit, `git`, `repo`, patch, checkpoint, or build operation.
- Use the mount only for human source CRUD and confirmed product-output artifact delivery.
- If the mount target directory is non-empty, refuse to mount over it.
