---
name: akbs-knowledge-search
description: Search the team knowledge repository for reusable cases, platform variants, archived patches, search anchors, validation notes, and prior Android engineering solutions. Use before re-implementing an Android change across App or GMS, platform, native, HAL, kernel, device, or build layers; during requirement triage; or when a user asks to find existing patches or team knowledge.
---

# AKBS Knowledge Search

Use this skill to search the team knowledge repository before starting new analysis or implementation. It is the member-side search entry for the knowledge system: `akbs-daily-report`, `akbs-weekly-report`, `akbs-patch-submit`, and `android-patch-capture` produce or submit materials through the appropriate member contract, and the user's local `akbs-curation-maintainer` skill promotes AI-usable knowledge into the knowledge repository for this skill to retrieve.

This skill does not submit reports, create patches, edit source, or decide correctness by itself. It returns prior facts so Codex can judge whether an existing case, variant, patch, symbol, or validation fact is relevant to the current requirement.

## Active Install Family

Before any search, local-index read, server request, refresh, merge-confirmation action,
or usage-record write, run the plugin's read-only machine preflight:

```bash
python3 "../akbs-member-setup/scripts/akbs_member_setup.py" preflight-install-family
```

Only pure `--help` may bypass it. Continue only on exit 0 with JSON `status=PASS`;
missing, ambiguous, checkout, or legacy/target mixed installations are a hard stop.

Default search uses the AKBS member search endpoint when reachable. The request carries only `X-AKBS-User=<member_alias>` plus content-negotiation headers, and the endpoint address comes from the AKBS endpoint resolver defaults or `CODEX_REPORT_AKBS_ENDPOINT_*` admin/test overrides. The server validates the fixed workstation source IP; do not send role, token, cookie, or client-IP claims. Do not ask ordinary members to configure test35, server paths, submit commands, or a raw database repository path for search.

If the server endpoint is unavailable, unauthorized, times out, or returns an incompatible contract, the script falls back to the local JSONL knowledge repository worktree and marks the result as `source=local_jsonl_fallback`. Treat fallback output as local text search that has not passed server reuse grading.

Server failures use the shared `akbs-error-envelope-v1` client. Display only the stable error `code`, `request_id`, typed category, and sanitized message. A legacy HTTP body is explicitly marked as legacy and its free text must not decide retry, fallback business behavior, or merge state. Never expose a token, cookie, request body, session text, path, or underlying exception text.

`akbs-knowledge-merge-review` is the user-facing owner of merge-confirmation list,
detail, compare, analysis, and explicit disputes. This search Skill retains the legacy
`--merge-confirmation` flags only as compatibility entrypoints. Those flags remain
server-only, read-only by default, and must not submit a dispute without the user's
explicit request, `--send-dispute`, and a reason or assessment.

Each normal search writes a member-side search usage record under the intake artifact directory so later daily and patch packages can carry the pre-change knowledge use evidence. The record is development evidence only; it is not a curation decision.

If a search result shows a recommended replacement case, treat it as curation guidance from the local knowledge loop: inspect the replacement before reusing the obsolete or contradicted case. The replacement hint is still evidence, not an automatic reuse decision.

## Quick Command

```bash
python3 "scripts/akbs_knowledge_search.py" \
  "电源键 frameworks/base" \
  --limit 8
```

Useful variants:

```bash
# Search primary cases. Default `--source auto` prefers the server API.
python3 "scripts/akbs_knowledge_search.py" \
  "通知音量 SystemUI" --type case

# Search platform/project implementations.
python3 "scripts/akbs_knowledge_search.py" \
  "TVE8402M VolumeDialogImpl" --type variant

# Search only patch assets.
python3 "scripts/akbs_knowledge_search.py" \
  "persist.sys launcher" --type patch

# Return machine-readable output for another workflow.
python3 "scripts/akbs_knowledge_search.py" \
  "WindowManager display" --json

# Force local JSONL fallback for offline work.
python3 "scripts/akbs_knowledge_search.py" \
  "PackageManager permission" --source local --root /path/to/knowledge

# Record an explicit member-side use decision with the search.
python3 "scripts/akbs_knowledge_search.py" \
  "电源键 rk3576" \
  --reuse-decision adapt \
  --reuse-target case-power-key \
  --reuse-reason "同类策略可参考，当前项目需适配"

# Use an explicit mounted or cloned knowledge repository root.
python3 "scripts/akbs_knowledge_search.py" \
  "PackageManager permission" --root /path/to/knowledge

# Review pending merge confirmations without sending anything.
python3 "scripts/akbs_knowledge_search.py" \
  --merge-confirmation list

# Generate a human-readable and Codex-evidence merge analysis.
python3 "scripts/akbs_knowledge_search.py" \
  --merge-confirmation analyze \
  --merge-confirmation-id merge-confirmation-20260703-member-patch

# Send a dispute only after the member explicitly asks for it.
python3 "scripts/akbs_knowledge_search.py" \
  --merge-confirmation dispute \
  --merge-confirmation-id merge-confirmation-20260703-member-patch \
  --send-dispute \
  --dispute-reason "目标知识没有覆盖当前补丁的功能目标"
```

