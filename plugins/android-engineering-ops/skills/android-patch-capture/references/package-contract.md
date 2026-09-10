# Android Change Capture Package Contract

This package is a local handoff artifact. One package represents one coherent Android
change: a feature, bug fix, failed attempt, or blocked/stage-worthy implementation. A
change may touch multiple repo-managed Git repositories, so the package has one change
README and one patch per affected source repository.

The public `android-patch-capture` Skill accepts canonical `components[]` whose
`layer`, `type`, `partition`, and `ownership` facts come from
`contracts/change-domain/v1/domain-profiles.json`. Every supported layer produces an
`android_change_capture`. The legacy `--change-domain` flag is only a compatibility
adapter: known values provide only layer/type hints, absent orthogonal facets remain
`unknown`, and ambiguous `vendor` requires all explicit component fields.

One change may span components and repositories. The caller declares every component,
selects `primary_component_id`, and maps every captured repository to one or more
`component_ids`; capture never guesses the map from a repository path. Single-component
arguments remain a compatibility form and bind that one explicit component to all
captured repositories. Every patch inherits only its repository's explicit binding.

The capture writer emits the additive 2.1 contract for any supported layer. Pass the
whole capture directory to `akbs-patch-submit android-change-v2 adapt-capture`; never
pass a capture directly to canonical-package `prepare`. The frozen 2.0 contract remains
readable only through its original zero-write BLOCKED preflight. With the v2 server
writer disabled, network submission is capability-gated with zero side effects and no
fallback to v1.
Existing legacy Framework v1 packages remain readable/submittable only through their
permanent compatibility contract; their bytes and provenance are not rewritten.

Project metadata is an applicability boundary. If project clues conflict across `--project`, the verified remote snapshot, Git metadata, the platform-neutral source-access registry, summary, or diff text, the capture package must keep `project=unknown`, preserve all candidate TVD/TVE/TVA/TVI models in `project_inference.candidates`, and record the conflict in `project_inference.limits`.

For `current_codex_skill`, all source facts come from an immutable
`android-remote-patch-snapshot-v1` created inside `android-remote-channel` v2.
The snapshot binds the canonical remote root, workspace and command identities,
Git/repo status, HEAD/branch/remotes, staged and unstaged binary diffs, final
HEAD-relative binary diff, untracked inventory/content patch, changed files,
per-blob hashes, generation time, and a canonical snapshot SHA-256. The local
packager verifies every field and copies the validated snapshot into package
evidence. It never accepts `--source-root` or a caller patch for this workflow.
The preferred `capture_remote_snapshot.py --package` entry creates and transfers the
snapshot under the remote-channel lock and immediately invokes that same packager with
tool-owned identity arguments. This removes the human delay between snapshot and
packaging without weakening the immutable handoff or atomic package publication. The
split handoff remains a compatibility surface and retains the bounded age guard.

The manifest `project` field stores only the normalized company model. Branch suffixes, customer suffixes, build branches, business labels, module labels, Chinese descriptions, and other non-standard trailing text must stay in `project_inference` evidence. For example, `TVE1067M1_H031` becomes `TVE1067M1`, `TVE1086U_MAIN_HANGYAN` becomes `TVE1086U`, and `TVE1091U福建移动高清` becomes `TVE1091U`.

Patch capture filters diff sections that contain only file mode metadata, such as `old mode 100755` / `new mode 100644`. Mode-only changes are not change evidence and must not create a standalone patch package. If a file mode change is intentional, it must appear with content, summary, risk, and verification evidence explaining why executable permission is part of the change.

## Directory

```text
$CODEX_HOME/artifacts/android-patch-capture/packages/<run-id>/
├── manifest.json
├── README.md
├── patches/
│   ├── <platform>-<module>@<change-id>.patch
│   └── <platform>-<module>@<change-id>.patch
└── evidence/
    ├── changed-files.json
    ├── patch-diff-facts.json
    ├── patch-problem-summary.json
    ├── risk-surface.json
    ├── coding-standard-check.json
    ├── rollback-plan.json
    ├── remote-source-snapshot.json
    ├── build-result.json
    ├── verification-result.json
    ├── search-before-change.json
    ├── component-assertion.json
    ├── import-provenance.json
    └── package-check.json
```

