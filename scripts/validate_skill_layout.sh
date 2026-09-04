#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$repo_root/scripts/validator_cleanup.sh"
validator_cleanup_install "$repo_root"

repo_search() {
  local pattern="$1"
  shift
  if command -v rg >/dev/null 2>&1; then
    rg -n "$pattern" "$@"
    return
  fi
  python3 - "$pattern" "$@" <<'PY'
import re
import sys
from pathlib import Path

pattern = re.compile(sys.argv[1])
found = False
for raw_root in sys.argv[2:]:
    root = Path(raw_root)
    paths = [root] if root.is_file() else root.rglob("*")
    for path in paths:
        if not path.is_file() or ".git" in path.parts:
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for number, line in enumerate(lines, 1):
            if pattern.search(line):
                print(f"{path}:{number}:{line}")
                found = True
raise SystemExit(0 if found else 1)
PY
}

validate_manifest_skill_layout() {
  local failed=0
  local manifest plugin skill actual missing extra

  for manifest in "$repo_root"/manifests/*.toml; do
    plugin="$(basename "$manifest" .toml)"
    if [[ ! -d "$repo_root/plugins/$plugin/skills" ]]; then
      echo "plugin $plugin has manifest but no skills directory" >&2
      failed=1
      continue
    fi

    while IFS= read -r skill; do
      [[ -n "$skill" ]] || continue
      if [[ ! -f "$repo_root/plugins/$plugin/skills/$skill/SKILL.md" ]]; then
        echo "skill \`$skill\` is missing SKILL.md" >&2
        failed=1
      fi
    done < <(awk -F'"' '/^name = "/ {print $2}' "$manifest")

    while IFS= read -r actual; do
      [[ -n "$actual" ]] || continue
      if ! awk -F'"' '/^name = "/ {print $2}' "$manifest" | grep -Fxq "$actual"; then
        echo "skill \`$actual\` exists in $plugin but is not listed in manifests/$plugin.toml" >&2
        failed=1
      fi
    done < <(find "$repo_root/plugins/$plugin/skills" -mindepth 1 -maxdepth 1 -type d -exec basename {} \; | sort)
  done

  if [[ "$failed" != "0" ]]; then
    exit 1
  fi
}

validate_manifest_skill_layout

failed=0
while IFS= read -r script; do
  IFS= read -r first_line < "$script" || true
  if [[ "$first_line" == '#!'* && ! -x "$script" ]]; then
    echo "runtime script with a shebang must be executable: $script" >&2
    failed=1
  fi
done < <(find "$repo_root/plugins" -path '*/skills/*/scripts/*' -type f | sort)
if [[ "$failed" != "0" ]]; then
  exit 1
fi

for target_plugin in akbs-member-ops android-engineering-ops; do
  target_package="${target_plugin//-/_}"
  target_rules="$repo_root/plugins/$target_plugin/lib/$target_package/knowledge_rules.py"
  if grep -Eq '^[A-Z0-9_]*PLUGIN_VERSION[[:space:]]*=' "$target_rules"; then
    echo "$target_rules must resolve the enclosing plugin manifest instead of embedding a release version" >&2
    exit 1
  fi
  PYTHONPATH="$repo_root/plugins/$target_plugin/lib${PYTHONPATH:+:$PYTHONPATH}" \
    python3 - "$repo_root/plugins/$target_plugin/.codex-plugin/plugin.json" "$target_package" <<'PY'
import importlib
import json
import sys

module = importlib.import_module(f"{sys.argv[2]}.knowledge_rules")
with open(sys.argv[1], encoding="utf-8") as handle:
    manifest_version = str(json.load(handle)["version"])
observed_version = module.current_plugin_version()
if observed_version != manifest_version:
    raise SystemExit(
        "target knowledge rules could not resolve the enclosing plugin manifest: "
        f"manifest={manifest_version} observed={observed_version or 'missing'}"
    )
PY
done

for target_plugin in akbs-member-ops android-engineering-ops jinny-android-practices; do
  if find "$repo_root/plugins/$target_plugin" -type l | grep -q .; then
    echo "$target_plugin must be a standalone install and cannot contain symlinks" >&2
    exit 1
  fi
done

for required_path in \
  "$repo_root/plugins/android-engineering-ops/skills/android-source-access/SKILL.md" \
  "$repo_root/plugins/android-engineering-ops/skills/android-source-access/scripts/android_source_access.py" \
  "$repo_root/plugins/android-engineering-ops/lib/android_engineering_ops/source_access/host.py" \
  "$repo_root/plugins/android-engineering-ops/lib/android_engineering_ops/source_access/dispatch.py" \
  "$repo_root/plugins/android-engineering-ops/adapters/source-access/wsl" \
  "$repo_root/plugins/android-engineering-ops/adapters/source-access/macos"; do
  if [[ ! -e "$required_path" ]]; then
    echo "android-engineering-ops standalone source-access path is missing: $required_path" >&2
    exit 1
  fi
done

old_skill_names="$VALIDATOR_CLEANUP_TMPDIR/old-skill-names.txt"
if repo_search "android-wsl-source-access|android-wsl-remote-build-deploy|android-wsl-remote-channel|android-macos-source-access|android-macos-ops" "$repo_root/plugins" "$repo_root/docs" "$repo_root/manifests" >"$old_skill_names"; then
  cat "$old_skill_names" >&2
  echo "old platform skill/plugin names must not appear in current plugin sources" >&2
  exit 1
fi

validate_skill_metadata() {
  local failed=0
  local skill_file skill_dir skill_name agent_file

  while IFS= read -r -d '' skill_file; do
    skill_dir="${skill_file%/SKILL.md}"
    skill_name="$(basename "$skill_dir")"
    agent_file="$skill_dir/agents/openai.yaml"

    if [[ ! -f "$agent_file" ]]; then
      echo "$skill_name is missing agents/openai.yaml" >&2
      failed=1
      continue
    fi

    if ! grep -Fq "\$$skill_name" "$agent_file"; then
      echo "$agent_file default_prompt should reference \$$skill_name" >&2
      failed=1
    fi
  done < <(find "$repo_root/plugins" -path '*/skills/*/SKILL.md' -print0)

  if [[ "$failed" != "0" ]]; then
    exit 1
  fi
}

validate_skill_metadata

validate_runtime_skill_cleanliness() {
  local failed=0
  local skill_file skill_dir rel top_level_name path

  while IFS= read -r -d '' skill_file; do
    skill_dir="${skill_file%/SKILL.md}"
    for top_level_name in README.md tests fixtures output reports pending submitted logs; do
      if [[ -e "$skill_dir/$top_level_name" ]]; then
        echo "runtime skill directory must not contain $top_level_name: $skill_dir/$top_level_name" >&2
        failed=1
      fi
    done

    while IFS= read -r -d '' path; do
      echo "runtime skill directory contains cache/build output: $path" >&2
      failed=1
    done < <(
      find "$skill_dir" \
        \( -path '*/__pycache__' -o -path '*/.pytest_cache' -o -name '*.pyc' -o -name '*.pyo' -o -name '*.log' -o -name '*.zip' -o -name '*.tar.gz' \) \
        -print0
    )

    while IFS= read -r -d '' path; do
      rel="${path#"$skill_dir"/}"
      case "$rel" in
        SKILL.md|references/*.md)
          ;;
        *)
          echo "runtime skill markdown must be SKILL.md or references/*.md: $path" >&2
          failed=1
          ;;
      esac
    done < <(find "$skill_dir" -type f -name '*.md' -print0)
  done < <(find "$repo_root/plugins" -path '*/skills/*/SKILL.md' -print0)

  if [[ "$failed" != "0" ]]; then
    exit 1
  fi
}

validate_runtime_skill_cleanliness

validate_guarded_output_entrypoints() {
  local failed=0
  local entry file marker
  local -a entries=(
    "plugins/android-engineering-ops/skills/android-patch-capture/scripts/capture_android_patch.py|require_safe_artifact_path"
    "plugins/android-engineering-ops/skills/android-patch-capture/scripts/capture_remote_snapshot.py|require_safe_artifact_path"
    "plugins/android-engineering-ops/skills/android-change-workflow/scripts/collect_diagnostics.sh|--owned-create"
    "plugins/android-engineering-ops/skills/android-change-workflow/scripts/extract_video_frames.py|require_safe_artifact_path"
    "plugins/android-engineering-ops/skills/android-remote-build-deploy/scripts/push_artifacts.py|require_safe_artifact_path"
    "plugins/android-engineering-ops/skills/android-remote-build-deploy/scripts/remote-build-v2.py|require_safe_artifact_path"
  )
  for entry in "${entries[@]}"; do
    file="${entry%%|*}"
    marker="${entry#*|}"
    if ! grep -Fq -- "$marker" "$repo_root/$file"; then
      echo "explicit output entrypoint does not call the path guard: $file" >&2
      failed=1
    fi
  done
  if [[ "$failed" != "0" ]]; then
    exit 1
  fi
}

validate_guarded_output_entrypoints

echo "Skill layout validation passed"
