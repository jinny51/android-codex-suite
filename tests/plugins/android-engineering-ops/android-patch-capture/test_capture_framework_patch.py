from __future__ import annotations

import argparse
import importlib.util
import hashlib
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[4]
PLUGIN = ROOT / "plugins/android-engineering-ops"
CAPTURE = PLUGIN / "skills/android-patch-capture/scripts/capture_android_patch.py"
if str(PLUGIN / "lib") not in sys.path:
    sys.path.insert(0, str(PLUGIN / "lib"))


def load_capture():
    spec = importlib.util.spec_from_file_location("capture_framework_focused", CAPTURE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_diff_facts_extract_changed_android_facts() -> None:
    capture = load_capture()
    diff = """diff --git a/core/res/res/values/config.xml b/core/res/res/values/config.xml
--- a/core/res/res/values/config.xml
+++ b/core/res/res/values/config.xml
@@ -1,2 +1,2 @@
-<string name=\"removed_key\">old</string>
+<string name=\"added_key\">new</string>
 <string name=\"context_key\">same</string>
"""
    facts = capture.facts_from_diff(diff)
    assert facts["resource_keys"] == ["added_key", "removed_key"]
    assert "context_key" not in facts["resource_keys"]


def test_repository_layer_bindings_are_exact() -> None:
    capture = load_capture()
    repositories = [
        capture.RepositoryCapture(
            source_root="/source/frameworks/base",
            repo_path="frameworks/base",
            git_info={},
            diff_text="diff",
            facts={},
            module="frameworks-base",
            patch_name="rk14-frameworks-base@feature.patch",
            patch_rel="patches/rk14-frameworks-base@feature.patch",
        ),
        capture.RepositoryCapture(
            source_root="/source/packages/apps/Settings",
            repo_path="packages/apps/Settings",
            git_info={},
            diff_text="diff",
            facts={},
            module="settings",
            patch_name="rk14-settings@feature.patch",
            patch_rel="patches/rk14-settings@feature.patch",
        ),
    ]
    layers = capture.capture_layers(
        repositories,
        default_layer="platform",
        repository_layers={"packages/apps/Settings": "application"},
    )
    assert layers == {
        "patches/rk14-frameworks-base@feature.patch": "platform",
        "patches/rk14-settings@feature.patch": "application",
    }
    assert capture.component_rows(layers) == [
        {"layer": "application", "patches": ["patches/rk14-settings@feature.patch"]},
        {"layer": "platform", "patches": ["patches/rk14-frameworks-base@feature.patch"]},
    ]
    with pytest.raises(SystemExit, match="不存在的 repo_path"):
        capture.capture_layers(
            repositories,
            default_layer="platform",
            repository_layers={"unknown/repo": "native"},
        )


def test_component_rows_group_every_patch_once() -> None:
    capture = load_capture()
    rows = capture.component_rows(
        {"patches/a.patch": "native", "patches/b.patch": "native", "patches/c.patch": "build"}
    )
    assert rows == [
        {"layer": "native", "patches": ["patches/a.patch", "patches/b.patch"]},
        {"layer": "build", "patches": ["patches/c.patch"]},
    ]


def test_project_inference_keeps_conflicts_out_of_formal_target() -> None:
    capture = load_capture()
    change = capture.RepositoryCapture(
        source_root="/source/TVE8402M/frameworks/base",
        repo_path="frameworks/base",
        git_info={"branch": "feature/TVA10A2R-policy", "remote": "", "remotes": ""},
        diff_text="diff --git a/A b/A\n",
        facts={"modified_files": ["A"], "modules": [], "symbols": []},
        module="frameworks-base",
        patch_name="rk14-frameworks-base@feature.patch",
        patch_rel="patches/rk14-frameworks-base@feature.patch",
    )
    project, evidence = capture.infer_capture_project_for_feature(
        argparse.Namespace(project="unknown", summary="policy", feature="feature"),
        [change],
        trusted_platform="rk",
    )
    assert project == "unknown"
    assert set(evidence["candidates"]) == {"TVA10A2R", "TVE8402M"}


@pytest.mark.parametrize(
    "summary",
    ["今日补丁合集", "多个独立功能合集"],
)
def test_scope_wording_emits_a_review_hint(summary: str) -> None:
    capture = load_capture()
    errors = capture.validate_feature_scope(
        argparse.Namespace(summary=summary, feature="batch-collection")
    )
    assert errors


def test_capture_preserves_negated_scope_wording_as_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys,
) -> None:
    capture = load_capture()
    patch = tmp_path / "product.patch"
    patch.write_text(
        "diff --git a/core/Example.java b/core/Example.java\n"
        "--- a/core/Example.java\n+++ b/core/Example.java\n@@ -1 +1,2 @@\n-old();\n"
        "+//legacy_author 20251016@\n+updated();\n",
    )
    monkeypatch.setattr(sys, "argv", [str(CAPTURE), "--workflow-contract", "historical_import",
        "--implementation-origin", "historical", "--patch-artifact", str(patch),
        "--patch-repo-path", "frameworks/base", "--platform", "rk14",
        "--component-layer", "platform", "--feature", "rotation", "--summary",
        "单一旋转功能，明确排除多个独立功能。", "--out-dir", str(tmp_path / "captures")])
    assert capture.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["local_check"]["errors"] == []
    assert any("复核" in warning for warning in result["local_check"]["warnings"])
    assert Path(result["package"]).is_dir()


