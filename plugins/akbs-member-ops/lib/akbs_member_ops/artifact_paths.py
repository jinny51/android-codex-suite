from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path


OWNED_ARTIFACT_SCHEMA = "akbs-plugin-owned-artifact-v1"
OWNED_ARTIFACT_MARKER = ".akbs-plugin-owner.json"
OUTPUT_CATEGORIES = {
    "staging",
    "tmp",
    "test-runs",
    "diagnostics",
    "artifacts",
    "reports",
    "manifests",
}


def _is_plugin_suite_source_root(path: Path) -> bool:
    return (
        (path / ".agents" / "plugins" / "marketplace.json").is_file()
        and (path / "plugins").is_dir()
        and (path / "manifests").is_dir()
    )


def _has_parts(parts: tuple[str, ...], sequence: tuple[str, ...]) -> bool:
    width = len(sequence)
    return any(parts[index : index + width] == sequence for index in range(len(parts) - width + 1))


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _canonical_owned_output_allows(path: Path, authority_root: Path) -> bool:
    temporary_root = authority_root / "outputs" / "tmp"
    if not _is_within(path, temporary_root):
        return False
    helper = Path(
        os.environ.get("AKBS_OUTPUTS_HELPER", "").strip()
        or authority_root / "maintainer" / "scripts" / "akbs_outputs.py"
    ).expanduser()
    if not helper.is_file():
        return False
    if str(helper.parent) not in sys.path:
        sys.path.insert(0, str(helper.parent))
    current = path if path.is_dir() else path.parent
    while _is_within(current, temporary_root) and current != temporary_root:
        marker = current / ".akbs-output-owner.json"
        if marker.is_symlink():
            return False
        if marker.is_file():
            try:
                import akbs_outputs

                if Path(akbs_outputs.__file__).resolve() != helper.resolve():
                    return False
                payload = json.loads(marker.read_text(encoding="utf-8"))
                if not isinstance(payload, dict):
                    return False
                owned = akbs_outputs.OwnedOutputDirectory.load(
                    authority_root,
                    task_id=str(payload.get("task_id") or ""),
                    run_id=str(payload.get("run_id") or ""),
                    token=str(payload.get("token") or ""),
                    purpose=str(payload.get("purpose") or ""),
                )
            except (ImportError, OSError, RuntimeError, ValueError, json.JSONDecodeError):
                return False
            return _is_within(path, owned.path)
        current = current.parent
    return False


def artifact_path_guard_error(path: Path, *, purpose: str = "output") -> str:
    raw = _absolute_without_following(path)
    resolved = raw.resolve()
    candidates = (raw, resolved)
    forbidden_parts = {".git", "__pycache__", ".pytest_cache"}
    for candidate in candidates:
        posix = candidate.as_posix()
        parts = candidate.parts
        if any(part in forbidden_parts for part in parts):
            return f"{purpose} 不能写入源码或缓存目录: {raw}"
        if "/.codex/skills/" in posix or _has_parts(parts, ("skills", ".system")):
            return f"{purpose} 不能写入 Codex skill 安装目录: {raw}"
        if "/.codex/plugins/cache/" in posix or _has_parts(parts, ("plugins", "cache")):
            return f"{purpose} 不能写入 Codex 插件缓存目录: {raw}"
        for index in range(0, max(0, len(parts) - 2)):
            if parts[index] == "plugins" and parts[index + 2] == "skills":
                return f"{purpose} 不能写入插件 skill 源码目录: {raw}"
        for index, part in enumerate(parts[:-1]):
            if part == "skills" and parts[index + 1].startswith("akbs-"):
                return f"{purpose} 不能写入 AKBS 管理 skill 源码目录: {raw}"
        for ancestor in (candidate, *candidate.parents):
            if _is_plugin_suite_source_root(ancestor):
                return f"{purpose} 不能写入插件源码仓库: {raw}"

    configured_root = _absolute_without_following(
        Path(os.environ.get("AKBS_ROOT", "").strip() or Path.home() / "akbs")
    )
    if configured_root.is_symlink():
        return f"{purpose} 使用了符号链接 AKBS 根目录: {configured_root}"
    if _is_within(raw, configured_root):
        output = configured_root / "outputs"
        if not _is_within(raw, output):
            return f"{purpose} 位于 AKBS 根时必须写入 outputs 分类目录: {raw}"
        if output.is_symlink():
            return f"{purpose} 的 AKBS outputs 根不能是符号链接: {raw}"
        relative = raw.relative_to(output)
        current = output
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                return f"{purpose} 的 AKBS outputs 路径包含符号链接: {raw}"
        resolved_output = output.resolve(strict=output.exists())
        if not _is_within(resolved, resolved_output):
            return f"{purpose} 通过符号链接逃逸 AKBS outputs: {raw}"
        if not relative.parts:
            return f"{purpose} 不能直接写入 outputs 根: {raw}"
        if relative.parts[0] not in OUTPUT_CATEGORIES:
            return f"{purpose} 使用了未知 outputs 分类: {raw}"
        if relative.parts[0] == "tmp" and _canonical_owned_output_allows(raw, configured_root):
            return ""
        return f"{purpose} 不能直接写入 AKBS outputs；请使用默认受控输出流程: {raw}"
    return ""


def require_safe_artifact_path(path: Path, *, purpose: str = "output") -> Path:
    raw = _absolute_without_following(path)
    error = artifact_path_guard_error(raw, purpose=purpose)
    if error:
        raise SystemExit(error)
    return raw.resolve()


def _absolute_without_following(path: Path) -> Path:
    return Path(os.path.abspath(path.expanduser()))


def _is_artifact_link(path: Path) -> bool:
    if os.name == "nt":
        attributes = path.lstat().st_file_attributes
        return bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    return path.is_symlink()


