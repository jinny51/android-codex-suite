from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import unittest
import urllib.request
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
PLUGIN = ROOT / "plugins" / "akbs-member-ops"
CANONICAL = {
    "akbs-member-setup": "akbs_member_setup.py",
    "akbs-knowledge-search": "akbs_knowledge_search.py",
    "akbs-knowledge-merge-review": "akbs_knowledge_merge_review.py",
    "akbs-daily-report": "akbs_daily_report.py",
    "akbs-weekly-report": "akbs_weekly_report.py",
    "akbs-patch-submit": "akbs_patch_submit.py",
}
class SkillSurfaceTest(unittest.TestCase):
    def load_script(self, name: str, path: Path):
        spec = importlib.util.spec_from_file_location(name, path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_all_public_scripts_are_executable_and_help_from_unrelated_cwd(self) -> None:
        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        for skill, script_name in CANONICAL.items():
            script = PLUGIN / "skills" / skill / "scripts" / script_name
            with self.subTest(skill=skill):
                self.assertTrue(os.access(script, os.X_OK), script)
                completed = subprocess.run(
                    [sys.executable, str(script), "--help"],
                    cwd="/tmp",
                    env=env,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_only_one_incoming_v2_kernel_entry_exists(self) -> None:
        kernels = list(PLUGIN.rglob("akbs_member_intake.py"))
        self.assertEqual(
            kernels,
            [PLUGIN / "internal" / "incoming-v2" / "scripts" / "akbs_member_intake.py"],
        )

    def test_retired_v2_command_is_not_available(self) -> None:
        script = PLUGIN / "skills" / "akbs-patch-submit" / "scripts" / "akbs_patch_submit.py"
        self.assertNotIn("android-change-v2", script.read_text(encoding="utf-8"))
        current = PLUGIN / "lib/akbs_member_ops/incoming_v2"
        self.assertTrue(current.exists())
        for retired in ("schema.py", "submission.py", "validation.py"):
            self.assertFalse((current / retired).exists())

    def test_search_literal_help_after_option_terminator_cannot_bypass_family_gate(self) -> None:
        script = PLUGIN / "skills/akbs-knowledge-search/scripts/akbs_knowledge_search.py"
        module = self.load_script("akbs_knowledge_search_help_gate_test", script)
        with mock.patch.object(
            module,
            "installed_plugin_family_status",
            return_value={"blocking": True, "message": "target not active"},
        ) as family_gate, mock.patch.object(module, "search_main") as business:
            with self.assertRaisesRegex(SystemExit, "target not active"):
                module.main(["--source", "local", "--no-record-usage", "--", "--help"])
        family_gate.assert_called_once_with()
        business.assert_not_called()

    def test_single_v2_boundary_is_aligned_across_public_surfaces(self) -> None:
        surfaces = (
            PLUGIN / "README.md",
            PLUGIN / ".codex-plugin" / "plugin.json",
            PLUGIN / "skills" / "akbs-patch-submit" / "SKILL.md",
            PLUGIN / "skills" / "akbs-patch-submit" / "agents" / "openai.yaml",
            ROOT / "docs" / "skills" / "akbs-member-ops" / "akbs-patch-submit" / "README.md",
            ROOT / "manifests" / "akbs-member-ops.toml",
        )
        for surface in surfaces:
            with self.subTest(surface=surface):
                text = surface.read_text(encoding="utf-8")
                self.assertNotIn("adapt-capture", text)
                self.assertNotIn("qualification", text.lower())
        skill = surfaces[2].read_text(encoding="utf-8")
        docs = surfaces[4].read_text(encoding="utf-8")
        for text in (skill, docs):
            normalized = " ".join(text.split())
            self.assertIn("knowledge-incoming-package", normalized)
            self.assertIn("android_change", normalized)
            for layer in ("application", "platform", "native", "hal", "kernel", "device", "build"):
                self.assertIn(layer, normalized)

    def test_canonical_search_covers_non_framework_android_changes(self) -> None:
        skill = (PLUGIN / "skills" / "akbs-knowledge-search" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        contract = (
            PLUGIN
            / "skills"
            / "akbs-knowledge-search"
            / "references"
            / "search-contract.md"
        ).read_text(encoding="utf-8")
        agent = (
            PLUGIN
            / "skills"
            / "akbs-knowledge-search"
            / "agents"
            / "openai.yaml"
        ).read_text(encoding="utf-8")
        docs = (
            ROOT
            / "docs"
            / "skills"
            / "akbs-member-ops"
            / "akbs-knowledge-search"
            / "README.md"
        ).read_text(encoding="utf-8")

        for surface in (skill, contract, agent, docs):
            with self.subTest(surface=surface[:40]):
                for domain in ("GMS", "native", "HAL", "kernel", "device", "build"):
                    self.assertIn(domain, surface)
        self.assertNotIn("prior Android Framework solutions", skill)
        self.assertNotIn("new Android Framework requirement", skill)
        self.assertNotIn("primary Android Framework problem", contract)

    def test_member_setup_covers_gms_and_report_only_members(self) -> None:
        skill = (PLUGIN / "skills" / "akbs-member-setup" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        agent = (
            PLUGIN / "skills" / "akbs-member-setup" / "agents" / "openai.yaml"
        ).read_text(encoding="utf-8")
        for surface in (skill, agent):
            self.assertIn("AKBS member", surface)
            self.assertIn("GMS", surface)
            self.assertIn("report-only", surface)
        self.assertNotIn("Android engineering member", skill)

        docs = (
            ROOT
            / "docs"
            / "skills"
            / "akbs-member-ops"
            / "akbs-member-setup"
            / "README.md"
        ).read_text(encoding="utf-8")
        self.assertIn("GMS", docs)
        self.assertIn("仅填报成员", docs)
        self.assertIn("不限定为 Framework", docs)

    def test_member_setup_install_preflight_is_machine_checked_before_config(self) -> None:
        script = PLUGIN / "skills/akbs-member-setup/scripts/akbs_member_setup.py"
        module = self.load_script("akbs_member_setup_preflight_test", script)
        for family, expected in (
            ({"status": "PASS", "blocking": False}, 0),
            ({"status": "MIXED_INSTALL", "blocking": True}, 1),
        ):
            with self.subTest(family=family), mock.patch.object(
                module, "installed_plugin_family_status", return_value=family
            ) as gate, mock.patch.object(module, "incoming_main") as incoming:
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    result = module.main(["preflight-install-family"])
                self.assertEqual(result, expected)
                self.assertEqual(json.loads(output.getvalue()), family)
                gate.assert_called_once_with()
                incoming.assert_not_called()

        prompt = (
            PLUGIN
            / "internal/incoming-v2/references/member-setup-prompt.md"
        ).read_text(encoding="utf-8")
        self.assertLess(
            prompt.index("preflight-install-family"),
            prompt.index("仅在第 1 步门禁通过后创建"),
        )

    def test_incoming_v2_validate_cannot_bypass_target_install_family_gate(self) -> None:
        script = (
            PLUGIN
            / "internal/incoming-v2/scripts/akbs_member_intake.py"
        )
        module = self.load_script("akbs_member_intake_validate_gate_test", script)
        output = io.StringIO()
        with (
            mock.patch.object(module, "load_config", return_value=({}, [])),
            mock.patch.object(
                module,
                "installed_plugin_family_status",
                return_value={
                    "status": "MIXED_INSTALL",
                    "blocking": True,
                    "message": "target install family is not active",
                },
            ) as gate,
            mock.patch.object(
                module,
                "reexec_latest_plugin_script_after_update",
                side_effect=AssertionError("validate must not re-exec"),
            ) as reexec,
            mock.patch.object(
                module,
                "plugin_version_gate_check",
                side_effect=AssertionError("validate must not enter freshness/update"),
            ) as freshness,
            mock.patch.object(
                module,
                "run",
                side_effect=AssertionError("validate must not execute git/fetch/pull"),
            ) as run,
            mock.patch.object(module.os, "execv", side_effect=AssertionError("unexpected execv")) as execv,
            mock.patch.object(urllib.request, "urlopen") as urlopen,
            mock.patch.object(module, "validate_package") as validate,
            contextlib.redirect_stdout(output),
        ):
            result = module.main(["patch", "--validate", "/tmp/package"])
        self.assertEqual(result, 1)
        self.assertEqual(json.loads(output.getvalue())["status"], "FAIL")
        gate.assert_called_once_with()
        reexec.assert_not_called()
        freshness.assert_not_called()
        run.assert_not_called()
        execv.assert_not_called()
        urlopen.assert_not_called()
        validate.assert_not_called()

    def test_every_canonical_member_skill_gates_business_before_reads_or_writes(self) -> None:
        for skill in CANONICAL:
            text = (PLUGIN / "skills" / skill / "SKILL.md").read_text(
                encoding="utf-8"
            )
            with self.subTest(skill=skill):
                self.assertIn("preflight-install-family", text)
                self.assertIn("status=PASS", text)

        for report_type in ("daily", "weekly"):
            skill = (PLUGIN / f"skills/akbs-{report_type}-report/SKILL.md").read_text(
                encoding="utf-8"
            )
            facts = (
                PLUGIN
                / f"internal/incoming-v2/references/{report_type}-facts-contract.md"
            ).read_text(encoding="utf-8")
            docs = (
                ROOT
                / f"docs/skills/akbs-member-ops/akbs-{report_type}-report/README.md"
            ).read_text(encoding="utf-8")
            compact_skill = " ".join(skill.split())
            compact_facts = " ".join(facts.split())
            compact_docs = " ".join(docs.split())
            report_cn = {"daily": "日报", "weekly": "周报"}[report_type]
            self.assertIn(
                f"Before reading report inputs, writing direct {report_type} facts, "
                "generating a package, or performing any check/prepare/submit action, "
                "run:",
                compact_skill,
            )
            self.assertIn(
                f"Before creating or replacing any {report_type} facts file, run "
                "`akbs_member_setup.py preflight-install-family`",
                compact_facts,
            )
            self.assertIn(
                f"读取{report_cn}输入、创建/修改 {report_type}-facts 或执行生成、检查、"
                "提交前，必须先运行 `akbs_member_setup.py preflight-install-family`",
                compact_docs,
            )

    def test_target_config_exclusivity_is_documented_for_setup_and_search(self) -> None:
        surfaces = (
            PLUGIN / "README.md",
            PLUGIN / "skills" / "akbs-member-setup" / "SKILL.md",
            PLUGIN / "skills" / "akbs-knowledge-search" / "SKILL.md",
            PLUGIN / "internal" / "incoming-v2" / "references" / "member-setup-prompt.md",
            ROOT / "docs" / "skills" / "akbs-member-ops" / "akbs-member-setup" / "README.md",
            ROOT / "docs" / "skills" / "akbs-member-ops" / "akbs-knowledge-search" / "README.md",
        )
        for surface in surfaces:
            with self.subTest(surface=surface):
                text = " ".join(surface.read_text(encoding="utf-8").split()).lower()
                self.assertIn("target", text)
                self.assertTrue("legacy" in text or "旧配置" in text)
                self.assertTrue(
                    "sole akbs config authority" in text
                    or "only akbs configuration authority" in text
                    or "唯一 akbs 配置权威" in text
                    or "唯一 akbs 配置" in text
                )


if __name__ == "__main__":
    unittest.main()
