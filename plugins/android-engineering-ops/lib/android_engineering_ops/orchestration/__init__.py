"""Optional Android orchestration extension resolution."""

from .extension import (
    ExtensionResolution,
    ExtensionResolutionError,
    resolve_extension,
)

__all__ = [
    "ExtensionResolution",
    "ExtensionResolutionError",
    "resolve_extension",
]