The component assertion and import provenance files are conditional. Assertions use
the producer-owned neutral `android-patch-capture-component-assertion` contract and
`assertion_id`; the capture does not contain consumer group-to-adapter interpretation.
Its outer result is `INFO`. Nested `PASS`, `FAIL`, and `INFO` require observations;
`NOT_APPLICABLE` requires both basis and limits. Component/assertion pairs are unique
and the assertion component union exactly matches the evidence envelope.

There is no `patches/*.readme.md` in the capture package. The change-level README lives
at the package root.

## Manifest

The following is an abridged 2.1 shape (inventory and unchanged fields are omitted):

```json
{
  "schema_version": "2.1",
  "schema": "android-patch-capture-package-v2",
  "package_type": "android_change_capture",
  "components": [
    {"id": "platform-core", "layer": "platform", "type": "framework", "partition": "system", "ownership": "aosp"},
    {"id": "settings-ui", "layer": "application", "type": "system_app", "partition": "system_ext", "ownership": "product", "qualifiers": ["privileged"]}
  ],
  "primary_component_id": "platform-core",
  "change_id": "display-policy-settings-entry",
  "readme": "README.md",
  "project": "TVE8402M",
  "summary": "功能摘要",
  "status": "candidate",
  "declared_status": "candidate",
  "effective_status": "candidate",
  "status_was_upgraded": false,
  "implementation_origin": "codex",
  "workflow_contract": "current_codex_skill",
  "captured_by": "codex",
  "server_submission": {
    "v2_writer": "disabled",
    "v2_submission_allowed": false,
    "server_qualified": false
  },
  "coding_standard_check": {
    "required": false,
    "mode": "development_safety_net",
    "path": "evidence/coding-standard-check.json",
    "result": "PASS"
  },
  "related_report_run_ids": ["20260601-210000-daily"],
  "source_snapshot": {
    "path": "evidence/remote-source-snapshot.json",
    "schema": "android-remote-patch-snapshot-v1",
    "workspace_id": "0123456789abcdef",
    "command_id": "patch-snapshot-20260826",
    "remote_root": "/home/test61/unisoc/project",
    "sha256": "64-hex-sha256"
  },
  "source_roots": [
    "/home/test61/unisoc/project/frameworks/base",
    "/home/test61/unisoc/project/packages/apps/Settings"
  ],
  "git_repositories": [
    {
      "id": "repo-001",
      "repo_path": "frameworks/base",
      "root": "/home/test61/unisoc/project/frameworks/base",
      "component_ids": ["platform-core"],
      "git": {
        "branch": "feature/TVE8402M-policy",
        "remote": "ssh://example/frameworks/base.git",
        "head": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
      }
    }
  ],
  "project_inference": {
    "project": "TVE8402M",
    "recognized": true,
    "basis": ["source_root: /home/test61/unisoc/project"],
    "checked_sources": ["命令参数 project", "remote snapshot", "repo_path", "git branch", "git remote"],
    "limits": [],
    "recognition_scope": "TVD/TVE/TVA/TVI"
  },
  "verification_chain": {
    "remote_build": true,
    "local_delivery": true,
    "device_verification": true
  },
  "evidence": [
    {
      "id": "verification-result",
      "kind": "verification_result",
      "path": "evidence/verification-result.json",
      "result": "PASS",
      "scope": "feature",
      "component_ids": ["platform-core", "settings-ui"],
      "contract": {"id": "android-patch-capture-evidence", "version": "2.1"},
      "declared_claims": ["verification_recorded_not_server_accepted"],
      "summary": "device verification evidence"
    },
    {
      "id": "patch-diff-facts",
      "kind": "patch_diff_facts",
      "path": "evidence/patch-diff-facts.json",
      "result": "INFO",
      "scope": "feature",
      "component_ids": ["platform-core", "settings-ui"],
      "contract": {"id": "android-patch-capture-evidence", "version": "2.1"},
      "declared_claims": ["patch_bytes_parsed"],
      "summary": "变更补丁 diff 中解析出的客观事实"
    },
    {
      "id": "coding-standard-check",
      "kind": "coding_standard_check",
      "path": "evidence/coding-standard-check.json",
      "result": "PASS",
      "scope": "feature",
      "component_ids": ["platform-core", "settings-ui"],
      "contract": {"id": "android-patch-capture-evidence", "version": "2.1"},
      "declared_claims": ["local_policy_check_recorded"],
      "summary": "团队补丁开发与日志规范检查"
    }
  ],
  "qualification_bindings": [
    {
      "component_id": "platform-core",
      "repository_ids": ["repo-001"],
      "patch_ids": ["rk14-frameworks-base@display-policy-settings-entry"],
      "evidence_ids": ["verification-result", "patch-diff-facts", "coding-standard-check"],
      "contract": "android-patch-capture-local-qualification-v2",
      "declared_claims": ["verification_recorded_not_server_accepted", "patch_bytes_parsed", "local_policy_check_recorded"]
    }
  ],
  "patches": [
    {
      "id": "rk14-frameworks-base@display-policy-settings-entry",
      "path": "patches/rk14-frameworks-base@display-policy-settings-entry.patch",
      "repository_id": "repo-001",
      "repo_path": "frameworks/base",
      "component_ids": ["platform-core"],
      "source_root": "/home/test61/unisoc/project/frameworks/base",
      "content_sha1": "40-hex-sha1",
      "status": "candidate",
      "reuse_hint": false,
      "implementation_origin": "codex",
      "workflow_contract": "current_codex_skill",
      "captured_by": "codex",
      "facts": {
        "content_sha1": "40-hex-sha1",
        "repo_path": "frameworks/base",
        "modified_files": [],
        "modules": [],
        "symbols": [],
        "system_properties": [],
        "settings_keys": [],
        "resource_keys": [],
        "framework_log_keys": []
      }
    }
  ]
}
```

