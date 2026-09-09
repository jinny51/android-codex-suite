---
name: akbs-patch-submit
description: "Use when an AKBS member needs to preflight or materialize a capture, or read, check, prepare, or submit an Android change package. Supports legacy Framework v1 and canonical Android change v2 across all seven component layers; excludes source implementation and administrator curation."
---

# AKBS Patch Submit

Use this member-facing Skill for Android change packages across application,
platform, native, HAL, kernel, device, and build layers. It owns
`scripts/akbs_patch_submit.py` and has three deliberately separate contract routes.

## Active Install Family

Before reading/checking/preparing a package, preflighting a capture, creating an
archive, or making any server request, run:

```bash
python3 "../akbs-member-setup/scripts/akbs_member_setup.py" preflight-install-family
```

Only a true parser `--help` request may bypass it; a literal `--help` after `--` is
business input and must not bypass the gate. Continue only on exit 0 with JSON
`status=PASS`.

## Contract dispatch

- Legacy `knowledge-incoming-package/1/framework_change` remains readable,
  checkable, and genuinely submittable through the internal incoming v1 kernel.
- Generic `akbs-android-change-package-v2/2/android_change` supports strict local
  read, check, byte-preserving prepare, and explicit server submission.
- `android-patch-capture-package-v2/2.0/android_change_capture` remains the
  frozen read-only preflight contract and cannot be passed directly to `prepare`.
- Capture 2.1 is the materializer input. `adapt-capture` creates a canonical package
  for `application`, `platform`, `native`, `hal`, `kernel`, `device`, and `build`.
  Every component must satisfy its own required and conditional evidence groups;
  a cross-layer change stays one coherent package with exact component bindings.
- The seven-layer qualification pack requires the matching server contract. Deploy
  server support before distributing this member update. Existing two-layer packages
  retain their original contract hash and bytes and remain readable/submittable;
  rerunning adaptation reuses an existing matching package without changing its identity.
- Android change v2 submission uses the selected member profile and the existing
  incoming HTTP endpoint. The server independently qualifies every component;
  ordinary members do not need a pilot grant. A disabled or incompatible server
  returns an explicit error, never a v1 fallback or a fabricated success.
- Every v2 read/check/prepare/submit/adapt-capture action first requires an authoritative,
  target-only active plugin inventory. Missing, malformed, ambiguous, or mixed
  inventory fails closed; `--help` remains available without a business gate.
- Never translate a v2 package or an `android_change_capture` into
  `framework_change` merely to make upload available.
- On a layer/contract rejection, preserve the capture and report the failing stage,
  declared component, contract hash, and exact error. Do not infer a server rejection
  from a local adaptation error, or change a layer label to bypass acceptance.

The v2 client outputs are untrusted inputs. The local checker validates schema,
the frozen evidence-profile hash, qualification-input hash, file hashes and sizes,
archive inventory, component bindings, and required evidence groups. A PASS means
`client_semantic_coherence_valid`; it never means server-qualified.
The hash-pinned qualification pack freezes all 37 evidence groups using shared
shape families and a machine-validated versioned adapter input contract with
closed per-group rules. Producer-owned capture `component_assertion.assertion_id`
values map here through each structured group's `accepted_assertion_ids`;
consumer `group_id` never enters the capture contract. Offline adapter rows remain
`untrusted_client_input`; they never represent server qualification.
Each v2 component must provide the contract's canonical `layer`, `type`,
`partition`, and `ownership` facets. Legacy `change_domain` is not a v2 facet and
is rejected rather than used to infer any canonical value.

## Generic Android change v2

Preflight a frozen capture 2.0 without network or file writes, or materialize a
complete capture 2.1 locally:

```bash
python3 "scripts/akbs_patch_submit.py" android-change-v2 adapt-capture /path/to/capture
```

For 2.0, the Phase 2 result remains intentionally non-zero with `status=BLOCKED` and
`reason_code=android_change_v2_adapter_contracts_unavailable`. The preflight
strictly checks the bundled, hash-pinned Draft 2020-12 capture schema before the
capture's semantic identity and declared/effective
validated status chain, local-only authority, every regular file against the
manifest SHA-256 inventory, patch SHA-1 bytes, `components[]`,
`primary_component_id`, repository and patch `component_ids[]`, evidence and
qualification bindings. It emits their hashes and structured gaps to stdout,
but creates no canonical v2 package, client-adapter output, receipt, or PASS.
For 2.1, the same command evaluates component-scoped evidence and writes one
deterministic hash-bound canonical package. Repeating the same input returns the
same package as an idempotent reuse. The source capture is never rewritten,
`server_qualified` remains false, and no HTTP or v1 route is attempted.

Read only the manifest identity and component layers:

```bash
python3 "scripts/akbs_patch_submit.py" android-change-v2 read /path/to/package
```

Check the complete directory, including exact file inventory and hashes:

```bash
python3 "scripts/akbs_patch_submit.py" android-change-v2 check /path/to/package
```

After a successful check, preserve the exact package bytes under
`$CODEX_HOME/artifacts/akbs-member-ops/android-change-v2/pending/`:

```bash
python3 "scripts/akbs_patch_submit.py" android-change-v2 prepare /path/to/package
```

Submit a checked canonical package using the existing selected member profile
(or select an existing profile explicitly):

```bash
python3 "scripts/akbs_patch_submit.py" android-change-v2 submit /path/to/package --profile <member_alias>
```

The package member must match that profile. Submission preserves package bytes,
uses a stable idempotency key, and checks the server receipt. Retry the same
package after a transport failure; do not rename it or regenerate its identity
to work around an uncertain response. Local checks and adaptation never imply
server acceptance. Capture artifact names (including `@` or `+`) remain intact;
canonical IDs are separate deterministic identifiers.

## Legacy Framework change v1

Prepare a validated v1 package only from a legacy v1-compatible Framework capture:

```bash
python3 "scripts/akbs_patch_submit.py" --profile <member_alias> --prepare \
  --patch-package /path/to/capture \
  --project "TVE8402M" --platform rk --android-version 14 \
  --summary "功能补丁摘要" --status validated
```

Submit the latest v1 pending package through the real incoming HTTP route:

```bash
python3 "scripts/akbs_patch_submit.py" --profile <member_alias> --submit-latest
```

Queue information completion remains attached to the same server-assigned
`patch_package_id`. Read a request with `--inspect-information-request <request-id>`
and answer it with `--complete-information-request /path/to/response.json`.
Patch bytes and their hash stay immutable; completion may add only the allowed
text, fields, and non-patch attachments bound to that causal `request_id`.

## Boundaries

- Use `$android-patch-capture` when source changes or recapture are required,
  then run the explicit `adapt-capture` route. Do not feed that capture
  directory directly to `prepare` and do not route it through v1.
- Use `$akbs-knowledge-search` before implementation and record the honest reuse
  decision; search evidence is not a curation merge decision.
- Keep one functional goal per package and never upgrade incomplete evidence by
  prose or by relabeling the package contract.
- Do not decide new-case or merge outcomes. Administrator curation owns those
  decisions after intake.
- The deprecated `$android-framework-patch-intake` and
  `android_knowledge_intake.py ... patch` surfaces are compatibility-only thin
  forwarders for v1-era calls.
