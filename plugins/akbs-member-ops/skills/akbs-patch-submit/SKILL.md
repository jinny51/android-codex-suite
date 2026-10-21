---
name: akbs-patch-submit
description: "Use to prepare, validate, submit, or complete information for an Android change package using the single incoming v2 contract across all seven Android layers. Reuses local preparation for an administrator's own Patch, with authenticated submission and curation owned by the available management Skill; excludes source implementation and capture."
---

# AKBS Patch Submit

This Skill turns one completed `android-patch-capture` directory into the final
AKBS package and validates it. Ordinary members submit through the member route;
an administrator's own submission continues through the existing management Skill.

There is one current package contract:

```json
{
  "schema": "knowledge-incoming-package",
  "schema_version": "2",
  "package_kind": "android_change",
  "files": {
    "patches": ["patches/example.patch"],
    "evidence": ["materials/evidence/verification_result.json"]
  },
  "components": [
    {"layer": "platform", "patches": ["patches/example.patch"]}
  ]
}
```

Before preparing or submitting, confirm that every patch changes the target Android
product source. An independent App source diff must not be submitted as Patch, even
when its APK is included in a product image or its layer would be `application`.
Product-source App changes and product-side APK integration changes remain eligible.
Apply this boundary to capture packages and direct/manual patch imports alike;
unknown ownership needs source clarification, not a guessed layer.

`components` is only a patch-to-layer map for eligible product-source Patches. Every `files.patches` path appears
exactly once. Allowed layers are `application`, `platform`, `native`, `hal`,
`kernel`, `device`, and `build`. The package keeps the stable v2 directory,
evidence, validation, upload, queue, information-completion, and curation lifecycle.

Historical `knowledge-incoming-package/1/framework_change` packages remain readable
on the server. This Skill must not create or submit a new `framework_change` package.
The retired Android-only experimental schema must not be confused with or used as a
fallback for the current `knowledge-incoming-package/2`.

## Startup and update check

Before preparing or submitting, the packaged intake entry runs the shared
`preflight-install-family` check and continues only with `status=PASS` for the installed plugin family and current
marketplace version. If an update is installed, follow
the returned instruction to restart Codex before continuing. A local
`--validate` is read-only and does not fetch or update.

If only remote version transport is unavailable and the current installation
still validates, `--prepare` may finish locally with an explicit unknown-version
warning. Actual submission and information completion keep their existing
freshness requirements; invalid manifests, installation drift and stale caches
still stop. Daily and weekly reporting are unchanged.

## Prepare

Use the complete capture directory, not copied individual files:

```bash
python3 "scripts/akbs_patch_submit.py" --profile <member_alias> --prepare \
  --patch-package "$CODEX_HOME/artifacts/android-patch-capture/packages/<run-id>" \
  --project TVE8402M \
  --platform rk \
  --android-version 14 \
  --summary "功能补丁摘要" \
  --status validated
```

The capture already carries every patch's layer. Ordinary member submissions use
`current_codex_skill`. Manual/historical imports are local preparation for the
existing authenticated administrator submission route, not member upload options.
`historical_import` means production patches from before Codex was used;
`manual_import` preserves other genuinely pre-existing product patches. Neither
label can replace the current workflow for newly developed work. Codex packaging
an old artifact today does not make its implementation new. For an administrator's
truthful single-patch import, declare the layer explicitly. Direct `--patch` is an input
shortcut for one pre-existing, verified product-source Git patch, not a bypass of
source review or the capture route for current work. Do not relabel a patch
newly generated from this Codex task's local product Git edits as an import.
Before using the shortcut,
inspect the original patch's changed paths and confirm its actual product
repository and available Git revision in the engineering task; if any section
belongs to an independent App or the repository remains unknown, retain the
artifact locally. Missing remote snapshot alone does not disqualify a genuine
manual product patch. This prepares local pending material; it does not claim
the artifact came from a remote Git snapshot:

```bash
python3 "scripts/akbs_patch_submit.py" --profile <member_alias> --prepare \
  --patch /path/to/change.patch \
  --component-layer native \
  --workflow-contract manual_import \
  --project TVE8402M --platform rk --android-version 14 \
  --summary "既有 native 补丁导入" --status candidate
```

Direct-only input does not carry machine-verifiable acceptance evidence, so it
stays a local candidate even if the artifact was previously verified. For an
administrator submission, use the existing truthful manual/historical capture
route with real verification evidence, then prepare with `--patch-package`.
Do not invent PASS evidence or weaken the validated upload gate.

## Validate and submit

Local validation does not submit:

```bash
python3 "scripts/akbs_patch_submit.py" --profile <member_alias> \
  --validate /path/to/pending/package
```

### Submission authority

Choose authority before any upload, not from a profile alias, machine name, or the
mere presence of a management Skill:

- A previously confirmed `role=member` uses the existing member profile and the
  command below, even if a management Skill is installed. Without a management
  Skill, the ordinary member path also stays unchanged. Do not look for or require
  administrator credentials on these paths.
- For a confirmed administrator, or when the role is unknown but
  `akbs-curation-maintainer` is available, let that Skill make one read-only
  `author-identity` check in this same conversation. Availability enables this
  check, not permission. A confirmed administrator's request such as “submit my
  Patch” naturally selects this route; the user need not repeat “administrator”.
  Only the returned `admin` role and server-bound author establish the own-Patch
  route. The prepared package's author must match; do not rewrite attribution
  to make it match. Reuse this Skill's local prepare/validate, then let the
  management Skill submit that exact package and continue eligibility review, knowledge
  comparison, and the formal curation decision through the existing workflow.
  Upload success is not the stopping point. Preserve its returned
  `patch_package_id` and workflow on retry or recovery; do not open another chat,
  send the user to the UI, or submit again through ordinary `--submit-latest`.
- If an administrative task lacks the management Skill, or the selected identity
  check cannot confirm the administrator, authentication fails, or its bound author
  differs, keep the prepared material local and explain the specific blocker.
  Never silently fall back to member submission. An administrator handling another
  author's material follows the management Skill's
  existing other-author flow; this is not a self-submission shortcut.

Only making, checking, or staging material does not authorize production submission
or curation. An explicit upload-only request authorizes submission through the selected
role's entry, but not queue processing, review, or a knowledge write. An instruction
not to add knowledge must never be expanded into curation.

For an authorized ordinary-member submission of a `current_codex_skill` package:

```bash
python3 "scripts/akbs_patch_submit.py" --profile <member_alias> --submit-latest
```

Both `manual_import` and `historical_import` are refused by this member upload
route. Keep a genuine imported package unchanged for the administrator's existing
`submit-local-patch` route; do not relabel it to pass. Local prepare/validate does
not require administrator login and remains available for that preparation.

Submission preserves the generated package bytes. Retry the same package after an
uncertain transport failure with `--submit-latest --run-id <original-run-id>`;
do not rename it or change its identity. Without `--run-id`, the command keeps
its usual latest-package selection; an explicit missing or ambiguous run ID
never falls back to another package.
Do not relabel an independent App as product source, or a manual artifact as a
remote snapshot. If product-source ownership is uncertain, keep the package local
until the source can be clarified; do not infer eligibility from its layer.

Information completion stays on the server-assigned `patch_package_id`:

```bash
python3 "scripts/akbs_patch_submit.py" --profile <member_alias> \
  --inspect-information-request <request-id>

python3 "scripts/akbs_patch_submit.py" --profile <member_alias> \
  --complete-information-request /path/to/response.json
```

## Boundaries

- Use `android-change-workflow` for analysis, implementation, build, deploy, and final verification.
- Use `android-patch-capture` to capture the completed source change and evidence.
- Keep one functional goal per package; a goal may span multiple repositories or layers.
- Evidence files declare their own `kind`; the manifest only lists their paths.
- AKBS server validation is authoritative for identity, persistence, deduplication, and upload acceptance.
- Administrator curation decides new case or merge after upload. For the administrator's own submission, the management Skill continues that decision in the current task rather than waiting for a separate member-review step.
