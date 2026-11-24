#!/usr/bin/env bash
# Installed and executed only inside the canonical remote Android workspace.

REMOTE_V2_RUNTIME_VERSION=2
REMOTE_V2_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REMOTE_V2_BASE="$(cd "$REMOTE_V2_SCRIPT_DIR/../.." && pwd -P)"
REMOTE_V2_PROJECT_ROOT="$(cd "$REMOTE_V2_BASE/../.." && pwd -P)"
REMOTE_V2_CONFIG="$REMOTE_V2_BASE/config.env"
REMOTE_V2_PROFILES="$REMOTE_V2_BASE/profiles"
REMOTE_V2_LOGS="$REMOTE_V2_BASE/logs"
REMOTE_V2_MANIFESTS="$REMOTE_V2_BASE/manifests"
REMOTE_V2_INITIALIZED="${REMOTE_V2_INITIALIZED:-false}"

remote_v2_die() {
  echo "REMOTE_V2_ERROR $*" >&2
  return 2
}

remote_v2_safe_id() {
  [[ "$1" =~ ^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$ ]]
}

remote_v2_safe_relative() {
  local value="$1"
  [[ "$value" == "." ]] && return 0
  [[ -n "$value" && "$value" != /* && "/$value/" != *"/../"* && "/$value/" != *"/./"* && "$value" != *$'\n'* ]]
}

remote_v2_atomic_from_stdin() {
  local target="$1" directory tmp
  directory="$(dirname "$target")"
  mkdir -p "$directory"
  tmp="$(mktemp "$directory/.remote-v2.XXXXXX")" || return 1
  trap 'rm -f "$tmp"' RETURN
  cat >"$tmp"
  chmod 600 "$tmp"
  mv -f "$tmp" "$target"
  trap - RETURN
}

remote_v2_load_config() {
  [[ -f "$REMOTE_V2_CONFIG" ]] || remote_v2_die "CONFIG_MISSING run configure first"
  # Generated only by remote_v2_configure with shell-quoted scalar values.
  # shellcheck disable=SC1090
  source "$REMOTE_V2_CONFIG"
  [[ "${PROJECT_ROOT:-}" == "$REMOTE_V2_PROJECT_ROOT" ]] || remote_v2_die "CONFIG_ROOT_MISMATCH"
  remote_v2_safe_relative "${ENVSETUP_SCRIPT:-}" || remote_v2_die "CONFIG_ENVSETUP_UNSAFE"
  remote_v2_safe_relative "${PRODUCT_OUT_DIR_REL:-}" || remote_v2_die "CONFIG_PRODUCT_OUT_UNSAFE"
  [[ -n "${LUNCH_TARGET:-}" ]] || remote_v2_die "CONFIG_LUNCH_MISSING"
}

remote_v2_discover() {
  local working_subpath="." entry="" hints="" envsetup="build/envsetup.sh" lunch="" product="" status="partial"
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --working-subpath) working_subpath="${2:-}"; shift 2 ;;
      *) remote_v2_die "discover unknown argument: $1"; return $? ;;
    esac
  done
  remote_v2_safe_relative "$working_subpath" || remote_v2_die "WORKING_SUBPATH_UNSAFE"
  if [[ -f "$REMOTE_V2_CONFIG" ]]; then
    remote_v2_load_config || return $?
    envsetup="$ENVSETUP_SCRIPT"; lunch="$LUNCH_TARGET"; product="$PRODUCT_OUT_DIR_REL"
    entry="${BUILD_ENTRY_SCRIPT:-}"
  else
    entry="$({
      find "$REMOTE_V2_PROJECT_ROOT" -maxdepth 1 -type f -name 'debug.sh' 2>/dev/null
      find "$REMOTE_V2_PROJECT_ROOT" -maxdepth 1 -type f -name 'debug*.sh' ! -name debug.sh 2>/dev/null | sort
      find "$REMOTE_V2_PROJECT_ROOT" -maxdepth 1 -type f -name '*.sh' ! -name 'debug*.sh' 2>/dev/null | sort
    } | sed -n '1p')"
    if [[ -n "$entry" ]]; then
      hints="$(grep -nE 'source[[:space:]]+.*build/envsetup|^[[:space:]]*\.[[:space:]]+.*build/envsetup|lunch[[:space:]]+|out/target/product' "$entry" 2>/dev/null || true)"
      lunch="$(printf '%s\n' "$hints" | sed -nE 's/.*lunch[[:space:]]+([A-Za-z0-9_.+-]+).*/\1/p' | sed -n '1p')"
      product="$(printf '%s\n' "$hints" | sed -nE 's#.*(out/target/product/[A-Za-z0-9_.-]+).*#\1#p' | sed -n '1p')"
      entry="${entry#"$REMOTE_V2_PROJECT_ROOT/"}"
    fi
    if [[ -z "$product" && -n "$lunch" ]]; then
      product="out/target/product/${lunch%%-*}"
    fi
  fi
  [[ -n "$lunch" && -n "$product" ]] && status=complete
  printf 'DISCOVERY_STATUS=%q\n' "$status"
  printf 'PROJECT_ROOT=%q\n' "$REMOTE_V2_PROJECT_ROOT"
  printf 'WORKING_SUBPATH=%q\n' "$working_subpath"
  printf 'ENVSETUP_SCRIPT=%q\n' "$envsetup"
  [[ -z "$entry" ]] || printf 'BUILD_ENTRY_SCRIPT=%q\n' "$entry"
  [[ -z "$lunch" ]] || printf 'LUNCH_TARGET=%q\n' "$lunch"
  [[ -z "$product" ]] || printf 'PRODUCT_OUT_DIR_REL=%q\n' "$product"
}

remote_v2_configure() {
  local envsetup="build/envsetup.sh" lunch="" product="" entry=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --envsetup) envsetup="${2:-}"; shift 2 ;;
      --lunch) lunch="${2:-}"; shift 2 ;;
      --product-out) product="${2:-}"; shift 2 ;;
      --build-entry) entry="${2:-}"; shift 2 ;;
      *) remote_v2_die "configure unknown argument: $1"; return $? ;;
    esac
  done
  remote_v2_safe_relative "$envsetup" || remote_v2_die "ENVSETUP_UNSAFE"
  remote_v2_safe_relative "$product" || remote_v2_die "PRODUCT_OUT_UNSAFE"
  [[ -n "$lunch" && "$lunch" != *$'\n'* ]] || remote_v2_die "LUNCH_INVALID"
  [[ -z "$entry" ]] || remote_v2_safe_relative "$entry" || remote_v2_die "BUILD_ENTRY_UNSAFE"
  {
    printf 'PROJECT_ROOT=%q\n' "$REMOTE_V2_PROJECT_ROOT"
    printf 'ENVSETUP_SCRIPT=%q\n' "$envsetup"
    printf 'LUNCH_TARGET=%q\n' "$lunch"
    printf 'PRODUCT_OUT_DIR_REL=%q\n' "$product"
    printf 'BUILD_ENTRY_SCRIPT=%q\n' "$entry"
  } | remote_v2_atomic_from_stdin "$REMOTE_V2_CONFIG"
  echo "REMOTE_V2_CONFIG_OK file=$REMOTE_V2_CONFIG"
}

