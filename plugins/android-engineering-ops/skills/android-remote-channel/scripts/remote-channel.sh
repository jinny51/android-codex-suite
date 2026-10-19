#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
remote-channel.sh --ssh-host HOST --remote-root PATH ACTION [options]

Control a persistent remote tmux session for one Android source tree.

Required:
  --ssh-host HOST       Remote SSH target.
  --remote-root PATH    Remote Android source root.

Actions:
  check                 Check SSH, tmux, and remote root readiness.
  install-tmux          Install tmux on the remote host when missing.
  ensure                Create or reuse the remote session.
  run -- COMMAND        Send COMMAND to the session.
  status                Print session and current command status.
  tail                  Tail the latest or selected command log.
  stop                  Kill the remote session.

Run options:
  --lock none|exclusive Default: none. Use exclusive for edits, git writes, and builds.
  --no-wait             Return after dispatching the command.
  --command-id ID       Optional stable command id.
  --wait-timeout SEC    Maximum wait time. Default: 86400 (24 hours).

Tail options:
  --command-id ID       Tail a specific command log.
  --lines N             Default: 120.

Install options:
  --sudo-password-env NAME
                         Env var containing remote sudo password.
                         Default: CODEX_REMOTE_SUDO_PASSWORD.
USAGE
}

die() {
  echo "ERROR: $*" >&2
  exit 1
}

single_quote() {
  printf "'%s'" "$(printf "%s" "$1" | sed "s/'/'\\\\''/g")"
}

contains_control_chars() {
  local cleaned
  cleaned="$(LC_ALL=C printf '%s' "$1" | LC_ALL=C tr -d '[:cntrl:]')"
  [[ "$cleaned" != "$1" ]]
}

sha256_stream() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then shasum -a 256 | awk '{print $1}'
  else openssl dgst -sha256 | awk '{print $NF}'
  fi
}

prepare_ssh_opts() {
  SSH_OPTS=(
    -o BatchMode=no
    -o ConnectTimeout="${CODEX_REMOTE_CHANNEL_CONNECT_TIMEOUT:-8}"
    -o ServerAliveInterval="${CODEX_REMOTE_CHANNEL_SERVER_ALIVE_INTERVAL:-30}"
    -o ServerAliveCountMax="${CODEX_REMOTE_CHANNEL_SERVER_ALIVE_COUNT_MAX:-3}"
  )
  if [[ "${CODEX_REMOTE_CHANNEL_SSH_MUX:-1}" != "0" ]]; then
    local control_dir="${CODEX_REMOTE_CHANNEL_CONTROL_DIR:-$HOME/.ssh/controlmasters}"
    mkdir -p "$control_dir" 2>/dev/null || true
    chmod 700 "$control_dir" 2>/dev/null || true
    SSH_OPTS+=(
      -o ControlMaster=auto
      -o ControlPersist="${CODEX_REMOTE_CHANNEL_CONTROL_PERSIST:-2h}"
      -o ControlPath="${CODEX_REMOTE_CHANNEL_CONTROL_PATH:-$control_dir/codex-%C}"
    )
  fi
}

ssh_run() {
  local cmd="$1"
  ssh "${SSH_OPTS[@]}" "$SSH_HOST" "$cmd"
}

ssh_run_capture() {
  local cmd="$1"
  ssh "${SSH_OPTS[@]}" "$SSH_HOST" "$cmd" 2>&1
}

ssh_run_capture_with_stdin() {
  local cmd="$1"
  local input="$2"
  printf "%s\n" "$input" | ssh "${SSH_OPTS[@]}" "$SSH_HOST" "$cmd" 2>&1
}

ssh_run_with_stdin() {
  local cmd="$1"
  ssh "${SSH_OPTS[@]}" "$SSH_HOST" "$cmd"
}

SSH_HOST=""
REMOTE_ROOT=""
ACTION=""
LOCK_MODE="none"
WAIT=true
COMMAND_ID=""
TAIL_LINES="120"
SUDO_PASSWORD_ENV="CODEX_REMOTE_SUDO_PASSWORD"
WAIT_TIMEOUT="${CODEX_REMOTE_CHANNEL_WAIT_TIMEOUT:-86400}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --ssh-host) SSH_HOST="${2:-}"; shift 2 ;;
    --remote-root) REMOTE_ROOT="${2:-}"; shift 2 ;;
    --lock) LOCK_MODE="${2:-}"; shift 2 ;;
    --no-wait) WAIT=false; shift ;;
    --command-id) COMMAND_ID="${2:-}"; shift 2 ;;
    --lines) TAIL_LINES="${2:-}"; shift 2 ;;
    --wait-timeout) WAIT_TIMEOUT="${2:-}"; shift 2 ;;
    --sudo-password-env) SUDO_PASSWORD_ENV="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    check|install-tmux|ensure|run|status|tail|stop) ACTION="$1"; shift; break ;;
    *) die "Unknown argument: $1" ;;
  esac
done

