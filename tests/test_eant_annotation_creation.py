from __future__ import annotations

import json
from pathlib import Path

import pytest

from pdf2ovr.cli import main
from pdf2ovr.eant_reader import EANTDocument
from pdf2ovr.eant_writer import EANTWriteError, EANTWriter
from tests.fixture_utils import classic_fixture, modern_fixture


BBOX = [(10.0, 10.0), (10.0, 30.0), (50.0, 30.0), (50.0, 10.0)]


def annotation_at(data: bytes, offset: int):
    document = EANTDocument(data)
    annotation = next((item for item in document.iter_annotations() if item.entry_offset == offset), None)
    assert annotation is not None
    assert not document.decode_errors
    return document, annotation


def test_create_classic_ellipse_uses_observed_parent_child_schema():
    source_path = classic_fixture()
    original_bytes = source_path.read_bytes()
    original = EANTDocument(original_bytes)
    template = original.annotations[0]

    result = EANTWriter(original_bytes).create_ellipse_annotation(
        template.entry_offset,
        new_name="Phase 5 ellipse",
        center=(150.0, -250.0),
        semi_major_axis=12.5,
        semi_minor_axis=8.25,
        orientation=0.5,
        bbox=[(137.5, -258.25), (137.5, -241.75), (162.5, -241.75), (162.5, -258.25)],
    )
    updated, created = annotation_at(result.data, result.new_entry_offset)

    assert source_path.read_bytes() == original_bytes
    assert created.element_schema == "Element_Eant"
    assert updated.hfa.entry_by_offset[result.new_entry_offset].name == "AntElement"
    assert created.geometry_entry_type == "Eant_Ellipse"
    assert created.name == "Phase 5 ellipse"
    assert created.element_id == max(a.element_id for a in original.iter_annotations()) + 1
    assert created.geometry["center_xy"] == (150.0, -250.0)
    assert created.geometry["semiMajorAxis"] == 12.5
    assert created.geometry["semiMinorAxis"] == 8.25
    parent = updated.decoded[result.new_entry_offset]
    assert parent["bBox"]["values"] == [v for point in [(137.5, -258.25), (137.5, -241.75), (162.5, -241.75), (162.5, -258.25)] for v in point]


def test_create_text_preserves_textbox_and_style_reference():
    source = modern_fixture().read_bytes()
    original = EANTDocument(source)
    template = next(a for a in original.iter_annotations() if a.element_type == "EANT_TEXT")
    source_parent = original.decoded[template.entry_offset]
    source_geometry_entry = original.hfa.entry_by_offset[template.entry_offset]
    source_text_entry = next(c for c in original.hfa.children(source_geometry_entry) if c.entry_type == "Text2")
    source_text = original.decoded[source_text_entry.offset]

    result = EANTWriter(source).create_text_annotation(
        template.entry_offset,
        new_name="Phase 5 label",
        description="Newly created test label",
        text="Road junction 42",
        origin=(123.0, 456.0),
        orientation=0.25,
        scale=18.0,
        horizontal_alignment="EEVG_XCENTER",
        vertical_alignment="EEVG_YCENTER",
        text_box_width=88.0,
        bbox=BBOX,
    )
    updated, created = annotation_at(result.data, result.new_entry_offset)
    parent_entry = updated.hfa.entry_by_offset[result.new_entry_offset]
    geometry = next(c for c in updated.hfa.children(parent_entry) if c.entry_type == "Text2")
    geometry_values = updated.decoded[geometry.offset]
    textbox = next(c for c in updated.hfa.children(geometry) if c.entry_type == "TextBox_Eant")

    assert created.element_type == "EANT_TEXT"
    assert updated.hfa.entry_by_offset[result.new_entry_offset].name == f"AntElement_{created.element_id}"
    assert created.geometry["text_value"] == "Road junction 42"
    assert created.geometry["origin_xy"] == (123.0, 456.0)
    assert geometry_values["textStyle"] == source_text["textStyle"]
    assert geometry_values["orientation"] == pytest.approx(0.25)
    assert geometry_values["scale"] == pytest.approx(18.0)
    assert geometry_values["alignment"]["xalign"]["label"] == "EEVG_XCENTER"
    assert geometry_values["alignment"]["yalign"]["label"] == "EEVG_YCENTER"
    assert updated.decoded[textbox.offset]["boxWidth"] == pytest.approx(88.0)
    assert EANTWriter._clean(updated.decoded[result.new_entry_offset]["xformMatrix"]) == EANTWriter._clean(source_parent["xformMatrix"])


