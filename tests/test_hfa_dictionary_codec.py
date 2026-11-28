from __future__ import annotations

import struct

import pytest

from pdf2ovr.hfa_dictionary import HFADictionary, HFADictionaryError


def test_dictionary_parses_simple_inline_object_and_enum():
    dictionary = HFADictionary("{1:e2:RED,BLUE,color,1:oPoint,point,}Sample,{2:dxy,}Point,")
    value = {"color": "BLUE", "point": {"xy": [1.25, -2.5]}}
    encoded = dictionary.encode_instance("Sample", value, absolute_offset=100)
    decoded = dictionary.decode_instance("Sample", encoded, absolute_offset=100)
    assert decoded["color"] == {"code": 1, "label": "BLUE"}
    assert decoded["point"]["xy"] == [1.25, -2.5]


def test_pointer_string_uses_absolute_inline_offset():
    dictionary = HFADictionary("{1:pcvalue,}StringValue,")
    encoded = dictionary.encode_instance("StringValue", {"value": "hello"}, absolute_offset=500)
    count, target = struct.unpack_from("<II", encoded, 0)
    assert count == 6
    assert target == 508
    decoded = dictionary.decode_instance("StringValue", encoded, absolute_offset=500)
    assert decoded["value"] == "hello"
    assert decoded["_value_pointer"]["target_matches_inline_data"] is True


def test_packed_basedata_roundtrips_raw_bytes_without_guessing_bit_order():
    dictionary = HFADictionary("{1:*bvalues,}Packed,")
    source = {
        "values": {
            "rows": 8,
            "columns": 1,
            "item_type": "u1",
            "object_type": 0,
            "raw": b"\xA5",
            "values": None,
        }
    }
    encoded = dictionary.encode_instance("Packed", source, absolute_offset=1000)
    decoded = dictionary.decode_instance("Packed", encoded, absolute_offset=1000)
    assert decoded["values"]["item_type"] == "u1"
    assert decoded["values"]["values"] is None
    assert decoded["values"]["raw"] == b"\xA5"


def test_encoder_rejects_unknown_enum_label():
    dictionary = HFADictionary("{1:e2:RED,BLUE,color,}Sample,")
    with pytest.raises(HFADictionaryError, match="Unknown enum label"):
        dictionary.encode_instance("Sample", {"color": "GREEN"})


def test_decoder_rejects_truncated_pointer_header():
    dictionary = HFADictionary("{1:pcvalue,}StringValue,")
    with pytest.raises(HFADictionaryError, match="Pointer header"):
        dictionary.decode_instance("StringValue", b"\x00\x00\x00")
