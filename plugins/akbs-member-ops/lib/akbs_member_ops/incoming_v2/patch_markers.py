"""Parse the frozen attribution-marker grammar used by incoming v2 packages."""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from pathlib import Path


ALIAS_PATTERN = r"^[a-z0-9][a-z0-9._-]{1,63}$"
ALIAS_RE = re.compile(ALIAS_PATTERN)
MARKER_RE = re.compile(
    r"//(?P<alias>[A-Za-z0-9][A-Za-z0-9_.-]*)\s+"
    r"(?P<date>\d{8})@(?P<delimiter>[{}]?)"
)
SLASH_LINE_COMMENT_SUFFIXES = {
    ".aidl",
    ".bp",
    ".c",
    ".cc",
    ".cpp",
    ".cxx",
    ".h",
    ".hh",
    ".hpp",
    ".java",
    ".kt",
    ".kts",
    ".proto",
    ".rs",
}


@dataclass(frozen=True)
class PatchMarker:
    alias: str
    date: str
    delimiter: str
    line_number: int

    @property
    def kind(self) -> str:
        return {"{": "open", "}": "close", "": "legacy"}[self.delimiter]


@dataclass(frozen=True)
class MarkerAnalysis:
    markers: tuple[PatchMarker, ...]
    errors: tuple[str, ...]

    @property
    def aliases(self) -> tuple[str, ...]:
        return tuple(sorted({marker.alias for marker in self.markers}))

    @property
    def dates(self) -> tuple[str, ...]:
        return tuple(sorted({marker.date for marker in self.markers}))

    @property
    def has_marker(self) -> bool:
        return bool(self.markers)

    @property
    def has_legacy_marker(self) -> bool:
        return any(marker.kind == "legacy" for marker in self.markers)

    @property
    def valid(self) -> bool:
        return self.has_marker and not self.errors


@dataclass(frozen=True)
class PatchFileMarkerAnalysis:
    path: str
    comment_adapter: str
    analysis: MarkerAnalysis | None

    @property
    def applicable(self) -> bool:
        return self.analysis is not None


def require_valid_alias(member_alias: str) -> str:
    value = member_alias.strip()
    if not ALIAS_RE.fullmatch(value):
        raise ValueError("member_alias must match the Android change policy alias pattern")
    return value


def require_valid_date(value: str | dt.date) -> str:
    if isinstance(value, dt.date):
        return value.strftime("%Y%m%d")
    try:
        parsed = dt.datetime.strptime(value, "%Y%m%d").date()
    except ValueError as exc:
        raise ValueError("marker date must be a real calendar date in yyyyMMdd format") from exc
    return parsed.strftime("%Y%m%d")


def opening_marker(member_alias: str, date: str | dt.date) -> str:
    return f"//{require_valid_alias(member_alias)} {require_valid_date(date)}@{{"


def closing_marker(member_alias: str, date: str | dt.date) -> str:
    return f"//{require_valid_alias(member_alias)} {require_valid_date(date)}@}}"


def analyze_patch_markers(
    text: str,
    *,
    expected_alias: str | None = None,
    require_pairs: bool = False,
) -> MarkerAnalysis:
    expected = require_valid_alias(expected_alias) if expected_alias is not None else None
    markers: list[PatchMarker] = []
    errors: list[str] = []
    stack: list[PatchMarker] = []

    for line_number, line in enumerate(text.splitlines(), start=1):
        for match in MARKER_RE.finditer(line):
            marker = PatchMarker(
                alias=match.group("alias"),
                date=match.group("date"),
                delimiter=match.group("delimiter"),
                line_number=line_number,
            )
            markers.append(marker)
            try:
                require_valid_date(marker.date)
            except ValueError:
                errors.append(
                    f"line {line_number}: marker date {marker.date} is not a real yyyyMMdd date"
                )
            if expected is not None and marker.alias != expected:
                errors.append(
                    f"line {line_number}: marker alias {marker.alias!r} does not match "
                    f"current member_alias {expected!r}"
                )
            if marker.kind == "legacy":
                if require_pairs:
                    errors.append(
                        f"line {line_number}: new Codex-authored changes require paired @{{ and @}} markers"
                    )
                continue
            if marker.kind == "open":
                stack.append(marker)
                continue
            if not stack:
                errors.append(f"line {line_number}: closing marker has no opening marker")
                continue
            opening = stack.pop()
            if (opening.alias, opening.date) != (marker.alias, marker.date):
                errors.append(
                    f"line {line_number}: closing marker does not match opening marker "
                    f"on line {opening.line_number}"
                )

    for opening in stack:
        errors.append(f"line {opening.line_number}: opening marker has no closing marker")
    if not markers:
        errors.append("patch has no author/date marker")
    return MarkerAnalysis(markers=tuple(markers), errors=tuple(errors))


def _diff_sections(diff_text: str) -> list[str]:
    sections: list[list[str]] = []
    current: list[str] = []
    for line in diff_text.splitlines(keepends=True):
        if line.startswith("diff --git ") and current:
            sections.append(current)
            current = [line]
        else:
            current.append(line)
    if current:
        sections.append(current)
    return ["".join(section) for section in sections if section]


def _diff_path(section: str) -> str:
    match = re.search(r"^diff --git a/(.+?) b/(.+)$", section, re.M)
    if match:
        old_path, new_path = match.groups()
        return old_path if new_path == "/dev/null" else new_path
    match = re.search(r"^\+\+\+\s+(?:b/)?(.+)$", section, re.M)
    if match and match.group(1) != "/dev/null":
        return match.group(1)
    return "<unknown>"


def _added_lines(section: str) -> list[str]:
    return [
        line[1:]
        for line in section.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    ]


def slash_line_comment_applicable(path: str) -> bool:
    if path == "<unknown>":
        return True
    return Path(path).suffix.lower() in SLASH_LINE_COMMENT_SUFFIXES


def analyze_unified_diff_markers(
    diff_text: str,
    *,
    expected_alias: str | None = None,
    require_pairs: bool = False,
) -> tuple[PatchFileMarkerAnalysis, ...]:
    results: list[PatchFileMarkerAnalysis] = []
    for section in _diff_sections(diff_text):
        added = _added_lines(section)
        if not added:
            continue
        path = _diff_path(section)
        if not slash_line_comment_applicable(path):
            results.append(
                PatchFileMarkerAnalysis(
                    path=path,
                    comment_adapter="NOT_APPLICABLE_NO_ADAPTER",
                    analysis=None,
                )
            )
            continue
        results.append(
            PatchFileMarkerAnalysis(
                path=path,
                comment_adapter="slash_line",
                analysis=analyze_patch_markers(
                    "\n".join(added),
                    expected_alias=expected_alias,
                    require_pairs=require_pairs,
                ),
            )
        )
    return tuple(results)
