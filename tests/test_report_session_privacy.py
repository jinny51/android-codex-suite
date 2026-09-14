from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = REPO_ROOT / "plugins" / "akbs-member-ops"
PLUGIN_LIB = PLUGIN_ROOT / "lib"
INTAKE_SCRIPTS = PLUGIN_ROOT / "internal" / "incoming-v2" / "scripts"
for path in (PLUGIN_LIB, INTAKE_SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from akbs_intake import report_sessions, submit  # noqa: E402
from akbs_intake.io_utils import write_json  # noqa: E402
from akbs_intake.reports.builder import build_report_package  # noqa: E402
from akbs_intake.session_privacy import (  # noqa: E402
    SESSION_CONSENT_VERSION,
    SESSION_RETENTION_POLICY,
    configure_report_session_consent,
    require_report_session_consent,
    session_evidence_errors,
)


SESSION_ID = "11111111-2222-3333-4444-555555555555"
SECRET_VALUES = (
    "password-value-should-never-appear",
    "token-value-should-never-appear",
    "clipboard-value-should-never-appear",
    "cookie-value-should-never-appear",
    "shell-secret-should-never-appear",
    "env-secret-should-never-appear",
    "out-of-window-raw-message",
)


def base_config(root: Path) -> dict[str, str]:
    return {
        "member_alias": "member01",
        "member_name": "Synthetic Member",
        "codex_home": str(root / "synthetic-codex-home"),
        "out_dir": str(root / "artifacts"),
        "timezone": "Asia/Shanghai",
        "synthetic_data": "false",
        "include_patches": "false",
        "incoming_schema_version": "2",
    }


def consent(
    config: dict[str, str],
    date: dt.date,
    *fields: str,
) -> None:
    configure_report_session_consent(config, {date}, granted=True, fields=list(fields))


def session_path(codex_home: Path, date: dt.date, name: str = f"{SESSION_ID}.jsonl") -> Path:
    path = codex_home / "sessions" / f"{date:%Y}" / f"{date:%m}" / f"{date:%d}" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def write_synthetic_session_fixture(
    codex_home: Path,
    date: dt.date,
    cwd: Path,
    *,
    session_id: str = SESSION_ID,
    name: str | None = None,
) -> Path:
    rows: list[dict[str, object]] = [
        {
            "timestamp": f"{date.isoformat()}T09:00:00+08:00",
            "type": "session_meta",
            "payload": {
                "id": session_id,
                "cwd": str(cwd),
                "thread_name": "unrelated raw thread title",
            },
        },
        {
            "timestamp": f"{date.isoformat()}T09:01:00+08:00",
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": "TVE1086U 青鸾云；已完成 SystemUI 状态栏修复并通过验证，进度100%。",
                    }
                ],
            },
        },
        {
            "timestamp": f"{date.isoformat()}T09:02:00+08:00",
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            "password=password-value-should-never-appear "
                            "token=token-value-should-never-appear "
                            "剪贴板：clipboard-value-should-never-appear"
                        ),
                    }
                ],
            },
        },
        {
            "timestamp": f"{date.isoformat()}T09:03:00+08:00",
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            "完成 Framework 长输出过滤验证 "
                            + "A" * 1200
                            + " C:\\Users\\member\\private\\session.jsonl /home/member/private/session.jsonl"
                        ),
                    }
                ],
            },
        },
        {
            "timestamp": f"{date.isoformat()}T09:04:00+08:00",
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "exec_command",
                "arguments": json.dumps(
                    {
                        "cmd": (
                            "curl --token shell-secret-should-never-appear "
                            "-H 'Cookie: cookie-value-should-never-appear' "
                            "--data request-body /home/member/private/upload"
                        )
                    }
                ),
            },
        },
        {
            "timestamp": f"{date.isoformat()}T09:05:00+08:00",
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "exec_command",
                "arguments": json.dumps({"cmd": "AKBS_TOKEN=env-secret-should-never-appear git status"}),
            },
        },
        {
            "timestamp": f"{(date - dt.timedelta(days=1)).isoformat()}T09:06:00+08:00",
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "out-of-window-raw-message"}],
            },
        },
    ]
    path = session_path(codex_home, date, name or f"{session_id}.jsonl")
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")
    return path


def successful_command(_command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], 0, stdout="TVE1086U\n", stderr="")


