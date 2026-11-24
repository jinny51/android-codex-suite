#!/usr/bin/env python3
"""Formal remote-v2 entry for Android discovery, profiles, builds, and checkpoints."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import subprocess
import sys
import tempfile
import time
from typing import Iterable


SCRIPT_DIR = Path(__file__).resolve().parent
SKILLS_DIR = SCRIPT_DIR.parents[1]
PLUGIN_ROOT = SCRIPT_DIR.parents[2]
PLUGIN_LIB = PLUGIN_ROOT / "lib"
if str(PLUGIN_LIB) not in sys.path:
    sys.path.insert(0, str(PLUGIN_LIB))

from android_engineering_ops.local_host import run_channel  # noqa: E402


from android_engineering_ops.remote_artifact_manifest import (  # noqa: E402
    RemoteArtifactManifestError,
    validate_remote_artifact_manifest,
)
from android_engineering_ops.artifact_paths import require_safe_artifact_path  # noqa: E402
from android_engineering_ops.install_family import require_target_install_family  # noqa: E402


SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,127}")
WORKSPACE_RE = re.compile(r"WORKSPACE_OK id=([0-9a-f]{16})\b")
CANONICAL_RE = re.compile(r"^REMOTE_ROOT_OK .* canonical=(.*)$", re.M)
MANIFEST_LINE_RE = re.compile(
    r"^REMOTE_ARTIFACT_MANIFEST_B64 index=(\d+) module=([A-Za-z0-9][A-Za-z0-9._+-]{0,127}) "
    r"destination=(\S+) payload=([A-Za-z0-9+/=]+)$"
)


class RemoteV2Error(RuntimeError):
    def __init__(self, message: str, *, returncode: int = 1):
        super().__init__(message)
        self.returncode = returncode


def safe_id(value: str, field: str) -> str:
    if not SAFE_ID.fullmatch(value):
        raise SystemExit(f"{field} must match {SAFE_ID.pattern}")
    return value


def safe_relative(value: str, field: str) -> str:
    path = PurePosixPath(value or ".")
    if path.is_absolute() or ".." in path.parts or any(ord(ch) < 32 for ch in value):
        raise SystemExit(f"{field} must be a safe project-relative POSIX path")
    return path.as_posix()


def default_channel() -> Path:
    return SKILLS_DIR / "android-remote-channel" / "scripts" / "remote-channel.sh"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Android build-side work only through android-remote-channel v2."
    )
    parser.add_argument("--ssh-host", required=True)
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--working-subpath", default=".")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--channel", type=Path, default=default_channel())
    parser.add_argument("--preserve-legacy", action="store_true")
    parser.add_argument("--wait-timeout", type=int, default=86400)
    parser.add_argument("--artifacts-root", type=Path)

    actions = parser.add_subparsers(dest="action", required=True)
    actions.add_parser("check")
    actions.add_parser("install")

    discover = actions.add_parser("discover")
    discover.add_argument("--output", type=Path)

    configure = actions.add_parser("configure")
    configure.add_argument("--envsetup", default="build/envsetup.sh")
    configure.add_argument("--lunch", required=True)
    configure.add_argument("--product-out", required=True)
    configure.add_argument("--build-entry", default="")

    infer = actions.add_parser("infer-profile")
    infer.add_argument("--path", action="append", required=True)
    infer.add_argument("--profile", default="")
    infer.add_argument("--output", type=Path)

    profile = actions.add_parser("profile-set")
    profile.add_argument("--profile", required=True)
    profile.add_argument("--modules", required=True)
    profile.add_argument("--artifact", action="append", required=True)
    profile.add_argument("--touch-path", default="")

    plan = actions.add_parser("plan")
    plan.add_argument("--profile", required=True)

    checkpoint = actions.add_parser("checkpoint")
    checkpoint.add_argument("--name", required=True)
    checkpoint.add_argument("--purpose", default="")

    build = actions.add_parser("build")
    build.add_argument("--profile", required=True)
    build.add_argument("--command-id", required=True)
    build.add_argument("--jobs", type=int)
    build.add_argument("--mode", choices=("modules", "full"), default="modules")
    return parser.parse_args()


def run_process(command: list[str]) -> subprocess.CompletedProcess[str]:
    return run_channel(
        Path(command[0]), *command[1:],
        check=False,
        encoding="utf-8",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


class RemoteChannel:
    def __init__(self, *, executable: Path, ssh_host: str, project_root: str, timeout: int):
        expected = default_channel()
        if executable != expected:
            raise SystemExit(
                "--channel cannot override the bundled android-remote-channel owner"
            )
        self.executable = expected
        if self.executable.is_symlink() or not self.executable.is_file():
            raise SystemExit(
                f"bundled android-remote-channel executable not found: {self.executable}"
            )
        self.ssh_host = ssh_host
        self.project_root = project_root
        self.timeout = timeout
        self.workspace_id = ""
        self.canonical_root = ""

    def base(self) -> list[str]:
        return [
            str(self.executable),
            "--ssh-host",
            self.ssh_host,
            "--remote-root",
            self.project_root,
        ]

    def invoke(self, *arguments: str, allow_failure: bool = False) -> subprocess.CompletedProcess[str]:
        result = run_process([*self.base(), *arguments])
        if result.returncode and not allow_failure:
            detail = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)
            raise RemoteV2Error(
                detail or f"remote channel failed with {result.returncode}",
                returncode=result.returncode,
            )
        return result

    def check(self) -> str:
        result = self.invoke("check")
        workspace = WORKSPACE_RE.search(result.stdout)
        canonical = CANONICAL_RE.search(result.stdout)
        if not workspace or not canonical:
            raise RemoteV2Error("remote channel did not return canonical workspace identity")
        self.workspace_id = workspace.group(1)
        self.canonical_root = canonical.group(1)
        return result.stdout

    def run(self, *, command_id: str, command: str, lock: str) -> str:
        safe_id(command_id, "command-id")
        arguments = (
            "run",
            "--lock",
            lock,
            "--wait-timeout",
            str(self.timeout),
            "--command-id",
            command_id,
            "--",
            command,
        )
        result = self.invoke(*arguments, allow_failure=True)
        if result.returncode == 255:
            # The remote command may already be queued, running, or complete.
            # One retry with the identical id/payload attaches; it cannot rerun.
            result = self.invoke(*arguments, allow_failure=True)
        if result.returncode:
            detail = "\n".join(
                part for part in (result.stdout.strip(), result.stderr.strip()) if part
            )
            raise RemoteV2Error(
                detail or f"remote channel failed with {result.returncode}",
                returncode=result.returncode,
            )
        return result.stdout


def asset_payloads() -> dict[str, bytes]:
    manifest_module = PLUGIN_LIB / "android_engineering_ops" / "remote_artifact_manifest.py"
    assets = {
        "session.sh": SCRIPT_DIR / "remote_build_runtime.sh",
        "remote_profile_infer.py": SCRIPT_DIR / "remote_profile_infer.py",
        "remote_artifact_manifest_cli.py": SCRIPT_DIR / "remote_artifact_manifest_cli.py",
        "android_engineering_ops/remote_artifact_manifest.py": manifest_module,
    }
    result = {name: path.read_bytes() for name, path in assets.items()}
    result["android_engineering_ops/__init__.py"] = b"\n"
    return result


def payload_digest(payloads: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    for name, payload in sorted(payloads.items()):
        digest.update(name.encode("utf-8") + b"\0" + payload + b"\0")
    return digest.hexdigest()


def install_command(payloads: dict[str, bytes], *, digest: str, preserve_legacy: bool) -> str:
    encoded_writes = []
    verification_lines = []
    for name, payload in sorted(payloads.items()):
        encoded = base64.b64encode(payload).decode("ascii")
        encoded_writes.append(
            f"mkdir -p \"$stage/{shlex.quote(str(PurePosixPath(name).parent))}\"; "
            f"printf %s {shlex.quote(encoded)} | base64 -d > \"$stage/{name}\""
        )
        mode = "700" if name in {
            "session.sh",
            "remote_profile_infer.py",
            "remote_artifact_manifest_cli.py",
        } else "600"
        verification_lines.append(
            "verify_release_file \"$release/"
            + name
            + f"\" {hashlib.sha256(payload).hexdigest()} {mode} || return 1"
        )
    preserve = "true" if preserve_legacy else "false"
    writes = "\n".join(encoded_writes)
    verifications = "\n  ".join(verification_lines)
    expected_inventory = sorted(
        {
            "./android_engineering_ops",
            "./release.sha256",
            *(f"./{name}" for name in payloads),
        }
    )
    inventory_values = " ".join(shlex.quote(item) for item in expected_inventory)
    return f"""set -euo pipefail
