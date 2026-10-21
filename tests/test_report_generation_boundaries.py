from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest


ROOT = Path(__file__).resolve().parents[1]
MEMBER = ROOT / "plugins/akbs-member-ops"
for path in (MEMBER / "lib", MEMBER / "internal/incoming-v2/scripts"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import akbs_member_intake as intake  # noqa: E402
from akbs_intake import submit as submit_module  # noqa: E402
from akbs_intake.io_utils import write_json  # noqa: E402
from akbs_intake.report_sessions import (  # noqa: E402
    SessionWork, project_customer_contexts_from_text, report_project_customers_from_clues,
)
from akbs_intake.reports import builder, common, daily_facts, weekly_facts  # noqa: E402
from akbs_intake.reports.common import (  # noqa: E402
    ensure_report_not_duplicate, ensure_report_submit_allowed, report_dates, report_session_privacy_errors,
)
from akbs_intake.reports.identity import infer_report_project  # noqa: E402
from akbs_intake.session_privacy import configure_report_session_consent, sanitize_work_summary  # noqa: E402
from akbs_member_ops.http_client import HttpClientFailure, failure_result  # noqa: E402


GIT_STATUS = "On branch TVI2343R_CRB2\nYour branch is up to date with origin/TVI2343R_CRB2."


@pytest.mark.parametrize("date,expected", [
    ("2026-10-12", "2026-10-16"), ("2026-06-15", "2026-06-18"),
    ("2026-09-14", "2026-09-20"), ("2026-09-28", "2026-09-30"),
    ("2026-02-16", "2026-02-22"),
])
def test_weekly_display_uses_packaged_work_calendar_offline(date: str, expected: str) -> None:
    assert common.weekly_display_date(dt.date.fromisoformat(date)) == expected


@pytest.mark.parametrize("contents", [None, "not-json", "[]", '{"holidays": [], "makeup_workdays": ["wrong"]}'])
def test_missing_or_bad_calendar_stops_before_any_package_or_history_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, contents: str | None,
) -> None:
    calendar = tmp_path / "calendar.json"
    if contents is not None:
        calendar.write_text(contents)
    monkeypatch.setattr(common, "WORK_CALENDAR_PATH", calendar)
    monkeypatch.setattr(weekly_facts, "load_history", Mock(side_effect=AssertionError("no history HTTP")))
    config = report_config(tmp_path)
    with pytest.raises(SystemExit, match="工作日历数据不足"):
        build("weekly", dt.date(2026, 10, 12), config)
    assert not Path(config["out_dir"]).exists()


@pytest.mark.parametrize("date", ["2025-12-31", "2026-12-31", "2027-01-05"])
def test_weekly_calendar_does_not_guess_uncovered_years(date: str) -> None:
    with pytest.raises(SystemExit, match="工作日历数据不足"):
        common.weekly_display_date(dt.date.fromisoformat(date))


def test_weekly_builder_passes_resolved_date_once_without_new_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provenance: None,
) -> None:
    date = dt.date(2026, 9, 28)
    config = report_config(tmp_path)
    monkeypatch.setattr(weekly_facts, "load_history", lambda *args: ([], [], {"source": "local_submitted"}))
    resolve = Mock(wraps=common.weekly_display_date)
    monkeypatch.setattr(builder, "weekly_display_date", resolve)
    package = build("weekly", date, config, weekly_facts_path=str(weekly_facts_file(tmp_path, date)))
    resolve.assert_called_once_with(date)
    view = json.loads((package / "materials/display/report_view.json").read_text())["payload"]
    assert view["display_date"] == "2026-09-30"
    assert view["week_range"] == "20260928-20261004"
    assert view["report_date"] == "2026-09-28"
    assert_fact_package(package, config, "explicit_weekly_facts")


@pytest.mark.parametrize("manifest", [[], None, True, "bad-root", 23])
def test_local_validate_non_object_manifest_returns_fail(tmp_path: Path, manifest) -> None:
    write_json(tmp_path / "manifest.json", manifest)
    result = intake.validate_package(tmp_path)
    assert result["status"] == "FAIL"
    assert result["errors"] == ["manifest.json 根节点必须是对象"]


