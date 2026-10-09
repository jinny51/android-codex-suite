#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$repo_root/scripts/validator_cleanup.sh"
validator_cleanup_install "$repo_root"

if [[ "$(uname -s)" == "Linux" ]]; then
  test_tmp="${AKBS_TEST_TMPDIR:-/tmp}"
  [[ -d "$test_tmp" ]] || { echo "AKBS test temp directory not found: $test_tmp" >&2; exit 1; }
  export TMPDIR="$test_tmp" TMP="$test_tmp" TEMP="$test_tmp"
fi

# Check the suite's declared metadata and topology with versioned repository code.
# This is local/private suite validation, not the public dashboard submission gate.
python3 "$repo_root/scripts/validate_active_plugin_topology.py"
"$repo_root/scripts/validate_skill_layout.sh"

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
