---
name: android-patch-capture
description: "Use after one coherent Android change is implemented and verified to capture its README, repository patches, evidence, and seven-layer classification for AKBS v1 submission. Supports application, platform, native, HAL, kernel, device, and build; excludes upload and curation."
---

# Android Patch Capture

Use this Skill after `android-change-workflow` has completed and verified one
coherent change. It creates an engineering capture directory; `akbs-patch-submit`
then turns that directory into the single final
`knowledge-incoming-package/2/android_change` package.

## Task startup

Before a new engineering task starts, set `PLUGIN_ROOT` to the directory two levels
above this `SKILL.md`, read `../../references/task-start.md`, and run its shared
`task_start.py` entry. Reuse that result across nested engineering Skills.

Before reading source facts or writing an artifact, run:

```bash
python3 "$PLUGIN_ROOT/lib/android_engineering_ops/install_family.py" \
  --plugin-root "$PLUGIN_ROOT"
```

Continue only when the target install family passes; a nonzero result is a hard stop.
This is the target-only install receipt. If the task-start check installs
an update, restart Codex before continuing.

## Layer classification

Choose the layer from the actual changed source, not the feature name:

- `application`: ordinary apps and SystemUI/Settings-style system apps
- `platform`: Java Framework, system services, framework resources
- `native`: native platform services and libraries such as SurfaceFlinger
- `hal`: HIDL/AIDL HAL interfaces and implementations
- `kernel`: kernel and drivers
- `device`: device/product configuration and overlays
- `build`: Soong, Make, release, and build tooling

`vendor` is not a layer. Classify vendor-owned code by what it implements.

For a single-layer change, pass `--component-layer`. For one functional change that
spans layers, give a default and override only the affected repositories:

```text
--component-layer platform
--repo-layer frameworks/base=platform
--repo-layer packages/apps/Settings=application
```

Every generated patch is assigned exactly once. The capture writes only
`{"layer", "patches"}` component rows; it does not require component IDs, type,
partition, ownership, qualifiers, or evidence bindings.

## Remote-Only Source Contract

For current Codex-authored work, obtain Git status, binary diffs, branch, HEAD, repo
paths, and changed-file facts from an immutable snapshot created through
`android-remote-channel`. Mounted source is a human CRUD and artifact bridge, not
the Codex Git authority. Do not use direct SSH for capture.

Use the one-step snapshot and package flow:

```bash
python3 "scripts/capture_remote_snapshot.py" \
  --ssh-host "$SSH_HOST" \
  --remote-root "$REMOTE_ROOT" \
  --repo-path frameworks/base \
  --repo-path packages/apps/Settings \
  --command-id "$PATCH_SNAPSHOT_COMMAND_ID" \
  --package -- \
  --profile <member_alias> \
  --platform rk14 \
  --component-layer platform \
  --repo-layer packages/apps/Settings=application \
  --feature display-policy-settings-entry \
  --summary "调整显示策略和设置入口" \
  --problem-summary "当前产品缺少对应策略和设置入口" \
  --solution-summary "修改平台策略并补齐设置入口" \
  --implementation-origin codex \
  --workflow-contract current_codex_skill \
  --project TVE8402M \
  --verification "相关模块构建通过" \
  --device rk3576 \
  --device-verification "目标行为及相邻功能验证通过" \
  --search-query "显示策略 设置入口" \
  --search-result "未发现可直接复用案例" \
  --reuse-decision not_found \
  --rollback "恢复本次修改并重新构建相关模块"
```

A truthful existing-code import may use `--patch-artifact` and
`--patch-repo-path` under `manual_import` or `historical_import`. Each artifact
must be generated from one actual Git repository and paired with that repository's
path. Repeat both arguments for a cross-repository change; Android source root `.`
is not a repository path and is rejected.

The capture is written below:

```text
$CODEX_HOME/artifacts/android-patch-capture/packages/<run-id>/
├── manifest.json
├── README.md
├── patches/
└── evidence/
```

Pass the whole directory to `akbs-patch-submit --patch-package`.

## Hard stops

Do not publish a capture when the change is not verified, a patch is empty, unrelated
changes are mixed in, the member marker policy fails, project/platform facts conflict,
a patch has no layer, current-workflow search evidence is fabricated or missing, or
the change combines unrelated functional goals.

One functional goal may legitimately span several repositories and layers, so it stays
in one package. Its patch files are still generated separately per Git repository;
split packages by functional goal, not merely by repository or layer.

## Boundaries

- `android-change-workflow` owns implementation and final verification.
- This Skill owns source capture, engineering evidence, and layer assignment.
- `akbs-patch-submit` owns final v1 package construction, local validation, and upload.
- The server owns final acceptance and queue identity.
