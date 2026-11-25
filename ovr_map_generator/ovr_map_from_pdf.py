"""
ovr_map_from_pdf - Generate .ovr and .map files from a PDF using template patching.

Approach:
  1. Read template .ovr/.map files as raw binary
  2. Parse EHFA structure to locate and replace annotation elements
  3. Extract content from input PDF
  4. Patch template with new elements preserving styles, transforms, etc.
  5. Update .map file's .ovr path reference

Usage:
    python ovr_map_from_pdf.py input.pdf template.ovr template.map output_prefix
"""

import sys
import os
import struct
import re
from typing import List, Tuple, Optional, Dict
from dataclasses import dataclass


try:
    import pymupdf
    HAVE_FITZ = True
except ImportError:
    HAVE_FITZ = False


@dataclass
class PDFElement:
    elem_type: str
    x: float
    y: float
    width: float
    height: float
    content: str = ""
    color: Tuple[float, float, float] = (0, 0, 0)
    font_size: float = 10.0
    points: List[Tuple[float, float]] = None
    page_num: int = 0


def extract_pdf_elements(pdf_path: str) -> List[PDFElement]:
    if not HAVE_FITZ:
        print("Warning: PyMuPDF (fitz) not installed. Install with: pip install pymupdf")
        return []

    try:
        from .pdf_extractor import PDFExtractor, ExtractedElement
        from .element_types import (
            TextElement, RectangleElement, PolylineElement,
            EANT_TEXT, EANT_RECTANGLE, EANT_POLYLINE
        )
    except ImportError:
        return _extract_pdf_elements_legacy(pdf_path)

    extractor = PDFExtractor(pdf_path)
    extracted = extractor.extract_all()
    extractor.close()

    elements = []
    for ext in extracted:
        if ext.elem_type == EANT_TEXT:
            elem = ext.element
            elements.append(PDFElement(
                elem_type="text",
                x=elem.x, y=elem.y,
                width=ext.bbox[2] - ext.bbox[0],
                height=ext.bbox[3] - ext.bbox[1],
                content=elem.text,
                font_size=elem.scale,
                page_num=ext.page_num
            ))
        elif ext.elem_type == EANT_RECTANGLE:
            elem = ext.element
            elements.append(PDFElement(
                elem_type="rect",
                x=elem.x, y=elem.y,
                width=elem.width, height=elem.height,
                page_num=ext.page_num
            ))
        elif ext.elem_type == EANT_POLYLINE:
            elem = ext.element
            points = [(p.x, p.y) for p in elem.points]
            if points:
                elements.append(PDFElement(
                    elem_type="line",
                    x=points[0][0], y=points[0][1],
                    width=abs(points[-1][0] - points[0][0]),
                    height=abs(points[-1][1] - points[0][1]),
                    points=points,
                    page_num=ext.page_num
                ))

    return elements


def _extract_pdf_elements_legacy(pdf_path: str) -> List[PDFElement]:
    import pymupdf
    doc = pymupdf.open(pdf_path)
    page = doc[0]
    page_w = page.rect.width
    page_h = page.rect.height
    elements = []

    text_dict = page.get_text("dict")
    for block in text_dict.get("blocks", []):
        if block.get("type") == 0:
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = span.get("text", "").strip()
                    if not text:
                        continue
                    x0, y0, x1, y1 = span.get("bbox", (0, 0, 0, 0))
                    elements.append(PDFElement(
                        elem_type="text",
                        x=x0, y=page_h - y1,
                        width=x1 - x0, height=y1 - y0,
                        content=text,
                        font_size=span.get("size", 10)
                    ))

    try:
        drawings = page.get_drawings()
        for drawing in drawings:
            for item in drawing.get("items", []):
                if not item:
                    continue
                itype = item[0]
                if itype == "l":
                    p1, p2 = item[1], item[2]
                    elements.append(PDFElement(
                        elem_type="line",
                        x=p1.x, y=page_h - p1.y,
                        width=p2.x - p1.x, height=p2.y - p1.y,
                        points=[(p1.x, page_h - p1.y), (p2.x, page_h - p2.y)]
                    ))
                elif itype == "re":
                    r = item[1]
                    elements.append(PDFElement(
                        elem_type="rect",
                        x=r.x0, y=page_h - r.y1,
                        width=r.width, height=r.height
                    ))
    except Exception:
        pass

    doc.close()
    return elements


ENTRY_SIZE = 124


