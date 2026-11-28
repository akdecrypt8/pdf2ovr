from __future__ import annotations

import struct

import pytest

from pdf2ovr.hfa_structure import HFAFormatError, HFAReader, HEADER_TAG


def make_minimal_hfa(
    *, data_pointer: int = 210, data_size: int = 10,
    free_list_pointer: int = 0, free_size: int = 16, free_next: int = 0,
) -> bytes:
    """Build a tiny structural fixture with a child data block independent of its header."""
    raw = bytearray(420)
    raw[:16] = HEADER_TAG
    struct.pack_into("<I", raw, 16, 64)
    struct.pack_into("<IIIHI", raw, 64, 1, free_list_pointer, 82, 128, 400)

    # Root entry at 82; its first child is at 240.
    struct.pack_into("<IIIIII", raw, 82, 0, 0, 0, 240, 0, 0)
    raw[82 + 24:82 + 24 + 5] = b"root\0"
    raw[82 + 88:82 + 88 + 5] = b"root\0"

    # Independent payload block in the gap between the root and child headers.
    raw[data_pointer:data_pointer + data_size] = b"0123456789"[:data_size]

    # Child entry at 240. Its data pointer is deliberately not header+128.
    struct.pack_into("<IIIIII", raw, 240, 0, 0, 82, 0, data_pointer, data_size)
    raw[240 + 24:240 + 24 + 6] = b"child\0"
    raw[240 + 88:240 + 88 + 5] = b"Test\0"
    if free_list_pointer:
        struct.pack_into("<II", raw, free_list_pointer, free_next, free_size)
    raw[400:] = b"{}\0"
    return bytes(raw)


def test_hfa_data_pointer_is_absolute_and_need_not_be_adjacent_to_entry_header():
    reader = HFAReader(make_minimal_hfa())
    summary = reader.validate()
    child = reader.find_entry("child")
    assert child is not None
    assert child.data_pointer == 210
    assert child.offset == 240
    assert summary["entry_count"] == 2


def test_hfa_reader_rejects_data_block_that_overlaps_an_entry_header():
    reader = HFAReader(make_minimal_hfa(data_pointer=250, data_size=10))
    with pytest.raises(HFAFormatError, match="overlaps"):
        reader.validate()


def make_nested_hfa(nested_count: int) -> bytes:
    dictionary_pointer = 210 + nested_count * 128
    raw = bytearray(dictionary_pointer + 3)
    raw[:16] = HEADER_TAG
    struct.pack_into("<I", raw, 16, 64)
    struct.pack_into("<IIIHI", raw, 64, 1, 0, 82, 128, dictionary_pointer)

    first_child = 210 if nested_count else 0
    struct.pack_into("<IIIIII", raw, 82, 0, 0, 0, first_child, 0, 0)
    raw[106:111] = b"root\0"
    raw[170:175] = b"root\0"

    for index in range(nested_count):
        offset = 210 + index * 128
        parent = 82 if index == 0 else offset - 128
        child = offset + 128 if index + 1 < nested_count else 0
        struct.pack_into("<IIIIII", raw, offset, 0, 0, parent, child, 0, 0)
        name = f"n{index}".encode("ascii") + b"\0"
        raw[offset + 24:offset + 24 + len(name)] = name
        raw[offset + 88:offset + 93] = b"Test\0"
    raw[dictionary_pointer:] = b"{}\0"
    return bytes(raw)


def test_hfa_reader_applies_a_configurable_nesting_limit():
    with pytest.raises(HFAFormatError, match="nesting exceeds"):
        HFAReader(make_nested_hfa(3), max_depth=2)


def test_hfa_reader_rejects_non_positive_safety_limits():
    with pytest.raises(ValueError, match="must be positive"):
        HFAReader(make_minimal_hfa(), max_depth=0)


def test_hfa_reader_rejects_truncated_file_header():
    with pytest.raises(HFAFormatError, match="too short"):
        HFAReader(b"EHFA_HEADER_TAG\x00")


def test_hfa_reader_rejects_header_pointer_outside_file():
    raw = bytearray(make_minimal_hfa())
    struct.pack_into("<I", raw, 16, len(raw) + 100)
    with pytest.raises(HFAFormatError, match="header pointer.*outside"):
        HFAReader(raw)


def test_hfa_reader_rejects_dictionary_pointer_outside_file():
    raw = bytearray(make_minimal_hfa())
    struct.pack_into("<I", raw, 64 + 14, len(raw) + 1)
    with pytest.raises(HFAFormatError, match="Dictionary pointer.*outside"):
        HFAReader(raw)


def test_hfa_reader_rejects_root_entry_pointer_before_dictionary():
    raw = bytearray(make_minimal_hfa())
    struct.pack_into("<I", raw, 64 + 8, len(raw) - 10)
    with pytest.raises(HFAFormatError, match="Root entry pointer.*outside"):
        HFAReader(raw)


def test_hfa_reader_follows_a_valid_free_list_block():
    reader = HFAReader(make_minimal_hfa(free_list_pointer=380, free_size=16))
    summary = reader.validate()
    assert summary["free_list_blocks"] == [
        {"offset": 380, "size": 16, "end": 396}
    ]


def test_hfa_reader_rejects_free_list_block_overlapping_entry_data():
    reader = HFAReader(make_minimal_hfa(free_list_pointer=210, free_size=16))
    with pytest.raises(HFAFormatError, match="overlaps data block"):
        reader.validate()


def test_hfa_reader_rejects_free_list_block_outside_dictionary_boundary():
    reader = HFAReader(make_minimal_hfa(free_list_pointer=380, free_size=32))
    with pytest.raises(HFAFormatError, match="invalid size|overlaps the dictionary"):
        reader.validate()

def test_hfa_reader_rejects_free_list_cycle():
    reader = HFAReader(make_minimal_hfa(free_list_pointer=380, free_size=16, free_next=380))
    with pytest.raises(HFAFormatError, match="Cycle in EHFA free-list"):
        reader.validate()
