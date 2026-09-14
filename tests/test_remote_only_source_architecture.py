from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


SKILL_CONTRACTS = (
    Path("plugins/android-engineering-ops/skills/android-remote-channel/SKILL.md"),
    Path("plugins/android-engineering-ops/skills/android-change-workflow/SKILL.md"),
    Path("plugins/android-engineering-ops/skills/android-patch-capture/SKILL.md"),
    Path("plugins/android-engineering-ops/skills/android-remote-build-deploy/SKILL.md"),
    Path("plugins/android-engineering-ops/skills/android-source-access/SKILL.md"),
)

RUNTIME_SCAN_ROOTS = (
    Path("plugins/android-engineering-ops/skills/android-change-workflow/scripts"),
    Path("plugins/android-engineering-ops/skills/android-patch-capture/scripts"),
    Path("plugins/android-engineering-ops/skills/android-remote-build-deploy/scripts"),
    Path("plugins/akbs-member-ops/internal/incoming-v2/scripts"),
    Path("plugins/android-engineering-ops/skills/android-source-access/scripts"),
    Path("plugins/android-engineering-ops/adapters/source-access/wsl"),
    Path("plugins/android-engineering-ops/adapters/source-access/macos"),
)


# No current runtime owns a direct-SSH Android source operation. Do not add an
# entry merely to make the test green; source commands belong in the channel.
EXPECTED_DIRECT_SSH_DEBT_FILES: set[Path] = set()


# Direct SSH is permitted only for connection/key/Samba infrastructure. These
# scripts must not grow Android source inspection; source commands belong in the
# channel even when they are read-only.
DIRECT_SSH_INFRASTRUCTURE_ALLOWLIST = {
    Path("plugins/android-engineering-ops/adapters/source-access/wsl/skills/android-source-access/scripts/discover-samba-share.sh"),
    Path("plugins/android-engineering-ops/adapters/source-access/wsl/skills/android-source-access/scripts/ensure-samba-share.sh"),
    Path("plugins/android-engineering-ops/adapters/source-access/wsl/skills/android-source-access/scripts/install-ssh-key.sh"),
    Path("plugins/android-engineering-ops/adapters/source-access/wsl/skills/android-source-access/scripts/resolve-ssh-candidate.sh"),
    Path("plugins/android-engineering-ops/adapters/source-access/macos/skills/android-source-access/scripts/discover-samba-share.sh"),
}


# Explicit manual/historical import remains the only local Git compatibility
# path. `current_codex_skill` rejects it; mounted source is never an automatic
# or current-workflow fallback.
EXPECTED_LOCAL_SOURCE_DEBT_FILES = {
    Path("plugins/android-engineering-ops/skills/android-patch-capture/scripts/capture_android_patch.py"),
    Path("plugins/android-engineering-ops/skills/android-patch-capture/scripts/patch_capture/git_diff.py"),
}


# These signatures intentionally target source-tree behavior, not generic local
# artifact/evidence processing. The expected-file inventory above makes current
# P0 debt non-blocking while preventing it from spreading to another owner.
LOCAL_SOURCE_SIGNATURES = (
    re.compile(r"\bos\.walk\(root\)"),
    re.compile(r"run\(\[\s*[\"']git[\"']\s*,\s*[\"'](?:diff|rev-parse|status|branch|remote)"),
    re.compile(r"\$LOCAL_PROJECT/(?:build|frameworks|\.repo)"),
    re.compile(r"\$PROJECT_PATH/(?:build|frameworks|\.repo)"),
    re.compile(r"\$REPO/\.codex"),
    re.compile(r"Locally mounted Android source path"),
    re.compile(r"only reads local source files", re.IGNORECASE),
)


DIRECT_SSH_SIGNATURES = (
    re.compile(r"(?m)^\s*ssh\s+"),
    re.compile(r"\$\(\s*ssh\s+"),
    re.compile(r"(?m)^\s*ssh_cmd=\(ssh\s+"),
    re.compile(r"sshpass[^\n]*\bssh\s+"),
)


def runtime_files() -> list[Path]:
    result: list[Path] = []
    for relative_root in RUNTIME_SCAN_ROOTS:
        root = REPO_ROOT / relative_root
        result.extend(path for path in root.rglob("*") if path.suffix in {".py", ".sh"})
    return sorted(result)


def relative(path: Path) -> Path:
    return path.relative_to(REPO_ROOT)


def files_matching(signatures: tuple[re.Pattern[str], ...]) -> set[Path]:
    matches: set[Path] = set()
    for path in runtime_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        if any(pattern.search(text) for pattern in signatures):
            matches.add(relative(path))
    return matches