[[ -n "$SSH_HOST" ]] || die "--ssh-host is required"
[[ -n "$REMOTE_ROOT" ]] || die "--remote-root is required"
[[ -n "$ACTION" ]] || die "action is required"
[[ "$REMOTE_ROOT" == /* ]] || die "--remote-root must be absolute"
[[ "$LOCK_MODE" == "none" || "$LOCK_MODE" == "exclusive" ]] || die "--lock must be none or exclusive"
[[ "$TAIL_LINES" =~ ^[0-9]+$ ]] || die "--lines must be a positive integer"
[[ "$WAIT_TIMEOUT" =~ ^[1-9][0-9]*$ ]] || die "--wait-timeout must be a positive integer"
! contains_control_chars "$SSH_HOST" || die "--ssh-host must not contain control characters"
! contains_control_chars "$REMOTE_ROOT" || die "--remote-root must not contain control characters"
[[ "$SSH_HOST" != -* && "$SSH_HOST" != *[[:space:]]* ]] || die "--ssh-host must be a host token, not an option or whitespace list"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
python3 "$PLUGIN_ROOT/lib/android_engineering_ops/install_family.py"

prepare_ssh_opts

WORKSPACE_HASH=""
SERVER_ID_SHA256=""
REMOTE_UID=""
CANONICAL_ROOT=""
SESSION_NAME=""
STATE_DIR=""
PROJECT_LOCK_REL=""
RUNNER_SHA256=""

resolve_workspace() {
  local remote_root_q output line key value
  remote_root_q="$(single_quote "$REMOTE_ROOT")"
  output="$(ssh_run "
set -eu
requested_root=$remote_root_q
if command -v realpath >/dev/null 2>&1; then
  canonical_root=\$(realpath -e -- \"\$requested_root\" 2>/dev/null || true)
else
  canonical_root=\$(readlink -f -- \"\$requested_root\" 2>/dev/null || true)
fi
[ -n \"\$canonical_root\" ] && [ -d \"\$canonical_root\" ] || {
  printf 'REMOTE_ROOT_MISSING %s\n' \"\$requested_root\" >&2
  exit 2
}
if LC_ALL=C printf '%s' \"\$canonical_root\" | LC_ALL=C grep -q '[[:cntrl:]]'; then
  echo 'REMOTE_ROOT_UNSAFE control-character' >&2
  exit 2
fi
server_id=\${CODEX_REMOTE_CHANNEL_SERVER_ID:-}
if [ -z \"\$server_id\" ] && [ -r /etc/machine-id ]; then
  server_id=\$(tr -d '[:space:]' </etc/machine-id)
fi
if [ -z \"\$server_id\" ] && [ -r /var/lib/dbus/machine-id ]; then
  server_id=\$(tr -d '[:space:]' </var/lib/dbus/machine-id)
fi
if [ -z \"\$server_id\" ] && [ -r /sys/class/dmi/id/product_uuid ]; then
  server_id=\$(tr -d '[:space:]' </sys/class/dmi/id/product_uuid)
fi
[ -n \"\$server_id\" ] || { echo 'SERVER_ID_MISSING stable machine identity unavailable' >&2; exit 126; }
digest() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum | awk '{print \$1}'
  elif command -v shasum >/dev/null 2>&1; then shasum -a 256 | awk '{print \$1}'
  elif command -v openssl >/dev/null 2>&1; then openssl dgst -sha256 | awk '{print \$NF}'
  else echo 'HASH_TOOL_MISSING sha256sum/shasum/openssl' >&2; exit 127
  fi
}
remote_uid=\$(id -u)
server_hash=\$(printf '%s' \"\$server_id\" | digest)
workspace_hash=\$(printf '%s\\n%s\\n%s' \"\$server_id\" \"\$remote_uid\" \"\$canonical_root\" | digest)
printf 'WORKSPACE_HASH=%s\\n' \"\$(printf '%s' \"\$workspace_hash\" | cut -c1-16)\"
printf 'SERVER_ID_SHA256=%s\\n' \"\$server_hash\"
printf 'REMOTE_UID=%s\\n' \"\$remote_uid\"
printf 'CANONICAL_ROOT=%s\\n' \"\$canonical_root\"
")"
  while IFS= read -r line; do
    key="${line%%=*}"
    value="${line#*=}"
    case "$key" in
      WORKSPACE_HASH) WORKSPACE_HASH="$value" ;;
      SERVER_ID_SHA256) SERVER_ID_SHA256="$value" ;;
      REMOTE_UID) REMOTE_UID="$value" ;;
      CANONICAL_ROOT) CANONICAL_ROOT="$value" ;;
    esac
  done <<<"$output"
  [[ "$WORKSPACE_HASH" =~ ^[0-9a-f]{16}$ ]] || die "invalid canonical workspace hash"
  [[ "$SERVER_ID_SHA256" =~ ^[0-9a-f]{64}$ ]] || die "invalid server identity hash"
  [[ "$REMOTE_UID" =~ ^[0-9]+$ ]] || die "invalid remote uid"
  [[ -n "$CANONICAL_ROOT" && "$CANONICAL_ROOT" == /* ]] || die "invalid canonical remote root"
  ! contains_control_chars "$CANONICAL_ROOT" || die "invalid canonical remote root control character"
  SESSION_NAME="codex-android-$WORKSPACE_HASH"
  STATE_DIR=".codex/android-remote-sessions/$WORKSPACE_HASH"
  PROJECT_LOCK_REL=".codex/android-remote-locks/$WORKSPACE_HASH.lock"
}

resolve_workspace

remote_state_path() {
  printf "\$HOME/%s" "$STATE_DIR"
}

tmux_install_body() {
  cat <<'REMOTE'
set -e
if command -v apt-get >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y tmux
elif command -v dnf >/dev/null 2>&1; then
  dnf install -y tmux
elif command -v yum >/dev/null 2>&1; then
  yum install -y tmux
elif command -v zypper >/dev/null 2>&1; then
  zypper --non-interactive install tmux
else
  echo 'PACKAGE_MANAGER_UNSUPPORTED install tmux manually' >&2
  exit 11
fi
REMOTE
}

remote_tmux_install_passwordless_command() {
  local install_body_q
  install_body_q="$(single_quote "$(tmux_install_body)")"
  cat <<REMOTE
set -e
if command -v tmux >/dev/null 2>&1; then
  printf 'TMUX_OK path=%s\n' "\$(command -v tmux)"
  tmux -V
  exit 0
fi
command -v sudo >/dev/null 2>&1 || { echo 'SUDO_MISSING install tmux manually' >&2; exit 13; }
if ! sudo -n true >/dev/null 2>&1; then
  echo 'REMOTE_SUDO_PASSWORD_REQUIRED env=$SUDO_PASSWORD_ENV action=install_tmux' >&2
  exit 10
fi
sudo -n sh -c $install_body_q
printf 'TMUX_INSTALLED version=%s path=%s\n' "\$(tmux -V)" "\$(command -v tmux)"
REMOTE
}

remote_tmux_install_password_command() {
  local install_body_q
  install_body_q="$(single_quote "$(tmux_install_body)")"
  cat <<REMOTE
set -e
if command -v tmux >/dev/null 2>&1; then
  printf 'TMUX_OK path=%s\n' "\$(command -v tmux)"
  tmux -V
  exit 0
fi
command -v sudo >/dev/null 2>&1 || { echo 'SUDO_MISSING install tmux manually' >&2; exit 13; }
if ! sudo -S -p '' -v >/dev/null 2>&1; then
  echo 'REMOTE_SUDO_AUTH_FAILED' >&2
  exit 12
fi
sudo -n sh -c $install_body_q
printf 'TMUX_INSTALLED version=%s path=%s\n' "\$(tmux -V)" "\$(command -v tmux)"
REMOTE
}

add_unique_password_file() {
  local file="$1"
  [[ -f "$file" ]] || return 0
  local existing
  for existing in "${PASSWORD_FILES[@]}"; do
    [[ "$existing" == "$file" ]] && return 0
  done
  PASSWORD_FILES+=("$file")
}

add_unique_cred_file() {
  local file="$1"
  [[ -f "$file" ]] || return 0
  local existing
  for existing in "${CRED_FILES[@]}"; do
    [[ "$existing" == "$file" ]] && return 0
  done
  CRED_FILES+=("$file")
}

account_hash() {
  if command -v sha256sum >/dev/null 2>&1; then
    printf "%s@%s" "$1" "$2" | sha256sum | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    printf "%s@%s" "$1" "$2" | shasum -a 256 | awk '{print $1}'
  elif command -v openssl >/dev/null 2>&1; then
    printf "%s@%s" "$1" "$2" | openssl dgst -sha256 | awk '{print $NF}'
  else
    die "sha256sum, shasum, or openssl is required"
  fi
}

server_for_share() {
  local share="$1"
  share="${share#//}"
  printf "%s" "${share%%/*}"
}

add_account_credential_candidates() {
  local user="$1"
  local server="$2"
  [[ -n "$user" && -n "$server" ]] || return 0
  local key credentials_dir
  credentials_dir="${ANDROID_WSL_SOURCE_ACCESS_CREDENTIALS_DIR:-$HOME/.servers/credentials}"
  key="$(account_hash "$user" "$server")"
  add_unique_password_file "$credentials_dir/$key.passwords.env"
  add_unique_cred_file "$credentials_dir/$key.cred"
}

ssh_host_matches() {
  local candidate="$1"
  [[ -z "$candidate" || "$candidate" == "$SSH_HOST" || "${candidate#*@}" == "${SSH_HOST#*@}" ]]
}

collect_wsl_source_access_credentials() {
  local registry_dir remote_root_norm registry
  registry_dir="${ANDROID_WSL_SOURCE_ACCESS_PROJECTS_DIR:-$HOME/.servers/projects}"
  remote_root_norm="${REMOTE_ROOT%/}"
  [[ -d "$registry_dir" ]] || return 0
  for registry in "$registry_dir"/*.env; do
    [[ -f "$registry" ]] || continue
    while IFS=$'\t' read -r kind value extra; do
      case "$kind" in
        account) add_account_credential_candidates "$value" "$extra" ;;
        cred) add_unique_cred_file "$value" ;;
      esac
    done < <(
      SSH_HOST_VALUE="$SSH_HOST" REMOTE_ROOT_VALUE="$remote_root_norm" REGISTRY_FILE="$registry" bash -c '
set +u
SAMBA_USER=""
SAMBA_CREDENTIALS_FILE=""
REMOTE_SSH_HOSTS=()
REMOTE_ROOTS=()
SAMBA_PROJECT_SHARES=()
source "$REGISTRY_FILE" 2>/dev/null || exit 0
for i in "${!REMOTE_ROOTS[@]}"; do
  root="${REMOTE_ROOTS[$i]%/}"
  host="${REMOTE_SSH_HOSTS[$i]-}"
  if [ "$root" = "$REMOTE_ROOT_VALUE" ] && { [ -z "$host" ] || [ "$host" = "$SSH_HOST_VALUE" ] || [ "${host#*@}" = "${SSH_HOST_VALUE#*@}" ]; }; then
    share="${SAMBA_PROJECT_SHARES[$i]-}"
    if [ -n "$SAMBA_USER" ] && [ -n "$share" ]; then
      share_body="${share#//}"
      server="${share_body%%/*}"
      printf "account\t%s\t%s\n" "$SAMBA_USER" "$server"
    fi
    if [ -n "$SAMBA_CREDENTIALS_FILE" ]; then
      printf "cred\t%s\t\n" "$SAMBA_CREDENTIALS_FILE"
    fi
  fi
done
'
    )
  done
}

collect_ssh_config_credentials() {
  local ssh_user ssh_hostname host_part user_part
  host_part="${SSH_HOST#*@}"
  user_part=""
  if [[ "$SSH_HOST" == *@* ]]; then
    user_part="${SSH_HOST%@*}"
  fi
  while read -r key value _; do
    case "$key" in
      user) ssh_user="$value" ;;
      hostname) ssh_hostname="$value" ;;
    esac
  done < <(ssh -G "$SSH_HOST" 2>/dev/null || true)
  [[ -n "${user_part:-}" ]] && add_account_credential_candidates "$user_part" "$host_part"
  [[ -n "${ssh_user:-}" && -n "${host_part:-}" ]] && add_account_credential_candidates "$ssh_user" "$host_part"
  [[ -n "${ssh_user:-}" && -n "${ssh_hostname:-}" ]] && add_account_credential_candidates "$ssh_user" "$ssh_hostname"
}

read_saved_password_var() {
  local file="$1"
  local var_name="$2"
  [[ -f "$file" ]] || return 0
  VAR_NAME="$var_name" bash -c '
set +u
SAVED_SSH_PASSWORD=""
SAVED_SAMBA_PASSWORD=""
SAVED_REMOTE_SUDO_PASSWORD=""
SAVED_LOCAL_SUDO_PASSWORD=""
source "$1" 2>/dev/null || exit 0
printf "%s" "${!VAR_NAME-}"
' bash "$file"
}

read_cred_password() {
  local file="$1"
  [[ -f "$file" ]] || return 0
  awk -F= '$1=="password"{sub(/^password=/,""); gsub(/\r$/,""); print; exit}' "$file"
}

try_tmux_password_candidate() {
  local source_label="$1"
  local password="$2"
  [[ -n "$password" ]] || return 1
  local output status
  set +e
  output="$(ssh_run_capture_with_stdin "$(remote_tmux_install_password_command)" "$password")"
  status=$?
  set -e
  if [[ "$status" -eq 0 ]]; then
    [[ -n "$output" ]] && printf "%s\n" "$output"
    echo "TMUX_INSTALL_AUTH source=$source_label"
    return 0
  fi
  if [[ "$status" -eq 12 ]]; then
    return 1
  fi
  [[ -n "$output" ]] && printf "%s\n" "$output" >&2
  exit "$status"
}

try_saved_source_access_passwords() {
  PASSWORD_FILES=()
  CRED_FILES=()
  collect_wsl_source_access_credentials
  collect_ssh_config_credentials

  local file var_name password
  for file in "${PASSWORD_FILES[@]}"; do
    for var_name in SAVED_REMOTE_SUDO_PASSWORD SAVED_SSH_PASSWORD SAVED_SAMBA_PASSWORD; do
      password="$(read_saved_password_var "$file" "$var_name")"
      try_tmux_password_candidate "source-access:$var_name" "$password" && return 0
    done
  done
  for file in "${CRED_FILES[@]}"; do
    password="$(read_cred_password "$file")"
    try_tmux_password_candidate "source-access:SAMBA_CREDENTIALS_FILE" "$password" && return 0
  done
  return 1
}

install_tmux() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --sudo-password-env) SUDO_PASSWORD_ENV="${2:-}"; shift 2 ;;
      --) shift; break ;;
      *) die "Unknown install-tmux argument: $1" ;;
    esac
  done
  [[ -n "$SUDO_PASSWORD_ENV" ]] || die "--sudo-password-env must not be empty"

  local output status env_password
  set +e
  output="$(ssh_run_capture "$(remote_tmux_install_passwordless_command)")"
  status=$?
  set -e
  if [[ "$status" -eq 0 ]]; then
    [[ -n "$output" ]] && printf "%s\n" "$output"
    echo "TMUX_INSTALL_AUTH source=passwordless-or-existing"
    return 0
  fi
  if [[ "$status" -ne 10 ]]; then
    [[ -n "$output" ]] && printf "%s\n" "$output" >&2
    exit "$status"
  fi

  env_password="${!SUDO_PASSWORD_ENV-}"
  try_tmux_password_candidate "env:$SUDO_PASSWORD_ENV" "$env_password" && return 0
  try_saved_source_access_passwords && return 0

  echo "REMOTE_SUDO_PASSWORD_REQUIRED env=$SUDO_PASSWORD_ENV action=install_tmux" >&2
  exit 10
}

check_channel() {
  local canonical_root_q ssh_host_q requested_root_q workspace_q server_hash_q remote_uid_q
  canonical_root_q="$(single_quote "$CANONICAL_ROOT")"
  ssh_host_q="$(single_quote "$SSH_HOST")"
  requested_root_q="$(single_quote "$REMOTE_ROOT")"
  workspace_q="$(single_quote "$WORKSPACE_HASH")"
  server_hash_q="$(single_quote "$SERVER_ID_SHA256")"
  remote_uid_q="$(single_quote "$REMOTE_UID")"
  ssh_run "
set -e
ssh_host=$ssh_host_q
requested_root=$requested_root_q
canonical_root=$canonical_root_q
workspace_id=$workspace_q
server_hash=$server_hash_q
remote_uid=$remote_uid_q
printf 'SSH_OK host=%s\n' \"\$ssh_host\"
command -v tmux >/dev/null 2>&1 || { echo 'TMUX_MISSING install tmux on remote host' >&2; exit 127; }
command -v flock >/dev/null 2>&1 || { echo 'FLOCK_MISSING install util-linux on remote host' >&2; exit 127; }
printf 'TMUX_OK path=%s\n' \"\$(command -v tmux)\"
printf 'FLOCK_OK path=%s\n' \"\$(command -v flock)\"
test -d \"\$canonical_root\" || {
  printf 'REMOTE_ROOT_MISSING %s\n' \"\$canonical_root\" >&2
  exit 2
}
printf 'REMOTE_ROOT_OK requested=%s canonical=%s\n' \"\$requested_root\" \"\$canonical_root\"
printf 'WORKSPACE_OK id=%s server_id_sha256=%s uid=%s\n' \
  \"\$workspace_id\" \"\$server_hash\" \"\$remote_uid\"
"
}

runner_script() {
  local root_q session_q
  root_q="$(single_quote "$CANONICAL_ROOT")"
  session_q="$(single_quote "$SESSION_NAME")"
  cat <<EOF_RUNNER
#!/usr/bin/env bash
set -uo pipefail
umask 077

PROTOCOL_VERSION=2
SESSION_NAME=$session_q
STATE_DIR="\$HOME/$STATE_DIR"
COMMANDS_DIR="\$STATE_DIR/commands"
CANONICAL_ROOT=$root_q
PROJECT_LOCK="\$HOME/$PROJECT_LOCK_REL"
CURRENT_ID=""

mkdir -p "\$COMMANDS_DIR" "\$(dirname "\$PROJECT_LOCK")"
chmod 700 "\$STATE_DIR" "\$COMMANDS_DIR" "\$(dirname "\$PROJECT_LOCK")" 2>/dev/null || true
: >"\$PROJECT_LOCK"
chmod 600 "\$PROJECT_LOCK"

write_once() {
  local path="\$1" content="\${2-}" tmp
  [ ! -e "\$path" ] || return 1
  tmp=\$(mktemp "\$STATE_DIR/.once.XXXXXX") || return 1
  printf '%s\n' "\$content" >"\$tmp"
  chmod 600 "\$tmp"
  if ln "\$tmp" "\$path" 2>/dev/null; then
    rm -f "\$tmp"
    return 0
  fi
  rm -f "\$tmp"
  return 1
}

append_event() {
  local id="\$1" state="\$2" detail="\${3-}"
  printf '%s\t%s\t%s\n' "\$(date +%s)" "\$state" "\$detail" >>"\$COMMANDS_DIR/\$id.events"
  chmod 600 "\$COMMANDS_DIR/\$id.events"
}

terminal_state() {
  local id="\$1" terminal="\$COMMANDS_DIR/\$id.terminal"
  [ -f "\$terminal" ] && { cat "\$terminal"; return 0; }
  return 1
}

mark_terminal() {
  local id="\$1" state="\$2" rc="\$3" base="\$COMMANDS_DIR/\$1" tmp existing
  case "\$state" in completed|failed|aborted|lost) ;; *) return 2;; esac
  exec 6>"\$base.transition.lock"
  flock 6
  if [ -f "\$base.terminal" ]; then
    if [ ! -f "\$base.exit" ]; then
      existing=\$(cat "\$base.terminal" 2>/dev/null || printf failed)
      case "\$existing" in completed) rc=0 ;; aborted) rc=130 ;; lost) rc=125 ;; *) rc=1 ;; esac
      tmp=\$(mktemp "\$STATE_DIR/.exit-repair.XXXXXX")
      printf '%s\n' "\$rc" >"\$tmp"; chmod 600 "\$tmp"; mv -f "\$tmp" "\$base.exit"
    fi
  else
    tmp=\$(mktemp "\$STATE_DIR/.exit.XXXXXX")
    printf '%s\n' "\$rc" >"\$tmp"; chmod 600 "\$tmp"; mv -f "\$tmp" "\$base.exit"
    rm -f "\$base.completed" "\$base.failed" "\$base.aborted" "\$base.lost"
    write_once "\$base.\$state" "at=\$(date +%s) rc=\$rc" || true
    if write_once "\$base.terminal" "\$state"; then
      append_event "\$id" "\$state" "rc=\$rc"
    fi
  fi
  flock -u 6
  exec 6>&-
  if [ -f "\$STATE_DIR/busy" ] && grep -q "^\$id " "\$STATE_DIR/busy" 2>/dev/null; then
    rm -f "\$STATE_DIR/busy"
  fi
}

on_shutdown() {
  trap - HUP INT TERM
  local requested=false state=lost rc=125 queued id
  [ -f "\$STATE_DIR/stop.requested" ] && requested=true
  if [ "\$requested" = true ]; then state=aborted; rc=130; fi
  if [ -n "\$CURRENT_ID" ]; then
    mark_terminal "\$CURRENT_ID" "\$state" "\$rc"
  fi
  if [ "\$requested" = true ]; then
    shopt -s nullglob
    for committed in "\$COMMANDS_DIR"/*.request-complete; do
      id=\${committed##*/}; id=\${id%.request-complete}
      terminal_state "\$id" >/dev/null 2>&1 || mark_terminal "\$id" aborted 130
    done
  fi
  if [ "\$(cat "\$STATE_DIR/runner.pid" 2>/dev/null || true)" = "\$\$" ]; then
    rm -f "\$STATE_DIR/runner.ready" "\$STATE_DIR/busy"
  fi
  exit 0
}

