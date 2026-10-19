from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
MEMBER = ROOT / "plugins/akbs-member-ops"
for path in (MEMBER / "lib", MEMBER / "internal/incoming-v2/scripts"):
    sys.path.insert(0, str(path))

from akbs_intake import submit  # noqa: E402


def package_with_workflow(tmp_path: Path, workflow: str) -> tuple[Path, bytes]:
    package = tmp_path / "package"
    package.mkdir()
    manifest = json.loads((ROOT / "contracts/incoming/v2/fixtures/patch.manifest.json").read_text())
    manifest["workflow_contract"] = workflow
    path = package / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return package, path.read_bytes()


@pytest.mark.parametrize("workflow", ["manual_import", "historical_import"])
def test_member_upload_cannot_use_import_labels_or_a_local_admin_role(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, workflow: str,
) -> None:
    package, before = package_with_workflow(tmp_path, workflow)
    config = {"member_alias": "test-member", "role": "admin"}

    def forbidden(*args, **kwargs):
        pytest.fail("member import rejection must precede validation writes, archive creation and HTTP")

    monkeypatch.setattr(submit, "package_tar_gz_bytes", forbidden)
    monkeypatch.setattr(submit, "request_json_with_metadata", forbidden)
    for action in (
        lambda: submit.submit_package(package, config, validate_package_fn=forbidden,
                                      write_json_fn=forbidden, patch_upload_gate_errors_fn=forbidden),
        lambda: submit.server_submit_package(package, config),
        lambda: submit.http_submit_package(package, config, "test-member", b"unchanged"),
    ):
        with pytest.raises(SystemExit, match="管理员使用现有 submit-local-patch"):
            action()
    assert (package / "manifest.json").read_bytes() == before
    assert not (package / "local-check.json").exists()


def test_member_current_workflow_keeps_the_existing_submission_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    package, before = package_with_workflow(tmp_path, "current_codex_skill")
    config = {"member_alias": "test-member", "role": "member"}
    sent = []

    def http(package_dir, _config, member, payload):
        sent.append((package_dir, member, bool(payload)))
        return {"submitted": True}

    monkeypatch.setattr(submit, "http_submit_package", http)
    result = submit.submit_package(
        package, config, validate_package_fn=lambda _: {"status": "PASS"},
        write_json_fn=lambda *args: None, patch_upload_gate_errors_fn=lambda _: [],
    )
    assert result == {"submitted": True}
    assert sent == [(package, "test-member", True)]
    assert (package / "manifest.json").read_bytes() == before