def test_missing_consent_stops_before_session_read_packaging_and_http(tmp_path: Path) -> None:
    config = base_config(tmp_path)
    date = dt.date.today() - dt.timedelta(days=2)
    parse_spy = Mock(side_effect=AssertionError("session read must not start"))
    validate_spy = Mock(side_effect=AssertionError("package validation must not start"))
    source_spy = Mock(side_effect=AssertionError("package source writing must not start"))
    patch_spy = Mock(side_effect=AssertionError("patch discovery must not start"))

    with patch.object(report_sessions, "session_files", parse_spy):
        with pytest.raises(SystemExit, match="session-consent"):
            report_sessions.parse_sessions(config, {date}, successful_command)
    parse_spy.assert_not_called()

    with pytest.raises(SystemExit, match="session-consent"):
        build_report_package(
            "daily",
            date,
            config,
            run_id=f"{date:%Y%m%d}-090000-daily",
            incoming_schema_version="2",
            validate_package_fn=validate_spy,
            write_package_source_fn=source_spy,
            parse_sessions_fn=Mock(side_effect=AssertionError("session parser must not start")),
            discover_patches_fn=patch_spy,
        )
    assert not Path(config["out_dir"]).exists()
    validate_spy.assert_not_called()
    source_spy.assert_not_called()
    patch_spy.assert_not_called()

    package = tmp_path / "externally-created-package"
    package.mkdir()
    write_json(
        package / "manifest.json",
        {
            "package_kind": "daily_trace",
            "member_alias": "member01",
            "date": date.isoformat(),
            "run_id": f"{date:%Y%m%d}-090000-daily",
            "files": {"evidence": []},
        },
    )
    http_spy = Mock(side_effect=AssertionError("HTTP must not start"))
    with patch.object(submit, "server_submit_package", http_spy):
        with pytest.raises(SystemExit, match="HTTP 前停止"):
            submit.submit_package(
                package,
                config,
                validate_package_fn=lambda _path: {"status": "PASS", "errors": []},
                write_json_fn=lambda _path, _payload: None,
                patch_upload_gate_errors_fn=lambda _manifest: [],
            )
    http_spy.assert_not_called()


def test_consent_scope_must_exactly_match_time_window_and_fields(tmp_path: Path) -> None:
    config = base_config(tmp_path)
    date = dt.date.today() - dt.timedelta(days=2)
    consent(config, date, "work_summary")

    granted = require_report_session_consent(config, {date}, synthetic=False)
    assert granted.version == SESSION_CONSENT_VERSION
    assert granted.start_date == date
    assert granted.end_date == date
    assert granted.fields == {"work_summary"}

    with pytest.raises(SystemExit, match="时间窗口"):
        require_report_session_consent(config, {date + dt.timedelta(days=1)}, synthetic=False)

    consent(config, date, "patch_discovery")
    with pytest.raises(SystemExit, match="project_hint"):
        require_report_session_consent(config, {date}, synthetic=False)


def test_synthetic_fixture_is_windowed_filtered_bounded_and_field_scoped(tmp_path: Path) -> None:
    config = base_config(tmp_path)
    date = dt.date.today() - dt.timedelta(days=2)
    cwd = tmp_path / "TVE1086U"
    cwd.mkdir()
    write_synthetic_session_fixture(Path(config["codex_home"]), date, cwd)
    write_synthetic_session_fixture(
        Path(config["codex_home"]),
        date - dt.timedelta(days=1),
        cwd,
        session_id="personal-source-name",
        name="personal-source-name.jsonl",
    )
    consent(config, date, "work_summary", "project_hint", "command_summary")
    temporary_parent = tmp_path / "session-tmp"

    with patch.dict(os.environ, {"AKBS_REPORT_SESSION_TMPDIR": str(temporary_parent)}):
        sessions = report_sessions.parse_sessions(config, {date}, successful_command)

    assert len(sessions) == 1
    work = sessions[0]
    assert work.session_id == SESSION_ID
    assert work.thread_name == ""
    assert work.cwd == ""
    assert work.project == "TVE1086U"
    assert any("SystemUI 状态栏修复" in item for item in work.messages)
    assert any(item.startswith("curl") for item in work.commands)
    assert all(not item.startswith("执行命令:") for item in work.messages)
    assert max(map(len, work.messages)) <= 180
    assert max(map(len, work.commands)) <= 180
    rendered = json.dumps([item.__dict__ for item in sessions], ensure_ascii=False)
    for secret in SECRET_VALUES:
        assert secret not in rendered
    assert "unrelated raw thread title" not in rendered
    assert "C:\\Users\\member" not in rendered
    assert "/home/member/private" not in rendered
    assert temporary_parent.is_dir()
    assert list(temporary_parent.iterdir()) == []

    consent(config, date, "work_summary")
    work_only = report_sessions.parse_sessions(config, {date}, successful_command)
    assert work_only
    assert all(not message.startswith("执行命令:") for message in work_only[0].messages)
    assert work_only[0].commands == []
    assert work_only[0].cwd == ""


