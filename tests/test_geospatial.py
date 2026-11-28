from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
from pypdf import PdfWriter
from pypdf.generic import (
    ArrayObject, DictionaryObject, FloatObject, NameObject,
    NumberObject, TextStringObject,
)

from pdf2ovr.geospatial import (
    AffineTransform,
    CalibrationError,
    GeoPDFDocument,
    GeoPDFError,
    GroundControlPoint,
    extractor_to_pdf_user_space,
    fit_affine,
    pdf_user_to_extractor_space,
    reproject_point,
)


def _arr(values):
    return ArrayObject([FloatObject(float(value)) for value in values])


def _make_iso_pdf(path: Path, *, geographic=False, pages=1, multiple=False, pcsm=False, pcsm_values=None):
    writer = PdfWriter()
    for _ in range(pages):
        page = writer.add_blank_page(width=600, height=800)
        if geographic:
            gcs = DictionaryObject({
                NameObject("/Type"): NameObject("/GEOGCS"),
                NameObject("/EPSG"): NumberObject(4326),
            })
            gpts = [36.0, 3.0, 36.0, 3.1, 35.9, 3.1, 35.9, 3.0]
        else:
            gcs = DictionaryObject({
                NameObject("/Type"): NameObject("/PROJCS"),
                NameObject("/EPSG"): NumberObject(32631),
            })
            gpts = [500000, 4600000, 501000, 4600000, 501000, 4599000, 500000, 4599000]
        measure = DictionaryObject({
            NameObject("/Subtype"): NameObject("/GEO"),
            NameObject("/GCS"): gcs,
            NameObject("/GPTS"): _arr(gpts),
            NameObject("/LPTS"): _arr([0, 0, 1, 0, 1, 1, 0, 1]),
            NameObject("/Bounds"): _arr([0, 0, 1, 0, 1, 1, 0, 1]),
        })
        if pcsm:
            measure[NameObject("/PCSM")] = _arr(pcsm_values or [1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 1])
        viewport = DictionaryObject({
            NameObject("/Type"): NameObject("/Viewport"),
            NameObject("/Name"): TextStringObject("Main Map"),
            NameObject("/BBox"): _arr([100, 200, 500, 600]),
            NameObject("/Measure"): measure,
        })
        viewports = [viewport]
        if multiple:
            second = DictionaryObject({
                NameObject("/Type"): NameObject("/Viewport"),
                NameObject("/Name"): TextStringObject("Inset"),
                NameObject("/BBox"): _arr([200, 300, 400, 500]),
                NameObject("/Measure"): measure,
            })
            viewports.append(second)
        page[NameObject("/VP")] = ArrayObject(viewports)
    with path.open("wb") as stream:
        writer.write(stream)
    return path


def _make_ogc_pdf(path: Path, *, with_registration=True, with_ctm=False, with_neatline=True, multiple=False, ctm_values=None):
    writer = PdfWriter()
    page = writer.add_blank_page(width=400, height=300)
    projection = DictionaryObject({
        NameObject("/Type"): NameObject("/Projection"),
        NameObject("/EPSG"): NumberObject(32631),
        NameObject("/Projection"): NameObject("/UTM"),
        NameObject("/Zone"): NumberObject(31),
    })
    frame = DictionaryObject({
        NameObject("/Type"): NameObject("/LGIDict"),
        NameObject("/Version"): TextStringObject("2.2"),
        NameObject("/Projection"): projection,
    })
    if with_neatline:
        frame[NameObject("/Neatline")] = _arr([0, 0, 400, 0, 400, 300, 0, 300])
    if with_registration:
        frame[NameObject("/Registration")] = ArrayObject([
            _arr([0, 0, 500000, 4600000]),
            _arr([400, 0, 501000, 4600000]),
            _arr([400, 300, 501000, 4599000]),
            _arr([0, 300, 500000, 4599000]),
        ])
    if with_ctm:
        # Map = [2.5*PDF_x + 500000, (-10/3)*PDF_y + 4600000]
        frame[NameObject("/CTM")] = _arr(ctm_values or [2.5, 0, 0, -10 / 3, 500000, 4600000])
    if multiple:
        second = DictionaryObject({
            NameObject("/Type"): NameObject("/LGIDict"),
            NameObject("/Version"): TextStringObject("2.2"),
            NameObject("/Projection"): projection,
            NameObject("/Registration"): ArrayObject([
                _arr([10, 10, 600000, 4600000]),
                _arr([390, 10, 601000, 4600000]),
                _arr([390, 290, 601000, 4599000]),
                _arr([10, 290, 600000, 4599000]),
            ]),
        })
        frames = ArrayObject([frame, second])
        page[NameObject("/LGIDict")] = frames
    else:
        page[NameObject("/LGIDict")] = frame
    with path.open("wb") as stream:
        writer.write(stream)
    return path