def report_config(tmp_path: Path) -> dict[str, str]:
    return {"member_alias": "synthetic-member", "member_name": "Synthetic Member",
            "codex_home": str(tmp_path / "never-read-sessions"),
            "out_dir": str(tmp_path / "incoming"), "synthetic_data": "false",
            "timezone": "Asia/Shanghai", "include_patches": "false",
            "incoming_schema_version": "2"}


@pytest.fixture
def provenance(monkeypatch: pytest.MonkeyPatch) -> None:
    version = json.loads((MEMBER / ".codex-plugin/plugin.json").read_text())["version"]
    monkeypatch.setattr(intake, "plugin_install_metadata", lambda: {
        "plugin_name": "akbs-member-ops", "plugin_version": version,
    })
    gate = {"status": "PASS", "result": "PASS", "blocking": False,
            "plugin_version": version, "installed_plugin_version": version,
            "remote_plugin_version": version, "skill_cache_version": version,
            "checked_at": dt.datetime.now().isoformat()}
    monkeypatch.setattr(intake, "LAST_PLUGIN_VERSION_GATE", gate)
    monkeypatch.setattr(intake, "plugin_version_gate_check", lambda *args, **kwargs: dict(gate))


def actual_work() -> dict:
    return {"name": "完成接口核查", "did": ["核查接口调用"], "how": ["对照源码和验证记录"],
            "result": "核查完成", "status": "已完成"}


def daily_facts_file(tmp_path: Path, date: dt.date) -> Path:
    path = tmp_path / "daily-facts.json"
    write_json(path, {"schema": daily_facts.DAILY_FACTS_SCHEMA, "report_date": date.isoformat(),
                      "projects": [{"project": "TVI2343R", "customer": "海信", "work_type": "Patch",
                                    "today_topic": "接口核查", "current_result": "核查完成",
                                    "work_items": [actual_work()], "key_points": [], "dependencies": []}],
                      "documents": [], "standalone_work": [],
                      "tomorrow_plan": daily_facts.empty_tomorrow_plan()})
    return path


def weekly_facts_file(tmp_path: Path, date: dt.date) -> Path:
    path = tmp_path / "weekly-facts.json"
    week = report_dates("weekly", date)[3]
    write_json(path, {"schema": weekly_facts.WEEKLY_FACTS_SCHEMA, "week_range": week,
                      "projects": [], "standalone_work": [],
                      "documents": [{"work_type": "Doc", "document_name": "接口操作文档",
                                     "week_summary": "完成接口操作文档核查", "completed_this_week": 1,
                                     "remaining": 0, "completed_items": ["完成接口核查"], "remaining_items": [],
                                     "key_points": [], "risks": [], "dependencies": [], "next_week_plan": []}]})
    return path


def build(report_type: str, date: dt.date, config: dict[str, str], **kwargs) -> Path:
    return builder.build_report_package(
        report_type, date, config, run_id=kwargs.pop("run_id", f"{date:%Y%m%d}-091500-{report_type}"),
        incoming_schema_version="2", validate_package_fn=intake.validate_package,
        write_package_source_fn=intake.write_package_source,
        parse_sessions_fn=Mock(side_effect=AssertionError("session reader must not run")),
        discover_patches_fn=Mock(side_effect=AssertionError("patch discovery must not run")), **kwargs,
    )


def assert_fact_package(package: Path, config: dict[str, str], source: str) -> None:
    manifest = json.loads((package / "manifest.json").read_text())
    assert manifest["schema_version"] == "2"
    assert not (package / "materials/evidence/codex_sessions.json").exists()
    assert "materials/evidence/codex_sessions.json" not in manifest["files"]["evidence"]
    assert intake.validate_package(package)["status"] == "PASS"
    fact_name = "daily" if manifest["package_kind"] == "daily_trace" else "weekly"
    evidence = json.loads((package / f"materials/evidence/{fact_name}_fact_sources.json").read_text())
    assert evidence["payload"]["source"] == source
    findings = json.loads((package / "materials/evidence/work_findings.json").read_text())
    assert findings["payload"]["scanned_sources"] == [f"{fact_name}_fact_sources"]
    ensure_report_submit_allowed(package, config, manifest)


