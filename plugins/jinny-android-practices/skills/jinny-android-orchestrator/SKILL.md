---
name: jinny-android-orchestrator
description: "Use when jinny-android-practices is explicitly selected for an Android engineering task, or when the user explicitly invokes Jinny Android orchestration. Keep simple work in the current task and create real bounded subagents only when independent investigation, implementation, verification, or review will materially improve a complex Android task."
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

## Decide whether delegation helps

Work directly in the current task when the next safe action is clear and one reasoning
thread can implement and verify it efficiently. Typical direct tasks include:

- changing one known setting or resource and running its focused check;
- fixing a localized null check, permission declaration, or build rule;
- explaining a known log failure without modifying code;
- capturing an already completed and verified change.

Use real subagents only when the work contains genuinely separable uncertainty or
review value. Typical complex tasks include:

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

## Define bounded roles

Create only the roles the task needs. Each prompt must state the objective, allowed
scope, known context, constraints, exact deliverable, and acceptance evidence.

- `investigator`: read-only evidence collection and root-cause analysis;
- `implementer`: one explicitly bounded write scope;
- `verifier`: independent focused tests and regression evidence, normally read-only;
- `reviewer`: independent design/risk review, read-only.

Use one writer per file or subsystem. Never ask two agents to edit overlapping paths.
Start independent read-only investigation in parallel; serialize implementation before
verification when verification depends on the change. Return ordinary concise messages
and filesystem changes—there is no worker receipt or controller approval document.

When the environment supports an explicit model override and the user has not chosen a
model, these are Jinny defaults rather than protocol requirements:

- investigator: `gpt-5.6-luna`, high reasoning;
- implementer: `gpt-5.6-terra`, high reasoning;
- verifier: `gpt-5.6-luna`, high reasoning;
- reviewer: `gpt-6-astra`, low reasoning.

The user's explicit model or reasoning choice wins. If a suggested model is unavailable,
use the current/inherited model; do not run an automatic fallback chain or silently
cycle models.

## Integrate once

Inspect every returned claim against the actual source and evidence. Resolve conflicts
in the current task, preserve unrelated user changes, and run only missing checks. Do
not rerun an already valid full build solely because a subagent ran it. Keep Android
source authority, build/deploy safety, patch policy, and submission authority in the
corresponding Android Engineering Ops Skills.

Finish only when the user's acceptance criteria are met or a genuine blocker is stated
in plain language. The final response comes from the current task and summarizes the
integrated result, tests, remaining risk, and any action that was intentionally not
authorized.
