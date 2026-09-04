#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
validator="${CODEX_HOME:-$HOME/.codex}/skills/.system/plugin-creator/scripts/validate_plugin.py"
source "$repo_root/scripts/validator_cleanup.sh"
validator_cleanup_install "$repo_root"

if [[ "$(uname -s)" == "Linux" ]]; then
  test_tmp="${AKBS_TEST_TMPDIR:-/tmp}"
  [[ -d "$test_tmp" ]] || { echo "AKBS test temp directory not found: $test_tmp" >&2; exit 1; }
  export TMPDIR="$test_tmp" TMP="$test_tmp" TEMP="$test_tmp"
fi

if [[ ! -f "$validator" ]]; then
  echo "Plugin validator not found: $validator" >&2
  exit 1
fi

for plugin in akbs-member-ops android-engineering-ops jinny-android-practices; do
  python3 "$validator" "$repo_root/plugins/$plugin"
done

"$repo_root/scripts/validate_skill_layout.sh"
python3 "$repo_root/scripts/validate_active_plugin_topology.py"

# Validate repository contracts together, then run each plugin test tree in a
# fresh interpreter to avoid cross-plugin Python import state.
python3 -m pytest --import-mode=importlib --capture=no "$repo_root"/tests/test_*.py
for plugin_tests in "$repo_root"/tests/plugins/*; do
  [[ -d "$plugin_tests" ]] || continue
  python3 -m pytest --import-mode=importlib --capture=no "$plugin_tests"
done

python3 "$repo_root/scripts/test_validator_cleanup.py"

python3 "$repo_root/scripts/validate_incoming_contract_gate.py" --mode client-only

echo "Plugin validation passed"
