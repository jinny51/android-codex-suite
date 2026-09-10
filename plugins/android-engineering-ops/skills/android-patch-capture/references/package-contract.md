# Android Change V2 Package Contract

`android-patch-capture` directly writes the final
`akbs-android-change-package-v2/2/android_change` directory. One package represents
one coherent, fully verified Android change. It may span repositories and components
only when every patch serves the same functional goal.

The package is the input to member-side `read`, `check`, `prepare`, and `submit`.
There is no second local format or conversion phase. V1 and v2 are input formats on
the common patch upload lifecycle; a v2 package never falls back to v1.

## Directory

```text
$CODEX_HOME/artifacts/android-patch-capture/packages/<run-id>/
├── manifest.json
├── README.md
├── patches/
│   ├── <platform><version>-<module>@<change-id>.patch
│   └── <platform><version>-<module>@<change-id>.patch
└── evidence/
    ├── changed-files.json
    ├── patch-diff-facts.json
    ├── patch-problem-summary.json
    ├── risk-surface.json
    ├── coding-standard-check.json
    ├── verification-result.json
    ├── rollback-plan.json
    ├── search-before-change.json
    └── package-check.json
```

Conditional evidence may add more JSON files. There are no per-patch README files;
the root `README.md` explains the change across all repositories.

Every payload path is relative, normalized, and confined to the package. Each
descriptor records exact lowercase SHA-256 and byte size. The descriptor set must
equal the complete regular-file inventory other than `manifest.json`; symlinks,
directories in payload positions, unlisted files, missing files, and byte drift are
rejected.

## Manifest

An abridged manifest looks like this:

```json
{
  "schema": "akbs-android-change-package-v2",
  "schema_version": "2",
  "package_kind": "android_change",
  "package_status": "validated",
  "identity": {
    "member_alias": "alice",
    "run_id": "20260910-153000-display-policy",
    "created_at": "2026-09-10T15:30:00+08:00"
  },
  "subject": {
    "title": "调整显示策略和设置入口",
    "summary": "调整显示策略和设置入口",
    "feature_key": "display-policy-settings-entry",
    "primary_component_id": "platform-core",
    "target": {
      "project": "TVE8402M",
      "platform": "rk",
      "android_version": "14"
    }
  },
  "workflow": {
    "contract": "current_codex_skill",
    "implementation_origins": ["codex"],
    "capture_tool": {"id": "android-patch-capture", "version": "2"}
  },
  "components": [
    {
      "id": "platform-core",
      "layer": "platform",
      "type": "framework",
      "partition": "system",
      "ownership": "aosp"
    }
  ],
  "sources": [
    {
      "id": "repo-001",
      "kind": "git",
      "repo_path": "frameworks/base",
      "base_revision": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "head_revision": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    }
  ],
  "readme": {
    "path": "README.md",
    "sha256": "<64 lowercase hex>",
    "size_bytes": 1200
  },
  "patches": [
    {
      "id": "patch-001",
      "component_ids": ["platform-core"],
      "source_id": "repo-001",
      "path": "patches/rk14-frameworks-base@display-policy-settings-entry.patch",
      "sha256": "<64 lowercase hex>",
      "size_bytes": 2400,
      "format": "git_diff"
    }
  ],
  "evidence": [
    {
      "id": "verification-result",
      "kind": "verification_result",
      "component_ids": ["platform-core"],
      "path": "evidence/verification-result.json",
      "sha256": "<64 lowercase hex>",
      "size_bytes": 600,
      "scope": "feature",
      "result": "PASS",
      "summary": "目标行为和相邻回归验证通过"
    }
  ]
}
```

Formal `subject.target.platform` is exactly `mtk`, `rk`, or `unisoc`, while
`android_version` is separate. A capture CLI token such as `rk14` is split before
manifest construction. Combined or aliased formal values are invalid.

Component layer is one of `application`, `platform`, `native`, `hal`, `kernel`,
`device`, or `build`; `type`, `partition`, and `ownership` remain independent. Every
component must be referenced by at least one patch and at least one evidence item.
Every source must be referenced by a patch. All IDs are unique within their
collections and every reference must resolve.

Git sources bind repository path and revisions. Manual/historical imports use an
`external` source with an immutable external reference. Credentials must not appear
in source URLs or evidence.

## Source and Verification Facts

For `current_codex_skill`, the source comes from an immutable
`android-remote-patch-snapshot-v1` created through `android-remote-channel`. It binds
the remote root, workspace/command identity, Git state, diffs, changed files, blob
hashes, generation time, and canonical snapshot SHA-256. The one-step
`capture_remote_snapshot.py --package` path obtains that snapshot and immediately
packages it. A compatibility split handoff retains the bounded freshness check.

The package stores facts, not promises. Verification evidence says what command,
artifact, device behavior, or equivalent coverage was observed. Build transfer alone
does not prove the requirement. Search evidence records the real pre-change reuse
decision but does not make a curation decision.

`package_status` is always `validated`. If policy, source freshness, component
coverage, build/device evidence, hashes, or schema checks do not pass, the final
directory is not published. Failed or partial work belongs in engineering/report
evidence instead of a weakened upload package.

## Atomic Publication

The writer builds in a private staging directory, validates the complete manifest and
payload, fsyncs as required by the local publisher, and publishes with a single atomic
winner. A failure or concurrent loser does not expose a half package. An existing
published package is never overwritten.

After publication, pass the whole directory directly to `akbs-patch-submit`. Preparing
or submitting must preserve the package bytes and identity.
