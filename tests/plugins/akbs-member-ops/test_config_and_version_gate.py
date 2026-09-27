from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import pytest


ROOT = Path(__file__).resolve().parents[3]
PLUGIN = ROOT / "plugins" / "akbs-member-ops"
PLUGIN_VERSION = json.loads((PLUGIN / ".codex-plugin" / "plugin.json").read_text())["version"]
sys.path.insert(0, str(PLUGIN / "lib"))
sys.path.insert(0, str(PLUGIN / "internal" / "incoming-v2" / "scripts"))

from akbs_intake import config, version_gate  # noqa: E402
from akbs_intake.reports import gms  # noqa: E402
from akbs_member_ops.knowledge_search import config as search_config  # noqa: E402
from akbs_member_ops.member import profile as member_profile  # noqa: E402


def test_member_installed_rpc_rejects_incomplete_and_duplicate_marketplaces() -> None:
    member = {
        "id": "akbs-member-ops@android-codex-suite",
        "name": "akbs-member-ops",
        "localVersion": PLUGIN_VERSION,
        "source": {"type": "local", "path": "/source"},
        "installed": True,
        "enabled": True,
    }
    legacy = {
        "id": "android-framework-ops@legacy-market",
        "name": "android-framework-ops",
        "localVersion": "1.0.0",
        "source": {"type": "local", "path": "/legacy"},
        "installed": True,
        "enabled": True,
    }
    result = {
        "marketplaces": [
            {"name": "android-codex-suite", "plugins": [member]},
            {"name": "legacy-market", "plugins": [legacy]},
        ],
        "marketplaceLoadErrors": [],
    }
    payload = version_gate._installed_inventory({"result": result})
    with mock.patch.object(version_gate, "_app_server_installed_payload", return_value=payload):
        status = version_gate.installed_plugin_family_status()
    assert status["status"] == "MIXED_INSTALL"
    for invalid in (
        {**result, "marketplaceLoadErrors": ["unavailable"]},
        {**result, "marketplaces": result["marketplaces"] * 2},
        {**result, "marketplaces": [{"name": "android-codex-suite", "plugins": [member]}, {"name": "android-codex-suite", "plugins": [{**legacy, "id": "android-framework-ops@android-codex-suite"}]}]},
        {**result, "marketplaces": [{"name": "android-codex-suite", "plugins": [{**member, "enabled": None}]}]},
    ):
        with pytest.raises(ValueError):
            version_gate._installed_inventory({"result": invalid})
    for raw in (b'{"id":2,"id":2}', b'{"id":NaN}'):
        with pytest.raises(ValueError):
            version_gate._strict_rpc_json(raw)


def test_member_one_shot_app_server_always_exits() -> None:
    for mode in ("success", "timeout", "bad-json", "bad-init"):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            codex = root / "codex"
            pid_file = root / "child.pid"
            codex.write_text(
                "#!/usr/bin/env python3\n"
                "import json, os, sys, time\n"
                f"open({str(pid_file)!r}, 'w').write(str(os.getpid()))\n"
                f"mode = {mode!r}\n"
                "for line in sys.stdin:\n"
                "    request = json.loads(line)\n"
                "    if request.get('id') == 1:\n"
                "        print(json.dumps({'id': 1, 'result': None if mode == 'bad-init' else {}}), flush=True)\n"
                "    elif request.get('id') == 2:\n"
                "        if request.get('params', {}).get('cwds') != [os.getcwd()]:\n"
                "            raise SystemExit(65)\n"
                "        if mode == 'timeout':\n"
                "            time.sleep(30)\n"
                "        elif mode == 'bad-json':\n"
                "            print('{\"id\":2,\"result\":{},\"result\":{}}', flush=True)\n"
                "        else:\n"
                "            print(json.dumps({'id': 2, 'result': {'marketplaces': [], 'marketplaceLoadErrors': []}}), flush=True)\n",
                encoding="utf-8",
            )
            codex.chmod(0o755)
            with mock.patch.dict(os.environ, {"PATH": f"{root}{os.pathsep}{os.environ['PATH']}"}), mock.patch.object(
                version_gate, "INSTALLED_INVENTORY_TIMEOUT_SECONDS", 0.2 if mode == "timeout" else 15
            ):
                started = time.monotonic()
                if mode == "success":
                    assert version_gate._app_server_installed_payload() == {"installed": []}
                else:
                    with pytest.raises((ValueError, TimeoutError)):
                        version_gate._app_server_installed_payload()
                assert time.monotonic() - started < 3
            pid = int(pid_file.read_text())
            with pytest.raises(ProcessLookupError):
                os.kill(pid, 0)