on_exit() {
  local runner_rc=\$?
  trap - EXIT HUP INT TERM
  if [ -n "\$CURRENT_ID" ] && ! terminal_state "\$CURRENT_ID" >/dev/null 2>&1; then
    if [ -f "\$COMMANDS_DIR/\$CURRENT_ID.abort-requested" ]; then
      mark_terminal "\$CURRENT_ID" aborted 130
    elif [ "\$runner_rc" -ne 0 ]; then
      mark_terminal "\$CURRENT_ID" failed "\$runner_rc"
    else
      # A command that exits the persistent shell with zero did not return
      # through the normal completion path, so completion is uncertain.
      mark_terminal "\$CURRENT_ID" lost 125
    fi
  fi
  rm -f "\$STATE_DIR/runner.ready" "\$STATE_DIR/busy"
}
trap on_shutdown HUP INT TERM
trap on_exit EXIT

# Only one fixed runner may consume this canonical workspace queue.
exec 8>"\$STATE_DIR/runner.lock"
runner_lock_attempt=0
while ! flock -n 8; do
  runner_lock_attempt=\$((runner_lock_attempt + 1))
  if [ "\$runner_lock_attempt" -ge 100 ]; then
    echo 'RUNNER_LOCK_TIMEOUT attempts=100' >&2
    exit 75
  fi
  sleep 0.1
