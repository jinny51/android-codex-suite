---
name: android-remote-channel
description: Provide a remote execution channel for Android build servers using SSH and persistent remote tmux sessions. Use when Android skills need to create/reuse a remote session, run repeated remote source/git/build commands, tail logs, check busy state, keep long builds recoverable, or avoid duplicating remote-session logic.
---

# Android Remote Channel

Use this skill as the shared remote-workspace layer for Android build-server
workflows. It owns canonical workspace identity, remote command transport, one
persistent fixed `tmux` runner, durable command state, reconnect/attach behavior,
logs, and project-global locking. It does not own Android source mounting, build
profile inference, build wrapper generation, artifact mapping, adb deployment,
or framework verification.

## Task Startup

Before a new engineering task starts, set `PLUGIN_ROOT` to the directory two levels
above this `SKILL.md`, read `../../references/task-start.md`, and run its shared
`task_start.py` entry, which includes the first local install check. Reuse the same task ID/result across nested Skills;
do not update again at each build/remote command or while recovering running work.
This applies equally when this Skill is invoked directly without the full workflow.

## Active Install Family

Before any SSH/network request, remote source read, state lookup/write, lock, or command
dispatch, set `PLUGIN_ROOT` to the directory two levels above this `SKILL.md` and run:

```bash
python3 "$PLUGIN_ROOT/lib/android_engineering_ops/install_family.py" \
  --plugin-root "$PLUGIN_ROOT"
```

Only packaged documentation and pure `--help` may precede this check. A nonzero result
is a hard stop. The bundled `remote-channel.sh` enforces the same target-only check;
direct SSH is not a bypass when the legacy and target families are mixed.

## Remote-Only Source Contract

The workstation mount is for human source CRUD and the confirmed build-output
artifact bridge. It is not a Codex source workspace. This channel is the only
allowed execution path for Codex source reads, searches, edits, Git/repo,
diagnostics, checkpoints, patch capture, and builds. A channel failure is a hard
stop, not permission to fall back to the mount or a one-off SSH source command.

## Boundary

Every Codex operation that reads or changes Android source-tree facts must use
this skill. That includes source search/read/edit, Git/repo, diagnostics,
checkpoints, patch capture, module builds, and build-output identity generation.
The mounted workstation path is not a fallback source workspace.

Do not use this skill as a replacement for:

- `android-source-access`: it owns local source mapping and registry.
- `android-remote-build-deploy`: it owns Android build/deploy semantics.
- `android-change-workflow`: it owns diagnosis, implementation discipline, risk, and final behavior verification.

## Protocol

Protocol v2 canonicalizes identity on the server from its stable machine
identity, the remote uid, and `realpath(REMOTE_ROOT)`. Literal SSH aliases do not
participate in identity, so WSL and macOS clients share the same workspace,
queue, runner, and exclusive lock when they reach the same account and tree.

Private remote state is stored on the build server:

```text
~/.codex/android-remote-sessions/<hash>/
├── session.env
├── runner.sh / runner.protocol / runner.ready / runner.heartbeat
├── runner.expected.sha256 / runner.active.sha256
├── dispatch.lock / runner.lock
├── queue.sequence
├── busy                         # compatibility hint, not state authority
├── current.log
└── commands/
    ├── <command_id>.line
    ├── <command_id>.request.sha256
    ├── <command_id>.queue-seq
    ├── <command_id>.queued / .running / .terminal
    ├── <command_id>.events
    ├── <command_id>.log
    └── <command_id>.exit

~/.codex/android-remote-locks/<hash>.lock
```

Command IDs are immutable idempotency keys. Repeating the same ID with the same
command and lock attaches to the existing command; changing the payload is
`COMMAND_ID_CONFLICT`. Terminal states are `completed`, `failed`, `aborted`, or
`lost`.

Queue order follows the remote monotonic registration sequence, not command-id
sorting. Runner reuse proves the fixed `runner.0` pane, pane/runner PID,
readiness, protocol, and payload digest. A payload upgrade while work is active
fails with `RUNNER_UPGRADE_BLOCKED_ACTIVE` without killing that work; retry
ensure after it reaches a terminal state. Terminal transition writes `.exit`
before the create-once `.terminal` commit marker and repairs legacy
terminal-only crash remnants fail-closed.

Runner replacement has a bounded lock-handoff retry: the new fixed pane may
wait briefly for the killed runner to release `runner.lock`, while ensure uses a
longer ready deadline. Lock timeout never creates `runner.ready` and therefore
fails closed.