def _artifact_owner(path: Path) -> dict[str, int | list[int]]:
    if os.name != "nt":
        return {"uid": os.getuid()}
    # Windows st_uid does not identify an owner. Bind the marker to the
    # exact directory created by this run, in addition to its token and path.
    identity = path.stat(follow_symlinks=False)
    if not identity.st_ino:
        raise RuntimeError(f"无法识别实际产物目录，拒绝接管或清理: {path}")
    return {"windows_directory_id": [identity.st_dev, identity.st_ino]}


@dataclass
class OwnedArtifactDirectory:
    path: Path
    canonical_path: Path
    token: str
    purpose: str
    committed: bool = False

    @property
    def marker(self) -> Path:
        return self.path / OWNED_ARTIFACT_MARKER

    @classmethod
    def create(cls, path: Path, *, purpose: str) -> OwnedArtifactDirectory:
        raw = _absolute_without_following(path)
        if os.path.lexists(raw):
            raise RuntimeError(f"{purpose} 已存在，拒绝接管或清理: {raw}")
        canonical = require_safe_artifact_path(raw, purpose=purpose)
        token = uuid.uuid4().hex
        owned = cls(raw, canonical, token, purpose)
        raw.mkdir(parents=True, mode=0o700)
        created_marker = None
        try:
            payload = {
                "schema": OWNED_ARTIFACT_SCHEMA,
                "token": token,
                "path": str(canonical),
                "purpose": purpose,
                **_artifact_owner(raw),
            }
            with owned.marker.open("x", encoding="utf-8") as stream:
                created_marker = owned.marker.lstat()
                json.dump(payload, stream, sort_keys=True)
                stream.write("\n")
            owned.marker.chmod(0o600)
        except BaseException:
            if created_marker is not None:
                try:
                    if os.path.samestat(created_marker, owned.marker.lstat()):
                        owned.marker.unlink()
                except OSError:
                    pass
            try:
                raw.rmdir()
            except OSError:
                pass
            raise
        return owned

    @classmethod
    def load(cls, path: Path, *, token: str, purpose: str) -> OwnedArtifactDirectory:
        raw = _absolute_without_following(path)
        if not os.path.lexists(raw):
            return cls(raw, raw, token, purpose, committed=True)
        if _is_artifact_link(raw) or not raw.is_dir():
            raise RuntimeError(f"{purpose} 所有权路径不是实际目录，拒绝清理: {raw}")
        canonical = require_safe_artifact_path(raw, purpose=purpose)
        marker = raw / OWNED_ARTIFACT_MARKER
        if not os.path.lexists(marker) or _is_artifact_link(marker) or not marker.is_file():
            raise RuntimeError(f"{purpose} 缺少有效所有权标记，拒绝清理: {raw}")
        payload = json.loads(marker.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("schema") != OWNED_ARTIFACT_SCHEMA:
            raise RuntimeError(f"{purpose} 所有权标记 schema 无效，拒绝清理: {raw}")
        if payload.get("token") != token:
            raise RuntimeError(f"{purpose} 所有权 token 不匹配，拒绝清理: {raw}")
        expected_owner = _artifact_owner(raw)
        if any(payload.get(key) != value for key, value in expected_owner.items()):
            raise RuntimeError(f"{purpose} 产物目录所有权不匹配，拒绝清理: {raw}")
        if "uid" in expected_owner and (
            raw.stat().st_uid != expected_owner["uid"] or marker.stat().st_uid != expected_owner["uid"]
        ):
            raise RuntimeError(f"{purpose} 所有权 uid 不匹配，拒绝清理: {raw}")
        if Path(str(payload.get("path"))).resolve() != canonical:
            raise RuntimeError(f"{purpose} canonical path 已变化，拒绝清理: {raw}")
        return cls(raw, canonical, token, purpose)

    def cleanup(self) -> None:
        if self.committed:
            return
        owned = self.load(self.path, token=self.token, purpose=self.purpose)
        if owned.committed:
            self.committed = True
            return
        shutil.rmtree(owned.path)
        self.committed = True

    def commit(self) -> None:
        if self.committed:
            return
        owned = self.load(self.path, token=self.token, purpose=self.purpose)
        if owned.committed:
            raise RuntimeError(f"{self.purpose} 在提交前已消失: {self.path}")
        owned.marker.unlink()
        self.committed = True

def main() -> int:
    parser = argparse.ArgumentParser(description="Fail closed when an artifact output targets plugin source or cache paths.")
    parser.add_argument("--owned-create", action="store_true", help="Create a new marker-owned output directory and print its token.")
    parser.add_argument("--owned-cleanup", action="store_true", help="Remove an owned output directory after verifying its token.")
    parser.add_argument("--owned-commit", action="store_true", help="Keep a completed output directory and remove its marker.")
    parser.add_argument("--token", default="")
    parser.add_argument("path", type=Path)
    parser.add_argument("--purpose", default="output")
    args = parser.parse_args()
    actions = sum((args.owned_create, args.owned_cleanup, args.owned_commit))
    if actions > 1:
        raise SystemExit("owned artifact actions are mutually exclusive")
    if args.owned_create:
        print(OwnedArtifactDirectory.create(args.path, purpose=args.purpose).token)
        return 0
    if args.owned_cleanup:
        if not args.token:
            raise SystemExit("--owned-cleanup requires --token")
        OwnedArtifactDirectory.load(args.path, token=args.token, purpose=args.purpose).cleanup()
        return 0
    if args.owned_commit:
        if not args.token:
            raise SystemExit("--owned-commit requires --token")
        OwnedArtifactDirectory.load(args.path, token=args.token, purpose=args.purpose).commit()
        return 0
    print(require_safe_artifact_path(args.path, purpose=args.purpose))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
