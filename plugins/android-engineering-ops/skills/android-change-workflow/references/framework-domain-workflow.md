# Framework Domain Workflow

Use this reference as the `component.layer=platform` plus
`component.type=framework` operating protocol. It defines requirement specification,
diagnosis, code-change discipline, risk judgment, final acceptance verification, and
final reporting for that component type.

## Composable Use Contract

When user-provided skills, project-local rules, or review workflows exist, preserve
them. Apply this reference only after the canonical component facts select
platform/framework.

## Remote-Only Source Contract

The mounted Android path has exactly two consumers:

- a human using an editor, IDE, Finder, or shell for source CRUD
- the local artifact bridge reading confirmed build outputs for local `adb` delivery

It is not a Codex source workspace. Codex must not use the mounted path as its
working directory and must not read, search, edit, scan, diff, patch, or run
`git`, `repo`, or builds through it. Every Codex operation that touches Android
source or source-tree metadata must execute against `REMOTE_ROOT` through the
stable `android-remote-channel` tmux session. Read-only operations use the
channel's non-exclusive mode; source writes, patch application, `git`/`repo`
writes, checkpoints, and builds require the exclusive project lock.

Direct SSH is not a source execution fallback. Outside `android-remote-channel`,
it is allowed only for source-access infrastructure: resolving SSH configuration
or reachability, installing a public key, reading or updating Samba
configuration, and reloading the Samba service. An infrastructure command must
not inspect or mutate `REMOTE_ROOT`.

Treat a missing or unhealthy remote channel as a hard stop. Do not fall back to
the mounted source tree, a one-off SSH source command, or local `apply_patch`.

If the user explicitly asks for a personal coding-style skill, project `AGENTS.md`, local engineering rule, or review skill, treat that instruction as part of the active requirement. Do not replace it with this workflow. Combine the user's rule with this workflow's Android Framework evidence and verification discipline.

Before Gate 3, read the canonical Android change policy at
`../../contracts/android-change-policy/v1/README.md` and its machine contract
`policy.json`. The universal patch-attribution rules are mandatory. Apply the
Framework profile only to Framework changes. Personal, project, or legacy Jinny style
preferences may add constraints, but they do not replace or redefine the canonical
identity, evidence, or Framework safety rules. Do not wait until patch capture to
retrofit the policy.

Use it for both direct requirement implementation and bug/regression work. Do not force requirement work into a root-cause narrative; use a requirement contract and acceptance evidence instead.

It coordinates with adjacent Android skills:

- `akbs-knowledge-search` searches prior cases, platform variants, archived patches, search anchors, and validation evidence before re-analysis or re-implementation. Use it as the pre-analysis knowledge gate when the team knowledge repository is available.
- `android-source-access` records the SSH/remote-root mapping and optionally maintains the human/artifact mount; it does not authorize Codex to operate on source through that mount.
- `android-remote-channel` is the mandatory source execution gateway for analysis, search, edits, `git`, `repo`, checkpoints, patch capture, and builds.
- `android-remote-build-deploy` uses `android-remote-channel` for remote build work and uses the mounted path only as a confirmed product-output artifact bridge for local `adb` delivery.
- `android-patch-capture` turns one implemented, failed, blocked, or stage-worthy Android change into a change README, repository-level patches, and evidence after this workflow has produced concrete material. Use it before `akbs-patch-submit` whenever such a change should be preserved, regardless of Android layer.
- `akbs-patch-submit` owns strict v2 local validation, byte-preserving prepare, and
  submission through the common patch upload lifecycle.
- This skill proves the framework change satisfies the requirement or diagnosis outcome on device.

## Core Contract

Follow this ownership boundary:

