from __future__ import annotations

from pathlib import Path
import sys

if sys.version_info >= (3, 11):
    import tomllib
else:  # Python 3.10 support in the configured CI matrix.
    import tomli as tomllib

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _project_metadata():
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as stream:
        return tomllib.load(stream)["project"]


def test_project_metadata_matches_identity_and_license_file():
    project = _project_metadata()
    license_text = (PROJECT_ROOT / "LICENSE").read_text(encoding="utf-8")
    assert project["name"] == "pdf2ovr"
    assert project["version"] == "0.6.0"
    assert project["authors"] == [{"name": "akdecrypt8"}]
    assert all("email" not in author for author in project["authors"])
    assert project["license"] == "MIT"
    assert project["license-files"] == ["LICENSE"]
    assert "Copyright (c) 2026 [COPYRIGHT HOLDER TO BE CONFIRMED]" in license_text
    assert license_text.startswith("MIT License\n")


def test_project_metadata_has_required_urls_and_realistic_classifiers():
    project = _project_metadata()
    urls = project["urls"]
    assert urls["Homepage"].endswith("/akdecrypt8/pdf2ovr")
    assert urls["Repository"] == urls["Homepage"]
    assert urls["Documentation"].endswith("/tree/main/docs")
    assert urls["Issues"].endswith("/issues")
    assert urls["Changelog"].endswith("/blob/main/CHANGELOG.md")
    classifiers = set(project["classifiers"])
    assert "Development Status :: 3 - Alpha" in classifiers
    # PEP 639 SPDX metadata replaces the legacy MIT Trove license classifier.
    assert project["license"] == "MIT"
    assert "Programming Language :: Python :: 3.13" in classifiers
    assert not any(item.startswith("Programming Language :: Python :: 3.10") for item in classifiers)
    assert "Topic :: Scientific/Engineering :: GIS" in classifiers


def test_project_metadata_preserves_all_optional_extras_and_console_script():
    project = _project_metadata()
    extras = project["optional-dependencies"]
    assert set(extras) == {"pdf", "geo", "test"}
    assert "PyMuPDF>=1.24" in extras["pdf"]
    assert "pypdf>=5.0" in extras["geo"]
    assert "pyproj>=3.6" in extras["geo"]
    assert any(item.startswith("pytest>=") for item in extras["test"])
    assert any(item.startswith("tomli") for item in extras["test"])
    assert project["scripts"]["pdf2ovr"] == "pdf2ovr.cli:main"
