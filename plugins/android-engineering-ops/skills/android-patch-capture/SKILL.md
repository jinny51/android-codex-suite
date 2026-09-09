---
name: android-patch-capture
description: "Use after one coherent Android change in an application, platform, native, HAL, kernel, device, or build component is implemented, staged, failed, or blocked and needs a reviewable local android_change_capture. Capture 2.1 records exact component evidence for any supported layer; network submission remains capability-gated."
---

# Android Patch Capture

Use this Skill when one coherent Android change is ready to be packaged as a reviewable or cautionary local engineering material package. A change may be a feature, bug fix, failed attempt, or blocked/stage-worthy implementation. This Skill does not implement requirements, diagnose root cause, build, deploy, decide final correctness, or make curation decisions. It packages existing source changes into one change README, one or more repository-level patches, and evidence.

Use it after `android-change-workflow` has produced a concrete change. The capture
taxonomy covers any supported layer across all seven Android engineering layers,
and capture/local validation is available for every one of them. A downstream
consumer may impose its own independently versioned acceptance policy without
changing this engineering contract. Writer-off gates network submission with
zero side effects and never causes fallback to Framework v1.

## Active Install Family

Before reading a snapshot, patch, package, identity/config, or evidence, and before any
state or artifact write, set `PLUGIN_ROOT` to the directory two levels above this
`SKILL.md` and run:

```bash
python3 "$PLUGIN_ROOT/lib/android_engineering_ops/install_family.py" \
  --plugin-root "$PLUGIN_ROOT"
```

Only packaged documentation and pure `--help` may precede this check. A nonzero result
is a hard stop. Canonical and compatibility capture entrypoints must execute from the
inventory-bound target cache and produce a target-only receipt; mixed legacy/target
installation is never a fallback.

## Component Boundary

Single-component compatibility input uses explicit `--component-layer`,
`--component-type`, `--component-partition`, and `--component-ownership`. A change
spanning layers uses repeated
`--component ID:LAYER:TYPE:PARTITION:OWNERSHIP`, one
`--primary-component-id`, and an exact
`--repo-component REPO_PATH=COMPONENT_ID[,COMPONENT_ID]` for every repository. The
manifest records `components[]`, `primary_component_id`, stable repository IDs, and
`component_ids[]` on every repository and patch. It never derives these bindings from
paths. A single component applies to every captured repository when no mapping is
needed.

Every new capture records orthogonal component objects. `layer` is one of
`application`, `platform`, `native`, `hal`, `kernel`, `device`, or `build`; `type`,
`partition`, and `ownership` are independent token facets. A legacy route may supply
only known hints, while `--component-layer`, `--component-type`,
`--component-partition`, and `--component-ownership` provide exact facts. Legacy
Framework material normalizes only for read display to `platform/framework`; its
history is never rewritten.

Evidence membership is exact. Generated facts bind only components their payload
actually proves. In a multi-component capture, `verification-result`,
`rollback-plan`, and `search-before-change` require explicit repeated
`--evidence-component EVIDENCE_ID:COMPONENT_ID` mappings; capture fails closed
when those mappings are absent. External evidence must declare `component_ids`;
omission is accepted only for an unambiguous single-component capture. Evidence
from one component cannot qualify another. Conditional producer facts may use a
neutral `component_assertion` JSON input whose assertions each name one
`component_id`, one producer-owned `assertion_id`, and `PASS`, `FAIL`, `INFO`, or
`NOT_APPLICABLE`. The envelope result is always `INFO`; nested `PASS`, `FAIL`, and
`INFO` require non-empty `observations`, while N/A requires non-empty `basis` and
`limits`. Capture validates the closed shape, unique component/assertion pairs,
and exact component union. It does not embed or interpret AKBS group mappings.

## Remote-Only Source Contract

For Codex-authored work, Android source and source-tree metadata are authoritative
only on `REMOTE_ROOT`. Patch capture must obtain `git status`, staged and
unstaged binary diffs, branch, HEAD, remotes, repo paths, and changed-file facts
from an immutable snapshot created through the stable `android-remote-channel`
tmux session. Source-mutating preparation uses the exclusive project lock.

