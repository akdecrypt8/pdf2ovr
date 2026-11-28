"""Annotation data models and legacy schema labels.

The dataclasses are used as normalized extraction models. The historic binary
parsers and serializers in this module are not validated against both observed
EANT schema families and therefore fail closed instead of guessing payload
layouts. A future schema-aware codec will replace these legacy entry points.
"""

import struct
from typing import List, Tuple, Optional
from dataclasses import dataclass


# Element type codes (from EHFA dictionary / observed values)
EANT_GROUP = 3
EANT_POLYLINE = 5
EANT_RECTANGLE = 6
EANT_ELLIPSE = 7
EANT_TEXT = 10
EANT_SYMBOL = 11


@dataclass
class ElementHeader:
    """Common header for all Element_2_Eant entries."""
    elem_id: int
    name: str
    description: str
    elem_type: int
    min_x: float
    min_y: float
    max_x: float
    max_y: float
    lattribute: int = 0


@dataclass
class TextElement:
    """Text2 element payload."""
    x: float
    y: float
    orientation: float
    scale: float
    h_align: int = 0
    v_align: int = 0
    text: str = ""
    style_index: int = 0


@dataclass
class RectangleElement:
    """Rectangle2 element payload."""
    x: float
    y: float
    width: float
    height: float
    style_index: int = 0
    fill_style: int = 0


@dataclass
class Point:
    """A 2D point."""
    x: float
    y: float


@dataclass
class PolylineElement:
    """Polyline2 element payload."""
    points: List[Point]
    style_index: int = 0
    closed: bool = False


@dataclass
class SymbolElement:
    """Symbol2 element payload (north arrow, etc.)."""
    x: float
    y: float
    scale: float
    rotation: float
    symbol_index: int = 0


@dataclass
class EllipseElement:
    """Ellipse2 element payload."""
    cx: float
    cy: float
    rx: float
    ry: float
    rotation: float
    style_index: int = 0


@dataclass
class GroupElement:
    """Group_Eant element payload."""
    group_type: int = 3


class StringField:
    """Read observed strings. Writing is disabled until encoding is verified."""

    @staticmethod
    def read(data: bytes, offset: int) -> Tuple[str, int]:
        if offset + 8 > len(data):
            return "", offset

        length = struct.unpack_from("<I", data, offset)[0]
        masterptr = struct.unpack_from("<I", data, offset + 4)[0]

        if length == 0 or length > 1024:
            return "", offset + 8

        chars_offset = offset + 8
        if chars_offset + length > len(data):
            return "", offset + 8

        try:
            text = data[chars_offset:chars_offset + length - 1].decode('ascii')
        except UnicodeDecodeError as exc:
            raise ValueError("Unsupported non-ASCII bytes in legacy EANT string field") from exc
        total_size = 8 + length
        if total_size % 2 != 0:
            total_size += 1

        return text, offset + total_size

    @staticmethod
    def write(text: str) -> bytes:
        raise NotImplementedError(
            "StringField serialization is disabled until EANT string encoding "
            "and pointer semantics are validated."
        )


class ElementParser:
    """Legacy payload parsers disabled pending per-schema verification."""

    @staticmethod
    def _blocked(*args, **kwargs):
        raise NotImplementedError(
            "Legacy EANT payload parsing is disabled because the observed "
            "Element_Eant and Element_2_Eant schemas use different typed "
            "child-entry structures. Use HFAReader for container inspection."
        )

    parse_text = _blocked
    parse_rectangle = _blocked
    parse_polyline = _blocked
    parse_symbol = _blocked
    parse_ellipse = _blocked
    parse_group = _blocked


class ElementFactory:
    """Serialization entry points deliberately blocked pending EANT validation.

    The historic payload encoders did not match the observed parent/child
    entry structure in the supplied OVR fixtures. Keeping these names but
    failing loudly prevents downstream callers from generating misleading
    binary payloads.
    """

    @staticmethod
    def _blocked(*args, **kwargs):
        raise NotImplementedError(
            "EANT serialization is disabled until payload fields, child-entry "
            "relationships, style references, and transforms are validated "
            "against genuine OVR fixtures and an ERDAS round trip."
        )

    create_header = _blocked
    create_text = _blocked
    create_rectangle = _blocked
    create_polyline = _blocked
    create_symbol = _blocked
    create_ellipse = _blocked
    create_group = _blocked


TYPE_NAME_MAP = {
    EANT_GROUP: "Group_Eant",
    EANT_POLYLINE: "Polyline2",
    EANT_RECTANGLE: "Rectangle2",
    EANT_ELLIPSE: "Ellipse2",
    EANT_TEXT: "Text2",
    EANT_SYMBOL: "Symbol2",
}

ELEMENT_INFO_MAP = {
    "Group_Eant": ("Group Info", ElementFactory.create_group, GroupElement),
    "Polyline2": ("Polyline Info", ElementFactory.create_polyline, PolylineElement),
    "Rectangle2": ("Rectangle Info", ElementFactory.create_rectangle, RectangleElement),
    "Ellipse2": ("Ellipse Info", ElementFactory.create_ellipse, EllipseElement),
    "Text2": ("Text Info", ElementFactory.create_text, TextElement),
    "Symbol2": ("Symbol Info", ElementFactory.create_symbol, SymbolElement),
}


def get_element_info_name(elem_type: int) -> str:
    type_name = TYPE_NAME_MAP.get(elem_type, "Unknown")
    info = ELEMENT_INFO_MAP.get(type_name)
    return info[0] if info else "Unknown Info"


def create_element_data(elem_type: int, element) -> bytes:
    raise NotImplementedError(
        "EANT serialization is disabled pending validation against the actual "
        "Element_*_Eant parent and typed child-entry schemas."
    )
