# Android Remote Channel Protocol v2

## Ownership

`android-remote-channel` owns transport and remote-workspace mechanics only:

- SSH reachability and canonical remote-workspace discovery.
- One fixed `tmux` runner per canonical workspace.
- Durable command registration, execution state, logs, and exit status.
- Same-command attachment after client disconnect.
- Project-global exclusive locking for source writes and builds.

It does not own Android build profiles, wrapper generation, artifact mapping,
local adb deployment, or Framework acceptance verification.

## Canonical workspace identity

The literal SSH argument is transport configuration, not workspace identity.
Aliases such as `test61`, `user@192.168.100.23`, or two SSH config names must
resolve to the same workspace when they reach the same remote account and source
tree.

The server derives identity from:

```text
stable server identity | remote uid | realpath(REMOTE_ROOT)
```

Stable server identity is read from `CODEX_REMOTE_CHANNEL_SERVER_ID`,
`/etc/machine-id`, `/var/lib/dbus/machine-id`, or the DMI product UUID, in that
order. The resulting SHA-256 is not based on the literal SSH alias. The remote
root must exist and is canonicalized before any state path is selected.

The first 16 hexadecimal characters of the workspace SHA-256 identify:

```text
tmux session:  codex-android-<workspace-id>
state:         ~/.codex/android-remote-sessions/<workspace-id>/
project lock:  ~/.codex/android-remote-locks/<workspace-id>.lock
runner target: codex-android-<workspace-id>:runner.0
```

All aliases and symlink spellings for the same canonical workspace therefore
share one queue, one runner, and one exclusive lock.

## Private state layout

Protocol v2 sets `umask 077`. State directories use mode `0700`; command,
metadata, log, exit, and lock files use mode `0600`; `runner.sh` uses `0700`.

```text
~/.codex/android-remote-sessions/<workspace-id>/
├── session.env
├── runner.sh
├── runner.lock
├── runner.protocol
├── runner.pid
├── runner.ready
├── runner.heartbeat
├── runner.expected.sha256
├── runner.active.sha256
├── dispatch.lock
├── queue.sequence
├── busy                         # compatibility/current-running hint only
├── current.log                  # symlink to latest command log
└── commands/
    ├── <id>.line                # immutable user command body
    ├── <id>.request.sha256      # command body + requested lock fingerprint
    ├── <id>.request-complete    # atomic registration commit point
    ├── <id>.queue-seq           # zero-padded monotonic FIFO sequence
    ├── <id>.lock-mode
    ├── <id>.events              # append-only transition journal
    ├── <id>.queued
    ├── <id>.running
    ├── <id>.terminal            # completed|failed|aborted|lost
    ├── <id>.completed|failed|aborted|lost
    ├── <id>.log
    └── <id>.exit
```

`busy` is not the authority for state and does not reject a second dispatch.
The fixed runner serializes the workspace queue. `project.lock` additionally
serializes every `--lock exclusive` command against other protocol-v2 clients.

## Command IDs and deduplication

Command IDs must match:

```text
^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$
```

Registration first prepares the command body, digest, lock mode, queued marker,
and initial event in a private `.register.<id>.*` staging directory. Under
`dispatch.lock`, it publishes those files and atomically renames
`<id>.request-complete` last. The runner consumes only requests with that commit
marker. An SSH interruption before the marker therefore cannot execute a
half-registered command; a later same-ID request may safely replace incomplete
non-running files. Registration never overwrites a committed command body or
terminal result.

- A new ID creates the immutable request and `queued` transition.
- The same ID with the same command and lock mode attaches to the existing
  queued, running, or terminal command. It never executes a second time.
- The same ID with a different command or lock mode fails with
  `COMMAND_ID_CONFLICT` and exit code `4`.

This makes a client retry after an uncertain SSH disconnect an attach operation,
not a second source edit or build.

Under `dispatch.lock`, every new request receives the next remote monotonic
sequence. The fixed runner selects the lowest sequence; command id is only a
legacy tie-breaker. Concurrent requests therefore run in registration order,
not glob or identifier order.

## State machine

```text
queued -> running -> completed
                  -> failed
                  -> aborted
                  -> lost
queued ----------> aborted       # explicit stop before execution
```

Terminal transition uses a per-command lock. Numeric `.exit` and state detail
are written first; create-once `.terminal` is the final commit marker, so a
crash cannot leave terminal-without-exit. Reconciliation repairs legacy
terminal-only remnants (`failed` repairs conservatively to rc `1`). Only one
terminal outcome wins.