def test_iso_geopdf_extracts_projected_gcp_and_affine(tmp_path):
    path = _make_iso_pdf(tmp_path / "iso.pdf")
    document = GeoPDFDocument(path)
    assert document.georeferenced_pages == [0]
    ref = document.pages[0].viewports[0]
    assert ref.encoding == "adobe-iso32000-geo"
    assert ref.crs == "EPSG:32631"
    assert ref.crs_resolved is True
    assert len(ref.control_points) == 4
    assert ref.transform is not None
    assert ref.map_point(100, 200) == pytest.approx((500000, 4600000))
    assert ref.map_point(500, 600) == pytest.approx((501000, 4599000))
    assert ref.fit.rmse == pytest.approx(0, abs=2e-9)


def test_iso_geographic_gpts_lat_lon_is_converted_to_xy(tmp_path):
    path = _make_iso_pdf(tmp_path / "geo.pdf", geographic=True)
    ref = GeoPDFDocument(path).pages[0].viewports[0]
    assert ref.crs == "EPSG:4326"
    assert ref.control_points[0].map_x == pytest.approx(3.0)  # longitude
    assert ref.control_points[0].map_y == pytest.approx(36.0)  # latitude


def test_iso_multipage_and_multiple_viewports_are_preserved(tmp_path):
    path = _make_iso_pdf(tmp_path / "multipage.pdf", pages=2, multiple=True)
    document = GeoPDFDocument(path)
    assert document.georeferenced_pages == [0, 1]
    assert document.summary()["viewport_count"] == 4
    assert [v.metadata["viewport_name"] for v in document.pages[0].viewports] == ["Main Map", "Inset"]


def test_iso_projected_pcsm_takes_priority_over_gpts(tmp_path):
    # [u, v, 0, 1] * matrix => X=100+2u, Y=200+3v, Z=0.
    values = [2, 0, 0, 0, 3, 0, 0, 0, 1, 100, 200, 0]
    path = _make_iso_pdf(tmp_path / "pcsm.pdf", pcsm=True, pcsm_values=values)
    ref = GeoPDFDocument(path).pages[0].viewports[0]
    assert ref.transform is not None
    assert ref.fit is None
    assert ref.map_point(100, 200) == pytest.approx((100, 200))
    assert ref.map_point(500, 600) == pytest.approx((102, 203))
    assert any("PCSM takes priority" in warning for warning in ref.warnings)


def test_iso_pcsm_is_ignored_for_geographic_gcs(tmp_path):
    path = _make_iso_pdf(tmp_path / "geo-pcsm.pdf", geographic=True, pcsm=True)
    ref = GeoPDFDocument(path).pages[0].viewports[0]
    assert ref.transform is not None
    assert ref.map_point(100, 200) == pytest.approx((3.0, 36.0))
    assert any("PCSM is present but ignored" in warning for warning in ref.warnings)


def test_ogc_lgidict_registration_extracts_transform_and_neatline(tmp_path):
    path = _make_ogc_pdf(tmp_path / "ogc.pdf")
    ref = GeoPDFDocument(path).pages[0].viewports[0]
    assert ref.encoding == "ogc-geopdf-lgidict"
    assert ref.crs == "EPSG:32631"
    assert len(ref.control_points) == 4
    assert ref.map_point(400, 300) == pytest.approx((501000, 4599000))
    assert len(ref.neatline_pdf) == 4


