---
name: android-change-workflow
description: "Use when implementing, diagnosing, modifying, or verifying Android source changes across application, platform, native, HAL, kernel, device, or build layers. Coordinates source authority, optional AKBS knowledge search, optional orchestration, policy, build-route selection, layer-aware verification, current v2 Android change capture, and submission."
---

# Android Change Workflow

Use this Skill as the end-to-end engineering workflow for Android source work.
Framework work belongs to the `platform` layer; Framework is not a product boundary.

## Task Startup

Before a new engineering task starts, set `PLUGIN_ROOT` to the directory two levels
above this `SKILL.md`, read `../../references/task-start.md`, and run its shared
`task_start.py` entry, which includes the first local install check. Reuse the same task ID/result across nested Skills;
do not update again at each build/remote command or while recovering running work.
This applies equally when this Skill is invoked directly without the full workflow.

## Gate 0: Active Install Family

Before reading project/source data, resolving an orchestration extension, editing,
running a local or remote command, using `adb`, writing state/artifacts, or delegating,
set `PLUGIN_ROOT` to the directory two levels above this `SKILL.md` and run:

```bash
python3 "$PLUGIN_ROOT/lib/android_engineering_ops/install_family.py" \
  --plugin-root "$PLUGIN_ROOT"
```

Only packaged documentation and a pure `--help` operation may be read first. A nonzero
result is a hard stop. This target-only receipt is mandatory for both
`registered_remote_tree` and `local_project`, and it also binds any direct local build
or `adb` action performed by this workflow. Re-run it if the active Codex plugin
inventory changes; a subagent result cannot replace this task's receipt.

## Optional Orchestration Extension

Run `scripts/resolve_android_orchestration.py --project-root <project>` immediately
after Gate 0. Project config `<project>/.codex/android-engineering.toml` takes
precedence over `$CODEX_HOME/android-engineering-ops.toml`. Missing config or
`mode="none"` returns `source=core`; continue this workflow directly without reading
or validating any optional plugin.

`mode="jinny"` selects `jinny-android-practices`. `mode="custom"` requires one
`plugin_name` implementing `android-orchestration-extension-v1`. The resolver uses the
active installed+enabled inventory and the fixed extension manifest path; it validates
only the explicitly selected plugin. A missing, ambiguous, incompatible, substituted,
or malformed selected extension is a hard stop before source work.

When the resolver returns `source=extension`, read its returned `skill_path` completely
and apply that orchestrator in this same user task. Record in working context that
resolution is complete. The orchestrator and nested Android Skills must not re-enter
this workflow or resolve the extension again. The extension decides only how the
current task is organized; Android source authority, policy, build/deploy rules,
capture, submission authority, integration, and the final user response remain here.

The resolver does not classify the task, select a model, spawn an agent, create worker
documents, or make a second acceptance decision. Legacy provider version/hash config
fields may be read and ignored during configuration migration, but they are not part
of the active extension protocol.

## Required Contracts

Before modifying source, read:

- `../../contracts/change-domain/v1/domain-profiles.json`
- `../../contracts/android-change-policy/v1/README.md`
- `../../contracts/android-change-policy/v1/policy.json`
- the current host's `android-source-access` Skill when source access or recovery is needed

For Framework work in the `platform` layer, also read
`references/framework-domain-workflow.md` and its linked references. Always read
`references/domain-routing.md` for the canonical component model.

## Remote-Only Source Contract

Classify each affected repository as `registered_remote_tree` or `local_project` before
reading or editing it. A path mounted or registered by `android-source-access` is always
`registered_remote_tree`: the mount is only a human CRUD surface and a confirmed
artifact bridge, never the Codex working tree. Codex must perform every source read/search/edit,
Git/repo or patch operation, checkpoint, and build for that tree through
`android-remote-channel`. Direct SSH is reserved for source-access infrastructure.

A real local Git project explicitly opened as the Codex workspace may use normal local
project tools; this commonly applies to standalone Gradle Apps and independently cloned
repositories. Never reclassify SMB/CIFS-mounted Android source as `local_project`. For a
mixed requirement, record source authority per repository. If authority or remote
project identity cannot be proven, stop before touching that repository.

## Gate 1: Requirement and Component

Write a concise requirement contract: requested behavior, current behavior, acceptance
criteria, target product/build, constraints, out-of-scope work, and rollback.

Select the canonical `component.layer` for every affected patch from `application`,
`platform`, `native`, `hal`, `kernel`, `device`, or `build`. Layer is explicit package
classification, not a filename guess. Legacy routes map as follows: `framework` to
`platform`; `system_app` and `app` to `application`; `driver` to `kernel`; the other
same-named routes remain in their layer. `vendor` is not a layer. A coherent change may
span layers, but every patch must be assigned to exactly one layer.

## Gate 2: Knowledge and Source Authority