```text
akbs-knowledge-search
  -> search prior cases/variants/patches/search anchors/validation evidence before reimplementing

android-source-access
  -> access/recover/identify source tree handoff
  -> register SSH/REMOTE_ROOT identity -> optional human/artifact mount

android-remote-channel
  -> mandatory remote source reads/search/edits/git/repo/patch/build transport

android-change-workflow
  -> specify requirement or diagnose issue -> instrument if needed -> change -> define verification

android-remote-build-deploy
  -> build -> push/deploy -> return delivery evidence

android-change-workflow
  -> final acceptance verification -> recover/iterate or complete

android-patch-capture
  -> package one accepted or stage-worthy coherent change into a change README, repository-level patches, and evidence

akbs-patch-submit
  -> generate member-side incoming package with package status and evidence
```

Build/deploy evidence is necessary but not sufficient. Final completion must come from this skill because only the framework workflow knows the requirement contract or root cause, touched subsystem, risk matrix, and expected device behavior.

## Load References As Needed

Keep this file as the orchestrator. Load only the reference needed for the current task:

- `references/framework-risk-model.md`: read before changing system_server, WM/ATM, input, surfaces, Binder identity, locks, handlers, boot phases, users/profiles, resources, or shared framework APIs.
- `references/requirements-implementation.md`: read when the user asks for a new behavior, feature, policy change, product customization, or any direct requirement rather than a bug investigation.
- `references/diagnosis-and-instrumentation.md`: read when root cause is unclear, diagnostic logs are needed, visual evidence is needed, or temporary logs must be audited.
- `references/subsystem-playbooks.md`: read after identifying the likely owner subsystem.
- `references/verification-matrix.md`: read before build/deploy verification and again before final reporting.
- `references/failure-signatures.md`: read when build, boot, deploy, logcat, or behavior verification fails.
- `references/build-deploy-contract.md`: read when coordinating with `android-remote-build-deploy`.
- `references/capability-capture.md`: read near final reporting only when the task produced reusable process knowledge, exposed a skill gap, or the user asks to remember/summarize a lesson.

Use scripts in `scripts/` as optional helpers. Prefer them for log slicing, health scans, artifact probing, diagnostic log audits, dumpsys capture, and video frame extraction when the matching artifact exists.

## Start Triage

Before editing behavior:

0. Resolve `SSH_HOST` and `REMOTE_ROOT`, ensure the stable remote channel is healthy, and keep the mounted path out of the Codex workspace. Stop if the channel is unavailable.
1. Identify whether the request is a new requirement, behavior change, bug/regression, verification task, or failure recovery.
2. For Android Framework implementation work, use `akbs-knowledge-search` before source edits when the team knowledge repository is available. Search with feature words, subsystem, file/class names, properties, Settings keys, resource keys, artifact names, and visible log keywords. Treat matches as evidence, not final truth. Decide and record one pre-change knowledge use decision for `search-before-change.json`: `reuse` when the old knowledge directly applies, `adapt` when the case applies but platform/version/project/source details differ, `reference_only` when it only informs diagnosis or risk, `not_applicable` when it is explicitly excluded, or `not_found` when no usable knowledge was found. Record query terms, target case/variant/patch ids, match points, mismatch points, reason, and later outcome so `android-patch-capture` can preserve the decision.
3. For direct requirements, capture acceptance criteria, negative cases, product/device/variant scope, and expected owner subsystem. Load `references/requirements-implementation.md`.
4. For bugs or regressions, capture visible symptom, reproduction, expected behavior, and evidence source.
5. Identify likely owner process and subsystem: app, SystemUI, launcher, system_server, WM/ATM, PMS, input, resources/overlays, display/surface/compositor, native service, or build config.
6. Identify affected artifact: `framework.jar`, `services.jar`, `framework-res.apk`, `SystemUI.apk`, Launcher APK, permission/config XML, overlay APK, native binary, or mixed artifacts.
7. Check source/build/deploy readiness. Use `android-source-access` first if source access is broken and `android-remote-build-deploy` later for build/delivery.
8. Check dirty files before editing and preserve unrelated user work.
9. Choose mode: direct requirement, analysis only, diagnostics, behavior change, build/deploy coordination, final verification, or failure recovery.
10. Decide the expected knowledge outcome early: no code change, `draft`, `candidate`, `validated`, `failed`, or `blocked`. This is a working expectation, not a final claim.