def test_ogc_ctm_maps_pdf_coordinates_directly_when_registration_absent(tmp_path):
    path = _make_ogc_pdf(tmp_path / "ogc-ctm.pdf", with_registration=False, with_ctm=True)
    ref = GeoPDFDocument(path).pages[0].viewports[0]
    assert ref.transform is not None
    assert ref.map_point(0, 0) == pytest.approx((500000, 4600000))
    assert ref.map_point(400, 300) == pytest.approx((501000, 4599000))


def test_non_geospatial_pdf_is_reported_not_guessed(tmp_path):
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    path = tmp_path / "plain.pdf"
    with path.open("wb") as stream:
        writer.write(stream)
    document = GeoPDFDocument(path)
    assert document.georeferenced_pages == []
    assert document.pages[0].warnings


def test_iso_missing_lpts_does_not_guess_transform(tmp_path):
    writer = PdfWriter()
    page = writer.add_blank_page(width=100, height=100)
    gcs = DictionaryObject({NameObject("/Type"): NameObject("/PROJCS"), NameObject("/EPSG"): NumberObject(32631)})
    measure = DictionaryObject({
        NameObject("/Subtype"): NameObject("/GEO"),
        NameObject("/GCS"): gcs,
        NameObject("/GPTS"): _arr([0, 0, 1, 0, 1, 1]),
    })
    page[NameObject("/VP")] = ArrayObject([DictionaryObject({
        NameObject("/BBox"): _arr([0, 0, 100, 100]),
        NameObject("/Measure"): measure,
    })])
    path = tmp_path / "no-lpts.pdf"
    with path.open("wb") as stream:
        writer.write(stream)
    document = GeoPDFDocument(path)
    assert len(document.pages[0].viewports) == 1
    assert document.pages[0].viewports[0].transform is None
    assert any("LPTS is missing" in warning for warning in document.pages[0].viewports[0].warnings)


def test_affine_fit_with_four_points_and_residual_diagnostics():
    gcps = [
        GroundControlPoint(0, 0, 10, 20),
        GroundControlPoint(100, 0, 210, 20),
        GroundControlPoint(100, 100, 210, -80),
        GroundControlPoint(0, 100, 10, -80),
    ]
    fit = fit_affine(gcps)
    assert fit.transform.apply(50, 50) == pytest.approx((110, -30))
    assert fit.rmse == pytest.approx(0, abs=1e-10)
    assert fit.max_error == pytest.approx(0, abs=1e-10)


def test_affine_fit_rejects_collinear_gcps():
    gcps = [
        GroundControlPoint(0, 0, 1, 2),
        GroundControlPoint(10, 10, 3, 4),
        GroundControlPoint(20, 20, 5, 6),
    ]
    with pytest.raises(CalibrationError):
        fit_affine(gcps)


def test_affine_inverse_round_trip():
    transform = AffineTransform(2, 0.5, -0.25, -3, 100, 400)
    point = (12.5, -3.25)
    mapped = transform.apply(*point)
    assert transform.inverse().apply(*mapped) == pytest.approx(point)


def test_crs_reprojection_uses_xy_axis_order():
    lon, lat = reproject_point(500000, 4600000, "EPSG:32631", "EPSG:4326")
    assert lon == pytest.approx(3.0, abs=0.01)
    assert lat == pytest.approx(41.56, abs=0.1)


def test_extractor_page_coordinate_adapter_round_trip_with_rotation_and_crop(tmp_path):
    writer = PdfWriter()
    page = writer.add_blank_page(width=600, height=800)
    page.cropbox.lower_left = (30, 40)
    page.cropbox.upper_right = (570, 760)
    page.rotate(90)
    path = tmp_path / "rotated-crop.pdf"
    with path.open("wb") as stream:
        writer.write(stream)
    source_point = (100.0, 200.0)
    displayed = pdf_user_to_extractor_space(path, 0, *source_point)
    restored = extractor_to_pdf_user_space(path, 0, *displayed)
    assert restored == pytest.approx(source_point)


