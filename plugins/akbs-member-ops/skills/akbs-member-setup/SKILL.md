---
name: akbs-member-setup
description: "Use when any AKBS member, including GMS or report-only members, needs first setup, profile configuration guidance, doctor checks, plugin/cache freshness diagnosis, or endpoint health verification. Do not use for daily/weekly generation, patch submission, knowledge search, or curation."
---

# AKBS Member Setup

Use this Skill for member identity and local client readiness. It owns first-setup
guidance, the selected `member_alias`/`member_name` profile, doctor checks, plugin and
session-cache freshness, and AKBS endpoint health. It does not generate or submit daily,
weekly, or patch packages. It applies to every AKBS member, including GMS and
report-only members; setup is not restricted to Framework or source-development work.

For first setup, read the existing canonical prompt at
`../../internal/incoming-v2/references/member-setup-prompt.md`. That internal-kernel
location remains the single source; do not duplicate member configuration rules.

Before creating or modifying `$CODEX_HOME/akbs-member-ops.toml`, run the read-only
install-family preflight. Continue only on exit 0 with JSON `status=PASS`:

```bash
python3 "scripts/akbs_member_setup.py" preflight-install-family
```

This command reads only the authoritative Codex active inventory and the bound plugin
publication. It does not read member configuration or write files. A failure leaves
setup unchanged and is not permission to repair config first.

Run strict doctor for the exact member profile before enabling report or patch automation:

```bash
python3 "scripts/akbs_member_setup.py" doctor \
  --profile <member_alias> \
  --strict \
  --check-remote
```

Doctor treats `codex plugin list --json` as active-install authority. It requires
one enabled `akbs-member-ops@android-codex-suite` row and binds its
absolute marketplace `source.path` to this process's exact versioned Codex cache.
The two roots are expected to differ, while their direct manifest bytes,
name/version identity, and full publication content plus normalized
regular-file executable-bit hashes must agree. Only
`__pycache__` and `.pyc` runtime caches are excluded. Inventory failure,
malformed JSON/version, symlinks, duplicate entries, a source/cache/content
mismatch, execution from a checkout, or any legacy/target generation mix is
blocking. A checkout is only development evidence and cannot impersonate an
installed active plugin.

The member profile supplies `member_alias`; do not derive it from Git author, invent an
alias, or ask ordinary members to configure server tokens, cookies, roles, database
paths, or client-IP headers. Incoming v2 support is an internal data-compatibility
kernel, not a public legacy Skill.

Local doctor reports `configured_unverified`, not authenticated identity. With
`--check-remote`, it reads `/akbs/api/member/me/identity`; only an authenticated
response matching the configured alias yields `server_confirmed`. An unavailable
server, unsupported identity endpoint, rejection or mismatch is not confirmation.
Keep server-returned member status, ranking eligibility and daily/weekly obligations
separate from local configuration and business permissions. An administrator who
also contributes uses an ordinary member profile; administrator privileges are not
required for development, search or contribution. Do not rewrite identity or grant
privileges to make a diagnostic pass.

If `$CODEX_HOME/akbs-member-ops.toml` is present, it is the sole AKBS config
authority: do not discover or read any legacy member/search/report config, even
for conflict checks. Read legacy config only when the target file is absent.
The separate `$CODEX_HOME/android-engineering-ops.toml`
`[identity].member_alias` is a strict standalone Android attribution fallback,
not a second AKBS profile and not a free-form identity override.
