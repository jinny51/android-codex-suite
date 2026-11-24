#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


PLUGIN_LIB = Path(__file__).resolve().parents[3] / "lib"
if str(PLUGIN_LIB) not in sys.path:
    sys.path.insert(0, str(PLUGIN_LIB))

from android_engineering_ops.json_io import write_json
from android_engineering_ops.artifact_paths import require_safe_artifact_path
from android_engineering_ops.install_family import require_target_install_family
from android_engineering_ops.remote_artifact_manifest import (
    RemoteArtifactManifest,
    RemoteArtifactManifestError,
    validate_remote_artifact_manifest,
    verify_mounted_artifact,
)
from android_engineering_ops.verification_evidence import build_delivery_contract_fields


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Push remote Android build artifacts with local adb.")
    parser.add_argument("--artifact", action="append", type=Path, default=[])
    parser.add_argument("--artifact-manifest", action="append", type=Path, default=[])
    parser.add_argument("--artifact-bridge-root", type=Path)
    parser.add_argument("--expected-module", action="append", default=[])
    parser.add_argument("--expected-workspace-id", default="")
    parser.add_argument("--expected-command-id", default="")
    parser.add_argument("--max-build-age-seconds", type=int, default=86400)
    parser.add_argument("--compat-unverified", action="store_true")
    parser.add_argument("--dest", action="append", default=[])
    parser.add_argument("--product-out", type=Path)
    parser.add_argument("--destinations-file", type=Path)
    parser.add_argument("--learn-destinations", action="store_true")
    parser.add_argument("--adb-serial", default="")
    parser.add_argument("--evidence-out", type=Path)
    parser.add_argument("--remote-build-host", default="")
    parser.add_argument("--remote-source-root", default="")
    parser.add_argument("--remote-build-command", default="")
    parser.add_argument("--remote-build-profile", default="")
    parser.add_argument("--remote-artifact", action="append", default=[])
    parser.add_argument("--artifact-sha1", action="append", default=[])
    parser.add_argument("--artifact-transfer", default="")
    parser.add_argument("--reboot", action="store_true")
    parser.add_argument("--wait-boot", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def read_destination_memory(path: Path | None) -> dict[str, str]:
    if path is None or not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {
        str(key): str(value)
        for key, value in payload.items()
        if str(key).strip() and str(value).strip()
    }


def artifact_relative_path(artifact: Path, product_out: Path | None) -> str:
    if product_out is None:
        return ""
    try:
        return artifact.resolve().relative_to(product_out.resolve()).as_posix()
    except (OSError, ValueError):
        return ""


def infer_destination(artifact: Path, product_out: Path | None, memory: dict[str, str]) -> str:
    relative = artifact_relative_path(artifact, product_out)
    if not relative:
        return ""
    if relative in memory:
        return memory[relative]
    partition = relative.split("/", 1)[0]
    if partition in {"system", "system_ext", "product", "vendor", "odm"}:
        return "/" + relative
    return ""


def verified_manifest_artifacts(
    args: argparse.Namespace,
) -> tuple[list[Path], list[RemoteArtifactManifest]]:
    if not args.artifact_manifest:
        if not args.compat_unverified:
            raise SystemExit(
                "verified delivery requires --artifact-manifest; "
                "legacy fixture-only use must opt in with --compat-unverified"
            )
        if not args.dry_run:
            raise SystemExit("--compat-unverified is dry-run only and cannot invoke adb")
        return list(args.artifact), []
    if args.compat_unverified:
        raise SystemExit("--compat-unverified cannot be combined with artifact manifests")
    required = {
        "--artifact-bridge-root": args.artifact_bridge_root,
        "--remote-source-root": args.remote_source_root,
        "--remote-build-profile": args.remote_build_profile,
        "--expected-workspace-id": args.expected_workspace_id,
        "--expected-command-id": args.expected_command_id,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise SystemExit("manifest verification is missing trusted context: " + ", ".join(missing))
    if len(args.expected_module) != len(args.artifact_manifest):
        raise SystemExit("provide one --expected-module for each --artifact-manifest")
    if args.artifact and len(args.artifact) != len(args.artifact_manifest):
        raise SystemExit("--artifact count must match --artifact-manifest count when both are supplied")
    if args.max_build_age_seconds <= 0:
        raise SystemExit("--max-build-age-seconds must be positive")

    artifacts: list[Path] = []
    manifests: list[RemoteArtifactManifest] = []
    now_ns = time.time_ns()
    for index, manifest_path in enumerate(args.artifact_manifest):
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            verified_path = verify_mounted_artifact(
                payload,
                mounted_root=args.artifact_bridge_root,
                remote_root=args.remote_source_root,
                expected_module=args.expected_module[index],
                expected_profile=args.remote_build_profile,
                expected_workspace_id=args.expected_workspace_id,
                expected_command_id=args.expected_command_id,
                now_ns=now_ns,
                max_build_age_ns=args.max_build_age_seconds * 1_000_000_000,
            )
            # verify_mounted_artifact already parsed the same closed payload; keep
            # the trusted fields for evidence without accepting caller file facts.
            manifest = validate_remote_artifact_manifest(
                payload,
                expected_module=args.expected_module[index],
                expected_profile=args.remote_build_profile,
                expected_workspace_id=args.expected_workspace_id,
                expected_command_id=args.expected_command_id,
                expected_remote_root=args.remote_source_root,
                now_ns=now_ns,
                max_build_age_ns=args.max_build_age_seconds * 1_000_000_000,
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError, RemoteArtifactManifestError) as exc:
            raise SystemExit(f"artifact manifest verification failed: {manifest_path}: {exc}") from exc
        if args.artifact and args.artifact[index].expanduser().resolve() != verified_path:
            raise SystemExit("caller artifact path does not match the manifest-derived mounted path")
        artifacts.append(verified_path)
        manifests.append(manifest)
    return artifacts, manifests


def stage_verified_artifacts(
    artifacts: list[Path],
    manifests: list[RemoteArtifactManifest],
) -> tuple[tempfile.TemporaryDirectory[str], list[Path]]:
    """Snapshot verified SMB/CIFS files into a private local directory for adb.

    This closes the verification-to-use race: a later remote build may replace
    the mounted path, but adb receives the private copy whose hash was checked
    while it was copied.
    """

    if len(artifacts) != len(manifests):
        raise SystemExit("verified artifact and manifest counts do not match")
    temporary = tempfile.TemporaryDirectory(prefix="codex-verified-artifacts-")
    root = Path(temporary.name)
    os.chmod(root, 0o700)
    staged: list[Path] = []
    try:
        for index, (source, manifest) in enumerate(zip(artifacts, manifests)):
            target = root / f"{index:03d}-{manifest.sha256[:16]}-{source.name}"
            digest = hashlib.sha256()
            size = 0
            with source.open("rb") as reader, target.open("xb") as writer:
                before = os.fstat(reader.fileno())
                while chunk := reader.read(1024 * 1024):
                    writer.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
                writer.flush()
                os.fsync(writer.fileno())
                after = os.fstat(reader.fileno())
            identity_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
            if tuple(getattr(before, field) for field in identity_fields) != tuple(
                getattr(after, field) for field in identity_fields
            ):
                raise SystemExit(f"verified mounted artifact changed while staging for adb: {source}")
            if size != manifest.size or digest.hexdigest() != manifest.sha256:
                raise SystemExit(f"verified mounted artifact changed before adb staging: {source}")
            os.chmod(target, 0o600)
            staged.append(target)
    except BaseException:
        temporary.cleanup()
        raise
    return temporary, staged


def adb_command() -> list[str]:
    value = os.environ.get("ADB", "adb").strip()
    command = [value.strip('"') or "adb"] if os.name == "nt" else (shlex.split(value) if value else ["adb"])
    executable = command[0]
    if not (Path(executable).is_file() or shutil.which(executable)):
        raise SystemExit("adb not found; set ADB to the local adb executable")
    return command


def path_for_adb(path: Path, command: list[str]) -> str:
    if os.name != "nt" and command[0].lower().endswith(".exe") and shutil.which("wslpath"):
        converted = subprocess.run(
            ["wslpath", "-w", str(path)],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout.strip()
        return converted
    return str(path)


def run_adb(command: list[str], serial: str, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    full = [*command]
    if serial:
        full.extend(["-s", serial])
    full.extend(args)
    return subprocess.run(full, check=check, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def default_evidence_path(args: argparse.Namespace) -> Path | None:
    if args.evidence_out:
        return args.evidence_out
    configured = os.environ.get("CODEX_BUILD_DELIVERY_EVIDENCE", "").strip()
    if configured:
        return Path(configured).expanduser()
    return None


def delivery_evidence(
    args: argparse.Namespace,
    pairs: list[tuple[Path, str]],
    manifests: list[RemoteArtifactManifest],
) -> dict[str, Any]:
    adb_prefix = f"adb -s {args.adb_serial}" if args.adb_serial else "adb"
    remote_artifacts = [manifest.to_dict() for manifest in manifests]
    adb_actions = [f"{adb_prefix} push {artifact} {dest}" for artifact, dest in pairs]
    device_restarts: list[str] = []
    if args.reboot:
        device_restarts.append(f"{adb_prefix} reboot")
    if args.wait_boot:
        device_restarts.append(f"{adb_prefix} wait-for-device")
    return {
        **build_delivery_contract_fields(),
        "kind": "verification_result",
        "result": "INFO" if args.dry_run or not manifests else "PASS",
        "method": "device",
        "summary": (
            "dry-run remote build artifact delivery evidence"
            if args.dry_run
            else "remote build artifact delivered to local adb device"
        ),
        "build": [],
        "device": args.adb_serial,
        "steps": [*adb_actions, *device_restarts],
        "remote_build": {
            "host": args.remote_build_host,
            "source_root": args.remote_source_root,
            "command": args.remote_build_command,
            "profile": args.remote_build_profile,
            "artifacts": remote_artifacts,
            "manifest_verified": bool(manifests),
        },
        "local_delivery": {
            "transfer": args.artifact_transfer,
            "local_artifacts": [str(artifact) for artifact, _ in pairs],
            "adb_serial": args.adb_serial,
            "adb_actions": adb_actions,
            "device_restarts": device_restarts,
            "verified_private_staging": bool(manifests) and not args.dry_run,
        },
    }


def main() -> int:
    args = parse_args()
    require_target_install_family(Path(__file__).resolve().parents[3])
    if args.remote_artifact or args.artifact_sha1:
        raise SystemExit(
            "caller-supplied --remote-artifact/--artifact-sha1 facts are retired; "
            "use a remote-generated --artifact-manifest"
        )
    if args.learn_destinations and args.destinations_file is None:
        raise SystemExit("--learn-destinations requires --destinations-file")
    if args.learn_destinations and not args.dry_run and args.destinations_file is not None:
        args.destinations_file = require_safe_artifact_path(
            args.destinations_file,
            purpose="artifact destination memory output",
        )
    if args.evidence_out is not None:
        args.evidence_out = require_safe_artifact_path(args.evidence_out, purpose="build delivery evidence output")
    evidence_path = default_evidence_path(args)
    if evidence_path is not None:
        evidence_path = require_safe_artifact_path(evidence_path, purpose="build delivery evidence output")
    memory = read_destination_memory(args.destinations_file)
    artifacts, manifests = verified_manifest_artifacts(args)
    if not artifacts:
        raise SystemExit("at least one verified artifact is required")
    pairs: list[tuple[Path, str]] = []
    for index, artifact in enumerate(artifacts):
        if not artifact.is_file():
            raise SystemExit(f"artifact not found: {artifact}")
        destination = args.dest[index] if index < len(args.dest) else ""
        destination = destination or infer_destination(artifact, args.product_out, memory)
        if not destination:
            raise SystemExit(f"cannot infer destination for {artifact}; provide --dest")
        pairs.append((artifact.resolve(), destination))

    command = ["adb"] if args.dry_run else adb_command()
    staging: tempfile.TemporaryDirectory[str] | None = None
    delivery_pairs = pairs
    try:
        if manifests and not args.dry_run:
            staging, staged = stage_verified_artifacts(artifacts, manifests)
            delivery_pairs = [(staged[index], destination) for index, (_, destination) in enumerate(pairs)]
        if args.dry_run:
            for artifact, destination in pairs:
                print(f"ADB push {artifact} {destination}")
        else:
            run_adb(command, args.adb_serial, "wait-for-device")
            run_adb(command, args.adb_serial, "root", check=False)
            run_adb(command, args.adb_serial, "remount")
            for index, (delivery_artifact, destination) in enumerate(delivery_pairs):
                source_artifact = pairs[index][0]
                print(f"PUSH {source_artifact} -> {destination}")
                run_adb(
                    command,
                    args.adb_serial,
                    "push",
                    path_for_adb(delivery_artifact, command),
                    destination,
                )
                relative = artifact_relative_path(source_artifact, args.product_out)
                if args.learn_destinations and relative:
                    memory[relative] = destination
            run_adb(command, args.adb_serial, "shell", "sync", check=False)
            if args.reboot:
                run_adb(command, args.adb_serial, "reboot")
            if args.wait_boot:
                run_adb(command, args.adb_serial, "wait-for-device")
                deadline = time.monotonic() + 300
                while time.monotonic() < deadline:
                    result = run_adb(command, args.adb_serial, "shell", "getprop", "sys.boot_completed", check=False)
                    if result.stdout.strip().replace("\r", "") == "1":
                        print("BOOT_OK")
                        break
                    time.sleep(1)
                else:
                    raise SystemExit("device did not finish booting within 300 seconds")
    finally:
        if staging is not None:
            staging.cleanup()

    if args.learn_destinations and not args.dry_run:
        write_json(args.destinations_file, memory)
        print(f"DESTINATION_MEMORY file={args.destinations_file}")

    if evidence_path:
        write_json(evidence_path, delivery_evidence(args, pairs, manifests))
        print(f"EVIDENCE {evidence_path}")
    print(f"PUSH_OK count={len(pairs)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