def test_viewport_selection_uses_last_matching_viewport(tmp_path):
    path = _make_iso_pdf(tmp_path / "overlap.pdf", multiple=True)
    page = GeoPDFDocument(path).pages[0]
    # In the overlapping region both frames contain this point; PDF viewports
    # are examined from last to first, so the later inset is selected.
    selected = page.select_viewport(250, 350)
    assert selected is page.viewports[1]


def test_map_point_reprojects_only_when_source_crs_is_known(tmp_path):
    path = _make_iso_pdf(tmp_path / "iso-reproject.pdf")
    ref = GeoPDFDocument(path).pages[0].viewports[0]
    lon, lat = ref.map_point(100, 200, target_crs="EPSG:4326")
    assert lon == pytest.approx(3.0, abs=0.01)
    assert lat == pytest.approx(41.56, abs=0.1)


def test_ogc_degenerate_ctm_is_warning_not_uncaught_exception(tmp_path):
    writer = PdfWriter()
    page = writer.add_blank_page(width=400, height=300)
    frame = DictionaryObject({
        NameObject("/Type"): NameObject("/LGIDict"),
        NameObject("/CTM"): _arr([1, 2, 2, 4, 0, 0]),
    })
    page[NameObject("/LGIDict")] = frame
    path = tmp_path / "ogc-bad-ctm.pdf"
    with path.open("wb") as stream:
        writer.write(stream)
    ref = GeoPDFDocument(path).pages[0].viewports[0]
    assert ref.transform is None
    assert any("singular" in warning for warning in ref.warnings)



def test_single_ogc_frame_without_neatline_covers_page(tmp_path):
    path = _make_ogc_pdf(tmp_path / "ogc-no-neatline.pdf", with_neatline=False)
    page = GeoPDFDocument(path).pages[0]
    assert page.viewports[0].neatline_pdf == []
    assert page.select_viewport(200, 150) is page.viewports[0]


def test_multiple_ogc_frames_without_neatline_are_not_auto_selected(tmp_path):
    path = _make_ogc_pdf(tmp_path / "ogc-multiple-no-neatline.pdf", with_neatline=False, multiple=True)
    page = GeoPDFDocument(path).pages[0]
    assert len(page.viewports) == 2
    assert all(any("no /Neatline" in warning for warning in item.warnings) for item in page.viewports)
    assert page.select_viewport(200, 150) is None


def test_affine_coefficients_must_be_finite():
    with pytest.raises(ValueError, match="must be finite"):
        AffineTransform(float("nan"), 0, 0, 1, 0, 0)


def test_affine_fit_handles_overdetermined_noisy_control_points():
    gcps = [
        GroundControlPoint(0, 0, 100, 200),
        GroundControlPoint(100, 0, 300.02, 200),
        GroundControlPoint(100, 100, 300, 0.01),
        GroundControlPoint(0, 100, 100, 0),
        GroundControlPoint(50, 50, 200.01, 100),
        GroundControlPoint(25, 75, 150, 50.01),
    ]
    fit = fit_affine(gcps)
    assert fit.point_count == 6
    assert fit.rmse < 0.02
    assert fit.max_error < 0.03


def test_invalid_iso_bounds_are_reported_and_not_selected(tmp_path):
    writer = PdfWriter()
    page = writer.add_blank_page(width=400, height=300)
    gcs = DictionaryObject({NameObject("/Type"): NameObject("/PROJCS"), NameObject("/EPSG"): NumberObject(32631)})
    measure = DictionaryObject({
        NameObject("/Subtype"): NameObject("/GEO"),
        NameObject("/GCS"): gcs,
        NameObject("/GPTS"): _arr([500000, 4600000, 501000, 4600000, 501000, 4599000]),
        NameObject("/LPTS"): _arr([0, 0, 1, 0, 1, 1]),
        NameObject("/Bounds"): _arr([0, 0, 1, 1]),  # only two points, not a polygon
    })
    page[NameObject("/VP")] = ArrayObject([DictionaryObject({
        NameObject("/BBox"): _arr([0, 0, 400, 300]),
        NameObject("/Measure"): measure,
    })])
    pdf = tmp_path / "bad-bounds.pdf"
    with pdf.open("wb") as stream:
        writer.write(stream)
    geo_page = GeoPDFDocument(pdf).pages[0]
    ref = geo_page.viewports[0]
    assert ref.metadata["neatline_invalid"] is True
    assert geo_page.select_viewport(100, 100) is None


