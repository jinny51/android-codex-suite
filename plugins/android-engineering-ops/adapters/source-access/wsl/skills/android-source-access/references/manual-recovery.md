# Manual Recovery Flow

Use this only when `scripts/mount-from-remote-path.sh` fails and a narrower step is needed.

## Inspect Mounts

```bash
findmnt -t cifs -o TARGET,SOURCE,FSTYPE,OPTIONS
```

## Derive The Plan

```bash
PLUGIN_ROOT="<path-to-installed-android-engineering-ops>"
SKILL_DIR="$PLUGIN_ROOT/adapters/source-access/wsl/skills/android-source-access"
python3 "$PLUGIN_ROOT/lib/android_engineering_ops/install_family.py" \
  --plugin-root "$PLUGIN_ROOT" &&
"$SKILL_DIR/scripts/plan-from-remote-path.sh" \
  --remote-root /home/test61/mtk/tb8788p1 \
  > /tmp/android-source-plan.env
source /tmp/android-source-plan.env
```

## Install SSH Key When Needed

```bash
read -rsp "Server password: " SERVER_PASSWORD; echo
SSHPASS="$SERVER_PASSWORD" "$SKILL_DIR/scripts/install-ssh-key.sh" \
  --ssh-host "$SSH_HOST"
```

If no local public key exists, ask before generating one, then rerun with `--generate-key`.

## Inspect SDK Identity

Inspect the SDK root before choosing the local platform and project name. If
inspection cannot determine either value, stop and ask the user instead of using
the remote path segments as a fallback. If explicit user input conflicts with
source evidence, stop and ask which value to use. After the user confirms, rerun
with `--accept-platform-conflict` or `--accept-sdk-name-conflict` together with
the explicit value.

```bash
python3 "$PLUGIN_ROOT/lib/android_engineering_ops/install_family.py" \
  --plugin-root "$PLUGIN_ROOT" &&
"$SKILL_DIR/scripts/inspect-android-sdk.sh" \
  --ssh-host "$SSH_HOST" \
  --remote-root "$REMOTE_ROOT" \
  > /tmp/android-source-project.env
source /tmp/android-source-project.env
LOCAL_PLATFORM="$HOME/work/$PLATFORM"
LOCAL_PROJECT="$LOCAL_PLATFORM/$SDK_NAME"
```

## Discover Or Create Project Share

```bash
python3 "$PLUGIN_ROOT/lib/android_engineering_ops/install_family.py" \
  --plugin-root "$PLUGIN_ROOT" &&
"$SKILL_DIR/scripts/discover-samba-share.sh" \
  --ssh-host "$SSH_HOST" \
  --remote-root "$REMOTE_ROOT" \
  > /tmp/android-source-cifs.env
source /tmp/android-source-cifs.env
```

Use `SAMBA_PROJECT_URL` for the default project-level mount. If no Samba share
covers the remote SDK root, create a project-level share for the SDK root:

```bash
"$SKILL_DIR/scripts/ensure-samba-share.sh" \
  --ssh-host "$SSH_HOST" \
  --remote-root "$REMOTE_ROOT" \
  --share-name "$SDK_NAME" \
  --share-path "$REMOTE_ROOT" \
  --apply

python3 "$PLUGIN_ROOT/lib/android_engineering_ops/install_family.py" \
  --plugin-root "$PLUGIN_ROOT" &&
"$SKILL_DIR/scripts/discover-samba-share.sh" \
  --ssh-host "$SSH_HOST" \
  --remote-root "$REMOTE_ROOT" \
  > /tmp/android-source-cifs.env
source /tmp/android-source-cifs.env
```

Parent/platform shares are explicit exceptions only. Do not mount
`SAMBA_SHARE_URL` to the local platform directory during normal recovery.

## Mount Project Share

```bash
SAMBA_PASSWORD="$SERVER_PASSWORD" "$SKILL_DIR/scripts/mount-platform.sh" \
  --platform "$PLATFORM" \
  --share "$SAMBA_PROJECT_URL" \
  --target "$LOCAL_PROJECT" \
  --user "$SAMBA_USER"
```

## Verify Project

Check mount metadata only. Reuse the SDK identity returned by
`inspect-android-sdk.sh` above; it inspects source through `android-remote-channel`.
If identity needs refreshing, rerun that inspector, not a directory probe of the
mounted Android tree.

```bash
findmnt -T "$LOCAL_PROJECT"
```

## Remember For Recovery

Tell the user first that Samba credentials will be saved locally under `$HOME/.servers/credentials/` with mode `600`, then store mount metadata, remote mapping, and credentials:

```bash
SAMBA_PASSWORD="$SERVER_PASSWORD" "$SKILL_DIR/scripts/restore-project-mount.sh" \
  --project "$LOCAL_PROJECT" \
  --ssh-host "$SSH_HOST" \
  --remote-root "$REMOTE_ROOT" \
  --platform "$PLATFORM" \
  --sdk-name "$SDK_NAME" \
  --remember-current \
  --remember-password
unset SERVER_PASSWORD
```
