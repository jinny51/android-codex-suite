from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
PLUGIN_ROOT = REPO_ROOT / "plugins/android-engineering-ops"
PLUGIN_LIB = PLUGIN_ROOT / "lib"
if str(PLUGIN_LIB) not in sys.path:
    sys.path.insert(0, str(PLUGIN_LIB))

from android_engineering_ops.policy.patch_markers import (
    POLICY_ID,
    POLICY_VERSION,
    analyze_patch_markers,
    analyze_unified_diff_markers,
    closing_marker,
    load_policy,
    opening_marker,
)
from android_engineering_ops.member.profile import MemberProfileError, load_member_profile
from android_engineering_ops.patch_analysis import facts_from_diff


def test_policy_contract_is_the_canonical_three_layer_source() -> None:
    policy = load_policy()
    assert policy["authority"] == "canonical"
    assert policy["policy_id"] == POLICY_ID
    assert policy["version"] == POLICY_VERSION
    assert policy["attribution"]["identity_field"] == "member_alias"
    assert policy["attribution"]["identity_source"] == "current_member_profile"
    assert policy["attribution"]["free_form_identity_override"] == "forbidden"
    assert policy["attribution"]["generation_owner"] == "codex"
    assert policy["attribution"]["direct_marker_applicability"] == (
        "files_where_slash_line_comments_are_syntactically_valid"
    )
    assert policy["profiles"]["universal_patch_archive"]["mandatory"] is True
    assert policy["profiles"]["framework"]["mandatory_when_component"] == {
        "layer": "platform", "type": "framework"
    }
    assert policy["scope"]["component_layers"] == [
        "application", "platform", "native", "hal", "kernel", "device", "build"
    ]
    assert policy["profiles"]["legacy_jinny_style"]["mandatory"] is False


def test_component_contract_is_orthogonal_and_submission_never_falls_back() -> None:
    contract = json.loads(
        (PLUGIN_ROOT / "contracts/change-domain/v1/domain-profiles.json").read_text(encoding="utf-8")
    )
    assert contract["canonical_selector"] == "component.layer"
    assert contract["component_model"]["weak_title_or_filename_inference_forbidden"] is True
    assert contract["component_model"]["layer"] == [
        "application", "platform", "native", "hal", "kernel", "device", "build"
    ]
    assert contract["component_model"]["orthogonal_facets"] == [
        "type", "partition", "ownership"
    ]
    assert contract["submission"]["fallback_to_framework_v1"] is False


def test_marker_format_uses_member_alias_and_real_calendar_date() -> None:
    assert opening_marker("member01", dt.date(2026, 8, 28)) == "//member01 20260828@{"
    assert closing_marker("member01", "20260828") == "//member01 20260828@}"
    with pytest.raises(ValueError, match="real calendar date"):
        opening_marker("member01", "20260230")
    with pytest.raises(ValueError, match="member_alias"):
        opening_marker("not a profile", "20260828")


def test_new_codex_change_requires_matching_paired_markers() -> None:
    source = "\n".join(
        (
            "//member01 20260828@{",
            "customLogic();",
            "//member01 20260828@}",
        )
    )
    analysis = analyze_patch_markers(
        source,
        expected_alias="member01",
        require_pairs=True,
    )
    assert analysis.valid
    assert analysis.aliases == ("member01",)
    assert analysis.dates == ("20260828",)


def test_wrong_profile_alias_and_unpaired_markers_fail_closed() -> None:
    wrong_alias = analyze_patch_markers(
        "//someone_else 20260828@{\ncustomLogic();\n//someone_else 20260828@}",
        expected_alias="member01",
        require_pairs=True,
    )
    assert not wrong_alias.valid
    assert any("current member_alias" in error for error in wrong_alias.errors)

    legacy = analyze_patch_markers(
        "//legacy_author 20251016@",
        require_pairs=True,
    )
    assert legacy.has_legacy_marker
    assert any("require paired" in error for error in legacy.errors)


def test_historical_marker_can_be_observed_without_rewriting_authorship() -> None:
    historical = analyze_patch_markers(
        "//legacy_author 20251016@",
        require_pairs=False,
    )
    assert historical.valid
    assert historical.aliases == ("legacy_author",)


def test_unbalanced_or_impossible_marker_pairs_are_rejected() -> None:
    mismatched = analyze_patch_markers(
        "//member01 20260828@{\ncustomLogic();\n//member02 20260828@}",
        require_pairs=True,
    )
    assert any("does not match opening marker" in error for error in mismatched.errors)

    impossible_date = analyze_patch_markers(
        "//member01 20260230@{\ncustomLogic();\n//member01 20260230@}",
        require_pairs=True,
    )
    assert any("not a real yyyyMMdd date" in error for error in impossible_date.errors)