remote_v2_profile_set() {
  local profile="" modules="" touch_rel="" spec module body relative destination index=0
  local -a artifacts=()
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --profile) profile="${2:-}"; shift 2 ;;
      --modules) modules="${2:-}"; shift 2 ;;
      --artifact) artifacts+=("${2:-}"); shift 2 ;;
      --touch-path) touch_rel="${2:-}"; shift 2 ;;
      *) remote_v2_die "profile-set unknown argument: $1"; return $? ;;
    esac
  done
  remote_v2_safe_id "$profile" || remote_v2_die "PROFILE_ID_UNSAFE"
  [[ -n "$modules" && "$modules" != *$'\n'* ]] || remote_v2_die "MODULES_INVALID"
  ((${#artifacts[@]} > 0)) || remote_v2_die "ARTIFACT_REQUIRED"
  [[ -z "$touch_rel" ]] || remote_v2_safe_relative "$touch_rel" || remote_v2_die "TOUCH_PATH_UNSAFE"
  mkdir -p "$REMOTE_V2_PROFILES"
  {
    printf 'PROFILE=%q\n' "$profile"
    printf 'MODULES=%q\n' "$modules"
    printf 'TOUCH_REL=%q\n' "$touch_rel"
    printf 'ARTIFACT_COUNT=%q\n' "${#artifacts[@]}"
    for spec in "${artifacts[@]}"; do
      module="${spec%%=*}"; body="${spec#*=}"
      relative="${body%%|*}"; destination="${body#*|}"
      [[ "$module" != "$spec" && "$destination" != "$body" ]] || remote_v2_die "ARTIFACT_FORMAT expected MODULE=RELATIVE|DEST"
      remote_v2_safe_id "$module" || remote_v2_die "ARTIFACT_MODULE_UNSAFE"
      remote_v2_safe_relative "$relative" || remote_v2_die "ARTIFACT_PATH_UNSAFE"
      [[ "$destination" == /* && "$destination" != *$'\n'* ]] || remote_v2_die "ARTIFACT_DEST_UNSAFE"
      printf 'ARTIFACT_MODULE_%s=%q\n' "$index" "$module"
      printf 'ARTIFACT_REL_%s=%q\n' "$index" "$relative"
      printf 'ARTIFACT_DEST_%s=%q\n' "$index" "$destination"
      index=$((index + 1))
    done
  } | remote_v2_atomic_from_stdin "$REMOTE_V2_PROFILES/$profile.env"
  echo "REMOTE_V2_PROFILE_OK profile=$profile modules=$modules artifacts=${#artifacts[@]}"
}

remote_v2_load_profile() {
  local profile="$1" file="$REMOTE_V2_PROFILES/$1.env"
  remote_v2_safe_id "$profile" || remote_v2_die "PROFILE_ID_UNSAFE"
  [[ -f "$file" ]] || remote_v2_die "PROFILE_MISSING $profile"
  # Generated only by remote_v2_profile_set.
  # shellcheck disable=SC1090
  source "$file"
  [[ "$PROFILE" == "$profile" && "$ARTIFACT_COUNT" =~ ^[1-9][0-9]*$ ]] || remote_v2_die "PROFILE_CORRUPT $profile"
}

remote_v2_plan() {
  local profile="" i module relative destination
  while [[ $# -gt 0 ]]; do
    case "$1" in --profile) profile="${2:-}"; shift 2 ;; *) remote_v2_die "plan unknown argument: $1"; return $? ;; esac
  done
  remote_v2_load_config || return $?
  remote_v2_load_profile "$profile" || return $?
  echo "PLAN profile=$PROFILE"
  echo "PROJECT_ROOT=$REMOTE_V2_PROJECT_ROOT"
  echo "LUNCH_TARGET=$LUNCH_TARGET"
  echo "PRODUCT_OUT_DIR_REL=$PRODUCT_OUT_DIR_REL"
  echo "MODULES=$MODULES"
  for ((i=0; i<ARTIFACT_COUNT; i++)); do
    eval "module=\${ARTIFACT_MODULE_$i}; relative=\${ARTIFACT_REL_$i}; destination=\${ARTIFACT_DEST_$i}"
    printf 'ARTIFACT module=%s relative=%s destination=%s\n' "$module" "$relative" "$destination"
  done
}

remote_v2_session_init() {
  remote_v2_load_config || return $?
  if [[ "$REMOTE_V2_INITIALIZED" == true && "${REMOTE_V2_ACTIVE_LUNCH:-}" == "$LUNCH_TARGET" ]]; then
    return 0
  fi
  cd "$REMOTE_V2_PROJECT_ROOT" || return 2
  set +u
  # shellcheck disable=SC1090
  source "$ENVSETUP_SCRIPT" || return $?
  lunch "$LUNCH_TARGET" || return $?
  REMOTE_V2_ACTIVE_LUNCH="$LUNCH_TARGET"
  REMOTE_V2_INITIALIZED=true
  export REMOTE_V2_ACTIVE_LUNCH REMOTE_V2_INITIALIZED
  echo "REMOTE_V2_SESSION_READY lunch=$LUNCH_TARGET"
}

remote_v2_key_errors() {
  local log="$1"
  echo KEY_ERRORS_BEGIN
  grep -n -i -E 'error:|fatal:|FAILED:|ninja failed|build failed|undefined reference|No rule to make target|Traceback' "$log" 2>/dev/null | tail -n 40 || true
  echo KEY_ERRORS_END
}

remote_v2_build() {
  local profile="" workspace_id="" command_id="" jobs="" mode=modules i module relative destination
  local start_ns finish_ns log manifest_dir manifest_file artifact_path rc before
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --profile) profile="${2:-}"; shift 2 ;;
      --workspace-id) workspace_id="${2:-}"; shift 2 ;;
      --command-id) command_id="${2:-}"; shift 2 ;;
      --jobs) jobs="${2:-}"; shift 2 ;;
      --mode) mode="${2:-}"; shift 2 ;;
      *) remote_v2_die "build unknown argument: $1"; return $? ;;
    esac
  done
  remote_v2_safe_id "$workspace_id" || remote_v2_die "WORKSPACE_ID_UNSAFE"
  remote_v2_safe_id "$command_id" || remote_v2_die "COMMAND_ID_UNSAFE"
  [[ "$mode" == modules || "$mode" == full ]] || remote_v2_die "BUILD_MODE_INVALID"
  [[ -z "$jobs" || "$jobs" =~ ^[1-9][0-9]*$ ]] || remote_v2_die "JOBS_INVALID"
  remote_v2_load_config || return $?
  remote_v2_load_profile "$profile" || return $?
  mkdir -p "$REMOTE_V2_LOGS" "$REMOTE_V2_MANIFESTS"
  log="$REMOTE_V2_LOGS/$command_id.log"
  manifest_dir="$REMOTE_V2_MANIFESTS/$command_id"
  [[ ! -e "$manifest_dir" ]] || { echo "REMOTE_V2_MANIFEST_SET_EXISTS command_id=$command_id" >&2; return 4; }
  mkdir -m 700 "$manifest_dir"
  start_ns="$(python3 -c 'import time; print(time.time_ns())')"
  echo "BUILD_START profile=$profile command_id=$command_id start_ns=$start_ns log=$log"
  for ((i=0; i<ARTIFACT_COUNT; i++)); do
    eval "relative=\${ARTIFACT_REL_$i}"
    artifact_path="$REMOTE_V2_PROJECT_ROOT/$relative"
    before="$(stat -c '%Y:%s' "$artifact_path" 2>/dev/null || printf missing)"
    echo "ARTIFACT_BEFORE relative=$relative identity=$before"
  done
  if [[ -n "$TOUCH_REL" ]]; then
    [[ -e "$REMOTE_V2_PROJECT_ROOT/$TOUCH_REL" ]] || remote_v2_die "TOUCH_TARGET_MISSING $TOUCH_REL"
    touch "$REMOTE_V2_PROJECT_ROOT/$TOUCH_REL"
    echo "TOUCH_TARGET relative=$TOUCH_REL"
  fi
  rc=0
  if [[ "$mode" == full ]]; then
    [[ -n "$BUILD_ENTRY_SCRIPT" ]] || remote_v2_die "BUILD_ENTRY_MISSING"
    (cd "$REMOTE_V2_PROJECT_ROOT" && bash "$BUILD_ENTRY_SCRIPT") >"$log" 2>&1 || rc=$?
  else
    remote_v2_session_init || return $?
    jobs="${jobs:-$(nproc 2>/dev/null || echo 8)}"
    m -j"$jobs" $MODULES >"$log" 2>&1 || rc=$?
  fi
  if [[ "$rc" -ne 0 ]]; then
    echo "BUILD_FAIL rc=$rc profile=$profile command_id=$command_id log=$log"
    remote_v2_key_errors "$log"
    return "$rc"
  fi
  finish_ns="$(python3 -c 'import time; print(time.time_ns())')"
  echo "BUILD_OK profile=$profile command_id=$command_id finish_ns=$finish_ns log=$log"
  for ((i=0; i<ARTIFACT_COUNT; i++)); do
    eval "module=\${ARTIFACT_MODULE_$i}; relative=\${ARTIFACT_REL_$i}; destination=\${ARTIFACT_DEST_$i}"
    artifact_path="$REMOTE_V2_PROJECT_ROOT/$relative"
    manifest_file="$manifest_dir/$i.json"
    python3 -B "$REMOTE_V2_SCRIPT_DIR/remote_artifact_manifest_cli.py" \
      --artifact "$artifact_path" --remote-root "$REMOTE_V2_PROJECT_ROOT" \
      --module "$module" --profile "$profile" --workspace-id "$workspace_id" \
      --command-id "$command_id" --build-started-ns "$start_ns" \
      --build-finished-ns "$finish_ns" --out "$manifest_file" || return $?
    printf 'REMOTE_ARTIFACT_MANIFEST_B64 index=%s module=%s destination=%q payload=' "$i" "$module" "$destination"
    base64 <"$manifest_file" | tr -d '\n'
    printf '\n'
  done
}

remote_v2_checkpoint() {
  local name="" purpose="" stage final
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --name) name="${2:-}"; shift 2 ;;
      --purpose) purpose="${2:-}"; shift 2 ;;
      *) remote_v2_die "checkpoint unknown argument: $1"; return $? ;;
    esac
  done
  remote_v2_safe_id "$name" || remote_v2_die "CHECKPOINT_ID_UNSAFE"
  final="$REMOTE_V2_BASE/checkpoints/$name"
  [[ ! -e "$final" ]] || { echo "REMOTE_V2_CHECKPOINT_EXISTS name=$name"; return 0; }
  mkdir -p "$REMOTE_V2_BASE/checkpoints"
  stage="$(mktemp -d "$REMOTE_V2_BASE/checkpoints/.stage.$name.XXXXXX")"
  trap 'rm -rf "$stage"' RETURN
  cd "$REMOTE_V2_PROJECT_ROOT" || return 2
  if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    git status --short >"$stage/status.txt"
    git diff --binary >"$stage/unstaged.patch"
    git diff --cached --binary >"$stage/staged.patch"
    git ls-files --others --exclude-standard -z | python3 -c '
import sys
for path in sys.stdin.buffer.read().split(b"\0"):
    if not path or path.split(b"/", 1)[0] in {b"out", b".repo", b".git", b".codex"}:
        continue
    sys.stdout.buffer.write(path + b"\0")
' >"$stage/untracked.nul"
    if [[ -s "$stage/untracked.nul" ]]; then
      tar --null -czf "$stage/untracked.tgz" -T "$stage/untracked.nul"
    fi
    printf 'mode=git\nhead=%s\nbranch=%s\npurpose=%s\n' \
      "$(git rev-parse HEAD)" "$(git branch --show-current)" "$purpose" >"$stage/meta.txt"
  elif [[ -d .repo ]] && command -v repo >/dev/null 2>&1; then
    command -v tar >/dev/null 2>&1 || remote_v2_die "CHECKPOINT_TAR_MISSING"
    repo status >"$stage/status.txt"
    repo manifest -r -o "$stage/manifest.xml"
    mkdir -p "$stage/repositories"
    : >"$stage/repositories.tsv"
    export REMOTE_V2_CHECKPOINT_STAGE="$stage"
    repo forall -c '
      set -eu
      status=$(git status --porcelain=v1)
      [ -n "$status" ] || exit 0
      safe=$(printf "%s" "$REPO_PATH" | tr -c "A-Za-z0-9._-" "_")
      [ -n "$safe" ] || safe=root
      if command -v sha256sum >/dev/null 2>&1; then digest=$(printf "%s" "$REPO_PATH" | sha256sum | cut -d" " -f1)
      else digest=$(printf "%s" "$REPO_PATH" | shasum -a 256 | cut -d" " -f1); fi
      digest=$(printf "%s" "$digest" | cut -c1-12)
      key="$safe-$digest"
      target="$REMOTE_V2_CHECKPOINT_STAGE/repositories/$key"
      mkdir -p "$target"
      printf "%s\n" "$REPO_PATH" >"$target/repo_path.txt"
      git status --short >"$target/status.txt"
      git diff --binary >"$target/unstaged.patch"
      git diff --cached --binary >"$target/staged.patch"
      git ls-files --others --exclude-standard -z >"$target/untracked.nul"
      if [ -s "$target/untracked.nul" ]; then
        tar --null -czf "$target/untracked.tgz" -T "$target/untracked.nul"
      fi
      printf "%s\t%s\n" "$key" "$REPO_PATH" >>"$REMOTE_V2_CHECKPOINT_STAGE/repositories.tsv"
    '
    cat >"$stage/restore.sh" <<'EOF_RESTORE'
#!/usr/bin/env bash
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1
root="${1:-.}"
checkpoint="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
while IFS=$'\t' read -r key repo_path; do
  target="$root/$repo_path"
  record="$checkpoint/repositories/$key"
  [[ -d "$target/.git" || -f "$target/.git" ]] || { echo "missing repo: $target" >&2; exit 2; }
  if [[ -s "$record/staged.patch" ]]; then
    git -C "$target" apply "$record/staged.patch"
    git -C "$target" apply --cached "$record/staged.patch"
  fi
  [[ ! -s "$record/unstaged.patch" ]] || git -C "$target" apply "$record/unstaged.patch"
  [[ ! -f "$record/untracked.tgz" ]] || tar -xzf "$record/untracked.tgz" -C "$target"
done <"$checkpoint/repositories.tsv"
EOF_RESTORE
    chmod 700 "$stage/restore.sh"
    printf 'mode=repo\npurpose=%s\n' "$purpose" >"$stage/meta.txt"
  else
    remote_v2_die "CHECKPOINT_PROJECT_UNSUPPORTED"
    return $?
  fi
  chmod -R go-rwx "$stage"
  mv "$stage" "$final"
  trap - RETURN
  echo "REMOTE_V2_CHECKPOINT_OK name=$name path=$final"
}

remote_v2_cli() {
  local action="${1:-}"; [[ -n "$action" ]] || remote_v2_die "action required"
  shift || true
  case "$action" in
    discover) remote_v2_discover "$@" ;;
    configure) remote_v2_configure "$@" ;;
    profile-set) remote_v2_profile_set "$@" ;;
    plan) remote_v2_plan "$@" ;;
    build) remote_v2_build "$@" ;;
    checkpoint) remote_v2_checkpoint "$@" ;;
    *) remote_v2_die "unknown action: $action" ;;
  esac
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  set -euo pipefail
  umask 077
  remote_v2_cli "$@"
fi