def test_create_rectangle_preserves_fill_style_and_updates_geometry():
    source = modern_fixture().read_bytes()
    document = EANTDocument(source)
    template = next(a for a in document.iter_annotations() if a.element_type == "EANT_RECTANGLE")
    template_entry = document.hfa.entry_by_offset[template.entry_offset]
    source_geometry = next(c for c in document.hfa.children(template_entry) if c.entry_type == "Rectangle2")
    source_fill_style = document.decoded[source_geometry.offset]["fillStyle"]

    result = EANTWriter(source).create_rectangle_annotation(
        template.entry_offset,
        new_name="New rectangle",
        center=(20.0, 30.0), width=12.0, height=6.0,
        orientation=0.1,
        bbox=[(14.0, 27.0), (14.0, 33.0), (26.0, 33.0), (26.0, 27.0)],
    )
    updated, created = annotation_at(result.data, result.new_entry_offset)
    geometry = updated.decoded[result.new_geometry_offset]

    assert created.geometry_entry_type == "Rectangle2"
    assert created.geometry["center_xy"] == (20.0, 30.0)
    assert geometry["width"] == pytest.approx(12.0)
    assert geometry["height"] == pytest.approx(6.0)
    assert geometry["orientation"] == pytest.approx(0.1)
    assert geometry["fillStyle"] == source_fill_style


def test_create_polyline_encodes_xy_vertex_pairs_and_keeps_line_style():
    source = modern_fixture().read_bytes()
    document = EANTDocument(source)
    template = next(a for a in document.iter_annotations() if a.element_type == "EANT_POLYLINE")
    template_entry = document.hfa.entry_by_offset[template.entry_offset]
    source_geometry = next(c for c in document.hfa.children(template_entry) if c.entry_type == "Polyline2")
    source_line_style = document.decoded[source_geometry.offset]["lineStyle"]
    points = [(1.0, 2.0), (3.0, 4.0), (5.0, 1.5), (8.0, 6.0)]

    result = EANTWriter(source).create_polyline_annotation(
        template.entry_offset,
        new_name="New polyline",
        points=points,
        bbox=[(1.0, 1.5), (1.0, 6.0), (8.0, 6.0), (8.0, 1.5)],
    )
    updated, created = annotation_at(result.data, result.new_entry_offset)
    geometry = updated.decoded[result.new_geometry_offset]

    assert created.geometry["points"] == points
    assert geometry["lineStyle"] == source_line_style
    assert geometry["coords"]["coords"]["rows"] == 2
    assert geometry["coords"]["coords"]["columns"] == len(points)


@pytest.mark.parametrize("bbox", [None, [], [(1, 1)] * 4, [(float("nan"), 0), (0, 1), (1, 1), (1, 0)]])
def test_annotation_creation_rejects_invalid_bounds(bbox):
    source = modern_fixture().read_bytes()
    document = EANTDocument(source)
    template = next(a for a in document.iter_annotations() if a.element_type == "EANT_RECTANGLE")
    with pytest.raises(EANTWriteError):
        EANTWriter(source).create_rectangle_annotation(
            template.entry_offset, new_name="Bad bbox", center=(2, 2), width=1, height=1, bbox=bbox
        )



@pytest.mark.parametrize("bad_number", [True, "12.5", float("nan"), float("inf")])
def test_annotation_creation_rejects_non_numeric_or_non_finite_dimensions(bad_number):
    source = modern_fixture().read_bytes()
    document = EANTDocument(source)
    template = next(a for a in document.iter_annotations() if a.element_type == "EANT_RECTANGLE")
    with pytest.raises(EANTWriteError):
        EANTWriter(source).create_rectangle_annotation(
            template.entry_offset, new_name="Invalid dimensions", center=(1, 2),
            width=bad_number, height=2, bbox=BBOX,
        )


