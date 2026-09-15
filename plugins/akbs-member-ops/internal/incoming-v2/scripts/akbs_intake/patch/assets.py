from __future__ import annotations

import datetime as dt
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from akbs_intake.io_utils import materials_rel, sha1_file
from akbs_member_ops.incoming_v2.patch_markers import analyze_unified_diff_markers
from akbs_member_ops.patch_analysis import BANNED_LOG_PATTERNS
from akbs_intake.report_sessions import ymd


PATCH_FILENAME_RE = re.compile(r"^[a-z0-9]+[0-9]+-[A-Za-z0-9._-]+@[a-z0-9_.-]+\.patch$")
PATCH_README_HEADINGS = ("功能描述", "修改点", "日志控制", "SystemProperties", "字符串国际化", "可回滚性")
PATCH_README_PLACEHOLDER_RE = re.compile(r"(?im)^\s*(?:[-*]\s*)?TODO\b|TODO:")
PATCH_README_FORBIDDEN_MARKERS = (
    "自动生成的草稿说明",
    "根据补丁 diff 自动生成",
    "当前说明仅根据 diff 自动生成",
)
PACKAGE_CONTRACT_URI_RE = re.compile(
    r"knowledge-incoming-package/(?P<version>[0-9]+)/(?P<kind>[a-z][a-z0-9_]*)"
)


@dataclass
class PatchInfo:
    path: Path
    name: str
    project: str


