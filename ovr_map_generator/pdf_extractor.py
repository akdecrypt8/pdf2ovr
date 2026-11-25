"""
PDF Extractor - Extract content from PDFs and map to annotation elements.

Uses PyMuPDF to extract:
  - Text elements (position, font, size, content)
  - Vector shapes (lines, rectangles, polygons)
  - Images (raster data)
  - Page dimensions and transforms
"""

import fitz
from typing import List, Tuple, Optional
from dataclasses import dataclass

from .element_types import (
    TextElement, RectangleElement, PolylineElement,
    SymbolElement, EllipseElement, Point,
    EANT_TEXT, EANT_RECTANGLE, EANT_POLYLINE,
    EANT_SYMBOL, EANT_ELLIPSE, EANT_GROUP
)


@dataclass
class ExtractedElement:
    """A generic extracted element from a PDF."""
    elem_type: int
    element: object
    bbox: Tuple[float, float, float, float]
    page_num: int = 0


class PDFExtractor:
    """Extract annotation-relevant content from a PDF file."""

    def __init__(self, pdf_path: str):
        self.doc = fitz.open(pdf_path)
        self.page = self.doc[0]
        self.page_width = self.page.rect.width
        self.page_height = self.page.rect.height

    def extract_all(self) -> List[ExtractedElement]:
        elements = []
        for page_num in range(len(self.doc)):
            page = self.doc[page_num]
            elements.extend(self._extract_page_text(page, page_num))
            elements.extend(self._extract_page_drawings(page, page_num))
        return elements

    def _extract_page_text(self, page, page_num: int) -> List[ExtractedElement]:
        elements = []
        text_dict = page.get_text("dict")
        page_h = page.rect.height

        for block in text_dict["blocks"]:
            if block["type"] == 0:
                for line in block["lines"]:
                    for span in line["spans"]:
                        text = span["text"].strip()
                        if not text:
                            continue

                        x0, y0, x1, y1 = span["bbox"]
                        font_size = span["size"]

                        map_x = x0
                        map_y = page_h - y1

                        text_width = x1 - x0
                        text_height = font_size

                        elem = TextElement(
                            x=map_x,
                            y=map_y,
                            orientation=0.0,
                            scale=font_size,
                            h_align=0,
                            v_align=0,
                            text=text,
                            style_index=0
                        )

                        elements.append(ExtractedElement(
                            elem_type=EANT_TEXT,
                            element=elem,
                            bbox=(map_x, map_y, map_x + text_width, map_y + text_height),
                            page_num=page_num
                        ))

        return elements

    def _extract_page_drawings(self, page, page_num: int) -> List[ExtractedElement]:
        elements = []
        page_h = page.rect.height

        try:
            drawings = page.get_drawings()
        except Exception:
            drawings = []

        # Collect all rectangles first
        for drawing in drawings:
            items = drawing.get("items", [])
            for item in items:
                if not isinstance(item, (list, tuple)) or len(item) < 2:
                    continue

                item_type = item[0]

                if item_type == "re":
                    r = item[1]
                    elem = RectangleElement(
                        x=r.x0,
                        y=page_h - r.y1,
                        width=r.width,
                        height=r.height,
                        style_index=0
                    )
                    elements.append(ExtractedElement(
                        elem_type=EANT_RECTANGLE,
                        element=elem,
                        bbox=(r.x0, page_h - r.y1,
                              r.x0 + r.width, page_h - r.y1 + r.height),
                        page_num=page_num
                    ))

                elif item_type in ("c", "qu", "cu"):
                    pts = item[1:]
                    points = [Point(p.x, page_h - p.y) for p in pts]
                    elem = PolylineElement(points=points, style_index=0)
                    elements.append(ExtractedElement(
                        elem_type=EANT_POLYLINE,
                        element=elem,
                        bbox=self._points_bbox(points),
                        page_num=page_num
                    ))

        # Collect ALL line segments from the page
        all_segments = []
        for drawing in drawings:
            items = drawing.get("items", [])
            for item in items:
                if not isinstance(item, (list, tuple)) or len(item) < 2:
                    continue
                if item[0] == "l":
                    p1, p2 = item[1], item[2]
                    all_segments.append((p1.x, page_h - p1.y, p2.x, page_h - p2.y))

        # Group segments into continuous polylines by connectivity
        # A polyline is a chain where segment[i].end == segment[i+1].start
        used = [False] * len(all_segments)
        for i, seg in enumerate(all_segments):
            if used[i]:
                continue
            # Start a new polyline
            poly_points = [Point(seg[0], seg[1]), Point(seg[2], seg[3])]
            used[i] = True
            current_end = (seg[2], seg[3])

            # Extend forward
            while True:
                found = False
                for j, next_seg in enumerate(all_segments):
                    if used[j]:
                        continue
                    if (next_seg[0], next_seg[1]) == current_end:
                        poly_points.append(Point(next_seg[2], next_seg[3]))
                        used[j] = True
                        current_end = (next_seg[2], next_seg[3])
                        found = True
                        break
                if not found:
                    break

            if len(poly_points) >= 2:
                elem = PolylineElement(points=poly_points, style_index=0)
                elements.append(ExtractedElement(
                    elem_type=EANT_POLYLINE,
                    element=elem,
                    bbox=self._points_bbox(poly_points),
                    page_num=page_num
                ))

        return elements

    def extract_images(self) -> List[Tuple[bytes, str, Tuple[float, float, float, float]]]:
        images = []
        for img_index in range(len(self.page.get_images())):
            try:
                xref = self.page.get_images()[img_index][0]
                base_image = self.doc.extract_image(xref)
                image_bytes = base_image["image"]
                ext = base_image["ext"]

                bbox = (0, 0, self.page_width, self.page_height)

                images.append((image_bytes, ext, bbox))
            except Exception:
                continue

        return images

    @staticmethod
    def _points_bbox(points: List[Point]) -> Tuple[float, float, float, float]:
        if not points:
            return (0, 0, 0, 0)
        xs = [p.x for p in points]
        ys = [p.y for p in points]
        return (min(xs), min(ys), max(xs), max(ys))

    def close(self):
        self.doc.close()


