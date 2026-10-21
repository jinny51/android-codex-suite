#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  mount-from-remote-path.sh --remote-root /home/<user>/<platform>/<sdk> [options]

One-shot Android source mount from a remote SDK path:
  1. derive SSH host/user from the remote path
  2. install SSH key if needed
  3. inspect the source tree or use explicit user input for platform/project
  4. discover Samba share, or auto-configure it when missing
  5. mount the SDK root to /home/<wsl-user>/work/<platform>/<sdk>
  6. remember project mount info and Samba credentials for reboot recovery

Options:
  --remote-root PATH       Remote SDK root path. Required.
  --ssh-host HOST          Override SSH host/alias. Default: user segment from remote path.
  --ip IP                  Use <remote-user>@IP as the first SSH/Samba candidate.
  --ssh-config PATH        Extra OpenSSH config to scan for first-time host candidates.
  --connect-timeout N      Seconds for quick SSH candidate checks. Default: 3.
  --local-platform NAME    Override local platform folder when the remote path
                           uses the wrong platform name.
  --sdk-name NAME          Override local SDK/project directory name.
  --mount-root PATH        Local mount root. Default: $ANDROID_WORK_ROOT or $HOME/work.
  --password-env NAME      Remote-role fallback password env var. Default: SERVER_PASSWORD.
  --ssh-password-env NAME  SSH bootstrap password env var. Overrides saved/default.
  --samba-password-env NAME
                           Samba password env var. Overrides saved/default.
  --remote-sudo-password-env NAME
                           Remote sudo password env var. Overrides saved/default.
  --local-sudo-password-env NAME
                           Local WSL sudo password env var. Overrides local-sudo.env.
  --smb-conf PATH          Remote Samba config path. Default: /etc/samba/smb.conf.
  --server-name NAME       Override server name/IP used in //server/share URL.
  --project-level-mount    Mount the discovered project URL to the local project
                           path. This is the default.
  --platform-level-mount   Mount the discovered parent/platform share to
                           $ANDROID_WORK_ROOT/<platform>. Use only when the
                           user explicitly wants the parent share exposed.
  --no-sdk-inspect         Do not inspect the remote source tree. Only valid
                           when --local-platform and --sdk-name are both set.
  --accept-platform-conflict
                           Continue after the user confirms --local-platform
                           should override conflicting source evidence.
  --accept-sdk-name-conflict
                           Continue after the user confirms --sdk-name should
                           override conflicting source evidence.
  --no-auto-samba-config   Do not configure Samba if the share is missing.
  --no-save-credentials    Do not save Samba credentials for reboot recovery.
  --no-save-passwords      Do not save SSH/Samba/remote sudo or local sudo
                           fallback passwords.
  -h, --help               Show this help.

Set SERVER_PASSWORD before running when the user supplied one bare/default
password for remote roles. SSH/Samba/remote sudo prefer an explicit role env,
then the same account's typed saved field, then that explicit remote default.
Local WSL sudo uses only its explicit role env or local-sudo.env, never remote credentials.
USAGE
}

explain_failure() {
  local status="$1"
  case "$status" in
    2)
      echo "FAILED_HINT: 缺少必要参数或密码。请确认远端路径形如 /home/<user>/<platform>/<sdk>；通过 SERVER_PASSWORD 提供 SSH/Samba/远端 sudo 默认密码，通过 --local-sudo-password-env 提供本机 WSL sudo 密码。" >&2
      ;;
    3)
      echo "FAILED_HINT: 远端路径或平台目录不存在，无法继续挂载。" >&2
      ;;
    4)
      echo "FAILED_HINT: 无法读取远端 /etc/samba/smb.conf，请检查 SSH 用户是否有读取 Samba 配置的权限。" >&2
      ;;
    5)
      echo "FAILED_HINT: sudo 权限不足。本机挂载或远端 Samba 配置需要 sudo，当前密码或权限不满足。" >&2
      ;;
    6)
      echo "FAILED_HINT: Samba 配置校验失败，脚本已尝试恢复 smb.conf 备份。" >&2
      ;;
    7)
      echo "FAILED_HINT: Samba 服务 reload/restart 失败，脚本已尝试恢复 smb.conf 备份。" >&2
      ;;
    *)
      echo "FAILED_HINT: 挂载流程失败，请查看上方最后几行关键错误。" >&2
      ;;
  esac
}