def synthetic_patch_info(package_dir: Path, date: dt.date, project: str, config: dict[str, str]) -> PatchInfo:
    import uuid

    token = uuid.uuid4().hex[:8]
    patch_dir = package_dir / "evidence" / "synthetic"
    patch_dir.mkdir(parents=True, exist_ok=True)
    patch_name = f"mtk15-frameworks-base@synthetic-settings-{token}.patch"
    patch_path = patch_dir / patch_name
    readme_path = patch_dir / f"{patch_path.stem}.readme.md"
    patch_path.write_text(
        "\n".join(
            [
                "diff --git a/frameworks/base/core/java/android/provider/Settings.java b/frameworks/base/core/java/android/provider/Settings.java",
                "--- a/frameworks/base/core/java/android/provider/Settings.java",
                "+++ b/frameworks/base/core/java/android/provider/Settings.java",
                "@@ -1,3 +1,4 @@",
                f"+//synthetic {ymd(date)}@ synthetic test patch, not from real source code",
                "+// synthetic setting key: persist.sys.codex.synthetic_flag",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    readme_path.write_text(
        f"""# {patch_name}

## 功能描述

合成测试补丁，用于验证 incoming 协议、服务器解析、索引构建和可视化展示流程，不来自真实源码仓库。

## 修改点

- 合成一条注释级 diff，避免引入真实业务代码。
- 合成系统属性 `persist.sys.codex.synthetic_flag`，用于验证 symbol 索引。

## 日志控制

无新增运行时日志。

## SystemProperties

`persist.sys.codex.synthetic_flag`，仅作为合成测试索引样例。

## 字符串国际化

无新增字符串资源。

## 可回滚性

合成测试包可直接删除对应 incoming/patches 归档，不参与真实版本回滚。

## 补丁状态

- status: draft
- reuse_hint: false
- owner: {config["member_name"]} ({config["member_alias"]})
""",
        encoding="utf-8",
    )
    return PatchInfo(path=patch_path, name=patch_name, project=project)


def paired_readme(path: Path) -> Path | None:
    candidates = [path.with_suffix(".readme.md"), path.with_suffix(".md"), path.with_suffix(".txt")]
    return next((item for item in candidates if item.is_file()), None)


def patch_readme_template(patch: PatchInfo, config: dict[str, str], status: str = "draft", reuse_hint: bool = False) -> str:
    return f"""# {patch.name}

## 功能描述

TODO: 说明这个补丁解决的具体问题、适用平台和复用边界。

## 修改点

- TODO: 列出核心修改文件和关键逻辑。

## 日志控制

TODO: 说明是否使用 FrameworkLog，以及对应的 debug 属性。

## SystemProperties

TODO: 说明新增或依赖的系统属性；没有则写“无”。

## 字符串国际化

TODO: 说明是否新增字符串资源；没有则写“无”。

## 可回滚性

TODO: 说明回滚方式、风险点和验证建议。

## 补丁状态

- status: {status}
- reuse_hint: {str(reuse_hint).lower()}
- owner: {config["member_name"]} ({config["member_alias"]})
"""


def copy_patch_assets(
    package_dir: Path,
    patches: list[PatchInfo],
    config: dict[str, str],
    status: str = "draft",
    reuse_hint: bool = False,
    note: str = "待成员确认复用状态",
) -> list[dict[str, Any]]:
    patch_dir = package_dir / "patches"
    patch_dir.mkdir(parents=True, exist_ok=True)
    max_bytes = int(float(config.get("max_attachment_mb", "5")) * 1024 * 1024)
    entries: list[dict[str, Any]] = []
    for patch in patches:
        if patch.path.stat().st_size > max_bytes:
            continue
        target = patch_dir / patch.name
        shutil.copy2(patch.path, target)
        source_readme = paired_readme(patch.path)
        readme_target = patch_dir / f"{patch.path.stem}.readme.md"
        generated_readme = False
        if source_readme:
            shutil.copy2(source_readme, readme_target)
        else:
            readme_target.write_text(patch_readme_template(patch, config, status, reuse_hint), encoding="utf-8")
            generated_readme = True
        entries.append(
            {
                "path": f"patches/{target.name}",
                "readme": f"patches/{readme_target.name}",
                "content_sha1": sha1_file(target),
                "status": status,
                "reuse_hint": reuse_hint,
                "project": patch.project,
                "implementation_origin": "manual",
                "captured_by": "akbs-patch-submit",
                "coding_standard_check": {
                    "required": True,
                    "mode": "intake_patch_gate",
                    "result": "UNKNOWN",
                },
                "note": "缺少原始readme，已生成模板，提交前请补充" if generated_readme else note,
            }
        )
    return entries


def patch_infos_from_paths(paths: list[str], project: str) -> list[PatchInfo]:
    result: dict[Path, PatchInfo] = {}
    for raw in paths:
        path = Path(raw).expanduser().resolve()
        if not path.is_file():
            raise SystemExit(f"patch 文件不存在: {path}")
        if path.suffix != ".patch":
            raise SystemExit(f"不是 .patch 文件: {path}")
        result[path] = PatchInfo(path=path, name=path.name, project=project)
    return sorted(result.values(), key=lambda item: item.name)


def has_heading(text: str, heading: str) -> bool:
    return re.search(rf"^##\s+{re.escape(heading)}\s*$", text, re.M) is not None


def validate_patch_readme(
    path: Path,
    *,
    expected_version: str = "",
    expected_kind: str = "",
) -> list[str]:
    errors: list[str] = []
    text = path.read_text(encoding="utf-8", errors="ignore")
    if not text.strip():
        return [f"{path.name} readme 不能为空"]
    if PATCH_README_PLACEHOLDER_RE.search(text):
        errors.append(f"{path.name} readme 仍包含 TODO 模板内容")
    for marker in PATCH_README_FORBIDDEN_MARKERS:
        if marker in text:
            errors.append(f"{path.name} readme 包含草稿/模板说明: {marker}")
    for heading in PATCH_README_HEADINGS:
        if not has_heading(text, heading):
            errors.append(f"{path.name} 缺少必填章节: ## {heading}")
    if expected_version and expected_kind:
        declared = {
            (match.group("version"), match.group("kind"))
            for match in PACKAGE_CONTRACT_URI_RE.finditer(text)
        }
        if any(item != (expected_version, expected_kind) for item in declared):
            errors.append(
                f"{path.name} 包合同标识必须与 manifest 一致: "
                f"knowledge-incoming-package/{expected_version}/{expected_kind}"
            )
    return errors


def patch_readme_usable_for_inference(path: Path) -> bool:
    return path.is_file() and not validate_patch_readme(path)


def validate_patch_attribution(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    errors: list[str] = []
    for file_analysis in analyze_unified_diff_markers(text):
        if file_analysis.analysis is None:
            continue
        errors.extend(
            f"{path.name} {file_analysis.path} 作者日期标记无效: {error}"
            for error in file_analysis.analysis.errors
        )
    return errors


def validate_patch_file(path: Path) -> list[str]:
    errors: list[str] = []
    if not PATCH_FILENAME_RE.fullmatch(path.name):
        errors.append(f"patch 文件名不符合规范: {path.name}")
    text = path.read_text(encoding="utf-8", errors="ignore")
    added_lines = [line for line in text.splitlines() if line.startswith("+") and not line.startswith("+++")]
    errors.extend(validate_patch_attribution(path))
    for pattern in BANNED_LOG_PATTERNS:
        if any(pattern in line for line in added_lines):
            errors.append(f"{path.name} 新增代码禁止直接使用 {pattern}，应使用 FrameworkLog")
            break
    readme = paired_readme(path)
    if not readme:
        errors.append(f"{path.name} 缺少配套 readme")
    else:
        errors.extend(validate_patch_readme(readme))
    return errors


def write_feature_readme_from_patch_entries(package_dir: Path, summary: str, patch_entries: list[dict[str, Any]]) -> str:
    target_rel = materials_rel("readme.md")
    target = package_dir / target_rel
    target.parent.mkdir(parents=True, exist_ok=True)
    for entry in patch_entries:
        readme_rel = entry.get("readme")
        if isinstance(readme_rel, str) and readme_rel:
            source = package_dir / readme_rel
            if source.is_file():
                shutil.copy2(source, target)
                return target_rel
    patches = "\n".join(f"- `{entry.get('path', '')}`" for entry in patch_entries) or "- 待补充"
    target.write_text(
        f"""# {summary}

## 功能描述

TODO: 说明这个功能解决的具体问题、适用平台和复用边界。

## 修改点

{patches}

## 日志控制

TODO: 说明是否使用 FrameworkLog，以及对应的 debug 属性。

## SystemProperties

TODO: 说明新增或依赖的系统属性；没有则写“无”。

## 字符串国际化

TODO: 说明是否新增字符串资源；没有则写“无”。

## 可回滚性

TODO: 说明涉及的源码仓库、回滚顺序、风险点和验证建议。
""",
        encoding="utf-8",
    )
    return target_rel