When `akbs-member-ops` is installed and configured, run `akbs-knowledge-search` before
implementation and record `reuse`, `adapt`, `reference_only`, `not_applicable`, or
`not_found` with the evidence used. AKBS search is an optional integration: its absence
must not break source access, implementation, verification, or local capture. Record the
absence truthfully; a later submit flow may apply its own stricter server gate.

For a `registered_remote_tree`, use the host platform's `android-source-access` entry
when mount or registry preparation is needed. It routes to the single core
implementation and verifies WSL or macOS before side effects; then use
`android-remote-channel` for all source and build operations. For a `local_project`,
verify its real Git root and project instructions, then use normal local project tools.

## Gate 3: Policy and Change Plan

Apply the shared canonical Android change policy before edits. Universal member/patch
attribution applies to every patch-archived Android change; only a matching component
overlay applies. This policy is an internal contract and validator used automatically
by the workflow and capture; it is not a separate user-facing step.

Identify repositories and their source authority, modules, API/ABI boundaries,
generated files, build targets and build route, runtime/deployment mechanism,
regression surface, diagnostics, and rollback. Preserve unrelated user changes and use
the smallest coherent change.

## Gate 4: Implement and Verify by Component

Choose a build route from authoritative project files and instructions:

- `remote_profile`: use `android-remote-build-deploy` for a registered remote AOSP
  Soong/Make module build or an explicitly configured vendor full build. It owns exact
  artifact manifests and supported local adb delivery.
- `remote_project_command`: for a registered remote Gradle, Kbuild/kernel, external
  driver, Bazel, or other project build not supported by that Skill, run the project's
  documented build entry through `android-remote-channel`. Record the exact command,
  environment/profile, exit status, artifact identity, and delivery evidence.
- `local_project_command`: for a real local project, use its wrapper/build entry in the
  local workspace and record equivalent evidence.

Do not invent a generic build command or make `android-remote-build-deploy` claim
support for arbitrary Gradle/Kbuild pipelines. Build success and file transfer are
necessary evidence, not final acceptance. Deploy only through the selected component's
safe project/device mechanism and keep an explicit rollback.

Apply the selected layer's evidence and the relevant engineering risks:

- Framework: Binder/system_server, locks, Handler/Looper, boot, multi-user, resources,
  FrameworkLog, service restart or reboot.
- SystemApp: platform build/signing, privileged permissions, shared UID, privapp,
  SystemUI/Launcher/Settings integration, process or SystemUI restart.
- App: Gradle variant, manifest, unit/UI tests, APK/AAB, signing and install/upgrade.
- HAL: AIDL/HIDL interface/version, VINTF, service registration, SELinux, vendor/system
  boundary and device behavior.
- Native: Soong module, ABI/API, linker namespace, service lifecycle, native tests,
  tombstones and sanitizer evidence when relevant.
- Vendor/BSP: proprietary ownership, product integration, partition boundary,
  compatibility and rollback.
- Kernel: Kconfig/Makefile, subsystem behavior, image/module build, boot, dmesg and
  regression evidence.
- Driver: probe/bind, firmware, power/suspend, device I/O, module/image packaging and
  hardware verification.
- Device/board: product/device configuration, DTS/DTBO/overlays, partition or boot
  integration and board-specific rollback.
- Build: Soong/Make/Gradle/release integration, dependency graph, clean/incremental
  behavior, reproducibility and artifact contract.

The current user task runs final acceptance against the requirement contract and nearby
regressions; no extension or subagent, including a reviewer, owns the final state. Remove or
explicitly retain temporary diagnostics with a reason.

## Gate 5: Capture and Submission

Use `android-patch-capture --component-layer ...` and, for a multi-repository change,
`--repo-layer REPO_PATH=LAYER` to create one coherent package. Capture verifies
policy/evidence and directly writes a `knowledge-incoming-package/2/android_change`
directory. Every `files.patches` path appears exactly once in `components[].patches`.

Pass that same directory to `akbs-patch-submit read`, `check`, `prepare`, or `submit`.
There is one current incoming v2 package contract and one existing patch upload
lifecycle; there is no second Android-only package, conversion step, relabel fallback,
or second approval path. Historical
`framework_change` archives remain readable without rewriting their bytes, but current
tools never create or submit a new `framework_change` package.

## Hard Stops

Stop before claiming completion when:

- the requirement, component layer, source authority, build route, owner, acceptance, or
  rollback is unresolved;
- registered remote source work would bypass `android-remote-channel`;
- mandatory policy or member identity is missing;
- build, boot, device behavior, safety, or nearby regression verification failed;
- temporary diagnostics have no cleanup decision;
- a current package would omit a patch's layer or attempt to use `framework_change`.

## Final Report

Report the requirement/component, source authority per repository, root cause or design,
changed repositories/files, policy result, selected build route, exact
builds/tests/device evidence, risks/rollback, capture path/status, and whether submission
was performed or capability-gated. Never claim publication, install, server activation,
production deployment, or knowledge curation without its own evidence.
