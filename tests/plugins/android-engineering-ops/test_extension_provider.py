from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
CORE = ROOT / "plugins/android-engineering-ops"
JINNY = ROOT / "plugins/jinny-android-practices"
CORE_LIB = CORE / "lib"
if str(CORE_LIB) not in sys.path:
    sys.path.insert(0, str(CORE_LIB))

from android_engineering_ops.practices import (  # noqa: E402
    ExtensionResolutionError,
    ProviderValidationError,
    resolve_extension,
    validate_coding_decision,
    validate_execution_decision_for_resolution,
)
from android_engineering_ops.practices import provider as provider_module  # noqa: E402
from android_engineering_ops.practices.schema import validate_document  # noqa: E402


PROVIDER_PATH = JINNY / "contracts/android-practices-provider/v1/provider.json"
PROVIDER_SHA = hashlib.sha256(PROVIDER_PATH.read_bytes()).hexdigest()


def inventory(
    *,
    root: Path = JINNY,
    enabled: bool = True,
    name: str = "jinny-android-practices",
    version: str = "2.0.1",
    marketplace: str = "android-codex-suite",
    plugin_id: str | None = None,
) -> dict:
    return {
        "installed": [
            {
                "pluginId": plugin_id or f"{name}@{marketplace}",
                "name": name,
                "marketplaceName": marketplace,
                "version": version,
                "installed": True,
                "enabled": enabled,
                "source": {"source": "local", "path": str(root)},
            }
        ]
    }


