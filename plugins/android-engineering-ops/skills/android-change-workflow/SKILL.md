---
name: android-change-workflow
description: "Use when implementing, diagnosing, modifying, or verifying Android source changes across application, platform, native, HAL, kernel, device, or build layers. Orchestrates canonical component facets and source authority, optional AKBS knowledge search, policy, an explicit optional practices provider, build-route selection, layer-aware verification, final v2 package capture, and submission."
---

# Android Change Workflow

Use this Skill as the end-to-end engineering workflow for Android source work.
Framework is a `platform/framework` component type, not a product boundary or layer.

## Task Startup

Before a new engineering task starts, set `PLUGIN_ROOT` to the directory two levels
above this `SKILL.md`, read `../../references/task-start.md`, and run its shared
`task_start.py` entry, which includes the first local install check. Reuse the same task ID/result across nested Skills;
do not update again at each build/remote command or while recovering running work.
This applies equally when this Skill is invoked directly without the full workflow.

## Gate 0: Active Install Family

Before reading project/source data, resolving a practices provider, editing, running a
local or remote command, using `adb`, writing state/artifacts, or delegating a worker,
set `PLUGIN_ROOT` to the directory two levels above this `SKILL.md` and run:

```bash
python3 "$PLUGIN_ROOT/lib/android_engineering_ops/install_family.py" \
  --plugin-root "$PLUGIN_ROOT"
```

Only packaged documentation and a pure `--help` operation may be read first. A nonzero
result is a hard stop. This target-only receipt is mandatory for both
`registered_remote_tree` and `local_project`, and it also binds any direct local build
or `adb` action performed by this workflow. Re-run it if the active Codex plugin
inventory changes; a worker result cannot replace the controller's receipt.

## Required Contracts

Before modifying source, read:

- `../../contracts/change-domain/v1/domain-profiles.json`
- `../android-change-policy/SKILL.md`
- the current host's `android-source-access` Skill when source access or recovery is needed

For `component.layer=platform` plus `component.type=framework`, also read
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

Select exactly one canonical `component.layer` from `application`, `platform`, `native`,
`hal`, `kernel`, `device`, or `build`, then record independent `type`, `partition`, and
`ownership` facets. Layer is the principal evidence surface, not a filename guess.
`Framework`, `SystemApp`, `App`, and `driver` are types or compatibility routes;
`vendor` is an ownership/partition facet and must never be used as a layer. When a
legacy `change_domain` provides known layer/type hints, record the compatibility route
and keep missing partition/ownership as `unknown` rather than inventing them;
ambiguous `vendor` requires explicit canonical fields. If any facet remains ambiguous
after source/build evidence, ask only for the missing product decision.

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

## Optional Practices Resolution

Before asking a provider for coding or execution policy, run
`scripts/resolve_android_practices.py`. The project config
`<project>/.codex/android-engineering.toml` takes precedence over
`$CODEX_HOME/android-engineering-ops.toml`; no config means `none`/core.

The mode field sets are closed: `none=[mode]`;
`jinny=[mode,provider_version,provider_manifest_sha256]`; and
`custom=[mode,plugin_name,provider_id,provider_version,provider_manifest_sha256]`.
The resolver uses only active installed+enabled entries from `codex plugin list --json`,
then reads the provider at the contract-fixed relative path. It never scans cache
versions or guesses from a description. A selected missing, ambiguous, symlinked,
unstable, schema-invalid, or hash-mismatched provider fails before delegation or source
write. A valid provider with an absent/non-applicable capability falls back to core.

Validate each decision with the schemas packaged in this plugin, the exact
provider/Skill bindings, the declared profile ceiling, and the controller's rollout
ceiling. A provider only returns a decision. It never spawns, writes, takes a lock,
executes a side effect, uploads, changes a Gate, or performs final acceptance.

A selected Jinny/custom provider is user-installed trusted Skill code, not an OS
sandbox. Validate active plugin identity, manifest, Skill, agent metadata,
decision-entrypoint hashes, closed output, and expected decision/run/stage/context
before use. These bindings prevent substitution and authority escalation; they do not
claim arbitrary custom provider code is process-level side-effect-free.

## Gate 3: Policy and Change Plan

Apply `android-change-policy` before edits. Universal member/patch attribution applies
to every patch-archived Android change; only a matching component overlay applies.

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

Apply the selected layer's evidence and the relevant type/facet risks:

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

The `android-change-workflow` controller runs final acceptance against the requirement
contract and nearby regressions; no provider or worker, including a review worker, owns
the final state. Remove or
explicitly retain temporary diagnostics with a reason.

## Gate 5: Capture and Submission

Use `android-patch-capture --component-layer ... --component-type ...
--component-partition ... --component-ownership ...` to create one coherent,
reviewable `android_change_capture`. Capture verifies policy/evidence and determines an
effective local status, but does not repair code after the fact.

`android-patch-capture` directly emits the final validated package. The same validated package from any supported layer
goes to `akbs-patch-submit android-change-v2 check`, `prepare`, or `submit`; there is
no conversion step or intermediate package state.

V1 and v2 are input formats on the common patch upload lifecycle. The member side
uses strict v2 local validation and byte-preserving prepare, then submits through the
same profile, patch endpoint, idempotent retry rules, and ordinary receipt used by v1.
Local rejection has zero side effects. Never relabel a non-Framework component or fall back to v1.
Existing legacy Framework
v1 packages remain eligible only through their permanent compatibility contract and
preserve their original bytes, package identity, provenance, and wire behavior.

## Hard Stops

Stop before claiming completion when:

- the requirement, component layer/facets, source authority, build route, owner, acceptance, or
  rollback is unresolved;
- registered remote source work would bypass `android-remote-channel`;
- mandatory policy or member identity is missing;
- build, boot, device behavior, safety, or nearby regression verification failed;
- temporary diagnostics have no cleanup decision;
- a v2 package would be relabelled or routed through Framework v1.

## Final Report

Report the requirement/component, source authority per repository, root cause or design,
changed repositories/files, policy result, selected build route, exact
builds/tests/device evidence, risks/rollback, capture path/status, and whether submission
was performed or capability-gated. Never claim publication, install, server activation,
production deployment, or knowledge curation without its own evidence.