Ask the user only when evidence cannot be obtained from source search, logs, dumpsys, recordings, screenshots, adb, or available local/remote tools.

## Hard Stops

Stop and resolve the blocker before continuing when:

- A behavior edit is being made without either a direct requirement contract or source/log/dumpsys/visual/reproduction evidence.
- A first diagnosis attempt failed and no stronger diagnostic plan exists.
- A direct requirement lacks acceptance criteria, scope, owner subsystem, or negative/regression boundaries.
- A broad shared-path edit lacks owner process, caller/callee, thread/lock, lifecycle, state, artifact, and downstream mutation understanding.
- A change fails build, deploy, boot, health scan, or target behavior verification.
- Temporary diagnostics were added but no remove/guard/downgrade/keep decision exists.
- The proposed change masks a framework requirement with an app-side or unrelated workaround.
- Completion would rely only on "compiled" or "pushed" rather than target behavior evidence.

## Gate 1: Diagnose Or Specify

Exit only when either the direct requirement is specified enough to implement or root cause is known. If neither is true, use targeted instrumentation or requirement clarification.

For direct requirements, minimum specification:

- Desired behavior and user-visible/system-visible outcome.
- Product, variant, display, user/profile, configuration, and feature-flag scope.
- Owner subsystem, process, artifact, and build profile.
- Acceptance criteria and negative cases.
- Nearby regression paths before changing code.

For bugs or regressions, minimum evidence:

- Restate the symptom precisely enough to verify later.
- Trace caller, callee, lifecycle owner, state transition, and downstream mutation.
- Check product, build variant, resource overlay, feature flag, device config, runtime state, permission, user/profile, and process boundary gates.
- Name the affected subsystem, process, artifact, and build profile.
- Identify nearby regression paths before changing code.

If diagnosis stalls, load `references/diagnosis-and-instrumentation.md` and instrument the uncertainty instead of guessing. If requirement scope is unclear and cannot be inferred safely, ask a concise question before editing.

## Gate 2: Instrument

Use instrumentation to answer named uncertainties, not to create noisy logs.

Temporary diagnostics must:

- Use stable tags or keywords.
- Include relevant IDs, state values, bounds, flags, caller/reason, user/profile/display/window/task IDs, process/thread context, and timing when useful.
- Avoid sensitive user data.
- Be easy to search and remove.

For visual or timing-sensitive behavior, capture recording or screenshots and align visible frames with logs before deciding the implementation path or root cause.

Before final completion, audit diagnostics through the remote channel with
`scripts/diagnostic_log_audit.py --ssh-host <host> --remote-root <root> --path
<changed-file>` and decide remove, guard, downgrade, or keep with reason. Repeat
`--path` for the bounded changed-file set; the helper never reads mounted source.

## Gate 3: Change

Enter only after the requirement is specified or the diagnosis has converged.

Before editing, have:

- Requirement contract or root-cause statement.
- Canonical `android-change-policy/v1`, plus any active personal/team/project rules that do not conflict with it.
- Planned files/modules and ownership boundary.
- Risk level and rollback/checkpoint approach.
- Build profile and expected artifacts.
- Verification matrix for target behavior and nearby regressions.

Change with framework discipline:

- Match local patterns and existing framework APIs.
- Preserve lock ordering, Binder identity, handler/thread affinity, lifecycle timing, transaction ordering, resource precedence, and multi-user/profile semantics.
- Keep edits scoped to the responsible subsystem.
- Avoid unrelated refactors, formatting churn, and unrelated dirty files.

## Gate 4: Build And Delivery

Use the core `android-remote-build-deploy` skill for remote build and local push mechanics on WSL or macOS.

Require it to return:

- Build target/profile and result.
- Produced artifact paths.
- Pushed/deployed artifact paths or partitions.
- Required restart/reboot/remount actions.
- Basic device health evidence after delivery.
- Any skipped or failed delivery step.

