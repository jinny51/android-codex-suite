---
name: akbs-patch-submit
description: "Use when an AKBS member needs to prepare, validate, submit, or complete information for an Android change package. Uses the single knowledge-incoming-package v2 contract across all seven Android layers; excludes source implementation, patch capture, and administrator curation."
---

# AKBS Patch Submit

This Skill turns one completed `android-patch-capture` directory into the final
AKBS package, validates it, and submits it through the ordinary patch queue.

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

`components` is only a patch-to-layer map. Every `files.patches` path appears
exactly once. Allowed layers are `application`, `platform`, `native`, `hal`,
`kernel`, `device`, and `build`. The package keeps the stable v2 directory,
evidence, validation, upload, queue, information-completion, and curation lifecycle.

Historical `framework_change` packages remain readable on the server. This Skill
must not create or submit a new `framework_change` package. Android package v2 is
retired and must not be used as a fallback.

## Startup and update check

Before preparing or submitting, the packaged intake entry runs the shared
`preflight-install-family` check and continues only with `status=PASS` for the installed plugin family and current
marketplace version. If an update is installed, follow
the returned instruction to restart Codex before continuing. A local
`--validate` is read-only and does not fetch or update.

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

The capture already carries every patch's layer. For a truthful manual or historical
single-patch import, declare the layer explicitly:

```bash
python3 "scripts/akbs_patch_submit.py" --profile <member_alias> --prepare \
  --patch /path/to/change.patch \
  --component-layer native \
  --workflow-contract manual_import \
  --project TVE8402M --platform rk --android-version 14 \
  --summary "既有 native 补丁导入" --status validated
```

## Validate and submit

```bash
python3 "scripts/akbs_patch_submit.py" --profile <member_alias> \
  --validate /path/to/pending/package

python3 "scripts/akbs_patch_submit.py" --profile <member_alias> --submit-latest
```

Submission preserves the generated package bytes. Retry the same package after an
uncertain transport failure; do not rename it or change its identity.

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
- Administrator curation decides new case or merge after upload.
