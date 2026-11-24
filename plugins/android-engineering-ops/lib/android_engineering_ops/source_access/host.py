"""Detect the local host before selecting a source-access platform adapter."""

from __future__ import annotations

import os
import platform
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


SUPPORTED_HOSTS = ("windows", "wsl", "macos")


class UnsupportedSourceAccessHost(RuntimeError):
    """No source-access adapter is valid for the current host."""


@dataclass(frozen=True)
class SourceAccessHost:
    host: str
    system: str
    release: str
    version: str
    evidence: tuple[str, ...]


def _read_kernel_fact(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def detect_source_access_host(
    *,
    system: str | None = None,
    release: str | None = None,
    version: str | None = None,
    environ: Mapping[str, str] | None = None,
    kernel_osrelease: str | None = None,
    kernel_version: str | None = None,
) -> SourceAccessHost:
    actual_system = (system if system is not None else platform.system()).strip()
    actual_release = (release if release is not None else platform.release()).strip()
    actual_version = (version if version is not None else platform.version()).strip()
    environment = os.environ if environ is None else environ

    if actual_system == "Windows":
        return SourceAccessHost(
            host="windows",
            system=actual_system,
            release=actual_release,
            version=actual_version,
            evidence=("platform.system=Windows",),
        )

    if actual_system == "Darwin":
        return SourceAccessHost(
            host="macos",
            system=actual_system,
            release=actual_release,
            version=actual_version,
            evidence=("platform.system=Darwin",),
        )

    if actual_system == "Linux":
        observed_osrelease = (
            kernel_osrelease
            if kernel_osrelease is not None
            else _read_kernel_fact(Path("/proc/sys/kernel/osrelease"))
        )
        observed_version = (
            kernel_version
            if kernel_version is not None
            else _read_kernel_fact(Path("/proc/version"))
        )
        kernel_text = f"{observed_osrelease}\n{observed_version}".lower()
        if "microsoft" in kernel_text or "wsl" in kernel_text:
            evidence = ["proc.kernel-wsl-marker"]
            if environment.get("WSL_INTEROP"):
                evidence.append("env.WSL_INTEROP")
            if environment.get("WSL_DISTRO_NAME"):
                evidence.append("env.WSL_DISTRO_NAME")
            return SourceAccessHost(
                host="wsl",
                system=actual_system,
                release=actual_release,
                version=actual_version,
                evidence=tuple(evidence),
            )

    raise UnsupportedSourceAccessHost(
        "android-source-access supports WSL, macOS, and Windows only; "
        f"detected system={actual_system or '<empty>'} release={actual_release or '<empty>'}"
    )


def adapter_root(plugin_root: Path, host: str) -> Path:
    if host not in SUPPORTED_HOSTS:
        raise UnsupportedSourceAccessHost(f"unsupported source-access host: {host}")
    root = plugin_root / "adapters" / "source-access" / host
    if not root.is_dir():
        raise UnsupportedSourceAccessHost(
            f"source-access adapter is missing for host {host}: {root}"
        )
    return root
