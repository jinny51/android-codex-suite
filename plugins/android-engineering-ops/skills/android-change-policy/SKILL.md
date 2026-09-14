---
name: android-change-policy
description: "Use when implementing, modifying, reviewing, importing, or packaging Android changes that are archived as patches. Enforces member-bound attribution across all canonical component layers and applies the existing Framework logging/debug/resource overlay to Framework work in the platform layer."
---

# Android Change Policy

Use this skill as the public entrypoint to the core plugin's canonical engineering
change policy. It is a policy layer, not a second change workflow and not a patch
packager.

## Task Startup

Before a new engineering task starts, set `PLUGIN_ROOT` to the directory two levels
above this `SKILL.md`, read `../../references/task-start.md`, and run its shared
`task_start.py` entry, which includes the first local install check. Reuse the same task ID/result across nested Skills;
do not update again at each build/remote command or while recovering running work.
This applies equally when this Skill is invoked directly without the full workflow.

## Active Install Family

Before reading project/source data or applying this policy to a change, set
`PLUGIN_ROOT` to the directory two levels above this `SKILL.md` and run:

```bash
python3 "$PLUGIN_ROOT/lib/android_engineering_ops/install_family.py" \
  --plugin-root "$PLUGIN_ROOT"
```

Only packaged documentation and pure `--help` may precede this check. A nonzero result
is a hard stop; do not edit, review, import, or package under a missing, mixed, or
source-checkout install family. When this Skill is called inside
`android-change-workflow`, reuse its still-current target-only controller receipt.

## Required Contract

Before editing or judging Android source, read both files completely:

- `../../contracts/android-change-policy/v1/README.md`
- `../../contracts/android-change-policy/v1/policy.json`

The JSON contract is authoritative. Do not reproduce or reinterpret it in another
Skill, project note, prompt, or generated evidence file.

## Apply the Correct Layer

- Apply `universal_patch_archive` to every Android change that will be preserved as
  patches.
- Apply the `framework` overlay only to known Framework work in the `platform` layer,
  including the legacy `change_domain=framework` route. Do not infer the overlay from
  filenames.
- Apply `legacy_jinny_style` only when the user explicitly requests the old Jinny
  naming preference. It is advisory and cannot replace mandatory core rules.

## Identity and Markers

Select an existing member profile from `$CODEX_HOME/akbs-member-ops.toml` or a supported
legacy profile file. When the optional AKBS member plugin is installed, its setup Skill
may manage that profile; the engineering plugin only reads it. Resolve the identity
only from that profile's `member_alias`; never derive it from Git author,
an example name, an invented alias, or a ticket number.

For new Codex-authored code in files that support slash line comments, add the markers
while implementing the change:

```text
//<member_alias> <yyyyMMdd>@{
...
//<member_alias> <yyyyMMdd>@}
```

Use a pair even for one changed line. Opening and closing alias/date values must match,
the date must be a real calendar date, and each marker must occupy its own slash-comment
line (only indentation and trailing whitespace are allowed). In a current Codex diff,
every nonblank added line in an applicable file and hunk must be enclosed by a pair;
pairs cannot span files or hunks. A marker-looking string literal or inline comment is
ordinary added content, never attribution evidence. Do not insert `//` into XML, shell,
properties, make, or another syntax that does not support it. Until a versioned
comment adapter exists, record that file as not applicable to the slash-marker check;
do not break its syntax.

## Source Origin

- `codex`: every applicable changed file uses the current profile's paired marker.
- `mixed`: preserve historical markers and add at least one paired marker for the
  current member's Codex-authored part.
- `manual`, `external`, or `historical`: preserve original authorship. Legacy markers
  may be observed for compatibility; never rewrite them to the submitting member.

Do not invent missing identity or requirement metadata. A manual or historical local
draft with missing markers may remain `WARN`; it is not policy-compliant and cannot be
upgraded by wording alone.

## Workflow Boundary

Apply policy during implementation. `android-patch-capture` verifies the
same contract per changed file and writes versioned `coding-standard-check.json`
evidence. Raw patch intake runs the same canonical parser before creating an incoming
directory. Do not hand-edit generated evidence to turn a failure into a pass.