@pytest.mark.parametrize("report_type", ["daily", "weekly"])
def test_complete_facts_generate_without_session_read_or_fabricated_consent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provenance: None, report_type: str,
) -> None:
    date = dt.date.today() - dt.timedelta(days=2)
    config = report_config(tmp_path)
    monkeypatch.setattr(weekly_facts, "load_history", lambda *args: ([], [], {"source": "local_submitted"}))
    facts = daily_facts_file(tmp_path, date) if report_type == "daily" else weekly_facts_file(tmp_path, date)
    package = build(report_type, date, config, **{f"{report_type}_facts_path": str(facts)})
    assert_fact_package(package, config, f"explicit_{report_type}_facts")


@pytest.mark.parametrize("report_type", ["daily", "weekly"])
def test_failed_draft_can_be_completed_without_report_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provenance: None, report_type: str,
) -> None:
    date = dt.date.today() - dt.timedelta(days=2)
    config = report_config(tmp_path)
    if report_type == "daily":
        configure_report_session_consent(config, {date}, granted=True, fields=["work_summary", "command_summary"])
        failed = builder.build_report_package(
            report_type, date, config, run_id=f"{date:%Y%m%d}-090000-daily",
            incoming_schema_version="2", validate_package_fn=intake.validate_package,
            write_package_source_fn=intake.write_package_source,
            parse_sessions_fn=lambda *args: [SessionWork(
                project="TVI2343R", messages=["TVI2343R；完成接口核查，对照源码和验证记录，核查完成"],
                commands=["git diff"], source_work_type_hint="Patch",
            )], discover_patches_fn=lambda *args: [],
        )
    else:
        daily = {"package_key": "synthetic-daily-key", "report_date": date.isoformat(),
                 "report_view": {"projects": [], "standalone_work": [], "documents": [
                     {"work_type": "Doc", "document_name": "接口操作文档", "work_items": [actual_work()],
                      "key_points": [], "dependencies": ["等待接口负责人确认"]}]}}
        monkeypatch.setattr(weekly_facts, "load_history", lambda *args: (
            [daily], [], {"source": "akbs_api", "daily_package_keys": [daily["package_key"]],
                          "previous_weekly_package_keys": [], "api_errors": []},
        ))
        failed = build(report_type, date, config, run_id=f"{date:%Y%m%d}-090000-weekly")
    check = json.loads((failed / "local-check.json").read_text())
    assert check["status"] == "FAIL"
    assert check["errors"]
    assert check["report_generation_failed"] is True

    facts = daily_facts_file(tmp_path, date) if report_type == "daily" else weekly_facts_file(tmp_path, date)
    completed = build(report_type, date, config, **{f"{report_type}_facts_path": str(facts)})
    assert_fact_package(completed, config, f"explicit_{report_type}_facts")
    manifest = json.loads((completed / "manifest.json").read_text())
    assert manifest["report_submission_intent"] == "initial"
    assert "supersedes" not in manifest
    assert "replacement_for_run_id" not in manifest
    assert (failed / "manifest.json").is_file()
    assert "report_generation_failed" not in json.loads((completed / "local-check.json").read_text())