def installed_inventory(
    tmp_path: Path,
    *,
    plugin_root: Path = JINNY,
    enabled: bool = True,
    name: str = "jinny-android-practices",
    version: str = "2.0.1",
    plugin_id: str | None = None,
) -> dict:
    marketplace = (
        "android-codex-suite"
        if name == "jinny-android-practices"
        else "test-marketplace"
    )
    source = tmp_path / "home/.tmp/marketplaces" / marketplace / "plugins" / name
    runtime = tmp_path / "home/plugins/cache" / marketplace / name / version
    for target in (source, runtime):
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(
            plugin_root,
            target,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    return inventory(
        root=source,
        enabled=enabled,
        name=name,
        version=version,
        marketplace=marketplace,
        plugin_id=plugin_id,
    )


def write_config(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[extension]\n" + body, encoding="utf-8")


def jinny_body() -> str:
    return (
        'mode = "jinny"\n'
        'provider_version = "2.0.1"\n'
        f'provider_manifest_sha256 = "{PROVIDER_SHA}"\n'
    )


def test_no_config_is_standalone_core_mode(tmp_path: Path) -> None:
    resolution = resolve_extension(project_root=tmp_path, codex_home=tmp_path / "home")
    assert resolution.mode == "none"
    assert resolution.capability(
        "execution", workflow_action="analysis", component_layer="platform"
    ).snapshot() == {"source": "core", "reason": "mode_none"}


def test_user_config_may_combine_closed_identity_and_extension_tables(
    tmp_path: Path,
) -> None:
    config = tmp_path / "home/android-engineering-ops.toml"
    config.parent.mkdir(parents=True)
    config.write_text(
        '[identity]\nmember_alias = "engineer01"\n\n'
        '[extension]\nmode = "none"\n',
        encoding="utf-8",
    )
    resolution = resolve_extension(project_root=tmp_path, codex_home=tmp_path / "home")
    assert resolution.mode == "none"

    config.write_text('[identity]\nmember_alias = "engineer01"\n', encoding="utf-8")
    resolution = resolve_extension(project_root=tmp_path, codex_home=tmp_path / "home")
    assert resolution.mode == "none"


def test_project_engineering_config_cannot_supply_identity(tmp_path: Path) -> None:
    config = tmp_path / ".codex/android-engineering.toml"
    config.parent.mkdir(parents=True)
    config.write_text(
        '[extension]\nmode = "none"\n\n'
        '[identity]\nmember_alias = "project-invented"\n',
        encoding="utf-8",
    )
    with pytest.raises(ExtensionResolutionError, match="unsupported table"):
        resolve_extension(project_root=tmp_path, codex_home=tmp_path / "home")


def test_project_config_precedes_user_and_binds_active_inventory(tmp_path: Path) -> None:
    write_config(tmp_path / "home/android-engineering-ops.toml", 'mode = "none"\n')
    write_config(tmp_path / ".codex/android-engineering.toml", jinny_body())
    resolution = resolve_extension(
        project_root=tmp_path,
        codex_home=tmp_path / "home",
        inventory=installed_inventory(tmp_path),
    )
    assert resolution.mode == "jinny"
    assert (
        resolution.active_plugin_id
        == "jinny-android-practices@android-codex-suite"
    )
    assert resolution.provider_manifest_path == (
        tmp_path
        / "home/plugins/cache/android-codex-suite/jinny-android-practices/2.0.1"
        / "contracts/android-practices-provider/v1/provider.json"
    )
    assert resolution.active_plugin_source_root != resolution.active_plugin_root
    assert resolution.source_plugin_manifest_sha256 == resolution.execution_plugin_manifest_sha256
    assert resolution.provider_manifest_sha256 == PROVIDER_SHA
    assert set(resolution.skills) == {"coding", "execution"}
    assert all(skill.skill_sha256 for skill in resolution.skills.values())


def test_dangling_project_or_user_config_symlink_fails_closed(tmp_path: Path) -> None:
    user = tmp_path / "home/android-engineering-ops.toml"
    write_config(user, 'mode = "none"\n')
    project = tmp_path / ".codex/android-engineering.toml"
    project.parent.mkdir(parents=True)
    project.symlink_to(tmp_path / "missing-project-config.toml")

    with pytest.raises(ExtensionResolutionError, match="contains a symlink"):
        resolve_extension(project_root=tmp_path, codex_home=tmp_path / "home")

    project.unlink()
    user.unlink()
    user.symlink_to(tmp_path / "missing-user-config.toml")
    with pytest.raises(ExtensionResolutionError, match="contains a symlink"):
        resolve_extension(project_root=tmp_path, codex_home=tmp_path / "home")


def test_dependency_free_frozen_toml_fallback_resolves_without_tomllib(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_config(tmp_path / ".codex/android-engineering.toml", jinny_body())
    monkeypatch.setattr(provider_module, "_stdlib_tomllib", None)
    resolution = resolve_extension(
        project_root=tmp_path,
        codex_home=tmp_path / "home",
        inventory=installed_inventory(tmp_path),
    )
    assert resolution.mode == "jinny"
    assert resolution.provider_manifest_sha256 == PROVIDER_SHA


@pytest.mark.parametrize(
    "content",
    [
        '[extension]\nmode = "none"\nmode = "none"\n',
        '[extension]\nmode = "none" garbage\n',
        '[extension]\nmode = "none" # inline comments are outside the frozen subset\n',
        '[extension]\nmode = "none"\n[extension]\n',
        '[extension]\nmode = 1\n',
        'mode = "none"\n[extension]\n',
        '[extension.extra]\nmode = "none"\n',
    ],
)
def test_frozen_toml_subset_rejects_duplicate_or_malformed_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content: str,
) -> None:
    config = tmp_path / ".codex/android-engineering.toml"
    config.parent.mkdir(parents=True)
    config.write_text(content, encoding="utf-8")
    monkeypatch.setattr(provider_module, "_stdlib_tomllib", None)
    with pytest.raises(ExtensionResolutionError):
        resolve_extension(project_root=tmp_path, codex_home=tmp_path / "home")


@pytest.mark.parametrize(
    "body",
    [
        'mode = "none"\nprovider_version = "2.0.1"\n',
        jinny_body() + 'provider_id = "jinny-android-practices"\n',
        jinny_body() + 'plugin_id = "jinny-android-practices@test"\n',
        jinny_body() + f'provider_manifest_path = "{PROVIDER_PATH}"\n',
        (
            'mode = "custom"\nplugin_name = "custom-plugin"\n'
            'provider_version = "2.0.1"\n'
            f'provider_manifest_sha256 = "{PROVIDER_SHA}"\n'
        ),
    ],
)
def test_mode_config_fields_are_exact_and_arbitrary_paths_are_forbidden(
    tmp_path: Path, body: str,
) -> None:
    write_config(tmp_path / ".codex/android-engineering.toml", body)
    with pytest.raises(ExtensionResolutionError, match="requires exactly|shape is invalid"):
        resolve_extension(project_root=tmp_path, codex_home=tmp_path / "home", inventory=inventory())


def test_disabled_or_hash_mismatched_selected_provider_fails_closed(tmp_path: Path) -> None:
    write_config(tmp_path / ".codex/android-engineering.toml", jinny_body())
    with pytest.raises(ProviderValidationError, match="not active"):
        resolve_extension(
            project_root=tmp_path,
            codex_home=tmp_path / "home",
            inventory=inventory(enabled=False),
        )
    write_config(
        tmp_path / ".codex/android-engineering.toml",
        jinny_body().replace(PROVIDER_SHA, "0" * 64),
    )
    with pytest.raises(ProviderValidationError, match="SHA-256 differs"):
        resolve_extension(
            project_root=tmp_path,
            codex_home=tmp_path / "home",
            inventory=installed_inventory(tmp_path),
        )


def test_inventory_root_symlink_is_rejected_before_provider_read(tmp_path: Path) -> None:
    write_config(tmp_path / ".codex/android-engineering.toml", jinny_body())
    link = tmp_path / "provider-link"
    link.symlink_to(JINNY, target_is_directory=True)
    runtime = (
        tmp_path
        / "home/plugins/cache/android-codex-suite/jinny-android-practices/2.0.1"
    )
    runtime.parent.mkdir(parents=True)
    shutil.copytree(JINNY, runtime)
    with pytest.raises(ProviderValidationError, match="contains a symlink"):
        resolve_extension(
            project_root=tmp_path,
            codex_home=tmp_path / "home",
            inventory=inventory(root=link),
        )


def test_inventory_source_and_runtime_cache_divergence_fails_closed(tmp_path: Path) -> None:
    write_config(tmp_path / ".codex/android-engineering.toml", jinny_body())
    selected = installed_inventory(tmp_path)
    source = Path(selected["installed"][0]["source"]["path"])
    source_provider = source / "contracts/android-practices-provider/v1/provider.json"
    source_provider.write_bytes(source_provider.read_bytes() + b"\n")
    with pytest.raises(ProviderValidationError, match="provider manifest bytes differ"):
        resolve_extension(
            project_root=tmp_path,
            codex_home=tmp_path / "home",
            inventory=selected,
        )


def test_runtime_cache_content_hash_and_inventory_identity_fail_closed(tmp_path: Path) -> None:
    write_config(tmp_path / ".codex/android-engineering.toml", jinny_body())
    selected = installed_inventory(tmp_path)
    runtime_skill = (
        tmp_path
        / "home/plugins/cache/android-codex-suite/jinny-android-practices/2.0.1"
        / "skills/jinny-android-coding-practices/SKILL.md"
    )
    runtime_skill.write_bytes(runtime_skill.read_bytes() + b"\nchanged\n")
    with pytest.raises(ProviderValidationError, match="Skill SHA-256 differs"):
        resolve_extension(
            project_root=tmp_path,
            codex_home=tmp_path / "home",
            inventory=selected,
        )
    selected["installed"][0]["pluginId"] = "wrong@android-codex-suite"
    with pytest.raises(ProviderValidationError, match="supported installed source"):
        resolve_extension(
            project_root=tmp_path,
            codex_home=tmp_path / "home",
            inventory=selected,
        )


def test_duplicate_inventory_and_symlinked_runtime_cache_are_rejected(tmp_path: Path) -> None:
    write_config(tmp_path / ".codex/android-engineering.toml", jinny_body())
    selected = installed_inventory(tmp_path)
    selected["installed"].append(dict(selected["installed"][0]))
    with pytest.raises(ProviderValidationError, match="ambiguous"):
        resolve_extension(
            project_root=tmp_path,
            codex_home=tmp_path / "home",
            inventory=selected,
        )

    other = tmp_path / "symlink-case"
    write_config(other / ".codex/android-engineering.toml", jinny_body())
    source = other / "home/.tmp/marketplaces/android-codex-suite/plugins/jinny-android-practices"
    source.parent.mkdir(parents=True)
    shutil.copytree(JINNY, source)
    runtime = other / "home/plugins/cache/android-codex-suite/jinny-android-practices/2.0.1"
    runtime.parent.mkdir(parents=True)
    runtime.symlink_to(JINNY, target_is_directory=True)
    with pytest.raises(ProviderValidationError, match="contains a symlink"):
        resolve_extension(
            project_root=other,
            codex_home=other / "home",
            inventory=inventory(root=source),
        )


def test_provider_plugin_declaring_write_is_rejected(tmp_path: Path) -> None:
    untrusted = tmp_path / "write-capable-provider"
    shutil.copytree(JINNY, untrusted)
    plugin_path = untrusted / ".codex-plugin/plugin.json"
    plugin = json.loads(plugin_path.read_text(encoding="utf-8"))
    plugin["interface"]["capabilities"].append("Write")
    plugin_path.write_text(json.dumps(plugin) + "\n", encoding="utf-8")
    write_config(tmp_path / ".codex/android-engineering.toml", jinny_body())
    with pytest.raises(ProviderValidationError, match="must not include Write"):
        resolve_extension(
            project_root=tmp_path,
            codex_home=tmp_path / "home",
            inventory=installed_inventory(tmp_path, plugin_root=untrusted),
        )


@pytest.mark.parametrize(
    ("relative_path", "expected_error"),
    [
        (
            "skills/jinny-android-coding-practices/SKILL.md",
            "declared coding Skill SHA-256 differs",
        ),
        (
            "skills/jinny-android-coding-practices/agents/openai.yaml",
            "declared coding agent metadata SHA-256 differs",
        ),
        (
            "skills/jinny-android-coding-practices/scripts/jinny_coding_policy.py",
            "declared coding decision entrypoint SHA-256 differs",
        ),
    ],
)
def test_selected_provider_content_is_hash_bound(
    tmp_path: Path, relative_path: str, expected_error: str,
) -> None:
    tampered = tmp_path / "tampered-provider"
    shutil.copytree(JINNY, tampered)
    content = tampered / relative_path
    content.write_bytes(content.read_bytes() + b"\n# tampered\n")
    write_config(tmp_path / ".codex/android-engineering.toml", jinny_body())
    with pytest.raises(ProviderValidationError, match=expected_error):
        resolve_extension(
            project_root=tmp_path,
            codex_home=tmp_path / "home",
            inventory=installed_inventory(tmp_path, plugin_root=tampered),
        )


def test_applicability_miss_falls_back_but_selected_invalid_never_does(tmp_path: Path) -> None:
    write_config(tmp_path / ".codex/android-engineering.toml", jinny_body())
    resolution = resolve_extension(
        project_root=tmp_path,
        codex_home=tmp_path / "home",
        inventory=installed_inventory(tmp_path),
    )
    assert resolution.capability(
        "coding", workflow_action="diagnosis", component_layer="platform"
    ).snapshot() == {"source": "core", "reason": "applicability_miss"}
    assert resolution.capability(
        "execution", workflow_action="diagnosis", component_layer="platform"
    ).source == "provider"


def test_custom_mode_separates_inventory_plugin_name_from_provider_id(tmp_path: Path) -> None:
    custom = tmp_path / "custom-plugin-root"
    shutil.copytree(JINNY, custom)
    plugin_path = custom / ".codex-plugin/plugin.json"
    plugin = json.loads(plugin_path.read_text(encoding="utf-8"))
    plugin["name"] = "custom-provider-plugin"
    plugin_path.write_text(json.dumps(plugin) + "\n", encoding="utf-8")
    provider_path = custom / "contracts/android-practices-provider/v1/provider.json"
    provider = json.loads(provider_path.read_text(encoding="utf-8"))
    provider["provider_id"] = "acme-android-practices"
    provider_path.write_text(json.dumps(provider) + "\n", encoding="utf-8")
    digest = hashlib.sha256(provider_path.read_bytes()).hexdigest()
    write_config(
        tmp_path / ".codex/android-engineering.toml",
        (
            'mode = "custom"\n'
            'plugin_name = "custom-provider-plugin"\n'
            'provider_id = "acme-android-practices"\n'
            'provider_version = "2.0.1"\n'
            f'provider_manifest_sha256 = "{digest}"\n'
        ),
    )
    custom_inventory = installed_inventory(
        tmp_path, plugin_root=custom, name="custom-provider-plugin"
    )
    resolution = resolve_extension(
        project_root=tmp_path,
        codex_home=tmp_path / "home",
        inventory=custom_inventory,
    )
    assert resolution.active_plugin_id == "custom-provider-plugin@test-marketplace"
    assert resolution.provider["provider_id"] == "acme-android-practices"


def test_packaged_schemas_validate_provider_and_bound_decisions(tmp_path: Path) -> None:
    provider = json.loads(PROVIDER_PATH.read_text(encoding="utf-8"))
    validate_document(
        provider,
        CORE / "contracts/android-practices-provider/v1/provider.schema.json",
    )
    write_config(tmp_path / ".codex/android-engineering.toml", jinny_body())
    resolution = resolve_extension(
        project_root=tmp_path,
        codex_home=tmp_path / "home",
        inventory=installed_inventory(tmp_path),
    )
    coding_binding = resolution.capability(
        "coding", workflow_action="implementation", component_layer="platform"
    )
    coding = {
        "schema": "coding-policy-decision-v1",
        "decision_id": "decision-1",
        "run_id": "run-1",
        "stage_id": "stage-1",
        "context_sha256": "1" * 64,
        "core_policy_sha256": "2" * 64,
        "provider": {
            key: getattr(coding_binding, key)
            for key in (
                "provider_id", "provider_version", "provider_manifest_sha256",
                "skill_id", "skill_version", "skill_sha256",
                "agent_metadata_sha256", "decision_entrypoint_sha256",
            )
        },
        "outcome": {
            "type": "applied",
            "rules": [
                {
                    "rule_id": "jinny-rule-1",
                    "effect": "recommend",
                    "statement": "Keep one narrow helper boundary.",
                    "scope": {
                        "component_layers": ["platform"],
                        "path_patterns": [],
                        "languages": ["java"],
                    },
                    "evidence_requirements": [],
                }
            ],
        },
        "created_at": "2026-09-03T00:00:00Z",
    }
    validate_coding_decision(
        coding,
        binding=coding_binding,
        core_policy_sha256="2" * 64,
        expected_decision_id="decision-1",
        expected_run_id="run-1",
        expected_stage_id="stage-1",
        expected_context_sha256="1" * 64,
    )
    for field, expected in (
        ("decision_id", "decision-1"),
        ("run_id", "run-1"),
        ("stage_id", "stage-1"),
        ("context_sha256", "1" * 64),
    ):
        replay = json.loads(json.dumps(coding))
        replay[field] = "0" * 64 if field == "context_sha256" else f"old-{field}"
        with pytest.raises(ProviderValidationError, match=f"expected {field}"):
            validate_coding_decision(
                replay,
                binding=coding_binding,
                core_policy_sha256="2" * 64,
                expected_decision_id="decision-1",
                expected_run_id="run-1",
                expected_stage_id="stage-1",
                expected_context_sha256="1" * 64,
            )
    execution_binding = resolution.capability(
        "execution", workflow_action="implementation", component_layer="platform"
    )
    execution = {
        "schema": "execution-policy-decision-v1",
        "decision_id": "decision-2",
        "run_id": "run-1",
        "stage_id": "stage-2",
        "context_sha256": "3" * 64,
        "provider": {
            key: getattr(execution_binding, key)
            for key in (
                "provider_id", "provider_version", "provider_manifest_sha256",
                "skill_id", "skill_version", "skill_sha256",
                "agent_metadata_sha256", "decision_entrypoint_sha256",
            )
        },
        "outcome": {
            "type": "delegate",
            "worker_profile_id": "terra-implementation",
            "task_class": "implementation",
            "requested_effect": "workspace_mutation",
            "reason_codes": ["workspace-bounded-implementation"],
            "independent_review_requested": False,
        },
        "created_at": "2026-09-03T00:00:00Z",
    }
    with pytest.raises(ProviderValidationError, match="rollout ceiling"):
        validate_execution_decision_for_resolution(
            execution,
            resolution=resolution,
            binding=execution_binding,
            rollout_effect_ceiling="read_only",
            expected_decision_id="decision-2",
            expected_run_id="run-1",
            expected_stage_id="stage-2",
            expected_context_sha256="3" * 64,
        )
    validate_execution_decision_for_resolution(
        execution,
        resolution=resolution,
        binding=execution_binding,
        rollout_effect_ceiling="workspace_mutation",
        expected_decision_id="decision-2",
        expected_run_id="run-1",
        expected_stage_id="stage-2",
        expected_context_sha256="3" * 64,
    )
    for field in ("decision_id", "run_id", "stage_id", "context_sha256"):
        replay = json.loads(json.dumps(execution))
        replay[field] = "0" * 64 if field == "context_sha256" else f"old-{field}"
        with pytest.raises(ProviderValidationError, match=f"expected {field}"):
            validate_execution_decision_for_resolution(
                replay,
                resolution=resolution,
                binding=execution_binding,
                rollout_effect_ceiling="workspace_mutation",
                expected_decision_id="decision-2",
                expected_run_id="run-1",
                expected_stage_id="stage-2",
                expected_context_sha256="3" * 64,
            )


def test_model_names_do_not_leak_into_engineering_core() -> None:
    forbidden = ("gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna")
    for path in CORE.rglob("*"):
        if path.is_file() and path.suffix in {".py", ".md", ".json", ".yaml", ".toml"}:
            text = path.read_text(encoding="utf-8")
            assert not any(token in text for token in forbidden), path