## Read the Case's Original Patches

After choosing a server search candidate, use its `case_id` to list the complete
patch originals that the active case explicitly cites:

```bash
python3 "scripts/akbs_knowledge_search.py" --case-patches <case_id> --json
python3 "scripts/akbs_knowledge_search.py" --case-patches <case_id> \
  --download-patch <asset_id> --out /path/to/task-output/source.patch --json
```

These actions use the same member identity, endpoint resolver, and installation
preflight as search. They are server-only GETs: no local JSONL fallback, merge
write, or search-usage/reuse-success record. Pick `asset_id` from this case's list;
the client checks downloaded size and SHA-256 and refuses an existing `--out`.
Never apply a truncated preview as an original. Downloading a verified original
does not prove applicability, successful adaptation, or target-device acceptance.

Supported originals are an active v2 case's exact `source_evidence` assets or a
historical case's hash-bound current revision and original source snapshot. For a
historical original, `asset_id` is an opaque membership handle from this case's
list, not a newly registered package asset. Legacy/history cases without that
closed source and controlled bytes return `unavailable`; state the missing
original without downgrading their historical validation or fabricating a path.
Do not call unrelated package assets the case's source patches.

## Source Selection

In `--source auto`, the script first tries the server endpoint. Local JSONL fallback searches the first valid knowledge repository root it can find:

1. `--root <path>`
2. `CODEX_KNOWLEDGE_ROOT`
3. `CODEX_KNOWLEDGE_REPO_WORKTREE` or `CODEX_REPORT_KNOWLEDGE_REPO_WORKTREE`
4. `knowledge_repo_worktree` or `knowledge_worktree` from the selected profile in `$CODEX_HOME/akbs-member-ops.toml`. If that target file is present, it is the sole AKBS config authority and none of the legacy files are discovered or read. Only when it is absent may the permanent read-only compatibility files `$CODEX_HOME/android-knowledge-search.toml`, `$CODEX_HOME/android-knowledge-intake.toml`, `$CODEX_HOME/report/config.toml`, or the nearest `.codex/report.toml` supply the value.
5. current directory or its parents, when they contain current `index/*.jsonl` knowledge indexes
6. generic Codex worktrees such as `$CODEX_HOME/worktrees/knowledge` or detected Windows `Documents/Codex/worktrees/knowledge`
7. common mapped server locations such as `/mnt/z/knowledge/knowledge`

The search skill must not automatically read the database repository. If a local maintainer needs to inspect database internals, pass that path explicitly with `--root` and understand that it is not the normal member reuse path.

Pass `--refresh` only when using a local Git clone and the latest server content is required. Refresh runs `git pull --ff-only`; it skips refresh when the worktree is dirty.

## Search Discipline

When handling a new Android engineering requirement in any supported change layer (App or GMS, platform, native, HAL, kernel, device, or build):

1. Search with feature words, affected module, likely class name, property key, Settings key, resource key, search anchor, and artifact name.
2. Read the top matching case, variant, patch readme, or validation fact before deciding whether to reuse.
3. Treat `status`, `package_status`, `reuse_hint`, platform, and validation fields as hints, not truth.
4. Prefer case and variant results first; then inspect related patches, AI evidence, and symbols.
5. Compare facts: modified files, touched symbols, artifact, risk notes, build evidence, device verification, rollback path.
6. If a prior patch looks relevant, report the evidence and remaining uncertainty before applying or adapting it.
7. Use explicit `--type report`, `--type event`, or `--type evidence` only for administrator trace-back or debugging archive material. Default `--type all` is the AI reuse view and does not return report/event archive rows.
8. Default member search filters out retracted cases, variants, patch assets, symbols, and evidence rows. It also redacts retracted object references embedded inside ordinary search evidence payloads, such as older `search_before_change.results` entries. The knowledge repository can retain those rows for traceability, but they must not appear as reusable member results. Explicit archive/debug queries may still show archived or retracted material.

For `android-change-workflow`, this is the pre-analysis search gate. Search first; if no useful result exists, continue with normal requirement analysis and implementation.

## Search Usage Evidence

默认会写入搜索使用证据（search usage evidence）：

```text
$CODEX_HOME/artifacts/akbs-member-ops/search-usage/<YYYYMMDD>/*.json
```

旧配置中的 `out_dir` 仅作为兼容读取信息，不改变写入位置。后续 `akbs-daily-report` 和 `akbs-patch-submit` 会通过共享 intake 内核读取同一天同成员的 target/legacy 证据，并生成 `materials/evidence/search_before_change.json`。

可记录的成员侧使用决策：

```text
reuse
adapt
reference_only
not_applicable
not_found
unknown
```

这些值只说明成员侧 Codex 如何使用知识库仓库。它们不能替代管理端本地知识沉淀技能做出的沉淀结论（curation decision）。

## References

Read `references/search-contract.md` before changing the script output format or integrating this skill into another workflow.