def scan_entries(data: bytes) -> List[Dict]:
    entries = []
    for i in range(0x60, len(data) - ENTRY_SIZE):
        try:
            next_p, prev_p, parent_p, child_p, dptr, dsize = struct.unpack_from(
                "<IIIIII", data, i)
            name = data[i+24:i+88].split(b'\x00')[0].decode('ascii', errors='ignore')
            ctype = data[i+88:i+120].split(b'\x00')[0].decode('ascii', errors='ignore')

            if len(name) < 2 or len(name) > 60:
                continue
            if not all(c.isalnum() or c in '_- ' for c in name):
                continue
            if name.isdigit():
                continue
            if dsize < 0 or dsize > 500000:
                continue
            if dptr < 0 or (dptr > 0 and dptr + dsize > len(data)):
                continue
            if dptr > 0 and dptr >= i and dptr < i + ENTRY_SIZE:
                continue

            entries.append({
                'offset': i,
                'next': next_p, 'prev': prev_p,
                'parent': parent_p, 'child': child_p,
                'data_ptr': dptr, 'data_size': dsize,
                'name': name, 'type': ctype
            })
        except Exception:
            pass

    offsets = {e['offset'] for e in entries}
    valid = []
    for e in entries:
        linked = any([
            e['next'] in offsets, e['prev'] in offsets,
            e['parent'] in offsets, e['child'] in offsets,
            e['name'] in ('Annotation', 'Map Composition', 'root',
                          'ElementList', 'SymbolElementList')
        ])
        pointed = any(
            o['next'] == e['offset'] or o['prev'] == e['offset'] or
            o['parent'] == e['offset'] or o['child'] == e['offset']
            for o in entries
        )
        if linked or pointed:
            valid.append(e)
    return valid


def follow_siblings(data: bytes, entries: List[Dict], start_offset: int) -> List[Dict]:
    by_off = {e['offset']: e for e in entries}
    result = []
    cur = start_offset
    while cur > 0 and cur in by_off:
        result.append(by_off[cur])
        cur = by_off[cur]['next']
    return result


def find_entry(entries: List[Dict], name: str) -> Optional[Dict]:
    for e in entries:
        if e['name'] == name:
            return e
    return None


def make_text_payload(text: str, x: float, y: float, font_size: float = 10.0) -> bytes:
    payload = struct.pack("<dd", x, y)
    payload += struct.pack("<d", 0.0)
    payload += struct.pack("<d", font_size)
    payload += struct.pack("<ii", 0, 0)

    chars = text.encode('latin-1', errors='replace') + b'\x00'
    payload += struct.pack("<II", len(chars), 8) + chars

    while len(payload) % 8 != 0:
        payload += b'\x00'
    return payload


def make_rect_payload(x: float, y: float, w: float, h: float) -> bytes:
    payload = struct.pack("<I", 0)
    payload += struct.pack("<I", 0)
    payload += struct.pack("<I", 1)
    payload += struct.pack("<I", 0)
    payload += struct.pack("<dddd", x, y, x + w, y + h)
    while len(payload) < 56:
        payload += b'\x00'
    return payload


def make_polyline_payload(points: List[Tuple[float, float]]) -> bytes:
    payload = struct.pack("<I", 0)
    payload += struct.pack("<I", len(points))
    payload += struct.pack("<I", 0)
    payload += struct.pack("<I", 0)
    payload += b'\x00' * 16
    for px, py in points:
        payload += struct.pack("<dd", px, py)
    return payload


def make_symbol_payload(x: float, y: float, size: float = 20.0) -> bytes:
    payload = struct.pack("<I", 0)
    payload += struct.pack("<I", 0)
    payload += struct.pack("<I", 0)
    payload += struct.pack("<dd", x, y)
    payload += struct.pack("<d", 0.0)
    payload += struct.pack("<I", 0)
    payload += struct.pack("<I", 0x020000)
    return payload


def make_group_payload() -> bytes:
    return struct.pack("<H", 3)