def test_non_uuid_source_identifier_is_minimized(tmp_path: Path) -> None:
    config = base_config(tmp_path)
    date = dt.date.today() - dt.timedelta(days=2)
    cwd = tmp_path / "TVE1086U"
    cwd.mkdir()
    write_synthetic_session_fixture(
        Path(config["codex_home"]),
        date,
        cwd,
        session_id="member-personal-source-name",
        name="human-readable-name.jsonl",
    )
    consent(config, date, "work_summary")

    sessions = report_sessions.parse_sessions(config, {date}, successful_command)

    assert len(sessions) == 1
    assert sessions[0].session_id.startswith("session_")
    assert "member-personal-source-name" not in sessions[0].session_id


def test_package_contains_only_minimal_session_provenance_and_sanitized_derivatives(tmp_path: Path) -> None:
    config = base_config(tmp_path)
    date = dt.date.today() - dt.timedelta(days=2)
    cwd = tmp_path / "TVE1086U"
    cwd.mkdir()
    write_synthetic_session_fixture(Path(config["codex_home"]), date, cwd)
    consent(config, date, "work_summary", "project_hint", "command_summary")

    def write_source(package_dir: Path, _config: dict[str, str], tool: str) -> dict[str, str]:
        payload = {"tool": tool, "source": "synthetic-test"}
        write_json(package_dir / "materials" / "evidence" / "source.json", payload)
        return payload

    package = build_report_package(
        "daily",
        date,
        config,
        run_id=f"{date:%Y%m%d}-091500-daily",
        incoming_schema_version="2",
        validate_package_fn=lambda _path: {"status": "PASS", "errors": []},
        write_package_source_fn=write_source,
        parse_sessions_fn=lambda cfg, dates: report_sessions.parse_sessions(cfg, dates, successful_command),
        discover_patches_fn=lambda _config, _sessions, _start, _end: [],
    )

    evidence = json.loads(
        (package / "materials" / "evidence" / "codex_sessions.json").read_text(encoding="utf-8")
    )["payload"]
    assert set(evidence) == {
        "source",
        "synthetic_data",
        "source_session_ids",
        "time_range",
        "consent",
        "retention",
    }
    assert evidence["source_session_ids"] == [SESSION_ID]
    assert evidence["time_range"]["start_date"] == date.isoformat()
    assert evidence["time_range"]["end_date"] == date.isoformat()
    assert evidence["consent"] == {
        "version": SESSION_CONSENT_VERSION,
        "granted": True,
        "scope": "single_report_generation",
        "fields": ["command_summary", "project_hint", "work_summary"],
    }
    assert evidence["retention"] == {
        "policy": SESSION_RETENTION_POLICY,
        "raw_session_copied": False,
        "temporary_artifacts_retained": False,
    }
    assert not ({"messages", "thread_name", "cwd", "commands", "raw"} & set(evidence))
    with_unrelated_raw = dict(evidence)
    with_unrelated_raw["notes"] = "unrelated raw session content"
    assert session_evidence_errors(with_unrelated_raw)

    package_text = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in package.rglob("*")
        if path.is_file()
    )
    for secret in SECRET_VALUES:
        assert secret not in package_text
    assert "unrelated raw thread title" not in package_text
    assert "C:\\Users\\member" not in package_text
    assert "/home/member/private" not in package_text


def test_temporary_extraction_is_removed_without_local_git_callback(tmp_path: Path) -> None:
    config = base_config(tmp_path)
    date = dt.date.today() - dt.timedelta(days=2)
    cwd = tmp_path / "TVE1086U"
    cwd.mkdir()
    write_synthetic_session_fixture(Path(config["codex_home"]), date, cwd)
    consent(config, date, "work_summary", "project_hint", "patch_discovery")
    temporary_parent = tmp_path / "session-tmp"

    def forbidden_command(_command: list[str]) -> subprocess.CompletedProcess[str]:
        raise AssertionError("session cwd Git callback must not run")

    with patch.dict(os.environ, {"AKBS_REPORT_SESSION_TMPDIR": str(temporary_parent)}):
        sessions = report_sessions.parse_sessions(config, {date}, forbidden_command)

    assert len(sessions) == 1
    assert temporary_parent.is_dir()
    assert list(temporary_parent.iterdir()) == []
