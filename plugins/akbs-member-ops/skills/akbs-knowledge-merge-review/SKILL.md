---
name: akbs-knowledge-merge-review
description: "Use when an AKBS member needs to list, inspect, compare, analyze, or explicitly dispute a server-provided knowledge merge confirmation. Do not use for ordinary knowledge search, administrator curation decisions, or patch submission."
---

# AKBS Knowledge Merge Review

Use this skill for the member side of an existing AKBS merge-confirmation event. The
server-provided `confirmation_id` is the causal event identifier;
`patch_package_id` remains the patch business subject and `package_key` remains source
provenance only.

## Active Install Family

Before listing, reading, comparing, analyzing, or disputing a confirmation, run:

```bash
python3 "../akbs-member-setup/scripts/akbs_member_setup.py" preflight-install-family
```

Only pure `--help` may bypass it. Continue only on exit 0 with JSON `status=PASS`;
no server read or dispute is allowed from a missing, ambiguous, checkout, or mixed
legacy/target installation.

Read-only actions are `list`, `detail`, `target`, `compare`, and `analyze`. They must use
the member merge-confirmation API and must fail clearly when it is unavailable. Never
replace a failed server read with local knowledge search or fabricate merge evidence.

`dispute` is an external write. Send it only when the user explicitly asks to submit an
objection, `--send-dispute` is present, and a reason or member assessment is supplied.
Reading or analyzing a confirmation does not authorize a dispute.

## Commands

```bash
python3 "scripts/akbs_knowledge_merge_review.py" list

python3 "scripts/akbs_knowledge_merge_review.py" analyze \
  --confirmation-id <confirmation_id>

python3 "scripts/akbs_knowledge_merge_review.py" dispute \
  --confirmation-id <confirmation_id> \
  --send-dispute \
  --dispute-reason "目标知识没有覆盖当前补丁的功能目标"
```

The existing `android-knowledge-search --merge-confirmation ...` commands remain as
backward-compatible entrypoints. This Skill is the user-facing owner of the merge-review
intent; it does not decide whether AKBS creates or merges knowledge.

For a typed knowledge proposal, distinguish `add_implementation` (another
technical solution under the same Case) from `attach_evidence` (evidence or
applicability for the exact existing Implementation). Compare the server's
immutable proposal and decision identity with the full target Case and, when
present, target Implementation; use their precise hashes, not title similarity.
Stale targets are recheck-required, not an excuse to confirm a different object.
Initial proposals still support member confirmation/dispute. After an explicit
dispute is re-reviewed by the administrator, the server closes that review
without asking the member to confirm the same dispute again. This Skill must
not submit either action merely because a read or analysis was requested.
