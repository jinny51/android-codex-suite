from __future__ import annotations

import datetime as dt
import shutil
from pathlib import Path
from typing import Any, Callable

from akbs_intake.config import expanded_path, local_now, require_config, require_safe_artifact_path, synthetic_mode
from akbs_intake.io_utils import materials_rel, write_json
from akbs_intake.report_sessions import SessionWork, synthetic_sessions
from akbs_intake.session_privacy import require_report_session_consent, session_evidence_payload
from akbs_intake.reports.common import ensure_report_date_allowed, ensure_report_not_duplicate, report_dates, report_identity, ymd
from akbs_intake.reports.identity import infer_report_project
from akbs_intake.reports.daily_facts import build_daily_facts, project_rows_to_items as daily_project_rows_to_items
from akbs_intake.reports.render import write_report, write_report_view
from akbs_intake.reports.render_binding import write_report_render_binding
from akbs_intake.reports.session_summary import (
    daily_work_items_from_scopes,
    daily_work_scopes,
    items_by_project,
    overview_text,
    work_findings_payload,
)
from akbs_intake.reports.weekly_facts import build_weekly_facts, project_rows_to_items
from akbs_intake.search_usage import search_usage_payload
from akbs_intake.patch.assets import PatchInfo
from akbs_intake.patch.package_quality import write_default_evidence

ValidatePackage = Callable[[Path], dict[str, Any]]
WritePackageSource = Callable[[Path, dict[str, str], str], dict[str, Any]]
ParseSessions = Callable[[dict[str, str], set[dt.date]], list[SessionWork]]
DiscoverPatches = Callable[[dict[str, str], list[SessionWork], dt.date, dt.date], list[PatchInfo]]


def incoming_report_manifest(
    report_type: str,
    date: dt.date,
    week_key: str,
    config: dict[str, str],
    summary: str,
    source: dict[str, Any],
    run_id: str,
    schema_version: str,
    project: str = "",
    project_evidence_path: str = "",
    display_path: str = "",
    has_non_project_work: bool = False,
) -> dict[str, Any]:
    report_name = f"{report_type}.md"
    package_kind = "daily_trace" if report_type == "daily" else "weekly_trace"
    manifest: dict[str, Any] = {
        "schema": "knowledge-incoming-package",
        "schema_version": schema_version,
        "package_kind": package_kind,
        "member_alias": config["member_alias"],
        "member_name": config["member_name"],
        "date": date.isoformat(),
        "run_id": run_id,
        "tool": "android-knowledge-intake",
        "report_type": report_type,
        "report_path": f"reports/{report_name}",
        "summary": summary,
        "files": {
            "evidence": [
                materials_rel("evidence", "source.json"),
                materials_rel("evidence", "codex_sessions.json"),
                materials_rel("evidence", "work_findings.json"),
            ],
            "display": [display_path or materials_rel("display", "report_view.json")],
        },
    }
    if report_type == "weekly":
        manifest["week_range"] = week_key
    if report_type == "daily" and project:
        manifest["project"] = project
    if has_non_project_work:
        manifest["has_non_project_work"] = True
    if project_evidence_path:
        manifest["files"]["evidence"].append(project_evidence_path)
    return manifest