def test_member_cleanup_tolerates_child_exit_after_poll() -> None:
    process = mock.Mock()
    process.stdin = io.BytesIO()
    process.stdout = io.BytesIO()
    process.poll.return_value = None
    process.terminate.side_effect = ProcessLookupError
    responses = iter((
        {"result": {}},
        {"result": {"marketplaces": [], "marketplaceLoadErrors": []}},
    ))
    with mock.patch.object(version_gate.subprocess, "Popen", return_value=process), mock.patch.object(
        version_gate, "_rpc_response", side_effect=lambda *args, **kwargs: next(responses)
    ):
        assert version_gate._app_server_installed_payload() == {"installed": []}
    process.wait.assert_called_once_with(timeout=1)


def test_member_inventory_cache_is_bound_to_effective_project_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    first_inventory = {"installed": [{"name": "akbs-member-ops", "installed": True, "enabled": True}]}
    second_inventory = {"installed": [{"name": "android-framework-ops", "installed": True, "enabled": True}]}
    version_gate.PLUGIN_LIST_CACHE = None
    version_gate.PLUGIN_LIST_CACHE_CWD = None
    try:
        with mock.patch.object(
            version_gate,
            "_app_server_installed_payload",
            side_effect=[first_inventory, second_inventory],
        ) as reader:
            monkeypatch.chdir(first)
            assert version_gate._plugin_list_payload()[0] == first_inventory
            assert version_gate._plugin_list_payload()[0] == first_inventory
            monkeypatch.chdir(second)
            assert version_gate._plugin_list_payload()[0] == second_inventory
        assert reader.call_args_list == [mock.call(first), mock.call(second)]
    finally:
        version_gate.PLUGIN_LIST_CACHE = None
        version_gate.PLUGIN_LIST_CACHE_CWD = None


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
                version_gate, "_app_server_installed_payload", return_value=payload
            ):
                result = version_gate.latest_installed_plugin_cache_metadata()
            self.assertEqual(result["installed_plugin_version"], "2.0.0")
            self.assertEqual(Path(result["installed_plugin_path"]), exact)
            self.assertEqual(result["installed_plugin_authority"], "codex_plugin_installed")
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
                version_gate, "_app_server_installed_payload", return_value=payload
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
                    version_gate, "_app_server_installed_payload", return_value={"installed": [row]}
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
                ), mock.patch.object(version_gate, "_app_server_installed_payload", return_value=payload):
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
                version_gate, "_app_server_installed_payload", return_value=payload
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
                version_gate, "_app_server_installed_payload", return_value=payload
            ):
                accepted = version_gate.installed_plugin_family_status()
            self.assertEqual(accepted["status"], "PASS")

            project_source = workspace / "project/plugins/akbs-member-ops"
            project_source.parent.mkdir(parents=True)
            shutil.copytree(source, project_source)
            (project_source / "README.md").write_text(
                "project marketplace override\n", encoding="utf-8"
            )
            version_gate.PLUGIN_LIST_CACHE = None
            with mock.patch.object(version_gate, "PLUGIN_ROOT", execution), mock.patch.object(
                version_gate,
                "_app_server_installed_payload",
                return_value={"installed": [self.target_row(project_source)]},
            ):
                project_rejected = version_gate.installed_plugin_family_status()
            self.assertEqual(project_rejected["status"], "ACTIVE_IDENTITY_MISMATCH")
            self.assertIn(
                "source and execution plugin publication content hashes differ",
                project_rejected["target_member_binding"]["issues"],
            )

            source_manifest = source / ".codex-plugin" / "plugin.json"
            source_payload = json.loads(source_manifest.read_text(encoding="utf-8"))
            source_payload["same_version_tamper"] = True
            source_manifest.write_text(json.dumps(source_payload), encoding="utf-8")
            version_gate.PLUGIN_LIST_CACHE = None
            with mock.patch.object(version_gate, "PLUGIN_ROOT", execution), mock.patch.object(
                version_gate, "_app_server_installed_payload", return_value=payload
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
                version_gate, "_app_server_installed_payload", return_value=payload
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
                version_gate, "_app_server_installed_payload", return_value=payload
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
                version_gate, "_app_server_installed_payload", return_value=payload
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
            cases = (
                ({"installed": [duplicate, dict(duplicate)]}, "AMBIGUOUS_INSTALL"),
                (ValueError("duplicate JSON key"), "UNKNOWN"),
                ({"installed": ["not-an-object"]}, "UNKNOWN"),
            )
            for response, expected in cases:
                version_gate.PLUGIN_LIST_CACHE = None
                with self.subTest(expected=expected), mock.patch.object(
                    version_gate, "PLUGIN_ROOT", plugin
                ), mock.patch.object(
                    version_gate,
                    "_app_server_installed_payload",
                    side_effect=response if isinstance(response, Exception) else None,
                    return_value=None if isinstance(response, Exception) else response,
                ):
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
        with mock.patch.object(version_gate, "_app_server_installed_payload", return_value={"installed": rows}):
            result = version_gate.installed_plugin_family_status()
        self.assertEqual(result["status"], "MIXED_INSTALL")
        self.assertTrue(result["blocking"])

    def test_member_install_family_ignores_optional_orchestration_plugins(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, mock.patch.dict(
            os.environ, {"CODEX_HOME": temporary}, clear=False
        ):
            home = Path(temporary)
            source = home / ".tmp/marketplaces/android-codex-suite/plugins/akbs-member-ops"
            runtime = self.cache_root(home, version=PLUGIN_VERSION)
            for target in (source, runtime):
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(PLUGIN, target)
            rows = [
                self.target_row(source, version=PLUGIN_VERSION),
                {
                    "pluginId": "jinny-android-practices@unknown",
                    "name": "jinny-android-practices",
                    "version": "malformed",
                    "installed": True,
                    "enabled": True,
                    "source": {},
                },
            ]
            with mock.patch.object(
                version_gate, "_app_server_installed_payload", return_value={"installed": rows}
            ), mock.patch.object(version_gate, "PLUGIN_ROOT", runtime):
                result = version_gate.installed_plugin_family_status()
            self.assertEqual(result["status"], "PASS")
            self.assertFalse(result["blocking"])

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
        with mock.patch.object(version_gate, "_app_server_installed_payload", return_value={"installed": rows}):
            family = version_gate.installed_plugin_family_status()
            metadata = version_gate.latest_installed_plugin_cache_metadata()
        self.assertEqual(family["status"], "TARGET_NOT_ACTIVE")
        self.assertTrue(family["blocking"])
        self.assertFalse(metadata["installed_plugin_active"])
        self.assertNotIn("installed_plugin_version", metadata)
        self.assertEqual(metadata["execution_plugin_version"], PLUGIN_VERSION)

    def test_app_server_unavailable_falls_back_to_execution_root_not_history(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, mock.patch.dict(
            os.environ, {"CODEX_HOME": temporary}, clear=False
        ), mock.patch.object(version_gate, "_app_server_installed_payload", side_effect=OSError("unavailable")):
            result = version_gate.latest_installed_plugin_cache_metadata()
        self.assertTrue(result["installed_plugin_fallback"])
        self.assertFalse(result["installed_plugin_active"])
        self.assertEqual(result["installed_plugin_authority"], "current_execution_plugin_root_fallback")
        self.assertNotIn("installed_plugin_version", result)
        self.assertEqual(result["execution_plugin_version"], PLUGIN_VERSION)

    def test_unavailable_or_malformed_active_inventory_is_blocking(self) -> None:
        cases = (OSError("unavailable"), ValueError("malformed JSON"))
        for response in cases:
            version_gate.PLUGIN_LIST_CACHE = None
            with self.subTest(response=response), mock.patch.object(
                version_gate, "_app_server_installed_payload", side_effect=response
            ):
                result = version_gate.installed_plugin_family_status()
            self.assertEqual(result["status"], "UNKNOWN")
            self.assertTrue(result["blocking"])
            self.assertTrue(result["fallback"])

        version_gate.PLUGIN_LIST_CACHE = None
        with mock.patch.object(version_gate, "_app_server_installed_payload", side_effect=FileNotFoundError("codex")):
            missing = version_gate.installed_plugin_family_status()
        self.assertEqual(missing["status"], "UNKNOWN")
        self.assertTrue(missing["blocking"])

        version_gate.PLUGIN_LIST_CACHE = None
        with mock.patch.object(
            version_gate,
            "_app_server_installed_payload",
            side_effect=TimeoutError("plugin/installed timed out"),
        ):
            timed_out = version_gate.installed_plugin_family_status()
        self.assertEqual(timed_out["status"], "UNKNOWN")
        self.assertTrue(timed_out["blocking"])


    def test_packaged_update_rechecks_fresh_active_inventory_and_new_cache_content(self) -> None:
        for outcome in ("valid", "no-active", "content-mismatch"):
            version_gate.PLUGIN_LIST_CACHE = None
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as temporary, mock.patch.dict(
                os.environ, {"CODEX_HOME": temporary}, clear=False
            ):
                home = Path(temporary)
                source = home / ".tmp/marketplaces/android-codex-suite/plugins/akbs-member-ops"
                old_cache = self.cache_root(home, version="2.0.0")
                new_cache = self.cache_root(home, version="2.0.1")
                self.write_manifest(source)
                self.write_manifest(old_cache)
                inventory = {"installed": [self.target_row(source)]}
                commands: list[list[str]] = []

                def simulate(command: list[str], *, timeout: float | None = None) -> subprocess.CompletedProcess[str]:
                    commands.append(command)
                    self.assertEqual(timeout, version_gate._plugin_update.UPDATE_COMMAND_TIMEOUT)
                    if command == ["codex", "plugin", "marketplace", "upgrade", "android-codex-suite", "--json"]:
                        self.write_manifest(source, version="2.0.1")
                    elif command == ["codex", "plugin", "add", "akbs-member-ops@android-codex-suite", "--json"]:
                        self.write_manifest(new_cache, version="2.0.1")
                        inventory["installed"] = [self.target_row(source, version="2.0.1")]
                        if outcome == "no-active":
                            inventory["installed"] = []
                        elif outcome == "content-mismatch":
                            (new_cache / "unexpected.txt").write_text("different content", encoding="utf-8")
                    else:
                        self.fail(f"unexpected command: {command}")
                    return subprocess.CompletedProcess(command, 0, stdout="updated", stderr="")

                original_family = version_gate.installed_plugin_family_status
                with mock.patch.object(version_gate, "PLUGIN_ROOT", old_cache), mock.patch.object(
                    version_gate, "run", side_effect=simulate
                ), mock.patch.object(
                    version_gate, "_app_server_installed_payload", side_effect=lambda _cwd: inventory.copy()
                ), mock.patch.object(
                    version_gate, "installed_plugin_family_status", wraps=original_family
                ) as family:
                    result = version_gate.auto_update_packaged_plugin("akbs-member-ops")
                    self.assertEqual(version_gate.PLUGIN_ROOT, old_cache)
                self.assertEqual(len(commands), 2)
                self.assertEqual(family.call_count, 1 if outcome == "no-active" else 2)
                self.assertEqual(result["status"], "PASS" if outcome == "valid" else "FAIL")
                self.assertEqual(result["restart_required"], outcome == "valid")
                if outcome == "valid":
                    self.assertEqual(result["installed_plugin_version"], "2.0.1")
                    self.assertEqual(Path(result["installed_plugin_path"]), new_cache)
                    self.assertTrue(result["installed_plugin_binding"]["valid"])
                    self.assertEqual(result["install_family"]["status"], "PASS")
                if outcome == "content-mismatch":
                    self.assertEqual(result["install_family"]["status"], "ACTIVE_IDENTITY_MISMATCH")

    def test_packaged_freshness_rejects_an_update_that_did_not_reach_remote_version(self) -> None:
        metadata = {"plugin_name": "akbs-member-ops", "plugin_version": "2.0.0"}
        with mock.patch.object(version_gate, "latest_installed_plugin_cache_metadata", return_value={}), mock.patch.object(
            version_gate, "current_skill_cache_metadata", return_value={}
        ), mock.patch.object(
            version_gate, "fetch_remote_plugin_manifest", return_value={"version": "2.0.2"}
        ), mock.patch.object(
            version_gate,
            "auto_update_packaged_plugin",
            return_value={"status": "PASS", "installed_plugin_version": "2.0.1", "restart_required": True},
        ) as update:
            result = version_gate.packaged_plugin_freshness(metadata, fetch=True, require=True)
        update.assert_called_once_with("akbs-member-ops")
        self.assertEqual(result["status"], "STALE")
        self.assertTrue(result["blocking"])
        self.assertEqual(result["auto_update"]["reason"], "installed_version_mismatch")
        self.assertFalse(result["auto_update"]["restart_required"])

    def test_member_manifest_wrapper_keeps_the_urlopen_mock_boundary(self) -> None:
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b'{"name":"akbs-member-ops","version":"2.0.3"}'
        with mock.patch.object(version_gate.urllib.request, "urlopen", return_value=response) as opener:
            result = version_gate.fetch_remote_plugin_manifest(
                {"repository": "https://github.com/jinny51/android-codex-suite.git", "plugin_name": "akbs-member-ops"}
            )
        self.assertEqual(result["version"], "2.0.3")
        opener.assert_called_once_with(
            "https://raw.githubusercontent.com/jinny51/android-codex-suite/main/plugins/akbs-member-ops/.codex-plugin/plugin.json",
            timeout=version_gate.PLUGIN_REMOTE_MANIFEST_TIMEOUT,
        )
        response.__enter__.return_value.read.assert_called_once_with(version_gate._plugin_update.MAX_MANIFEST_BYTES + 1)


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
