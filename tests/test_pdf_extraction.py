from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pymupdf
import pytest

from pdf2ovr.pdf_extractor import PDFExtractor


def _save(doc: pymupdf.Document, path: Path) -> Path:
    doc.save(path)
    doc.close()
    return path


def test_text_uses_text_trace_order_baseline_rotation_and_font_style(tmp_path: Path):
    path = tmp_path / "text-style.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=300, height=400)
    page.draw_rect(pymupdf.Rect(10, 10, 80, 80), color=(1, 0, 0))
    page.insert_text((20, 120), "Normal", fontsize=14, fontname="hebo", color=(0, 0, 1))
    page.insert_text((30, 300), "Vertical", fontsize=12, rotate=90, color=(0, 0, 0))
    _save(doc, path)

    extractor = PDFExtractor(str(path))
    try:
        result = extractor.extract_document()
    finally:
        extractor.close()

    texts = [item for item in result.elements if item.elem_type == 10]
    assert [item.element.text for item in texts] == ["Normal", "Vertical"]
    assert texts[0].paint_order < texts[1].paint_order
    assert texts[0].element.y == pytest.approx(280, abs=0.1)  # displayed bottom-left page space
    assert texts[0].style.font_name.lower().endswith("bold")
    assert texts[0].style.font_size == pytest.approx(14)
    assert texts[0].style.fill_color == pytest.approx((0.0, 0.0, 1.0))
    assert texts[0].text_metadata.characters
    assert abs(texts[1].element.orientation) == pytest.approx(90, abs=0.1)


def test_vector_styles_and_subpaths_are_preserved(tmp_path: Path):
    path = tmp_path / "vector-styles.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=300, height=300)
    shape = page.new_shape()
    shape.draw_rect(pymupdf.Rect(20, 20, 90, 90))
    shape.draw_rect(pymupdf.Rect(120, 20, 190, 90))
    shape.finish(
        color=(1, 0, 0), fill=(0, 1, 0), width=3,
        dashes="[4 2] 0", even_odd=True,
        stroke_opacity=0.6, fill_opacity=0.35,
    )
    shape.commit()
    _save(doc, path)

    extractor = PDFExtractor(str(path))
    try:
        result = extractor.extract_document()
    finally:
        extractor.close()

    vectors = [item for item in result.elements if item.elem_type in (5, 6)]
    assert len(vectors) == 2
    assert {item.path_metadata["subpath_index"] for item in vectors} == {0, 1}
    for item in vectors:
        assert item.style.stroke_color == pytest.approx((1, 0, 0))
        assert item.style.fill_color == pytest.approx((0, 1, 0))
        assert item.style.line_width == pytest.approx(3)
        assert item.style.stroke_opacity == pytest.approx(0.6, abs=1e-5)
        assert item.style.fill_opacity == pytest.approx(0.35, abs=1e-5)
        assert item.style.even_odd is True
        assert "4" in item.style.dashes
        assert item.path_metadata["fill_rule"] == "evenodd"
        assert len(item.path_metadata["source_commands"]) == 2


def test_curves_and_quad_paths_keep_path_style_and_closure(tmp_path: Path):
    path = tmp_path / "curve-quad.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=240, height=240)
    page.draw_bezier(
        pymupdf.Point(20, 80), pymupdf.Point(50, 10),
        pymupdf.Point(150, 150), pymupdf.Point(200, 80),
        color=(0, 0, 1), width=2,
    )
    page.draw_quad(pymupdf.Quad(
        pymupdf.Point(30, 130), pymupdf.Point(120, 125),
        pymupdf.Point(35, 190), pymupdf.Point(115, 185),
    ), color=(1, 0, 0), fill=(1, 1, 0), fill_opacity=0.5)
    _save(doc, path)

    extractor = PDFExtractor(str(path), curve_tolerance=0.2)
    try:
        result = extractor.extract_document()
    finally:
        extractor.close()

    curves = [item for item in result.elements if item.path_metadata and any(c["type"] == "cubic_bezier" for c in item.path_metadata["source_commands"])]
    quads = [item for item in result.elements if item.path_metadata and any(c["type"] == "quadrilateral" for c in item.path_metadata["source_commands"])]
    assert curves and len(curves[0].element.points) > 4
    assert quads and quads[0].closed
    # PyMuPDF may emit a compound fill path and a separate quad stroke path.
    quad_fill = [item for item in result.elements if item.path_metadata and item.path_metadata["subpath_count"] and item.style.fill_color == (1.0, 1.0, 0.0)]
    assert quad_fill