def test_marker_must_be_a_standalone_line_and_cover_added_content() -> None:
    string_forgery = analyze_patch_markers(
        'String a = "//member01 20260828@{"; String b = "//member01 20260828@}";',
        expected_alias="member01",
        require_pairs=True,
    )
    assert not string_forgery.valid
    assert not string_forgery.markers
    assert any("outside a paired" in error for error in string_forgery.errors)

    empty_then_unwrapped = analyze_patch_markers(
        "//member01 20260828@{\n//member01 20260828@}\ncustomLogic();",
        expected_alias="member01",
        require_pairs=True,
    )
    assert not empty_then_unwrapped.valid
    assert any("contains no added content" in error for error in empty_then_unwrapped.errors)
    assert any("outside a paired" in error for error in empty_then_unwrapped.errors)

    invalid_alias = analyze_patch_markers(
        "//Member01 20260828@{\ncustomLogic();\n//Member01 20260828@}",
        require_pairs=True,
    )
    assert not invalid_alias.valid
    assert any("alias 'Member01' is invalid" in error for error in invalid_alias.errors)


def test_paired_marker_state_cannot_cross_diff_hunks_or_files() -> None:
    diff = "\n".join(
        (
            "diff --git a/X.java b/X.java",
            "--- a/X.java",
            "+++ b/X.java",
            "@@ -1 +1,2 @@",
            "+//member01 20260828@{",
            "+firstChange();",
            "@@ -20 +21,2 @@",
            "+secondChange();",
            "+//member01 20260828@}",
            "diff --git a/Y.java b/Y.java",
            "--- a/Y.java",
            "+++ b/Y.java",
            "@@ -1 +1 @@",
            "+thirdChange();",
            "",
        )
    )
    analyses = analyze_unified_diff_markers(
        diff, expected_alias="member01", require_pairs=True
    )
    assert len(analyses) == 2
    assert all(item.analysis is not None and not item.analysis.valid for item in analyses)
    assert any(
        "hunk 1" in error and "no closing" in error
        for error in analyses[0].analysis.errors  # type: ignore[union-attr]
    )
    assert any(
        "outside a paired" in error
        for error in analyses[1].analysis.errors  # type: ignore[union-attr]
    )


def test_context_whitespace_generated_and_non_slash_files_are_not_marker_inputs() -> None:
    diff = "\n".join(
        (
            "diff --git a/X.java b/X.java",
            "--- a/X.java",
            "+++ b/X.java",
            "@@ -1,2 +1,5 @@",
            " contextOutsideMarkers();",
            "+//member01 20260828@{",
            "+",
            "+changed();",
            "+//member01 20260828@}",
            "diff --git a/out/generated/Fake.java b/out/generated/Fake.java",
            "--- a/out/generated/Fake.java",
            "+++ b/out/generated/Fake.java",
            "@@ -1 +1 @@",
            "+generated();",
            "diff --git a/res/values/x.xml b/res/values/x.xml",
            "--- a/res/values/x.xml",
            "+++ b/res/values/x.xml",
            "@@ -1 +1 @@",
            "+<string name=\"x\">x</string>",
            "",
        )
    )
    analyses = analyze_unified_diff_markers(
        diff, expected_alias="member01", require_pairs=True
    )
    assert analyses[0].analysis is not None and analyses[0].analysis.valid
    assert analyses[1].comment_adapter == "NOT_APPLICABLE_GENERATED_OUTPUT"
    assert analyses[2].comment_adapter == "NOT_APPLICABLE_NO_ADAPTER"


