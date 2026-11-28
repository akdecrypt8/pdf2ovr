from __future__ import annotations

from pathlib import Path
import struct

from pdf2ovr.eant_reader import EANTDocument
from pdf2ovr.hfa_structure import HFAReader
from tests.fixture_utils import CLASSIC_FIXTURE, MODERN_FIXTURE
from tests.fixtures.build_synthetic_ovr import (
    _classic_tree,
    _modern_tree,
    build_ovr,
)


def test_committed_synthetic_fixtures_match_the_independent_builder():
    assert CLASSIC_FIXTURE.read_bytes() == build_ovr(_classic_tree())
    assert MODERN_FIXTURE.read_bytes() == build_ovr(_modern_tree())


def test_synthetic_fixture_headers_and_tree_metadata_are_meaningful():
    for path in (CLASSIC_FIXTURE, MODERN_FIXTURE):
        data = path.read_bytes()
        assert data.startswith(b"EHFA_HEADER_TAG\x00")
        reader = HFAReader(data)
        report = reader.validate()
        assert report["entry_count"] == len(reader.entries)
        assert reader.header.entry_header_length == 128
        assert reader.header.root_entry_pointer in reader.entry_by_offset
        assert reader.dictionary.endswith(b".\x00")


def test_synthetic_fixtures_exercise_classic_and_modern_eant_records():
    classic = EANTDocument(CLASSIC_FIXTURE)
    modern = EANTDocument(MODERN_FIXTURE)
    assert classic.decode_errors == []
    assert modern.decode_errors == []
    assert [item.geometry_entry_type for item in classic.iter_annotations()] == ["Eant_Ellipse"]
    assert {item.geometry_entry_type for item in modern.iter_annotations()} >= {
        "Text2", "Rectangle2", "Polyline2", None
    }
    assert modern.summary()["recursive_annotation_count"] == 4


def test_fixtures_are_small_and_checked_in():
    assert CLASSIC_FIXTURE.is_file() and CLASSIC_FIXTURE.stat().st_size < 10_000
    assert MODERN_FIXTURE.is_file() and MODERN_FIXTURE.stat().st_size < 15_000


def _independent_struct_walk(data: bytes):
    """Check HFA container/tree bytes without using HFAReader."""
    assert len(data) >= 18
    assert data[:16] == b"EHFA_HEADER_TAG\x00"
    header_offset = struct.unpack_from("<I", data, 16)[0]
    assert 0 < header_offset <= len(data) - 18
    version, free_list, root, entry_length, dictionary = struct.unpack_from(
        "<IIIHI", data, header_offset
    )
    assert version == 1
    assert entry_length == 128
    assert 0 < root < dictionary <= len(data)
    assert data[dictionary:].endswith(b".\x00")

    entries = {}
    headers = []
    data_blocks = []

    def walk(first: int, parent: int, depth: int = 0):
        assert depth < 20
        previous = 0
        current = first
        local_seen = set()
        while current:
            assert current not in local_seen
            assert current not in entries
            local_seen.add(current)
            assert current + entry_length <= dictionary
            next_ptr, previous_ptr, parent_ptr, child_ptr, data_ptr, data_size = (
                struct.unpack_from("<IIIIII", data, current)
            )
            assert previous_ptr == previous
            assert parent_ptr == parent
            name = data[current + 24:current + 88].split(b"\0", 1)[0].decode("ascii")
            entry_type = data[current + 88:current + 120].split(b"\0", 1)[0].decode("ascii")
            assert name and entry_type
            assert current + 124 <= current + entry_length
            headers.append((current, current + entry_length))
            if data_size:
                assert data_ptr > 0
                assert data_ptr + data_size <= dictionary
                data_blocks.append((data_ptr, data_ptr + data_size))
            entries[current] = (name, entry_type, parent_ptr, child_ptr, next_ptr)
            if child_ptr:
                walk(child_ptr, current, depth + 1)
            previous, current = current, next_ptr

    # The root is the only entry whose parent is null.
    next_ptr, previous_ptr, parent_ptr, child_ptr, data_ptr, data_size = struct.unpack_from(
        "<IIIIII", data, root
    )
    assert parent_ptr == 0 and previous_ptr == 0 and next_ptr == 0
    root_name = data[root + 24:root + 88].split(b"\0", 1)[0].decode("ascii")
    root_type = data[root + 88:root + 120].split(b"\0", 1)[0].decode("ascii")
    assert root_name and root_type
    headers.append((root, root + entry_length))
    entries[root] = (root_name, root_type, parent_ptr, child_ptr, next_ptr)
    if child_ptr:
        walk(child_ptr, root)

    sorted_headers = sorted(headers)
    assert all(left[1] <= right[0] for left, right in zip(sorted_headers, sorted_headers[1:]))
    sorted_data = sorted(data_blocks)
    assert all(left[1] <= right[0] for left, right in zip(sorted_data, sorted_data[1:]))
    for data_start, data_end in data_blocks:
        assert all(data_end <= start or end <= data_start for start, end in headers)
    return entries, dictionary, free_list


def test_independent_struct_walk_confirms_fixture_pointer_and_data_relationships():
    for path in (CLASSIC_FIXTURE, MODERN_FIXTURE):
        entries, dictionary, free_list = _independent_struct_walk(path.read_bytes())
        assert len(entries) == HFAReader(path.read_bytes()).validate()["entry_count"]
        assert dictionary < path.stat().st_size
        assert free_list == 0
        assert any(entry_type == "Element_Eant" for _name, entry_type, *_rest in entries.values()) if path == CLASSIC_FIXTURE else any(entry_type == "Element_2_Eant" for _name, entry_type, *_rest in entries.values())
