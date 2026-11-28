from __future__ import annotations

from pathlib import Path

import pytest

from pdf2ovr.eant_reader import EANTDocument
from pdf2ovr.eant_writer import EANTWriteError, EANTWriter
from pdf2ovr.hfa_dictionary import HFADictionary, HFADictionaryError
from pdf2ovr.hfa_structure import HFAReader
from pdf2ovr.hfa_writer import HFAWriteError, HFAWriter
from tests.fixture_utils import classic_fixture, modern_fixture


def strip_internal(value):
    if isinstance(value, dict):
        return {key: strip_internal(item) for key, item in value.items() if not key.startswith("_")}
    if isinstance(value, list):
        return [strip_internal(item) for item in value]
    return value


@pytest.mark.parametrize("path", [classic_fixture(), modern_fixture()])
def test_all_typed_synthetic_entries_decode_and_roundtrip(path: Path):
    data = path.read_bytes()
    reader = HFAReader(data)
    dictionary = HFADictionary(reader.dictionary)
    decoded_count = 0

    for entry in reader.entries:
        if not entry.data_size or entry.entry_type not in dictionary.types:
            continue
        payload = data[entry.data_pointer:entry.data_end]
        decoded = dictionary.decode_instance(
            entry.entry_type, payload, absolute_offset=entry.data_pointer
        )
        encoded = dictionary.encode_instance(
            entry.entry_type, strip_internal(decoded), absolute_offset=entry.data_pointer
        )
        decoded_again = dictionary.decode_instance(
            entry.entry_type, encoded, absolute_offset=entry.data_pointer
        )
        assert strip_internal(decoded_again) == strip_internal(decoded), (
            path.name, entry.name, entry.entry_type
        )
        decoded_count += 1

    assert decoded_count == len(reader.entries)


def test_classic_synthetic_fixture_exercises_ellipse_schema():
    document = EANTDocument(classic_fixture())
    summary = document.summary()
    assert summary["hfa"]["entry_header_length"] == 128
    assert summary["decode_error_count"] == 0
    assert summary["top_level_annotation_count"] == 1
    assert summary["recursive_annotation_count"] == 1
    assert summary["annotation_type_counts"] == {"EANT_ELLIPSE": 1}
    ellipse = document.annotations[0]
    assert ellipse.element_schema == "Element_Eant"
    assert ellipse.geometry_entry_type == "Eant_Ellipse"
    assert ellipse.geometry["center_xy"] == (0.0, 0.0)


def test_modern_synthetic_fixture_exercises_text_rectangle_group_and_polyline():
    document = EANTDocument(modern_fixture())
    summary = document.summary()
    assert summary["hfa"]["entry_header_length"] == 128
    assert summary["decode_error_count"] == 0
    assert summary["top_level_annotation_count"] == 3
    assert summary["recursive_annotation_count"] == 4
    assert summary["annotation_type_counts"] == {
        "EANT_TEXT": 1,
        "EANT_RECTANGLE": 1,
        "EANT_GROUP": 1,
        "EANT_POLYLINE": 1,
    }
    assert summary["element_schema_counts"] == {"Element_2_Eant": 4}
    group = next(item for item in document.iter_annotations() if item.element_type == "EANT_GROUP")
    assert group.group_metadata["group_type"] == "EANT_LEGEND"
    assert group.group_metadata["child_entries"][0]["entry_type"] == "Legend4"
    text = next(item for item in document.iter_annotations() if item.element_type == "EANT_TEXT")
    textbox = text.geometry["child_entries"][0]
    assert textbox["entry_type"] == "TextBox_Eant"
    assert textbox["values"]["boxWidth"] == pytest.approx(70.0)


def test_eant_reader_decodes_polyline_points_as_xy_pairs():
    document = EANTDocument(modern_fixture())
    line = next(item for item in document.iter_annotations() if item.element_type == "EANT_POLYLINE")
    assert line.geometry_entry_type == "Polyline2"
    assert line.geometry["points"] == [(1.0, 2.0), (10.0, 20.0), (30.0, 25.0)]


def test_clone_classic_ellipse_annotation_and_preserve_original():
    path = classic_fixture()
    source = path.read_bytes()
    original = EANTDocument(source)
    template = original.annotations[0]
    result = EANTWriter(source).clone_simple_annotation(
        template.entry_offset, new_name="Synthetic ellipse clone"
    )
    updated = EANTDocument(result.data)
    cloned = next(item for item in updated.iter_annotations() if item.entry_offset == result.new_entry_offset)
    assert path.read_bytes() == source
    assert updated.summary()["recursive_annotation_count"] == 2
    assert cloned.name == "Synthetic ellipse clone"
    assert cloned.element_type == "EANT_ELLIPSE"
    assert cloned.geometry["center_xy"] == template.geometry["center_xy"]
    assert not updated.decode_errors