def test_document_summary_exposes_pdf_version_information(tmp_path):
    pdf = _make_iso_pdf(tmp_path / "versioned.pdf")
    summary = GeoPDFDocument(pdf).summary()
    assert summary["pdf_header"].startswith("%PDF-")
    assert "page_count" in summary


def test_single_ogc_frame_with_one_point_neatline_is_not_treated_as_full_page(tmp_path):
    writer = PdfWriter()
    page = writer.add_blank_page(width=400, height=300)
    frame = DictionaryObject({
        NameObject("/Type"): NameObject("/LGIDict"),
        NameObject("/Neatline"): _arr([0, 0]),  # one point is insufficient
    })
    page[NameObject("/LGIDict")] = frame
    pdf = tmp_path / "ogc-malformed-single-neatline.pdf"
    with pdf.open("wb") as stream:
        writer.write(stream)
    geo_page = GeoPDFDocument(pdf).pages[0]
    assert geo_page.viewports[0].metadata["neatline_invalid"] is True
    assert geo_page.select_viewport(200, 150) is None


def test_ogc_affine_fit_does_not_require_optional_pyproj(monkeypatch, tmp_path):
    from pdf2ovr import geospatial

    monkeypatch.setattr(geospatial, "_try_import_pyproj", lambda: None)
    path = _make_ogc_pdf(tmp_path / "ogc-no-pyproj.pdf")
    ref = GeoPDFDocument(path).pages[0].viewports[0]
    assert ref.transform is not None
    assert ref.crs == "EPSG:32631"  # explicit code retained, not claimed resolved
    assert ref.crs_resolved is False
    assert ref.map_point(0, 0) == pytest.approx((500000, 4600000))


def test_invalid_page_rotation_is_reported_without_aborting(tmp_path):
    writer = PdfWriter()
    page = writer.add_blank_page(width=400, height=300)
    page[NameObject("/Rotate")] = NumberObject(45)
    pdf = tmp_path / "invalid-rotation.pdf"
    with pdf.open("wb") as stream:
        writer.write(stream)
    result = GeoPDFDocument(pdf).pages[0]
    assert result.rotation == 0
    assert any("Invalid /Rotate" in warning for warning in result.warnings)


def test_classic_ogc_projection_is_retained_even_without_epsg_or_wkt(tmp_path):
    writer = PdfWriter()
    page = writer.add_blank_page(width=400, height=300)
    projection = DictionaryObject({
        NameObject("/Type"): NameObject("/Projection"),
        NameObject("/Projection"): NameObject("/UTM"),
        NameObject("/Zone"): NumberObject(31),
        NameObject("/Datum"): NameObject("/WGS84"),
    })
    frame = DictionaryObject({
        NameObject("/Type"): NameObject("/LGIDict"),
        NameObject("/Projection"): projection,
        NameObject("/Registration"): ArrayObject([
            _arr([0, 0, 500000, 4600000]),
            _arr([400, 0, 501000, 4600000]),
            _arr([400, 300, 501000, 4599000]),
            _arr([0, 300, 500000, 4599000]),
        ]),
    })
    page[NameObject("/LGIDict")] = frame
    pdf = tmp_path / "ogc-classic-projection.pdf"
    with pdf.open("wb") as stream:
        writer.write(stream)
    ref = GeoPDFDocument(pdf).pages[0].viewports[0]
    assert ref.crs is None
    assert ref.crs_resolved is False
    assert ref.transform is not None
    assert ref.map_point(400, 300) == pytest.approx((501000, 4599000))
    assert "projection_dictionary" in ref.metadata