The mounted Android path is only for human source CRUD and as an artifact bridge
for confirmed build outputs used by local `adb`. Codex must not run this capture script against a
mounted source root, inspect a mounted `.git`/`.repo`, read source evidence from
mounted `.codex` files, or write a capture package below the mounted project.
Local capture processing may read only a channel-produced snapshot, explicit
non-source evidence, or a human-supplied immutable patch artifact. Packages and
evidence belong under a safe `$CODEX_HOME/artifacts` location.

Direct SSH is not permitted for capture. All remote source and Git operations go
through `android-remote-channel`; a missing snapshot/channel is a hard stop.

`capture_remote_snapshot.py` is the only current-workflow source entry. It embeds
the deterministic snapshot generator in one protocol-v2 command, runs it with
the exclusive workspace lock, transfers the resulting read-only JSON only after
the channel command completes, and verifies its workspace id, command id,
canonical remote root, age, closed schema, blob hashes, and snapshot SHA-256.
`capture_android_patch.py` rejects `--source-root` and caller patch files for
`current_codex_skill`. `manual_import` and `historical_import` may instead
consume an explicit immutable Git binary patch with `--patch-artifact` and its
matching `--patch-repo-path`.

The generated package includes patch content `sha1` for server-side deduplication. If a known daily/weekly incoming run produced the work context, pass `--related-report-run-id <run_id>` so intake can preserve an explicit report link. Weekly links are provenance only; weekly packages are not knowledge repository materialization candidates.

Search evidence is development evidence, not a curation decision. AKBS search is an
optional integration for the standalone engineering plugin. If a real search happened,
record its exact decision and evidence; never fabricate one. Its absence is a local
capture warning, not a capture status promotion/demotion rule. A later
`akbs-patch-submit` may independently reject or downgrade under its active server
contract.

`--implementation-origin` records who wrote the code. `--workflow-contract` records how the patch entered AKBS. They are independent: a manually written change can still be processed under `current_codex_skill`, while a truthful already-implemented import uses `manual_import` or `historical_import`. Import workflows may preserve missing pre-change search without earning search-loop credit. Never relabel either field to bypass a gate.

`--project` is a high-priority hint only when it contains a company project anchor in the current recognition scope (`TVD`, `TVE`, `TVA`, or `TVI`). If `--project` is omitted or contains a generic label such as `mtk android16 Camera2`, capture must continue looking at remote snapshot metadata, repository paths, git branches/remotes, the platform-neutral source-access registry, and change README/diff/summary text before falling back to `unknown`.

The structured project field must contain only the normalized company model. Any text outside that model is evidence text, not part of `project`: branch suffixes, customer suffixes, build branches, business labels, module labels, Chinese descriptions, and other non-standard trailing text stay in `project_inference.raw_inputs` or `basis`. Examples: `TVE1067M1_H031` -> `TVE1067M1`, `TVE1086U_MAIN_HANGYAN` -> `TVE1086U`, `TVE1091U福建移动高清` -> `TVE1091U`. Do not truncate `TVE1067M1` to `TVE1067M`; those are different projects.

Project inference must be conservative. If `--project`, remote snapshot metadata, repository paths, git branches/remotes, the platform-neutral source-access registry, change summary, or diff text expose multiple different TVD/TVE/TVA/TVI project models, the package must write `project=unknown`, preserve all candidates in `project_inference.candidates`, and record the conflict in `project_inference.limits`; `akbs-patch-submit` keeps it out of `validated` status until the member-side Codex resolves the ambiguity and regenerates one complete patch package.

Patch capture uses the plugin rules module (`android_engineering_ops.knowledge_rules`) before writing a package. The same project normalization, platform/Android version parsing, aggregate package detection, pre-change knowledge search classification, search usage decision closure checks, and patch asset pollution basics are reused by `akbs-patch-submit` through the shared intake kernel during upload preparation. These checks are deterministic gates only; admin-side local curation still owns new knowledge, merge, archive, reject, and knowledge validity decisions. Server upload entrypoints must not load this module.

## Boundary

- `android-change-workflow`: owns requirement analysis, source changes, risk,
  build/deploy coordination, Gate state, and final verification.
- `android-patch-capture`: owns the canonical component-labelled change README,
  repository patches, local evidence, and effective capture status.
- `akbs-patch-submit`: owns v2 local validation/byte-preserving prepare and any
  capability-gated network submission.
- `akbs-knowledge-search`: optional pre-change reuse integration.