done

# Anything left running before this lifetime acquired the runner lock is uncertain.
shopt -s nullglob
for running in "\$COMMANDS_DIR"/*.running; do
  id=\${running##*/}; id=\${id%.running}
  if ! terminal_state "\$id" >/dev/null 2>&1; then
    if [ -f "\$COMMANDS_DIR/\$id.abort-requested" ]; then
      mark_terminal "\$id" aborted 130
    else
      mark_terminal "\$id" lost 125
    fi
  fi
done
rm -f "\$STATE_DIR/busy" "\$STATE_DIR/stop.requested"
if command -v sha256sum >/dev/null 2>&1; then active_sha=\$(sha256sum "\$STATE_DIR/runner.sh" | awk '{print \$1}')
elif command -v shasum >/dev/null 2>&1; then active_sha=\$(shasum -a 256 "\$STATE_DIR/runner.sh" | awk '{print \$1}')
else active_sha=\$(openssl dgst -sha256 "\$STATE_DIR/runner.sh" | awk '{print \$NF}'); fi
expected_sha=\$(cat "\$STATE_DIR/runner.expected.sha256" 2>/dev/null || true)
[ -n "\$expected_sha" ] && [ "\$active_sha" = "\$expected_sha" ] || exit 126
printf '%s\n' "\$active_sha" >"\$STATE_DIR/runner.active.sha256"
chmod 600 "\$STATE_DIR/runner.active.sha256"
printf '%s\n' "\$PROTOCOL_VERSION" >"\$STATE_DIR/runner.protocol"
printf '%s\n' "\$\$" >"\$STATE_DIR/runner.pid"
: >"\$STATE_DIR/runner.ready"
chmod 600 "\$STATE_DIR/runner.protocol" "\$STATE_DIR/runner.pid" "\$STATE_DIR/runner.ready"

run_one() {
  local id="\$1" base="\$COMMANDS_DIR/\$1" lock_mode rc
  terminal_state "\$id" >/dev/null 2>&1 && return 0
  write_once "\$base.running" "at=\$(date +%s)" || return 0
  terminal_state "\$id" >/dev/null 2>&1 && return 0
  append_event "\$id" running
  lock_mode=\$(cat "\$base.lock-mode" 2>/dev/null || printf none)
  printf '%s remote=%s\n' "\$id" "\$CANONICAL_ROOT" >"\$STATE_DIR/busy"
  chmod 600 "\$STATE_DIR/busy"
  ln -sfn "\$base.log" "\$STATE_DIR/current.log"
  : >"\$base.log"
  chmod 600 "\$base.log"
  CURRENT_ID="\$id"

  # Source in the fixed runner itself so exported variables, functions, and the
  # Android lunch environment survive reconnects and later command IDs.
  rc=0
  if [ "\$lock_mode" = exclusive ]; then
    exec 9>"\$PROJECT_LOCK"
    flock 9
  fi
  set +e
  {
    cd "\$CANONICAL_ROOT" || rc=2
    if [ "\$rc" -eq 0 ]; then
      # shellcheck disable=SC1090
      # Do not put source in an OR-list: Bash would then ignore a command
      # body's explicit set -e. An errexit exits this runner and on_exit
      # records the original failure; successful commands keep shell state.
      source "\$base.line"
      rc=\$?
    fi
  } >"\$base.log" 2>&1
  if [ "\$lock_mode" = exclusive ]; then
    flock -u 9 2>/dev/null || true
    exec 9>&-
  fi
  # A sourced command may change runner shell policy. Keep environment and
  # functions, but restore the protocol's control options and signal handler.
  set +e
  set +x
  set -u -o pipefail
  umask 077
  trap on_shutdown HUP INT TERM
  trap on_exit EXIT
  if [ -f "\$base.abort-requested" ]; then
    mark_terminal "\$id" aborted 130
  elif [ "\$rc" -eq 0 ]; then
    mark_terminal "\$id" completed 0
  else
    mark_terminal "\$id" failed "\$rc"
  fi
  printf '__CODEX_CMD_DONE id=%s state=%s rc=%s\n' \
    "\$id" "\$(terminal_state "\$id" 2>/dev/null || printf lost)" \
    "\$(cat "\$base.exit" 2>/dev/null || printf 125)"
  CURRENT_ID=""
}