Every evidence row declares exact, non-empty `component_ids` membership and a
versioned contract object. Package-wide generated evidence may list all components only
when its payload contains facts for all of them; external evidence must state its scope,
and a multi-component capture never borrows one component's evidence for another.
`qualification_bindings[]` binds each component to repository IDs, patch IDs, local
evidence IDs, the neutral local contract, and truthful declared claims.
`file_inventory.files[]`
contains path, byte size, and SHA-256 for every regular package file except
`manifest.json`; the manifest is self-excluded explicitly because a self-hash would be
circular. The capture authority remains local-only, cannot upload/allocate a server ID,
and cannot promote status. `package-check.json` records declared/effective status and
always records `status_was_upgraded=false`.

## Fact-First Rule

Store objective evidence. Do not force permanent AI judgments into the package.

Good facts:

- repository path
- modified files
- patch content sha1
- added/deleted symbols
- property keys
- Settings keys
- resource keys
- FrameworkLog keys
- build target and result
- device verification steps
- remote build host/source root/command/profile/artifact path/artifact SHA1
- local artifact transfer path, local adb serial, push/install action, and restart/reload action
- search-before-change decision, match points, mismatch points, and later outcome
- equivalent verification method, reason, coverage, and remaining risk
- failure evidence

Patch-content explanation is allowed only when it carries basis and limits:

```json
{
  "kind": "patch_problem_summary",
  "scope": "feature",
  "confidence": "medium",
  "problem_summary": "窗口或 Activity 焦点行为需要按产品需求调整。",
  "solution_summary": "修改 WindowManager 或 ActivityTaskManager 相关路径中的焦点处理逻辑。",
  "keywords": [],
  "basis": ["功能涉及源码仓库: frameworks/base"],
  "limits": ["补丁内容不能单独证明原始需求文字"]
}
```

`capture_android_patch.py` accepts a paired `--problem-summary` and `--solution-summary` from the Codex workflow after it has read the actual request, diff, and verification evidence. The script writes those values into the generated evidence and records their explicit capture basis. Passing only one is invalid. Module-based inference is retained for backward-compatible draft or candidate capture; generated JSON must never be edited by hand to replace a generic fallback.