class RemoteOnlySourceArchitectureTests(unittest.TestCase):
    def test_runtime_skills_publish_the_remote_only_contract(self) -> None:
        for relative_path in SKILL_CONTRACTS:
            with self.subTest(skill=relative_path.as_posix()):
                text = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
                self.assertIn("Remote-Only Source Contract", text)
                self.assertIn("android-remote-channel", text)
                self.assertRegex(text, r"(?i)human.*(?:CRUD|source)|(?:CRUD|source).*human")
                self.assertRegex(text, r"(?i)artifact bridge|artifact.*bridge")

    def test_direct_ssh_source_debt_does_not_spread(self) -> None:
        detected = files_matching(DIRECT_SSH_SIGNATURES)
        expected = EXPECTED_DIRECT_SSH_DEBT_FILES | DIRECT_SSH_INFRASTRUCTURE_ALLOWLIST
        unexpected = sorted(detected - expected)
        self.assertEqual(
            unexpected,
            [],
            "new direct-SSH runtime owner detected; route source work through "
            "android-remote-channel or document a narrowly scoped infrastructure review: "
            + ", ".join(path.as_posix() for path in unexpected),
        )

    def test_local_mounted_source_debt_does_not_spread(self) -> None:
        detected = files_matching(LOCAL_SOURCE_SIGNATURES)
        unexpected = sorted(detected - EXPECTED_LOCAL_SOURCE_DEBT_FILES)
        self.assertEqual(
            unexpected,
            [],
            "new local/mounted Android source owner detected; add remote-channel "
            "execution instead of expanding the debt inventory: "
            + ", ".join(path.as_posix() for path in unexpected),
        )

    def test_debt_and_infrastructure_inventories_are_disjoint(self) -> None:
        self.assertFalse(EXPECTED_DIRECT_SSH_DEBT_FILES & DIRECT_SSH_INFRASTRUCTURE_ALLOWLIST)

    def test_knowledge_intake_has_no_implicit_android_cwd_patch_fallback(self) -> None:
        sessions = (
            REPO_ROOT
            / "plugins/akbs-member-ops/internal/incoming-v2/scripts/akbs_intake/report_sessions.py"
        ).read_text(encoding="utf-8")
        summary = (
            REPO_ROOT
            / "plugins/akbs-member-ops/internal/incoming-v2/scripts/akbs_intake/reports/session_summary.py"
        ).read_text(encoding="utf-8")
        assets = (
            REPO_ROOT
            / "plugins/akbs-member-ops/internal/incoming-v2/scripts/akbs_intake/patch/assets.py"
        ).read_text(encoding="utf-8")
        builder = (
            REPO_ROOT
            / "plugins/akbs-member-ops/internal/incoming-v2/scripts/akbs_intake/patch/builder.py"
        ).read_text(encoding="utf-8")
        intake_entry = (
            REPO_ROOT
            / "plugins/akbs-member-ops/internal/incoming-v2/scripts/akbs_member_intake.py"
        ).read_text(encoding="utf-8")

        self.assertIn("registered_android_mapping(raw_cwd, config)", sessions)
        self.assertNotIn("def git_root", sessions)
        self.assertNotIn("def git_branch_or_name", sessions)
        self.assertNotIn('["git", "-C"', sessions)
        self.assertNotIn("git_branch_or_name(raw_cwd", sessions)
        self.assertNotIn("Path(raw_cwd).exists", sessions)
        self.assertNotIn("git_root(session.cwd)", summary)
        self.assertNotIn("base.glob(pattern)", summary)
        self.assertIn('artifacts_root.glob("**/manifest.json")', summary)
        self.assertNotIn("def discover_patches_from_cwd", assets)
        self.assertNotIn("discover_patches_from_cwd", builder)
        self.assertIn("--patch-package 必须位于 Codex artifacts 根目录下", builder)
        self.assertNotIn("def git_root", intake_entry)
        self.assertNotIn("def git_branch_or_name", intake_entry)

    def test_local_artifact_basename_probe_is_retired(self) -> None:
        probe = (
            REPO_ROOT
            / "plugins/android-engineering-ops/skills/android-change-workflow/scripts/artifact_probe.py"
        )
        result = subprocess.run(
            [str(probe), "/path/that/must/not/be-scanned"],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(result.returncode, 64)
        self.assertIn("exact artifact path", result.stderr)
        self.assertIn("remote artifact manifest", result.stderr)


if __name__ == "__main__":
    unittest.main()