run_step() {
  set +e
  "$@"
  local status=$?
  set -e
  if [ "$status" -ne 0 ]; then
    explain_failure "$status"
    exit "$status"
  fi
}

run_step_capture() {
  local output_file="$1"
  shift
  : >"$output_file"
  set +e
  "$@" 2>&1 | tee "$output_file"
  local status=${PIPESTATUS[0]}
  set -e
  if [ "$status" -ne 0 ]; then
    explain_failure "$status"
    exit "$status"
  fi
}

remote_root=
ssh_host_override=
ip_override=
ssh_config=
connect_timeout=3
local_platform_override=
sdk_name_override=
mount_root="${ANDROID_WORK_ROOT:-$HOME/work}"
password_env=SERVER_PASSWORD
ssh_password_env=
samba_password_env=
remote_sudo_password_env=
local_sudo_password_env=
smb_conf=/etc/samba/smb.conf
server_name=
server_name_user_provided=0
project_level_mount=1
sdk_inspect=1
accept_platform_conflict=0
accept_sdk_name_conflict=0
auto_samba_config=1
save_credentials=1
save_passwords=1

while [ "$#" -gt 0 ]; do
  case "$1" in
    --remote-root) remote_root="${2:?missing value for --remote-root}"; shift 2 ;;
    --ssh-host) ssh_host_override="${2:?missing value for --ssh-host}"; shift 2 ;;
    --ip) ip_override="${2:?missing value for --ip}"; shift 2 ;;
    --ssh-config) ssh_config="${2:?missing value for --ssh-config}"; shift 2 ;;
    --connect-timeout) connect_timeout="${2:?missing value for --connect-timeout}"; shift 2 ;;
    --local-platform) local_platform_override="${2:?missing value for --local-platform}"; shift 2 ;;
    --sdk-name) sdk_name_override="${2:?missing value for --sdk-name}"; shift 2 ;;
    --mount-root) mount_root="${2:?missing value for --mount-root}"; shift 2 ;;
    --password-env) password_env="${2:?missing value for --password-env}"; shift 2 ;;
    --ssh-password-env) ssh_password_env="${2:?missing value for --ssh-password-env}"; shift 2 ;;
    --samba-password-env) samba_password_env="${2:?missing value for --samba-password-env}"; shift 2 ;;
    --remote-sudo-password-env) remote_sudo_password_env="${2:?missing value for --remote-sudo-password-env}"; shift 2 ;;
    --local-sudo-password-env) local_sudo_password_env="${2:?missing value for --local-sudo-password-env}"; shift 2 ;;
    --smb-conf) smb_conf="${2:?missing value for --smb-conf}"; shift 2 ;;
    --server-name) server_name="${2:?missing value for --server-name}"; server_name_user_provided=1; shift 2 ;;
    --project-level-mount) project_level_mount=1; shift ;;
    --platform-level-mount) project_level_mount=0; shift ;;
    --no-sdk-inspect) sdk_inspect=0; shift ;;
    --accept-platform-conflict) accept_platform_conflict=1; shift ;;
    --accept-sdk-name-conflict) accept_sdk_name_conflict=1; shift ;;
    --no-auto-samba-config) auto_samba_config=0; shift ;;
    --no-save-credentials) save_credentials=0; shift ;;
    --no-save-passwords) save_passwords=0; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [ -z "$remote_root" ]; then
  echo "REMOTE_ROOT_REQUIRED option=--remote-root" >&2
  explain_failure 2
  exit 2
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
atomic_state="$(cd "$script_dir/../../../lib" && pwd)/akbs_plugin_state/atomic.py"
atomic_state_write_private() {
  local file="$1"
  python3 "$atomic_state" write --path "$file" --mode 600
}
plan_env="$(mktemp /tmp/codex-source-plan.XXXXXX)"
cifs_env="$(mktemp /tmp/codex-source-cifs.XXXXXX)"
resolve_env="$(mktemp /tmp/codex-source-ssh.XXXXXX)"
ensure_output="$(mktemp /tmp/codex-source-ensure.XXXXXX)"
mount_output="$(mktemp /tmp/codex-source-mount.XXXXXX)"
cleanup() {
  rm -f "$plan_env" "$cifs_env" "$resolve_env" "$ensure_output" "$mount_output"
}
trap cleanup EXIT

