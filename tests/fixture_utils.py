"""Paths for independently generated synthetic HFA/EANT test fixtures."""
from pathlib import Path

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
MODERN_FIXTURE = FIXTURE_DIR / "synthetic_modern.ovr"
CLASSIC_FIXTURE = FIXTURE_DIR / "synthetic_classic.ovr"


def modern_fixture() -> Path:
    if not MODERN_FIXTURE.is_file():
        raise AssertionError(f"Required synthetic fixture is missing: {MODERN_FIXTURE}")
    return MODERN_FIXTURE


def classic_fixture() -> Path:
    if not CLASSIC_FIXTURE.is_file():
        raise AssertionError(f"Required synthetic fixture is missing: {CLASSIC_FIXTURE}")
    return CLASSIC_FIXTURE