class OVRPatcher:
    """Patch a template .ovr file with new annotation elements."""

    def __init__(self, template_path: str):
        with open(template_path, "rb") as f:
            self.data = bytearray(f.read())
        self.entries = scan_entries(self.data)
        self.by_offset = {e['offset']: e for e in self.entries}

    def get_element_list(self) -> Optional[Dict]:
        return find_entry(self.entries, "ElementList")

    def remove_subtree(self, entry: Dict):
        if entry['data_ptr'] > 0:
            end = min(entry['data_ptr'] + entry['data_size'], len(self.data))
            for i in range(entry['data_ptr'], end):
                self.data[i] = 0

        for i in range(entry['offset'], entry['offset'] + ENTRY_SIZE):
            self.data[i] = 0

        child_off = entry['child']
        while child_off > 0 and child_off in self.by_offset:
            child = self.by_offset[child_off]
            self.remove_subtree(child)
            child_off = child['next']

    def clear_element_list(self):
        elem_list = self.get_element_list()
        if not elem_list:
            return

        self._existing_first_child = elem_list['child']

        struct.pack_into("<I", self.data, elem_list['offset'] + 12, 0)

        elem_list['child'] = 0

        self.entries = scan_entries(self.data)
        self.by_offset = {e['offset']: e for e in self.entries}

    def append_new_elements(self, pdf_elements: List[PDFElement]):
        elem_list = self.get_element_list()
        if not elem_list:
            raise ValueError("ElementList not found")

        existing_first_child = getattr(self, '_existing_first_child', 0)

        max_data_end = max(
            (e['data_ptr'] + e['data_size'] for e in self.entries if e['data_ptr'] > 0),
            default=len(self.data)
        )
        max_entry_end = max(
            (e['offset'] + ENTRY_SIZE for e in self.entries),
            default=0x60
        )

        base = max(max_entry_end + 8, max_data_end + 8)
        if base % 8 != 0:
            base += 8 - (base % 8)

        num_elems = len(pdf_elements)
        total_entries = num_elems * 2
        entry_block_size = total_entries * ENTRY_SIZE

        entry_cursor = base
        data_cursor = base + entry_block_size + 1024
        if data_cursor % 8 != 0:
            data_cursor += 8 - (data_cursor % 8)

        min_size = data_cursor + num_elems * 200 + 1024
        if len(self.data) < min_size:
            self.data.extend(b'\x00' * (min_size - len(self.data)))

        first_child_offset = 0
        prev_main_offset = 0
        elem_id = 1000

        for pdf_elem in pdf_elements:
            elem_id += 1
            elem_name = f"AntElement_{elem_id}"

            if pdf_elem.elem_type == "text":
                payload = make_text_payload(
                    pdf_elem.content, pdf_elem.x, pdf_elem.y, pdf_elem.font_size)
                info_name = "Text Info"
                info_type = "Text2"
            elif pdf_elem.elem_type == "rect":
                payload = make_rect_payload(
                    pdf_elem.x, pdf_elem.y, pdf_elem.width, pdf_elem.height)
                info_name = "Rectangle Info"
                info_type = "Rectangle2"
            elif pdf_elem.elem_type == "line":
                pts = pdf_elem.points or [(pdf_elem.x, pdf_elem.y),
                                           (pdf_elem.x + pdf_elem.width,
                                            pdf_elem.y + pdf_elem.height)]
                payload = make_polyline_payload(pts)
                info_name = "Polyline Info"
                info_type = "Polyline2"
            else:
                continue

            main_offset = entry_cursor
            entry_cursor += ENTRY_SIZE

            name_b = elem_name.encode('ascii')[:63].ljust(64, b'\x00')
            ctype_b = "Element_2_Eant".encode('ascii')[:31].ljust(32, b'\x00')

            struct.pack_into("<IIIIII", self.data, main_offset,
                             0, prev_main_offset,
                             elem_list['offset'], 0,
                             data_cursor, len(payload))
            self.data[main_offset + 24:main_offset + 88] = name_b
            self.data[main_offset + 88:main_offset + 120] = ctype_b
            struct.pack_into("<I", self.data, main_offset + 120, 0)

            if prev_main_offset > 0:
                struct.pack_into("<I", self.data, prev_main_offset, main_offset)
            if first_child_offset == 0:
                first_child_offset = main_offset

            data_end = data_cursor + len(payload)
            self.data[data_cursor:data_end] = payload
            data_cursor = data_end
            if data_cursor % 8 != 0:
                data_cursor += 8 - (data_cursor % 8)

            info_offset = entry_cursor
            entry_cursor += ENTRY_SIZE

            info_payload = b"\x00" * 4
            info_name_b = info_name.encode('ascii')[:63].ljust(64, b'\x00')
            info_ctype_b = f"{info_type}_Eant".encode('ascii')[:31].ljust(32, b'\x00')

            struct.pack_into("<IIIIII", self.data, info_offset,
                             0, 0, main_offset, 0,
                             data_cursor, len(info_payload))
            self.data[info_offset + 24:info_offset + 88] = info_name_b
            self.data[info_offset + 88:info_offset + 120] = info_ctype_b
            struct.pack_into("<I", self.data, info_offset + 120, 0)

            struct.pack_into("<I", self.data, main_offset + 12, info_offset)

            info_data_end = data_cursor + len(info_payload)
            self.data[data_cursor:info_data_end] = info_payload
            data_cursor = info_data_end
            if data_cursor % 8 != 0:
                data_cursor += 8 - (data_cursor % 8)

            prev_main_offset = main_offset

        if existing_first_child > 0:
            struct.pack_into("<I", self.data, prev_main_offset, existing_first_child)
            struct.pack_into("<I", self.data, existing_first_child + 4, prev_main_offset)
            first_child_offset = first_child_offset or existing_first_child
        elif first_child_offset > 0:
            pass

        if first_child_offset > 0:
            struct.pack_into("<I", self.data, elem_list['offset'] + 12, first_child_offset)
            elem_list['child'] = first_child_offset

        self.entries = scan_entries(self.data)
        self.by_offset = {e['offset']: e for e in self.entries}

    def save(self, path: str):
        with open(path, "wb") as f:
            f.write(self.data)