@pytest.mark.parametrize("workflow_contract", ["manual_import", "historical_import"])
@pytest.mark.parametrize("source_argument", [False, True])
def test_import_capture_never_generates_a_patch_from_current_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys,
    workflow_contract: str, source_argument: bool,
) -> None:
    capture = load_capture()
    monkeypatch.setattr(
        capture, "collect_repository_captures",
        lambda *args: pytest.fail("an import must not capture the current Git diff"),
    )
    output = tmp_path / "not-created"
    argv = [str(CAPTURE), "--workflow-contract", workflow_contract,
        "--platform", "rk14", "--component-layer", "platform",
        "--feature", "rotation", "--summary", "旋转策略", "--out-dir", str(output)]
    if source_argument:
        argv += ["--source-root", str(tmp_path / "current-product-source")]
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit) as failure:
        capture.main()
    assert failure.value.code == 2
    assert "已有的 --patch-artifact" in capsys.readouterr().err
    assert not output.exists()


@pytest.mark.parametrize("workflow_contract", ["manual_import", "historical_import"])
def test_import_capture_keeps_existing_artifact_author_and_date(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys,
    workflow_contract: str,
) -> None:
    capture = load_capture()
    patch = tmp_path / "verified-product.patch"
    original = (
        "diff --git a/core/Example.java b/core/Example.java\n"
        "--- a/core/Example.java\n+++ b/core/Example.java\n@@ -1 +1,2 @@\n-old();\n"
        "+//legacy_author 20251016@\n+updated();\n"
    ).encode("utf-8")
    patch.write_bytes(original)
    monkeypatch.setattr(
        capture, "collect_repository_captures",
        lambda *args: pytest.fail("existing artifact import must not read current Git"),
    )
    monkeypatch.setattr(sys, "argv", [str(CAPTURE), "--workflow-contract", workflow_contract,
        "--implementation-origin", "codex", "--patch-artifact", str(patch),
        "--patch-repo-path", "frameworks/base", "--platform", "rk14",
        "--component-layer", "platform", "--feature", "rotation", "--summary", "旋转策略",
        "--run-id", "20261019-existing-artifact", "--out-dir", str(tmp_path / "captures")])
    assert capture.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["local_check"]["errors"] == []
    package = Path(result["package"])
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["workflow_contract"] == workflow_contract
    assert manifest["implementation_origin"] == "codex"
    assert (package / manifest["patches"][0]["path"]).read_bytes() == original
    assert patch.read_bytes() == original


def test_controlled_patch_filename_rules() -> None:
    capture = load_capture()
    good = capture.RepositoryCapture(
        source_root="/source",
        repo_path="frameworks/base",
        git_info={},
        diff_text="diff",
        facts={},
        module="frameworks-base",
        patch_name="rk14-frameworks-base@feature.patch",
        patch_rel="patches/rk14-frameworks-base@feature.patch",
    )
    assert capture.validate_patch_asset_names([good]) == []


