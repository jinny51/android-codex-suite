# Patch Package Status Rules

Daily work is always recorded. Framework change package status describes member-side evidence quality; it is not a curation decision and does not decide whether anything enters the knowledge repository. Member-side upload preparation and admin-side queue admission are stricter than local preservation: every uploaded patch package must be `validated`.

## Status

- `validated`: clear scope, clean diff, build pass, and device or accepted equivalent verification pass.
- `candidate`: clear implementation evidence, but validation or acceptance evidence is incomplete.
- `draft`: partial or WIP implementation evidence.
- `failed`: failed implementation or failed verification retained as negative evidence.
- `blocked`: blocked work retained with cause, checked paths, and missing external condition.

## Default Policy

- No completed patch: record daily/weekly trace with `work_findings`.
- Clear patch without validation evidence: keep it local or record it in daily/weekly context; do not upload as an ordinary patch package.
- Patch with clean function scope, traceable project/platform/Android version, clean patch assets, and accepted device/equivalent verification: upload as `validated`.
- Known failure: record it in daily/weekly context when it helps future AI avoid repeating the path.
- Blocked work: record it in daily/weekly context when the missing condition is explicit.
- Stop ordinary patch upload when status is not `validated`, or when sensitive material, mixed unrelated diffs, unclear task boundaries, high-risk misleading reuse hints, bad metadata, missing verification, or dirty patch assets are present.
- New protocol framework change packages must include `materials/display/patch_view.json` for human-facing cards/details and `materials/evidence/patch_ai_facts.json` for admin/AI validation inputs. Missing either file means the package is not ready for ordinary upload.

Admission moves the same `patch_package_id` from queue to main `under_review`; it does not replace the subject with a source `package_key`. The management-side curation Skill then produces the new-knowledge or planned-merge decision. A queue information request uses its causal `request_id` to complete non-patch metadata or supporting files on that same subject; it never changes the patch set.

## Evidence Keywords

Treat these as validation signals when found in Codex sessions:

- 编译通过
- 验证通过
- 已验证
- 已交付
- 已合入
- 客户复测通过