@pytest.mark.parametrize("report_type", ["daily", "weekly"])
@pytest.mark.parametrize("bucket,check,receipt", [
    ("pending", {"status": "PASS", "errors": []}, None),
    ("pending", None, None),
    ("pending", "invalid JSON", None),
    ("pending", {"status": "UNKNOWN", "errors": ["unknown result"]}, None),
    ("pending", {"status": "FAIL", "errors": ["local failure"]}, None),
    ("pending", {"status": "FAIL", "errors": ["local failure"], "report_generation_failed": False}, None),
    ("pending", {"status": "FAIL", "errors": ["local failure"], "report_generation_failed": "true"}, None),
    ("pending", {"status": "FAIL", "errors": []}, None),
    ("pending", {"status": "FAIL", "errors": "not a check error list"}, None),
    ("pending", {"status": "FAIL", "errors": ["local failure"]}, {"submitted": True}),
    ("pending", {"status": "FAIL", "errors": ["local failure"]}, {"status": "unknown"}),
    ("submitted", {"status": "FAIL", "errors": ["local failure"]}, None),
    ("submitted", {"status": "FAIL", "errors": ["local failure"], "report_generation_failed": True}, None),
    ("pending", {"status": "FAIL", "errors": ["local failure"], "report_generation_failed": True}, {"status": "unknown"}),
])
def test_effective_or_uncertain_reports_still_block_a_duplicate(
    tmp_path: Path, report_type: str, bucket: str, check, receipt,
) -> None:
    date = dt.date.today() - dt.timedelta(days=2)
    config = report_config(tmp_path)
    week = report_dates("weekly", date)[3]
    package = Path(config["out_dir"]) / bucket / f"{date:%Y%m%d}" / config["member_alias"] / "existing-run"
    write_json(package / "manifest.json", {"package_kind": f"{report_type}_trace",
               "member_alias": config["member_alias"], "date": date.isoformat(),
               "week_range": week, "run_id": "existing-run"})
    if isinstance(check, dict):
        write_json(package / "local-check.json", check)
    elif check is not None:
        (package / "local-check.json").write_text(check)
    if receipt is not None:
        write_json(package / ".akbs-upload-receipt.json", receipt)
    identity = date.isoformat() if report_type == "daily" else week
    with pytest.raises(SystemExit, match="已停止生成或上传第二个普通"):
        ensure_report_not_duplicate(config, report_type, identity, "new-run")


@pytest.mark.parametrize("report_type", ["daily", "weekly"])
def test_lost_submit_response_then_failed_recheck_does_not_allow_new_initial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provenance: None, report_type: str,
) -> None:
    date = dt.date.today() - dt.timedelta(days=2)
    config = report_config(tmp_path)
    facts = daily_facts_file(tmp_path, date) if report_type == "daily" else weekly_facts_file(tmp_path, date)
    arguments = {f"{report_type}_facts_path": str(facts)}
    package = build(report_type, date, config, **arguments)
    request = Mock(side_effect=HttpClientFailure(failure_result(TimeoutError("response lost"))))
    monkeypatch.setattr(submit_module, "submission_api_base_url", lambda _: "http://akbs.invalid/api")
    monkeypatch.setattr(submit_module, "request_json_with_metadata", request)

    with pytest.raises(SystemExit, match="HTTP 上传入口提交失败"):
        intake.submit_package(package, config)
    request.assert_called_once()
    assert not (package / ".akbs-upload-receipt.json").exists()
    report = package / f"reports/{report_type}.md"
    report.write_text(report.read_text() + "\n未同步的补充内容\n")
    with pytest.raises(SystemExit, match="本地工作包校验失败"):
        intake.submit_package(package, config)
    check = json.loads((package / "local-check.json").read_text())
    assert check["status"] == "FAIL" and check["errors"]
    assert "report_generation_failed" not in check
    request.assert_called_once()
    with pytest.raises(SystemExit, match="已停止生成或上传第二个普通"):
        build(report_type, date, config, run_id=f"{date:%Y%m%d}-100000-{report_type}", **arguments)


@pytest.mark.parametrize("helper", ["server", "http"])
def test_direct_upload_failure_removes_generation_failure_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provenance: None, helper: str,
) -> None:
    date = dt.date.today() - dt.timedelta(days=2)
    config = report_config(tmp_path)
    configure_report_session_consent(config, {date}, granted=True, fields=["work_summary", "command_summary"])
    package = builder.build_report_package(
        "daily", date, config, run_id=f"{date:%Y%m%d}-090000-daily",
        incoming_schema_version="2", validate_package_fn=intake.validate_package,
        write_package_source_fn=intake.write_package_source,
        parse_sessions_fn=lambda *args: [SessionWork(
            project="TVI2343R", messages=["TVI2343R；完成接口核查，对照源码和验证记录，核查完成"],
            commands=["git diff"], source_work_type_hint="Patch",
        )], discover_patches_fn=lambda *args: [],
    )
    check = json.loads((package / "local-check.json").read_text())
    assert check["status"] == "FAIL" and check["report_generation_failed"] is True
    ensure_report_not_duplicate(config, "daily", date.isoformat(), "new-run")
    request = Mock(side_effect=HttpClientFailure(failure_result(TimeoutError("response lost"))))
    monkeypatch.setattr(submit_module, "submission_api_base_url", lambda _: "http://akbs.invalid/api")
    monkeypatch.setattr(submit_module, "request_json_with_metadata", request)

    with pytest.raises(SystemExit, match="HTTP 上传入口提交失败"):
        if helper == "server":
            submit_module.server_submit_package(package, config)
        else:
            submit_module.http_submit_package(package, config, config["member_alias"], b"test-package")
    request.assert_called_once()
    expected_check = {key: value for key, value in check.items() if key != "report_generation_failed"}
    assert json.loads((package / "local-check.json").read_text()) == expected_check
    with pytest.raises(SystemExit, match="已停止生成或上传第二个普通"):
        ensure_report_not_duplicate(config, "daily", date.isoformat(), "new-run")