class ScaleBarBuilder:
    """Build a scale bar element group."""

    @staticmethod
    def build_scale_bar(x: float, y: float, width: float, num_segments: int = 4,
                        units: str = "Meters", label: str = "Scale") -> List[ExtractedElement]:
        elements = []
        segment_width = width / num_segments

        group = GroupElement(group_type=3)
        elements.append(ExtractedElement(
            elem_type=EANT_GROUP,
            element=group,
            bbox=(x, y, x + width, y + 20)
        ))

        for i in range(num_segments):
            seg_x = x + i * segment_width
            elem = RectangleElement(
                x=seg_x,
                y=y,
                width=segment_width,
                height=10,
                style_index=i % 2
            )
            elements.append(ExtractedElement(
                elem_type=EANT_RECTANGLE,
                element=elem,
                bbox=(seg_x, y, seg_x + segment_width, y + 10)
            ))

        label_elem = TextElement(
            x=x + width / 2,
            y=y + 15,
            orientation=0.0,
            scale=8.0,
            h_align=1,
            v_align=0,
            text=label,
            style_index=0
        )
        elements.append(ExtractedElement(
            elem_type=EANT_TEXT,
            element=label_elem,
            bbox=(x, y + 15, x + width, y + 25)
        ))

        units_elem = TextElement(
            x=x + width + 5,
            y=y + 5,
            orientation=0.0,
            scale=6.0,
            h_align=0,
            v_align=0,
            text=units,
            style_index=0
        )
        elements.append(ExtractedElement(
            elem_type=EANT_TEXT,
            element=units_elem,
            bbox=(x + width + 5, y, x + width + 50, y + 10)
        ))

        return elements


class NorthArrowBuilder:
    """Build a north arrow symbol element."""

    @staticmethod
    def build_north_arrow(x: float, y: float, size: float = 20.0) -> ExtractedElement:
        elem = SymbolElement(
            x=x,
            y=y,
            scale=size,
            rotation=0.0,
            symbol_index=0
        )
        return ExtractedElement(
            elem_type=EANT_SYMBOL,
            element=elem,
            bbox=(x - size/2, y - size/2, x + size/2, y + size/2)
        )