while :; do
  printf '%s\n' "\$(date +%s)" >"\$STATE_DIR/runner.heartbeat"
  chmod 600 "\$STATE_DIR/runner.heartbeat"
  selected_id=""
  selected_key=""
  for committed in "\$COMMANDS_DIR"/*.request-complete; do
    id=\${committed##*/}; id=\${id%.request-complete}
    terminal_state "\$id" >/dev/null 2>&1 && continue
    [ -f "\$COMMANDS_DIR/\$id.running" ] && continue
    sequence=\$(cat "\$COMMANDS_DIR/\$id.queue-seq" 2>/dev/null || printf 00000000000000000000)
    case "\$sequence" in *[!0-9]*|'') mark_terminal "\$id" lost 125; continue;; esac
    key="\$sequence:\$id"
    if [ -z "\$selected_key" ] || [[ "\$key" < "\$selected_key" ]]; then
      selected_key="\$key"
      selected_id="\$id"
    fi
  done
  if [ -n "\$selected_id" ]; then run_one "\$selected_id"; else sleep 1; fi
done
EOF_RUNNER
}

write_session_files() {
  local env_text runner_text runner_sha_q
  runner_text="$(runner_script)"
  RUNNER_SHA256="$(printf '%s\n' "$runner_text" | sha256_stream)"
  [[ "$RUNNER_SHA256" =~ ^[0-9a-f]{64}$ ]] || die "invalid local runner payload digest"
  runner_sha_q="$(single_quote "$RUNNER_SHA256")"
  env_text="$(printf '%s\n' \
    'PROTOCOL_VERSION=2' \
    "SESSION_NAME=$(single_quote "$SESSION_NAME")" \
    "WORKSPACE_HASH=$(single_quote "$WORKSPACE_HASH")" \
    "SERVER_ID_SHA256=$(single_quote "$SERVER_ID_SHA256")" \
    "REMOTE_UID=$(single_quote "$REMOTE_UID")" \
    "CANONICAL_ROOT=$(single_quote "$CANONICAL_ROOT")" \
    "RUNNER_SHA256=$(single_quote "$RUNNER_SHA256")" \
    "STATE_DIR=\"\${HOME}/$STATE_DIR\"" \
    "PROJECT_LOCK=\"\${HOME}/$PROJECT_LOCK_REL\"")"
  printf '%s\n' "$env_text" | ssh_run_with_stdin "
