# Android Component Routing

First decide whether the changed repository belongs to the target Android product
source. Seven layers classify eligible product-source Patches; they do not grant
upload eligibility to every Android project. An independently developed App (for
example, a SysPros source repository) has no Patch layer even if its APK is included
in the product image. Changes to the product source that integrate that APK are
separate eligible Patches. If source ownership is unresolved, do not submit a Patch.
The normal work paths are remote Android product-source customization via SSH and
local standalone App development followed by a push to SysPros. Route the task at
the start, then check the actual changed Git repository for Patch eligibility.
An existing product-source `.patch` may be submitted locally without an active
SSH session.

Examples: a Settings or SystemUI change in the target product source is an eligible
`application` Patch; a standalone SysPros App source diff is not a Patch; when one
feature also changes product APK integration, capture only that product Git diff
and keep the App diff in its own repository. Confirm the actual repository role in
each case; neither the repository name nor host alone proves it.

For each eligible Patch, the canonical selector is `component.layer`, never the
legacy ten-item domain list. Choose exactly one layer from:

```text
application | platform | native | hal | kernel | device | build
```

Classify every patch explicitly. Compatibility inputs map to the seven layers as
follows:

```text
framework             -> platform
system_app, app        -> application (only after product-source eligibility)
native                 -> native
hal                    -> hal
kernel, driver         -> kernel
device                 -> device
build                  -> build
```

The deprecated `--change-domain` input exists only for compatibility. `vendor` is not
a layer and is rejected as a layer selector. Do not infer native/hal/device from a
vendor path.

Use layer-specific evidence. A normal build
file touched beside an implementation does not move the change to the build layer;
choose build only when the build/release graph is itself the behavior. If one request
contains independently owned features with separate acceptance or rollback, split the
captures rather than hiding them under a broad label.

## Source Authority

Choose authority per repository, independently of the component:

- `registered_remote_tree`: registered or mounted by `android-source-access`. All
  Codex source/Git/build operations use `android-remote-channel`; the mount is not a
  local workspace.
- `local_project`: a real local Git project explicitly opened as the Codex workspace.
  A Samba/SMB/CIFS mount never qualifies.

## Build Route

Choose from the real project build mechanism rather than a layer label:

| Route | Use when | Executor |
| --- | --- | --- |
| `remote_profile` | Registered remote AOSP Soong/Make module or configured vendor full build | `android-remote-build-deploy` |
| `remote_project_command` | Registered remote Gradle, Kbuild, Bazel, or project-owned build | documented command through `android-remote-channel` |
| `local_project_command` | Real local project with its own wrapper/build entry | local project tools |

Record exact command/profile, exit status, artifact identity, verification, delivery,
and rollback. A successful build alone never proves the requirement.
