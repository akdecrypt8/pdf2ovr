from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pdf2ovr

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _run_python(*args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT_ROOT)
    return subprocess.run(
        [sys.executable, *args],
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_official_import_package_and_public_api():
    assert pdf2ovr.__version__ == "0.6.0"
    assert pdf2ovr.__file__.replace("\\", "/").endswith("/pdf2ovr/__init__.py")
    assert "EANTDocument" in pdf2ovr.__all__
    assert "EANTWriter" in pdf2ovr.__all__
    assert "PDFExtractor" in pdf2ovr.__all__
    assert "GeoPDFDocument" in pdf2ovr.__all__
    assert "_filter_elements" not in pdf2ovr.__all__
    assert not hasattr(pdf2ovr, "generate")


def test_legacy_import_shim_reexports_without_duplicate_modules():
    result = _run_python(
        "-Wdefault",
        "-c",
        "import warnings; warnings.simplefilter('always'); "
        "import ovr_map_generator; "
        "from ovr_map_generator.eant_reader import EANTDocument as Old; "
        "from pdf2ovr.eant_reader import EANTDocument as New; "
        "assert Old is New; assert ovr_map_generator.EANTWriter is not None; "
        "assert ovr_map_generator.geospatial is __import__('pdf2ovr.geospatial', fromlist=['x']); "
        "print('legacy shim ok')",
    )
    assert result.returncode == 0, result.stderr
    assert "legacy shim ok" in result.stdout
    assert "deprecated" in result.stderr.lower()


def test_legacy_cli_module_is_a_wrapper_for_the_new_cli():
    result = _run_python("-Wdefault", "-m", "ovr_map_generator.cli", "--help")
    assert result.returncode == 0, result.stderr
    assert "{inspect,clone,add,extract,georef,calibrate,transform}" in result.stdout
    assert "deprecated" in result.stderr.lower()


def test_official_module_entrypoint_works():
    result = _run_python("-m", "pdf2ovr", "--help")
    assert result.returncode == 0, result.stderr
    assert "{inspect,clone,add,extract,georef,calibrate,transform}" in result.stdout


def test_obsolete_pdf_to_ovr_module_is_removed():
    result = _run_python("-m", "ovr_map_generator.ovr_map_from_pdf", "input.pdf")
    assert result.returncode != 0
    assert "No module named" in result.stderr
    assert "Done!" not in result.stdout


def test_old_four_positional_conversion_invocation_is_not_a_cli_command():
    result = _run_python("-m", "pdf2ovr", "input.pdf", "template.ovr", "template.map", "out")
    assert result.returncode != 0
    assert "invalid choice" in result.stderr or "unrecognized arguments" in result.stderr
    assert "Done!" not in result.stdout
