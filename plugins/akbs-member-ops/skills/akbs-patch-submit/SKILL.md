---
name: akbs-patch-submit
description: "Use when an AKBS member needs to read, check, prepare, or submit an Android change package. Supports legacy Framework v1 and final Android change v2 packages across all seven component layers; excludes source implementation, capture generation, and administrator curation."
---

# AKBS Patch Submit

Use this member-facing Skill for the final package and upload part of an Android
change. It has two input formats but one patch-upload lifecycle:

- legacy `knowledge-incoming-package/1/framework_change` remains readable,
  checkable, preparable, and submittable through incoming v1;
- `akbs-android-change-package-v2/2/android_change` is the final v2 package for
  `application`, `platform`, `native`, `hal`, `kernel`, `device`, and `build`.

V2 is a package format, not a second business workflow. It uses the same selected
member profile, patch upload endpoint, idempotent retry behavior, and ordinary server
receipt as v1. There is no capture adapter, intermediate materialization state,
client-side approval result, pilot grant, writer switch, or cross-format fallback.
也就是说，最终 v2 包与 v1 包走同一补丁上传生命周期，区别仅在输入结构。

## Active Install Family

Before a business action, run:

```bash
python3 "../akbs-member-setup/scripts/akbs_member_setup.py" preflight-install-family
```

Continue only on exit 0 with JSON `status=PASS`. A true parser `--help` request may
bypass this gate; a literal `--help` after `--` is business input and may not.

## V2 Contract

`android-patch-capture` directly writes the final v2 directory. This Skill only
reads, checks, prepares, or submits that directory; do not recreate or reinterpret it.

The checker validates the bundled JSON Schema plus the facts that the schema cannot
express alone:

- the exact regular-file inventory, with no symlinks or unlisted payloads;
- SHA-256 and byte size for the README, each patch, and each evidence file;
- unique component, source, patch, and evidence IDs and valid cross-references;
- at least one patch and one evidence item for every declared component;
- use of every declared source by a patch;
- canonical target platform `mtk`, `rk`, or `unisoc`, with Android version stored
  separately as a decimal string.

Inputs such as `mtk16`, `rk14`, and `unisoc13` are convenient capture CLI syntax only.
They become `platform=mtk|rk|unisoc` plus `android_version=16|14|13` in the manifest.
Formal packages containing `mtk16` or another combined platform value are rejected.

A local PASS means only that the package is internally complete and hash-consistent.
The server remains authoritative for authentication, authorization, persistence,
deduplication, and the upload result.

## Android Change V2 Commands

Read identity, target, and component layers without preparing or uploading:

```bash
python3 "scripts/akbs_patch_submit.py" android-change-v2 read /path/to/package
```

Check the complete final package locally:

```bash
python3 "scripts/akbs_patch_submit.py" android-change-v2 check /path/to/package
```

After a successful check, copy the exact package bytes under
`$CODEX_HOME/artifacts/akbs-member-ops/android-change-v2/pending/`:

```bash
python3 "scripts/akbs_patch_submit.py" android-change-v2 prepare /path/to/package
```

Submit a checked package using the selected member profile, or name an existing
profile explicitly:

```bash
python3 "scripts/akbs_patch_submit.py" android-change-v2 submit /path/to/package \
  --profile <member_alias>
```

The package member must match the profile. Submission preserves package bytes, uses a
stable idempotency key, and verifies the ordinary patch-upload response. After a
transport failure, retry the same package; do not rename it or regenerate its identity
to work around an uncertain response.

## Legacy Framework Change V1

Prepare a validated v1 package from a legacy v1-compatible Framework capture:

```bash
python3 "scripts/akbs_patch_submit.py" --profile <member_alias> --prepare \
  --patch-package /path/to/capture \
  --project "TVE8402M" --platform rk --android-version 14 \
  --summary "功能补丁摘要" --status validated
```

Submit the latest pending v1 package:

```bash
python3 "scripts/akbs_patch_submit.py" --profile <member_alias> --submit-latest
```

Information completion remains attached to the same server-assigned
`patch_package_id`. Inspect with `--inspect-information-request <request-id>` and
answer with `--complete-information-request /path/to/response.json`. Patch bytes and
their hash remain immutable.

## Boundaries

- Use `$android-patch-capture` when source changes or a new package are required. Its
  output is already the final v2 package and may be passed directly to `check`,
  `prepare`, or `submit`.
- Never translate v2 material into `framework_change`. V1 compatibility applies only
  to genuine historical v1 packages.
- Keep one functional goal per package. Do not relabel a component or invent evidence
  to pass a local check.
- Use `$akbs-knowledge-search` before implementation when available; its search result
  is development evidence, not a curation decision.
- Administrator curation decides new-case, merge, archive, or rejection after intake.
- Deprecated Framework intake entrypoints are compatibility-only forwarders for v1.