set -e
umask 077
state_dir="\$HOME/$STATE_DIR"
mkdir -p \"\$state_dir/commands\" \"\$HOME/.codex/android-remote-locks\"
chmod 700 \"\$state_dir\" \"\$state_dir/commands\" \"\$HOME/.codex/android-remote-locks\"
tmp=\$(mktemp \"\$state_dir/.session.env.XXXXXX\")
cat >\"\$tmp\"
chmod 600 \"\$tmp\"
mv -f \"\$tmp\" \"\$state_dir/session.env\"
touch \"\$HOME/$PROJECT_LOCK_REL\"
chmod 600 \"\$HOME/$PROJECT_LOCK_REL\"
"
  printf '%s\n' "$runner_text" | ssh_run_with_stdin "
set -e
umask 077
state_dir="\$HOME/$STATE_DIR"
tmp=\$(mktemp \"\$state_dir/.runner.sh.XXXXXX\")
cat >\"\$tmp\"
chmod 700 \"\$tmp\"
if command -v sha256sum >/dev/null 2>&1; then actual=\$(sha256sum \"\$tmp\" | awk '{print \$1}')
elif command -v shasum >/dev/null 2>&1; then actual=\$(shasum -a 256 \"\$tmp\" | awk '{print \$1}')
else actual=\$(openssl dgst -sha256 \"\$tmp\" | awk '{print \$NF}'); fi
expected=$runner_sha_q
[ \"\$actual\" = \"\$expected\" ] || { echo 'RUNNER_PAYLOAD_TRANSFER_MISMATCH' >&2; exit 5; }
mv -f \"\$tmp\" \"\$state_dir/runner.sh\"
digest_tmp=\$(mktemp \"\$state_dir/.runner.expected.XXXXXX\")
printf '%s\n' \"\$expected\" >\"\$digest_tmp\"; chmod 600 \"\$digest_tmp\"
mv -f \"\$digest_tmp\" \"\$state_dir/runner.expected.sha256\"
"
}

reconcile_session() {
  local session_q target_q
  session_q="$(single_quote "$SESSION_NAME")"
  target_q="$(single_quote "$SESSION_NAME:runner.0")"
  ssh_run "
set -e
umask 077
state_dir="\$HOME/$STATE_DIR"
commands=\"\$state_dir/commands\"
[ -d \"\$commands\" ] || exit 0
session_alive=false
if tmux has-session -t $session_q 2>/dev/null && [ -f \"\$state_dir/runner.ready\" ]; then
  runner_pid=\$(cat \"\$state_dir/runner.pid\" 2>/dev/null || true)
  expected_sha=\$(cat \"\$state_dir/runner.expected.sha256\" 2>/dev/null || true)
  active_sha=\$(cat \"\$state_dir/runner.active.sha256\" 2>/dev/null || true)
  pane_info=\$(tmux list-panes -t $target_q -F '#{pane_dead} #{pane_pid}' 2>/dev/null || true)
  pane_dead=\${pane_info%% *}; pane_pid=\${pane_info#* }
  [ -n \"\$runner_pid\" ] && kill -0 \"\$runner_pid\" 2>/dev/null \
    && [ -n \"\$expected_sha\" ] && [ \"\$active_sha\" = \"\$expected_sha\" ] \
    && [ \"\$pane_dead\" = 0 ] && [ \"\$pane_pid\" = \"\$runner_pid\" ] \
    && session_alive=true
fi
write_once() {
  path=\"\$1\"; content=\"\${2-}\"
  [ ! -e \"\$path\" ] || return 1
  tmp=\$(mktemp \"\$state_dir/.reconcile.XXXXXX\")
  printf '%s\\n' \"\$content\" >\"\$tmp\"; chmod 600 \"\$tmp\"
  ln \"\$tmp\" \"\$path\" 2>/dev/null && { rm -f \"\$tmp\"; return 0; }
  rm -f \"\$tmp\"; return 1
}
commit_terminal() {
  id=\"\$1\"; state=\"\$2\"; rc=\"\$3\"; base=\"\$commands/\$id\"
  exec 6>\"\$base.transition.lock\"; flock 6
  if [ -f \"\$base.terminal\" ]; then
    if [ ! -f \"\$base.exit\" ]; then
      existing=\$(cat \"\$base.terminal\" 2>/dev/null || printf failed)
      case \"\$existing\" in completed) rc=0 ;; aborted) rc=130 ;; lost) rc=125 ;; *) rc=1 ;; esac
      tmp=\$(mktemp \"\$state_dir/.exit-repair.XXXXXX\")
      printf '%s\n' \"\$rc\" >\"\$tmp\"; chmod 600 \"\$tmp\"; mv -f \"\$tmp\" \"\$base.exit\"
    fi
  else
    tmp=\$(mktemp \"\$state_dir/.exit.XXXXXX\")
    printf '%s\n' \"\$rc\" >\"\$tmp\"; chmod 600 \"\$tmp\"; mv -f \"\$tmp\" \"\$base.exit\"
    rm -f \"\$base.completed\" \"\$base.failed\" \"\$base.aborted\" \"\$base.lost\"
    write_once \"\$base.\$state\" \"at=\$(date +%s) rc=\$rc\" || true
    if write_once \"\$base.terminal\" \"\$state\"; then
      printf '%s\t%s\trc=%s\n' \"\$(date +%s)\" \"\$state\" \"\$rc\" >>\"\$base.events\"
      chmod 600 \"\$base.events\"
    fi
  fi
  flock -u 6; exec 6>&-
}
for running in \"\$commands\"/*.running; do
  [ -e \"\$running\" ] || continue
  id=\${running##*/}; id=\${id%.running}; base=\"\$commands/\$id\"
  if [ -f \"\$base.terminal\" ]; then
    [ -f \"\$base.exit\" ] || commit_terminal \"\$id\" failed 1
    continue
  fi
  [ \"\$session_alive\" = false ] || continue
  if [ -f \"\$base.abort-requested\" ]; then state=aborted; rc=130; else state=lost; rc=125; fi
  commit_terminal \"\$id\" \"\$state\" \"\$rc\"
done
if [ -f \"\$state_dir/busy\" ] && [ \"\$session_alive\" = false ]; then rm -f \"\$state_dir/busy\"; fi
"
}

guard_runner_upgrade() {
  local session_q target_q desired_q
  RUNNER_SHA256="$(runner_script | sha256_stream)"
  [[ "$RUNNER_SHA256" =~ ^[0-9a-f]{64}$ ]] || die "invalid local runner payload digest"
  session_q="$(single_quote "$SESSION_NAME")"
  target_q="$(single_quote "$SESSION_NAME:runner.0")"
  desired_q="$(single_quote "$RUNNER_SHA256")"
  ssh_run "
set -e
state_dir=\"\$HOME/$STATE_DIR\"; commands=\"\$state_dir/commands\"; desired=$desired_q
tmux has-session -t $session_q 2>/dev/null || exit 0
runner_pid=\$(cat \"\$state_dir/runner.pid\" 2>/dev/null || true)
active_sha=\$(cat \"\$state_dir/runner.active.sha256\" 2>/dev/null || true)
pane_info=\$(tmux list-panes -t $target_q -F '#{pane_dead} #{pane_pid}' 2>/dev/null || true)
pane_dead=\${pane_info%% *}; pane_pid=\${pane_info#* }
runner_live=false
[ -f \"\$state_dir/runner.ready\" ] && [ -n \"\$runner_pid\" ] \
  && kill -0 \"\$runner_pid\" 2>/dev/null \
  && [ \"\$pane_dead\" = 0 ] && [ \"\$pane_pid\" = \"\$runner_pid\" ] \
  && runner_live=true
[ \"\$runner_live\" = true ] || exit 0
[ \"\$active_sha\" = \"\$desired\" ] && exit 0
active=false
if [ -f \"\$state_dir/busy\" ]; then active=true; fi
for running in \"\$commands\"/*.running; do
  [ -e \"\$running\" ] || continue
  id=\${running##*/}; id=\${id%.running}
  [ -f \"\$commands/\$id.terminal\" ] || active=true
done
if [ \"\$active\" = true ]; then
  echo 'RUNNER_UPGRADE_BLOCKED_ACTIVE existing runner payload differs and work is active' >&2
  exit 6
fi
"
}

ensure_session() {
  local session_q root_q target_q
  session_q="$(single_quote "$SESSION_NAME")"
  root_q="$(single_quote "$CANONICAL_ROOT")"
  target_q="$(single_quote "$SESSION_NAME:runner.0")"
  check_channel >/dev/null
  guard_runner_upgrade
  write_session_files
  reconcile_session
  ssh_run "
set -e
umask 077
state_dir="\$HOME/$STATE_DIR"
canonical_root=$root_q
if tmux has-session -t $session_q 2>/dev/null; then
  protocol=\$(cat \"\$state_dir/runner.protocol\" 2>/dev/null || true)
  runner_pid=\$(cat \"\$state_dir/runner.pid\" 2>/dev/null || true)
  expected_sha=\$(cat \"\$state_dir/runner.expected.sha256\" 2>/dev/null || true)
  active_sha=\$(cat \"\$state_dir/runner.active.sha256\" 2>/dev/null || true)
  pane_info=\$(tmux list-panes -t $target_q -F '#{pane_dead} #{pane_pid}' 2>/dev/null || true)
  pane_dead=\${pane_info%% *}; pane_pid=\${pane_info#* }
  if [ \"\$protocol\" = 2 ] && [ -f \"\$state_dir/runner.ready\" ] \
    && [ -n \"\$runner_pid\" ] && kill -0 \"\$runner_pid\" 2>/dev/null \
    && [ -n \"\$expected_sha\" ] && [ \"\$active_sha\" = \"\$expected_sha\" ] \
    && [ \"\$pane_dead\" = 0 ] && [ \"\$pane_pid\" = \"\$runner_pid\" ]; then
    printf 'SESSION_OK name=%s state=%s remote=%s runner=%s reused=true\n' \
      '$SESSION_NAME' \"\$state_dir\" \"\$canonical_root\" '$SESSION_NAME:runner.0'
    exit 0
  fi
  tmux kill-session -t $session_q 2>/dev/null || true
fi
rm -f \"\$state_dir/runner.ready\" \"\$state_dir/runner.protocol\" \
  \"\$state_dir/runner.active.sha256\" \"\$state_dir/stop.requested\"
tmux new-session -d -s $session_q -n runner -c $root_q \"exec bash \\\"\$state_dir/runner.sh\\\"\"
i=0
while [ ! -f \"\$state_dir/runner.ready\" ] && [ \"\$i\" -lt 150 ]; do sleep 0.1; i=\$((i+1)); done
[ -f \"\$state_dir/runner.ready\" ] || { echo 'RUNNER_START_FAILED name=$SESSION_NAME' >&2; exit 5; }
printf 'SESSION_OK name=%s state=%s remote=%s runner=%s reused=false\n' \
  '$SESSION_NAME' \"\$state_dir\" \"\$canonical_root\" '$SESSION_NAME:runner.0'
"
}

status_session() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --command-id) COMMAND_ID="${2:-}"; shift 2 ;;
      --) shift; break ;;
      *) die "Unknown status argument: $1" ;;
    esac
  done
  [[ -z "$COMMAND_ID" || "$COMMAND_ID" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]] || die "unsafe --command-id"
  local session_q command_id_q root_q target_q
  session_q="$(single_quote "$SESSION_NAME")"
  command_id_q="$(single_quote "$COMMAND_ID")"
  root_q="$(single_quote "$CANONICAL_ROOT")"
  target_q="$(single_quote "$SESSION_NAME:runner.0")"
  reconcile_session
  ssh_run "
umask 077
state_dir=\"\$HOME/$STATE_DIR\"
canonical_root=$root_q
runner_pid=\$(cat \"\$state_dir/runner.pid\" 2>/dev/null || true)
expected_sha=\$(cat \"\$state_dir/runner.expected.sha256\" 2>/dev/null || true)
active_sha=\$(cat \"\$state_dir/runner.active.sha256\" 2>/dev/null || true)
pane_info=\$(tmux list-panes -t $target_q -F '#{pane_dead} #{pane_pid}' 2>/dev/null || true)
pane_dead=\${pane_info%% *}; pane_pid=\${pane_info#* }
if tmux has-session -t $session_q 2>/dev/null && [ -f \"\$state_dir/runner.ready\" ] \
  && [ -n \"\$runner_pid\" ] && kill -0 \"\$runner_pid\" 2>/dev/null \
  && [ -n \"\$expected_sha\" ] && [ \"\$active_sha\" = \"\$expected_sha\" ] \
  && [ \"\$pane_dead\" = 0 ] && [ \"\$pane_pid\" = \"\$runner_pid\" ]; then
  printf 'SESSION_STATUS running name=%s workspace=%s state=%s remote=%s runner=%s\n' \
    '$SESSION_NAME' '$WORKSPACE_HASH' \"\$state_dir\" \"\$canonical_root\" '$SESSION_NAME:runner.0'
else
  printf 'SESSION_STATUS stopped name=%s workspace=%s state=%s remote=%s\n' \
    '$SESSION_NAME' '$WORKSPACE_HASH' \"\$state_dir\" \"\$canonical_root\"
fi
if [ -f \"\$state_dir/busy\" ]; then
  printf 'BUSY '
  cat \"\$state_dir/busy\"
  echo
else
  echo 'BUSY none'
fi
if [ -L \"\$state_dir/current.log\" ] || [ -f \"\$state_dir/current.log\" ]; then
  echo \"CURRENT_LOG=\$state_dir/current.log\"
fi
command_id=$command_id_q
if [ -n \"\$command_id\" ]; then
  base=\"\$state_dir/commands/\$command_id\"
  if [ -f \"\$base.terminal\" ]; then state=\$(cat \"\$base.terminal\");
  elif [ -f \"\$base.running\" ]; then state=running;
  elif [ -f \"\$base.queued\" ]; then state=queued;
  else state=missing; fi
  rc=\$(cat \"\$base.exit\" 2>/dev/null || printf pending)
  echo \"COMMAND_STATUS id=\$command_id state=\$state rc=\$rc\"
fi
"
}

stop_session() {
  local session_q
  session_q="$(single_quote "$SESSION_NAME")"
  ssh_run "
set -e
umask 077
state_dir="\$HOME/$STATE_DIR"
commands=\"\$state_dir/commands\"
mkdir -p \"\$commands\"
: >\"\$state_dir/stop.requested\"; chmod 600 \"\$state_dir/stop.requested\"
write_once() {
  path=\"\$1\"; content=\"\${2-}\"
  [ ! -e \"\$path\" ] || return 1
  tmp=\$(mktemp \"\$state_dir/.stop.XXXXXX\")
  printf '%s\\n' \"\$content\" >\"\$tmp\"; chmod 600 \"\$tmp\"
  ln \"\$tmp\" \"\$path\" 2>/dev/null && { rm -f \"\$tmp\"; return 0; }
  rm -f \"\$tmp\"; return 1
}
commit_aborted() {
  id=\"\$1\"; base=\"\$commands/\$id\"
  exec 6>\"\$base.transition.lock\"; flock 6
  if [ -f \"\$base.terminal\" ]; then
    if [ ! -f \"\$base.exit\" ]; then
      existing=\$(cat \"\$base.terminal\" 2>/dev/null || printf failed)
      case \"\$existing\" in completed) rc=0 ;; aborted) rc=130 ;; lost) rc=125 ;; *) rc=1 ;; esac
      tmp=\$(mktemp \"\$state_dir/.exit-repair.XXXXXX\")
      printf '%s\n' \"\$rc\" >\"\$tmp\"; chmod 600 \"\$tmp\"; mv -f \"\$tmp\" \"\$base.exit\"
    fi
  else
    tmp=\$(mktemp \"\$state_dir/.exit.XXXXXX\")
    printf '130\n' >\"\$tmp\"; chmod 600 \"\$tmp\"; mv -f \"\$tmp\" \"\$base.exit\"
    write_once \"\$base.aborted\" \"at=\$(date +%s) rc=130\" || true
    if write_once \"\$base.terminal\" aborted; then
      printf '%s\taborted\trc=130\n' \"\$(date +%s)\" >>\"\$base.events\"
      chmod 600 \"\$base.events\"
    fi
  fi
  flock -u 6; exec 6>&-
}
for committed in \"\$commands\"/*.request-complete; do
  [ -e \"\$committed\" ] || continue
  id=\${committed##*/}; id=\${id%.request-complete}; base=\"\$commands/\$id\"
  if [ -f \"\$base.terminal\" ]; then
    [ -f \"\$base.exit\" ] || commit_aborted \"\$id\"
    continue
  fi
  if [ -f \"\$base.running\" ]; then
    : >\"\$base.abort-requested\"; chmod 600 \"\$base.abort-requested\"
  else
    commit_aborted \"\$id\"
  fi
done
if tmux has-session -t $session_q 2>/dev/null; then
  tmux kill-session -t $session_q
  sleep 1
  echo \"SESSION_STOPPED name=$SESSION_NAME\"
else
  echo \"SESSION_ALREADY_STOPPED name=$SESSION_NAME\"
fi
"
  reconcile_session
}

tail_log() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --command-id) COMMAND_ID="${2:-}"; shift 2 ;;
      --lines) TAIL_LINES="${2:-}"; shift 2 ;;
      --) shift; break ;;
      *) die "Unknown tail argument: $1" ;;
    esac
  done
  [[ "$TAIL_LINES" =~ ^[0-9]+$ ]] || die "--lines must be a positive integer"
  [[ -z "$COMMAND_ID" || "$COMMAND_ID" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]] || die "unsafe --command-id"

  local log_path
  if [[ -n "$COMMAND_ID" ]]; then
    log_path="\$HOME/$STATE_DIR/commands/$COMMAND_ID.log"
  else
    log_path="\$HOME/$STATE_DIR/current.log"
  fi
  ssh_run "
log=$log_path
if [ ! -e \"\$log\" ]; then
  echo \"LOG_MISSING \$log\" >&2
  exit 2
fi
tail -n $TAIL_LINES \"\$log\"
"
}

wait_for_command() {
  local command_id_q session_q target_q
  command_id_q="$(single_quote "$COMMAND_ID")"
  session_q="$(single_quote "$SESSION_NAME")"
  target_q="$(single_quote "$SESSION_NAME:runner.0")"
  ssh_run "
set -e
umask 077
state_dir=\"\$HOME/$STATE_DIR\"; commands=\"\$state_dir/commands\"; id=$command_id_q
base=\"\$commands/\$id\"; deadline=\$((\$(date +%s) + $WAIT_TIMEOUT))
write_once() {
  path=\"\$1\"; content=\"\${2-}\"
  [ ! -e \"\$path\" ] || return 1
  tmp=\$(mktemp \"\$state_dir/.wait.XXXXXX\")
  printf '%s\\n' \"\$content\" >\"\$tmp\"; chmod 600 \"\$tmp\"
  ln \"\$tmp\" \"\$path\" 2>/dev/null && { rm -f \"\$tmp\"; return 0; }
  rm -f \"\$tmp\"; return 1
}
commit_wait_terminal() {
  state=\"\$1\"; rc=\"\$2\"
  exec 6>\"\$base.transition.lock\"; flock 6
  if [ -f \"\$base.terminal\" ]; then
    if [ ! -f \"\$base.exit\" ]; then
      existing=\$(cat \"\$base.terminal\" 2>/dev/null || printf failed)
      case \"\$existing\" in completed) rc=0 ;; aborted) rc=130 ;; lost) rc=125 ;; *) rc=1 ;; esac
      tmp=\$(mktemp \"\$state_dir/.exit-repair.XXXXXX\")
      printf '%s\n' \"\$rc\" >\"\$tmp\"; chmod 600 \"\$tmp\"; mv -f \"\$tmp\" \"\$base.exit\"
    fi
  else
    tmp=\$(mktemp \"\$state_dir/.exit.XXXXXX\")
    printf '%s\n' \"\$rc\" >\"\$tmp\"; chmod 600 \"\$tmp\"; mv -f \"\$tmp\" \"\$base.exit\"
    write_once \"\$base.\$state\" \"at=\$(date +%s) rc=\$rc\" || true
    write_once \"\$base.terminal\" \"\$state\" || true
  fi
  flock -u 6; exec 6>&-
}
while [ ! -f \"\$base.terminal\" ] || [ ! -f \"\$base.exit\" ]; do
  if [ -f \"\$base.terminal\" ]; then
    commit_wait_terminal failed 1
    continue
  fi
  if [ \"\$(date +%s)\" -ge \"\$deadline\" ]; then
    echo \"COMMAND_WAIT_TIMEOUT id=\$id timeout=$WAIT_TIMEOUT state_dir=\$state_dir\" >&2
    exit 124
  fi
  runner_pid=\$(cat \"\$state_dir/runner.pid\" 2>/dev/null || true)
  expected_sha=\$(cat \"\$state_dir/runner.expected.sha256\" 2>/dev/null || true)
  active_sha=\$(cat \"\$state_dir/runner.active.sha256\" 2>/dev/null || true)
  pane_info=\$(tmux list-panes -t $target_q -F '#{pane_dead} #{pane_pid}' 2>/dev/null || true)
  pane_dead=\${pane_info%% *}; pane_pid=\${pane_info#* }
  if { ! tmux has-session -t $session_q 2>/dev/null \
    || [ ! -f \"\$state_dir/runner.ready\" ] || [ -z \"\$runner_pid\" ] \
    || ! kill -0 \"\$runner_pid\" 2>/dev/null \
    || [ -z \"\$expected_sha\" ] || [ \"\$active_sha\" != \"\$expected_sha\" ] \
    || [ \"\$pane_dead\" != 0 ] || [ \"\$pane_pid\" != \"\$runner_pid\" ]; } \
    && [ -f \"\$base.running\" ] && [ ! -f \"\$base.terminal\" ]; then
    if [ -f \"\$base.abort-requested\" ]; then state=aborted; rc=130; else state=lost; rc=125; fi
    commit_wait_terminal \"\$state\" \"\$rc\"
  fi
  sleep 1
done
[ ! -f \"\$base.log\" ] || cat \"\$base.log\"
rc=\$(cat \"\$base.exit\"); state=\$(cat \"\$base.terminal\" 2>/dev/null || { [ \"\$rc\" -eq 0 ] && echo completed || echo failed; })
printf '__CODEX_CMD_DONE id=%s state=%s rc=%s\\n' \"\$id\" \"\$state\" \"\$rc\"
exit \"\$rc\"
"
}

run_command() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --lock) LOCK_MODE="${2:-}"; shift 2 ;;
      --no-wait) WAIT=false; shift ;;
      --command-id) COMMAND_ID="${2:-}"; shift 2 ;;
      --wait-timeout) WAIT_TIMEOUT="${2:-}"; shift 2 ;;
      --) shift; break ;;
      *) break ;;
    esac
  done
  [[ "$LOCK_MODE" == "none" || "$LOCK_MODE" == "exclusive" ]] || die "--lock must be none or exclusive"
  [[ "$WAIT_TIMEOUT" =~ ^[1-9][0-9]*$ ]] || die "--wait-timeout must be a positive integer"
  [[ $# -gt 0 ]] || die "run requires COMMAND after --"
  local user_cmd="$*"
  [[ -n "$COMMAND_ID" ]] || COMMAND_ID="$(date +%Y%m%d-%H%M%S)-$$-${RANDOM:-0}"
  [[ "$COMMAND_ID" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]] || die "unsafe --command-id; use 1-128 letters, digits, dot, underscore, or dash"

  ensure_session >/dev/null
  reconcile_session

  local command_id_q lock_q registration registration_status
  command_id_q="$(single_quote "$COMMAND_ID")"
  lock_q="$(single_quote "$LOCK_MODE")"
  set +e
  registration="$(printf '%s' "$user_cmd" | ssh_run_with_stdin "
set -e
umask 077
state_dir=\"\$HOME/$STATE_DIR\"; commands=\"\$state_dir/commands\"; id=$command_id_q; lock_mode=$lock_q
mkdir -p \"\$commands\"; chmod 700 \"\$state_dir\" \"\$commands\"
stage=\$(mktemp -d \"\$commands/.register.\$id.XXXXXX\")
trap 'rm -rf \"\$stage\"' EXIT
cat >\"\$stage/line\"
{ printf 'lock=%s\\n' \"\$lock_mode\"; cat \"\$stage/line\"; } >\"\$stage/request\"
if command -v sha256sum >/dev/null 2>&1; then digest=\$(sha256sum \"\$stage/request\" | awk '{print \$1}')
elif command -v shasum >/dev/null 2>&1; then digest=\$(shasum -a 256 \"\$stage/request\" | awk '{print \$1}')
else digest=\$(openssl dgst -sha256 \"\$stage/request\" | awk '{print \$NF}'); fi
printf '%s\\n' \"\$digest\" >\"\$stage/request.sha256\"
printf '%s\\n' \"\$lock_mode\" >\"\$stage/lock-mode\"
printf 'at=%s\\n' \"\$(date +%s)\" >\"\$stage/queued\"
printf '%s\\tqueued\\tlock=%s\\n' \"\$(date +%s)\" \"\$lock_mode\" >\"\$stage/events\"
printf '%s\\n' \"\$digest\" >\"\$stage/request-complete\"
chmod 600 \"\$stage/line\" \"\$stage/request.sha256\" \"\$stage/lock-mode\" \
  \"\$stage/queued\" \"\$stage/events\" \"\$stage/request-complete\"
rm -f \"\$stage/request\"

exec 7>\"\$state_dir/dispatch.lock\"; flock 7
base=\"\$commands/\$id\"
if [ -f \"\$base.request-complete\" ]; then
  stored=\$(cat \"\$base.request.sha256\" 2>/dev/null || true)
  committed=\$(cat \"\$base.request-complete\" 2>/dev/null || true)
  [ -f \"\$base.line\" ] && [ -n \"\$stored\" ] && [ \"\$stored\" = \"\$committed\" ] || {
    echo \"COMMAND_REGISTRATION_CORRUPT id=\$id\" >&2; exit 5;
  }
  [ \"\$stored\" = \"\$digest\" ] || { echo \"COMMAND_ID_CONFLICT id=\$id\" >&2; exit 4; }
  if [ -f \"\$base.terminal\" ]; then state=\$(cat \"\$base.terminal\");
  elif [ -f \"\$base.running\" ]; then state=running; else state=queued; fi
  echo \"COMMAND_REGISTERED attached id=\$id state=\$state\"
  exit 0
fi
if [ -f \"\$base.running\" ] || [ -f \"\$base.terminal\" ]; then
  echo \"COMMAND_REGISTRATION_CORRUPT id=\$id state_without_commit=true\" >&2
  exit 5
fi
recovered=false
sequence_file=\"\$state_dir/queue.sequence\"
last_sequence=\$(cat \"\$sequence_file\" 2>/dev/null || printf 0)
case \"\$last_sequence\" in ''|*[!0-9]*) echo 'QUEUE_SEQUENCE_CORRUPT' >&2; exit 5;; esac
next_sequence=\$((last_sequence + 1))
queue_sequence=\$(printf '%020d' \"\$next_sequence\")
sequence_tmp=\$(mktemp \"\$state_dir/.queue-sequence.XXXXXX\")
printf '%s\n' \"\$next_sequence\" >\"\$sequence_tmp\"; chmod 600 \"\$sequence_tmp\"
mv -f \"\$sequence_tmp\" \"\$sequence_file\"
printf '%s\n' \"\$queue_sequence\" >\"\$stage/queue-seq\"; chmod 600 \"\$stage/queue-seq\"
for suffix in line request.sha256 lock-mode queued events queue-seq; do
  if [ -e \"\$base.\$suffix\" ]; then recovered=true; rm -f \"\$base.\$suffix\"; fi
done
# Publish data first. request-complete is the single atomic commit point; the
# runner never consumes queued files without it.
mv \"\$stage/line\" \"\$base.line\"
mv \"\$stage/request.sha256\" \"\$base.request.sha256\"
mv \"\$stage/lock-mode\" \"\$base.lock-mode\"
mv \"\$stage/queued\" \"\$base.queued\"
mv \"\$stage/events\" \"\$base.events\"
mv \"\$stage/queue-seq\" \"\$base.queue-seq\"
mv \"\$stage/request-complete\" \"\$base.request-complete\"
ln -sfn \"\$base.log\" \"\$state_dir/current.log\"
echo \"COMMAND_REGISTERED new id=\$id state=queued recovered=\$recovered\"
")"
  registration_status=$?
  set -e
  [[ "$registration_status" -eq 0 ]] || { [[ -n "$registration" ]] && printf '%s\n' "$registration" >&2; exit "$registration_status"; }
  if [[ "$registration" == COMMAND_REGISTERED\ attached* ]]; then
    echo "COMMAND_ATTACHED id=$COMMAND_ID session=$SESSION_NAME ${registration#* id=$COMMAND_ID } log=$(remote_state_path)/commands/$COMMAND_ID.log"
  else
    echo "COMMAND_STARTED id=$COMMAND_ID session=$SESSION_NAME state=queued log=$(remote_state_path)/commands/$COMMAND_ID.log"
  fi

  if [[ "$WAIT" == true ]]; then
    wait_for_command
  fi
}

case "$ACTION" in
  check) check_channel ;;
  install-tmux) install_tmux "$@" ;;
  ensure) ensure_session ;;
  status) status_session "$@" ;;
  stop) stop_session ;;
  tail) tail_log "$@" ;;
  run) run_command "$@" ;;
  *) die "Unknown action: $ACTION" ;;
esac
