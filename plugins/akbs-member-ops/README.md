# AKBS Member Ops

Standalone AKBS member plugin, version 2.3.21. It owns member setup, knowledge
search and merge review, personal daily/weekly reports, independent document
submission, and Android change package handling without depending on the engineering or optional practices
plugins at runtime.

This patch release reserves manual/historical Patch imports for the existing
authenticated administrator upload routes. Ordinary members submit newly developed
Patches through the current workflow; shared local preparation remains available.

Setup displays optional server-owned email and LincHub identity without writing
contacts locally or back to AKBS. New-member email belongs to the existing
administrator account-opening process; existing profiles and bindings are unchanged.

## Canonical Skills

- `akbs-member-setup`
- `akbs-knowledge-search`
- `akbs-knowledge-merge-review`
- `akbs-daily-report`
- `akbs-weekly-report`
- `akbs-patch-submit`
- `akbs-doc-submit`

App knowledge uses `akbs-knowledge-search` for published capabilities, exact LincHub
versions and explicit actual-use feedback. Source stays in its existing personal or
SysPros repository. The existing curation pass reads linked Git changes; members do
not submit an additional App package or register App capabilities themselves.

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

Weekly display dates use the packaged read-only 2026 work calendar, exported from
system `calendar_policy.py` at `93925749aa8a89dee9cbfeeb73d7e08532f07016`.
The server remains its maintenance authority; release updates compare that source
when calendar data changes. Date calculation adds no network dependency. Missing,
damaged or uncovered-year data stops generation before a package is written.

## Incoming contracts

There is one incoming implementation at
`internal/incoming-v2/scripts/akbs_member_intake.py`. Current capture and submit use
`knowledge-incoming-package/2/android_change`. Its `components[].layer` classifies
each patch into application, platform, native, hal, kernel, device, or build; all
other package fields and the upload lifecycle remain the current v2 contract.
`application` is a layer for changes to product-source apps, not a route for
independently developed App source. Manual and snapshot capture methods follow the
same product-source boundary.

Historical `knowledge-incoming-package/1/framework_change` archives remain readable
without rewriting their bytes. Current tools do not create or submit that legacy kind.

For an administrator's own Patch submission, local prepare/validate is reused,
while the available local `akbs-curation-maintainer` verifies the server-bound
administrator and author, then submits and continues the knowledge decision in
the same conversation. Ordinary members keep the member route without probing
administrator credentials; local-only preparation does not authorize production writes.

## Install-family boundary

Daily, weekly, Patch and independent Doc entrypoints share the existing member version gate. Its
generic release lookup, version comparison and marketplace update code comes from
the same `shared/codex_plugin_update.py` source packaged in the engineering plugin.
Business triggers and member configuration/package handling remain unchanged; neither
plugin needs the other installed to update itself. An update changes files on disk,
not Skill instructions already loaded into a Codex session.

Doctor and business gates query Codex's `plugin/installed` app-server inventory for
the current working directory as the authority for the unique active install.
Historical cache directories are evidence only and are
never selected by highest version. Legacy and target Android plugin generations
must not be active together. Optional orchestration plugins are outside the member
plugin's install-family decision and are validated only when Android Engineering Ops
explicitly selects one.
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