Compatibility only: existing Framework v1 packages and retired public Skill IDs retain
their frozen read/submission contracts; they are not the canonical component model.

## Current Workflow

First create and transfer one immutable snapshot. Repeat `--repo-path` when one
change spans multiple repo-managed Git repositories:

```bash
python3 "scripts/capture_remote_snapshot.py" \
  --ssh-host "$SSH_HOST" \
  --remote-root "$REMOTE_ROOT" \
  --repo-path frameworks/base \
  --repo-path packages/apps/Settings \
  --command-id "$PATCH_SNAPSHOT_COMMAND_ID"
```

The command returns JSON containing `snapshot`, `snapshot_sha256`,
`workspace_id`, `command_id`, and `remote_root`. Pass those exact values to the
local packager:

```bash
python3 "scripts/capture_android_patch.py" \
  --profile <profile_name> \
  --remote-snapshot "$SNAPSHOT" \
  --snapshot-sha256 "$SNAPSHOT_SHA256" \
  --snapshot-workspace-id "$WORKSPACE_ID" \
  --snapshot-command-id "$COMMAND_ID" \
  --remote-source-root "$REMOTE_ROOT" \
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
  --solution-summary "调整 Framework 显示策略并补齐 Settings 配置入口，再验证配置生效" \
  --implementation-origin codex \
  --workflow-contract current_codex_skill \
  --project "TVE8402M" \
  --status candidate \
  --verification "framework 编译通过" \
  --device rk3576 \
  --device-verification "设备验证通过" \
  --search-query "电源键 用户态 控制" \
  --search-result "命中电源键传递案例但项目和 Android 版本不同" \
  --reuse-decision adapt \
  --reuse-target case-power-key-to-app \
  --reuse-match "同类按键策略需求" \
  --reuse-mismatch "旧变体是 rk12，当前项目是 rk14" \
  --reuse-reason "复用案例思路，按当前项目源码适配" \
  --reuse-outcome adapted_success \
  --build-result /path/to/build-result.json
```

All new packages are written below the single root
`$CODEX_HOME/artifacts/android-patch-capture/packages` and include the
validated snapshot as evidence:

```text
$CODEX_HOME/artifacts/android-patch-capture/packages/<run-id>/
├── manifest.json
├── README.md
├── patches/
│   ├── rk14-frameworks-base@display-policy-settings-entry.patch
│   └── rk14-settings@display-policy-settings-entry.patch
└── evidence/
    ├── changed-files.json
    ├── patch-diff-facts.json
    ├── patch-problem-summary.json
    ├── risk-surface.json
    ├── coding-standard-check.json
    ├── remote-source-snapshot.json
    ├── build-result.json
    ├── verification-result.json
    ├── search-before-change.json
    └── package-check.json
```

Legacy packages already under
`$CODEX_HOME/artifacts/android-framework-patch-capture/packages` remain read-only.
Inspect one without copying, rewriting, renaming, or changing permissions:

```bash
python3 "scripts/read_legacy_capture.py" \
  --package "$CODEX_HOME/artifacts/android-framework-patch-capture/packages/<run-id>"
```

The normalized output is evidence only. It cannot create a new package, promote a
status, allocate a server ID, or imply that the v2 server writer is enabled.

For a truthful existing-code import, do not manufacture a channel identity.
Consume the human-supplied immutable patch explicitly:

```bash
python3 "scripts/capture_android_patch.py" \
  --workflow-contract manual_import \
  --implementation-origin manual \
  --patch-artifact /path/to/frameworks-base.patch \
  --patch-repo-path frameworks/base \
  --platform rk14 \
  --component-layer platform \
  --component-type framework \
  --component-partition system \
  --component-ownership aosp \
  --change-id existing-display-policy-fix \
  --summary "既有功能补丁导入"
```

To prepare later with canonical `akbs-patch-submit`, pass the whole capture package
directory to `android-change-v2 adapt-capture`. Capture 2.0 remains read-only and
returns the original Phase-2 BLOCKED preflight. Capture 2.1 materializes a new,
hash-bound canonical package below the AKBS member artifact root without changing
the capture. All seven layers require a member adapter and server with the matching
qualification contract; capture success alone is not adaptation or upload success.
If adaptation is blocked, preserve the capture and report that stage and its exact
error. Writer-off still rejects network submit. Never fall back to v1 or
relabel v2 material as a v1 Framework package:

```bash
python3 "<akbs-patch-submit>/scripts/akbs_patch_submit.py" \
  android-change-v2 adapt-capture \
  "$CODEX_HOME/artifacts/android-patch-capture/packages/<run-id>"
```

## Packaging Rules

Project recognition priority is:

1. Explicit `--project` containing a `TVE`/`TVA`/`TVI` project model.
2. Source context from the remote-channel snapshot: remote root, repo path, git branch, git remote, and platform-neutral source-access registry entries.
3. Change package text: summary, change ID, README, repository paths, and diffs.

Do not write generic labels such as `android16`, `Camera2`, or `mtk android16 Camera2` as the package project. Preserve them only as checked inference inputs.

Before packaging, inspect `git status --short` for every source repository through the remote channel and preserve unrelated user work. Package only the intended coherent change set. If unrelated files are dirty, stop and ask whether to split, stash, or include them.

One capture package represents one coherent change. If the change spans multiple repo-managed Git repositories, pass every affected remote repository with repeated `--repo-path` to `capture_remote_snapshot.py`; the package will contain one root `README.md` and one patch per affected repository. The skill, not the member, must write the function-boundary explanation into the generated README: change target, module scope, key anchors, and how each repository-level patch serves the same target. If those facts are missing or the relationship cannot be explained from source changes, summary, and evidence, stop and ask the member for the missing factual input before generating or uploading the package.

Before generating a package for intake, Codex must derive the actual requirement problem and implemented solution from the current request, diff, and verification evidence, then pass both `--problem-summary` and `--solution-summary`. The two arguments are a pair. They are not member-authored JSON overrides: the capture script validates them and writes the generated `patch-problem-summary.json`. Module-based inference remains a compatibility fallback for local draft or candidate material, but a generic low-confidence fallback is not sufficient reason to hand-edit generated JSON or stop permanently. Rerun the same capture command with the factual pair instead.

Do not use one capture package for a date-bundled patch set such as “今日补丁合集” or “今天完成 6 个补丁”, or for several unrelated changes listed in one title. The script must stop before writing any patch package when the summary or change ID is a no-common-target 聚合包（aggregate package）. A member may implement code manually, but material submitted to AKBS must still be wrapped by this skill as one coherent change package. Independent changes must be split into separate packages; only repository patches that serve the same change target may stay together.

If one change summary is clear but the diff includes many unrelated resource keys, settings keys, system properties, or other anchors, treat it as patch asset contamination rather than a valid package. Stop and ask the member to recapture the same change from a clean worktree. If an uploaded package needs patch changes, intake must reject it and the member must submit a newly generated complete patch package; never hand-edit the uploaded package.

For a correction, create a fresh change capture from a clean source worktree containing only the intended diff. Use `akbs-patch-submit android-change-v2 adapt-capture <this capture package dir>` to create the new canonical patch package, then submit that returned package. Do not prepare a correction from copied old patch files or handwritten claims.

Pure file mode diffs such as `old mode 100755` / `new mode 100644` are usually checkout or chmod noise, not a meaningful source change. Patch capture filters diff sections that contain only mode changes. If every changed file is mode-only, stop with no package. If a repository has both real content changes and mode-only noise, keep the content diff and drop the mode-only sections. A chmod change may be preserved only when it is part of an intentional executable-script or tool behavior change and is accompanied by content, summary, risk, and verification evidence.

Patch filename must follow:

```text
平台Android版本-模块名@变更标识.patch
```

`--platform` 必须使用当前受控平台令牌：`mtk<Android版本>`、`rk<Android版本>` 或 `unisoc<Android版本>`。历史别名 `sprd<Android版本>` 和 `u<Android版本>` 会规范化为 `unisoc<Android版本>`。不要使用泛化或非规范令牌；它们只能说明可能的 Android 版本或模块线索，不能证明平台（platform）。生成的补丁资产（patch asset）文件名前缀必须是合法项目名（project）或受控平台 Android 版本前缀，例如 `TVE1067M1-`、`mtk15-`、`rk14-` 或 `unisoc16-`；其他非受控前缀不能上传，必须从正确项目和平台工作树重新采集。

Examples:

```text
rk14-frameworks-base@allow-powerkey-to-user.patch
mtk14-systemui@statusbar-icon-policy.patch
unisoc13-framework-res@default-navigation-mode.patch
```

The generated change README should contain facts first. Do not overclaim reuse, platform compatibility, or validation. Store facts that future AI can judge:

- requirement summary
- modified files
- touched artifacts
- SystemProperties
- Settings keys
- resource/string keys
- FrameworkLog keys
- build and device verification evidence
- pre-change knowledge search decision: `reuse`, `adapt`, `reference_only`, `not_applicable`, `not_found`, or `unknown`
- remote build server, remote source path, artifact path, artifact SHA1, local transfer, local adb serial, and device delivery action when the member workflow spans a remote build server and a local device
- risk and rollback notes

Current capture never reads `.codex` evidence through a mounted source root.
Pass the build/delivery receipt explicitly with `--build-result`, or provide the
structured `--remote-build-*`, `--artifact-transfer`, `--local-artifact`, and
`--adb-*` facts. Build delivery remains `scope=build_delivery` and
`requirement_acceptance=unverified`; it cannot by itself satisfy `validated`.
The formal delivery receipt is preserved inside a separate `build-delivery` auxiliary
evidence entry, never merged into `verification-result`. For a multi-component
capture, bind it explicitly with `--evidence-component build-delivery:COMPONENT_ID`;
do not edit the original receipt to add capture metadata.

`validated`, `candidate`, `draft`, `failed`, `blocked`, and platform labels are useful
hints, not final truth. Capture can confirm the declared status or downgrade it; it
never upgrades `draft`/`candidate`, and a declared `validated` package with failed local
qualification is written as effective `candidate`. The manifest always records both
declared and effective status plus `status_was_upgraded=false`. Future
`android-change-workflow` or knowledge-search skills should dynamically judge
applicability from the stored facts.

Status meaning:

- `validated`: verified for the original project scope.
- `candidate`: coherent change with partial or incomplete verification.
- `draft`: unfinished but useful Android work.
- `failed`: failed implementation or verification path worth preserving.
- `blocked`: blocked work evidence worth preserving.

## Hard Stops

Stop before upload when:

- any generated patch is empty
- every changed file is only a file mode change such as `old mode 100755` / `new mode 100644`
- the summary or change ID describes a date-bundled patch set such as “今日补丁合集” instead of one coherent target
- the change set includes unrelated dirty files
- one change package contains many resource keys, settings keys, or system properties unrelated to the stated change; recapture a clean patch asset instead
- a new Codex-authored slash-comment source file lacks a matching pair of `//<member_alias> <yyyyMMdd>@{` and `//<member_alias> <yyyyMMdd>@}` markers from the selected member profile
- new added lines contain direct `Log.*` or `Slog.*`
- README facts are unknown but presented as verified
- build or device verification is missing but status is `validated`
- a recorded AKBS search hit lacks its explicit reuse/adapt/reference/not-applicable decision
- status is `validated`, knowledge search returned hits, and search usage decision is still `unknown`

The canonical `android-change-policy/v1` must be applied during development. This skill verifies it per changed file and records versioned evidence; it is not the place to retrofit coding style after the fact. `--allow-missing-author-date` is restricted to a manual or historical local `draft`, produces `WARN`, and never makes the material policy-compliant.

Record the implementation origin exactly as observed. Use `manual`, `external`, `historical`, or `unknown` only when Codex did not author any part of the implementation; use `mixed` when Codex participated alongside another author. Record the workflow contract separately. The package records both as curation input material; neither turns the change into curated knowledge, and neither may be rewritten to fabricate or bypass pre-change knowledge search.

`candidate`, `draft`, `failed`, and `blocked` captures stay local or in report context.
Every new manifest explicitly records `v2_writer=disabled` and no upload authority.
Any validated canonical component may be handed to `akbs-patch-submit`, which must
perform v2 local checks and independently verify actual server capability before any
network action. It must never disguise another component as Framework v1.

For `validated`, device verification is required by default. Use `--verification-method equivalent` only when device interaction is not the right proof, and provide `--equivalent-type`, `--equivalent-reason`, at least one `--equivalent-coverage`, and `--remaining-risk`.

## References

Read `references/package-contract.md` before changing the script output format or integrating with other skills.