plan_args=(--remote-root "$remote_root" --mount-root "$mount_root")
if [ -n "$ssh_host_override" ]; then
  plan_args+=(--ssh-host "$ssh_host_override")
fi
if [ -n "$local_platform_override" ]; then
  plan_args+=(--local-platform "$local_platform_override")
fi
if [ -n "$sdk_name_override" ]; then
  plan_args+=(--sdk-name "$sdk_name_override")
fi
run_step "$script_dir/plan-from-remote-path.sh" "${plan_args[@]}" >"$plan_env"
# shellcheck disable=SC1090
source "$plan_env"

provided_default_password="${!password_env-}"
ssh_password_verified=0
samba_password_verified=0
remote_sudo_password_verified=0
local_sudo_password_verified=0

passwords_file_for_account() {
  local user="$1"
  local server="$2"
  local key
  key="$(printf "%s@%s" "$user" "$server" | sha256sum | awk '{print $1}')"
  printf "%s/.servers/credentials/%s.passwords.env" "$HOME" "$key"
}

local_sudo_password_file() {
  printf "%s/.servers/credentials/local-sudo.env" "$HOME"
}

passwords_file_for_ssh_target() {
  local target="$1" ssh_user="" ssh_hostname="" key value
  while read -r key value _; do
    case "$key" in
      user) ssh_user="$value" ;;
      hostname) ssh_hostname="$value" ;;
    esac
  done < <(ssh -G "$target" 2>/dev/null)
  [ -n "$ssh_user" ] && [ -n "$ssh_hostname" ] || return 1
  passwords_file_for_account "$ssh_user" "$ssh_hostname"
}

load_saved_passwords() {
  local file="$1"
  SAVED_SSH_PASSWORD=""
  SAVED_SAMBA_PASSWORD=""
  SAVED_REMOTE_SUDO_PASSWORD=""
  SAVED_LOCAL_SUDO_PASSWORD=""
  if [ -f "$file" ]; then
    # shellcheck disable=SC1090
    source "$file"
  fi
}

load_saved_local_sudo_password() {
  local file="$1"
  SAVED_LOCAL_SUDO_PASSWORD=""
  if [ -f "$file" ]; then
    # shellcheck disable=SC1090
    source "$file"
  fi
}

value_from_env_or_saved_or_default() {
  local env_name="$1"
  local saved_value="$2"
  local default_value="$3"
  local env_value=""
  if [ -n "$env_name" ]; then
    env_value="${!env_name-}"
  fi
  if [ -n "$env_value" ]; then
    printf "%s" "$env_value"
  elif [ -n "$saved_value" ]; then
    printf "%s" "$saved_value"
  else
    printf "%s" "$default_value"
  fi
}

save_password_roles() {
  [ "$save_passwords" -eq 1 ] || return 0
  local file="$1" save_remote="$2" save_samba="$3"
  if { [ "$save_remote" -ne 1 ] || { [ "$ssh_password_verified" -ne 1 ] && [ "$remote_sudo_password_verified" -ne 1 ]; }; } &&
     { [ "$save_samba" -ne 1 ] || [ "$samba_password_verified" -ne 1 ]; }; then
    return 0
  fi
  load_saved_passwords "$file"
  local ssh_to_save="$SAVED_SSH_PASSWORD"
  local samba_to_save="$SAVED_SAMBA_PASSWORD"
  local remote_sudo_to_save="$SAVED_REMOTE_SUDO_PASSWORD"
  if [ "$save_remote" -eq 1 ] && [ "$ssh_password_verified" -eq 1 ] && [ -n "$ssh_password" ]; then
    ssh_to_save="$ssh_password"
  fi
  if [ "$save_samba" -eq 1 ] && [ "$samba_password_verified" -eq 1 ] && [ -n "$samba_password" ]; then
    samba_to_save="$samba_password"
  fi
  if [ "$save_remote" -eq 1 ] && [ "$remote_sudo_password_verified" -eq 1 ] && [ -n "$remote_sudo_password" ]; then
    remote_sudo_to_save="$remote_sudo_password"
  fi
  mkdir -p "$(dirname "$file")"
  chmod 700 "$(dirname "$file")"
  echo "NOTICE: storing SSH/Samba/remote sudo passwords locally at $file" >&2
  {
    printf "SAVED_SSH_PASSWORD=%q\n" "$ssh_to_save"
    printf "SAVED_SAMBA_PASSWORD=%q\n" "$samba_to_save"
    printf "SAVED_REMOTE_SUDO_PASSWORD=%q\n" "$remote_sudo_to_save"
  } | atomic_state_write_private "$file"
  chmod 600 "$file"
}