def test_clone_modern_text_annotation_and_preserve_parent_child_chain():
    source = modern_fixture().read_bytes()
    original = EANTDocument(source)
    template = next(item for item in original.iter_annotations() if item.element_type == "EANT_TEXT")
    result = EANTWriter(source).clone_simple_annotation(
        template.entry_offset, new_name="Synthetic text clone"
    )
    updated = EANTDocument(result.data)
    cloned = next(item for item in updated.iter_annotations() if item.entry_offset == result.new_entry_offset)
    assert updated.summary()["recursive_annotation_count"] == original.summary()["recursive_annotation_count"] + 1
    assert cloned.name == "Synthetic text clone"
    assert cloned.geometry["text_value"] == template.geometry["text_value"]
    assert cloned.geometry["origin_xy"] == template.geometry["origin_xy"]
    assert updated.hfa.entry_by_offset[result.new_entry_offset].parent_pointer == updated.element_list.offset
    assert not updated.decode_errors


def test_group_clone_is_rejected():
    document = EANTDocument(modern_fixture())
    group = next(item for item in document.iter_annotations() if item.element_type == "EANT_GROUP")
    with pytest.raises(EANTWriteError, match="Group cloning is not implemented"):
        EANTWriter(modern_fixture()).clone_simple_annotation(group.entry_offset, new_name="No group clone")


def test_hfa_writer_rejects_type_not_in_file_dictionary():
    writer = HFAWriter(classic_fixture().read_bytes())
    parent = writer.reader.find_entry("Annotation")
    with pytest.raises(HFAWriteError, match="absent from this file's dictionary"):
        writer.append_entry(
            name="Probe", entry_type="NotAnHFAType", parent_offset=parent.offset,
            payload=b"anything",
        )


def test_writer_rejects_long_or_non_ascii_entry_header_names():
    data = classic_fixture().read_bytes()
    writer = HFAWriter(data)
    ellipse = EANTDocument(data).annotations[0]
    source_entry = writer.reader.entry_by_offset[ellipse.entry_offset]
    source_child = writer.reader.children(source_entry)[0]
    payload = data[source_child.data_pointer:source_child.data_end]
    with pytest.raises(HFAWriteError, match="maximum"):
        writer.append_entry(
            name="N" * 64, entry_type=source_child.entry_type,
            parent_offset=source_entry.offset, payload=payload,
        )
    with pytest.raises(HFAWriteError, match="ASCII"):
        writer.append_entry(
            name="مرحبا", entry_type=source_child.entry_type,
            parent_offset=source_entry.offset, payload=payload,
        )


def test_clone_rejects_duplicate_annotation_id_and_non_latin1_name():
    data = modern_fixture().read_bytes()
    document = EANTDocument(data)
    template = next(item for item in document.iter_annotations() if item.element_type == "EANT_TEXT")
    with pytest.raises(EANTWriteError, match="already in use"):
        EANTWriter(data).clone_simple_annotation(
            template.entry_offset, new_name="Duplicate ID", new_id=template.element_id
        )
    with pytest.raises(EANTWriteError, match="single-byte text encoding"):
        EANTWriter(data).clone_simple_annotation(template.entry_offset, new_name="漢字")


def test_group_and_textbox_metadata_are_exposed_from_nested_eant_entries():
    document = EANTDocument(modern_fixture())
    group = next(item for item in document.iter_annotations() if item.element_type == "EANT_GROUP")
    assert group.group_metadata["group_type"] == "EANT_LEGEND"
    assert group.group_metadata["child_entries"][0]["entry_type"] == "Legend4"
    text = next(item for item in document.iter_annotations() if item.element_type == "EANT_TEXT")
    assert text.geometry["child_entries"][0]["entry_type"] == "TextBox_Eant"


def test_clone_modern_rectangle_annotation():
    source = modern_fixture().read_bytes()
    original = EANTDocument(source)
    rectangle = next(item for item in original.iter_annotations() if item.element_type == "EANT_RECTANGLE")
    result = EANTWriter(source).clone_simple_annotation(rectangle.entry_offset, new_name="Rectangle clone")
    updated = EANTDocument(result.data)
    clone = next(item for item in updated.iter_annotations() if item.entry_offset == result.new_entry_offset)
    assert clone.element_type == "EANT_RECTANGLE"
    assert clone.geometry["center_xy"] == rectangle.geometry["center_xy"]
    assert clone.geometry["width"] == rectangle.geometry["width"]
    assert clone.geometry["height"] == rectangle.geometry["height"]


def test_clone_preserves_all_original_entry_payloads_and_tree_metadata():
    source = modern_fixture().read_bytes()
    before = HFAReader(source)
    document = EANTDocument(source)
    annotation = next(item for item in document.iter_annotations() if item.element_type != "EANT_GROUP")
    result = EANTWriter(source).clone_simple_annotation(annotation.entry_offset, new_name="Preservation probe")
    after = HFAReader(result.data)
    assert after.header.version == before.header.version
    assert after.header.root_entry_pointer == before.header.root_entry_pointer
    assert after.header.entry_header_length == before.header.entry_header_length
    assert after.header.free_list_pointer == before.header.free_list_pointer
    assert after.dictionary == before.dictionary
    for old_entry in before.entries:
        new_entry = after.entry_by_offset[old_entry.offset]
        assert new_entry.name == old_entry.name
        assert new_entry.entry_type == old_entry.entry_type
        assert new_entry.parent_pointer == old_entry.parent_pointer
        assert new_entry.child_pointer == old_entry.child_pointer
        assert new_entry.previous_pointer == old_entry.previous_pointer
        if old_entry.data_size:
            assert source[old_entry.data_pointer:old_entry.data_end] == result.data[new_entry.data_pointer:new_entry.data_end]
