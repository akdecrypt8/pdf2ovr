"""
Element Types - Parsers and factories for annotation element payloads.

The .ovr file contains elements of these types:
  Text2, Rectangle2, Polyline2, Symbol2, Group_Eant, Ellipse2

Each element has an Element_2_Eant header followed by type-specific payload data.
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
    """Handles the [len:4][masterptr:4][chars+NUL] string format."""

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

        text = data[chars_offset:chars_offset + length - 1].decode('ascii', errors='ignore')
        total_size = 8 + length
        if total_size % 2 != 0:
            total_size += 1

        return text, offset + total_size

    @staticmethod
    def write(text: str) -> bytes:
        chars = text.encode('ascii') + b'\x00'
        length = len(chars)
        masterptr = 8

        payload = struct.pack("<II", length, masterptr) + chars
        if len(payload) % 2 != 0:
            payload += b'\x00'

        return payload


class ElementParser:
    """Parse element payloads from binary data."""

    @staticmethod
    def parse_text(data: bytes) -> TextElement:
        x, y = struct.unpack_from("<dd", data, 0)
        orientation = struct.unpack_from("<d", data, 16)[0]
        scale = struct.unpack_from("<d", data, 24)[0]
        h_align, v_align = struct.unpack_from("<ii", data, 32)

        text, _ = StringField.read(data, 40)

        return TextElement(x=x, y=y, orientation=orientation, scale=scale,
                           h_align=h_align, v_align=v_align, text=text)

    @staticmethod
    def parse_rectangle(data: bytes) -> RectangleElement:
        if len(data) < 40:
            return RectangleElement(0, 0, 0, 0)

        min_x, min_y, max_x, max_y = struct.unpack_from("<dddd", data, 16)
        style_index = struct.unpack_from("<I", data, 8)[0]

        return RectangleElement(x=min_x, y=min_y,
                                width=max_x - min_x,
                                height=max_y - min_y,
                                style_index=style_index)

    @staticmethod
    def parse_polyline(data: bytes) -> PolylineElement:
        if len(data) < 12:
            return PolylineElement([])

        count = struct.unpack_from("<I", data, 4)[0]
        style_index = struct.unpack_from("<I", data, 8)[0]

        points = []
        offset = 32
        for i in range(count):
            if offset + 16 > len(data):
                break
            px, py = struct.unpack_from("<dd", data, offset)
            points.append(Point(px, py))
            offset += 16

        return PolylineElement(points=points, style_index=style_index)

    @staticmethod
    def parse_symbol(data: bytes) -> SymbolElement:
        if len(data) < 32:
            return SymbolElement(0, 0, 1, 0)

        x, y = struct.unpack_from("<dd", data, 12)
        scale = 1.0
        rotation = 0.0
        symbol_index = struct.unpack_from("<I", data, 4)[0]

        if len(data) >= 36:
            rotation = struct.unpack_from("<d", data, 28)[0]

        return SymbolElement(x=x, y=y, scale=scale, rotation=rotation,
                             symbol_index=symbol_index)

    @staticmethod
    def parse_ellipse(data: bytes) -> EllipseElement:
        if len(data) < 40:
            return EllipseElement(0, 0, 0, 0, 0)

        min_x, min_y, max_x, max_y = struct.unpack_from("<dddd", data, 16)
        style_index = struct.unpack_from("<I", data, 8)[0]

        cx = (min_x + max_x) / 2
        cy = (min_y + max_y) / 2
        rx = (max_x - min_x) / 2
        ry = (max_y - min_y) / 2

        return EllipseElement(cx=cx, cy=cy, rx=rx, ry=ry, rotation=0,
                              style_index=style_index)

    @staticmethod
    def parse_group(data: bytes) -> GroupElement:
        if len(data) >= 2:
            group_type = struct.unpack_from("<H", data, 0)[0]
            return GroupElement(group_type=group_type)
        return GroupElement()


class ElementFactory:
    """Create element payload bytes from element objects."""

    @staticmethod
    def create_header(elem_id: int, name: str, elem_type: int,
                      bbox: Tuple[float, float, float, float]) -> bytes:
        min_x, min_y, max_x, max_y = bbox
        name_b = name.encode('ascii')[:63].ljust(64, b'\x00')
        desc_b = b"" .ljust(32, b'\x00')

        header = struct.pack("<I", elem_id)
        header += struct.pack("<I", len(name_b)) + name_b
        header += struct.pack("<I", len(desc_b)) + desc_b
        header += struct.pack("<I", elem_type)
        header += struct.pack("<dddd", min_x, min_y, max_x, max_y)
        header += struct.pack("<I", 0)

        return header

    @staticmethod
    def create_text(elem: TextElement) -> bytes:
        payload = struct.pack("<dd", elem.x, elem.y)
        payload += struct.pack("<d", elem.orientation)
        payload += struct.pack("<d", elem.scale)
        payload += struct.pack("<ii", elem.h_align, elem.v_align)
        payload += StringField.write(elem.text)

        while len(payload) % 8 != 0:
            payload += b'\x00'

        return payload

    @staticmethod
    def create_rectangle(elem: RectangleElement) -> bytes:
        min_x = elem.x
        min_y = elem.y
        max_x = elem.x + elem.width
        max_y = elem.y + elem.height

        payload = struct.pack("<I", 0)
        payload += struct.pack("<I", elem.style_index)
        payload += struct.pack("<I", 1)
        payload += struct.pack("<I", 0)
        payload += struct.pack("<dddd", min_x, min_y, max_x, max_y)

        while len(payload) < 56:
            payload += b'\x00'

        return payload

    @staticmethod
    def create_polyline(elem: PolylineElement) -> bytes:
        payload = struct.pack("<I", 0)
        payload += struct.pack("<I", len(elem.points))
        payload += struct.pack("<I", elem.style_index)
        payload += struct.pack("<I", 0)

        payload += b'\x00' * 16

        for pt in elem.points:
            payload += struct.pack("<dd", pt.x, pt.y)

        return payload

    @staticmethod
    def create_symbol(elem: SymbolElement) -> bytes:
        payload = struct.pack("<I", 0)
        payload += struct.pack("<I", elem.symbol_index)
        payload += struct.pack("<I", 0)
        payload += struct.pack("<dd", elem.x, elem.y)
        payload += struct.pack("<d", elem.rotation)
        payload += struct.pack("<I", 0)
        payload += struct.pack("<I", 0x020000)

        return payload

    @staticmethod
    def create_ellipse(elem: EllipseElement) -> bytes:
        min_x = elem.cx - elem.rx
        min_y = elem.cy - elem.ry
        max_x = elem.cx + elem.rx
        max_y = elem.cy + elem.ry

        payload = struct.pack("<I", 0)
        payload += struct.pack("<I", elem.style_index)
        payload += struct.pack("<I", 1)
        payload += struct.pack("<I", 0)
        payload += struct.pack("<dddd", min_x, min_y, max_x, max_y)

        while len(payload) < 56:
            payload += b'\x00'

        return payload

    @staticmethod
    def create_group(elem: GroupElement) -> bytes:
        return struct.pack("<H", elem.group_type)


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
    type_name = TYPE_NAME_MAP.get(elem_type, "Unknown")
    info = ELEMENT_INFO_MAP.get(type_name)
    if info:
        return info[1](element)
    return b""