class MAPPatcher:
    """Patch the .ovr path in a .map file."""

    def __init__(self, template_path: str):
        with open(template_path, "rb") as f:
            self.data = bytearray(f.read())

    def update_path(self, new_ovr_path: str):
        marker = b"(:Annotation)"
        pos = self.data.find(marker)

        if pos >= 0:
            start = pos
            while start > 0:
                c = self.data[start - 1]
                if c == 0:
                    start -= 1
                    break
                if c < 32 or c > 126:
                    break
                start -= 1

            end = pos + len(marker)
            while end < len(self.data) and self.data[end] == 0:
                end += 1

            old = bytes(self.data[start:end])
            new_b = (new_ovr_path.encode("utf-8") + marker)
            if len(new_b) < len(old):
                new_b = new_b.ljust(len(old), b"\x00")
            elif len(new_b) > len(old):
                new_b = new_b[:len(old)]

            self.data[start:start + len(new_b)] = new_b
        else:
            import re
            matches = list(re.finditer(rb'[^\x00]{5,}\.ovr', self.data))
            if not matches:
                raise ValueError("Could not find .ovr path in .map file")

            match = matches[-1]
            start = match.start()
            end = match.end()

            while end < len(self.data) and self.data[end] == 0:
                end += 1

            old = bytes(self.data[start:end])
            new_b = new_ovr_path.encode("utf-8")
            if len(new_b) < len(old):
                new_b = new_b.ljust(len(old), b"\x00")
            elif len(new_b) > len(old):
                new_b = new_b[:len(old)]

            self.data[start:start + len(new_b)] = new_b

    def save(self, path: str):
        with open(path, "wb") as f:
            f.write(self.data)


def _filter_elements(elements: List[PDFElement]) -> List[PDFElement]:
    min_size = 1.0
    filtered = [e for e in elements
                if e.width >= min_size or e.height >= min_size]

    texts = [e for e in filtered if e.elem_type == "text"]
    others = [e for e in filtered if e.elem_type != "text"]

    seen = set()
    unique_texts = []
    for e in texts:
        key = (round(e.x, 1), round(e.y, 1), e.content)
        if key not in seen:
            seen.add(key)
            unique_texts.append(e)

    return unique_texts + others


def generate(input_pdf: str, template_ovr: str, template_map: str,
             output_prefix: str):
    try:
        print(f"Reading templates: {template_ovr}, {template_map}")
    except UnicodeEncodeError:
        print("Reading templates: [path contains unicode]")
    ovr = OVRPatcher(template_ovr)
    map_p = MAPPatcher(template_map)

    try:
        print(f"Extracting from: {input_pdf}")
    except UnicodeEncodeError:
        print("Extracting from: [path contains unicode]")
    elements = extract_pdf_elements(input_pdf)
    print(f"  Extracted {len(elements)} raw elements")

    elements = _filter_elements(elements)
    print(f"  After filtering: {len(elements)} elements")

    print("Patching .ovr...")
    ovr.clear_element_list()
    ovr.append_new_elements(elements)

    out_ovr = f"{output_prefix}.map.ovr"
    out_map = f"{output_prefix}.map"

    try:
        print(f"Writing: {out_ovr}")
    except UnicodeEncodeError:
        print("Writing: [path contains unicode]")
    ovr.save(out_ovr)

    try:
        print(f"Writing: {out_map}")
    except UnicodeEncodeError:
        print("Writing: [path contains unicode]")
    map_p.update_path(os.path.basename(out_ovr))
    map_p.save(out_map)

    print("Done!")


def main():
    if len(sys.argv) < 5:
        print("Usage: python ovr_map_from_pdf.py <input.pdf> <template.ovr> <template.map> <output_prefix>")
        sys.exit(1)

    input_pdf, template_ovr, template_map, output_prefix = sys.argv[1:5]

    for p in (input_pdf, template_ovr, template_map):
        if not os.path.exists(p):
            print(f"Error: file not found: {p}")
            sys.exit(1)

    generate(input_pdf, template_ovr, template_map, output_prefix)


if __name__ == "__main__":
    main()