def test_overlapping_ogc_map_frames_are_ambiguous(tmp_path):
    writer = PdfWriter()
    page = writer.add_blank_page(width=400, height=300)
    frame_a = DictionaryObject({
        NameObject("/Type"): NameObject("/LGIDict"),
        NameObject("/Neatline"): _arr([0, 0, 400, 0, 400, 300, 0, 300]),
    })
    frame_b = DictionaryObject({
        NameObject("/Type"): NameObject("/LGIDict"),
        NameObject("/Neatline"): _arr([50, 50, 350, 50, 350, 250, 50, 250]),
    })
    page[NameObject("/LGIDict")] = ArrayObject([frame_a, frame_b])
    pdf = tmp_path / "ogc-overlapping-frames.pdf"
    with pdf.open("wb") as stream:
        writer.write(stream)
    geo_page = GeoPDFDocument(pdf).pages[0]
    assert geo_page.select_viewport(100, 100) is None


def test_mixed_iso_and_ogc_georeferences_are_not_given_arbitrary_precedence(tmp_path):
    writer = PdfWriter()
    page = writer.add_blank_page(width=400, height=300)
    gcs = DictionaryObject({NameObject("/Type"): NameObject("/PROJCS"), NameObject("/EPSG"): NumberObject(32631)})
    measure = DictionaryObject({
        NameObject("/Subtype"): NameObject("/GEO"),
        NameObject("/GCS"): gcs,
        NameObject("/GPTS"): _arr([500000, 4600000, 501000, 4600000, 501000, 4599000]),
        NameObject("/LPTS"): _arr([0, 0, 1, 0, 1, 1]),
    })
    page[NameObject("/VP")] = ArrayObject([DictionaryObject({
        NameObject("/BBox"): _arr([0, 0, 400, 300]),
        NameObject("/Measure"): measure,
    })])
    page[NameObject("/LGIDict")] = DictionaryObject({
        NameObject("/Type"): NameObject("/LGIDict"),
        NameObject("/Neatline"): _arr([0, 0, 400, 0, 400, 300, 0, 300]),
    })
    pdf = tmp_path / "mixed-georeferencing.pdf"
    with pdf.open("wb") as stream:
        writer.write(stream)
    geo_page = GeoPDFDocument(pdf).pages[0]
    assert len(geo_page.viewports) == 2
    assert any("Both ISO /VP and OGC /LGIDict" in warning for warning in geo_page.warnings)
    assert geo_page.select_viewport(100, 100) is None


def test_malformed_calibration_json_is_rejected(tmp_path):
    from pdf2ovr.geospatial import load_affine_fit

    path = tmp_path / "bad-calibration.json"
    path.write_text('{"schema_version": 99}', encoding="utf-8")
    with pytest.raises(CalibrationError, match="schema version"):
        load_affine_fit(path)


def test_ogc_ctm_takes_precedence_over_registration(tmp_path):
    # This CTM deliberately disagrees with the four registration points. The
    # OGC 08-139r3 table says CTM has precedence when both are present.
    ctm = [5, 0, 0, -5, 510000, 4700000]
    path = _make_ogc_pdf(tmp_path / "ogc-ctm-priority.pdf", with_ctm=True, ctm_values=ctm)
    ref = GeoPDFDocument(path).pages[0].viewports[0]
    assert ref.map_point(0, 0) == pytest.approx((510000, 4700000))
    assert ref.transform.to_dict() == AffineTransform(*ctm).to_dict()
    assert ref.metadata["registration_fit"] is not None
    assert any("higher-precedence /CTM" in warning for warning in ref.warnings)


def test_ogc_two_point_neatline_is_interpreted_as_diagonal_rectangle(tmp_path):
    writer = PdfWriter()
    page = writer.add_blank_page(width=400, height=300)
    page[NameObject("/LGIDict")] = DictionaryObject({
        NameObject("/Type"): NameObject("/LGIDict"),
        NameObject("/Neatline"): _arr([50, 40, 350, 260]),
    })
    pdf = tmp_path / "ogc-two-point-neatline.pdf"
    with pdf.open("wb") as stream:
        writer.write(stream)
    geo_page = GeoPDFDocument(pdf).pages[0]
    ref = geo_page.viewports[0]
    assert ref.neatline_pdf == [(50, 40), (50, 260), (350, 260), (350, 40)]
    assert geo_page.select_viewport(200, 150) is ref
    assert geo_page.select_viewport(20, 20) is None
