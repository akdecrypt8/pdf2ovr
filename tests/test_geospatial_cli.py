from __future__ import annotations

import json
from pathlib import Path

import pytest
from pypdf import PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, FloatObject, NameObject, NumberObject

from pdf2ovr.cli import main


def _array(values):
    return ArrayObject([FloatObject(float(value)) for value in values])


def _make_geopdf(path: Path):
    writer = PdfWriter()
    page = writer.add_blank_page(width=600, height=800)
    gcs = DictionaryObject({
        NameObject("/Type"): NameObject("/PROJCS"),
        NameObject("/EPSG"): NumberObject(32631),
    })
    measure = DictionaryObject({
        NameObject("/Subtype"): NameObject("/GEO"),
        NameObject("/GCS"): gcs,
        NameObject("/GPTS"): _array([500000, 4600000, 501000, 4600000, 501000, 4599000, 500000, 4599000]),
        NameObject("/LPTS"): _array([0, 0, 1, 0, 1, 1, 0, 1]),
    })
    viewport = DictionaryObject({
        NameObject("/Type"): NameObject("/Viewport"),
        NameObject("/BBox"): _array([100, 200, 500, 600]),
        NameObject("/Measure"): measure,
    })
    page[NameObject("/VP")] = ArrayObject([viewport])
    with path.open("wb") as stream:
        writer.write(stream)
    return path


def _invoke(argv):
    with pytest.raises(SystemExit) as exit_info:
        main(argv)
    return exit_info.value.code


def test_georef_cli_json(tmp_path, capsys):
    pdf = _make_geopdf(tmp_path / "map.pdf")
    assert _invoke(["georef", str(pdf), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["georeferenced_pages"] == [0]
    assert data["pages"][0]["viewports"][0]["crs"] == "EPSG:32631"


def test_georef_cli_strict_fails_for_non_geospatial_pdf(tmp_path):
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    pdf = tmp_path / "plain.pdf"
    with pdf.open("wb") as stream:
        writer.write(stream)
    assert _invoke(["georef", str(pdf), "--strict"]) == 4


def test_calibrate_and_transform_cli(tmp_path, capsys):
    calibration = tmp_path / "calibration.json"
    args = [
        "calibrate",
        "--gcp", "0,0,500000,4600000",
        "--gcp", "100,0,501000,4600000",
        "--gcp", "100,100,501000,4599000",
        "--gcp", "0,100,500000,4599000",
        "--crs", "EPSG:32631",
        "--output", str(calibration),
    ]
    assert _invoke(args) == 0
    report = capsys.readouterr().out
    assert "RMSE" in report
    assert calibration.is_file()

    assert _invoke(["transform", str(calibration), "--point", "50,50", "--target-crs", "EPSG:4326"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["crs"] == "EPSG:4326"
    assert result["x"] == pytest.approx(3.0, abs=0.01)
    assert result["y"] == pytest.approx(41.56, abs=0.1)


def test_calibrate_cli_does_not_overwrite_without_force(tmp_path):
    calibration = tmp_path / "calibration.json"
    calibration.write_text("preserve me", encoding="utf-8")
    args = [
        "calibrate",
        "--gcp", "0,0,0,0",
        "--gcp", "1,0,1,0",
        "--gcp", "0,1,0,1",
        "--output", str(calibration),
    ]
    assert _invoke(args) == 2
    assert calibration.read_text(encoding="utf-8") == "preserve me"
