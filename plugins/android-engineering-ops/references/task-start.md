# Engineering task startup

Run the shared engineering startup entry once before starting a new engineering task,
whether the user enters the full workflow or directly asks for source access, a build,
change review, or capture. It first checks the active installation, then checks the
official engineering release and updates only this plugin when needed.

```bash
python3 "$PLUGIN_ROOT/lib/android_engineering_ops/task_start.py" \
  --task-id "$ENGINEERING_TASK_ID"
```

Codex chooses one stable ID for the coherent user request and keeps it with task state;
reuse it for all nested Skills, commands and subagents. Use an existing task ID when
available, otherwise choose one once. Do not ask the member to manage this ID,
generate a new one for each command, or reuse a permanent chat/project ID for unrelated
requirements. State stays under `$CODEX_HOME/artifacts/android-engineering-ops/startup`;
it does not enter Android source, credentials, capture or incoming packages.

- `PASS`: start work. Later calls with the same task/root/version reuse the result,
  including when `--retry` is supplied. There is no TTL or background update.
- `UPDATED_RESTART_REQUIRED`: files were updated but this Codex session still has old
  instructions. Stop before business work; ask the member to exit and restart Codex.
  In the restarted session resolve the newly loaded plugin root and repeat startup with
  the same task ID. Do not execute scripts from a guessed newer cache or claim hot reload.
- `CHECK_FAILED` / `UPDATE_FAILED`: report the actual failed stage. The result is retained;
  do not retry implicitly at every command. After correcting the cause, use `--retry`.
- `STARTUP_BUSY`: another startup is updating/checking; no work was started. Retry only
  after that operation completes, without launching a background waiter.
- A changed active installation, damaged state or a different root/version for an
  already-started task stops safely. A saved startup result cannot override local guards.

Do not perform startup updates while recovering an already-running build or command.
Use the existing local install check and command status/tail to reach a safe boundary;
do not create a new task ID to turn recovery into a fresh update opportunity.
Low-level source, remote, build and capture commands retain their local-only install
guards. They never query releases or install plugins. An update in another Codex task
can invalidate the current active installation; report the drift without bypassing the
guard or silently switching this task's execution version.

The generic release lookup, version comparison and marketplace update implementation
is maintained at `shared/codex_plugin_update.py` in the source repository and packaged
identically in both plugins. This startup entry owns only engineering task timing and
state. It does not import AKBS member code, install the member plugin, or change an
optional orchestration extension. Existing users must install this release once before this
automatic engineering startup behavior becomes available.
