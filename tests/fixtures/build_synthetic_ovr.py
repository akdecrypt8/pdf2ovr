"""Build small, independent synthetic HFA/EANT fixtures for parser tests.

The fixtures are not copied from real OVR files. They use the HFA header and
entry layout observed in the supplied reference files and the minimal EANT
parent/child schemas documented in docs/TEST_FIXTURES.md. They are parser and
writer regression fixtures, not ERDAS compatibility samples.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pdf2ovr.hfa_dictionary import HFADictionary
from pdf2ovr.hfa_structure import HEADER_TAG, HFAReader

ENTRY_HEADER_LENGTH = 128
ROOT_ENTRY_OFFSET = 82
HEADER_STRUCT_OFFSET = 64

ELEMENT_ENUMS = (
    "EANT_UNDEFINEDELEMENTTYPE",
    "EANT_FILLED_POLYGON",
    "EANT_VECTORS",
    "EANT_GRID_CELLS",
    "EANT_UNFILLED_POLYGON",
    "EANT_PATTERN_POLYGON",
    "EANT_FILLED_CIRCLE",
    "EANT_UNFILLED_CIRCLE",
    "EANT_PATTERN_CIRCLE",
    "EANT_ARC",
    "EANT_TEXT",
    "EANT_SYMBOL",
    "EANT_LINE_GRID",
    "EANT_RECTANGLE",
    "EANT_ELLIPSE",
    "EANT_POLYGON",
    "EANT_POLYLINE",
    "EANT_GROUP",
    "EANT_POINT",
)

# A deliberately small dictionary: enough to exercise real pointer, enum,
# nested-object, BASEDATA, EANT tree and writer code paths. The grammar uses
# the same compact HFA dictionary syntax consumed by the production parser.
DICTIONARY_TEXT = """
{1:dx,1:dy,}Eevg_Coord,
{1:e3:EEVG_TOP,EEVG_YCENTER,EEVG_BOTTOM,yalign,1:e3:EEVG_LEFT,EEVG_XCENTER,EEVG_RIGHT,xalign,}Eevg_Align,
{1:e5:,EEVG_PIXEL,EEVG_NDU,EEVG_MAP,EEVG_OUTPUT,coordSys,0:pcunits,}Eant_CoordDefinition,
{1:lorder,1:lnumdimtransform,1:lnumdimpolynomial,1:ltermcount,0:plexponentlist,1:*bpolycoefmtx,1:*bpolycoefvector,}Efga_Polynomial,
{1:e3:EANT_LOCAL,EANT_VECTORDATA,EANT_DESCRIPTORDATA,source,0:pcstring,}Eant_TextData,
{1:e3:EANT_LOCAL,EANT_VECTORDATA,EANT_DESCRIPTORDATA,source,1:*bcoords,1:lsmooth,}VectorData_2_Eant,
{1:e2:EMSC_FALSE,EMSC_TRUE,useBox,1:dboxWidth,}TextBox_Eant,
{1:Lflags,1:ltextStyle,1:*oEevg_Coord,origin,1:dorientation,1:*oVectorData_2_Eant,coords,1:dscale,1:*oEevg_Align,alignment,1:*oEant_TextData,text,}Text2,
{1:Lflags,1:lfillStyle,1:*oEevg_Coord,center,1:dwidth,1:dheight,1:dorientation,}Rectangle2,
{1:Lflags,1:llineStyle,1:*oVectorData_2_Eant,coords,}Polyline2,
{1:lfillStyle,1:*oEevg_Coord,center,1:dsemiMajorAxis,1:dsemiMinorAxis,1:dorientation,}Eant_Ellipse,
{1:e5:,EEVG_PIXEL,EEVG_NDU,EEVG_MAP,EEVG_OUTPUT,outputUnits,1:LnextElement,}AntHeader_Eant,
{1:e2:EANT_ELEMENTLIST,EANT_SYMBOLELEMENTLIST,nodeType,}ElementNode_Eant,
{1:lid,0:pcname,0:pcdescription,1:e19:EANT_UNDEFINEDELEMENTTYPE,EANT_FILLED_POLYGON,EANT_VECTORS,EANT_GRID_CELLS,EANT_UNFILLED_POLYGON,EANT_PATTERN_POLYGON,EANT_FILLED_CIRCLE,EANT_UNFILLED_CIRCLE,EANT_PATTERN_CIRCLE,EANT_ARC,EANT_TEXT,EANT_SYMBOL,EANT_LINE_GRID,EANT_RECTANGLE,EANT_ELLIPSE,EANT_POLYGON,EANT_POLYLINE,EANT_GROUP,EANT_POINT,elmType,1:*oEant_CoordDefinition,scaleUnits,1:*oEant_CoordDefinition,positionUnits,1:*oEfga_Polynomial,xformMatrix,1:*oEfga_Polynomial,ixformMatrix,1:*bbBox,1:lattributeRecordNumber,}Element_2_Eant,
{1:lid,0:pcname,0:pcdescription,1:e19:EANT_UNDEFINEDELEMENTTYPE,EANT_FILLED_POLYGON,EANT_VECTORS,EANT_GRID_CELLS,EANT_UNFILLED_POLYGON,EANT_PATTERN_POLYGON,EANT_FILLED_CIRCLE,EANT_UNFILLED_CIRCLE,EANT_PATTERN_CIRCLE,EANT_ARC,EANT_TEXT,EANT_SYMBOL,EANT_LINE_GRID,EANT_RECTANGLE,EANT_ELLIPSE,EANT_POLYGON,EANT_POLYLINE,EANT_GROUP,EANT_POINT,elmType,1:*oEant_CoordDefinition,scaleUnits,1:*oEant_CoordDefinition,positionUnits,1:*oEfga_Polynomial,xformMatrix,1:*oEfga_Polynomial,ixformMatrix,1:*bbBox,}Element_Eant,
{1:e5:EANT_UNDEFINEDGROUPTYPE,EANT_GRIDTIC,EANT_LEGEND,EANT_SCALEBAR,EANT_GCP,type,}Group_Eant,
{1:lframeID,1:Lflags,0:pctitle,}Legend4,
.""".strip()


@dataclass
class Node:
    key: str
    name: str
    entry_type: str
    payload_type: str
    values: dict[str, Any]
    children: list["Node"] = field(default_factory=list)
    parent: "Node | None" = None
    offset: int = 0
    data_offset: int = 0
    payload: bytes = b""

    def add(self, child: "Node") -> "Node":
        child.parent = self
        self.children.append(child)
        return child


def _identity_polynomial() -> dict[str, Any]:
    return {
        "order": 1,
        "numdimtransform": 2,
        "numdimpolynomial": 2,
        "termcount": 3,
        "exponentlist": [0, 0, 1, 0, 0, 1],
        "polycoefmtx": {
            "rows": 2, "columns": 2, "item_type": "f64", "object_type": 2,
            "values": [1.0, 0.0, 0.0, 1.0],
        },
        "polycoefvector": {
            "rows": 2, "columns": 1, "item_type": "f64", "object_type": 1,
            "values": [0.0, 0.0],
        },
    }


def _bbox(x0: float, y0: float, x1: float, y1: float) -> dict[str, Any]:
    return {
        "rows": 2, "columns": 4, "item_type": "f64", "object_type": 2,
        "values": [x0, y0, x0, y1, x1, y1, x1, y0],
    }


def _modern_tree() -> Node:
    root = Node("root", "Annotation", "AntHeader_Eant", "AntHeader_Eant", {
        "outputUnits": "EEVG_MAP", "nextElement": 5,
    })
    element_list = root.add(Node("element-list", "ElementList", "ElementNode_Eant", "ElementNode_Eant", {
        "nodeType": "EANT_ELEMENTLIST",
    }))

    text_parent = element_list.add(Node("text-parent", "AntElement_1", "Element_2_Eant", "Element_2_Eant", {
        "id": 1, "name": "Synthetic text", "description": "Synthetic fixture label",
        "elmType": "EANT_TEXT", "scaleUnits": {"coordSys": "EEVG_OUTPUT", "units": "points"},
        "positionUnits": {"coordSys": "EEVG_MAP", "units": "inches"},
        "xformMatrix": _identity_polynomial(), "ixformMatrix": _identity_polynomial(),
        "bBox": _bbox(10, 10, 90, 40), "attributeRecordNumber": 0,
    }))
    text_geometry = text_parent.add(Node("text-geometry", "Text Info", "Text2", "Text2", {
        "flags": 0, "textStyle": 1, "origin": {"x": 15.0, "y": 25.0},
        "orientation": 0.0, "coords": None, "scale": 12.0,
        "alignment": {"yalign": "EEVG_YCENTER", "xalign": "EEVG_LEFT"},
        "text": {"source": "EANT_LOCAL", "string": "Synthetic label"},
    }))
    text_geometry.add(Node("text-box", "TextBox", "TextBox_Eant", "TextBox_Eant", {
        "useBox": "EMSC_TRUE", "boxWidth": 70.0,
    }))

    rectangle_parent = element_list.add(Node("rectangle-parent", "AntElement_2", "Element_2_Eant", "Element_2_Eant", {
        "id": 2, "name": "Synthetic rectangle", "description": "",
        "elmType": "EANT_RECTANGLE", "scaleUnits": {"coordSys": "EEVG_OUTPUT", "units": "points"},
        "positionUnits": {"coordSys": "EEVG_MAP", "units": "inches"},
        "xformMatrix": _identity_polynomial(), "ixformMatrix": _identity_polynomial(),
        "bBox": _bbox(100, 20, 150, 70), "attributeRecordNumber": 0,
    }))
    rectangle_parent.add(Node("rectangle-geometry", "Rectangle Info", "Rectangle2", "Rectangle2", {
        "flags": 0, "fillStyle": 2, "center": {"x": 125.0, "y": 45.0},
        "width": 50.0, "height": 50.0, "orientation": 0.0,
    }))

    group_parent = element_list.add(Node("group-parent", "AntElement_3", "Element_2_Eant", "Element_2_Eant", {
        "id": 3, "name": "Synthetic group", "description": "",
        "elmType": "EANT_GROUP", "scaleUnits": {"coordSys": "EEVG_OUTPUT", "units": "points"},
        "positionUnits": {"coordSys": "EEVG_MAP", "units": "inches"},
        "xformMatrix": _identity_polynomial(), "ixformMatrix": _identity_polynomial(),
        "bBox": _bbox(0, 0, 200, 200), "attributeRecordNumber": 0,
    }))
    group_meta = group_parent.add(Node("group-meta", "Group metadata", "Group_Eant", "Group_Eant", {
        "type": "EANT_LEGEND",
    }))
    group_meta.add(Node("legend-info", "Legend", "Legend4", "Legend4", {
        "frameID": 0, "flags": 0, "title": "Synthetic legend",
    }))

    polyline_parent = group_parent.add(Node("polyline-parent", "AntElement_4", "Element_2_Eant", "Element_2_Eant", {
        "id": 4, "name": "Synthetic polyline", "description": "Nested test line",
        "elmType": "EANT_POLYLINE", "scaleUnits": {"coordSys": "EEVG_OUTPUT", "units": "points"},
        "positionUnits": {"coordSys": "EEVG_MAP", "units": "inches"},
        "xformMatrix": _identity_polynomial(), "ixformMatrix": _identity_polynomial(),
        "bBox": _bbox(1, 2, 30, 25), "attributeRecordNumber": 0,
    }))
    polyline_parent.add(Node("polyline-geometry", "Polyline Info", "Polyline2", "Polyline2", {
        "flags": 0, "lineStyle": 3,
        "coords": {
            "source": "EANT_LOCAL",
            "coords": {
                "rows": 2, "columns": 3, "item_type": "f64", "object_type": 2,
                "values": [1.0, 2.0, 10.0, 20.0, 30.0, 25.0],
            },
            "smooth": 0,
        },
    }))
    return root


def _classic_tree() -> Node:
    root = Node("root", "Annotation", "AntHeader_Eant", "AntHeader_Eant", {
        "outputUnits": "EEVG_MAP", "nextElement": 1,
    })
    element_list = root.add(Node("element-list", "ElementList", "ElementNode_Eant", "ElementNode_Eant", {
        "nodeType": "EANT_ELEMENTLIST",
    }))
    ellipse_parent = element_list.add(Node("ellipse-parent", "AntElement", "Element_Eant", "Element_Eant", {
        "id": 10, "name": "Synthetic ellipse", "description": "Classic schema fixture",
        "elmType": "EANT_ELLIPSE", "scaleUnits": {"coordSys": "EEVG_MAP", "units": "other"},
        "positionUnits": {"coordSys": "EEVG_MAP", "units": "other"},
        "xformMatrix": _identity_polynomial(), "ixformMatrix": _identity_polynomial(),
        "bBox": _bbox(-5, -5, 5, 5),
    }))
    ellipse_parent.add(Node("ellipse-geometry", "Ellipse Info", "Eant_Ellipse", "Eant_Ellipse", {
        "fillStyle": 7, "center": {"x": 0.0, "y": 0.0},
        "semiMajorAxis": 5.0, "semiMinorAxis": 3.0, "orientation": 0.25,
    }))
    return root


def _flatten(root: Node) -> list[Node]:
    result: list[Node] = []
    def visit(node: Node) -> None:
        result.append(node)
        for child in node.children:
            visit(child)
    visit(root)
    return result


def build_ovr(root: Node) -> bytes:
    dictionary = HFADictionary(DICTIONARY_TEXT)
    nodes = _flatten(root)
    for index, node in enumerate(nodes):
        node.offset = ROOT_ENTRY_OFFSET + index * ENTRY_HEADER_LENGTH

    # Serialize each payload at its final absolute offset because HFA pointer
    # fields are absolute offsets to inline payloads.
    data_cursor = ROOT_ENTRY_OFFSET + len(nodes) * ENTRY_HEADER_LENGTH
    for node in nodes:
        node.data_offset = data_cursor
        node.payload = dictionary.encode_instance(
            node.payload_type, node.values, absolute_offset=data_cursor
        )
        data_cursor += len(node.payload)

    dictionary_bytes = (DICTIONARY_TEXT + "\x00").encode("latin-1")
    dictionary_pointer = data_cursor
    output = bytearray(dictionary_pointer + len(dictionary_bytes))
    output[:16] = HEADER_TAG
    struct.pack_into("<I", output, 16, HEADER_STRUCT_OFFSET)
    struct.pack_into(
        "<IIIHI", output, HEADER_STRUCT_OFFSET,
        1, 0, ROOT_ENTRY_OFFSET, ENTRY_HEADER_LENGTH, dictionary_pointer,
    )

    for node in nodes:
        parent_offset = node.parent.offset if node.parent else 0
        siblings = node.parent.children if node.parent else []
        sibling_index = siblings.index(node) if node.parent else 0
        previous = siblings[sibling_index - 1].offset if sibling_index > 0 else 0
        following = siblings[sibling_index + 1].offset if sibling_index + 1 < len(siblings) else 0
        child = node.children[0].offset if node.children else 0
        struct.pack_into(
            "<IIIIII", output, node.offset,
            following, previous, parent_offset, child, node.data_offset, len(node.payload),
        )
        name = node.name.encode("ascii")
        entry_type = node.entry_type.encode("ascii")
        if len(name) >= 64 or len(entry_type) >= 32:
            raise ValueError("Synthetic fixture entry name/type exceeds HFA header capacity")
        output[node.offset + 24:node.offset + 88] = name + b"\x00" * (64 - len(name))
        output[node.offset + 88:node.offset + 120] = entry_type + b"\x00" * (32 - len(entry_type))
        struct.pack_into("<I", output, node.offset + 120, 0)
        output[node.offset + 124:node.offset + 128] = b"\x00" * 4
        output[node.data_offset:node.data_offset + len(node.payload)] = node.payload

    output[dictionary_pointer:] = dictionary_bytes
    result = bytes(output)
    reader = HFAReader(result)
    summary = reader.validate()
    if summary["entry_count"] != len(nodes):
        raise AssertionError("Synthetic fixture builder produced an incomplete tree")
    return result


def main() -> None:
    fixture_dir = Path(__file__).resolve().parent
    outputs = {
        "synthetic_modern.ovr": build_ovr(_modern_tree()),
        "synthetic_classic.ovr": build_ovr(_classic_tree()),
    }
    for name, data in outputs.items():
        path = fixture_dir / name
        path.write_bytes(data)
        print(f"Wrote {path} ({len(data)} bytes)")


if __name__ == "__main__":
    main()
