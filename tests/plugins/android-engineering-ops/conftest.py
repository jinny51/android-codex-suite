from __future__ import annotations

import json
import os
import shutil
import stat
from pathlib import Path
from typing import Iterator

import pytest


ROOT = Path(__file__).resolve().parents[3]
PLUGIN = ROOT / "plugins/android-engineering-ops"


@pytest.fixture(scope="session")
def installed_engineering_family(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[Path, Path, Path, Path]:
    """Materialize the same source/cache split produced by `codex plugin add`."""
    root = tmp_path_factory.mktemp("installed-engineering-family")
    home = root / "codex-home"
    marketplace = "android-codex-suite"
    source = home / ".tmp/marketplaces" / marketplace / "plugins/android-engineering-ops"
    runtime = home / "plugins/cache" / marketplace / "android-engineering-ops/2.0.2"
    for target in (source, runtime):
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(
            PLUGIN,
            target,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )

    executable_dir = root / "active-inventory-bin"
    executable_dir.mkdir()
    executable = executable_dir / "codex"
    payload = {
        "installed": [
            {
                "pluginId": "android-engineering-ops@android-codex-suite",
                "name": "android-engineering-ops",
                "marketplaceName": marketplace,
                "version": "2.0.2",
                "installed": True,
                "enabled": True,
                "source": {"source": "local", "path": str(source)},
            }
        ],
        "available": [],
    }
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        f"payload = {payload!r}\n"
        "if sys.argv[1:] != ['plugin', 'list', '--json']:\n"
        "    raise SystemExit(64)\n"
        "print(json.dumps(payload, sort_keys=True))\n",
        encoding="utf-8",
    )
    executable.chmod(stat.S_IMODE(executable.stat().st_mode) | stat.S_IXUSR)
    return home, source, runtime, executable_dir


@pytest.fixture(autouse=True)
def isolated_active_engineering_inventory(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    installed_engineering_family: tuple[Path, Path, Path, Path],
) -> Iterator[None]:
    """Run plugin tests as the target-only installed family.

    Production entry points never accept an inventory environment override.  The
    fixture therefore provides the same `codex plugin list --json` executable
    boundary they use in a newly started target-only session.
    """
    home, _source, runtime, executable_dir = installed_engineering_family
    # A test must opt in each public/effectful entrypoint explicitly.  Source roots,
    # library paths, contract files, and self-managed install fixtures stay bound to
    # the checkout so the suite cannot silently mix checkout imports with cached
    # file reads or copy its marketplace source from the runtime cache.
    declared = getattr(request.module, "INSTALLED_RUNTIME_ENTRYPOINTS", ())
    if (
        not isinstance(declared, tuple)
        or len(declared) != len(set(declared))
        or not all(isinstance(name, str) and name for name in declared)
    ):
        pytest.fail("INSTALLED_RUNTIME_ENTRYPOINTS must be a unique tuple of names")
    for name in declared:
        value = getattr(request.module, name, None)
        if not isinstance(value, Path):
            pytest.fail(f"installed runtime entrypoint is not a Path: {name}")
        try:
            relative = value.relative_to(PLUGIN)
        except ValueError as exc:
            pytest.fail(f"installed runtime entrypoint is outside the plugin: {name}")
            raise AssertionError from exc
        if (
            len(relative.parts) < 4
            or relative.parts[0] != "skills"
            or "scripts" not in relative.parts
            or not value.is_file()
        ):
            pytest.fail(f"installed runtime entrypoint is not a Skill script: {name}")
        installed = runtime / relative
        if not installed.is_file():
            pytest.fail(f"installed runtime entrypoint is missing from cache: {name}")
        monkeypatch.setattr(request.module, name, installed)

    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.setenv("PATH", f"{executable_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    yield
