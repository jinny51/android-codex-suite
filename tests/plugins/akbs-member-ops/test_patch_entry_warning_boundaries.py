from __future__ import annotations

import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
PLUGIN = ROOT / "plugins/akbs-member-ops"
for path in (PLUGIN / "lib", PLUGIN / "internal/incoming-v2/scripts", ROOT / "plugins/android-engineering-ops/lib"):
    sys.path.insert(0, str(path))

from akbs_intake import version_gate  # noqa: E402
from akbs_member_ops import knowledge_rules as member_rules  # noqa: E402
from android_engineering_ops import knowledge_rules as engineering_rules  # noqa: E402


def intake_module():
    spec = importlib.util.spec_from_file_location(
        "patch_entry_warning_intake", PLUGIN / "internal/incoming-v2/scripts/akbs_member_intake.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(("command", "action", "reason", "status", "family_blocking", "allowed"), [
    ("patch", "--prepare", "remote_transport_unavailable", "UNKNOWN", False, True),
    ("patch", "--upload", "remote_transport_unavailable", "UNKNOWN", False, False),
    ("patch", "--submit-latest", "remote_transport_unavailable", "UNKNOWN", False, False),
    ("daily", "--prepare", "remote_transport_unavailable", "UNKNOWN", False, False),
    ("weekly", "--prepare", "remote_transport_unavailable", "UNKNOWN", False, False),
    ("patch", "--prepare", "", "UNKNOWN", False, False),
    ("patch", "--prepare", "remote_transport_unavailable", "ACTIVE_IDENTITY_MISMATCH", False, False),
    ("patch", "--prepare", "remote_transport_unavailable", "UNKNOWN", True, False),
])
def test_only_local_patch_preparation_can_continue_when_version_transport_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys,
    command: str, action: str, reason: str, status: str, family_blocking: bool, allowed: bool,
) -> None:
    module = intake_module()
    gate = {"status": status, "blocking": True, "reason": reason, "message": "version unknown",
            "install_family": {"status": "UNKNOWN" if family_blocking else "PASS", "blocking": family_blocking}}
    monkeypatch.setattr(module, "load_config", lambda _profile: ({}, []))
    monkeypatch.setattr(module, "plugin_version_gate_check", lambda *args, **kwargs: gate)
    monkeypatch.setattr(module, "parse_date_arg", lambda *_: dt.date(2026, 10, 15))
    monkeypatch.setattr(module, "report_dates", lambda *_: ([], None, None, ""))
    monkeypatch.setattr(module, "configure_report_session_consent", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "enforce_mode_allowed", lambda *args: None)
    monkeypatch.setattr(module, "latest_pending", lambda *args: tmp_path)
    (tmp_path / "local-check.json").write_text('{"status":"PASS"}')
    prepared = []
    def prepare(*args, **kwargs):
        prepared.append(True)
        return tmp_path
    def forbidden(*args, **kwargs):
        pytest.fail("no upload/report or re-exec may be reached in this boundary test")
    monkeypatch.setattr(module, "prepare_patch_package", prepare)
    monkeypatch.setattr(module, "prepare_package", forbidden)
    monkeypatch.setattr(module, "submit_package", forbidden)
    argv = [command, action]
    if command == "daily":
        argv += ["--daily-facts", "/unused/facts.json"]
    assert module.main(argv) == (0 if allowed else 1)
    result = json.loads(capsys.readouterr().out)
    assert bool(prepared) is allowed
    assert result["plugin_freshness"]["blocking"] is (not allowed)
    if allowed:
        assert module.LAST_PLUGIN_VERSION_GATE == result["plugin_freshness"]


@pytest.mark.parametrize("rules", [member_rules, engineering_rules], ids=["member", "engineering"])
def test_wording_heuristics_are_review_hints_not_scope_verdicts(rules) -> None:
    result = rules.classify_function_scope("单一旋转功能，明确排除多个独立功能。", 2)
    assert result["status"] == "warn"
    assert result["issue_codes"] == ["aggregate_package"]
    assert result["messages"]
    assert all("拆分" not in item and "重新" not in item for item in result["messages"])
    assert rules.classify_function_scope("调整旋转位置配置和配套共享库", 2)["status"] == "pass"


