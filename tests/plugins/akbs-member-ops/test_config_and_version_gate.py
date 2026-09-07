from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
PLUGIN = ROOT / "plugins" / "akbs-member-ops"
PLUGIN_VERSION = json.loads((PLUGIN / ".codex-plugin" / "plugin.json").read_text())["version"]
sys.path.insert(0, str(PLUGIN / "lib"))
sys.path.insert(0, str(PLUGIN / "internal" / "incoming-v1" / "scripts"))

from akbs_intake import config, version_gate  # noqa: E402
from akbs_intake.reports import gms  # noqa: E402
from akbs_member_ops.knowledge_search import config as search_config  # noqa: E402
from akbs_member_ops.member import profile as member_profile  # noqa: E402


def completed(payload: object, returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        ["codex", "plugin", "list", "--json"],
        returncode,
        stdout=json.dumps(payload) if returncode == 0 else "",
        stderr="" if returncode == 0 else "unavailable",
    )


class MemberConfigTest(unittest.TestCase):
    def test_target_presence_excludes_every_legacy_config_read(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, mock.patch.dict(
            os.environ,
            {"CODEX_HOME": temporary, "CODEX_REPORT_MEMBER_ALIAS": "environment-member"},
            clear=True,
        ):
            home = Path(temporary)
            (home / "android-knowledge-intake.toml").write_text(
                'member_alias = "legacy"\nmember_alias = "malformed"\n',
                encoding="utf-8",
            )
            (home / "akbs-member-ops.toml").write_text(
                'default_profile = "member1"\n[profiles.member1]\n'
                'member_alias = "member1"\nmember_name = "Member One"\n'
                'knowledge_repo_worktree = "/target"\n',
                encoding="utf-8",
            )
            with mock.patch.object(
                config,
                "find_project_report_config",
                side_effect=AssertionError("legacy discovery must not run"),
            ):
                loaded, paths = config.load_config()
            self.assertEqual(loaded["knowledge_repo_worktree"], "/target")
            self.assertEqual(loaded["member_alias"], "member1")
            self.assertEqual(loaded["out_dir"], "$CODEX_HOME/artifacts/akbs-member-ops")
            self.assertEqual(paths, [home / "akbs-member-ops.toml"])

    def test_target_parse_failure_never_falls_back_to_legacy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, mock.patch.dict(
            os.environ, {"CODEX_HOME": temporary}, clear=True
        ):
            home = Path(temporary)
            (home / "android-knowledge-intake.toml").write_text(
                'member_alias = "legacy-member"\nmember_name = "Member"\n',
                encoding="utf-8",
            )
            (home / "akbs-member-ops.toml").write_text(
                'member_alias = "broken" garbage\n', encoding="utf-8"
            )
            with self.assertRaisesRegex(SystemExit, "成员身份配置无效"):
                config.load_config()

    def test_legacy_project_precedence_applies_only_when_target_is_absent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, mock.patch.dict(
            os.environ,
            {"CODEX_HOME": temporary, "CODEX_REPORT_MEMBER_ALIAS": "environment-must-not-win"},
            clear=True,
        ):
            home = Path(temporary)
            legacy = home / "android-knowledge-intake.toml"
            project = home / "project" / ".codex" / "report.toml"
            project.parent.mkdir(parents=True)
            legacy.write_text(
                'member_alias = "member1"\nmember_name = "Member One"\n'
                'knowledge_repo_worktree = "/legacy"\n',
                encoding="utf-8",
            )
            project.write_text(
                'default_profile = "project-invented"\n'
                'member_alias = "project-must-not-win"\n'
                'member_name = "Project Must Not Win"\n'
                'knowledge_repo_worktree = "/project"\n',
                encoding="utf-8",
            )
            with mock.patch.object(config, "find_project_report_config", return_value=project):
                loaded, paths = config.load_config()
            self.assertEqual(loaded["knowledge_repo_worktree"], "/project")
            self.assertEqual(loaded["member_alias"], "member1")
            self.assertEqual(loaded["member_name"], "Member One")
            self.assertEqual(paths, [legacy, project])
            with mock.patch.object(
                search_config, "find_project_report_config", return_value=project
            ):
                self.assertEqual(search_config.selected_member_alias(), ("", "member1"))
            with mock.patch.object(Path, "cwd", return_value=project.parent.parent):
                resolved = member_profile.load_member_profile()
            self.assertEqual(resolved.member_alias, "member1")
            self.assertNotIn(project, resolved.loaded_paths)

            legacy.unlink()
            with mock.patch.object(config, "find_project_report_config", return_value=project):
                with self.assertRaisesRegex(SystemExit, "成员身份配置无效"):
                    config.load_config()
            with mock.patch.object(
                search_config, "find_project_report_config", return_value=project
            ):
                with self.assertRaisesRegex(ValueError, "requires an AKBS profile"):
                    search_config.selected_member_alias()


class MemberProfileAuthorityTest(unittest.TestCase):
    def test_target_profile_ignores_malformed_legacy_without_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, mock.patch.dict(
            os.environ, {"CODEX_HOME": temporary}, clear=True
        ):
            home = Path(temporary)
            (home / "akbs-member-ops.toml").write_text(
                'default_profile = "member1"\n[profiles.member1]\n'
                'member_alias = "member1"\nmember_name = "Member One"\n',
                encoding="utf-8",
            )
            (home / "android-knowledge-intake.toml").write_text(
                'member_alias = "legacy"\nmember_alias = "malformed"\n',
                encoding="utf-8",
            )
            loaded = member_profile.load_member_profile()
            self.assertEqual(loaded.profile, "member1")
            self.assertEqual(loaded.member_alias, "member1")
            self.assertEqual(loaded.source, "akbs-member-ops")
            self.assertEqual(loaded.loaded_paths, (home / "akbs-member-ops.toml",))

    def test_standalone_identity_is_bounded_fallback_and_conflicts_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, mock.patch.dict(
            os.environ, {"CODEX_HOME": temporary}, clear=True
        ):
            home = Path(temporary)
            engineering = home / "android-engineering-ops.toml"
            engineering.write_text(
                '[identity]\nmember_alias = "engineer01"\n\n'
                '[extension]\nmode = "none"\n',
                encoding="utf-8",
            )
            loaded = member_profile.load_member_profile()
            self.assertEqual(loaded.profile, "standalone")
            self.assertEqual(loaded.member_alias, "engineer01")
            self.assertEqual(loaded.source, "android-engineering-ops-identity")

            (home / "akbs-member-ops.toml").write_text(
                'default_profile = "member1"\n[profiles.member1]\n'
                'member_alias = "member1"\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                member_profile.MemberProfileError, "AKBS and standalone"
            ):
                member_profile.load_member_profile()

    def test_explicit_profile_cannot_select_standalone_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, mock.patch.dict(
            os.environ, {"CODEX_HOME": temporary}, clear=True
        ):
            home = Path(temporary)
            (home / "android-engineering-ops.toml").write_text(
                '[identity]\nmember_alias = "engineer01"\n', encoding="utf-8"
            )
            with self.assertRaisesRegex(
                member_profile.MemberProfileError,
                "may select only an existing AKBS profile",
            ):
                member_profile.load_member_profile("invented")


class KnowledgeSearchConfigAuthorityTest(unittest.TestCase):
    def test_target_presence_excludes_legacy_search_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, mock.patch.dict(
            os.environ,
            {"CODEX_HOME": temporary, "CODEX_REPORT_MEMBER_ALIAS": "environment-member"},
            clear=True,
        ):
            home = Path(temporary)
            target = home / "akbs-member-ops.toml"
            target.write_text(
                'default_profile = "member1"\n[profiles.member1]\n'
                'member_alias = "member1"\nknowledge_repo_worktree = "/target"\n',
                encoding="utf-8",
            )
            (home / "android-knowledge-search.toml").write_text(
                'member_alias = "legacy"\nmember_alias = "malformed"\n',
                encoding="utf-8",
            )
            with mock.patch.object(
                search_config,
                "find_project_report_config",
                side_effect=AssertionError("legacy discovery must not run"),
            ):
                self.assertEqual(search_config.member_config_paths(), [target])
                self.assertEqual(search_config.selected_member_alias(), ("member1", "member1"))
                self.assertEqual(search_config.configured_roots(), [Path("/target")])


class InstalledPluginAuthorityTest(unittest.TestCase):
    def setUp(self) -> None:
        version_gate.PLUGIN_LIST_CACHE = None

    def tearDown(self) -> None:
        version_gate.PLUGIN_LIST_CACHE = None

    def test_version_parser_rejects_prefix_like_versions(self) -> None:
        self.assertEqual(version_gate.version_parts("2.0.0"), (2, 0, 0))
        with self.assertRaises(ValueError):
            version_gate.version_parts("2evil")

    @staticmethod
    def target_row(
        root: Path,
        *,
        version: str = "2.0.0",
        marketplace: str = "android-codex-suite",
        plugin_id: str | None = None,
    ) -> dict[str, object]:
        return {
            "pluginId": plugin_id if plugin_id is not None else f"akbs-member-ops@{marketplace}",
            "name": "akbs-member-ops",
            "marketplaceName": marketplace,
            "version": version,
            "installed": True,
            "enabled": True,
            "source": {"source": "local", "path": str(root)},
        }

    @staticmethod
    def write_manifest(root: Path, *, name: str = "akbs-member-ops", version: str = "2.0.0") -> None:
        (root / ".codex-plugin").mkdir(parents=True, exist_ok=True)
        (root / ".codex-plugin" / "plugin.json").write_text(
            json.dumps({"name": name, "version": version}), encoding="utf-8"
        )

    def cache_root(self, codex_home: Path, *, version: str = "2.0.0") -> Path:
        return (
            codex_home
            / "plugins"
            / "cache"
            / "android-codex-suite"
            / "akbs-member-ops"
            / version
        )

    def test_active_list_selects_exact_version_not_highest_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, mock.patch.dict(
            os.environ, {"CODEX_HOME": temporary}, clear=False
        ):
            home = Path(temporary)
            source = (
                home
                / ".tmp"
                / "marketplaces"
                / "android-codex-suite"
                / "plugins"
                / "akbs-member-ops"
            )
            exact = self.cache_root(home)
            stale = self.cache_root(home, version="99.0.0")
            self.write_manifest(source)
            for root, version in ((exact, "2.0.0"), (stale, "99.0.0")):
                self.write_manifest(root, version=version)
            payload = {"installed": [self.target_row(source)]}
            with mock.patch.object(version_gate, "PLUGIN_ROOT", exact), mock.patch.object(
                version_gate, "run", return_value=completed(payload)
            ):
                result = version_gate.latest_installed_plugin_cache_metadata()
            self.assertEqual(result["installed_plugin_version"], "2.0.0")
            self.assertEqual(Path(result["installed_plugin_path"]), exact)
            self.assertEqual(result["installed_plugin_authority"], "codex_plugin_list")
            self.assertFalse(result["installed_plugin_fallback"])

    def test_active_target_identity_binds_marketplace_source_to_versioned_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, mock.patch.dict(
            os.environ, {"CODEX_HOME": str(Path(temporary) / "codex")}, clear=False
        ):
            workspace = Path(temporary)
            codex_home = workspace / "codex"
            source = (
                codex_home
                / ".tmp"
                / "marketplaces"
                / "android-codex-suite"
                / "plugins"
                / "akbs-member-ops"
            )
            execution = self.cache_root(codex_home)
            self.write_manifest(source)
            self.write_manifest(execution)
            payload = {"installed": [self.target_row(source)]}
            with mock.patch.object(version_gate, "PLUGIN_ROOT", execution), mock.patch.object(
                version_gate, "run", return_value=completed(payload)
            ):
                result = version_gate.installed_plugin_family_status()
            self.assertEqual(result["status"], "PASS")
            self.assertFalse(result["blocking"])
            binding = result["target_member_binding"]
            self.assertTrue(binding["valid"])
            self.assertNotEqual(source.resolve(), execution.resolve())
            self.assertNotEqual(source.stat().st_ino, execution.stat().st_ino)
            self.assertEqual(binding["inventory_source_realpath"], str(source.resolve()))
            self.assertEqual(binding["execution_plugin_realpath"], str(execution.resolve()))
            self.assertEqual(
                binding["source_manifest_sha256"], binding["execution_manifest_sha256"]
            )
            self.assertEqual(binding["source_tree_sha256"], binding["execution_tree_sha256"])

    def test_active_target_identity_mismatches_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, mock.patch.dict(
            os.environ, {"CODEX_HOME": str(Path(temporary) / "codex")}, clear=False
        ):
            workspace = Path(temporary)
            execution = self.cache_root(workspace / "codex")
            source = (
                workspace
                / "codex"
                / ".tmp"
                / "marketplaces"
                / "android-codex-suite"
                / "plugins"
                / "akbs-member-ops"
            )
            self.write_manifest(execution)
            self.write_manifest(source)
            cases: tuple[tuple[str, dict[str, object]], ...] = (
                ("inventory-version", self.target_row(source, version="2.0.1")),
                ("plugin-id", self.target_row(source, plugin_id="wrong@suite")),
                ("missing-plugin-id", self.target_row(source, plugin_id="")),
                ("missing-version", self.target_row(source, version="")),
                ("malformed-version", self.target_row(source, version="2evil")),
                (
                    "wrong-marketplace",
                    self.target_row(source, marketplace="lookalike-suite"),
                ),
                (
                    "missing-source-path",
                    {
                        **self.target_row(source),
                        "source": {"source": "local"},
                    },
                ),
                (
                    "relative-source-path",
                    {
                        **self.target_row(source),
                        "source": {"source": "local", "path": "relative/plugin"},
                    },
                ),
                (
                    "non-local-source",
                    {
                        **self.target_row(source),
                        "source": {"source": "cache", "path": str(source)},
                    },
                ),
                ("source-is-execution-cache", self.target_row(execution)),
            )
            for label, row in cases:
                version_gate.PLUGIN_LIST_CACHE = None
                with self.subTest(label=label), mock.patch.object(
                    version_gate, "PLUGIN_ROOT", execution
                ), mock.patch.object(
                    version_gate, "run", return_value=completed({"installed": [row]})
                ):
                    result = version_gate.installed_plugin_family_status()
                self.assertEqual(result["status"], "ACTIVE_IDENTITY_MISMATCH")
                self.assertTrue(result["blocking"])
                self.assertFalse(result["target_member_binding"]["valid"])

    def test_active_target_manifest_identity_mismatches_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, mock.patch.dict(
            os.environ, {"CODEX_HOME": str(Path(temporary) / "codex")}, clear=False
        ):
            workspace = Path(temporary)
            codex_home = workspace / "codex"
            source = (
                codex_home
                / ".tmp"
                / "marketplaces"
                / "android-codex-suite"
                / "plugins"
                / "akbs-member-ops"
            )
            execution = self.cache_root(codex_home)
            payload = {"installed": [self.target_row(source)]}

            cases = (
                ("name", "wrong", "2.0.0"),
                ("version", "akbs-member-ops", "2.0.1"),
            )
            for label, name, manifest_version in cases:
                version_gate.PLUGIN_LIST_CACHE = None
                for root in (source, execution):
                    shutil.rmtree(root, ignore_errors=True)
                    self.write_manifest(root, name=name, version=manifest_version)
                with self.subTest(label=label), mock.patch.object(
                    version_gate, "PLUGIN_ROOT", execution
                ), mock.patch.object(version_gate, "run", return_value=completed(payload)):
                    result = version_gate.installed_plugin_family_status()
                self.assertEqual(result["status"], "ACTIVE_IDENTITY_MISMATCH")
                self.assertTrue(result["blocking"])

            version_gate.PLUGIN_LIST_CACHE = None
            for root in (source, execution):
                shutil.rmtree(root, ignore_errors=True)
                (root / ".codex-plugin").mkdir(parents=True)
                (root / ".codex-plugin" / "plugin.json").write_text(
                    '{"name":"akbs-member-ops","version":"2.0.0","version":"9.0.0"}',
                    encoding="utf-8",
                )
            with mock.patch.object(version_gate, "PLUGIN_ROOT", execution), mock.patch.object(
                version_gate, "run", return_value=completed(payload)
            ):
                malformed = version_gate.installed_plugin_family_status()
            self.assertEqual(malformed["status"], "ACTIVE_IDENTITY_MISMATCH")
            self.assertTrue(malformed["blocking"])

    def test_active_target_publication_tree_tamper_fails_but_python_cache_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, mock.patch.dict(
            os.environ, {"CODEX_HOME": str(Path(temporary) / "codex")}, clear=False
        ):
            workspace = Path(temporary)
            codex_home = workspace / "codex"
            source = (
                codex_home
                / ".tmp"
                / "marketplaces"
                / "android-codex-suite"
                / "plugins"
                / "akbs-member-ops"
            )
            execution = self.cache_root(codex_home)
            for root in (source, execution):
                self.write_manifest(root)
                (root / "README.md").write_text("same publication\n", encoding="utf-8")
            (source / "__pycache__").mkdir()
            (source / "__pycache__" / "runtime.cpython.pyc").write_bytes(b"source-cache")
            (execution / "__pycache__").mkdir()
            (execution / "__pycache__" / "runtime.pyc").write_bytes(
                b"execution-cache"
            )
            payload = {"installed": [self.target_row(source)]}
            with mock.patch.object(version_gate, "PLUGIN_ROOT", execution), mock.patch.object(
                version_gate, "run", return_value=completed(payload)
            ):
                accepted = version_gate.installed_plugin_family_status()
            self.assertEqual(accepted["status"], "PASS")

            source_manifest = source / ".codex-plugin" / "plugin.json"
            source_payload = json.loads(source_manifest.read_text(encoding="utf-8"))
            source_payload["same_version_tamper"] = True
            source_manifest.write_text(json.dumps(source_payload), encoding="utf-8")
            version_gate.PLUGIN_LIST_CACHE = None
            with mock.patch.object(version_gate, "PLUGIN_ROOT", execution), mock.patch.object(
                version_gate, "run", return_value=completed(payload)
            ):
                manifest_rejected = version_gate.installed_plugin_family_status()
            self.assertEqual(manifest_rejected["status"], "ACTIVE_IDENTITY_MISMATCH")
            self.assertIn(
                "source and execution plugin manifests differ byte-for-byte",
                manifest_rejected["target_member_binding"]["issues"],
            )
            source_manifest.write_bytes(
                (execution / ".codex-plugin" / "plugin.json").read_bytes()
            )
            (source / "README.md").write_text("same version, tampered bytes\n", encoding="utf-8")
            version_gate.PLUGIN_LIST_CACHE = None
            with mock.patch.object(version_gate, "PLUGIN_ROOT", execution), mock.patch.object(
                version_gate, "run", return_value=completed(payload)
            ):
                rejected = version_gate.installed_plugin_family_status()
            self.assertEqual(rejected["status"], "ACTIVE_IDENTITY_MISMATCH")
            self.assertTrue(rejected["blocking"])
            self.assertIn(
                "source and execution plugin publication content hashes differ",
                rejected["target_member_binding"]["issues"],
            )

    def test_active_target_publication_executable_bit_drift_fails_closed(self) -> None:
        # DrvFS can synthesize mode 0777 regardless of chmod; use the Linux
        # filesystem so this regression really exercises executable-bit drift.
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary, mock.patch.dict(
            os.environ, {"CODEX_HOME": str(Path(temporary) / "codex")}, clear=False
        ):
            codex_home = Path(temporary) / "codex"
            source = (
                codex_home
                / ".tmp"
                / "marketplaces"
                / "android-codex-suite"
                / "plugins"
                / "akbs-member-ops"
            )
            execution = self.cache_root(codex_home)
            for root in (source, execution):
                self.write_manifest(root)
                script = root / "scripts" / "entry.py"
                script.parent.mkdir()
                script.write_text("print('same bytes')\n", encoding="utf-8")
                script.chmod(0o644)
            (source / "scripts" / "entry.py").chmod(0o755)
            payload = {"installed": [self.target_row(source)]}
            with mock.patch.object(version_gate, "PLUGIN_ROOT", execution), mock.patch.object(
                version_gate, "run", return_value=completed(payload)
            ):
                result = version_gate.installed_plugin_family_status()
            self.assertEqual(result["status"], "ACTIVE_IDENTITY_MISMATCH")
            self.assertTrue(result["blocking"])
            self.assertIn(
                "source and execution plugin publication content hashes differ",
                result["target_member_binding"]["issues"],
            )

    def test_active_identity_cannot_be_borrowed_by_an_execution_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, mock.patch.dict(
            os.environ, {"CODEX_HOME": str(Path(temporary) / "codex")}, clear=False
        ):
            workspace = Path(temporary)
            codex_home = workspace / "codex"
            source = (
                codex_home
                / ".tmp"
                / "marketplaces"
                / "android-codex-suite"
                / "plugins"
                / "akbs-member-ops"
            )
            installed_cache = self.cache_root(codex_home)
            checkout = workspace / "developer-checkout" / "plugins" / "akbs-member-ops"
            for root in (source, installed_cache, checkout):
                self.write_manifest(root)
            payload = {"installed": [self.target_row(source)]}
            with mock.patch.object(version_gate, "PLUGIN_ROOT", checkout), mock.patch.object(
                version_gate, "run", return_value=completed(payload)
            ):
                result = version_gate.installed_plugin_family_status()
            self.assertEqual(result["status"], "ACTIVE_IDENTITY_MISMATCH")
            self.assertTrue(result["blocking"])
            self.assertIn(
                "current execution root is not the exact versioned Codex plugin cache",
                result["target_member_binding"]["issues"],
            )

    def test_duplicate_or_malformed_inventory_identity_is_not_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            plugin = Path(temporary) / "plugin"
            self.write_manifest(plugin)
            duplicate = self.target_row(plugin)
            malformed_json = subprocess.CompletedProcess(
                ["codex", "plugin", "list", "--json"],
                0,
                stdout='{"installed":[],"installed":[]}',
                stderr="",
            )
            cases = (
                (completed({"installed": [duplicate, dict(duplicate)]}), "AMBIGUOUS_INSTALL"),
                (malformed_json, "UNKNOWN"),
                (completed({"installed": ["not-an-object"]}), "UNKNOWN"),
            )
            for response, expected in cases:
                version_gate.PLUGIN_LIST_CACHE = None
                with self.subTest(expected=expected), mock.patch.object(
                    version_gate, "PLUGIN_ROOT", plugin
                ), mock.patch.object(version_gate, "run", return_value=response):
                    result = version_gate.installed_plugin_family_status()
                self.assertEqual(result["status"], expected)
                self.assertTrue(result["blocking"])

    def test_mixed_family_is_blocking(self) -> None:
        rows = [
            {
                "pluginId": f"{name}@suite",
                "name": name,
                "version": "1",
                "installed": True,
                "enabled": True,
                "source": {},
            }
            for name in ("akbs-member-ops", "android-framework-ops")
        ]
        with mock.patch.object(version_gate, "run", return_value=completed({"installed": rows})):
            result = version_gate.installed_plugin_family_status()
        self.assertEqual(result["status"], "MIXED_INSTALL")
        self.assertTrue(result["blocking"])

    def test_optional_jinny_generation_must_match_active_core(self) -> None:
        cases = (
            (("android-framework-ops", "1.0.169"), ("jinny-android-practices", "2.0.0")),
            (("akbs-member-ops", "2.0.0"), ("jinny-android-practices", "1.0.3")),
        )
        for entries in cases:
            version_gate.PLUGIN_LIST_CACHE = None
            rows = [
                {
                    "pluginId": f"{name}@suite",
                    "name": name,
                    "version": version,
                    "installed": True,
                    "enabled": True,
                    "source": {},
                }
                for name, version in entries
            ]
            with self.subTest(entries=entries), mock.patch.object(
                version_gate, "run", return_value=completed({"installed": rows})
            ), mock.patch.object(
                version_gate,
                "plugin_install_metadata",
                return_value={"plugin_name": "development-audit"},
            ):
                result = version_gate.installed_plugin_family_status()
                self.assertEqual(result["status"], "MIXED_INSTALL")
                self.assertTrue(result["blocking"])

    def test_checkout_is_development_evidence_when_target_is_not_active(self) -> None:
        rows = [
            {
                "pluginId": "unrelated@suite",
                "name": "unrelated",
                "version": "1",
                "installed": True,
                "enabled": True,
                "source": {},
            }
        ]
        with mock.patch.object(version_gate, "run", return_value=completed({"installed": rows})):
            family = version_gate.installed_plugin_family_status()
            metadata = version_gate.latest_installed_plugin_cache_metadata()
        self.assertEqual(family["status"], "TARGET_NOT_ACTIVE")
        self.assertTrue(family["blocking"])
        self.assertFalse(metadata["installed_plugin_active"])
        self.assertNotIn("installed_plugin_version", metadata)
        self.assertEqual(metadata["execution_plugin_version"], PLUGIN_VERSION)

    def test_cli_unavailable_falls_back_to_execution_root_not_history(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, mock.patch.dict(
            os.environ, {"CODEX_HOME": temporary}, clear=False
        ), mock.patch.object(version_gate, "run", return_value=completed({}, returncode=1)):
            result = version_gate.latest_installed_plugin_cache_metadata()
        self.assertTrue(result["installed_plugin_fallback"])
        self.assertFalse(result["installed_plugin_active"])
        self.assertEqual(result["installed_plugin_authority"], "current_execution_plugin_root_fallback")
        self.assertNotIn("installed_plugin_version", result)
        self.assertEqual(result["execution_plugin_version"], PLUGIN_VERSION)

    def test_unavailable_or_malformed_active_inventory_is_blocking(self) -> None:
        cases = (
            completed({}, returncode=1),
            subprocess.CompletedProcess(
                ["codex", "plugin", "list", "--json"],
                0,
                stdout="{not-json",
                stderr="",
            ),
        )
        for response in cases:
            version_gate.PLUGIN_LIST_CACHE = None
            with self.subTest(response=response), mock.patch.object(
                version_gate, "run", return_value=response
            ):
                result = version_gate.installed_plugin_family_status()
            self.assertEqual(result["status"], "UNKNOWN")
            self.assertTrue(result["blocking"])
            self.assertTrue(result["fallback"])

        version_gate.PLUGIN_LIST_CACHE = None
        with mock.patch.object(version_gate, "run", side_effect=FileNotFoundError("codex")):
            missing = version_gate.installed_plugin_family_status()
        self.assertEqual(missing["status"], "UNKNOWN")
        self.assertTrue(missing["blocking"])

        version_gate.PLUGIN_LIST_CACHE = None
        with mock.patch.object(
            version_gate,
            "run",
            side_effect=subprocess.TimeoutExpired(["codex", "plugin", "list", "--json"], 15),
        ):
            timed_out = version_gate.installed_plugin_family_status()
        self.assertEqual(timed_out["status"], "UNKNOWN")
        self.assertTrue(timed_out["blocking"])


class GmsTargetContractTest(unittest.TestCase):
    def test_android_major_target_is_normalized_and_required(self) -> None:
        fields = {
            "work_type": "GMS",
            "gms_release_type": "IR",
            "gms_target": "a14",
            "gms_cycle_status": "active",
            "gms_current_stage": "self_test",
            "gms_self_test_round": 1,
            "gms_self_test_result": "in_progress",
            "gms_submission_count": 0,
            "gms_submission_result": "not_submitted",
        }
        self.assertEqual(gms.normalize_gms_fields(fields)["gms_target"], "A14")
        self.assertEqual(
            gms.normalize_gms_fields(fields, plan=True),
            {"gms_release_type": "IR", "gms_target": "A14"},
        )
        self.assertEqual(gms.gms_scope_identity(fields), ("IR", "a14"))
        self.assertEqual(gms.gms_release_heading(fields), "GMS：IR（A14）")
        self.assertEqual(gms.validate_gms_fields(fields, prefix="projects[0]"), [])

        for target in ("Android 14 首个量产版本", "GMS IR", "2026-06 SPL", ""):
            with self.subTest(target=target):
                invalid = {**fields, "gms_target": target}
                errors = gms.validate_gms_fields(invalid, prefix="projects[0]")
                self.assertTrue(any("必须是 Android 主版本" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