def test_member_profile_is_the_only_policy_identity_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    (codex_home / "android-knowledge-intake.toml").write_text(
        "default_profile = \"member01\"\n\n"
        "[profiles.member01]\n"
        "member_alias = \"member01\"\n"
        "member_name = \"Member 01\"\n"
        "timezone = \"Asia/Shanghai\"\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("CODEX_REPORT_ALIAS", "someone_else")
    monkeypatch.setenv("CODEX_REPORT_MEMBER_ALIAS", "someone_else")
    monkeypatch.chdir(tmp_path)
    profile = load_member_profile("member01")
    assert profile.profile == "member01"
    assert profile.member_alias == "member01"
    assert profile.timezone == "Asia/Shanghai"

    with pytest.raises(MemberProfileError, match="does not exist"):
        load_member_profile("invented")


def test_member_profile_rejects_an_unsafe_alias(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    (codex_home / "android-knowledge-intake.toml").write_text(
        "default_profile = \"unsafe\"\n\n"
        "[profiles.unsafe]\n"
        "member_alias = \"not a comment-safe alias\"\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.chdir(tmp_path)
    with pytest.raises(MemberProfileError, match="unsafe member_alias"):
        load_member_profile("unsafe")


def _clear_profile_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("CODEX_REPORT_PROFILE", "CODEX_WORK_REPORT_PROFILE"):
        monkeypatch.delenv(name, raising=False)


def test_clean_engineering_only_home_uses_frozen_standalone_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / "android-engineering-ops.toml").write_text(
        '[identity]\nmember_alias = "engineer01"\n', encoding="utf-8"
    )
    project = tmp_path / "project/.codex"
    project.mkdir(parents=True)
    (project / "report.toml").write_text(
        'default_profile = "invented"\n[profiles.invented]\n'
        'member_alias = "project-must-not-win"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(home))
    _clear_profile_selection(monkeypatch)
    monkeypatch.chdir(project.parent)

    profile = load_member_profile()

    assert profile.profile == "standalone"
    assert profile.member_alias == "engineer01"
    assert profile.source == "android-engineering-ops-identity"
    assert project / "report.toml" not in profile.loaded_paths


def test_authoritative_target_malformed_or_unselected_never_falls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / "android-engineering-ops.toml").write_text(
        '[identity]\nmember_alias = "engineer01"\n', encoding="utf-8"
    )
    target = home / "akbs-member-ops.toml"
    target.write_text('default_profile = "broken" garbage\n', encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(home))
    _clear_profile_selection(monkeypatch)
    with pytest.raises(MemberProfileError, match="failed to read member profile"):
        load_member_profile()

    target.write_text(
        'default_profile = "missing"\n[profiles.real]\nmember_alias = "member01"\n',
        encoding="utf-8",
    )
    with pytest.raises(MemberProfileError, match="authoritative member profile does not exist"):
        load_member_profile()


def test_dangling_identity_config_symlinks_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    legacy = home / "android-knowledge-intake.toml"
    legacy.write_text('member_alias = "legacy_user"\n', encoding="utf-8")
    (home / "akbs-member-ops.toml").symlink_to(home / "missing-akbs-config.toml")
    monkeypatch.setenv("CODEX_HOME", str(home))
    _clear_profile_selection(monkeypatch)

    with pytest.raises(MemberProfileError, match="must not be a symlink"):
        load_member_profile()

    (home / "akbs-member-ops.toml").unlink()
    legacy.unlink()
    (home / "android-engineering-ops.toml").symlink_to(
        home / "missing-engineering-config.toml"
    )
    with pytest.raises(MemberProfileError, match="regular non-symlink file"):
        load_member_profile()


def test_authoritative_target_does_not_read_or_merge_legacy_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    (home / "report").mkdir(parents=True)
    target = home / "akbs-member-ops.toml"
    target.write_text(
        'default_profile = "member"\n[profiles.member]\nmember_alias = "member01"\n',
        encoding="utf-8",
    )
    legacy = home / "android-knowledge-intake.toml"
    legacy.write_text('this is malformed legacy data', encoding="utf-8")
    conflicting = home / "report/config.toml"
    conflicting.write_text(
        'default_profile = "member"\n[profiles.member]\nmember_alias = "member02"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(home))
    _clear_profile_selection(monkeypatch)

    profile = load_member_profile()

    assert profile.member_alias == "member01"
    assert profile.loaded_paths == (target,)


def test_legacy_and_dual_identity_conflicts_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    (home / "report").mkdir(parents=True)
    (home / "android-knowledge-intake.toml").write_text(
        'default_profile = "member"\n[profiles.member]\nmember_alias = "member01"\n',
        encoding="utf-8",
    )
    (home / "report/config.toml").write_text(
        'default_profile = "member"\n[profiles.member]\nmember_alias = "member02"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(home))
    _clear_profile_selection(monkeypatch)
    with pytest.raises(MemberProfileError, match="conflicting member_alias"):
        load_member_profile()

    (home / "report/config.toml").unlink()
    (home / "android-engineering-ops.toml").write_text(
        '[identity]\nmember_alias = "engineer02"\n', encoding="utf-8"
    )
    with pytest.raises(MemberProfileError, match="AKBS and standalone"):
        load_member_profile()


def test_standalone_identity_forbids_profile_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / "android-engineering-ops.toml").write_text(
        '[identity]\nmember_alias = "engineer01"\n', encoding="utf-8"
    )
    monkeypatch.setenv("CODEX_HOME", str(home))
    _clear_profile_selection(monkeypatch)
    with pytest.raises(MemberProfileError, match="may select only an existing AKBS profile"):
        load_member_profile("invented")


def test_patch_analysis_ignores_markers_outside_added_lines() -> None:
    removed_only = "\n".join(
        (
            "diff --git a/X.java b/X.java",
            "--- a/X.java",
            "+++ b/X.java",
            "@@ -1,2 +1,2 @@",
            "-//legacy_author 20251016@ historical marker",
            "+customLogic();",
        )
    )
    assert facts_from_diff(removed_only)["author_date_marker_present"] is False

    added = removed_only + "\n+//member01 20260828@"
    assert facts_from_diff(added)["author_date_marker_present"] is True


def test_public_policy_skill_and_optional_jinny_layer_have_one_authority() -> None:
    policy_skill = (
        PLUGIN_ROOT / "skills/android-change-policy/SKILL.md"
    ).read_text(encoding="utf-8")
    practices_skill = (
        REPO_ROOT
        / "plugins/jinny-android-practices/skills/jinny-android-coding-practices/SKILL.md"
    ).read_text(encoding="utf-8")
    assert "../../contracts/android-change-policy/v1/README.md" in policy_skill
    assert "../../contracts/android-change-policy/v1/policy.json" in policy_skill
    assert "android-change-policy" in practices_skill
    assert not (
        REPO_ROOT
        / "plugins/jinny-android-practices/skills/jinny-framework-coding-standards"
    ).exists()
    assert "gyf" not in practices_skill.casefold()
