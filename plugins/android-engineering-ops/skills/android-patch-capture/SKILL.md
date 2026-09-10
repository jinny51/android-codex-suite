---
name: android-patch-capture
description: "Use after one coherent Android change in an application, platform, native, HAL, kernel, device, or build component is implemented and fully verified. Directly writes the final validated Android change v2 package for member check, prepare, and submission."
---

# Android Patch Capture

Use this Skill after `android-change-workflow` has implemented and verified one
coherent Android change. It directly creates the final
`akbs-android-change-package-v2/2/android_change` directory containing one change
README, repository-level patches, and evidence. There is no intermediate capture
format or conversion command.

The output may cover any supported layer: `application`, `platform`, `native`,
`hal`, `kernel`, `device`, or `build`. It is a validated package only. Failed,
blocked, draft, or partially verified work remains engineering/report evidence and
does not become an upload package.

V1 and v2 use the common patch upload lifecycle after packaging. A validated canonical component
is never relabelled as Framework, and v2 must never fall back to v1. Existing genuine
Framework v1 packages keep their historical route and bytes.

## Task Startup

Before a new engineering task starts, set `PLUGIN_ROOT` to the directory two levels
above this `SKILL.md`, read `../../references/task-start.md`, and run its shared
`task_start.py` entry. Reuse the same task ID and result across nested Skills; do not
update again for every remote or build command.

## Active Install Family

Before reading a snapshot, patch, identity/config, or evidence, and before writing
an artifact, run:

```bash
python3 "$PLUGIN_ROOT/lib/android_engineering_ops/install_family.py" \
  --plugin-root "$PLUGIN_ROOT"
```

Only packaged documentation and pure `--help` may precede this check. A nonzero result
is a hard stop. The command must return the inventory-bound target-only receipt;
mixed legacy/target installation is not a fallback.

## Component Boundary

For one component, provide `--component-layer`, `--component-type`,
`--component-partition`, and `--component-ownership`. For a cross-layer change, use:

```text
--component ID:LAYER:TYPE:PARTITION:OWNERSHIP
--primary-component-id ID
--repo-component REPO_PATH=COMPONENT_ID[,COMPONENT_ID]
```

Every repository must have an explicit component mapping. The packager never guesses
bindings from paths. `layer`, `type`, `partition`, and `ownership` are orthogonal;
`vendor` is not a layer. A legacy `--change-domain` value may supply known layer/type
hints only, and ambiguous `vendor` requires all facets explicitly.

Evidence membership is exact. In a multi-component change, bind shared evidence with
repeated `--evidence-component EVIDENCE_ID:COMPONENT_ID`. External evidence must name
`component_ids`, except for an unambiguous single-component package. Evidence for one
component cannot stand in for another.

## Remote-Only Source Contract

For Codex-authored work, source and Git facts are authoritative only on the registered
remote project. Use `android-remote-channel` to create an immutable remote snapshot;
do not inspect or package a mounted `.git` or `.repo` tree and do not use direct SSH.
A mounted source tree is a human CRUD surface and confirmed artifact bridge only; it
is never the Codex source or Git execution path.

`capture_remote_snapshot.py --package` is the preferred one-step path. It creates the
snapshot under the exclusive remote workspace lock, transfers and verifies it, and
immediately calls the local packager with the exact workspace, command, root, hash,
and freshness binding. This avoids an operator delay while retaining the freshness
check. The old split snapshot handoff remains a compatibility surface with the same
bounded-age guard.

`capture_android_patch.py` rejects `--source-root` and caller-supplied patch files for
`current_codex_skill`. A truthful `manual_import` or `historical_import` may consume
an explicit immutable binary patch with `--patch-artifact` and
`--patch-repo-path`.

## Current Workflow

Repeat `--repo-path` for one coherent change spanning repositories, and put packager
arguments after `--`:

```bash
python3 "scripts/capture_remote_snapshot.py" \
  --ssh-host "$SSH_HOST" \
  --remote-root "$REMOTE_ROOT" \
  --repo-path frameworks/base \
  --repo-path packages/apps/Settings \
  --command-id "$PATCH_SNAPSHOT_COMMAND_ID" \
  --package -- \
  --profile <profile_name> \
  --platform rk14 \
  --component platform-core:platform:framework:system:aosp \
  --component settings-ui:application:system_app:system_ext:product \
  --primary-component-id platform-core \
  --repo-component frameworks/base=platform-core \
  --repo-component packages/apps/Settings=settings-ui \
  --evidence-component verification-result:platform-core \
  --evidence-component verification-result:settings-ui \
  --evidence-component rollback-plan:platform-core \
  --evidence-component rollback-plan:settings-ui \
  --evidence-component search-before-change:platform-core \
  --evidence-component search-before-change:settings-ui \
  --change-id display-policy-settings-entry \
  --summary "调整显示策略和设置入口" \
  --problem-summary "显示策略缺少目标产品要求的配置入口和运行时行为" \
  --solution-summary "调整 Framework 显示策略并补齐 Settings 配置入口" \
  --implementation-origin codex \
  --workflow-contract current_codex_skill \
  --project TVE8402M \
  --verification "相关模块构建通过" \
  --device rk3576 \
  --device-verification "目标行为和相邻回归验证通过" \
  --search-query "显示策略 设置入口" \
  --search-result "未发现可直接复用案例" \
  --reuse-decision not_found \
  --rollback "恢复本次提交并重新构建相关模块"
```

The final package is atomically published below:

```text
$CODEX_HOME/artifacts/android-patch-capture/packages/<run-id>/
├── manifest.json
├── README.md
├── patches/
│   └── rk14-<module>@<change-id>.patch
└── evidence/
    ├── changed-files.json
    ├── patch-diff-facts.json
    ├── patch-problem-summary.json
    ├── risk-surface.json
    ├── coding-standard-check.json
    ├── verification-result.json
    ├── rollback-plan.json
    ├── search-before-change.json
    └── package-check.json
```

On any schema, evidence, hash, freshness, or policy error, publication fails before
the final directory appears. A failed run never leaves a half package.

The whole output directory can be passed directly to member operations:

```bash
python3 "<akbs-patch-submit>/scripts/akbs_patch_submit.py" \
  android-change-v2 check \
  "$CODEX_HOME/artifacts/android-patch-capture/packages/<run-id>"
```

Then use `prepare` or `submit` on that same directory. Do not copy individual patch
files into a handwritten manifest.

## Packaging Rules

- One package represents one functional goal. Split unrelated changes even when they
  were completed on the same day; multiple repositories may stay together only when
  they serve the same goal.
- Inspect every repository status through the remote channel and preserve unrelated
  user work. Recapture from a clean worktree when a diff is contaminated.
- Derive factual `--problem-summary` and `--solution-summary` from the request, diff,
  and verification evidence. Do not hand-edit generated evidence.
- Mode-only changes are filtered. If all changes are mode-only, no package is made.
- Project is a normalized `TVD`/`TVE`/`TVA`/`TVI` model. Conflicting project clues
  are a hard stop for a final validated package.
- `--platform` accepts transient versioned input only as `mtk<version>`,
  `rk<version>`, or `unisoc<version>`. It writes canonical `mtk`, `rk`, or `unisoc`
  to `subject.target.platform` and the decimal version separately. Aliases such as
  `sprd13` and `u13` are rejected.
- Patch filenames retain the versioned prefix:
  `<platform><android-version>-<module>@<change-id>.patch`.
- Every payload file is a regular file listed by a manifest descriptor with exact
  SHA-256 and size. Symlinks and extra files are rejected.
- Every component needs at least one bound patch and one bound evidence item, and
  every source must be used by a patch.
- Build transfer evidence alone does not prove the requirement. A final package needs
  device verification or an explicit equivalent-verification record with remaining
  risk.
- Search evidence records what happened before the change. It is not a curation
  decision and may not be fabricated.

## Policy and Hard Stops

Apply `android-change-policy/v1` during development. For Codex-authored current work,
the selected member profile supplies the required paired author/date markers. Do not
retrofit identity after implementation.

Stop without publishing when the package is not fully verified, a generated patch is
empty, the change is an aggregate bundle, unrelated files are present, component or
source mappings are incomplete, the member identity/policy check fails, direct banned
Framework logs are added, verification is missing or failed, or recorded search hits
lack an explicit reuse decision.

For work that cannot meet final-package requirements, preserve honest diagnostics in
the engineering task or personal report. Do not weaken package checks or invent a
status to upload it.

## Boundaries

- `android-change-workflow` owns requirements, implementation, build/deploy, and final
  verification.
- `android-patch-capture` owns final v2 package generation and atomic local publish.
- `akbs-patch-submit` owns read/check/byte-preserving prepare and the common patch
  upload lifecycle.
- `akbs-knowledge-search` is optional pre-change reuse input.

Read `references/package-contract.md` before changing the output format or integrating
another consumer.
