"""Release-only v2 producer -> member HTTP -> server storage/readback contract.

Invoked by validate_incoming_contract_gate.py --mode v2-release. All business
data is synthetic and confined to run-tests.sh's temporary database directory.
The only transport substitution routes urllib to the actual FastAPI TestClient.
"""
from __future__ import annotations

import io
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request


def run_remote(suite_root: Path, *, host: str, system_root: str) -> dict:
    def git(*args):
        return subprocess.check_output(["git", "-C", str(suite_root), *args], text=True).strip()

    source_commit = git("rev-parse", "HEAD")
    source_status = git("status", "--porcelain")
    archive_bytes = io.BytesIO()
    with tarfile.open(fileobj=archive_bytes, mode="w:gz") as archive:
        for relative in ("contracts", "plugins/akbs-member-ops", "plugins/android-engineering-ops", "scripts"):
            archive.add(suite_root / relative, arcname=f"suite/{relative}")
    script = (
        "set -euo pipefail; "
        f"export AKBS_SYSTEM={shlex.quote(system_root)}; "
        f"source {shlex.quote(system_root + '/scripts/lib/controlled-validation-output.sh')}; "
        "akbs_validation_output_init; "
        'gate_root="$AKBS_VALIDATION_OUTPUT_ROOT/v2-contract"; mkdir -p "$gate_root"; '
        'tar -xzf - -C "$gate_root"; '
        'export AKBS_CONTRACT_SUITE_ROOT="$gate_root/suite"; '
        'export AKBS_V2_GATE_RECEIPT="$gate_root/receipt.json"; '
        f"cd {shlex.quote(system_root)}; "
        'bash scripts/run-tests.sh "$gate_root/suite/scripts/android_change_v2_contract_gate.py" -q; '
        'cat "$AKBS_V2_GATE_RECEIPT"'
    )
    result = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", host, "bash", "-c", shlex.quote(script)],
        input=archive_bytes.getvalue(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    output = result.stdout.decode("utf-8", errors="replace")
    if result.returncode:
        raise AssertionError(f"v2 release gate failed ({result.returncode})\n{output}\n{result.stderr.decode('utf-8', errors='replace')}")
    receipt = json.loads(output.strip().splitlines()[-1])
    if receipt.get("status") != "PASS" or receipt.get("execution") != "formal-v2-isolated-http":
        raise AssertionError(f"v2 release gate returned invalid receipt: {receipt}")
    if git("rev-parse", "HEAD") != source_commit or git("status", "--porcelain") != source_status:
        raise AssertionError("plugin checkout changed during paired validation")
    receipt.update(plugin_commit=source_commit, plugin_clean=not source_status, plugin_payload_sha256=hashlib.sha256(archive_bytes.getvalue()).hexdigest())
    return receipt


def test_formal_v2_pipeline(tmp_path, monkeypatch, request):
    # Never infer a checkout or silently skip when the paired suite is missing.
    suite_root = Path(os.environ["AKBS_CONTRACT_SUITE_ROOT"]).resolve()
    sys.path.insert(0, str(suite_root / "scripts"))
    from android_change_v2_contract_samples import generate_packages

    # Member artifacts must not be placed in the administrator AKBS outputs
    # namespace; exercise the normal member path policy without bypassing it.
    members = tempfile.TemporaryDirectory(prefix="akbs-v2-contract-members-", dir="/tmp")
    request.addfinalizer(members.cleanup)
    monkeypatch.setenv("TMPDIR", members.name)
    monkeypatch.setattr(tempfile, "tempdir", members.name)
    packages, env = generate_packages(Path(members.name), suite_root)
    for key in ("CODEX_HOME", "PATH", "CODEX_REPORT_SKIP_PLUGIN_UPDATE_CHECK"):
        if key in env:
            monkeypatch.setenv(key, env[key])
    monkeypatch.setenv("CODEX_REPORT_AKBS_ENDPOINT_SUBMISSION_API_BASE_URL", "http://testserver/akbs/api")
    for path in (
        suite_root / "plugins/akbs-member-ops/lib",
        suite_root / "plugins/akbs-member-ops/internal/incoming-v1/scripts",
    ):
        sys.path.insert(0, str(path))
    from akbs_member_ops.incoming_v2.submission import submit_package
    from akbs_active.app import create_app
    from akbs_active.db import connect
    from akbs_active.normalized_patch_components import normalized_component_read
    from akbs_active.android_change_v2_qualified_contracts import QUALIFIED_CONTRACT_SET_ROOTS_BY_PIN
    from tests.auth_helpers import login_member
    from tests.test_android_change_v2_production_writer import setup_writer
    from fastapi.testclient import TestClient

    runtime = tmp_path / "server"
    runtime.mkdir()
    database, data, generation = setup_writer(
        runtime, monkeypatch, contract_pins=tuple(QUALIFIED_CONTRACT_SET_ROOTS_BY_PIN),
    )
    client = TestClient(create_app(database, data_root=data))
    login_member(client, "wick")

    class Response(io.BytesIO):
        def __init__(self, response):
            super().__init__(response.content)
            self.headers = response.headers

    def urlopen(request, timeout=0):
        del timeout
        parsed = urllib.parse.urlsplit(request.full_url)
        assert parsed.netloc == "testserver", "contract test attempted an external request"
        response = client.request(request.get_method(), parsed.path, content=request.data, headers=dict(request.header_items()))
        if response.status_code >= 400:
            raise urllib.error.HTTPError(request.full_url, response.status_code, response.reason_phrase, response.headers, io.BytesIO(response.content))
        return Response(response)

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    verified = []
    for name, package in sorted(packages.items()):
        before = {p.relative_to(package).as_posix(): p.read_bytes() for p in package.rglob("*") if p.is_file()}
        first = submit_package(package, profile="wick")
        second = submit_package(package, profile="wick")
        assert first["status"] == "PASS" and first["server_qualified"] is True
        assert first["receipt"] == second["receipt"]
        stored = data / "uploads" / first["source_package_key"]
        assert {p.relative_to(stored).as_posix(): p.read_bytes() for p in stored.rglob("*") if p.is_file()} == before
        assert {p.relative_to(package).as_posix(): p.read_bytes() for p in package.rglob("*") if p.is_file()} == before
        manifest = json.loads(before["manifest.json"])
        with connect(database, readonly=True) as conn:
            readback = normalized_component_read(conn, first["patch_package_id"])
            assert {c["component_id"] for c in readback["components"]} == {c["id"] for c in manifest["components"]}
            by_id = {c["component_id"]: c for c in readback["components"]}
            for component in manifest["components"]:
                for facet in ("layer", "type", "partition", "ownership"):
                    assert by_id[component["id"]][facet] == component[facet]
                assert sorted(by_id[component["id"]].get("qualifiers", [])) == sorted(component.get("qualifiers", []))
            assert conn.execute("pragma foreign_key_check").fetchall() == []
        detail = client.get(f"/akbs/api/member/me/packages/{first['source_package_key']}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["patch_package_id"] == first["patch_package_id"]
        verified.append({"case": name, "components": [c["layer"] for c in manifest["components"]]})
    with connect(database, readonly=True) as conn:
        for table in ("packages", "intake_queue", "android_change_v2_write_operations", "android_change_v2_write_operation_seals"):
            assert conn.execute(f"select count(*) from {table}").fetchone()[0] == len(packages)
        assert conn.execute("pragma quick_check").fetchone()[0] == "ok"
    assert set(layer for case in verified for layer in case["components"]) == {"application", "platform", "native", "hal", "kernel", "device", "build"}
    assert any(len(case["components"]) > 1 for case in verified)
    receipt = {"status": "PASS", "execution": "formal-v2-isolated-http", "cases": verified, "accepted": len(packages), "exact_replays": len(packages), "stored_bytes_preserved": True, "member_readback": True, "system_runtime_generation": generation}
    Path(os.environ["AKBS_V2_GATE_RECEIPT"]).write_text(json.dumps(receipt, ensure_ascii=False, sort_keys=True) + "\n")