def test_historical_import_rejects_android_root_combined_patch(tmp_path: Path) -> None:
    capture = load_capture()
    patch = tmp_path / "combined.patch"
    patch.write_text(
        "diff --git a/frameworks/base/core/Test.java b/frameworks/base/core/Test.java\n"
        "--- a/frameworks/base/core/Test.java\n"
        "+++ b/frameworks/base/core/Test.java\n"
        "@@ -1 +1 @@\n-old\n+new\n"
        "diff --git a/device/vendor/product/config.mk b/device/vendor/product/config.mk\n"
        "--- a/device/vendor/product/config.mk\n"
        "+++ b/device/vendor/product/config.mk\n"
        "@@ -1 +1 @@\n-old\n+new\n",
        encoding="utf-8",
    )
    args = argparse.Namespace(
        patch_artifact=[str(patch)],
        patch_repo_path=["."],
        module=None,
        remote_source_root="",
    )
    with pytest.raises(SystemExit, match="每个仓库分别提供"):
        capture.collect_patch_artifact_captures(args, "rk14", "feature")


def test_historical_import_rejects_workspace_relative_paths_for_declared_repo(
    tmp_path: Path,
) -> None:
    capture = load_capture()
    patch = tmp_path / "workspace-relative.patch"
    patch.write_text(
        "diff --git a/frameworks/base/core/Test.java b/frameworks/base/core/Test.java\n"
        "--- a/frameworks/base/core/Test.java\n"
        "+++ b/frameworks/base/core/Test.java\n"
        "@@ -1 +1 @@\n-old\n+new\n",
        encoding="utf-8",
    )
    args = argparse.Namespace(
        patch_artifact=[str(patch)],
        patch_repo_path=["frameworks/base"],
        module=None,
        remote_source_root="",
    )
    with pytest.raises(SystemExit, match="相对于 Android 源码顶层"):
        capture.collect_patch_artifact_captures(args, "rk14", "feature")


def test_manual_patch_artifact_never_silently_drops_mode_only_section(
    tmp_path: Path,
) -> None:
    capture = load_capture()
    patch = tmp_path / "original.patch"
    original = (
        "diff --git a/core/Mode.java b/core/Mode.java\n"
        "old mode 100644\n"
        "new mode 100755\n"
        "diff --git a/core/Test.java b/core/Test.java\n"
        "--- a/core/Test.java\n"
        "+++ b/core/Test.java\n"
        "@@ -1 +1 @@\n-old\n+new\n"
    ).encode("utf-8")
    patch.write_bytes(original)
    args = argparse.Namespace(
        patch_artifact=[str(patch)],
        patch_repo_path=["frameworks/base"],
        module=None,
        remote_source_root="",
    )
    with pytest.raises(SystemExit, match="在捕获时会被改写"):
        capture.collect_patch_artifact_captures(args, "rk14", "feature")
    assert patch.read_bytes() == original


def test_manual_multi_patch_never_silently_drops_mode_only_artifact(
    tmp_path: Path,
) -> None:
    capture = load_capture()
    code_patch = tmp_path / "code.patch"
    code_patch.write_text(
        "diff --git a/core/Test.java b/core/Test.java\n"
        "--- a/core/Test.java\n"
        "+++ b/core/Test.java\n"
        "@@ -1 +1 @@\n-old\n+new\n",
        encoding="utf-8",
    )
    mode_patch = tmp_path / "mode.patch"
    original_mode = (
        "diff --git a/core/Mode.java b/core/Mode.java\n"
        "old mode 100644\n"
        "new mode 100755\n"
    ).encode("utf-8")
    mode_patch.write_bytes(original_mode)
    args = argparse.Namespace(
        patch_artifact=[str(code_patch), str(mode_patch)],
        patch_repo_path=["frameworks/base", "frameworks/base"],
        module=None,
        remote_source_root="",
    )
    with pytest.raises(SystemExit, match="仅含权限变化"):
        capture.collect_patch_artifact_captures(args, "rk14", "feature")
    assert mode_patch.read_bytes() == original_mode


