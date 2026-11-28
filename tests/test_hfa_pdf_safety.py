from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from pdf2ovr.hfa_structure import HFAFormatError, HFAReader
from pdf2ovr.pdf_extractor import PDFExtractor, ScaleBarBuilder
from tests.fixture_utils import classic_fixture, modern_fixture


def make_pdf(path: Path, *, pages: int = 1, rotation: int = 0, blank: bool = False) -> None:
    doc = pymupdf.open()
    for page_index in range(pages):
        page = doc.new_page(width=240, height=320)
        if not blank:
            page.insert_text((30, 45), f"Page label {page_index}", fontsize=12)
            page.draw_rect(pymupdf.Rect(20, 70, 100, 120), color=(1, 0, 0))
            page.draw_quad(pymupdf.Quad(
                pymupdf.Point(110, 70), pymupdf.Point(170, 70),
                pymupdf.Point(110, 120), pymupdf.Point(170, 120),
            ))
            page.draw_bezier(
                pymupdf.Point(20, 150), pymupdf.Point(70, 110),
                pymupdf.Point(130, 200), pymupdf.Point(190, 150),
            )
            page.draw_polyline(
                [pymupdf.Point(20, 220), pymupdf.Point(100, 220),
                 pymupdf.Point(100, 280), pymupdf.Point(20, 220)],
                closePath=True,
            )
        if rotation:
            page.set_rotation(rotation)
    doc.save(path)
    doc.close()


def test_hfa_reader_parses_independently_generated_synthetic_fixtures():
    expected = {
        classic_fixture(): (4, 128),
        modern_fixture(): (12, 128),
    }
    for path, (count, header_length) in expected.items():
        reader = HFAReader(path.read_bytes())
        summary = reader.validate()
        assert summary["version"] == 1
        assert summary["entry_header_length"] == header_length
        assert summary["entry_count"] == count
        assert summary["dictionary_size"] > 0


def test_hfa_reader_rejects_bad_signature():
    with pytest.raises(HFAFormatError, match="signature"):
        HFAReader(b"not an HFA file" + b"\x00" * 100)


def test_hfa_reader_rejects_out_of_bounds_header_pointer():
    data = bytearray(128)
    data[:16] = b"EHFA_HEADER_TAG\x00"
    data[16:20] = (1000).to_bytes(4, "little")
    with pytest.raises(HFAFormatError, match="outside the file"):
        HFAReader(data)


def test_extracts_quadrilateral_curve_and_closed_polyline(tmp_path: Path):
    pdf_path = tmp_path / "geometry.pdf"
    make_pdf(pdf_path)
    extractor = PDFExtractor(str(pdf_path))
    try:
        extracted = extractor.extract_all()
    finally:
        extractor.close()
    polyline_items = [item for item in extracted if item.elem_type == 5]
    assert polyline_items
    assert any(len(item.element.points) == 5 for item in polyline_items)
    assert any(len(item.element.points) > 4 for item in polyline_items)
    assert any(item.closed for item in polyline_items)
    assert any(item.elem_type == 6 for item in extracted)


def test_rotated_page_coordinates_stay_inside_displayed_page(tmp_path: Path):
    pdf_path = tmp_path / "rotated.pdf"
    make_pdf(pdf_path, rotation=90)
    extractor = PDFExtractor(str(pdf_path))
    try:
        page = extractor.doc[0]
        width, height = page.rect.width, page.rect.height
        extracted = extractor.extract_all()
    finally:
        extractor.close()
    assert width == pytest.approx(320)
    assert height == pytest.approx(240)
    for item in extracted:
        x0, y0, x1, y1 = item.bbox
        assert -1 <= x0 <= width + 1
        assert -1 <= x1 <= width + 1
        assert -1 <= y0 <= height + 1
        assert -1 <= y1 <= height + 1


def test_multipage_extraction_keeps_page_identity(tmp_path: Path):
    pdf_path = tmp_path / "two-pages.pdf"
    make_pdf(pdf_path, pages=2)
    extractor = PDFExtractor(str(pdf_path))
    try:
        elements = extractor.extract_all()
    finally:
        extractor.close()
    assert {element.page_num for element in elements} == {0, 1}


def test_scalebar_builder_no_longer_raises_name_error():
    built = ScaleBarBuilder.build_scale_bar(10, 20, 80)
    assert len(built) == 7


def test_legacy_ehfa_engine_uses_pointer_based_reader():
    from pdf2ovr.ehfa_engine import EHFAFile

    fixture = modern_fixture()
    parsed = EHFAFile(fixture.read_bytes())
    summary = HFAReader(fixture.read_bytes()).validate()
    assert parsed.version == 1
    assert parsed.root_ptr == 82
    assert parsed.header_len == 128
    assert parsed.dict_ptr == summary["dictionary_pointer"]
    assert len(parsed.entries) == 12
    with pytest.raises(NotImplementedError, match="disabled"):
        parsed.rebuild()


def test_element_factory_writer_is_disabled():
    from pdf2ovr.element_types import ElementFactory, TextElement

    with pytest.raises(NotImplementedError, match="EANT serialization is disabled"):
        ElementFactory.create_text(TextElement(1, 2, 0, 10, text="example"))


def test_pdf_extractor_reports_missing_backend_cleanly(monkeypatch, tmp_path: Path):
    import pdf2ovr.pdf_extractor as extractor_module

    monkeypatch.setattr(extractor_module, "pymupdf", None)
    with pytest.raises(RuntimeError, match="PyMuPDF is required"):
        extractor_module.PDFExtractor(str(tmp_path / "not-opened.pdf"))


def test_factory_and_string_writers_fail_closed():
    from pdf2ovr.element_types import StringField

    with pytest.raises(NotImplementedError, match="StringField serialization is disabled"):
        StringField.write("مرحبا")


def test_segment_joining_handles_large_reversed_chain_linearly():
    from pdf2ovr.element_types import Point
    from pdf2ovr.pdf_extractor import PDFExtractor as Extractor

    count = 10000
    segments = [
        (Point(float(i + 1), 0.0), Point(float(i), 0.0))
        for i in range(count)
    ]
    paths = Extractor._join_segments(segments)
    assert len(paths) == 1
    points, closed = paths[0]
    assert not closed
    assert len(points) == count + 1
    assert points[0].x == count
    assert points[-1].x == 0


def test_legacy_payload_parsers_are_disabled_until_schema_verified():
    from pdf2ovr.element_types import ElementParser

    with pytest.raises(NotImplementedError, match="Legacy EANT payload parsing is disabled"):
        ElementParser.parse_text(b"\x00" * 64)
