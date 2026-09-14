"""Compatibility routing for business-specific incoming v2 entrypoints."""

from __future__ import annotations

from collections.abc import Sequence


MODES = {"daily", "weekly", "patch"}


def route_arguments(mode: str, argv: Sequence[str]) -> list[str]:
    if mode not in MODES:
        raise ValueError(f"unsupported incoming mode: {mode}")
    values = list(argv)
    profile: list[str] = []
    remaining: list[str] = []
    index = 0
    while index < len(values):
        value = values[index]
        if value == "--profile":
            if index + 1 >= len(values) or not values[index + 1]:
                raise SystemExit("--profile requires a value")
            profile = ["--profile", values[index + 1]]
            index += 2
            continue
        if value.startswith("--profile="):
            selected = value.split("=", 1)[1]
            if not selected:
                raise SystemExit("--profile requires a value")
            profile = ["--profile", selected]
            index += 1
            continue
        remaining.append(value)
        index += 1
    return [*profile, mode, *remaining]
