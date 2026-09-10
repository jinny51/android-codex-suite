# AKBS Member Ops

Standalone AKBS member plugin, version 2.1.0. It owns member setup, knowledge
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

`akbs-patch-submit` also handles final
`akbs-android-change-package-v2/2/android_change` directories produced directly by
`android-patch-capture`. It strictly reads, checks, byte-preserves, and submits them
for all seven component layers. V1 and v2 are two input formats on the same member
profile, patch-upload endpoint, idempotency rule, and ordinary server receipt. There
is no intermediate capture conversion, separate approval lifecycle, or v1 fallback.

The v2 checker enforces exact file inventory and hashes, component/source/patch/
evidence references, and per-component patch/evidence coverage. Formal target
platform is only `mtk`, `rk`, or `unisoc`; Android version is a separate decimal
field. Capture CLI inputs such as `mtk16` are split before the manifest is written,
and a formal package containing `mtk16` is rejected.

## Install-family boundary

Daily, weekly and patch entrypoints share the existing member version gate. Its
generic release lookup, version comparison and marketplace update code comes from
the same `shared/codex_plugin_update.py` source packaged in the engineering plugin.
Business triggers and member configuration/package handling remain unchanged; neither
plugin needs the other installed to update itself. An update changes files on disk,
not Skill instructions already loaded into a Codex session.

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
