# AKBS Deterministic Rules Contract

`akbs_member_ops.knowledge_rules` is the single source inside `akbs-member-ops` for deterministic member-package knowledge rules. It must remain pure Python and must not depend on Codex session state, plugin cache locations, network calls, AI inference, or production database state.

Engineering change attribution and domain coding policy have a separate owner in
`android-engineering-ops`. The incoming v1 adapter keeps only the frozen marker grammar
needed to validate historical v1 material; it is not a second public policy owner.

## Shared Rule Families

| Rule family | Representative functions |
| --- | --- |
| Source capability versions | `current_plugin_version`, `source_version_compatibility_matrix`, `source_version_errors` |
| Project normalization | `valid_project_model`, `find_company_project`, `complete_company_project_with_platform`, `parse_company_project` |
| Platform and Android version parsing | `parse_known_platform_token`, `find_platform_tokens`, `normalize_android_version` |
| Patch asset names | `patch_asset_name_prefix`, `has_uncontrolled_patch_asset_prefix`, `classify_patch_asset_names` |
| Function scope | `aggregate_package_scope_errors`, `classify_function_scope`, `curation_text_missing_fields` |
| Pre-change search evidence | `classify_pre_change_search`, `normalize_reuse_decision`, `search_results_need_usage_decision` |
| Patch package quality | `framework_metadata_is_traceable`, patch asset and verification validators |
| Upload text quality | `text_field_quality_errors`, `template_leak_errors`, `future_run_id_errors` |

Member generation and upload preflight import this module directly from `akbs-member-ops`.

The rules source is release-version independent. `current_plugin_version()` reads
the enclosing `.codex-plugin/plugin.json` when the module is running inside an
installed or checked-out plugin. A rules-only maintainer snapshot has no plugin
manifest, so the function returns an empty value instead of embedding or looking
up a dynamic "latest" plugin release. Member `source.json` version provenance is
owned by the plugin manifest/cache version gate, not by a constant in this rules
module. Capability minimum versions remain stable historical thresholds.

The local `akbs-curation-maintainer` executes the immutable rules snapshot carried
by the submitted bundle. The bundle manifest records the source plugin version,
source plugin commit, and rules-source content hash; those are provenance for the
snapshot, while the rules source itself embeds no plugin release version. The
maintainer must not import a mutable plugin worktree or resolve a dynamic latest
rules file, and it must not reimplement project, platform, Android version,
aggregate, search, package-quality, or patch-asset rules.

The Linux/test35 AKBS service owns HTTP authentication, storage, active SQLite transactions, queue state, and API validation. It neither loads the Codex plugin cache nor keeps a generated copy of this module.

## Member-Only Behavior

- Plugin install/update and current-session cache gates.
- Local report and patch-package generation.
- Read and complete server-owned non-patch information requests on the same patch package.
- Evidence collection from Codex sessions and developer workspaces.
- Platform source access and remote build/deploy orchestration.
- Upload preflight and actionable member prompts.

## Maintainer-Only Behavior

- AI decisions for new knowledge or planned merge after queue admission.
- Knowledge validity, applicability, confidence, and risk assessment.
- Active SQLite read-only curation analysis and materialization plans.
- Team aggregation and main-control operation.

## Server-Only Behavior

- HTTP authentication and package receipt.
- Active SQLite persistence, lifecycle transitions, and transactional writes.
- Admin/member API read models and UI data.
- Service deployment and runtime health.

Do not add an exporter or copied server rule module. A rule that must be shared between member generation and local curation belongs in `knowledge_rules.py`; a server state rule belongs in the AKBS system.
