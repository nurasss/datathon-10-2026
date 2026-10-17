"""Shared pytest fixtures (project paths, synthetic clips)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for extra in ("packages/vision", "packages/rules", "apps/api", "tests"):
    path = str(ROOT / extra)
    if path not in sys.path:
        sys.path.insert(0, path)

from support import make_clip  # noqa: E402


@pytest.fixture(scope="session")
def project_root() -> Path:
    return ROOT


@pytest.fixture(scope="session")
def violation_clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Cashier serves a client, then walks away while the client is still there."""

    out = tmp_path_factory.mktemp("clips") / "violation_like.mov"
    return make_clip(
        out,
        duration=14.0,
        fps=15.0,
        cashier_present=[(0.0, 6.0), (10.5, 14.0)],
        client_present=[(1.5, 13.0)],
    )


@pytest.fixture(scope="session")
def compliant_clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Cashier never leaves the workplace."""

    out = tmp_path_factory.mktemp("clips") / "compliant.mov"
    return make_clip(
        out,
        duration=12.0,
        fps=15.0,
        cashier_present=[(0.0, 12.0)],
        client_present=[(3.0, 11.0)],
    )


@pytest.fixture(scope="session")
def idle_clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Nobody at the workplace, nobody at the counter (no event expected)."""

    out = tmp_path_factory.mktemp("clips") / "idle.mov"
    return make_clip(out, duration=10.0, fps=10.0, cashier_present=[], client_present=[])