save_all_passwords() {
  if [ "$passwords_file" = "$samba_passwords_file" ]; then
    save_password_roles "$passwords_file" 1 1
  else
    save_password_roles "$passwords_file" 1 0
    save_password_roles "$samba_passwords_file" 0 1
  fi
}

save_local_sudo_password() {
  [ "$save_passwords" -eq 1 ] || return 0
  [ "$local_sudo_password_verified" -eq 1 ] || return 0
  [ -n "$local_sudo_password" ] || return 0
  local file
  file="$(local_sudo_password_file)"
  mkdir -p "$(dirname "$file")"
  chmod 700 "$(dirname "$file")"
  echo "NOTICE: storing local WSL sudo password locally at $file" >&2
  {
    printf "SAVED_LOCAL_SUDO_PASSWORD=%q\n" "$local_sudo_password"
  } | atomic_state_write_private "$file"
  chmod 600 "$file"
}

resolve_args=(--remote-root "$REMOTE_ROOT" --connect-timeout "$connect_timeout")
if [ -n "$ip_override" ]; then
  resolve_args+=(--ip "$ip_override")
fi
if [ -n "$ssh_config" ]; then
  resolve_args+=(--ssh-config "$ssh_config")
fi

if [ -z "$ssh_host_override" ] || [ -n "$ip_override" ]; then
  run_step "$script_dir/resolve-ssh-candidate.sh" "${resolve_args[@]}" >"$resolve_env"
  # shellcheck disable=SC1090
  source "$resolve_env"
fi

echo "MOUNT_PLAN remote=$REMOTE_ROOT ssh=$SSH_HOST platform=${PLATFORM:-pending} sdk=${SDK_NAME:-pending} local=${LOCAL_PROJECT:-pending}"

choose_ssh_host() {
  local i candidate candidate_server candidate_passwords_file install_output install_status
  local -a candidates servers
  if declare -p SSH_CANDIDATES >/dev/null 2>&1; then
    candidates=("${SSH_CANDIDATES[@]}")
    servers=("${SERVER_NAMES[@]}")
  else
    candidates=("$SSH_HOST")
    servers=("${server_name:-${SSH_HOST#*@}}")
  fi

  for i in "${!candidates[@]}"; do
    candidate="${candidates[$i]}"
    candidate_server="${servers[$i]:-${candidate#*@}}"
    if ssh -o BatchMode=yes -o ConnectTimeout="$connect_timeout" "$candidate" true >/dev/null 2>&1; then
      SSH_HOST="$candidate"
      if [ "$server_name_user_provided" -eq 0 ]; then
        server_name="$candidate_server"
      fi
      return 0
    fi
  done

  for i in "${!candidates[@]}"; do
    candidate="${candidates[$i]}"
    candidate_server="${servers[$i]:-${candidate#*@}}"
    candidate_passwords_file="$(passwords_file_for_ssh_target "$candidate")" || continue
    load_saved_passwords "$candidate_passwords_file"
    ssh_password="$(value_from_env_or_saved_or_default "$ssh_password_env" "$SAVED_SSH_PASSWORD" "$provided_default_password")"
    if [ -z "$ssh_password" ]; then
      continue
    fi
    set +e
    install_output="$(env SSHPASS="$ssh_password" "$script_dir/install-ssh-key.sh" \
      --ssh-host "$candidate" \
      --generate-key 2>&1)"
    install_status=$?
    set -e
    if [ -n "$install_output" ]; then
      printf "%s\n" "$install_output"
    fi
    if [ "$install_status" -eq 0 ]; then
      if ssh -o BatchMode=yes -o ConnectTimeout="$connect_timeout" "$candidate" true >/dev/null 2>&1; then
        SSH_HOST="$candidate"
        if printf "%s\n" "$install_output" | grep -q '^SSH_KEY_INSTALLED '; then
          ssh_password_verified=1
        fi
        if [ "$server_name_user_provided" -eq 0 ]; then
          server_name="$candidate_server"
        fi
        return 0
      fi
    fi
    echo "PASSWORD_REQUIRED env=$ssh_password_env reason=ssh_authentication_failed target=$candidate" >&2
    return 2
  done

  echo "PASSWORD_REQUIRED env=$ssh_password_env reason=install_ssh_key candidates=${candidates[*]}" >&2
  return 2
}