- `completed`: command exit `0`
- `failed`: original non-zero command exit
- `aborted`: `130`
- `lost`: `125`

The runner writes commands through a file and sources them in the fixed Bash
runner from the canonical root. Successful commands' exported variables, functions,
and Android `envsetup`/`lunch` state therefore survive later command IDs and client
reconnects. A command controls its own `errexit` policy: an explicit `set -e` is
not suppressed or added by the channel. If it exits the runner on an unhandled
failure, the exit handler records `failed` with the original nonzero code and the
process releases its locks. A later ensure restarts the runner and consumes safe
queued commands without replaying the failed ID; the exited shell's environment
must be initialized again. For commands that return, protocol shell options,
private umask, and signal handlers are restored. The channel never injects
keystrokes into whichever pane happens to be active.

Runner reuse also proves the fixed `runner.0` pane is alive, its pane PID equals
`runner.pid`, and `runner.active.sha256` equals the expected payload digest. An
upgrade request while a committed command is running or `busy` returns
`RUNNER_UPGRADE_BLOCKED_ACTIVE` without changing terminal state or killing the
runner. Once idle, ensure replaces the old runner. Dead panes, stale/reused PIDs,
and incompatible idle payloads are never reported healthy.

After replacing an idle runner, the new pane retries `runner.lock` for a bounded
10-second handoff window because the killed tmux process may release its lock
slightly after `kill-session` returns. Ensure waits up to 15 seconds for
`runner.ready`. Exhausting the lock window exits without writing ready, so
ensure fails closed with `RUNNER_START_FAILED`.

## Reconciliation and stop

`ensure`, `run`, and `status` reconcile stale state.

- A `running` command with no live runner becomes `lost`.
- If an abort request exists, it becomes `aborted` instead.
- A stale compatibility `busy` hint is removed after the runner disappears.
- Safe `queued` commands remain queued and are consumed when `ensure` starts the
  fixed runner again.

`stop` writes an explicit stop request, marks commands that have not started as
`aborted`, terminates the current command with its fixed runner, kills the tmux
session, and reconciles any remaining running state. Historical logs and
terminal records are not deleted.

## Waiting and reconnecting

`run` waits by default. Waiting has a finite timeout:

```bash
remote-channel.sh ... run --wait-timeout 86400 --command-id build-123 -- COMMAND
```

The default is 86400 seconds and can be changed through
`CODEX_REMOTE_CHANNEL_WAIT_TIMEOUT`. Timeout returns `124` but does not cancel
the remote command. Re-run the same command ID and identical command to attach
and continue waiting, or use:

```bash
remote-channel.sh ... status --command-id build-123
remote-channel.sh ... tail --command-id build-123 --lines 160
```

`--no-wait` returns after durable queue registration.

## Actions

- `check`: verify canonical identity, SSH, `tmux`, `flock`, and remote root.
- `install-tmux`: explicitly install `tmux` when missing.
- `ensure`: create or reuse the canonical fixed runner.
- `run`: register or attach to a command and optionally wait.
- `status`: report workspace/runner/busy state and optional command state.
- `tail`: read `current.log` or a command-specific log.
- `stop`: abort active/queued work and stop the runner without deleting history.

`check` and `ensure` never install packages. `install-tmux` retains the existing
credential policy and is the only package-mutating action.

## SSH multiplexing

The Bash entry continues to use command-line-only OpenSSH multiplexing:

```text
ControlMaster=auto
ControlPersist=2h
ControlPath=$HOME/.ssh/controlmasters/codex-%C
```

This optimizes transport but does not participate in workspace identity.
Disable it with `CODEX_REMOTE_CHANNEL_SSH_MUX=0` when troubleshooting.

## Build-skill contract

Build/deploy callers must:

1. Resolve an SSH transport and candidate remote source root.
2. Let the channel canonicalize that workspace.
3. Use a stable command ID for retryable source edits, checkpoints, and builds.
4. Use `--lock exclusive` for every authoritative source mutation or build.
5. Treat `lost` as an uncertain result that requires inspection, not automatic
   re-execution under a new ID.
6. Interpret Android build output and verify artifact identity themselves.
7. Deploy only through local adb and hand requirement verification to the
   Framework workflow.
