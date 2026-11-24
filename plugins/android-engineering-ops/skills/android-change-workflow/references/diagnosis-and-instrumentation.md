# Diagnosis And Instrumentation

Read this when source inspection is insufficient, when root cause is unclear, or when temporary diagnostics are needed.

## Evidence Ladder

Prefer stronger evidence when risk is high:

1. Source path and ownership proof.
2. Reproducible observation on target device.
3. Focused logcat slice.
4. Dumpsys snapshot before/after the action.
5. Screen recording or screenshots for visual/timing issues.
6. Instrumented log proving state, branch, caller, and timing.
7. Rebuilt/deployed artifact plus repeated device verification.

Do not move from unclear behavior to code edit on intuition alone. For direct requirements, use a requirement contract; for bugs/regressions, use evidence.

## Source Tracing Pattern

Trace in this order:

1. Entry point: user action, broadcast, service call, binder call, input event, lifecycle callback, resource lookup, or config observer.
2. Process boundary: caller process, callee process, binder interface, permission, identity, user/profile.
3. State owner: class/service that owns the deciding state.
4. Gate checks: feature flag, DeviceConfig, Settings, resource bool/dimen, build/product variant, user unlock, display/focus state.
5. Mutation: window/surface/task/package/resource/input state actually changed.
6. Observer: component expected to react and why it may not.

## Diagnostic Log Design

A useful log answers one uncertainty. Include only fields needed to distinguish hypotheses:

- Tag/keyword stable enough for `logcat_slice.py`.
- Operation/reason/caller.
- Relevant IDs: userId, displayId, taskId, window token, package, uid, pid, profile id.
- State gates: flags, bounds, focus, visibility, lifecycle state, config, feature flag value.
- Timing: action start/end, sequence number, transaction id, or elapsed time when ordering matters.
- Thread/process when concurrency or wrong owner is plausible.

Avoid sensitive user data and broad object dumps.

## Visual Evidence

Use recording or screenshots for:

- Flicker, stale surface, overlay residue, wrong z-order, wrong bounds.
- Animation timing, transition race, resize/move artifact.
- Input focus, touch target, split-screen, PiP, multi-display, or rotation visible failures.

Align logs with frames. If exact timestamps are unavailable, use an obvious marker action or log immediately before and after the visible action.

Use `scripts/extract_video_frames.py` to sample frames when `ffmpeg` is available.

## Dumpsys Capture

On native Windows, run `adb shell dumpsys` directly from PowerShell for targeted snapshots.
On WSL and macOS, use `scripts/collect_diagnostics.sh` or direct `adb shell dumpsys`. Useful sections include:

- `activity activities`, `activity displays`, `activity service`, or `activity recents`.
- `window`, `input`, `SurfaceFlinger`, `display`, `package <pkg>`.
- `device_config list`, `settings get`, or overlay/package state when relevant.

Capture before and after when the behavior depends on a state transition.

`collect_diagnostics.sh` requires an initialized AKBS outputs contract. Its default capture starts in controlled `outputs/tmp`, then atomically promotes a successful run to `$AKBS_ROOT/outputs/diagnostics/android-change-workflow/<run-id>`, writes `_manifest.json`, and rebuilds `outputs/manifests/catalog.jsonl`. Any explicit output must be a new directory outside plugin source and cache paths. It removes only a marker-owned invocation on failure or a controlled interrupt. An existing directory, missing/mismatched marker, or canonical/symlink path change fails closed.

## Temporary Diagnostic Lifecycle

Mark temporary logs with a searchable token such as `TODO_DIAG`, `TEMP_DIAG`, or a unique issue keyword.

Before final reporting:

1. Search changed files for temporary tags and suspicious debug logs.
2. Remove logs that only served diagnosis.
3. Guard logs that are useful but noisy.
4. Downgrade logs if they should remain for future field debugging.
5. Keep permanent logs only when they add operational value and match local logging style.

Use `scripts/diagnostic_log_audit.py --ssh-host <host> --remote-root <root>
--path <changed-file>` as a bounded final sweep through `android-remote-channel`.
Repeat `--path` for all changed source files; never pass a mounted source root.