def build_report_package(
    report_type: str,
    date: dt.date,
    config: dict[str, str],
    run_id: str | None = None,
    schema_version: str = "",
    replace_report_run_id: str = "",
    daily_facts_path: str = "",
    weekly_facts_path: str = "",
    *,
    incoming_schema_version: str,
    validate_package_fn: ValidatePackage,
    write_package_source_fn: WritePackageSource,
    parse_sessions_fn: ParseSessions,
    discover_patches_fn: DiscoverPatches,
) -> Path:
    require_config(config)
    schema_version = schema_version or incoming_schema_version
    if schema_version != incoming_schema_version:
        raise SystemExit(f"incoming 只支持 schema_version={incoming_schema_version}")
    dates, start, end, week_key = report_dates(report_type, date)
    ensure_report_date_allowed(report_type, date, config)
    is_synthetic = synthetic_mode(config)
    consent = require_report_session_consent(config, dates, synthetic=is_synthetic)
    run_id = run_id or f"{ymd(date)}-{local_now(config):%H%M%S}"
    out_dir = require_safe_artifact_path(expanded_path(config["out_dir"]), purpose="out_dir")
    package_dir = require_safe_artifact_path(out_dir / "pending" / ymd(date) / config["member_alias"] / run_id, purpose="incoming package output")
    if package_dir.exists():
        raise SystemExit(f"工作包已存在: {package_dir}")
    report_duplicates: list[dict[str, str]] = []
    replace_report_run_id = str(replace_report_run_id or "").strip()
    if report_type in {"daily", "weekly"}:
        report_duplicates = ensure_report_not_duplicate(
            config,
            report_type,
            report_identity(report_type, date, week_key),
            run_id,
            replace_report_run_id,
        )
    if is_synthetic:
        sessions = synthetic_sessions(config, dates)
        patches = []
    else:
        sessions = parse_sessions_fn(config, dates)
        patches = discover_patches_fn(config, sessions, start, end)
    daily_scopes = daily_work_scopes(sessions, patches) if report_type == "daily" else []
    daily_work_items = daily_work_items_from_scopes(daily_scopes) if report_type == "daily" else {}
    for session in sessions:
        session.cwd = ""
    session_items = items_by_project(sessions, patches)
    preliminary_summary = overview_text(report_type, session_items, patches)
    report_project, project_payload = infer_report_project(report_type, preliminary_summary, session_items, sessions, patches)
    project_customers: dict[str, dict[str, str]] = {}
    for item in project_payload.get("project_customers", []):
        if not isinstance(item, dict) or not item.get("project") or not item.get("customer_name"):
            continue
        context = {"customer_name": str(item["customer_name"])}
        if item.get("downstream_customer"):
            context["downstream_customer"] = str(item["downstream_customer"])
        project_customers[str(item["project"])] = context
    weekly_projects: list[dict[str, Any]] = []
    weekly_documents: list[dict[str, Any]] = []
    weekly_standalone_work: list[dict[str, Any]] = []
    daily_projects: list[dict[str, Any]] = []
    daily_documents: list[dict[str, Any]] = []
    daily_standalone_work: list[dict[str, Any]] = []
    daily_tomorrow_plan: dict[str, list[dict[str, Any]]] = {
        "projects": [],
        "documents": [],
        "standalone_work": [],
    }
    daily_fact_evidence_path = ""
    weekly_fact_evidence_path = ""
    items = session_items
    if report_type == "daily":
        daily_facts = build_daily_facts(
            date,
            explicit_path=daily_facts_path,
            synthetic=is_synthetic,
            project_items=session_items,
            daily_work_items=daily_work_items,
            project_customers=project_customers,
            inferred_scopes=daily_scopes,
        )
        daily_projects = daily_facts.projects
        daily_documents = daily_facts.documents
        daily_standalone_work = daily_facts.standalone_work
        daily_tomorrow_plan = daily_facts.tomorrow_plan
        if daily_projects:
            items = daily_project_rows_to_items(daily_projects)
            for row in daily_projects:
                project = str(row.get("project") or "")
                customer = str(row.get("customer") or row.get("customer_name") or "")
                if not project or not customer:
                    continue
                context = {"customer_name": customer}
                if row.get("downstream_customer"):
                    context["downstream_customer"] = str(row["downstream_customer"])
                project_customers[project] = context
        daily_fact_evidence_path = materials_rel("evidence", "daily_fact_sources.json")
        package_dir.mkdir(parents=True)
        write_json(
            package_dir / daily_fact_evidence_path,
            {"kind": "daily_fact_sources", "payload": daily_facts.evidence},
        )
    if report_type == "weekly":
        weekly_facts = build_weekly_facts(
            config,
            start,
            end,
            week_key,
            explicit_path=weekly_facts_path,
            synthetic=is_synthetic,
            fallback_items=session_items,
            project_customers=project_customers,
        )
        weekly_projects = weekly_facts.projects
        weekly_documents = weekly_facts.documents
        weekly_standalone_work = weekly_facts.standalone_work
        if weekly_projects:
            items = project_rows_to_items(weekly_projects)
            for row in weekly_projects:
                project = str(row.get("project") or "")
                customer = str(row.get("customer") or row.get("customer_name") or "")
                if project and customer:
                    context = {"customer_name": customer}
                    if row.get("downstream_customer"):
                        context["downstream_customer"] = str(row["downstream_customer"])
                    project_customers[project] = context
        weekly_fact_evidence_path = materials_rel("evidence", "weekly_fact_sources.json")
        package_dir.mkdir(parents=True)
        write_json(
            package_dir / weekly_fact_evidence_path,
            {"kind": "weekly_fact_sources", "payload": weekly_facts.evidence},
        )
    summary = overview_text(report_type, items, patches)
    report_project, project_payload = infer_report_project(report_type, summary, items, sessions, patches)
    if daily_projects:
        fact_customers = []
        for row in daily_projects:
            customer_row = {
                "project": str(row.get("project") or ""),
                "customer_name": str(row.get("customer") or row.get("customer_name") or ""),
            }
            if row.get("downstream_customer"):
                customer_row["downstream_customer"] = str(row["downstream_customer"])
            if customer_row["project"] and customer_row["customer_name"] and customer_row not in fact_customers:
                fact_customers.append(customer_row)
        project_payload["project_customers"] = fact_customers
        project_payload["projects"] = sorted({row["project"] for row in fact_customers})
        project_payload["customer_basis"] = {
            row["project"]: ["daily_fact_sources"]
            for row in fact_customers
        }
        if len(project_payload["projects"]) == 1:
            report_project = project_payload["projects"][0]
            project_payload["project"] = report_project
            context = next(row for row in fact_customers if row["project"] == report_project)
            project_payload["customer_name"] = context["customer_name"]
            if context.get("downstream_customer"):
                project_payload["downstream_customer"] = context["downstream_customer"]
        else:
            report_project = ""
            project_payload["project"] = ""
    if weekly_projects:
        fact_customers = []
        for row in weekly_projects:
            if not row.get("project") or not (row.get("customer") or row.get("customer_name")):
                continue
            customer_row = {
                "project": str(row.get("project") or ""),
                "customer_name": str(row.get("customer") or row.get("customer_name") or ""),
            }
            if row.get("downstream_customer"):
                customer_row["downstream_customer"] = str(row["downstream_customer"])
            fact_customers.append(customer_row)
        project_payload["project_customers"] = fact_customers
        project_payload["customer_basis"] = {
            row["project"]: ["weekly_fact_sources"]
            for row in fact_customers
        }
        if len(fact_customers) == 1 and report_project != "unknown":
            project_payload["customer_name"] = fact_customers[0]["customer_name"]
            if fact_customers[0].get("downstream_customer"):
                project_payload["downstream_customer"] = fact_customers[0]["downstream_customer"]
    documents = daily_documents if report_type == "daily" else weekly_documents
    standalone_work = daily_standalone_work if report_type == "daily" else weekly_standalone_work
    planned_documents = daily_tomorrow_plan["documents"] if report_type == "daily" else []
    planned_standalone_work = daily_tomorrow_plan["standalone_work"] if report_type == "daily" else []
    if documents or standalone_work or planned_documents or planned_standalone_work:
        project_payload["non_project_work"] = True
        project_payload["documents"] = [
            str(row.get("document_name") or "")
            for row in [*documents, *planned_documents]
            if row.get("document_name")
        ]
        project_payload["standalone_work"] = [
            str(row.get("work_name") or "")
            for row in [*standalone_work, *planned_standalone_work]
            if row.get("work_name")
        ]
        if not (daily_projects if report_type == "daily" else weekly_projects):
            report_project = ""
            project_payload["project"] = "unknown"
            project_payload["projects"] = []
            project_payload.setdefault("checked_sources", ["authorized_report_sessions", "structured_facts"])
            project_payload.setdefault(
                "limits",
                ["Non-project documents and standalone work do not require project or customer identity"],
            )
    package_dir.mkdir(parents=True, exist_ok=True)
    write_report(
        package_dir,
        report_type,
        date,
        week_key,
        config,
        items,
        patches,
        project_customers,
        weekly_projects,
        daily_work_items,
        daily_projects,
        weekly_documents,
        daily_documents,
        weekly_standalone_work,
        daily_standalone_work,
        daily_tomorrow_plan,
    )
    project_path = write_default_evidence(
        package_dir,
        materials_rel("evidence", "project_inference.json"),
        {
            "kind": "project_inference",
            "payload": project_payload,
        },
    )

    evidence = session_evidence_payload(
        consent,
        synthetic=is_synthetic,
        source_session_ids=[item.session_id for item in sessions],
        timezone=config.get("timezone", "Asia/Shanghai"),
    )
    write_json(package_dir / materials_rel("evidence", "codex_sessions.json"), {"kind": "codex_sessions", "payload": evidence})
    write_json(package_dir / materials_rel("evidence", "work_findings.json"), {"kind": "work_findings", "payload": work_findings_payload(sessions, patches)})
    search_path = ""
    if report_type == "daily":
        member_search_payload = search_usage_payload(config, date)
        if member_search_payload:
            search_path = materials_rel("evidence", "search_before_change.json")
            write_json(package_dir / search_path, {"kind": "search_before_change", "payload": member_search_payload})
    reports_dir = package_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    shutil.move(str(package_dir / f"{report_type}.md"), reports_dir / f"{report_type}.md")
    source = write_package_source_fn(package_dir, config, f"akbs-{report_type}-report")
    display_path = write_report_view(
        package_dir,
        report_type,
        date,
        week_key,
        config,
        items,
        patches,
        summary,
        project_customers,
        weekly_projects,
        daily_work_items,
        daily_projects,
        weekly_documents,
        daily_documents,
        weekly_standalone_work,
        daily_standalone_work,
        daily_tomorrow_plan,
    )
    fact_sources_path = daily_fact_evidence_path or weekly_fact_evidence_path
    fact_sources_payload = daily_facts.evidence if report_type == "daily" else weekly_facts.evidence
    render_binding_path = materials_rel("evidence", "report_render_binding.json")
    write_report_render_binding(
        package_dir,
        report_type=report_type,
        report_path=f"reports/{report_type}.md",
        report_view_path=display_path,
        fact_sources_path=fact_sources_path,
        facts_sha256=str(fact_sources_payload.get("facts_sha256") or ""),
        output_path=render_binding_path,
    )
    manifest = incoming_report_manifest(
        report_type,
        date,
        week_key,
        config,
        summary,
        source,
        run_id,
        incoming_schema_version,
        report_project,
        project_path,
        display_path,
        bool(documents or standalone_work or planned_documents or planned_standalone_work),
    )
    if report_type in {"daily", "weekly"} and replace_report_run_id:
        replacement = next((item for item in report_duplicates if item["run_id"] == replace_report_run_id), {})
        manifest["report_submission_intent"] = "revision"
        manifest["replacement_for_run_id"] = replace_report_run_id
        manifest["supersedes"] = {
            "report_type": report_type,
            "run_id": replace_report_run_id,
            "date": date.isoformat(),
            "week_range": week_key if report_type == "weekly" else "",
            "identity": report_identity(report_type, date, week_key),
            "package_key": replacement.get("package_key", ""),
        }
    elif report_type in {"daily", "weekly"}:
        manifest["report_submission_intent"] = "initial"
    if search_path:
        manifest["files"]["evidence"].append(search_path)
    if daily_projects:
        manifest["projects"] = sorted({str(row.get("project") or "") for row in daily_projects if row.get("project")})
    if daily_fact_evidence_path:
        manifest["files"]["evidence"].append(daily_fact_evidence_path)
    if weekly_fact_evidence_path:
        manifest["files"]["evidence"].append(weekly_fact_evidence_path)
    manifest["files"]["evidence"].append(render_binding_path)
    write_json(package_dir / "manifest.json", manifest)
    check = validate_package_fn(package_dir)
    write_json(package_dir / "local-check.json", check)
    return package_dir