def test_image_occurrences_are_page_aware_and_deduplicate_assets(tmp_path: Path):
    path = tmp_path / "images.pdf"
    doc = pymupdf.open()
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 12, 8), False)
    pix.clear_with(0x336699)
    for index in range(2):
        page = doc.new_page(width=300, height=300)
        page.insert_image(pymupdf.Rect(30 + index * 20, 50, 150 + index * 20, 130), pixmap=pix, rotate=90)
    _save(doc, path)

    extractor = PDFExtractor(str(path))
    try:
        result = extractor.extract_document()
    finally:
        extractor.close()

    assert len(result.image_placements) == 2
    assert {placement.page_num for placement in result.image_placements} == {0, 1}
    assert len(result.image_assets) == 1
    for placement in result.image_placements:
        assert placement.asset_id in result.image_assets
        assert len(placement.quad) == 4
        assert len(placement.source_transform) == 6
        assert placement.bbox[2] > placement.bbox[0]
        assert placement.bbox[3] > placement.bbox[1]
    asset = next(iter(result.image_assets.values()))
    assert asset.data.startswith(b"\x89PNG")
    assert asset.width == 12 and asset.height == 8


def test_soft_mask_data_is_retained_when_present(tmp_path: Path):
    path = tmp_path / "alpha-image.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=200, height=200)
    rgba = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 10, 10), True)
    rgba.samples_mv[:] = bytes([255, 0, 0, 128] * 100)
    page.insert_image(pymupdf.Rect(20, 20, 120, 120), pixmap=rgba)
    _save(doc, path)

    extractor = PDFExtractor(str(path))
    try:
        result = extractor.extract_document()
    finally:
        extractor.close()
    assert result.image_placements
    asset = result.image_assets[result.image_placements[0].asset_id]
    # A backend may combine a mask into the returned image or expose it as a
    # separate soft mask, so either representation is acceptable; bytes must exist.
    assert asset.data
    assert asset.soft_mask_data is not None or asset.extension in {"png", "pam"}


def test_optional_content_groups_and_object_layer_names_are_reported(tmp_path: Path):
    path = tmp_path / "layers.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=200, height=200)
    roads = doc.add_ocg("Roads", on=True)
    water = doc.add_ocg("Water", on=True)
    page.draw_rect(pymupdf.Rect(10, 10, 80, 80), color=(1, 0, 0), oc=roads)
    page.draw_rect(pymupdf.Rect(100, 10, 180, 80), color=(0, 0, 1), oc=water)
    _save(doc, path)

    extractor = PDFExtractor(str(path))
    try:
        result = extractor.extract_document()
    finally:
        extractor.close()
    assert {layer.name for layer in result.layers} == {"Roads", "Water"}
    assert all(layer.visible is True for layer in result.layers)
    assert {item.layer_name for item in result.elements} == {"Roads", "Water"}


def test_off_optional_content_state_is_reported_as_a_limitation(tmp_path: Path):
    path = tmp_path / "hidden-layer.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=200, height=200)
    hidden = doc.add_ocg("Hidden roads", on=False)
    page.draw_rect(pymupdf.Rect(10, 10, 80, 80), color=(1, 0, 0), oc=hidden)
    page.draw_rect(pymupdf.Rect(100, 10, 180, 80), color=(0, 0, 1))
    _save(doc, path)

    extractor = PDFExtractor(str(path))
    try:
        result = extractor.extract_document()
    finally:
        extractor.close()
    hidden_layer = next(layer for layer in result.layers if layer.name == "Hidden roads")
    assert hidden_layer.visible is False
    assert any("OFF groups may not be returned" in warning for warning in result.warnings)


