---
name: jinny-android-orchestrator
description: "Use when jinny-android-practices is explicitly selected for an Android engineering task, or when the user explicitly invokes Jinny Android orchestration. Keep simple work in the current task and coordinate real bounded subagents for complex Android investigation, implementation, verification, technical research, or independent review."
---

# Jinny Android Orchestrator

Apply this Skill inside the current user task. The current task owns the user goal,
architecture, integration decisions, authorization boundary, and final response. Do not
create a permanent control task, a worker-state file, an assignment/result JSON
protocol, or a second acceptance workflow.

When this Skill was loaded through `android-change-workflow`, treat extension
resolution as complete for the task. Do not invoke `android-change-workflow` again and
do not recursively resolve the extension. Call only the lower-level Android Engineering
Ops Skills needed for source access, policy, remote work, build, verification, capture,
or submission.

When the user invoked this Skill directly before the core workflow, load
`android-change-workflow` once, run its Gate 0, record that Jinny was explicitly
selected, and then continue its remaining engineering gates without running the
extension resolver. This is still one workflow in the current task, not a handoff or a
second controller.

## Root task ownership

The current task remains the orchestrator. It owns:

- the user's actual outcome and authorization boundary;
- requirement clarification, architecture, and decomposition;
- deciding which work is independent and which must be serialized;
- assigning one writer to each file or subsystem;
- resolving conflicting findings and integrating the final change;
- final verification and the response to the user.

Subagents return bounded evidence or implementation. They do not change the overall
architecture, broaden scope, accept the final result, contact another persistent task,
or create a second control plane.

## Delegation gate

Work directly in the current task when the next safe action is clear and one reasoning
thread can implement and verify it efficiently. Typical direct tasks include:

- changing one known setting or resource and running its focused check;
- fixing a localized null check, permission declaration, or build rule;
- explaining a known log failure without modifying code;
- capturing an already completed and verified change.

Classify the work as delegated only when at least one real subtask is both bounded and
materially improved by separate context, parallel evidence collection, specialized
execution, or independent review. Typical delegated tasks include:

- an intermittent boot failure requiring independent log/source investigation before
  one bounded implementation;
- a change spanning framework API behavior and a SystemUI consumer where repository
  ownership can be separated cleanly;
- a high-risk Binder, SELinux, HAL, kernel, or build-system change that benefits from
  an independent verifier or reviewer;
- several plausible root causes that can be investigated in parallel without editing
  the same files.

Multiple files, a long prompt, or an available model is not by itself a reason to
delegate. Do not delegate a tiny edit, split sequential work into artificial roles, or
create an agent merely to repeat checks the current task already ran.

If the user explicitly requests subagents or delegation, use at least one real
subagent unless the requested operation is impossible in the current environment. Do
not describe or simulate delegation. If a task does not pass this gate, proceed
directly without creating any agent.

## Select only the needed roles

Before spawning a role, read its reference completely and include its role contract in
the delegation message together with the task-specific assignment:

- [investigator](references/investigator.md): repository mapping, logs, execution flow,
  competing root causes, and read-only diagnosis;
- [implementer](references/implementer.md): one explicitly bounded write scope after
  the direction and acceptance criteria are known;
- [verifier](references/verifier.md): reproduction, focused tests, device/build
  evidence, and nearby regression checks;
- [researcher](references/researcher.md): current or version-specific Android, API,
  dependency, and compatibility facts from authoritative sources;
- [reviewer](references/reviewer.md): independent post-change correctness, security,
  concurrency, compatibility, and missing-test review.

Do not create every role mechanically. Investigation can be performed by the current
task when the code path is already known. Verification remains required, but it need
not be delegated unless separate context adds value. Review is reserved for material
risk or a user request, not every patch.

## Build each delegation contract

Every spawned task must receive one concrete contract containing:

1. objective: one outcome, not a broad theme;
2. scope: exact repository, module, files, symbols, or question when known;
3. context: only the facts and prior evidence needed for that assignment;
4. constraints: protected behavior, forbidden paths, authority, and ownership limits;
5. deliverable: evidence, files changed, or review findings;
6. acceptance: the check that proves the delegated work is complete.

Use a descriptive task name tied to the assignment rather than a generic role name.
For example, `trace_systemui_wake_lock` is useful; `investigator_1` is not. Keep the
returned task identifier so the current task can wait for and account for it.

The role reference is reusable behavior, not a substitute for the six task-specific
fields. Do not send a vague message such as "inspect the bug" or "fix the framework."

## Models belong to Jinny, not the core

Never change or restart the current task's model. The model and reasoning level chosen
for the root task remain exactly as selected by the user or current task.

When the user has not specified a subagent model, Jinny recommends:

- investigator, implementer, verifier, and researcher: `gpt-5.6-luna`, high reasoning;
- reviewer: `gpt-6-astra`, low reasoning.

An explicit user choice for a role or all subagents wins. If a recommended model is
unavailable, do not probe a sequence of alternatives or rotate models. Use the
current/inherited model only when it preserves the user's request, and disclose the
substitution in the final summary when it matters.

## Coordinate real work

Use one writer per file or subsystem. Never ask two agents to edit overlapping paths.
Start independent read-only investigation in parallel; serialize implementation before
verification when verification depends on the change. Spawn independent roles before
waiting for any of them; do not create a serial chain when no dependency exists.

A normal complex implementation usually has this shape, with unneeded roles omitted:

1. investigate the unknown code paths or external facts;
2. current task chooses the direction;
3. assign bounded, non-overlapping implementation;
4. verify the original behavior and the changed behavior;
5. independently review only when risk warrants it;
6. current task resolves findings, integrates, and performs final acceptance.

Subagents return concise messages and actual filesystem changes. There is no worker
receipt, assignment/result JSON, stage snapshot, controller approval document, or
hidden polling loop.

If a subagent fails, inspect the actual failure. Retry only when a narrower equivalent
assignment is clearly justified; do not keep respawning agents or broaden scope. The
current task may finish the bounded work directly when safe, but must not claim the
failed delegation succeeded. A failure requiring a different architecture,
authorization, dependency, or destructive action returns to the user as a real
decision or blocker.

## Integrate once

Inspect every returned claim against the actual source and evidence. Resolve conflicts
in the current task, preserve unrelated user changes, and run only missing checks. Do
not rerun an already valid full build solely because a subagent ran it. Keep Android
source authority, build/deploy safety, patch policy, and submission authority in the
corresponding Android Engineering Ops Skills.

Before finishing a delegated task, confirm that every required subagent completed or
explicitly failed, no required agent is still running, material findings were resolved,
and the highest-value verification is bound to the integrated change. A reviewer
reports findings; it does not become another acceptance gate.

Finish only when the user's acceptance criteria are met or a genuine blocker is stated
in plain language. The final response comes from the current task and summarizes the
integrated result, tests, remaining risk, and any action that was intentionally not
authorized.