Use `--lock exclusive` for source edits, Git/repo writes, checkpoints, patch
capture, and builds. Read-only searches can use `none`. The fixed runner sources
commands in one persistent Bash environment, so exported variables, functions,
`envsetup`, and lunch state survive later command IDs and client reconnects.

## WSL/macOS Entry

```bash
CHANNEL_DIR="<path-to-this-skill>"
"$CHANNEL_DIR/scripts/remote-channel.sh" \
  --ssh-host "$SSH_HOST" \
  --remote-root "$REMOTE_ROOT" \
  check
```

Create or reuse the remote session:

```bash
"$CHANNEL_DIR/scripts/remote-channel.sh" \
  --ssh-host "$SSH_HOST" \
  --remote-root "$REMOTE_ROOT" \
  ensure
```

Initialize reusable shell state, then use it from a later command ID:

```bash
"$CHANNEL_DIR/scripts/remote-channel.sh" \
  --ssh-host "$SSH_HOST" \
  --remote-root "$REMOTE_ROOT" \
  run --lock exclusive --command-id "$PROJECT_ID-shell-init-v1" -- \
  'export CODEX_PROJECT_READY=1; codex_project_ready() { test "$CODEX_PROJECT_READY" = 1; }'

"$CHANNEL_DIR/scripts/remote-channel.sh" \
  --ssh-host "$SSH_HOST" \
  --remote-root "$REMOTE_ROOT" \
  run --lock none --command-id "$PROJECT_ID-shell-check-v1" -- \
  'codex_project_ready && echo READY'
```

Android wrapper installation, profiles, builds, and artifact manifests belong
to `android-remote-build-deploy`; do not invoke legacy `.codex/build-push.sh` or
`.codex/build-session.sh` as a channel fallback.

Recover status or logs:

```bash
"$CHANNEL_DIR/scripts/remote-channel.sh" --ssh-host "$SSH_HOST" --remote-root "$REMOTE_ROOT" status --command-id "$BUILD_COMMAND_ID"
"$CHANNEL_DIR/scripts/remote-channel.sh" --ssh-host "$SSH_HOST" --remote-root "$REMOTE_ROOT" tail --command-id "$BUILD_COMMAND_ID" --lines 160
```

Waiting is finite. Timeout returns `124` without cancelling remote work. Re-run
the same command ID with the exact same command and lock to attach instead of
starting another build.

The Bash entry enables OpenSSH multiplexing by default with command-line options only; it does not edit `~/.ssh/config`. Set `CODEX_REMOTE_CHANNEL_SSH_MUX=0` to disable it for troubleshooting.

If `tmux` is missing, install it only through the explicit action:

```bash
"$CHANNEL_DIR/scripts/remote-channel.sh" \
  --ssh-host "$SSH_HOST" \
  --remote-root "$REMOTE_ROOT" \
  install-tmux
```

`install-tmux` first tries passwordless sudo, then
`CODEX_REMOTE_SUDO_PASSWORD`, then supported credentials already saved by
`android-source-access`. It does not save new passwords. If no usable password
exists, it prints `REMOTE_SUDO_PASSWORD_REQUIRED`. macOS Keychain fallback is
still tracked migration work; existing build servers already provide tmux.

## Failure Handling

- `TMUX_MISSING`: run the explicit `install-tmux` action or install it manually; do not use direct SSH source execution as a fallback.
- `FLOCK_MISSING`: install `flock`/util-linux on the server before source writes or builds.
- `REMOTE_SUDO_PASSWORD_REQUIRED`: read source-access credentials failed or no usable credential exists; ask the user for the remote sudo password and rerun with `CODEX_REMOTE_SUDO_PASSWORD`.
- `REMOTE_ROOT_MISSING`: resolve the source mapping again through the platform source-access skill.
- `COMMAND_ID_CONFLICT`: the same id was reused with a different command or lock; inspect the existing command and choose a new id only for genuinely new work.
- `COMMAND_WAIT_TIMEOUT`: the command continues remotely; attach with the same id or inspect `status`/`tail`.
- `RUNNER_UPGRADE_BLOCKED_ACTIVE`: wait for the active command to finish or stop it explicitly; never kill it implicitly to install a new runner payload.
- `lost`: execution became uncertain after runner/server loss; inspect source/build facts and never re-execute automatically.
- `aborted`: an explicit stop terminated or removed the command from the queue.
- Missing or stale `current.log`: use `status`, then tail a specific `--command-id` / `-CommandId` if known.

For details, read `references/protocol.md`.