def test_exact_unknown_receipt_retains_limitation_with_results(tmp_path: Path) -> None:
    capture = load_capture()
    payload = {
        "schema": "android-knowledge-search-usage", "schema_version": "1",
        "member_alias": "member-test", "searched": True, "queries": ["display policy"],
        "results": [{"id": "unclassified-case", "reuse_grade": "case_reference_only"}],
        "target_environment": {"project": "TVI2343R", "platform": "rk", "android_version": "14"},
        "source": "local_jsonl_fallback", "decision": "unknown", "reuse_decision": "unknown",
        "targets": [], "reason": "Applicability and search health remain unknown.",
    }
    raw = (json.dumps(payload) + "\r\n").encode()
    receipt = tmp_path / "usage.json"
    receipt.write_bytes(raw)
    args = argparse.Namespace(
        search_receipt=str(receipt), search_receipt_sha256=hashlib.sha256(raw).hexdigest(),
        policy_member_alias="member-test", project="TVI2343R",
        status="validated", workflow_contract="current_codex_skill",
    )
    selected, selected_raw = capture.load_search_receipt(args, platform="rk", android_version="14")
    assert selected == payload
    assert selected_raw == raw
    errors, warnings = capture.validate_search_decision_for_status(args, selected)
    assert errors == []
    assert any("用途仍不确定" in warning for warning in warnings)
    args.status = "candidate"
    assert capture.validate_search_decision_for_status(args, selected) == ([], [])
    assert selected["decision"] == "unknown"
    assert selected["results"][0]["reuse_grade"] == "case_reference_only"
    args.search_receipt_sha256 = "a" * 64
    with pytest.raises(SystemExit, match="SHA256 不一致"):
        capture.load_search_receipt(args, platform="rk", android_version="14")


def _chip_search_receipt(tmp_path: Path, *, platform: str = "uis7885") -> tuple[Path, bytes]:
    payload = {
        "schema": "android-knowledge-search-usage", "schema_version": "1",
        "member_alias": "member-test", "searched": True, "queries": ["display policy"],
        "results": [], "target_environment": {
            "project": "TVI7885U", "platform": platform, "android_version": "14",
        },
        "source": "local_jsonl_fallback", "decision": "unknown", "reuse_decision": "unknown",
        "targets": [], "reason": "Search health remains unknown.",
    }
    raw = (json.dumps(payload, indent=2) + "\r\n").encode()
    receipt = tmp_path / "chip-usage.json"
    receipt.write_bytes(raw)
    return receipt, raw


def _chip_capture_argv(receipt: Path, raw: bytes) -> list[str]:
    return [str(CAPTURE), "--profile", "member-test", "--platform", "unisoc14",
        "--component-layer", "platform", "--feature", "display-policy", "--summary", "显示策略",
        "--project", "TVI7885U", "--status", "validated", "--search-receipt", str(receipt),
        "--search-receipt-sha256", hashlib.sha256(raw).hexdigest()]


def test_chip_receipt_maps_to_package_family_without_changing_original(tmp_path: Path) -> None:
    capture = load_capture()
    receipt, raw = _chip_search_receipt(tmp_path)
    args = argparse.Namespace(search_receipt=str(receipt), search_receipt_sha256=hashlib.sha256(raw).hexdigest(),
        policy_member_alias="member-test", project="TVI7885U")
    selected, selected_raw = capture.load_search_receipt(args, platform="unisoc", android_version="14")
    assert selected["target_environment"]["platform"] == "uis7885"
    assert selected_raw == raw == receipt.read_bytes()
    assert selected["decision"] == "unknown"
    for incompatible in ("rk", "mtk", "unknown"):
        with pytest.raises(SystemExit, match="target_environment.platform"):
            capture.load_search_receipt(args, platform=incompatible, android_version="14")


def test_capture_rejects_abbreviated_internal_preflight(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    capture = load_capture()
    receipt, raw = _chip_search_receipt(tmp_path)
    monkeypatch.setattr(sys, "argv", _chip_capture_argv(receipt, raw) + ["--prefl"])
    with pytest.raises(SystemExit) as failure:
        capture.main()
    assert failure.value.code == 2


def test_preflight_needs_no_snapshot_and_creates_no_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys,
) -> None:
    capture = load_capture()
    receipt, raw = _chip_search_receipt(tmp_path)
    monkeypatch.setattr(capture, "load_member_profile", lambda _: argparse.Namespace(profile="member-test", member_alias="member-test"))
    monkeypatch.setattr(capture, "collect_snapshot_captures", lambda *args: pytest.fail("preflight must not collect remote source"))
    output = tmp_path / "not-created"
    monkeypatch.setattr(sys, "argv", _chip_capture_argv(receipt, raw) + ["--preflight", "--out-dir", str(output)])
    assert capture.main() == 0
    assert json.loads(capsys.readouterr().out) == {"preflight": "PASS"}
    assert not output.exists()
    assert receipt.read_bytes() == raw


