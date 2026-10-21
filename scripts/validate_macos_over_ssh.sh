#!/usr/bin/env bash
set -euo pipefail

host="${1:?用法: scripts/validate_macos_over_ssh.sh SSH_HOST}"
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$repo_root/scripts/validator_cleanup.sh"
validator_cleanup_install "$repo_root"

path_guard="$repo_root/scripts/validator_path_guard.py"
remote_authority="$(
  ssh -o BatchMode=yes "$host" \
    'authority="${TMPDIR:-/private/tmp}"; cd "$authority" && pwd -P'
)"
remote_akbs_root="${AKBS_REMOTE_MAC_ROOT:-/Users/jinny/Work/AKBS}"
remote_claim="$(
  ssh -o BatchMode=yes "$host" /usr/bin/python3 - create-private \
    --authority "$remote_authority" \
    --prefix android-engineering-ops-macos-verify. \
    --purpose android-engineering-ops-macos-validator \
    --allow-shared-authority \
    --akbs-root "$remote_akbs_root" <"$path_guard"
)"
IFS=$'\t' read -r remote_tmp remote_token <<<"$remote_claim"
[[ -n "$remote_tmp" && -n "$remote_token" ]] || {
  echo "remote validator path guard did not return an owned directory" >&2
  exit 78
}
cleanup_remote() {
  ssh -o BatchMode=yes "$host" /usr/bin/python3 - cleanup-private \
    --authority "$remote_authority" \
    --path "$remote_tmp" \
    --token "$remote_token" \
    --purpose android-engineering-ops-macos-validator \
    --allow-shared-authority \
    --akbs-root "$remote_akbs_root" <"$path_guard" >/dev/null 2>&1
}
cleanup_all() {
  local status="${1:-1}"
  trap - EXIT
  if ! cleanup_remote; then
    echo "remote validator owned-directory cleanup failed" >&2
    status=78
  fi
  validator_cleanup__exit "$status"
}
trap 'cleanup_all "$?"' EXIT

(
  cd "$repo_root"
  rsync -aR \
    .agents/plugins/marketplace.json \
    manifests/android-engineering-ops.toml \
    plugins/android-engineering-ops/.codex-plugin/plugin.json \
    plugins/android-engineering-ops/contracts/source-access \
    plugins/android-engineering-ops/adapters/source-access \
    plugins/android-engineering-ops/lib \
    plugins/android-engineering-ops/skills/android-source-access/scripts/android_source_access.py \
    plugins/android-engineering-ops/skills/android-change-workflow/scripts \
    plugins/android-engineering-ops/skills/android-remote-build-deploy \
    tests/plugins/android-engineering-ops \
    docs/skills/android-engineering-ops \
    "$host:$remote_tmp/"
)

ssh -o BatchMode=yes "$host" /bin/bash -s -- "$remote_tmp" <<'REMOTE'
set -euo pipefail
root="$1"

[ "$(uname -s)" = "Darwin" ] || { echo "ERROR: target is not macOS" >&2; exit 2; }
printf 'MACOS_VERSION=%s\n' "$(sw_vers -productVersion)"
printf 'MACOS_ARCH=%s\n' "$(uname -m)"
printf 'MACOS_BASH=%s\n' "$(/bin/bash --version | sed -n '1p')"

while IFS= read -r script; do
  /bin/bash -n "$script"
done <<EOF
$(find "$root/plugins/android-engineering-ops/adapters/source-access" "$root/plugins/android-engineering-ops/skills/android-change-workflow/scripts" "$root/plugins/android-engineering-ops/skills/android-remote-build-deploy" -type f -name '*.sh' -print | sort)
EOF

/usr/bin/python3 -m compileall -q "$root/plugins/android-engineering-ops"
/usr/bin/python3 -m compileall -q "$root/tests/plugins/android-engineering-ops"
cd "$root"
/usr/bin/python3 -m unittest discover \
  -s tests/plugins/android-engineering-ops/android-source-access \
  -p 'test_*.py' \
  -v
/usr/bin/python3 -m unittest discover \
  -s tests/plugins/android-engineering-ops/android-remote-build-deploy \
  -p 'test_*.py' \
  -v

detected_host="$(/usr/bin/python3 "$root/plugins/android-engineering-ops/skills/android-source-access/scripts/android_source_access.py" --expected-host macos detect --print-field host)"
[ "$detected_host" = macos ] || { echo "ERROR: source-access host detector returned $detected_host" >&2; exit 3; }

if /usr/bin/python3 "$root/plugins/android-engineering-ops/skills/android-source-access/scripts/android_source_access.py" --expected-host wsl list-commands >/dev/null 2>&1; then
  echo "ERROR: WSL source-access entry unexpectedly accepted macOS" >&2
  exit 3
fi

"$root/plugins/android-engineering-ops/adapters/source-access/macos/skills/android-source-access/scripts/detect-projects.sh" --help >/dev/null
# The public dispatcher above owns platform rejection. The internal adapter's
# read-only --list is not a mount and may legitimately succeed on macOS.

echo "MACOS_REMOTE_VALIDATION=PASS"
REMOTE