umask 077
root=$(pwd -P)
legacy="$root/.codex/build-push.sh"
if [ -f "$legacy" ] && [ {preserve} != true ]; then
  echo 'LEGACY_WRAPPER_REVIEW_REQUIRED use --preserve-legacy to install alongside it' >&2
  exit 42
fi
base="$root/.codex/remote-v2"
release="$base/releases/{digest}"
mkdir -p "$base/releases"
stage=$(mktemp -d "$base/releases/.stage.{digest[:12]}.XXXXXX")
trap 'rm -rf "$stage" "$base/.current.$$"' EXIT
{writes}
chmod 700 "$stage/session.sh" "$stage/remote_profile_infer.py" "$stage/remote_artifact_manifest_cli.py"
chmod 600 "$stage/android_engineering_ops/__init__.py" "$stage/android_engineering_ops/remote_artifact_manifest.py"
printf '%s\n' {shlex.quote(digest)} >"$stage/release.sha256"
verify_release_file() {{
  local path="$1" expected_sha="$2" expected_mode="$3" actual_sha actual_mode
  [ -f "$path" ] && [ ! -L "$path" ] || return 1
  if command -v sha256sum >/dev/null 2>&1; then actual_sha=$(sha256sum "$path" | awk '{{print $1}}')
  else actual_sha=$(shasum -a 256 "$path" | awk '{{print $1}}'); fi
  actual_mode=$(stat -c '%a' "$path" 2>/dev/null || stat -f '%Lp' "$path" 2>/dev/null || true)
  [ "$actual_sha" = "$expected_sha" ] && [ "$actual_mode" = "$expected_mode" ]
}}
verify_release() {{
  actual_inventory=$(cd "$release" && find . -mindepth 1 -print | LC_ALL=C sort)
  expected_inventory=$(printf '%s\n' {inventory_values} | LC_ALL=C sort)
  [ "$actual_inventory" = "$expected_inventory" ] || return 1
  [ -f "$release/release.sha256" ] && [ ! -L "$release/release.sha256" ] || return 1
  [ "$(cat "$release/release.sha256" 2>/dev/null || true)" = {shlex.quote(digest)} ] || return 1
  metadata_mode=$(stat -c '%a' "$release/release.sha256" 2>/dev/null || stat -f '%Lp' "$release/release.sha256" 2>/dev/null || true)
  [ "$metadata_mode" = 600 ] || return 1
  {verifications}
}}
if [ -e "$release" ] && {{ [ ! -d "$release" ] || [ -L "$release" ]; }}; then
  echo "REMOTE_V2_RELEASE_TAMPERED path=$release" >&2
  exit 43
