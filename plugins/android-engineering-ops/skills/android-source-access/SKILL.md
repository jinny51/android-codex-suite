---
name: android-source-access
description: "Use when mounting, restoring, diagnosing, or registering an Android remote source project from Windows, WSL, or macOS. Detects the actual host first, preserves existing .servers and Keychain identities, and dispatches exactly one in-plugin platform adapter."
---

# Android Source Access

Use this public Skill for Windows SMB, WSL/CIFS, and macOS/SMB source access. The implementation
is owned by `android-engineering-ops`; no host plugin is required.

## Remote-Only Source Contract

Mounted source remains the human CRUD and artifact bridge. Codex source, Git, capture,
and build operations use `android-remote-channel`.

## Task Startup

Before a new engineering task starts, set `PLUGIN_ROOT` to the directory two levels
above this `SKILL.md`, read `../../references/task-start.md`, and run its shared
`task_start.py` entry, which includes the first local install check. Reuse the same task ID/result across nested Skills;
do not update again at each build/remote command or while recovering running work.
This applies equally when this Skill is invoked directly without the full workflow.

## Active Install Family

Pure `--help`, host detection, and command listing may run without an installed-family
receipt. Before an adapter reads credentials/registry state or performs any network,
mount, registration, or filesystem action, set `PLUGIN_ROOT` to the directory two
levels above this `SKILL.md` and run:

```bash
python3 "$PLUGIN_ROOT/lib/android_engineering_ops/install_family.py" \
  --plugin-root "$PLUGIN_ROOT"
```

A nonzero result is a hard stop. Do not use a source checkout, a mixed legacy/target
installation, or a worker assertion as a substitute for this target-only receipt.

Detect the host that runs this chat's commands before selecting a command.
For a desktop connection to WSL, run this from that Ubuntu project with Linux
Python and the installed Skill from that connection. Choose the Windows native
route only for a chat executing on Windows. The Android build server remains a
separate registered target reached through the remote channel.

```bash
python3 "scripts/android_source_access.py" detect
python3 "scripts/android_source_access.py" list-commands
```

The dispatcher recognizes WSL only from WSL environment/kernel evidence and recognizes
macOS only from `platform.system() == Darwin`. Plain Linux and unknown systems fail
closed before an adapter command runs. A host-specific command invoked on the other host
also fails before side effects.

Use either the dispatcher or the stable command names in `scripts/`:

```bash
python3 "scripts/android_source_access.py" run mount-from-remote-path.sh -- \
  --remote-root /home/<remote-user>/work/<platform>/<project>
```

## Windows project registration

Use the native Windows Python command `python` for the dispatcher. Confirm the
SMB bridge for this project is accessible in Explorer and Windows OpenSSH
resolves the registered server alias. Use a project-specific UNC path or a
Windows mapped project directory. Run in PowerShell:

```powershell
python "$SkillDir/scripts/android_source_access.py" detect
python "$SkillDir/scripts/android_source_access.py" run register-project -- --ssh-host $SshHost --remote-root $RemoteRoot --local-path $LocalProjectPath --share $Share --project $SdkName --platform $Platform --project-id $ProjectId
```

The command checks the SSH channel identity, the actual SMB server and share,
and the canonical server-side directory before saving the mapping in
`$env:USERPROFILE/.servers/projects/<ssh-alias>.json`. Reuse the original project
ID when migrating. Remote source commands continue through `android-remote-channel`.

## Stable state

Read and update the existing identities in place:

- Windows registry: `$env:USERPROFILE/.servers/projects` and the existing Windows SSH identity.
- WSL and shared registry: `$HOME/.servers/projects`, `$HOME/.servers/credentials`
- macOS credentials: existing Keychain service identities
- human source and artifact bridge: `$HOME/work/<platform>/<project>`

Never copy, rename, or rewrite credentials as part of plugin migration. The mount is for
human CRUD and verified artifact delivery only. Every Codex source read, edit, Git/repo
operation, checkpoint, capture, or build on a registered remote tree goes through
`android-remote-channel`.

For WSL recognition and recovery detail, read `references/design.md` and
`references/manual-recovery.md`. For macOS registry, Keychain, mount, and restore detail,
read `references/macos-source-access.md`.
