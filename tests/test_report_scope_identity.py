from __future__ import annotations

import copy
import datetime as dt
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MEMBER = ROOT / "plugins/akbs-member-ops"
for path in (MEMBER / "lib", MEMBER / "internal/incoming-v2/scripts"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from akbs_intake.io_utils import write_json  # noqa: E402
from akbs_intake.reports import builder, daily_facts, scope, validation, weekly_facts  # noqa: E402
from akbs_intake.reports.common import report_dates  # noqa: E402
from akbs_intake.reports.weekly_ledger import empty_weekly_ledger  # noqa: E402
import akbs_member_intake as intake  # noqa: E402


PROJECT = "TVE1086U"
DATE = dt.date(2026, 10, 8)
WEEK = "20261005-20261011"


def identity(work_type: str, customer: str = "CustomerA", downstream: str = "") -> dict:
    row = {"project": PROJECT, "work_type": work_type, "customer": customer,
           "customer_name": customer, "downstream_customer": downstream}
    if work_type == "App":
        row["app_name"] = "ScopeDemo"
    return row


def daily_row(work_type: str, customer: str = "CustomerA", downstream: str = "") -> dict:
    return {
        **identity(work_type, customer, downstream),
        "today_topic": "核查独立范围", "current_result": "代码核查已完成",
        "work_items": [{"name": "核查接口", "did": ["核查接口调用"],
                        "how": ["对照调用栈和源码"], "result": "核查完成", "status": "已完成"}],
        "key_points": [], "dependencies": [],
    }


def weekly_raw_row(work_type: str, customer: str = "CustomerA", downstream: str = "") -> dict:
    ledger = empty_weekly_ledger(work_type)
    ledger.pop("baseline")
    zero = 0 if work_type == "App" else {"demand": 0, "migration": 0, "bug": 0, "bsp": 0}
    return {
        **identity(work_type, customer, downstream),
        "project_role": "协作", "week_summary": "本周核查独立范围的接口实现",
        "requirement_date": "2026-10-01", "requirement_source": "TL",
        "completed_this_week": copy.deepcopy(zero), "remaining": copy.deepcopy(zero),
        "completed_items": [], "remaining_items": [], "key_points": [], "risks": [],
        "dependencies": [], "next_week_plan": [], "ledger": ledger,
    }


def explicit_weekly(tmp_path: Path, rows: list[dict], week: str = WEEK) -> Path:
    path = tmp_path / "weekly-facts.json"
    write_json(path, {"schema": weekly_facts.WEEKLY_FACTS_SCHEMA, "week_range": week,
                      "projects": rows, "documents": [], "standalone_work": []})
    return path


def test_view_identity_allows_independent_patch_and_app_chains() -> None:
    rows = [identity("App", "CustomerA", "Factory"), identity("Patch", "CustomerB")]
    assert validation.report_project_customer_errors("view", rows, "projects", allow_work_scopes=True) == []
    assert validation.report_project_customer_errors("view", rows, "projects", allow_work_scopes=False)


@pytest.mark.parametrize("changed_downstream", [False, True])
def test_same_scope_duplicate_or_conflict_still_rejected(changed_downstream: bool) -> None:
    first = identity("App")
    second = {**first, "downstream_customer": "Factory"} if changed_downstream else dict(first)
    errors = validation.report_project_customer_errors("view", [first, second], "projects", allow_work_scopes=True)
    assert any("统计对象重复" in error for error in errors)


@pytest.mark.parametrize("work_type", ["Patch", "App", "GMS"])
def test_direct_customer_is_a_scope_dimension_but_downstream_is_not(work_type: str) -> None:
    first, second = identity(work_type), identity(work_type, "CustomerB")
    if work_type == "GMS":
        first.update(gms_release_type="IR", gms_target="A14")
        second.update(gms_release_type="IR", gms_target="A14")
    assert scope.report_scope_key(first) != scope.report_scope_key(second)
    assert scope.report_scope_key(first) == scope.report_scope_key({**first, "downstream_customer": "Factory"})
    assert validation.report_project_customer_errors("view", [first, second], "projects", allow_work_scopes=True) == []
    contexts = scope.report_customer_contexts_from_rows([first, second])
    assert PROJECT not in contexts
    assert validation.weekly_project_identity_consistency_errors("view", [first, second], [second, first]) == []


def test_distinct_apps_and_gms_targets_use_existing_scope_key() -> None:
    first = identity("App", "CustomerA")
    second = {**identity("App", "CustomerB"), "app_name": "AnotherApp"}
    gms_a = {**identity("GMS", "CustomerA"), "gms_release_type": "GMS", "gms_target": "TargetA"}
    gms_b = {**identity("GMS", "CustomerB"), "gms_release_type": "GMS", "gms_target": "TargetB"}
    assert validation.report_project_customer_errors("view", [first, second, gms_a, gms_b], "projects", allow_work_scopes=True) == []


def test_scoped_evidence_is_order_independent_and_does_not_lose_one_scope() -> None:
    rows = [identity("App", "CustomerA", "Factory"), identity("Patch", "CustomerB")]
    evidence = builder.report_customer_rows(rows)
    assert validation.weekly_project_identity_consistency_errors("view", rows, list(reversed(evidence))) == []
    bad = copy.deepcopy(evidence)
    bad[0]["downstream_customer"] = "ChangedFactory"
    assert validation.weekly_project_identity_consistency_errors("view", rows, bad)
    assert validation.weekly_project_identity_consistency_errors("view", rows, evidence[1:])


def test_duplicate_scoped_source_evidence_cannot_last_write_win() -> None:
    row = identity("App")
    evidence = builder.report_customer_rows([row])
    contradictory = {**evidence[0], "customer_name": "CustomerB"}
    assert validation.weekly_project_identity_consistency_errors("view", [row], [contradictory, evidence[0]])
    assert validation.weekly_project_identity_consistency_errors("view", [row], [evidence[0], evidence[0]])
    with pytest.raises(ValueError, match="身份矛盾"):
        scope.report_customer_contexts_from_rows([row, {**row, "downstream_customer": "ChangedFactory"}])


def test_flat_evidence_remains_valid_only_when_chains_are_unambiguous() -> None:
    app, patch = identity("App"), identity("Patch")
    flat = [{"project": PROJECT, "customer_name": "CustomerA"}]
    assert validation.weekly_project_identity_consistency_errors("view", [app], flat) == []
    assert validation.weekly_project_identity_consistency_errors("view", [app, patch], flat * 2) == []
    patch["customer"] = patch["customer_name"] = "CustomerB"
    assert validation.weekly_project_identity_consistency_errors("view", [app, patch], flat)


def test_project_hint_does_not_override_explicit_multiple_scopes() -> None:
    rows = [identity("App", "CustomerA", "Factory"), identity("Patch", "CustomerB")]
    generic = {PROJECT: {"customer_name": "GenericProjectHint", "downstream_customer": "Other"}}
    assert weekly_facts.project_customer_identity_conflicts(rows, generic) == []
    assert weekly_facts.project_customer_identity_conflicts([rows[0]], generic)
    contexts = scope.report_customer_contexts_from_rows(rows)
    assert PROJECT not in contexts
    assert contexts[scope.report_scope_key(rows[0])]["downstream_customer"] == "Factory"
    assert weekly_facts.project_customer_identity_conflicts(rows, contexts) == []
    contexts[scope.report_scope_key(rows[1])]["downstream_customer"] = "Other"
    assert weekly_facts.project_customer_identity_conflicts(rows, contexts)


def test_daily_and_tomorrow_scopes_are_independent_but_required_and_unique() -> None:
    rows = [daily_row("App", "CustomerA", "Factory"), daily_row("Patch", "CustomerB")]
    assert daily_facts.validate_daily_projects(rows, expected_project_customers={PROJECT: "GenericHint"}) == []
    contexts = scope.report_customer_contexts_from_rows(rows)
    assert daily_facts.validate_daily_projects(rows, expected_project_customers=contexts) == []
    contexts[scope.report_scope_key(rows[0])]["customer_name"] = "Changed"
    assert daily_facts.validate_daily_projects(rows, expected_project_customers=contexts)
    plan_rows = [{**identity("App", "CustomerA", "Factory"), "plan_items": ["继续接口核查"]},
                 {**identity("Patch", "CustomerB"), "plan_items": ["继续源码核查"]}]
    plan = {"projects": plan_rows, "documents": [], "standalone_work": []}
    assert daily_facts.validate_tomorrow_plan(plan) == []
    assert daily_facts.validate_tomorrow_plan({**plan, "projects": plan_rows + [plan_rows[0]]})
    assert daily_facts.validate_daily_projects(rows + [rows[0]])
    assert daily_facts.validate_daily_projects([{**rows[0], "customer": "", "customer_name": ""}])


def test_current_weekly_explicit_facts_allow_distinct_chains_without_relaxing_ledger(tmp_path: Path) -> None:
    raw = [weekly_raw_row("App", "CustomerA", "Factory"), weekly_raw_row("Patch", "CustomerB")]
    rows = weekly_facts.load_explicit_facts(explicit_weekly(tmp_path, raw), WEEK,
                                         expected_project_customers={PROJECT: "GenericHint"})
    assert [(r["customer"], r["downstream_customer"]) for r in rows] == [("CustomerA", "Factory"), ("CustomerB", "")]
    with pytest.raises(SystemExit, match="重复"):
        weekly_facts.load_explicit_facts(explicit_weekly(tmp_path, raw + [raw[0]]), WEEK)
    bad = copy.deepcopy(raw)
    bad[0]["ledger"]["opening_completed_before_week"] = 0
    with pytest.raises(SystemExit, match="未知字段"):
        weekly_facts.load_explicit_facts(explicit_weekly(tmp_path, bad), WEEK)


def test_history_customer_is_scope_bound_and_tomorrow_cannot_overwrite_actual() -> None:
    app, patch = daily_row("App", "CustomerA", "Factory"), daily_row("Patch", "CustomerB")
    tomorrow = {**identity("App", "TomorrowCustomer", "TomorrowFactory"), "plan_items": ["继续核查"]}
    daily = [{"package_key": "synthetic/daily", "report_date": DATE.isoformat(),
              "report_view": {"projects": [app, patch], "tomorrow_plan": {"projects": [tomorrow]}}}]
    metadata, records = weekly_facts._daily_project_records(daily)
    assert "customer" not in metadata[PROJECT]
    for work_type, customer, downstream in [("App", "CustomerA", "Factory"), ("Patch", "CustomerB", "")]:
        prior = {"work_type": work_type, "app_name": "ScopeDemo" if work_type == "App" else ""}
        own_records = [r for r in records[PROJECT] if r["work_type"] == work_type]
        row, _ = weekly_facts._history_project_row(PROJECT, prior, metadata[PROJECT], own_records,
                                                 period_start=DATE, as_of=DATE)
        assert (row["customer"], row["downstream_customer"]) == (customer, downstream)
        assert row["completed_this_week_total"] == 1
    missing_scope = {"work_type": "App", "app_name": "MissingApp"}
    row, missing = weekly_facts._history_project_row(PROJECT, missing_scope, metadata[PROJECT], [],
                                                   period_start=DATE, as_of=DATE)
    assert row["customer"] == "需成员补充客户名"
    assert f"{PROJECT}.customer" in missing


@pytest.mark.parametrize("previous_type,new_type", [("App", "Patch"), ("Patch", "App")])
def test_new_scope_never_counts_against_only_previous_scope(previous_type: str, new_type: str,
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    prior = {**identity(previous_type, "PriorCustomer"), "project_role": "协作",
             "requirement_date": "2026-10-01", "requirement_source": "TL",
             "completed_this_week": "本周完成 0 项", "remaining": "当前剩余 0 项",
             "completed_items": [], "remaining_items": [], "next_week_plan": []}
    daily = [{"package_key": "synthetic/daily", "report_date": DATE.isoformat(),
              "report_view": {"projects": [daily_row(new_type, "NewCustomer")]}}]
    previous = [{"package_key": "synthetic/previous", "week_range": "20260928-20261004",
                 "report_view": {"projects": [prior]}}]
    monkeypatch.setattr(weekly_facts, "load_history", lambda *args: (daily, previous, {"source": "synthetic_fixture"}))
    monkeypatch.setattr(weekly_facts, "local_now", lambda config: dt.datetime(2026, 10, 11))
    result = weekly_facts.build_weekly_facts({}, dt.date(2026, 10, 5), dt.date(2026, 10, 11), WEEK)
    rows = {row["work_type"]: row for row in result.projects}
    assert rows[previous_type]["completed_this_week_total"] == 0
    assert rows[new_type]["completed_this_week_total"] == 1
    assert rows[new_type]["customer"] == "NewCustomer"
    assert rows[new_type]["project_role"] == "需成员确认"
    assert result.evidence["missing_fields"]


def test_explicit_type_cannot_be_text_matched_into_wrong_scope() -> None:
    prior = {**identity("App"), "completed_items": ["核查接口"]}
    record = {"work_type": "Patch", "text": "核查接口", "progress": "已完成"}
    assigned, ambiguous = weekly_facts.assign_daily_records_to_scopes([prior], [record])
    assert assigned == [[]]
    assert ambiguous


def test_same_named_daily_work_keeps_customer_attention_plan_and_prior_assignment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, second = daily_row("App"), daily_row("App", "CustomerB")
    first.update(key_points=["客户甲接口确认"], dependencies=["等待客户甲设备"])
    second.update(key_points=["客户乙接口确认"], dependencies=["等待客户乙账号"])
    second["work_items"][0].update(status="处理中", result="接口仍在核查")
    daily = [
        {"package_key": "synthetic/daily-a", "report_date": "2026-10-06", "report_view": {
            "projects": [first], "tomorrow_plan": {"projects": [
                {**identity("App"), "plan_items": ["客户甲继续验收"]}]} }},
        {"package_key": "synthetic/daily-b", "report_date": "2026-10-07", "report_view": {
            "projects": [second], "tomorrow_plan": {"projects": [
                {**identity("App", "CustomerB"), "plan_items": ["客户乙继续核查"]}]} }},
    ]
    metadata, records = weekly_facts._daily_project_records(daily)
    assert len(records[PROJECT]) == 2
    seeds = weekly_facts.daily_scope_seed_rows(records[PROJECT])
    assigned, ambiguous = weekly_facts.assign_daily_records_to_scopes(seeds, records[PROJECT])
    assert not ambiguous and [len(rows) for rows in assigned] == [1, 1]
    for prior, rows in zip(seeds, assigned):
        assert rows[0]["customer"] == prior["customer"]
    monkeypatch.setattr(weekly_facts, "load_history", lambda *args: (daily, [], {"source": "synthetic_fixture"}))
    monkeypatch.setattr(weekly_facts, "local_now", lambda config: dt.datetime(2026, 10, 11))
    result = weekly_facts.build_weekly_facts({}, dt.date(2026, 10, 5), dt.date(2026, 10, 11), WEEK)
    rows = {row["customer"]: row for row in result.projects}
    assert set(rows) == {"CustomerA", "CustomerB"}
    assert rows["CustomerA"]["completed_this_week_total"] == 1
    assert rows["CustomerB"]["completed_this_week_total"] == 0
    assert rows["CustomerA"]["key_points"] == ["客户甲接口确认"]
    assert rows["CustomerB"]["key_points"] == ["客户乙接口确认"]
    assert rows["CustomerA"]["dependencies"] == ["等待客户甲设备"]
    assert rows["CustomerB"]["dependencies"] == ["等待客户乙账号"]
    assert rows["CustomerA"]["next_week_plan"] == ["客户甲继续验收"]
    assert rows["CustomerB"]["next_week_plan"] == ["客户乙继续核查"]
    # A record with an explicit different customer cannot borrow the sole old scope.
    assignment, ambiguous = weekly_facts.assign_daily_records_to_scopes([seeds[0]], assigned[1])
    assert assignment == [[]] and ambiguous
    legacy_record = {**assigned[1][0], "work_type": ""}
    assignment, ambiguous = weekly_facts.assign_daily_records_to_scopes([seeds[0]], [legacy_record])
    assert assignment == [[]] and ambiguous
    assignment, ambiguous = weekly_facts.assign_daily_records_to_scopes(seeds, [legacy_record])
    assert not ambiguous and assignment == [[], [legacy_record]]
    # A historical row with no customer keeps the existing unique-scope fallback.
    legacy_record.pop("customer")
    assignment, ambiguous = weekly_facts.assign_daily_records_to_scopes([seeds[0]], [legacy_record])
    assert not ambiguous and assignment == [[legacy_record]]


def test_downstream_supplement_cannot_reopen_existing_ledger_or_reset_total(tmp_path: Path) -> None:
    previous_raw = weekly_raw_row("App")
    previous_raw.update(project_role="主责", work_total=10, completed_this_week=2, remaining=8,
                        completed_items=["完成两个接口"], remaining_items=["核查剩余接口"],
                        next_week_plan=["继续核查剩余接口"])
    previous_raw["ledger"].update(opening=True, project_completed=2)
    previous = weekly_facts.load_explicit_facts(explicit_weekly(tmp_path, [previous_raw], "20260928-20261004"),
                                               "20260928-20261004")[0]
    previous.update(_package_key="synthetic/previous", _week_range="20260928-20261004")
    previous_projects = {PROJECT: [previous]}
    current = weekly_raw_row("App", downstream="Factory")
    current.update(project_role="主责", work_total=1, completed_this_week=1, remaining=0,
                   completed_items=["核查接口"], remaining_items=[])
    current["ledger"].update(opening=True, project_completed=1)
    with pytest.raises(SystemExit, match="不得重新开项或重置总量"):
        weekly_facts.load_explicit_facts(explicit_weekly(tmp_path, [current]), WEEK,
                                         previous_projects=previous_projects)
    current.update(work_total=10, remaining=7, remaining_items=["核查剩余接口"],
                   next_week_plan=["继续核查剩余接口"])
    current["ledger"].update(opening=False, baseline_package_key="synthetic/previous",
                             baseline_week_range="20260928-20261004")
    rows = weekly_facts.load_explicit_facts(explicit_weekly(tmp_path, [current]), WEEK,
                                           previous_projects=previous_projects)
    assert rows[0]["work_total"] == 10 and rows[0]["remaining_total"] == 7
    assert rows[0]["downstream_customer"] == "Factory"
    assert rows[0]["ledger"]["baseline"]["total"] == 10
    # A genuinely different direct customer is an independent new scope.
    current.update(customer="CustomerB", customer_name="CustomerB", work_total=1,
                   remaining=0, remaining_items=[], next_week_plan=[])
    current["ledger"].update(opening=True, baseline_package_key="", baseline_week_range="")
    rows = weekly_facts.load_explicit_facts(explicit_weekly(tmp_path, [current]), WEEK,
                                           previous_projects=previous_projects)
    assert rows[0]["ledger"]["opening"] is True and rows[0]["work_total"] == 1


@pytest.mark.parametrize("report_type", ["daily", "weekly"])
@pytest.mark.parametrize("same_work_type", [False, True])
def test_builder_preserves_scope_identity_in_view_and_source_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, report_type: str, same_work_type: bool,
) -> None:
    date = dt.date.today() - dt.timedelta(days=2)
    _, _, _, week = report_dates(report_type, date)
    monkeypatch.setattr(builder, "infer_report_project", lambda *args: (PROJECT, {
        "project": PROJECT, "projects": [PROJECT],
        "project_customers": [{"project": PROJECT, "customer_name": "GenericHint"}],
    }))
    monkeypatch.setattr(weekly_facts, "load_history", lambda *args: ([], [], {"source": "synthetic_fixture"}))
    config = {"member_alias": "synthetic-member", "member_name": "Synthetic Member",
              "out_dir": str(tmp_path / "incoming"), "synthetic_data": "true", "timezone": "Asia/Shanghai"}
    version = json.loads((MEMBER / ".codex-plugin/plugin.json").read_text())["version"]
    monkeypatch.setattr(intake, "plugin_install_metadata", lambda: {
        "plugin_name": "akbs-member-ops", "plugin_version": version,
    })
    monkeypatch.setattr(intake, "LAST_PLUGIN_VERSION_GATE", {
        "status": "PASS", "result": "PASS", "blocking": False,
        "plugin_version": version, "installed_plugin_version": version,
        "remote_plugin_version": version, "skill_cache_version": version,
        "checked_at": dt.datetime.now().isoformat(),
    })
    if report_type == "weekly":
        facts = explicit_weekly(tmp_path, [weekly_raw_row("App", "CustomerA", "Factory"),
                                          weekly_raw_row("App" if same_work_type else "Patch", "CustomerB")], week)
        kwargs = {"weekly_facts_path": str(facts)}
    else:
        facts = tmp_path / "daily-facts.json"
        write_json(facts, {"schema": daily_facts.DAILY_FACTS_SCHEMA, "report_date": date.isoformat(),
                           "projects": [daily_row("App", "CustomerA", "Factory"), daily_row("App" if same_work_type else "Patch", "CustomerB")],
                           "documents": [], "standalone_work": [], "tomorrow_plan": daily_facts.empty_tomorrow_plan()})
        kwargs = {"daily_facts_path": str(facts)}

    package = builder.build_report_package(report_type, date, config, run_id=f"{date:%Y%m%d}-091500-scope",
                                           incoming_schema_version="2", validate_package_fn=intake.validate_package,
                                           write_package_source_fn=intake.write_package_source, parse_sessions_fn=lambda *args: [],
                                           discover_patches_fn=lambda *args: [], **kwargs)
    source = json.loads((package / "materials/evidence/project_inference.json").read_text())["payload"]
    view = json.loads((package / "materials/display/report_view.json").read_text())["payload"]
    assert "customer_name" not in source and "downstream_customer" not in source
    expected = {"CustomerA": ("App", "Factory"), "CustomerB": ("App" if same_work_type else "Patch", "")}
    assert {r["customer_name"]: (r["work_type"], r.get("downstream_customer", ""))
            for r in source["project_customers"]} == expected
    assert {r["customer"]: (r["work_type"], r.get("downstream_customer", ""))
            for r in view["projects"]} == expected
    assert "CustomerA" in view["material_name"] and "CustomerB" in view["material_name"]
    assert "需成员补充客户名" not in view["material_name"]
    check = json.loads((package / "local-check.json").read_text())
    assert check["status"] == "PASS", check["errors"]
    assert intake.validate_package(package)["status"] == "PASS"
    source_metadata = json.loads((package / "materials/evidence/source.json").read_text())["payload"]
    assert source_metadata["member_alias"] == config["member_alias"]
    assert source_metadata["plugin_version"] == version
    assert source_metadata["plugin_commit"]
    # The formal package validator must reject display/source/hash drift.
    view_path = package / "materials/display/report_view.json"
    document = json.loads(view_path.read_text())
    document["payload"]["projects"][0]["downstream_customer"] = "ChangedAfterGeneration"
    write_json(view_path, document)
    changed = intake.validate_package(package)
    assert changed["status"] == "FAIL"
    assert any("report_render_binding.report_view_sha256" in error for error in changed["errors"])