fi
if [ ! -d "$release" ]; then
  mv "$stage" "$release"
else
  if ! verify_release; then
    echo "REMOTE_V2_RELEASE_TAMPERED path=$release" >&2
    exit 43
  fi
  rm -rf "$stage"
fi
if ! verify_release; then
  echo "REMOTE_V2_RELEASE_TAMPERED path=$release" >&2
  exit 43
fi
ln -s "releases/{digest}" "$base/.current.$$"
python3 -c 'import os,sys; os.replace(sys.argv[1], sys.argv[2])' "$base/.current.$$" "$base/current"
if [ -f "$legacy" ]; then
  if command -v sha256sum >/dev/null 2>&1; then legacy_sha=$(sha256sum "$legacy" | awk '{{print $1}}');
  else legacy_sha=$(shasum -a 256 "$legacy" | awk '{{print $1}}'); fi
  {{
    printf 'LEGACY_SHA256=%q\n' "$legacy_sha"
    grep -q 'ARTIFACT_MTIME_BEFORE' "$legacy" && echo 'CAP_ARTIFACT_FRESHNESS=1' || echo 'CAP_ARTIFACT_FRESHNESS=0'
    grep -q 'TOUCH_TARGET' "$legacy" && echo 'CAP_TOUCH_TARGET=1' || echo 'CAP_TOUCH_TARGET=0'
    grep -q 'device_dir' "$legacy" && echo 'CAP_DEVICE_DESTINATION=1' || echo 'CAP_DEVICE_DESTINATION=0'
  }} >"$base/legacy-capabilities.env.tmp"
  chmod 600 "$base/legacy-capabilities.env.tmp"
  mv -f "$base/legacy-capabilities.env.tmp" "$base/legacy-capabilities.env"
  echo "LEGACY_WRAPPER_PRESERVED sha256=$legacy_sha"
