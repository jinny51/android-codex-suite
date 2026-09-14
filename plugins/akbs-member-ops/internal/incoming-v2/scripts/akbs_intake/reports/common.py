from __future__ import annotations

import datetime as dt
import shutil
import sys
from pathlib import Path
from typing import Any

try:
    from ..config import artifact_read_roots, expanded_path, local_now, require_safe_artifact_path
    from ..io_utils import read_optional_json_object as read_json_file
    from ..session_privacy import session_evidence_errors
except ImportError:  # pragma: no cover - direct script import fallback
    scripts_root = Path(__file__).resolve().parents[2]
    if str(scripts_root) not in sys.path:
        sys.path.insert(0, str(scripts_root))
    from akbs_intake.config import artifact_read_roots, expanded_path, local_now, require_safe_artifact_path
    from akbs_intake.io_utils import read_optional_json_object as read_json_file
    from akbs_intake.session_privacy import session_evidence_errors


def ymd(date: dt.date) -> str:
    return date.strftime("%Y%m%d")


def week_bounds(date: dt.date) -> tuple[dt.date, dt.date]:
    start = date - dt.timedelta(days=date.weekday())
    return start, start + dt.timedelta(days=6)


def report_dates(report_type: str, date: dt.date) -> tuple[set[dt.date], dt.date, dt.date, str]:
    if report_type in {"daily", "patch"}:
        return {date}, date, date, ymd(date)
    start, end = week_bounds(date)
    days = {start + dt.timedelta(days=offset) for offset in range((end - start).days + 1)}
    return days, start, end, f"{ymd(start)}-{ymd(end)}"


def package_key_from_manifest(manifest: dict[str, Any], package_dir: Path | None = None) -> str:
    date_key = str(manifest.get("date") or "").replace("-", "")
    member = str(manifest.get("member_alias") or "").strip()
    run_id = str(manifest.get("run_id") or "").strip()
    if date_key and member and run_id:
        return f"{date_key}/{member}/{run_id}"
    if package_dir is not None:
        return str(package_dir)
    return run_id or "unknown"


def iter_local_manifests(config: dict[str, str]) -> list[tuple[str, Path, dict[str, Any]]]:
    rows: list[tuple[str, Path, dict[str, Any]]] = []
    seen: set[Path] = set()
    for out_dir in artifact_read_roots(config):
        for bucket in ("pending", "submitted"):
            for manifest_path in sorted((out_dir / bucket).glob("*/*/*/manifest.json")):
                resolved = manifest_path.resolve()
                if resolved in seen:
                    continue
                seen.add(resolved)
                manifest = read_json_file(manifest_path)
                if isinstance(manifest, dict):
                    rows.append((bucket, manifest_path.parent, manifest))
    return rows


def report_identity(report_type: str, date: dt.date, week_range: str) -> str:
    return date.isoformat() if report_type == "daily" else week_range


def report_identity_from_manifest(manifest: dict[str, Any]) -> str:
    kind = str(manifest.get("package_kind") or "")
    if kind == "daily_trace":
        return str(manifest.get("date") or "")
    if kind == "weekly_trace":
        return str(manifest.get("week_range") or "")
    return ""


def report_type_from_manifest(manifest: dict[str, Any]) -> str:
    kind = str(manifest.get("package_kind") or "")
    if kind == "daily_trace":
        return "daily"
    if kind == "weekly_trace":
        return "weekly"
    return ""


def replacement_run_id(manifest: dict[str, Any]) -> str:
    replacement = str(manifest.get("replacement_for_run_id") or "").strip()
    if replacement:
        return replacement
    supersedes = manifest.get("supersedes")
    if isinstance(supersedes, dict):
        return str(supersedes.get("run_id") or "").strip()
    return ""


def report_duplicate_label(report_type: str) -> str:
    return "日报日期" if report_type == "daily" else "周报周期"


def report_replace_option(report_type: str) -> str:
    return "--replace-daily-run-id" if report_type == "daily" else "--replace-weekly-run-id"


def local_report_packages(config: dict[str, str], report_type: str, identity: str, exclude_run_id: str = "") -> list[dict[str, str]]:
    member_alias = config.get("member_alias", "")
    packages: dict[str, dict[str, str]] = {}
    for bucket, package_dir, manifest in iter_local_manifests(config):
        if report_type_from_manifest(manifest) != report_type:
            continue
        if manifest.get("member_alias") != member_alias:
            continue
        manifest_identity = report_identity_from_manifest(manifest)
        if manifest_identity != identity:
            continue
        run_id = str(manifest.get("run_id") or package_dir.name)
        if exclude_run_id and run_id == exclude_run_id:
            continue
        package_key = package_key_from_manifest(manifest, package_dir)
        packages.setdefault(
            package_key,
            {
                "bucket": bucket,
                "run_id": run_id,
                "date": str(manifest.get("date") or ""),
                "report_type": report_type,
                "identity": identity,
                "week_range": str(manifest.get("week_range") or ""),
                "package_key": package_key,
                "path": str(package_dir),
                "replacement_for_run_id": replacement_run_id(manifest),
            },
        )
    return sorted(packages.values(), key=lambda item: (item["bucket"], item["package_key"]))


def effective_report_packages(packages: list[dict[str, str]]) -> list[dict[str, str]]:
    superseded_run_ids = {
        str(item.get("replacement_for_run_id") or "").strip()
        for item in packages
        if str(item.get("replacement_for_run_id") or "").strip()
    }
    return [item for item in packages if item["run_id"] not in superseded_run_ids]