def test_annotation_creation_rejects_boolean_annotation_id():
    source = modern_fixture().read_bytes()
    document = EANTDocument(source)
    template = next(a for a in document.iter_annotations() if a.element_type == "EANT_RECTANGLE")
    with pytest.raises(EANTWriteError, match="new_id must be an integer"):
        EANTWriter(source).create_rectangle_annotation(
            template.entry_offset, new_name="Invalid ID", center=(1, 2),
            width=2, height=3, bbox=BBOX, new_id=True,
        )

def test_annotation_creation_rejects_wrong_template_type():
    source = modern_fixture().read_bytes()
    document = EANTDocument(source)
    text = next(a for a in document.iter_annotations() if a.element_type == "EANT_TEXT")
    with pytest.raises(EANTWriteError, match="Template profile must be Element_2_Eant / EANT_RECTANGLE"):
        EANTWriter(source).create_rectangle_annotation(
            text.entry_offset, new_name="Wrong template", center=(0, 0), width=2, height=2, bbox=BBOX
        )


def test_text_creation_rejects_unrepresentable_unicode_without_replacement():
    source = modern_fixture().read_bytes()
    document = EANTDocument(source)
    template = next(a for a in document.iter_annotations() if a.element_type == "EANT_TEXT")
    with pytest.raises(EANTWriteError, match="Latin-1"):
        EANTWriter(source).create_text_annotation(
            template.entry_offset, new_name="Arabic", text="مرحبا", origin=(1, 2), bbox=BBOX
        )


def test_add_cli_creates_and_reopens_annotation(tmp_path: Path, capsys):
    source_path = modern_fixture()
    source = EANTDocument(source_path)
    template = next(a for a in source.iter_annotations() if a.element_type == "EANT_TEXT")
    spec_path = tmp_path / "add.json"
    spec_path.write_text(json.dumps({
        "kind": "text",
        "template_entry_offset": template.entry_offset,
        "name": "CLI-created label",
        "description": "CLI description",
        "bbox": BBOX,
        "geometry": {"text": "Added with CLI", "origin": [22.0, 33.0]},
    }), encoding="utf-8")
    output = tmp_path / "created.ovr"

    with pytest.raises(SystemExit) as exit_info:
        main(["add", str(source_path), "--spec", str(spec_path), "--output", str(output)])
    assert exit_info.value.code == 0
    assert output.is_file()
    updated = EANTDocument(output)
    assert any(a.name == "CLI-created label" and a.geometry.get("text_value") == "Added with CLI" for a in updated.iter_annotations())
    created_parent = next(a for a in updated.iter_annotations() if a.name == "CLI-created label")
    assert updated.decoded[created_parent.entry_offset]["description"] == "CLI description"
    assert updated.decode_errors == []
    assert "not certified" in capsys.readouterr().out


def test_add_cli_refuses_to_overwrite_source(tmp_path: Path):
    source_path = classic_fixture()
    source = EANTDocument(source_path)
    template = source.annotations[0]
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps({
        "kind": "ellipse", "template_entry_offset": template.entry_offset,
        "name": "Ellipse", "bbox": BBOX,
        "geometry": {"center": [2, 2], "semi_major_axis": 2, "semi_minor_axis": 1},
    }), encoding="utf-8")
    with pytest.raises(SystemExit) as exit_info:
        main(["add", str(source_path), "--spec", str(spec_path), "--output", str(source_path)])
    assert exit_info.value.code == 2


def test_new_annotation_is_attached_to_same_group_parent_as_its_template():
    source = modern_fixture().read_bytes()
    document = EANTDocument(source)
    template = next(a for a in document.iter_annotations() if a.element_type == "EANT_POLYLINE")
    source_entry = document.hfa.entry_by_offset[template.entry_offset]
    assert source_entry.parent_pointer != document.element_list.offset

    result = EANTWriter(source).create_polyline_annotation(
        template.entry_offset,
        new_name="Group sibling line",
        points=[(1, 1), (2, 2)],
        bbox=[(1, 1), (1, 2), (2, 2), (2, 1)],
    )
    updated = EANTDocument(result.data)
    created_entry = updated.hfa.entry_by_offset[result.new_entry_offset]
    assert created_entry.parent_pointer == source_entry.parent_pointer
    assert any(a.entry_offset == result.new_entry_offset for a in updated.iter_annotations())