def test_resource_name_similarity_warns_without_overriding_hard_validation(tmp_path: Path) -> None:
    module = intake_module()
    manifest = json.loads((ROOT / "contracts/incoming/v2/fixtures/patch.manifest.json").read_text())
    manifest["summary"] = "改善蓝牙配对提示"
    manifest["files"]["patches"] = ["patches/unisoc14-Settings@pairing.patch"]
    manifest["components"] = [{"layer": "application", "patches": manifest["files"]["patches"]}]
    readme = tmp_path / "README.md"
    readme.write_text("## 功能描述\n改善蓝牙配对提示\n## 修改点\n新增配对码输入、确认、取消及输入格式提示\n")
    patch = tmp_path / manifest["files"]["patches"][0]
    patch.parent.mkdir()
    keys = ["bluetooth_pairing_title", "pair_pin_entry", "pairing_confirmation_summary", "incorrect_pin_format", "pairing_cancel_button"]
    patch.write_text(
        'diff --git a/res/values/strings.xml b/res/values/strings.xml\n'
        '--- a/res/values/strings.xml\n+++ b/res/values/strings.xml\n@@ -1 +1,5 @@\n'
        + '\n'.join(f'+<string name="{key}">配对提示</string>' for key in keys) + '\n',
    )
    result = module.validate_incoming_package(tmp_path, manifest)
    assert not any("资源" in error and "范围" in error for error in result["errors"])
    assert any("资源" in warning and "复核" in warning for warning in result["warnings"])
    # Missing authoritative verification/files remain real hard failures.
    assert result["status"] == "FAIL"
    assert any("文件不存在" in error for error in result["errors"])
    manifest["components"][0]["layer"] = "invalid"
    invalid = module.validate_incoming_package(tmp_path, manifest)
    assert any("layer 非法" in error for error in invalid["errors"])


@pytest.mark.parametrize("command", ["patch", "daily", "weekly"])
@pytest.mark.parametrize("status", ["UPDATED_RESTART_REQUIRED", "SESSION_CACHE_STALE"])
def test_updated_or_stale_intake_stops_before_business_or_reexec(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys, command: str, status: str,
) -> None:
    module = intake_module()
    monkeypatch.delenv("CODEX_REPORT_PLUGIN_REEXEC_ATTEMPTED", raising=False)
    installed = tmp_path / "new-cache"
    script = installed / "internal/incoming-v2/scripts/akbs_member_intake.py"
    script.parent.mkdir(parents=True)
    script.write_text("# new cache fixture\n")
    gate = {"status": status, "blocking": True, "message": "请重启 Codex",
            "auto_update": {"status": "PASS", "installed_plugin_path": str(installed)}}
    monkeypatch.setattr(module, "load_config", lambda _profile: ({}, []))
    monkeypatch.setattr(module, "plugin_version_gate_check", lambda *args, **kwargs: gate)
    monkeypatch.setattr(version_gate, "latest_installed_plugin_cache_metadata", lambda *_: {})
    monkeypatch.setattr(module, "parse_date_arg", lambda *_: dt.date(2026, 10, 14))
    monkeypatch.setattr(module, "report_dates", lambda *_: ([], None, None, ""))
    monkeypatch.setattr(module, "configure_report_session_consent", lambda *args, **kwargs: None)

    def forbidden(*args, **kwargs):
        pytest.fail("updated/stale session must stop, not re-exec or prepare/submit")

    monkeypatch.setattr(module.os, "execv", forbidden)
    monkeypatch.setattr(module, "prepare_patch_package", forbidden)
    monkeypatch.setattr(module, "prepare_package", forbidden)
    monkeypatch.setattr(module, "submit_package", forbidden)
    argv = [command, "--prepare"]
    if command == "daily":
        argv += ["--daily-facts", "/unused/facts.json"]
    assert module.main(argv) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "FAIL"
    assert result["plugin_freshness"]["status"] == status
    assert "重启" in result["message"]