def format_report_duplicate_message(report_type: str, identity: str, duplicates: list[dict[str, str]]) -> str:
    refs = ", ".join(f"{item['bucket']}:{item['package_key']}" for item in duplicates)
    effective = effective_report_packages(duplicates)
    latest = (effective or duplicates)[-1] if duplicates else {"run_id": "<run_id>"}
    option = report_replace_option(report_type)
    label = report_duplicate_label(report_type)
    noun = "日报包" if report_type == "daily" else "周报包"
    return (
        f"同一成员同一{label}已存在{noun}: {label}={identity}; existing={refs}. "
        f"已停止生成或上传第二个普通{noun}。请选择："
        f"如需修订当前有效报告，请显式使用 {report_type} {option} {latest['run_id']}；"
        "如不修订，请取消本次提交。新包会作为修订版写入 supersedes/replacement 元数据，旧版保留在历史中。"
    )


def ensure_report_not_duplicate(
    config: dict[str, str],
    report_type: str,
    identity: str,
    current_run_id: str,
    replacement_run_id_value: str = "",
) -> list[dict[str, str]]:
    duplicates = local_report_packages(config, report_type, identity, exclude_run_id=current_run_id)
    if not duplicates:
        if replacement_run_id_value:
            raise SystemExit(f"{report_replace_option(report_type)} 未找到同{report_duplicate_label(report_type)}已有包: {replacement_run_id_value}")
        return []
    if not replacement_run_id_value:
        raise SystemExit(format_report_duplicate_message(report_type, identity, duplicates))
    if replacement_run_id_value == current_run_id:
        raise SystemExit(f"{report_replace_option(report_type)} 不能指向当前新{report_duplicate_label(report_type)} run_id")
    if not any(item["run_id"] == replacement_run_id_value for item in duplicates):
        raise SystemExit(f"{report_replace_option(report_type)} 未匹配同{report_duplicate_label(report_type)}已有包: {replacement_run_id_value}")
    effective = effective_report_packages(duplicates)
    if effective and not any(item["run_id"] == replacement_run_id_value for item in effective):
        current_ids = ", ".join(item["run_id"] for item in effective)
        raise SystemExit(
            f"{report_replace_option(report_type)} 必须指向当前有效版本，不能修订已被替换的历史版本: "
            f"requested={replacement_run_id_value}; current={current_ids}"
        )
    return duplicates


def ensure_report_date_allowed(report_type: str, date: dt.date, config: dict[str, str]) -> None:
    today = local_now(config).date()
    if date > today:
        if report_type == "daily":
            raise SystemExit("不能提交未来日期的日报，请重新生成正确日期的日报。")
        raise SystemExit("不能提交未来周期的周报，请重新生成正确周期的周报。")
    if report_type == "weekly":
        _, week_start, _, _ = report_dates("weekly", date)
        _, current_week_start, _, _ = report_dates("weekly", today)
        if week_start > current_week_start:
            raise SystemExit("不能提交未来周期的周报，请重新生成正确周期的周报。")


def ensure_report_submit_allowed(package_dir: Path, config: dict[str, str], manifest: dict[str, Any]) -> None:
    report_type = report_type_from_manifest(manifest)
    if report_type not in {"daily", "weekly"}:
        return
    evidence_paths = manifest.get("files", {}).get("evidence", []) if isinstance(manifest.get("files"), dict) else []
    session_payload: Any = None
    for relative in evidence_paths if isinstance(evidence_paths, list) else []:
        evidence = read_json_file(package_dir / str(relative))
        if isinstance(evidence, dict) and evidence.get("kind") == "codex_sessions":
            session_payload = evidence.get("payload")
            break
    privacy_errors = session_evidence_errors(session_payload)
    if privacy_errors:
        raise SystemExit("报告包缺少有效 session consent/privacy evidence，已在 HTTP 前停止: " + privacy_errors[0])
    manifest_date = str(manifest.get("date") or "").strip()
    try:
        date = dt.date.fromisoformat(manifest_date)
    except ValueError:
        return
    ensure_report_date_allowed(report_type, date, config)
    run_id = str(manifest.get("run_id") or package_dir.name)
    identity = report_identity_from_manifest(manifest)
    if not identity:
        return
    replacement = replacement_run_id(manifest)
    intent = str(manifest.get("report_submission_intent") or "").strip()
    if intent not in {"", "initial", "revision"}:
        raise SystemExit("report_submission_intent 必须是 initial 或 revision，已在 HTTP 前停止")
    if replacement and intent == "initial":
        raise SystemExit("已有报告的再次提交必须是 revision，不能声明为 initial，已在 HTTP 前停止")
    if not replacement and intent == "revision":
        raise SystemExit("revision 必须明确指向当前有效报告，已在 HTTP 前停止")
    ensure_report_not_duplicate(config, report_type, identity, run_id, replacement)


def record_submitted_package(package_dir: Path, config: dict[str, str], manifest: dict[str, Any]) -> None:
    out_dir = require_safe_artifact_path(expanded_path(config["out_dir"]), purpose="out_dir")
    date_key = str(manifest.get("date") or "").replace("-", "")
    member = str(manifest.get("member_alias") or config.get("member_alias") or "").strip()
    run_id = str(manifest.get("run_id") or package_dir.name).strip()
    if not date_key or not member or not run_id:
        return
    target = require_safe_artifact_path(out_dir / "submitted" / date_key / member / run_id, purpose="submitted package archive")
    if target.resolve() == package_dir.resolve() or target.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(package_dir, target)
