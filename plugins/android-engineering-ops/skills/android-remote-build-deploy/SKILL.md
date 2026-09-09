---
name: android-remote-build-deploy
description: "Use after Android source access has registered a remote Linux project that builds through AOSP Soong/Make modules or an explicitly configured vendor full-build entry. Owns channel-v2 discovery, profiles, checkpoints, supported builds, artifact manifests, mounted artifact verification, and local adb delivery on WSL or macOS."
---

# Android Remote Build Deploy

Use this Skill for registered remote AOSP Soong/Make module builds and explicitly
configured vendor full builds across Android domains. It is not a generic Gradle,
Kbuild/kernel, external-driver, or Bazel executor; those project-owned commands run
through `android-remote-channel` under `android-change-workflow`.

Linux remains authoritative for source, Git/repo state, build configuration, build
execution, and artifact identity. The administrator workstation owns only registry
resolution, the narrow artifact bridge, and local adb.

## Task Startup

Before a new engineering task starts, set `PLUGIN_ROOT` to the directory two levels
above this `SKILL.md`, read `../../references/task-start.md`, and run its shared
`task_start.py` entry, which includes the first local install check. Reuse the same task ID/result across nested Skills;
do not update again at each build/remote command or while recovering running work.
This applies equally when this Skill is invoked directly without the full workflow.

## Active Install Family

Before discovery, configuration, checkpoint, build, artifact-bridge reads, local
`adb`, network access, or state/artifact writes, set `PLUGIN_ROOT` to the directory two
levels above this `SKILL.md` and run:

```bash
python3 "$PLUGIN_ROOT/lib/android_engineering_ops/install_family.py" \
  --plugin-root "$PLUGIN_ROOT"
```

Only packaged documentation and pure `--help` may precede this check. A nonzero result
is a hard stop. Every independent public entry must be covered by a current target-only
receipt; a prior worker claim or a mixed legacy/target installation is invalid.

## Remote-Only Source Contract

The mounted Android path is not a Codex source workspace. It exists for human
source CRUD and as a narrowly scoped artifact bridge from a confirmed remote
`PRODUCT_OUT` to local `adb`. Codex must not inspect source, infer modules from
local Android.bp/Android.mk files, generate wrappers through the mount, or run
source search, edits, Git, repo, checkpoints, or builds locally.

Every build-side source operation runs at canonical `PROJECT_ROOT` through
`android-remote-channel` protocol v2. Discovery, inference, and plans use the
channel; remote `.codex` installation, profile writes, checkpoints, source
writes, and builds use its exclusive project lock. Direct SSH is not a fallback.

Local code may read only a manifest-confirmed artifact below the registered
artifact bridge. Local destination memory, manifests, and delivery evidence
belong under `$CODEX_HOME/artifacts/android-remote-build-deploy/<project-id>`.

## Boundary

This skill owns:

- consuming the registered `SSH_HOST`, `PROJECT_ROOT`, `WORKING_SUBPATH`,
  `PROJECT_ID`, and `ARTIFACT_BRIDGE_PATH`
- installing a content-addressed runtime atomically under
  `PROJECT_ROOT/.codex/remote-v2`
- remote discovery and remote Android.bp/Android.mk profile inference
- atomic remote configuration and profiles
- remote checkpoints, including staged and unstaged Git changes
- stable-command-ID module or explicit vendor full builds
- remote-generated artifact manifests and local bridge re-verification
- local adb delivery and build-delivery evidence

It does not own source mounting, arbitrary build systems, domain diagnosis,
requirement acceptance, rollback decisions, or Git history changes requested by the
user.

## Identity

Resolve a registry entry without converting a nested working path into a build
root:

```bash
python3 "<skill>/scripts/resolve_remote_mapping.py" --project "$HUMAN_SOURCE_PATH"
```

Consume these fields:

- `PROJECT_ROOT`: canonical build-workspace candidate registered for the project
- `WORKING_SUBPATH`: path below the project root used for changed-file context
- `REMOTE_WORKING_PATH`: diagnostic composition of the two fields
- `PROJECT_ID`: stable local artifact-memory key
- `ARTIFACT_BRIDGE_PATH`: mounted project root used only after manifest validation

The remote channel performs the final server identity and `realpath` canonicalization.

## Formal entry

```bash
ENTRY="<skill>/scripts/remote-build-v2.py"
COMMON=(
  --ssh-host "$SSH_HOST"
  --project-root "$PROJECT_ROOT"
  --working-subpath "$WORKING_SUBPATH"
  --project-id "$PROJECT_ID"
)
```

### Install

```bash
python3 "$ENTRY" "${COMMON[@]}" install
```

Installation is content-addressed and atomically swaps
`.codex/remote-v2/current`. If a legacy `.codex/build-push.sh` exists,
installation fails with `LEGACY_WRAPPER_REVIEW_REQUIRED`. Inspect it, then use
`--preserve-legacy` to install alongside it. That mode never overwrites the old
wrapper and records its SHA-256 and known freshness/touch/destination
capabilities. This prevents a test61-style wrapper from being silently degraded.