These judgments should still happen at search/use time:

- applicability to a new project
- reuse risk
- likely conflicts
- whether a newer patch replaces this one
- whether to apply, adapt, or only reference

`search-before-change.json` records the member-side AI use decision before the change. It is evidence for later curation, not a curation decision:

```json
{
  "result": "INFO",
  "method": "knowledge_search",
  "searched": true,
  "queries": ["电源键 用户态 控制"],
  "results": ["命中 case-power-key-to-app，但项目和 Android 版本不同"],
  "decision": "adapt",
  "reuse_decision": "adapt",
  "targets": ["case-power-key-to-app"],
  "match_points": ["同类按键策略需求"],
  "mismatch_points": ["旧变体是 rk12，当前项目是 rk14"],
  "reason": "复用案例思路，按当前项目源码适配",
  "outcome": "adapted_success"
}
```

Allowed `decision` values:

```text
reuse            直接复用：命中知识和当前平台/版本/路径/验证范围足够匹配。
adapt            适配：同类问题成立，但平台、Android 版本、项目、源码路径或实现细节不同。
reference_only   仅参考：机制、风险或排查方向有用，但不能作为实现依据。
not_applicable   不适用：命中知识与当前需求或源码条件冲突。
not_found        未命中：搜索后没有找到可用知识。
unknown          未记录：历史包或异常场景没有形成明确决策。
```

`implementation_origin` identifies who wrote the code. `workflow_contract` identifies how the patch entered AKBS. They are independent and must never be inferred from one another. AKBS pre-change search is optional for the standalone engineering plugin. When it really happened, preserve its exact decision (`not_found` included); when it did not, record `searched=false` and do not fabricate it. Capture status follows existing policy/verification evidence, while a later member/server contract may independently reject or downgrade missing search evidence.

## Remote Build To Local ADB Evidence

When Android work is built on a remote server and delivered to a local USB
device, pass the build/deploy receipt explicitly with `--build-result`. Current
capture must not read `.codex` evidence through a mounted source root. The
expected receipt shape is:

```json
{
  "contract_version": "akbs-verification-evidence/v2",
  "scope": "build_delivery",
  "requirement_acceptance": "unverified",
  "kind": "verification_result",
  "result": "PASS",
  "method": "device",
  "build": ["framework-services build PASS"],
  "device": "ABC123",
  "steps": ["adb -s ABC123 push services.jar /system/framework/services.jar"],
  "remote_build": {
    "host": "builder01",
    "source_root": "/build/android/TVE8402M",
    "command": "bash .codex/build-push.sh build --profile framework-services",
    "profile": "framework-services",
    "artifacts": [
      {"path": "/build/android/TVE8402M/out/target/product/tve/system/framework/services.jar", "sha1": "40-hex-sha1"}
    ]
  },
  "local_delivery": {
    "transfer": "mounted Samba/CIFS product output",
    "local_artifacts": ["/mnt/repo/out/target/product/tve/system/framework/services.jar"],
    "adb_serial": "ABC123",
    "adb_actions": ["adb -s ABC123 push services.jar /system/framework/services.jar"],
    "device_restarts": ["adb -s ABC123 reboot"]
  }
}
```

The top-level `PASS` in this automatic file means the build/delivery operation passed,
not requirement acceptance. Explicit imports require the v2 `build_delivery/unverified`
fields. Capture retains the complete producer object under `delivery_receipt` in a
separate `deploy_result` evidence envelope named `build-delivery*.json`; the source
file is not changed. The envelope carries capture component bindings and keeps the
delivery scope and result. It cannot replace the generated feature verification.
For multiple components, use `--evidence-component build-delivery:COMPONENT_ID` to
associate the first such receipt without rewriting it. Only explicit device-behavior
or qualified equivalent verification can produce requirement acceptance.

## Coding Standard Check

The canonical `android-change-policy/v1` is applied during development. Capture-time
checks verify the same versioned contract and remain a safety net for manual,
historical, external, or half-inherited code. The currently optional
`jinny-android-practices` layer may add non-conflicting preferences, but it is not a
second policy authority.

