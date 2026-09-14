# Android patch capture contract

The capture directory is an engineering handoff to `akbs-patch-submit`, not a
second AKBS incoming schema.

Its `manifest.json` keeps the established capture facts and must contain:

```json
{
  "package_type": "android_feature_patch",
  "components": [
    {
      "layer": "platform",
      "patches": ["patches/rk14-frameworks-base@example.patch"]
    }
  ],
  "patches": [
    {
      "path": "patches/rk14-frameworks-base@example.patch",
      "layer": "platform"
    }
  ]
}
```

Allowed layers are `application`, `platform`, `native`, `hal`, `kernel`,
`device`, and `build`. Each patch path appears exactly once in `components`
and its patch row carries the same layer.

One package may represent one coherent change across several Git repositories and
layers. Each patch file is nevertheless repository-scoped: generate one patch from
each actual Git repository and keep the same change id after `@` when the patches
serve the same goal. A combined Android-source-root patch and `repo_path: "."` are
not valid substitutes for repository patches.

The final package is constructed by `akbs-patch-submit` and uses only:

```text
knowledge-incoming-package / schema_version 1 / android_change
```

The final `components` rows contain only `layer` and `patches`. Evidence
membership is defined by `files.evidence`; every evidence JSON file's own
`kind` is authoritative.

Do not extend component rows beyond `layer` and `patches`, and do not add another
incoming schema or upload path.