run_step choose_ssh_host

if [ "$sdk_inspect" -eq 1 ]; then
  inspect_env="$(mktemp /tmp/codex-source-inspect.XXXXXX)"
  cleanup_inspect_env() {
    rm -f "$inspect_env"
  }
  trap 'cleanup; cleanup_inspect_env' EXIT
  inspect_args=(--ssh-host "$SSH_HOST" --remote-root "$REMOTE_ROOT")
  if [ -n "$local_platform_override" ]; then
    inspect_args+=(--platform "$local_platform_override")
  fi
  if [ -n "$sdk_name_override" ]; then
    inspect_args+=(--sdk-name "$sdk_name_override")
  fi
  if [ "$accept_platform_conflict" -eq 1 ]; then
    inspect_args+=(--accept-platform-conflict)
  fi
  if [ "$accept_sdk_name_conflict" -eq 1 ]; then
    inspect_args+=(--accept-sdk-name-conflict)
  fi
  echo "SDK_INSPECT_START ssh=$SSH_HOST remote=$REMOTE_ROOT"
  if "$script_dir/inspect-android-sdk.sh" "${inspect_args[@]}" >"$inspect_env"; then
    old_platform="${PLATFORM:-}"
    old_sdk_name="${SDK_NAME:-}"
    # shellcheck disable=SC1090
    source "$inspect_env"
    LOCAL_PLATFORM="${mount_root%/}/$PLATFORM"
    LOCAL_PROJECT="$LOCAL_PLATFORM/$SDK_NAME"
    if [ "$PLATFORM" != "$old_platform" ] || [ "$SDK_NAME" != "$old_sdk_name" ]; then
      project_level_mount=1
    fi
    echo "SDK_INSPECT_OK platform=$PLATFORM sdk=$SDK_NAME target_board_platform=${TARGET_BOARD_PLATFORM:-} scores=rk:${PLATFORM_SCORE_RK:-0},unisoc:${PLATFORM_SCORE_UNISOC:-0},mtk:${PLATFORM_SCORE_MTK:-0}"
  else
    status=$?
    case "$status" in
      5)
        echo "FAILED_HINT: 无法从远端源码树识别平台；请明确说挂到 rk/unisoc/mtk 哪个平台。" >&2
        ;;
      6)
        echo "FAILED_HINT: 无法从远端源码树识别项目名；请明确提供项目名，例如 --sdk-name TVA10A2R。" >&2
        ;;
      7)
        echo "FAILED_HINT: 用户指定的平台和源码识别的平台冲突；请确认最终使用哪个平台。确认后可带 --accept-platform-conflict 继续。" >&2
        ;;
      8)
        echo "FAILED_HINT: 用户指定的项目名和源码识别的项目名冲突；请确认最终使用哪个项目名。确认后可带 --accept-sdk-name-conflict 继续。" >&2
        ;;
      *)
        echo "FAILED_HINT: 无法完成远端源码树检查；请查看上方错误并补充缺失信息。" >&2
        ;;
    esac
    exit "$status"
  fi
fi

if [ -z "${PLATFORM:-}" ]; then
  echo "PLATFORM_REQUIRED reason=no_source_or_user_platform" >&2
  echo "FAILED_HINT: 没有平台结论，且未进行源码识别；请明确说挂到 rk/unisoc/mtk 哪个平台。" >&2
  exit 2
fi
if [ -z "${SDK_NAME:-}" ]; then
  echo "SDK_NAME_REQUIRED reason=no_source_or_user_project" >&2
  echo "FAILED_HINT: 没有项目名结论，且未进行源码识别；请明确提供项目名，例如 --sdk-name TVA10A2R。" >&2
  exit 2
fi
LOCAL_PLATFORM="${mount_root%/}/$PLATFORM"
LOCAL_PROJECT="$LOCAL_PLATFORM/$SDK_NAME"