Do not treat delivery as final correctness. Continue to Gate 5.

## Gate 5: Final Verification

Load `references/verification-matrix.md` and verify according to touched subsystem.

Always verify:

- Target behavior on the device.
- Process or boot stability for affected components.
- Relevant nearby regression paths.
- Logcat health for system_server, SystemUI, Launcher, target app/process, ANR, watchdog, crash, or fatal exception signals.
- Diagnostic log lifecycle cleanup.

For UI/windowing/input/surface changes, use repeated mixed interactions and visual evidence when the behavior is transient, timing-sensitive, or frame-visible.

## Gate 6: Material Capture And Incoming

This workflow is not complete after code and verification when the work produced reviewable or cautionary Framework engineering material. Member-side Codex should preserve materials automatically first and rank by package status later. It must not produce curation decisions or claim that the material has entered the knowledge repository.

After Gate 5:

1. Confirm that the requirement is met, build/deploy passed, target behavior is
   verified, and no blocking nearby regression remains.
2. If the intended source changes are isolated, invoke `android-patch-capture` with
   the platform/version input, project, change ID, summary, implementation origin,
   component mappings, risk/rollback, verification, search evidence, and explicit
   `related_report_run_ids` when a daily/weekly run ID is known.
3. Capture directly emits the current incoming v2 `android_change` package. Invoke
   `akbs-patch-submit` on that same directory for local check, prepare, or submit.
   There is one package format and one patch upload lifecycle.
4. Failed, blocked, draft, or partially verified work is not an upload package. Keep
   its honest diagnostics in the engineering task or daily/weekly report.
5. If no package can be made, state exactly why. Do not claim capture or upload.
6. Do not upload unrelated diffs, credentials, sensitive logs, mixed task changes, or
   material without full verification.

The normal successful `validated` chain is:

```text
akbs-knowledge-search
  -> android-change-workflow
  -> android-remote-build-deploy
  -> android-change-workflow final verification
  -> android-patch-capture
  -> akbs-patch-submit incoming
```

## Failure Recovery

When build, deploy, boot, health, or behavior verification fails:

1. Stop stacking changes.
2. Capture the failure symptom, minimal log lines, artifact path, and whether it is new or pre-existing.
3. Load `references/failure-signatures.md`.
4. Compare the failure with the requirement/root cause and changed files.
5. Revert/narrow the last local edit, or return to requirement clarification, diagnosis, or instrumentation with new uncertainty points.
6. Report the failure and next action concisely.

## Final Report

Use Chinese field names for the user-visible final report. Report only high-signal facts and keep technical terms such as profile, modules, artifacts, adb, log, `.codex`, SSH, Samba, and registry in English when appropriate.

Suggested user-visible fields:

- 根因/需求: requirement implemented or root cause addressed.
- 证据（验证记录/验证结果）: source/log/dumpsys/recording/device evidence used to support the conclusion.
- 修改文件: files changed.
- 构建部署: build/deploy evidence from the platform build/deploy executor, including profile, modules, artifacts, push/deploy destination, restart/reboot/remount when relevant.
- 验证结果: final device verification performed by this workflow.
- 日志处理: diagnostics added, removed, guarded, downgraded, or kept.
- 材料上传: pre-change search result, capture package path, incoming package path, package status, or the reason capture/intake was not possible.
- 结论: failures, skipped checks, remaining risk, and whether the work is accepted.

If verification was not run, state exactly why and what remains unverified under `结论`.

Add a `Skill 改进建议` section only when `references/capability-capture.md` says the task meets the trigger threshold. Do not modify this skill automatically; propose persistence and wait for explicit user confirmation.

When a concrete Framework change exists, do not stop at saying it is ready for capture. Run or hand off `android-patch-capture` and then `akbs-patch-submit` unless the user explicitly requested analysis-only, the change set is unsafe to package, or required identity/config is unavailable. In those cases, report the concrete blocker under `材料上传`.
