# AKBS Member Ops

Standalone AKBS member plugin, version 2.0.1. It owns member setup, knowledge
search and merge review, personal daily/weekly reports, and Android change
package handling without depending on the engineering or optional practices
plugins at runtime.

## Canonical Skills

- `akbs-member-setup`
- `akbs-knowledge-search`
- `akbs-knowledge-merge-review`
- `akbs-daily-report`
- `akbs-weekly-report`
- `akbs-patch-submit`

## Configuration and artifacts

The authoritative target configuration is:

```text
$CODEX_HOME/akbs-member-ops.toml
```

The presence of the target file selects the only AKBS configuration authority;
legacy member/search/report files are not discovered, parsed, merged, or read
for conflict diagnostics. Only when the target file is absent are legacy files
read as migration/rollback inputs. Legacy files are never rewritten or deleted.

For Android engineering attribution, a strict
`$CODEX_HOME/android-engineering-ops.toml` `[identity].member_alias` may supply
the standalone fallback only when no AKBS profile is available. An explicit
profile may select only an existing AKBS profile, and a differing AKBS and
standalone engineering alias fails closed.

All newly generated member artifacts use:

```text
$CODEX_HOME/artifacts/akbs-member-ops
```

The old `$CODEX_HOME/artifacts/android-knowledge-intake` tree remains a permanent
read-only compatibility source. Historical packages are not moved or rewritten.

## Incoming contracts

There is one incoming v1 implementation at
`internal/incoming-v1/scripts/akbs_member_intake.py`. Legacy Framework
`knowledge-incoming-package/1/framework_change` remains genuinely submittable.
The pinned public contract and verification reference stay byte-compatible with
incoming v1.

`akbs-patch-submit` also handles generic
`akbs-android-change-package-v2/2/android_change` packages. It can strictly read,
check, and byte-preserve them into the target artifact root. Client coherence is
not server qualification. The bundled v2 evidence profile keeps the server
writer blocked, so v2 submit fails before network or file submission side
effects and never falls back to Framework v1.

An `android-patch-capture-package-v2/2.0/android_change_capture` directory is a
frozen read-only source contract and cannot be passed directly to v2 `prepare`.
`android-change-v2 adapt-capture CAPTURE` preserves that 2.0 BLOCKED preflight.
For additive capture 2.1, it evaluates the hash-pinned 37-group qualification
pack and materializes a canonical v2 package offline for enabled application and
platform layers. The source capture is unchanged, output is idempotent,
`server_qualified=false`, and the writer remains off. Other frozen layers fail
with `layer_not_enabled`; no path falls back to Framework v1.

## Install-family boundary

Doctor and business gates use `codex plugin list --json` as the authority for the
unique active install. Historical cache directories are evidence only and are
never selected by highest version. Legacy and target Android plugin generations
must not be active together. The optional `jinny-android-practices` plugin must
also match the selected generation (`1.0.3` rollback versus `2.x` target).
An unavailable or malformed active inventory blocks every business action; only
help and side-effect-free static diagnostics remain available.
The active `akbs-member-ops` row must bind the exact published
`akbs-member-ops@android-codex-suite` identity and version to two
distinct roots: its absolute local marketplace `source.path` and this process's
exact versioned Codex cache root. Both direct manifests must have the same bytes,
name, and version, and both publication trees must have the same content plus
normalized executable-bit hash;
only `__pycache__` directories and `.pyc` runtime cache files are excluded.
Missing fields, symlinks, malformed versions, source/cache/manifest/content
mismatch, or an execution checkout fails closed. Another active installation
cannot lend its identity to this process.