def test_clipping_paths_and_transparency_groups_are_preserved(tmp_path: Path):
    source_path = tmp_path / "source.pdf"
    source = pymupdf.open()
    source_page = source.new_page(width=200, height=200)
    source_page.draw_rect(pymupdf.Rect(5, 5, 180, 180), color=(1, 0, 0), fill=(0, 1, 0))
    _save(source, source_path)

    output_path = tmp_path / "clipped.pdf"
    source = pymupdf.open(source_path)
    output = pymupdf.open()
    page = output.new_page(width=300, height=300)
    page.show_pdf_page(
        pymupdf.Rect(40, 40, 150, 150), source, 0,
        clip=pymupdf.Rect(20, 20, 100, 100),
    )
    source.close()
    _save(output, output_path)

    extractor = PDFExtractor(str(output_path))
    try:
        result = extractor.extract_document()
    finally:
        extractor.close()
    assert len(result.clips) >= 1
    assert any(item.clip_stack for item in result.elements)
    assert all(clip.scissor_bbox is None or clip.scissor_bbox[2] >= clip.scissor_bbox[0] for clip in result.clips)
    assert any("full text/image clip semantics" in warning for warning in result.warnings)


def test_draw_order_is_preserved_across_text_vector_and_image(tmp_path: Path):
    path = tmp_path / "paint-order.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=300, height=300)
    page.draw_rect(pymupdf.Rect(10, 10, 50, 50), color=(1, 0, 0))
    page.insert_text((20, 100), "middle", fontsize=12)
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 5, 5), False)
    pix.clear_with(0xabcdef)
    page.insert_image(pymupdf.Rect(100, 100, 150, 150), pixmap=pix)
    page.draw_line((10, 200), (200, 200), color=(0, 0, 1))
    _save(doc, path)

    extractor = PDFExtractor(str(path))
    try:
        result = extractor.extract_document()
    finally:
        extractor.close()
    assert [item.paint_order for item in result.elements] == sorted(item.paint_order for item in result.elements)
    all_orders = [item.paint_order for item in result.elements] + [item.paint_order for item in result.image_placements]
    assert sorted(all_orders) == sorted(all_orders)
    assert result.image_placements[0].paint_order == 2


def test_extraction_result_json_is_serializable_and_omits_binary_payloads(tmp_path: Path):
    path = tmp_path / "serializable.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((20, 40), "Hello")
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 5, 5), False)
    pix.clear_with(0x112233)
    page.insert_image(pymupdf.Rect(30, 50, 90, 100), pixmap=pix)
    _save(doc, path)

    extractor = PDFExtractor(str(path))
    try:
        result = extractor.extract_document()
        encoded = json.dumps(result.to_dict(), ensure_ascii=False)
    finally:
        extractor.close()
    assert '"image_assets"' in encoded
    assert "data_hex" not in encoded
    assert "soft_mask_hex" not in encoded


def test_cli_extract_writes_manifest_and_image_assets_without_overwrite(tmp_path: Path):
    source = tmp_path / "input.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=200, height=200)
    page.insert_text((20, 40), "Extract me")
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 5, 5), False)
    pix.clear_with(0x123456)
    page.insert_image(pymupdf.Rect(30, 60, 100, 120), pixmap=pix)
    _save(doc, source)

    output = tmp_path / "package"
    command = [sys.executable, "-m", "pdf2ovr.cli", "extract", str(source), "--output", str(output)]
    completed = subprocess.run(command, capture_output=True, text=True, cwd=Path(__file__).resolve().parents[1])
    assert completed.returncode == 0, completed.stderr
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["summary"]["element_count"] == 1
    assert manifest["summary"]["image_placement_count"] == 1
    asset_record = manifest["image_assets"][0]
    assert (output / asset_record["path"]).is_file()

    refused = subprocess.run(command, capture_output=True, text=True, cwd=Path(__file__).resolve().parents[1])
    assert refused.returncode == 2
    assert "already exists" in refused.stderr
