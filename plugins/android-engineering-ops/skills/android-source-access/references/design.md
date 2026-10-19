# Android Source CIFS Mount Design

Use this as the design baseline before changing scripts. The goal is a predictable mount agent, not a clever guessing agent.

## User Contract

Treat a user-provided remote path as an Android SDK source root. Do not use its
parent folder names or final basename to decide the platform or project name.

Common request:

```text
把/home/test55/work/unisoc/rk3576挂载一下，密码123
```

Extract:

- `remote_root=/home/test55/work/unisoc/rk3576`
- `remote_user=test55`
- bare password `123` as a runtime fallback for missing typed passwords; each role tries explicit typed password, then saved typed password, then the runtime fallback
- optional explicit platform or project overrides only when the user states them

## Non-Expansion Rules

Do not fan out when required connection facts are missing.

- If the user provides `ip192.168.x.x`, use `<remote_user>@IP` first.
- Otherwise read WSL and Windows SSH config only.
- If no usable SSH HostName/IP is found, stop and ask for the server IP.
- Do not rely on system DNS for names like `test55`.
- Do not scan networks, guess hosts, or try unrelated aliases.
- If a password is needed and no user-provided or saved password is available, stop and ask for that role's password.
- Do not try empty passwords, common passwords, or unrelated historical passwords.

## Decision Order

### Platform

Use this priority:

1. User-stated platform, such as `rk平台`.
2. Source-tree evidence from the remote SDK root.
3. If neither user input nor source-tree evidence gives a platform, stop and ask.

If user-stated platform conflicts with source-tree evidence, stop and ask which
platform to use. Do not silently prefer either side. After the user confirms,
continue only with an explicit acceptance flag such as
`--accept-platform-conflict`.

High-signal source evidence:

```text
rk     -> device/rockchip, vendor/rockchip, hardware/rockchip, TARGET_BOARD_PLATFORM=rk*
unisoc -> device/sprd, vendor/sprd, vendor/unisoc, hardware/sprd, ums/uis/sc/shark/qogir/pike platforms
mtk    -> device/mediatek, vendor/mediatek, device/mtk, vendor/mtk, TARGET_BOARD_PLATFORM=mt*
```

Even if the path contains another platform name, ignore that path segment for
the platform decision.

### SDK/Project Name

Use this priority:

1. User-stated project name.
2. Key repository branch names.
3. Project-level marker such as `BRANCH_BUILDTYPE`.
4. If neither user input nor source-tree evidence gives a project name, stop and ask.

If user-stated project name conflicts with source-tree evidence, stop and ask
which project name to use. Do not silently prefer either side. After the user
confirms, continue only with an explicit acceptance flag such as
`--accept-sdk-name-conflict`.

Prefer branches from:

```text
frameworks/base
device/<vendor>/<soc>
vendor/<vendor>/common
kernel
u-boot
```

Ignore generic branch names such as `master`, `main`, `develop`, `release`, `stable`, `HEAD`, and Android tag branches. Treat Android `PRODUCT_NAME`, lunch targets, and `TARGET_PRODUCT` as build product diagnostics, not business project names.

Example:

```text
remote_root=/home/test55/work/unisoc/rk3576
source evidence: device/rockchip + TARGET_BOARD_PLATFORM=rk3576
frameworks/base branch: TVA10A2R
platform=rk
sdk_name=TVA10A2R
local_project=/home/<wsl-user>/work/rk/TVA10A2R
```

## First-Time Mount Pipeline

Keep the pipeline linear:

1. Parse user input into `remote_root`, optional IP, optional platform/project overrides, optional server-side password defaults, optional local WSL sudo password, and optional role-specific password overrides.
2. Plan only path-derived connection basics: `remote_user`, initial `SSH_HOST`, and `remote_root`.
3. Resolve SSH candidates from explicit IP or SSH config. Stop if no IP/HostName is available.
4. Choose a reachable SSH candidate by checking the remote SDK root exists.
5. Inspect the remote SDK root through `android-remote-channel` to infer platform and project name.
6. If platform or project name is still missing, stop and ask for the missing value.
7. Build the final local path: `/home/<wsl-user>/work/<platform>/<sdk_name>`.
8. Use project-level mounting by default: mount the remote SDK root to `/home/<wsl-user>/work/<platform>/<sdk_name>`.
9. Discover an existing Samba share covering the remote SDK root.
10. If missing, create a project-level Samba share for the remote SDK root. Parent/platform shares are explicit exceptions only.
11. Before mounting, verify the local platform directory is not itself a mount point and the exact target directory is empty or absent. Refuse to mount over non-empty directories.
12. Mount, verify mount metadata without probing mounted source, reuse the remote-channel SDK facts, remember recovery metadata, and hand off to build/deploy work.

## Credential Scope

Keep remote account secrets and local WSL secrets scoped separately.

- Account-level password files are keyed by Samba user and server. They store server facts and server-side typed passwords: SSH bootstrap, Samba, and remote sudo.
- The local WSL sudo fallback password belongs to the current WSL user, not to any remote server. Store it once at `credentials/local-sudo.env`.
- Explicit `WSL密码...`, `本机密码...`, or `本地sudo密码...` targets `local-sudo.env`; explicit `服务器密码...` or `远端密码...` targets the remote account's SSH/Samba/remote-sudo typed passwords for this run.
- A bare `密码...` is a runtime fallback, not a persisted server field. Each role resolves explicit typed password, then saved typed password, then the runtime fallback.
- If the runtime fallback succeeds for a role, save it as that role's typed password. Runtime bare passwords may be used for local sudo only when no saved local sudo password exists.
- A role counts as succeeded only when the workflow observes role-specific evidence: `SSH_KEY_INSTALLED` for SSH password bootstrap, `SAMBA_AUTH mode=password` after a CIFS mount for Samba, `REMOTE_SUDO_AUTH mode=password` during Samba auto-config for remote sudo, and `LOCAL_SUDO_AUTH mode=password` during local CIFS mount setup for WSL sudo.
- Restore and mount flows may read account-level `SAVED_LOCAL_SUDO_PASSWORD` values from `.passwords.env` files; new writes use only `local-sudo.env`.

## Module Boundaries

- `plan-from-remote-path.sh`: parse only connection basics such as `remote_user`, `SSH_HOST`, and `remote_root`; never infer platform or project name from path segments.
- `resolve-ssh-candidate.sh`: resolve and rank SSH targets from explicit IP or SSH configs; do not use DNS guessing as a fallback.
- `inspect-android-sdk.*`: inspect the remote SDK root and produce authoritative `PLATFORM`, `SDK_NAME`, `PROJECT_BRANCH`, `ANDROID_PRODUCT_NAME`, and `TARGET_BOARD_PLATFORM`; return `PLATFORM_REQUIRED` or `SDK_NAME_REQUIRED` instead of using path fallback, return `PLATFORM_CONFLICT` or `SDK_NAME_CONFLICT` when explicit user input disagrees with source evidence, and proceed through conflicts only with explicit user-confirmation flags.
- `mount-from-remote-path.sh`: orchestrate the pipeline; keep recognition rules out of this script.
- `discover-samba-share.sh`: map a remote root to an existing Samba URL.
- `ensure-samba-share.sh`: create or validate Samba shares. Prefer project-level shares for SDK-root mounting; parent/platform shares are explicit exceptions only.
- `mount-platform.sh`: perform CIFS mount and local sudo handling.
- `restore-project-mount.sh`: list, remember, and restore exact project mounts.

## Refactor Direction

Prefer keeping mount/sudo/SSH orchestration in shell. Move parsing-heavy recognition into Python when it grows beyond simple checks.

Good Python candidates:

- source-tree platform scoring
- key repository branch collection
- manifest and product file parsing
- structured output validation

Keep shell for:

- `ssh`
- `sudo`
- `mount.cifs`
- `findmnt`
- small env-file orchestration