@pytest.mark.parametrize("mutation,error", [
    ("hash", "SHA256"), ("member_alias", "成员身份"), ("schema", "schema"),
    ("platform", "target_environment.platform"), ("project", "target_environment.project"),
    ("android_version", "target_environment.android_version"),
])
def test_capture_checks_bad_receipt_before_remote_collection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str, error: str,
) -> None:
    capture = load_capture()
    receipt, raw = _chip_search_receipt(tmp_path)
    payload = json.loads(raw)
    if mutation in {"member_alias", "schema"}:
        payload[mutation] = "different"
    elif mutation != "hash":
        payload["target_environment"][mutation] = "different"
    if mutation != "hash":
        raw = json.dumps(payload).encode()
        receipt.write_bytes(raw)
    argv = _chip_capture_argv(receipt, raw)
    if mutation == "hash":
        argv[-1] = "a" * 64
    argv += ["--remote-snapshot", str(tmp_path / "never-read.json"),
        "--snapshot-workspace-id", "workspace", "--snapshot-command-id", "command",
        "--snapshot-sha256", "a" * 64, "--remote-source-root", "/source"]
    monkeypatch.setattr(capture, "load_member_profile", lambda _: argparse.Namespace(profile="member-test", member_alias="member-test"))
    monkeypatch.setattr(capture, "collect_snapshot_captures", lambda *args: pytest.fail("invalid receipt reached remote collection"))
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit, match=error):
        capture.main()


def test_capture_rechecks_receipt_after_resolving_real_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture = load_capture()
    receipt, raw = _chip_search_receipt(tmp_path)
    change = capture.RepositoryCapture(source_root="/source/frameworks/base", repo_path="frameworks/base",
        git_info={}, diff_text="diff", facts={}, module="frameworks-base",
        patch_name="unisoc14-frameworks-base@display-policy.patch",
        patch_rel="patches/unisoc14-frameworks-base@display-policy.patch")
    argv = _chip_capture_argv(receipt, raw)
    argv[argv.index("--project") + 1] = "unknown"
    argv += ["--remote-snapshot", "snapshot.json", "--snapshot-workspace-id", "workspace",
        "--snapshot-command-id", "command", "--snapshot-sha256", "a" * 64,
        "--remote-source-root", "/source"]
    monkeypatch.setattr(capture, "load_member_profile", lambda _: argparse.Namespace(profile="member-test", member_alias="member-test"))
    monkeypatch.setattr(capture, "collect_snapshot_captures", lambda *args: ([change], {}))
    monkeypatch.setattr(capture, "infer_capture_project_for_feature", lambda *args, **kwargs: ("TVI9999U", {}))
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit, match="target_environment.project"):
        capture.main()
    assert receipt.read_bytes() == raw


def test_explicit_unknown_does_not_relax_missing_pre_change_search() -> None:
    capture = load_capture()
    errors, _ = capture.validate_search_decision_for_status(
        argparse.Namespace(status="validated", workflow_contract="current_codex_skill"),
        {"searched": False, "decision": "unknown", "reuse_decision": "unknown"},
    )
    assert any("未发生" in error for error in errors)


def test_capture_instructions_keep_unknown_non_blocking_without_dropping_search_requirements() -> None:
    skill = (PLUGIN / "skills/android-patch-capture/SKILL.md").read_text()
    docs = (ROOT / "docs/skills/android-engineering-ops/android-patch-capture/README.md").read_text()
    assert "it does not downgrade an otherwise verified capture" in skill
    assert "不因此把已验证的补丁降为" in docs
    assert "keep it as `candidate` until" not in skill
    assert "the use decision is closed; do not submit it as `validated`" not in skill
    assert "current-workflow search evidence is fabricated or missing" in skill
    assert "retrospectively fabricate one" in skill
    assert "开发前搜索未发生或回执被伪造时仍停止" in docs