Every independent `install`, `configure`, and `profile-set` invocation uses a
fresh ensure command id because remote runtime/config/profile state may have
been cleaned after an earlier channel command completed. These operations are
atomic and idempotent. Within one invocation, one uncertain SSH transport
failure retries the identical id and payload to attach; finite wait timeout
`124` returns immediately and is never silently doubled. Existing releases are
verified file-by-file for expected SHA-256 and mode plus `release.sha256`;
`REMOTE_V2_RELEASE_TAMPERED` is a hard stop and the evidence is not overwritten.

### Discover and configure

```bash
python3 "$ENTRY" "${COMMON[@]}" --preserve-legacy discover

python3 "$ENTRY" "${COMMON[@]}" --preserve-legacy configure \
  --envsetup build/envsetup.sh \
  --lunch uis7885_2h10_native-userdebug-native \
  --product-out out/target/product/uis7885_2h10 \
  --build-entry debug_Jide.sh
```

Discovery reads only the remote project. Configuration is an exclusive,
atomic remote update. `--build-entry` is retained for an explicitly requested
vendor full build; normal Framework delivery uses module builds.

### Infer and register a profile

```bash
python3 "$ENTRY" "${COMMON[@]}" --preserve-legacy infer-profile \
  --path services/core/java/com/example/File.java

python3 "$ENTRY" "${COMMON[@]}" --preserve-legacy profile-set \
  --profile framework-services \
  --modules services \
  --artifact 'services=out/target/product/uis7885_2h10/system/framework/services.jar|/system/framework/services.jar'
```

Inference combines `WORKING_SUBPATH` with each changed path and inspects build
files remotely. A profile must store exact artifact paths; basename searches are
not accepted. Repeat `--artifact` for multiple outputs. Optional `--touch-path`
preserves projects whose established incremental build requires touching a
source or resource first.

### Plan and checkpoint

```bash
python3 "$ENTRY" "${COMMON[@]}" --preserve-legacy plan \
  --profile framework-services

python3 "$ENTRY" "${COMMON[@]}" --preserve-legacy checkpoint \
  --name before-services-build \
  --purpose "preserve staged, unstaged, and untracked work"
```

Checkpoint creation is an exclusive channel operation. It never inspects Git or
repo state through the mount.

### Build

Use a stable command ID that the caller persists for the build transaction:

```bash
BUILD_COMMAND_ID="req-123-services-001"
python3 "$ENTRY" "${COMMON[@]}" --preserve-legacy build \
  --profile framework-services \
  --command-id "$BUILD_COMMAND_ID"
```

The same ID and same command attach after a disconnect; they do not rebuild.
The build runs with the exclusive project lock. The remote runtime records the
build time window, requires each exact artifact to be freshly produced, hashes
the file remotely, and creates
`android-remote-build-artifact-manifest-v1`. The formal entry validates the
closed context and saves manifests under:

```text
$CODEX_HOME/artifacts/android-remote-build-deploy/<project-id>/manifests/<command-id>/
```

`--mode full` is allowed only with a confirmed vendor `--build-entry`; module
mode is the default.

## Local adb delivery

Delivery is fail-closed. `push_artifacts.py` requires the remote manifest,
trusted build context, and the registered mounted project root. It derives the
local file path from the remote path, rejects symlink escape, rechecks size and
SHA-256, then copies and re-hashes it into a private local staging directory.
Local adb receives that private snapshot rather than the mutable SMB/CIFS path,
closing the verification-to-push race.

```bash
python3 "<skill>/scripts/push_artifacts.py" \
  --artifact-manifest "$LOCAL_MANIFEST" \
  --artifact-bridge-root "$ARTIFACT_BRIDGE_PATH" \
  --expected-module services \
  --expected-workspace-id "$WORKSPACE_ID" \
  --expected-command-id "$BUILD_COMMAND_ID" \
  --remote-source-root "$CANONICAL_PROJECT_ROOT" \
  --remote-build-profile framework-services \
  --product-out "$ARTIFACT_BRIDGE_PATH/out/target/product/uis7885_2h10" \
  --destinations-file "$CODEX_HOME/artifacts/android-remote-build-deploy/$PROJECT_ID/artifact-destinations.json" \
  --evidence-out "$CODEX_HOME/artifacts/android-remote-build-deploy/$PROJECT_ID/latest-build-delivery.json" \
  --learn-destinations \
  --adb-serial "$ADB_SERIAL"
```

`ADB` may name Windows `adb.exe` under WSL; macOS uses native adb. Unverified
legacy artifacts are accepted only with `--compat-unverified --dry-run`; that
mode cannot invoke adb or produce a delivery PASS.

Delivery evidence remains `scope=build_delivery` and
`requirement_acceptance=unverified`. Return control to
`android-change-workflow` for behavior acceptance.

## Legacy CLI policy

- `discover-project.sh`, `ensure-build-session.sh`, `infer-profile.sh`, and
  `create-checkpoint.sh` are thin remote-v2 shims and require explicit remote
  project identity.
- Mounted `--repo` flows fail with exit `64`.
- `generate-build-push.sh` is retired and always fails with migration guidance.
- No legacy entry contains direct SSH or reads/writes mounted Android source.