fi
echo 'REMOTE_V2_INSTALL_OK release={digest}'
"""


def ensure_command_id(
    project_id: str,
    action: str,
    command: str,
    invocation_nonce: str,
) -> str:
    digest = hashlib.sha256(
        "\0".join((project_id, action, command, invocation_nonce)).encode("utf-8")
    ).hexdigest()[:24]
    return f"remote-v2-{action}-{digest}"


def install_runtime(
    channel: RemoteChannel,
    *,
    preserve_legacy: bool,
    invocation_nonce: str,
    project_id: str,
) -> str:
    payloads = asset_payloads()
    digest = payload_digest(payloads)
    mode = "preserve" if preserve_legacy else "strict"
    command = install_command(payloads, digest=digest, preserve_legacy=preserve_legacy)
    command_id = ensure_command_id(
        project_id,
        f"install-{mode}",
        command,
        invocation_nonce,
    )
    return channel.run(
        command_id=command_id,
        command=command,
        lock="exclusive",
    )


def shell_command(action: str, arguments: Iterable[str], *, source: bool = False) -> str:
    runtime = ".codex/remote-v2/current/session.sh"
    values = " ".join(shlex.quote(value) for value in arguments)
    if source:
        return f"source {shlex.quote(runtime)}; remote_v2_{action.replace('-', '_')} {values}"
    return f"bash {shlex.quote(runtime)} {shlex.quote(action)} {values}"


def unique_read_id(project_id: str, action: str, content: str) -> str:
    nonce = str(time.time_ns())
    suffix = hashlib.sha256(
        "\0".join((project_id, action, content, nonce)).encode("utf-8")
    ).hexdigest()[:24]
    return f"remote-v2-read-{action}-{suffix}"


def atomic_write(path: Path, text: str) -> None:
    path = require_safe_artifact_path(path.expanduser(), purpose="remote-v2 local handoff output")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    tmp = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def artifact_root(args: argparse.Namespace) -> Path:
    if args.artifacts_root:
        return args.artifacts_root.expanduser()
    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()
    return codex_home / "artifacts" / "android-remote-build-deploy" / args.project_id


def save_manifests(
    output: str,
    *,
    destination: Path,
    channel: RemoteChannel,
    profile: str,
    command_id: str,
) -> list[Path]:
    saved: list[Path] = []
    for line in output.splitlines():
        match = MANIFEST_LINE_RE.fullmatch(line)
        if not match:
            continue
        index, expected_module, _, encoded = match.groups()
        try:
            payload = json.loads(base64.b64decode(encoded, validate=True))
            validate_remote_artifact_manifest(
                payload,
                expected_module=expected_module,
                expected_profile=profile,
                expected_workspace_id=channel.workspace_id,
                expected_command_id=command_id,
                expected_remote_root=channel.canonical_root,
            )
        except (ValueError, TypeError, json.JSONDecodeError, RemoteArtifactManifestError) as exc:
            raise RemoteV2Error(f"remote artifact manifest rejected: {exc}") from exc
        target = destination / command_id / f"artifact-{int(index):03d}.json"
        atomic_write(target, json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
        saved.append(target)
    if not saved:
        raise RemoteV2Error("successful build returned no remote artifact manifests")
    return saved


def main() -> int:
    args = parse_args()
    require_target_install_family(PLUGIN_ROOT)
    args.project_id = safe_id(args.project_id, "project-id")
    args.working_subpath = safe_relative(args.working_subpath, "working-subpath")
    if args.wait_timeout <= 0:
        raise SystemExit("--wait-timeout must be positive")
    channel = RemoteChannel(
        executable=args.channel,
        ssh_host=args.ssh_host,
        project_root=args.project_root,
        timeout=args.wait_timeout,
    )
    invocation_nonce = f"{os.getpid()}-{time.time_ns()}-{os.urandom(4).hex()}"
    try:
        check_output = channel.check()
        if args.action == "check":
            print(check_output, end="")
            return 0
        install_output = install_runtime(
            channel,
            preserve_legacy=args.preserve_legacy,
            invocation_nonce=invocation_nonce,
            project_id=args.project_id,
        )
        if args.action == "install":
            print(install_output, end="")
            return 0

        if args.action == "discover":
            command = shell_command("discover", ["--working-subpath", args.working_subpath])
            output = channel.run(
                command_id=unique_read_id(args.project_id, "discover", command),
                command=command,
                lock="none",
            )
            if args.output:
                atomic_write(args.output, output)
            print(output, end="")
            return 0

        if args.action == "configure":
            values = [
                "--envsetup",
                args.envsetup,
                "--lunch",
                args.lunch,
                "--product-out",
                args.product_out,
                "--build-entry",
                args.build_entry,
            ]
            command = shell_command("configure", values)
            command_id = ensure_command_id(
                args.project_id,
                "configure",
                command,
                invocation_nonce,
            )
            print(channel.run(command_id=command_id, command=command, lock="exclusive"), end="")
            return 0

        if args.action == "infer-profile":
            values = [
                "--project-root",
                channel.canonical_root,
                "--working-subpath",
                args.working_subpath,
            ]
            for path in args.path:
                values.extend(["--path", safe_relative(path, "path")])
            if args.profile:
                values.extend(["--profile", safe_id(args.profile, "profile")])
            runtime = ".codex/remote-v2/current/remote_profile_infer.py"
            command = (
                f"PYTHONDONTWRITEBYTECODE=1 python3 {shlex.quote(runtime)} "
                + " ".join(shlex.quote(v) for v in values)
            )
            output = channel.run(
                command_id=unique_read_id(args.project_id, "infer", command),
                command=command,
                lock="none",
            )
            if args.output:
                atomic_write(args.output, output)
            print(output, end="")
            return 0

        if args.action == "profile-set":
            values = ["--profile", safe_id(args.profile, "profile"), "--modules", args.modules]
            for artifact in args.artifact:
                values.extend(["--artifact", artifact])
            if args.touch_path:
                values.extend(["--touch-path", safe_relative(args.touch_path, "touch-path")])
            command = shell_command("profile-set", values)
            command_id = ensure_command_id(
                args.project_id,
                "profile-set",
                command,
                invocation_nonce,
            )
            print(channel.run(command_id=command_id, command=command, lock="exclusive"), end="")
            return 0

        if args.action == "plan":
            profile = safe_id(args.profile, "profile")
            command = shell_command("plan", ["--profile", profile])
            print(
                channel.run(
                    command_id=unique_read_id(args.project_id, "plan", command),
                    command=command,
                    lock="none",
                ),
                end="",
            )
            return 0

        if args.action == "checkpoint":
            name = safe_id(args.name, "checkpoint name")
            command = shell_command("checkpoint", ["--name", name, "--purpose", args.purpose])
            command_id = f"checkpoint-{args.project_id}-{name}"
            print(channel.run(command_id=command_id, command=command, lock="exclusive"), end="")
            return 0

        if args.action == "build":
            profile = safe_id(args.profile, "profile")
            command_id = safe_id(args.command_id, "command-id")
            values = [
                "--profile",
                profile,
                "--workspace-id",
                channel.workspace_id,
                "--command-id",
                command_id,
                "--mode",
                args.mode,
            ]
            if args.jobs:
                values.extend(["--jobs", str(args.jobs)])
            command = shell_command("build", values, source=True)
            output = channel.run(command_id=command_id, command=command, lock="exclusive")
            manifests = save_manifests(
                output,
                destination=artifact_root(args) / "manifests",
                channel=channel,
                profile=profile,
                command_id=command_id,
            )
            print(output, end="")
            for path in manifests:
                print(f"LOCAL_ARTIFACT_MANIFEST path={path}")
            return 0
        raise AssertionError(args.action)
    except RemoteV2Error as exc:
        print(str(exc), file=sys.stderr)
        return exc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