def test_effective_daily_history_generates_weekly_without_session_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provenance: None,
) -> None:
    date = dt.date.today() - dt.timedelta(days=2)
    daily = {"package_key": "synthetic-daily-key", "report_date": date.isoformat(),
             "report_view": {"projects": [], "standalone_work": [], "documents": [
                 {"work_type": "Doc", "document_name": "接口操作文档", "work_items": [actual_work()],
                  "key_points": [], "dependencies": []}]}}
    history = Mock(return_value=([daily], [], {"source": "akbs_api", "daily_package_keys": [daily["package_key"]],
                                              "previous_weekly_package_keys": [], "api_errors": []}))
    monkeypatch.setattr(weekly_facts, "load_history", history)
    config = report_config(tmp_path)
    package = build("weekly", date, config)
    history.assert_called_once()
    assert_fact_package(package, config, "akbs_api")


@pytest.mark.parametrize("authorized", [False, True])
def test_incomplete_daily_facts_only_use_existing_fallback_with_session_authorization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provenance: None, authorized: bool,
) -> None:
    date = dt.date.today() - dt.timedelta(days=2)
    config = report_config(tmp_path)
    path = daily_facts_file(tmp_path, date)
    facts = json.loads(path.read_text())
    del facts["projects"][0]["work_items"]
    write_json(path, facts)
    if authorized:
        configure_report_session_consent(config, {date}, granted=True, fields=["work_summary", "command_summary"])
    reader = Mock(return_value=[SessionWork(
        project="TVI2343R", messages=["TVI2343R 海信；完成接口修复", "对照源码并验证，结果已完成"],
        commands=["git diff"], source_work_type_hint="Patch",
    )])
    arguments = dict(
        run_id=f"{date:%Y%m%d}-091500-daily", incoming_schema_version="2",
        daily_facts_path=str(path), validate_package_fn=intake.validate_package,
        write_package_source_fn=intake.write_package_source, parse_sessions_fn=reader,
        discover_patches_fn=lambda *args: [],
    )
    if not authorized:
        with pytest.raises(SystemExit, match="session-consent"):
            builder.build_report_package("daily", date, config, **arguments)
        reader.assert_not_called()
        assert not Path(config["out_dir"]).exists()
        return
    package = builder.build_report_package("daily", date, config, **arguments)
    reader.assert_called_once()
    assert intake.validate_package(package)["status"] == "PASS"
    assert (package / "materials/evidence/codex_sessions.json").exists()
    view = json.loads((package / "materials/display/report_view.json").read_text())["payload"]
    assert view["projects"][0]["customer"] == "海信"
    assert view["projects"][0]["work_items"]


def test_weekly_with_no_facts_or_history_still_requires_session_authorization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    date = dt.date.today() - dt.timedelta(days=2)
    monkeypatch.setattr(weekly_facts, "load_history", lambda *args: ([], [], {"source": "local_submitted"}))
    config = report_config(tmp_path)
    with pytest.raises(SystemExit, match="session-consent"):
        build("weekly", date, config)
    assert not Path(config["out_dir"]).exists()