passwords_file="$(passwords_file_for_ssh_target "$SSH_HOST")" || {
  echo "PASSWORD_REQUIRED reason=ssh_account_unresolved target=$SSH_HOST" >&2
  exit 2
}
samba_passwords_file="$(passwords_file_for_account "$SAMBA_USER" "${server_name:-${SSH_HOST#*@}}")"
local_sudo_file="$(local_sudo_password_file)"
load_saved_passwords "$passwords_file"
ssh_password="$(value_from_env_or_saved_or_default "$ssh_password_env" "$SAVED_SSH_PASSWORD" "$provided_default_password")"
remote_sudo_password="$(value_from_env_or_saved_or_default "$remote_sudo_password_env" "$SAVED_REMOTE_SUDO_PASSWORD" "$provided_default_password")"
load_saved_passwords "$samba_passwords_file"
samba_password="$(value_from_env_or_saved_or_default "$samba_password_env" "$SAVED_SAMBA_PASSWORD" "$provided_default_password")"
load_saved_local_sudo_password "$local_sudo_file"
local_sudo_password="$(value_from_env_or_saved_or_default "$local_sudo_password_env" "$SAVED_LOCAL_SUDO_PASSWORD" "")"

export CODEX_REMOTE_SUDO_PASSWORD="$remote_sudo_password"
export CODEX_LOCAL_SUDO_PASSWORD="$local_sudo_password"
remote_sudo_password_env=CODEX_REMOTE_SUDO_PASSWORD
local_sudo_password_env=CODEX_LOCAL_SUDO_PASSWORD

discover_args=(--ssh-host "$SSH_HOST" --remote-root "$REMOTE_ROOT" --smb-conf "$smb_conf")
if [ -n "$server_name" ]; then
  discover_args+=(--server-name "$server_name")
fi

if ! "$script_dir/discover-samba-share.sh" "${discover_args[@]}" >"$cifs_env"; then
  if [ "$auto_samba_config" -ne 1 ]; then
    echo "SAMBA_SHARE_MISSING remote=$REMOTE_ROOT auto_config=false" >&2
    echo "FAILED_HINT: 远端路径没有匹配的 Samba share，且当前禁止自动配置 Samba，无法挂载到 WSL。" >&2
    exit 1
  fi
  echo "SAMBA_SHARE_MISSING remote=$REMOTE_ROOT action=auto_configure"
  ensure_args=(--ssh-host "$SSH_HOST" --remote-root "$REMOTE_ROOT" --smb-conf "$smb_conf" --apply)
  if [ "$project_level_mount" -eq 1 ]; then
    ensure_args+=(--share-name "$SDK_NAME" --share-path "$REMOTE_ROOT")
  else
    remote_parent="${REMOTE_ROOT%/}"
    remote_parent="${remote_parent%/*}"
    parent_share_name="${remote_parent##*/}"
    ensure_args+=(--share-name "$parent_share_name" --share-path "$remote_parent")
  fi
  if [ -n "$remote_sudo_password" ]; then
    ensure_args+=(--sudo-password-env "$remote_sudo_password_env")
  fi
  run_step_capture "$ensure_output" "$script_dir/ensure-samba-share.sh" "${ensure_args[@]}"
  if grep -q '^REMOTE_SUDO_AUTH mode=password$' "$ensure_output"; then
    remote_sudo_password_verified=1
  fi
  run_step "$script_dir/discover-samba-share.sh" "${discover_args[@]}" >"$cifs_env"
fi

# shellcheck disable=SC1090
source "$cifs_env"

mount_args=(
  --platform "$PLATFORM"
  --user "$SAMBA_USER"
  --sudo-password-env "$local_sudo_password_env"
)
if [ "$project_level_mount" -eq 1 ]; then
  if mountpoint -q "$LOCAL_PLATFORM"; then
    existing_parent_source="$(findmnt -n -o SOURCE --mountpoint "$LOCAL_PLATFORM" 2>/dev/null || true)"
    echo "PLATFORM_PARENT_MOUNTED path=$LOCAL_PLATFORM source=$existing_parent_source" >&2
    echo "FAILED_HINT: 项目级挂载要求本地平台目录只是普通目录，不能已经挂载父级 share。请先显式卸载 $LOCAL_PLATFORM，再重试。" >&2
    exit 1
  fi
  mount_args+=(--share "$SAMBA_PROJECT_URL" --target "$LOCAL_PROJECT")
else
  mount_args+=(--share "$SAMBA_SHARE_URL" --mount-root "$mount_root")
fi

run_step_capture "$mount_output" env SAMBA_PASSWORD="$samba_password" CODEX_LOCAL_SUDO_PASSWORD="$local_sudo_password" \
  "$script_dir/mount-platform.sh" "${mount_args[@]}"
if grep -q '^SAMBA_AUTH mode=password$' "$mount_output"; then
  samba_password_verified=1
fi
if grep -q '^LOCAL_SUDO_AUTH mode=password$' "$mount_output"; then
  local_sudo_password_verified=1
fi

# Do not inspect the mounted source tree. Platform/project facts came from the
# remote-channel inspection; mount-platform already verified the CIFS mount.

if [ "$save_credentials" -eq 1 ] && [ "$samba_password_verified" -eq 1 ] && [ -n "$samba_password" ]; then
  echo "NOTICE: storing Samba credentials for reboot recovery under $HOME/.servers/credentials/" >&2
  SAMBA_PASSWORD="$samba_password" "$script_dir/restore-project-mount.sh" \
    --project "$LOCAL_PROJECT" \
    --ssh-host "$SSH_HOST" \
    --remote-root "$REMOTE_ROOT" \
    --platform "$PLATFORM" \
    --sdk-name "$SDK_NAME" \
    --remember-current \
    --remember-password
elif [ "$save_credentials" -eq 1 ]; then
  echo "CREDENTIALS_NOT_SAVED reason=samba_password_not_verified env=$samba_password_env"
  "$script_dir/restore-project-mount.sh" \
    --project "$LOCAL_PROJECT" \
    --ssh-host "$SSH_HOST" \
    --remote-root "$REMOTE_ROOT" \
    --platform "$PLATFORM" \
    --sdk-name "$SDK_NAME" \
    --remember-current
else
  "$script_dir/restore-project-mount.sh" \
    --project "$LOCAL_PROJECT" \
    --ssh-host "$SSH_HOST" \
    --remote-root "$REMOTE_ROOT" \
    --platform "$PLATFORM" \
    --sdk-name "$SDK_NAME" \
    --remember-current
fi

save_all_passwords "$passwords_file"
save_local_sudo_password

if [ "$project_level_mount" -eq 1 ]; then
  final_share="$SAMBA_PROJECT_URL"
  echo "MOUNT_FROM_REMOTE_PATH_OK local=$LOCAL_PROJECT remote=$REMOTE_ROOT share=$final_share"
else
  final_share="$SAMBA_SHARE_URL"
  echo "MOUNT_FROM_REMOTE_PATH_OK local=$LOCAL_PROJECT remote=$REMOTE_ROOT share=$final_share"
fi

recognition="$PLATFORM/$SDK_NAME"
if [ -n "${PROJECT_BRANCH:-}" ]; then
  recognition="$recognition，来源=PROJECT_BRANCH:$PROJECT_BRANCH"
elif [ -n "${SOURCE_SDK_SOURCE:-}" ]; then
  recognition="$recognition，来源=$SOURCE_SDK_SOURCE"
elif [ -n "$local_platform_override" ] || [ -n "$sdk_name_override" ]; then
  recognition="$recognition，来源=用户确认"
else
  recognition="$recognition，来源=source inspection"
fi

if [ "$save_credentials" -eq 1 ] && [ "$samba_password_verified" -eq 1 ] && [ -n "$samba_password" ]; then
  recovery_info="registry/credentials 已记录，可用于 reboot/Codex-restart restore"
elif [ "$save_credentials" -eq 1 ]; then
  recovery_info="registry 已记录，本次未保存 credentials（Samba password 未在本次验证）"
else
  recovery_info="registry 已记录，本次未保存 credentials（--no-save-credentials）"
fi

echo "挂载结果: 成功"
echo "本地路径: $LOCAL_PROJECT"
echo "远端路径: $REMOTE_ROOT"
echo "Samba 映射: $final_share"
echo "项目识别: $recognition"
echo "恢复信息: $recovery_info"
echo "交接: 可交给 android-remote-build-deploy"