`coding-standard-check.json` records:

- policy ID/version, selected member profile and expected `member_alias`
- per-file comment adapter, paired/legacy marker counts, aliases, dates and violations
- legacy import exceptions, which remain `WARN` and never become canonical-policy `PASS`
- direct `Log.*` or `Slog.*` additions
- FrameworkLog keys
- `persist.sys.framework.debug.*` usage
- resource keys
- repository-level errors and warnings

Noncompliant code should fail or be downgraded; do not present it as validated.

## Package Status Is A Hint

Allowed statuses:

- `draft`: generated or unfinished, not enough validation
- `candidate`: implemented, waiting for broader validation
- `validated`: compiled and device-verified for the original scope
- `failed`: retained as failed verification or failed implementation evidence
- `blocked`: retained as blocked work evidence

Status helps rank local engineering materials, but it is not a curation decision.
Capture never upgrades a declared status; failed qualification downgrades declared
`validated` to effective `candidate`. Optional AKBS pre-change search is preserved when
available and never fabricated. A later submit or curation flow may impose its own
server-side evidence requirements. `reuse_hint` remains only a later-review hint.

## Project Recognition

`project` must be a recognized company project model in the current scope:

```text
TVE
TVA
TVI
```

Recognition priority:

1. Explicit `--project` containing a scoped company project model.
2. Change package project or patch item project.
3. Source context: verified remote snapshot root, `repo_path`, Git branch, Git remote, or the platform-neutral source-access registry.
4. README/diff/summary text.

Generic labels such as `android16`, `Camera2`, or `mtk android16 Camera2` are checked inputs, not project names. When no company project model is found, write `project: "unknown"` and preserve the checked sources in `project_inference`.

## Report Link

When the change came from a known daily or weekly incoming run, include the run id. Weekly run ids are provenance only; weekly packages remain database archive records and are not materialized into the knowledge repository:

```json
{
  "related_report_run_ids": ["20260601-210000-daily"]
}
```

This is an explicit deterministic link for the server. Do not invent report links from fuzzy similarity.

## Verification Evidence

Runtime behavior changes in the modified module should use device verification. Equivalent verification is allowed for resource-only, build-only, packaging-only, static config, or documentation changes when device interaction is not the right proof.

`verification-result.json` should record:

```json
{
  "contract_version": "akbs-verification-evidence/v2",
  "scope": "feature",
  "requirement_acceptance": "accepted",
  "result": "PASS",
  "method": "device",
  "device": "rk3576",
  "steps": [],
  "observed": "",
  "health_checks": [],
  "artifacts": [],
  "remote_build": {
    "host": "builder01",
    "source_root": "/build/android/TVE8402M",
    "command": "bash .codex/build-push.sh build --profile framework-services",
    "profile": "framework-services",
    "artifacts": [
      {
        "path": "/build/android/TVE8402M/out/target/product/tve8402m/system/framework/services.jar",
        "sha1": "40-hex-sha1"
      }
    ]
  },
  "local_delivery": {
    "transfer": "scp builder01:/build/android/TVE8402M/out/.../services.jar ~/.codex/artifacts/services.jar",
    "local_artifacts": ["~/.codex/artifacts/services.jar"],
    "adb_serial": "ABC123",
    "adb_actions": ["adb -s ABC123 push services.jar /system/framework/services.jar"],
    "device_restarts": ["adb -s ABC123 reboot"]
  }
}
```

Equivalent verification must be explicit:

```json
{
  "contract_version": "akbs-verification-evidence/v2",
  "scope": "feature",
  "requirement_acceptance": "accepted",
  "result": "PASS",
  "method": "equivalent",
  "equivalent_type": "artifact_static_check",
  "reason": "资源/配置类变更不涉及被修改模块运行时行为",
  "coverage": [],
  "remaining_risk": "未覆盖真机运行时行为；该变更不涉及被修改模块的运行时路径"
}
```

Legacy unscoped verification records remain readable as historical evidence, but they cannot
upgrade a package to `validated`.