def test_scheduled_generate_only_runs_with_each_date_window_and_never_submits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provenance: None, capsys: pytest.CaptureFixture,
) -> None:
    config = report_config(tmp_path)
    monkeypatch.setattr(intake, "load_config", lambda profile: (dict(config), []))
    read = Mock(return_value=[SessionWork(project="TVI2343R", messages=[
        "TVI2343R 海信；完成接口修复", "通过源码对照完成验证，结果已完成"],
        commands=["git diff"], source_work_type_hint="Patch")])
    monkeypatch.setattr(intake, "parse_sessions", read)
    monkeypatch.setattr(intake, "discover_patches", lambda *args: [])
    submit = Mock(side_effect=AssertionError("generate-only must not submit"))
    monkeypatch.setattr(intake, "submit_package", submit)
    for offset in (3, 2):
        date = dt.date.today() - dt.timedelta(days=offset)
        result = intake.main(["daily", "--date", date.isoformat(), "--session-consent",
                              "--session-field", "work_summary", "--session-field", "command_summary",
                              "--prepare"])
        output = json.loads(capsys.readouterr().out)
        assert result == 0, output
        package = Path(output["package"])
        evidence = json.loads((package / "materials/evidence/codex_sessions.json").read_text())["payload"]
        assert evidence["time_range"]["start_date"] == evidence["time_range"]["end_date"] == date.isoformat()
        manifest = json.loads((package / "manifest.json").read_text())
        assert "supersedes" not in manifest
        assert manifest["report_submission_intent"] == "initial"
    assert read.call_count == 2
    submit.assert_not_called()


def test_report_rules_recognize_scheduled_generation_without_expanding_submission() -> None:
    for kind in ("daily", "weekly"):
        text = (MEMBER / f"skills/akbs-{kind}-report/SKILL.md").read_text()
        assert "member-configured recurring task" in text
        assert "Generation is not submission or revision authorization" in text
        assert "must not call `--upload`, `--submit-latest`, or a replacement option" in text
        assert "Do not reuse consent from a previous run or recurring automation" not in text


@pytest.mark.parametrize("text", [GIT_STATUS, sanitize_work_summary(GIT_STATUS),
                                 "TVI2343R\nYour branch is up to date", "On branch TVI2343R 海信",
                                 "/home/source/TVI2343R 海信"])
def test_git_or_next_line_never_establishes_customer(text: str) -> None:
    assert project_customer_contexts_from_text(text) == []


@pytest.mark.parametrize("text", ["TVI2343R 海信", "### **TVI2343R** 海信",
                                 "项目：TVI2343R 客户：海信", "项目客户：TVI2343R 海信"])
def test_same_line_customer_declaration_remains_supported(text: str) -> None:
    assert project_customer_contexts_from_text(text) == [("TVI2343R", {"customer_name": "海信"})]


def test_metadata_candidates_cannot_override_member_declaration() -> None:
    session = SessionWork(project="TVI2343R OtherCandidate", cwd="/source/TVI2343R Candidate",
                          messages=[sanitize_work_summary(GIT_STATUS), "TVI2343R 海信"])
    project, payload = infer_report_project("daily", "TVI2343R SummaryCandidate", {}, [session], [])
    assert project == "TVI2343R"
    assert payload["customer_name"] == "海信"
    assert weekly_facts.project_customer_identity_conflicts(
        [{"project": "TVI2343R", "customer": "海信", "work_type": "Patch"}],
        {"TVI2343R": {"customer_name": payload["customer_name"]}},
    ) == []


def test_flat_conflicting_declarations_do_not_silently_make_the_first_authoritative() -> None:
    clues = [("member declaration", "TVI2343R 海信"), ("member declaration", "TVI2343R AnotherCustomer")]
    for ordered in (clues, list(reversed(clues))):
        customers, basis = report_project_customers_from_clues(ordered)
        assert "TVI2343R" not in customers
        assert len(basis["TVI2343R"]) == 2
    rows = [{"project": "TVI2343R", "customer": "海信", "work_type": "Patch"}]
    assert weekly_facts.project_customer_identity_conflicts(rows, {"TVI2343R": {"customer_name": "AnotherCustomer"}})


def test_absent_session_evidence_requires_real_fact_provenance_and_invalid_session_evidence_still_fails() -> None:
    assert report_session_privacy_errors({})
    assert report_session_privacy_errors({"daily_fact_sources": {"payload": {"source": "session_draft"}}})
    facts = {"daily_fact_sources": {"payload": {"source": "explicit_daily_facts"}}}
    assert report_session_privacy_errors(facts) == []
    assert report_session_privacy_errors({**facts, "codex_sessions": {"payload": {"granted": True}}})
