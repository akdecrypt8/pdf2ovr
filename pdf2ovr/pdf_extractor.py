"""High-fidelity, page-aware PDF content extraction using PyMuPDF.

The extractor preserves source drawing order, text runs, vector path style,
optional-content layer labels, clipping/group scopes, and embedded image
placements.  It creates an interchange model only: this module does not claim
that arbitrary PDF content can be serialized into ERDAS EANT annotations.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
import hashlib
import json
import math
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    import pymupdf
except ImportError:  # HFA inspection must remain usable without the PDF extra.
    pymupdf = None

from .element_types import (
    TextElement,
    RectangleElement,
    PolylineElement,
    SymbolElement,
    EllipseElement,
    GroupElement,
    Point,
    EANT_TEXT,
    EANT_RECTANGLE,
    EANT_POLYLINE,
    EANT_SYMBOL,
    EANT_ELLIPSE,
    EANT_GROUP,
)


@dataclass
class PDFStyle:
    """PDF appearance attributes retained without lossy EANT mapping."""

    stroke_color: Optional[Tuple[float, ...]] = None
    fill_color: Optional[Tuple[float, ...]] = None
    stroke_opacity: Optional[float] = None
    fill_opacity: Optional[float] = None
    line_width: Optional[float] = None
    dashes: Optional[str] = None
    line_cap: Optional[Tuple[int, ...]] = None
    line_join: Optional[float] = None
    even_odd: Optional[bool] = None
    blend_mode: Optional[str] = None
    group_opacity: float = 1.0
    font_name: Optional[str] = None
    font_size: Optional[float] = None
    font_flags: Optional[int] = None
    char_flags: Optional[int] = None
    text_render_mode: Optional[int] = None
    writing_mode: Optional[int] = None
    bidi_level: Optional[int] = None
    colorspace: Optional[int] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TextRunMetadata:
    """Text extraction details that do not fit the historic EANT TextElement."""

    font_name: str
    font_size: float
    font_flags: int
    char_flags: Optional[int]
    writing_mode: int
    bidi_level: int
    ascender: Optional[float]
    descender: Optional[float]
    render_mode: int
    characters: List[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ClipPathRecord:
    clip_id: str
    page_num: int
    level: int
    scissor_bbox: Optional[Tuple[float, float, float, float]]
    commands: List[dict]
    even_odd: Optional[bool]
    layer_name: Optional[str]

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TransparencyGroupRecord:
    group_id: str
    page_num: int
    level: int
    bbox: Tuple[float, float, float, float]
    isolated: bool
    knockout: bool
    blend_mode: str
    opacity: float
    layer_name: Optional[str]

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PDFLayerRecord:
    xref: int
    name: str
    visible: Optional[bool]
    intent: List[str] = field(default_factory=list)
    usage: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PDFLayerConfiguration:
    number: int
    name: str
    creator: str
    on_xrefs: List[int] = field(default_factory=list)
    off_xrefs: List[int] = field(default_factory=list)
    radio_groups: List[List[int]] = field(default_factory=list)
    base_state: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PDFPageMetadata:
    page_num: int
    width: float
    height: float
    rotation: int
    media_box: Tuple[float, float, float, float]
    crop_box: Tuple[float, float, float, float]
    label: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PDFImageAsset:
    """One unique embedded raster payload, optionally with a soft mask."""

    asset_id: str
    extension: str
    width: int
    height: int
    colorspace: Optional[int]
    bits_per_component: Optional[int]
    data: bytes = field(repr=False)
    soft_mask_data: Optional[bytes] = field(default=None, repr=False)
    source_xref: int = 0
    soft_mask_xref: int = 0
    digest: Optional[str] = None

    def to_dict(self, *, include_data: bool = False) -> dict:
        result = {
            "asset_id": self.asset_id,
            "extension": self.extension,
            "width": self.width,
            "height": self.height,
            "colorspace": self.colorspace,
            "bits_per_component": self.bits_per_component,
            "source_xref": self.source_xref,
            "soft_mask_xref": self.soft_mask_xref,
            "digest": self.digest,
            "data_bytes": len(self.data),
            "soft_mask_bytes": len(self.soft_mask_data) if self.soft_mask_data else 0,
        }
        if include_data:
            result["data_hex"] = self.data.hex()
            result["soft_mask_hex"] = self.soft_mask_data.hex() if self.soft_mask_data else None
        return result


@dataclass
class PDFImagePlacement:
    page_num: int
    paint_order: int
    asset_id: Optional[str]
    bbox: Tuple[float, float, float, float]
    quad: List[Tuple[float, float]]
    source_transform: Tuple[float, float, float, float, float, float]
    pixel_width: int
    pixel_height: int
    xref: int
    layer_name: Optional[str] = None
    optional_content_xref: int = 0
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ExtractedElement:
    """A normalized extracted PDF element plus fidelity metadata."""

    elem_type: int
    element: object
    bbox: Tuple[float, float, float, float]
    page_num: int = 0
    sequence: int = 0
    closed: bool = False
    paint_order: int = -1
    style: Optional[PDFStyle] = None
    layer_name: Optional[str] = None
    clip_stack: Tuple[str, ...] = ()
    group_stack: Tuple[str, ...] = ()
    path_metadata: Optional[dict] = None
    text_metadata: Optional[TextRunMetadata] = None
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        if is_dataclass(self.element):
            element_data = asdict(self.element)
        else:
            element_data = self.element
        return {
            "elem_type": self.elem_type,
            "element": element_data,
            "bbox": list(self.bbox),
            "page_num": self.page_num,
            "sequence": self.sequence,
            "paint_order": self.paint_order,
            "closed": self.closed,
            "style": self.style.to_dict() if self.style else None,
            "layer_name": self.layer_name,
            "clip_stack": list(self.clip_stack),
            "group_stack": list(self.group_stack),
            "path_metadata": self.path_metadata,
            "text_metadata": self.text_metadata.to_dict() if self.text_metadata else None,
            "warnings": list(self.warnings),
        }


@dataclass
class PDFExtractionResult:
    pages: List[PDFPageMetadata]
    elements: List[ExtractedElement]
    image_assets: Dict[str, PDFImageAsset]
    image_placements: List[PDFImagePlacement]
    clips: List[ClipPathRecord]
    groups: List[TransparencyGroupRecord]
    layers: List[PDFLayerRecord]
    layer_configurations: List[PDFLayerConfiguration]
    warnings: List[str]

    def summary(self) -> dict:
        type_counts: Dict[str, int] = defaultdict(int)
        for item in self.elements:
            type_counts[str(item.elem_type)] += 1
        return {
            "page_count": len(self.pages),
            "element_count": len(self.elements),
            "element_type_counts": dict(sorted(type_counts.items())),
            "image_asset_count": len(self.image_assets),
            "image_placement_count": len(self.image_placements),
            "clip_path_count": len(self.clips),
            "transparency_group_count": len(self.groups),
            "optional_content_group_count": len(self.layers),
            "layer_configuration_count": len(self.layer_configurations),
            "warning_count": len(self.warnings),
            "warnings": list(self.warnings),
        }

    def to_dict(self) -> dict:
        return {
            "schema_version": 1,
            "summary": self.summary(),
            "pages": [page.to_dict() for page in self.pages],
            "elements": [element.to_dict() for element in self.elements],
            "image_assets": [asset.to_dict() for asset in self.image_assets.values()],
            "image_placements": [placement.to_dict() for placement in self.image_placements],
            "clips": [clip.to_dict() for clip in self.clips],
            "groups": [group.to_dict() for group in self.groups],
            "layers": [layer.to_dict() for layer in self.layers],
            "layer_configurations": [item.to_dict() for item in self.layer_configurations],
        }


class PDFExtractor:
    """Extract text, vector geometry, styles, layers, clips and images."""

    def __init__(
        self,
        pdf_path: str,
        *,
        curve_tolerance: float = 0.35,
        include_images: bool = True,
    ):
        if pymupdf is None:
            raise RuntimeError(
                "PyMuPDF is required for PDF extraction. Install it with: pip install 'pdf2ovr[pdf]'"
            )
        if curve_tolerance <= 0 or not math.isfinite(curve_tolerance):
            raise ValueError("curve_tolerance must be a finite positive number")
        self.doc = pymupdf.open(pdf_path)
        if self.doc.is_closed:
            raise ValueError(f"Could not open PDF: {pdf_path}")
        if len(self.doc) == 0:
            self.doc.close()
            raise ValueError("PDF contains no pages")
        self.pdf_path = str(pdf_path)
        self.curve_tolerance = float(curve_tolerance)
        self.include_images = bool(include_images)
        self.page = self.doc[0]  # Backwards-compatible first page alias.
        self.page_width = float(self.page.rect.width)
        self.page_height = float(self.page.rect.height)
        self._warnings: List[str] = []

    @property
    def page_count(self) -> int:
        return len(self.doc)

    @staticmethod
    def _rect_tuple(rect: Any) -> Tuple[float, float, float, float]:
        return (float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1))

    @staticmethod
    def _point(page, point) -> Point:
        """Map unrotated page coordinates into displayed bottom-left coordinates."""
        p = pymupdf.Point(float(point.x), float(point.y)) * page.rotation_matrix
        return Point(float(p.x), float(page.rect.height - p.y))

    @classmethod
    def _bbox_from_points(cls, page, points: Iterable) -> Tuple[float, float, float, float]:
        mapped = [cls._point(page, point) for point in points]
        return cls._points_bbox(mapped)

    @staticmethod
    def _points_bbox(points: Sequence[Point]) -> Tuple[float, float, float, float]:
        if not points:
            return (0.0, 0.0, 0.0, 0.0)
        xs = [point.x for point in points]
        ys = [point.y for point in points]
        return (min(xs), min(ys), max(xs), max(ys))

    @staticmethod
    def _rgb_from_span_color(value: object) -> Tuple[float, float, float]:
        try:
            integer = int(value)
        except (TypeError, ValueError):
            return (0.0, 0.0, 0.0)
        return (
            ((integer >> 16) & 0xFF) / 255.0,
            ((integer >> 8) & 0xFF) / 255.0,
            (integer & 0xFF) / 255.0,
        )

    @staticmethod
    def _as_color(value: Any) -> Optional[Tuple[float, ...]]:
        if value is None:
            return None
        try:
            return tuple(float(component) for component in value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _bbox_distance(left: Sequence[float], right: Sequence[float]) -> float:
        return sum(abs(float(a) - float(b)) for a, b in zip(left, right))

    def _page_sequence_layers(self, page) -> Dict[int, Optional[str]]:
        try:
            bboxlog = page.get_bboxlog(layers=True)
        except TypeError:
            bboxlog = page.get_bboxlog()
        except Exception:
            return {}
        result: Dict[int, Optional[str]] = {}
        for sequence, record in enumerate(bboxlog):
            layer = record[2] if len(record) > 2 else None
            result[sequence] = str(layer) if layer else None
        return result

    def _text_span_candidates(self, page) -> List[dict]:
        candidates: List[dict] = []
        try:
            text_dict = page.get_text("dict")
        except Exception:
            return candidates
        for block in text_dict.get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    candidates.append(span)
        return candidates

    @staticmethod
    def _trace_text(trace: dict) -> str:
        chars = []
        for item in trace.get("chars", ()):
            try:
                codepoint = int(item[0])
                if 0 <= codepoint <= 0x10FFFF and not 0xD800 <= codepoint <= 0xDFFF:
                    chars.append(chr(codepoint))
                else:
                    chars.append("\uFFFD")
            except (TypeError, ValueError, IndexError):
                chars.append("\uFFFD")
        return "".join(chars)

    def _match_span_text(self, trace: dict, candidates: List[dict], used: set[int]) -> Optional[dict]:
        bbox = trace.get("bbox")
        if not bbox:
            return None
        best_index = None
        best_score = float("inf")
        for index, span in enumerate(candidates):
            if index in used:
                continue
            span_bbox = span.get("bbox")
            if not span_bbox:
                continue
            font_match = str(span.get("font", "")) == str(trace.get("font", ""))
            score = self._bbox_distance(bbox, span_bbox)
            if not font_match:
                score += 1000.0
            size_difference = abs(float(span.get("size", 0.0)) - float(trace.get("size", 0.0)))
            score += size_difference * 5.0
            if score < best_score:
                best_index, best_score = index, score
        if best_index is None or best_score > 1500:
            return None
        used.add(best_index)
        return candidates[best_index]

    def _extract_page_text(
        self, page, page_num: int, sequence_layers: Optional[Dict[int, Optional[str]]] = None
    ) -> List[ExtractedElement]:
        elements: List[ExtractedElement] = []
        sequence_layers = sequence_layers or {}
        candidates = self._text_span_candidates(page)
        used_candidates: set[int] = set()
        try:
            traces = page.get_texttrace()
        except Exception as exc:
            self._warnings.append(f"Page {page_num + 1}: text trace unavailable ({exc}); used basic text extraction")
            traces = []

        if traces:
            for trace_index, trace in enumerate(traces):
                text_span = self._match_span_text(trace, candidates, used_candidates)
                text = str(text_span.get("text", "")) if text_span else self._trace_text(trace)
                if not text or not text.strip():
                    continue
                chars = []
                for char in trace.get("chars", ()):
                    try:
                        origin = pymupdf.Point(*char[2]) * page.rotation_matrix
                        bbox = [float(v) for v in char[3]]
                        mapped_corners = [
                            self._point(page, pymupdf.Point(bbox[0], bbox[1])),
                            self._point(page, pymupdf.Point(bbox[2], bbox[1])),
                            self._point(page, pymupdf.Point(bbox[2], bbox[3])),
                            self._point(page, pymupdf.Point(bbox[0], bbox[3])),
                        ]
                        chars.append({
                            "unicode": int(char[0]),
                            "glyph_id": int(char[1]),
                            "origin": [float(origin.x), float(page.rect.height - origin.y)],
                            "bbox": list(self._points_bbox(mapped_corners)),
                        })
                    except (TypeError, ValueError, IndexError):
                        continue

                bbox_raw = trace.get("bbox") or (0, 0, 0, 0)
                corners = [
                    pymupdf.Point(bbox_raw[0], bbox_raw[1]),
                    pymupdf.Point(bbox_raw[2], bbox_raw[1]),
                    pymupdf.Point(bbox_raw[2], bbox_raw[3]),
                    pymupdf.Point(bbox_raw[0], bbox_raw[3]),
                ]
                mapped_corners = [self._point(page, point) for point in corners]
                mapped_bbox = self._points_bbox(mapped_corners)

                origin_raw = trace.get("chars", ((0, 0, (bbox_raw[0], bbox_raw[3]), bbox_raw),))[0][2]
                baseline_raw = pymupdf.Point(*origin_raw) * page.rotation_matrix
                baseline = Point(float(baseline_raw.x), float(page.rect.height - baseline_raw.y))
                direction = trace.get("dir", (1.0, 0.0))
                direction_start = pymupdf.Point(*origin_raw) * page.rotation_matrix
                direction_end = pymupdf.Point(
                    float(origin_raw[0]) + float(direction[0]),
                    float(origin_raw[1]) + float(direction[1]),
                ) * page.rotation_matrix
                orientation = math.degrees(math.atan2(
                    -(direction_end.y - direction_start.y),
                    direction_end.x - direction_start.x,
                ))

                font_size = float(trace.get("size", 10.0))
                font_flags = int(trace.get("flags", 0))
                char_flags = int(text_span.get("char_flags", 0)) if text_span else None
                render_mode = int(trace.get("type", 0))
                opacity = float(trace.get("opacity", 1.0))
                color = self._as_color(trace.get("color"))
                style = PDFStyle(
                    stroke_color=color if render_mode in (1, 2) else None,
                    fill_color=color if render_mode in (0, 2) else None,
                    stroke_opacity=opacity if render_mode in (1, 2) else None,
                    fill_opacity=opacity if render_mode in (0, 2) else None,
                    line_width=float(trace.get("linewidth", 0.0)),
                    font_name=str(trace.get("font", "")),
                    font_size=font_size,
                    font_flags=font_flags,
                    char_flags=char_flags,
                    text_render_mode=render_mode,
                    writing_mode=int(trace.get("wmode", 0)),
                    bidi_level=int(trace.get("bidi_lvl", 0)),
                    colorspace=int(trace.get("colorspace", 0)),
                )
                metadata = TextRunMetadata(
                    font_name=str(trace.get("font", "")),
                    font_size=font_size,
                    font_flags=font_flags,
                    char_flags=char_flags,
                    writing_mode=int(trace.get("wmode", 0)),
                    bidi_level=int(trace.get("bidi_lvl", 0)),
                    ascender=float(trace.get("ascender", 0.0)),
                    descender=float(trace.get("descender", 0.0)),
                    render_mode=render_mode,
                    characters=chars,
                )
                paint_order = int(trace.get("seqno", trace_index))
                elem = TextElement(
                    x=baseline.x,
                    y=baseline.y,
                    orientation=orientation,
                    scale=font_size,
                    h_align=0,
                    v_align=0,
                    text=text,
                    style_index=0,
                )
                elements.append(ExtractedElement(
                    elem_type=EANT_TEXT,
                    element=elem,
                    bbox=mapped_bbox,
                    page_num=page_num,
                    sequence=paint_order,
                    paint_order=paint_order,
                    style=style,
                    layer_name=(trace.get("layer") or sequence_layers.get(paint_order)),
                    text_metadata=metadata,
                ))
            return elements

        # Compatibility fallback for older PyMuPDF versions without get_texttrace.
        text_dict = page.get_text("dict")
        fallback_seq = 0
        for block in text_dict.get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                direction = line.get("dir", (1.0, 0.0))
                for span in line.get("spans", []):
                    text = span.get("text", "")
                    if not text or not text.strip():
                        continue
                    x0, y0, x1, y1 = (float(v) for v in span["bbox"])
                    mapped_bbox = self._bbox_from_points(page, [
                        pymupdf.Point(x0, y0), pymupdf.Point(x1, y0),
                        pymupdf.Point(x1, y1), pymupdf.Point(x0, y1),
                    ])
                    origin = span.get("origin", (x0, y1))
                    baseline_raw = pymupdf.Point(*origin) * page.rotation_matrix
                    baseline = Point(baseline_raw.x, page.rect.height - baseline_raw.y)
                    dir_start = pymupdf.Point(*origin) * page.rotation_matrix
                    dir_end = pymupdf.Point(origin[0] + direction[0], origin[1] + direction[1]) * page.rotation_matrix
                    orientation = math.degrees(math.atan2(-(dir_end.y-dir_start.y), dir_end.x-dir_start.x))
                    color = self._rgb_from_span_color(span.get("color", 0))
                    font_size = float(span.get("size", 10.0))
                    style = PDFStyle(
                        fill_color=color,
                        fill_opacity=float(span.get("alpha", 255)) / 255.0,
                        font_name=str(span.get("font", "")),
                        font_size=font_size,
                        font_flags=int(span.get("flags", 0)),
                        char_flags=int(span.get("char_flags", 0)),
                    )
                    elem = TextElement(baseline.x, baseline.y, orientation, font_size, 0, 0, text, 0)
                    elements.append(ExtractedElement(
                        EANT_TEXT, elem, mapped_bbox, page_num,
                        fallback_seq, False, fallback_seq, style,
                        sequence_layers.get(fallback_seq),
                    ))
                    fallback_seq += 1
        return elements

    @staticmethod
    def _distance_to_line(point: Point, start: Point, end: Point) -> float:
        dx, dy = end.x - start.x, end.y - start.y
        length = math.hypot(dx, dy)
        if length <= 1e-12:
            return math.hypot(point.x - start.x, point.y - start.y)
        return abs(dy * point.x - dx * point.y + end.x * start.y - end.y * start.x) / length

    @classmethod
    def _flatten_cubic(
        cls, p0: Point, p1: Point, p2: Point, p3: Point,
        tolerance: float, depth: int = 0, max_depth: int = 16,
    ) -> List[Point]:
        """Approximate one cubic Bezier with adaptive de Casteljau subdivision."""
        flatness = max(
            cls._distance_to_line(p1, p0, p3),
            cls._distance_to_line(p2, p0, p3),
        )
        if flatness <= tolerance or depth >= max_depth:
            return [p0, p3]
        p01 = Point((p0.x+p1.x)/2, (p0.y+p1.y)/2)
        p12 = Point((p1.x+p2.x)/2, (p1.y+p2.y)/2)
        p23 = Point((p2.x+p3.x)/2, (p2.y+p3.y)/2)
        p012 = Point((p01.x+p12.x)/2, (p01.y+p12.y)/2)
        p123 = Point((p12.x+p23.x)/2, (p12.y+p23.y)/2)
        mid = Point((p012.x+p123.x)/2, (p012.y+p123.y)/2)
        left = cls._flatten_cubic(p0, p01, p012, mid, tolerance, depth+1, max_depth)
        right = cls._flatten_cubic(mid, p123, p23, p3, tolerance, depth+1, max_depth)
        return left[:-1] + right

    @staticmethod
    def _quantized(point: Point, tolerance: float) -> Tuple[int, int]:
        return (round(point.x / tolerance), round(point.y / tolerance))

    @classmethod
    def _join_segments(
        cls, segments: Sequence[Tuple[Point, Point]], tolerance: float = 0.01
    ) -> List[Tuple[List[Point], bool]]:
        """Join independent endpoint-connected segments in near-linear time.

        Kept for compatibility with earlier callers. The fidelity extractor
        itself preserves source path command order and does not join across
        distinct PDF path objects.
        """
        if not segments:
            return []
        adjacency: Dict[Tuple[int, int], List[int]] = defaultdict(list)
        for index, (start, end) in enumerate(segments):
            adjacency[cls._quantized(start, tolerance)].append(index)
            adjacency[cls._quantized(end, tolerance)].append(index)
        used = [False] * len(segments)
        paths: List[Tuple[List[Point], bool]] = []
        for start_index, (start, end) in enumerate(segments):
            if used[start_index]:
                continue
            used[start_index] = True
            path = deque([start, end])

            def extend(at_end: bool) -> None:
                while True:
                    anchor = path[-1] if at_end else path[0]
                    key = cls._quantized(anchor, tolerance)
                    candidates = [index for index in adjacency.get(key, ()) if not used[index]]
                    if not candidates:
                        break
                    candidate = min(candidates)
                    seg_start, seg_end = segments[candidate]
                    next_point = seg_end if cls._quantized(seg_start, tolerance) == key else seg_start
                    used[candidate] = True
                    if at_end:
                        path.append(next_point)
                    else:
                        path.appendleft(next_point)
            extend(True)
            extend(False)
            closed = len(path) >= 4 and cls._quantized(path[0], tolerance) == cls._quantized(path[-1], tolerance)
            paths.append((list(path), closed))
        return paths

    @staticmethod
    def _point_close(a: Point, b: Point, tolerance: float = 0.02) -> bool:
        return math.hypot(a.x - b.x, a.y - b.y) <= tolerance

    def _map_drawing_point(self, page, value: Any) -> Point:
        if hasattr(value, "x") and hasattr(value, "y"):
            point = value
        else:
            point = pymupdf.Point(float(value[0]), float(value[1]))
        return self._point(page, point)

    def _decode_path_items(self, page, drawing: dict) -> Tuple[List[List[Point]], List[dict], List[str]]:
        """Return ordered subpaths, source commands, and unsupported command warnings."""
        subpaths: List[List[Point]] = []
        commands: List[dict] = []
        warnings: List[str] = []
        current: List[Point] = []

        def finish_current(force_closed: bool = False) -> None:
            nonlocal current
            if len(current) >= 2:
                if force_closed and not self._point_close(current[0], current[-1]):
                    current.append(current[0])
                subpaths.append(current)
            current = []

        for item_index, item in enumerate(drawing.get("items", [])):
            if not isinstance(item, (list, tuple)) or not item:
                warnings.append(f"Unknown drawing item at index {item_index}")
                continue
            kind = item[0]
            if kind == "l" and len(item) >= 3:
                p0, p1 = self._map_drawing_point(page, item[1]), self._map_drawing_point(page, item[2])
                commands.append({"type": "line", "points": [[p0.x,p0.y],[p1.x,p1.y]]})
                if not current:
                    current = [p0, p1]
                elif self._point_close(current[-1], p0):
                    current.append(p1)
                else:
                    finish_current()
                    current = [p0, p1]
            elif kind == "c" and len(item) >= 5:
                points = [self._map_drawing_point(page, value) for value in item[1:5]]
                commands.append({"type": "cubic_bezier", "points": [[p.x,p.y] for p in points]})
                if not current:
                    current = [points[0]]
                elif not self._point_close(current[-1], points[0]):
                    finish_current()
                    current = [points[0]]
                flattened = self._flatten_cubic(*points, self.curve_tolerance)
                current.extend(flattened[1:])
            elif kind == "re" and len(item) >= 2:
                finish_current()
                rect = item[1]
                corners = [
                    pymupdf.Point(rect.x0, rect.y0), pymupdf.Point(rect.x1, rect.y0),
                    pymupdf.Point(rect.x1, rect.y1), pymupdf.Point(rect.x0, rect.y1),
                    pymupdf.Point(rect.x0, rect.y0),
                ]
                mapped = [self._point(page, value) for value in corners]
                orientation = int(item[2]) if len(item) > 2 else 0
                if orientation < 0:
                    mapped = [mapped[0], *reversed(mapped[1:4]), mapped[0]]
                current = mapped
                commands.append({"type": "rectangle", "points": [[p.x,p.y] for p in mapped], "orientation": orientation})
                finish_current()
            elif kind == "qu" and len(item) >= 2:
                finish_current()
                quad = item[1]
                raw_points = [quad.ul, quad.ur, quad.lr, quad.ll, quad.ul] if hasattr(quad, "ul") else list(quad) + [quad[0]]
                mapped = [self._map_drawing_point(page, value) for value in raw_points]
                commands.append({"type": "quadrilateral", "points": [[p.x,p.y] for p in mapped]})
                current = mapped
                finish_current()
            else:
                warnings.append(f"Unsupported PDF path command {kind!r} at index {item_index}")
        finish_current(bool(drawing.get("closePath", False)))

        # Normalize nearly closed paths and preserve explicit closure.
        for path in subpaths:
            if len(path) >= 3 and self._point_close(path[0], path[-1]):
                path[-1] = Point(path[0].x, path[0].y)
        return subpaths, commands, warnings

    @staticmethod
    def _rect_corners(rect) -> List[Any]:
        return [
            pymupdf.Point(rect.x0, rect.y0), pymupdf.Point(rect.x1, rect.y0),
            pymupdf.Point(rect.x1, rect.y1), pymupdf.Point(rect.x0, rect.y1),
        ]

    def _drawing_style(self, drawing: dict, active_groups: Sequence[TransparencyGroupRecord]) -> PDFStyle:
        group_opacity = math.prod(max(0.0, min(1.0, group.opacity)) for group in active_groups) if active_groups else 1.0
        blend_mode = active_groups[-1].blend_mode if active_groups else None
        return PDFStyle(
            stroke_color=self._as_color(drawing.get("color")),
            fill_color=self._as_color(drawing.get("fill")),
            stroke_opacity=(float(drawing["stroke_opacity"]) if drawing.get("stroke_opacity") is not None else None),
            fill_opacity=(float(drawing["fill_opacity"]) if drawing.get("fill_opacity") is not None else None),
            line_width=(float(drawing["width"]) if drawing.get("width") is not None else None),
            dashes=(str(drawing["dashes"]) if drawing.get("dashes") is not None else None),
            line_cap=(tuple(int(v) for v in drawing["lineCap"]) if drawing.get("lineCap") is not None else None),
            line_join=(float(drawing["lineJoin"]) if drawing.get("lineJoin") is not None else None),
            even_odd=(bool(drawing["even_odd"]) if drawing.get("even_odd") is not None else None),
            blend_mode=blend_mode,
            group_opacity=group_opacity,
        )

    def _extract_page_drawings(self, page, page_num: int) -> Tuple[List[ExtractedElement], List[ClipPathRecord], List[TransparencyGroupRecord]]:
        elements: List[ExtractedElement] = []
        clips: List[ClipPathRecord] = []
        groups: List[TransparencyGroupRecord] = []
        try:
            drawings = page.get_drawings(extended=True)
        except TypeError:
            drawings = page.get_drawings()
            self._warnings.append(f"Page {page_num + 1}: extended drawing extraction unavailable; clipping/group scopes not captured")
        except Exception as exc:
            raise RuntimeError(f"Could not extract vector drawings from PDF page {page_num + 1}") from exc

        context_stack: List[Tuple[int, str, str, Optional[ClipPathRecord], Optional[TransparencyGroupRecord]]] = []
        for drawing_index, drawing in enumerate(drawings):
            level = int(drawing.get("level", 0))
            while context_stack and context_stack[-1][0] >= level:
                context_stack.pop()
            kind = drawing.get("type")
            layer_name = str(drawing.get("layer")) if drawing.get("layer") else None

            if kind == "clip":
                clip_id = f"p{page_num + 1}-clip{len(clips) + 1}"
                commands = []
                for item in drawing.get("items", []):
                    if not item:
                        continue
                    values = []
                    for value in item[1:]:
                        if hasattr(value, "x") and hasattr(value, "y"):
                            values.append([float(value.x), float(value.y)])
                        elif hasattr(value, "ul"):
                            values.append([[float(p.x), float(p.y)] for p in (value.ul, value.ur, value.lr, value.ll)])
                        elif hasattr(value, "x0"):
                            values.append([float(value.x0),float(value.y0),float(value.x1),float(value.y1)])
                        else:
                            values.append(value)
                    commands.append({"type": str(item[0]), "values": values})
                scissor = drawing.get("scissor") or drawing.get("rect")
                scissor_bbox = None
                if scissor is not None:
                    scissor_bbox = self._bbox_from_points(page, self._rect_corners(scissor))
                # Clip command coordinates are normalized to the same displayed,
                # bottom-left page space as extracted annotation geometry.
                normalized_commands = []
                for command in commands:
                    normalized_values = []
                    for value in command.get("values", []):
                        if isinstance(value, list) and len(value) == 2 and all(isinstance(v, (int, float)) for v in value):
                            point = self._map_drawing_point(page, value)
                            normalized_values.append([point.x, point.y])
                        elif isinstance(value, list) and value and isinstance(value[0], list):
                            points = [self._map_drawing_point(page, point) for point in value]
                            normalized_values.append([[point.x, point.y] for point in points])
                        elif isinstance(value, list) and len(value) == 4 and all(isinstance(v, (int, float)) for v in value):
                            x0, y0, x1, y1 = value
                            normalized_values.append(list(self._bbox_from_points(page, [
                                pymupdf.Point(x0, y0), pymupdf.Point(x1, y0),
                                pymupdf.Point(x1, y1), pymupdf.Point(x0, y1),
                            ])))
                        else:
                            normalized_values.append(value)
                    normalized_commands.append({"type": command["type"], "values": normalized_values})
                clip = ClipPathRecord(
                    clip_id=clip_id, page_num=page_num, level=level,
                    scissor_bbox=scissor_bbox,
                    commands=normalized_commands,
                    even_odd=(bool(drawing.get("even_odd")) if drawing.get("even_odd") is not None else None),
                    layer_name=layer_name,
                )
                clips.append(clip)
                context_stack.append((level, "clip", clip_id, clip, None))
                continue

            if kind == "group":
                rect = drawing.get("rect")
                group_id = f"p{page_num + 1}-group{len(groups) + 1}"
                group = TransparencyGroupRecord(
                    group_id=group_id,
                    page_num=page_num,
                    level=level,
                    bbox=(self._bbox_from_points(page, self._rect_corners(rect)) if rect is not None else (0.0,0.0,0.0,0.0)),
                    isolated=bool(drawing.get("isolated", False)),
                    knockout=bool(drawing.get("knockout", False)),
                    blend_mode=str(drawing.get("blendmode", "Normal")),
                    opacity=float(drawing.get("opacity", 1.0)),
                    layer_name=layer_name,
                )
                groups.append(group)
                context_stack.append((level, "group", group_id, None, group))
                continue

            if not drawing.get("items"):
                continue
            subpaths, commands, command_warnings = self._decode_path_items(page, drawing)
            active_clips = tuple(entry[2] for entry in context_stack if entry[1] == "clip")
            active_groups = [entry[4] for entry in context_stack if entry[1] == "group" and entry[4] is not None]
            style = self._drawing_style(drawing, active_groups)
            paint_order = int(drawing.get("seqno", drawing_index))
            all_points = [point for subpath in subpaths for point in subpath]
            bbox_all = self._points_bbox(all_points)
            closed_count = sum(1 for path in subpaths if len(path) >= 4 and self._point_close(path[0], path[-1]))
            path_metadata = {
                "path_type": kind,
                "source_commands": commands,
                "subpath_count": len(subpaths),
                "subpath_index": None,
                "close_path": bool(drawing.get("closePath", False)),
                "fill_rule": "evenodd" if drawing.get("even_odd") else "nonzero",
                "has_stroke": drawing.get("color") is not None,
                "has_fill": drawing.get("fill") is not None,
                "bbox": list(bbox_all),
            }
            for subpath_index, points in enumerate(subpaths):
                if len(points) < 2:
                    continue
                bbox = self._points_bbox(points)
                closed = len(points) >= 4 and self._point_close(points[0], points[-1])
                path_metadata_item = dict(path_metadata)
                path_metadata_item["subpath_index"] = subpath_index
                if len(commands) == 1 and commands[0]["type"] == "rectangle":
                    x0,y0,x1,y1 = bbox
                    elem = RectangleElement(x0, y0, x1-x0, y1-y0, style_index=0)
                    elem_type = EANT_RECTANGLE
                else:
                    elem = PolylineElement(points=list(points), style_index=0, closed=closed)
                    elem_type = EANT_POLYLINE
                warnings = list(command_warnings)
                if style.fill_color is not None and len(subpaths) > 1:
                    warnings.append("Compound filled path has multiple subpaths; hole/fill-rule semantics are retained as metadata, not converted to independent EANT fills")
                elements.append(ExtractedElement(
                    elem_type=elem_type,
                    element=elem,
                    bbox=bbox,
                    page_num=page_num,
                    sequence=paint_order,
                    closed=closed,
                    paint_order=paint_order,
                    style=style,
                    layer_name=layer_name,
                    clip_stack=active_clips,
                    group_stack=tuple(entry[2] for entry in context_stack if entry[1] == "group"),
                    path_metadata=path_metadata_item,
                    warnings=warnings,
                ))
                self._warnings.extend(f"Page {page_num + 1}: {warning}" for warning in command_warnings)
        return elements, clips, groups

    @staticmethod
    def _image_block_candidates(page) -> List[dict]:
        candidates = []
        try:
            page_dict = page.get_text("dict")
        except Exception:
            return candidates
        for block in page_dict.get("blocks", []):
            if block.get("type") == 1 and block.get("image"):
                candidates.append(block)
        return candidates

    def _extract_page_images(self, page, page_num: int, sequence_layers: Dict[int, Optional[str]], assets: Dict[str, PDFImageAsset]) -> List[PDFImagePlacement]:
        placements: List[PDFImagePlacement] = []
        try:
            infos = page.get_image_info(hashes=True, xrefs=True)
        except Exception as exc:
            self._warnings.append(f"Page {page_num + 1}: image occurrence scan failed ({exc})")
            return placements
        raw_blocks = self._image_block_candidates(page)
        bboxlog = []
        try:
            bboxlog = page.get_bboxlog(layers=True)
        except Exception:
            try:
                bboxlog = page.get_bboxlog()
            except Exception:
                pass
        image_order_records = []
        for order, record in enumerate(bboxlog):
            if record and record[0] in ("fill-image", "fill-image-mask"):
                image_order_records.append((order, record))
        used_order: set[int] = set()

        for info_index, info in enumerate(infos):
            bbox_raw = tuple(float(value) for value in info.get("bbox", (0,0,0,0)))
            transform = tuple(float(value) for value in info.get("transform", (1,0,0,1,0,0)))
            if len(transform) != 6:
                transform = (1,0,0,1,0,0)
            rect = pymupdf.Rect(*bbox_raw)
            mapped_bbox_points = [self._point(page, point) for point in self._rect_corners(rect)]
            mapped_bbox = self._points_bbox(mapped_bbox_points)
            # Preserve the full affine placement, not just the axis-aligned bbox.
            matrix = pymupdf.Matrix(*transform)
            unit_points = [pymupdf.Point(0,0), pymupdf.Point(1,0), pymupdf.Point(1,1), pymupdf.Point(0,1)]
            mapped_quad = []
            for source_point in unit_points:
                placed = source_point * matrix
                bottom_left = self._point(page, placed)
                mapped_quad.append((bottom_left.x, bottom_left.y))

            best_order = -1
            best_score = float("inf")
            for order, record in image_order_records:
                if order in used_order:
                    continue
                candidate_bbox = record[1] if len(record) > 1 else (0,0,0,0)
                score = self._bbox_distance(bbox_raw, candidate_bbox)
                if score < best_score:
                    best_score, best_order = score, order
            if best_order >= 0 and best_score <= 2.0:
                used_order.add(best_order)
                paint_order = best_order
                record = bboxlog[best_order]
                layer_name = str(record[2]) if len(record) > 2 and record[2] else None
            else:
                paint_order = int(info.get("number", info_index))
                layer_name = sequence_layers.get(paint_order)

            xref = int(info.get("xref", 0) or 0)
            ext = ""
            data: Optional[bytes] = None
            soft_mask_data: Optional[bytes] = None
            smask_xref = 0
            image_width = int(info.get("width", 0))
            image_height = int(info.get("height", 0))
            colorspace = int(info.get("colorspace", 0)) if info.get("colorspace") is not None else None
            bpc = int(info.get("bpc", 0)) if info.get("bpc") is not None else None
            if xref > 0:
                try:
                    extracted = self.doc.extract_image(xref)
                    data = extracted.get("image")
                    ext = extracted.get("ext", "")
                    image_width = int(extracted.get("width", image_width))
                    image_height = int(extracted.get("height", image_height))
                    colorspace = int(extracted.get("colorspace", colorspace or 0))
                    bpc = int(extracted.get("bpc", bpc or 0))
                    for image_entry in page.get_images(full=True):
                        if int(image_entry[0]) == xref:
                            smask_xref = int(image_entry[1] or 0)
                            break
                    if smask_xref > 0:
                        try:
                            mask = self.doc.extract_image(smask_xref)
                            soft_mask_data = mask.get("image")
                        except Exception as mask_exc:
                            self._warnings.append(f"Page {page_num + 1}: soft mask {smask_xref} could not be extracted ({mask_exc})")
                except Exception as exc:
                    self._warnings.append(f"Page {page_num + 1}: image xref {xref} could not be extracted ({exc})")
            else:
                # Inline images have no xref; recover bytes from the matching image block when possible.
                for block in raw_blocks:
                    block_bbox = block.get("bbox", (0,0,0,0))
                    if self._bbox_distance(bbox_raw, block_bbox) <= 2.0 and int(block.get("width", 0)) == image_width and int(block.get("height", 0)) == image_height:
                        data = block.get("image")
                        ext = str(block.get("ext", ""))
                        break

            asset_id = None
            if data:
                digest = hashlib.sha256(data + (soft_mask_data or b"")).hexdigest()
                asset_id = f"img-{digest[:20]}"
                if asset_id not in assets:
                    assets[asset_id] = PDFImageAsset(
                        asset_id=asset_id,
                        extension=ext or "bin",
                        width=image_width,
                        height=image_height,
                        colorspace=colorspace,
                        bits_per_component=bpc,
                        data=bytes(data),
                        soft_mask_data=bytes(soft_mask_data) if soft_mask_data else None,
                        source_xref=xref,
                        soft_mask_xref=smask_xref,
                        digest=digest,
                    )
            else:
                self._warnings.append(f"Page {page_num + 1}: image occurrence at {mapped_bbox} has no recoverable embedded bytes (xref={xref})")

            optional_content_xref = 0
            if xref > 0:
                try:
                    optional_content_xref = int(self.doc.get_oc(xref) or 0)
                except Exception:
                    optional_content_xref = 0
            placement_warnings = []
            if not layer_name and optional_content_xref:
                placement_warnings.append("Image is attached to optional content, but its layer name could not be resolved")
            placements.append(PDFImagePlacement(
                page_num=page_num,
                paint_order=paint_order,
                asset_id=asset_id,
                bbox=mapped_bbox,
                quad=mapped_quad,
                source_transform=transform,
                pixel_width=image_width,
                pixel_height=image_height,
                xref=xref,
                layer_name=layer_name,
                optional_content_xref=optional_content_xref,
                warnings=placement_warnings,
            ))
        return placements

    def _extract_layers(self) -> Tuple[List[PDFLayerRecord], List[PDFLayerConfiguration]]:
        layers: List[PDFLayerRecord] = []
        configurations: List[PDFLayerConfiguration] = []
        try:
            ocgs = self.doc.get_ocgs() or {}
            default = self.doc.get_layer(-1) or {}
        except Exception:
            return layers, configurations
        default_on = set(int(v) for v in default.get("on", []))
        default_off = set(int(v) for v in default.get("off", []))
        base_state = str(default.get("basestate", "ON")).upper()
        for xref, item in ocgs.items():
            xref = int(xref)
            if xref in default_on:
                visible: Optional[bool] = True
            elif xref in default_off:
                visible = False
            else:
                # If the configuration does not explicitly override this OCG,
                # use its declared/default state exposed by PyMuPDF.
                declared_on = item.get("on")
                visible = bool(declared_on) if declared_on is not None else base_state != "OFF"
            layers.append(PDFLayerRecord(
                xref=xref,
                name=str(item.get("name", f"OCG {xref}")),
                visible=visible,
                intent=[str(value) for value in item.get("intent", [])],
                usage=(str(item.get("usage")) if item.get("usage") else None),
            ))
        try:
            configs = self.doc.get_layers()
        except Exception:
            configs = []
        for item in configs:
            number = int(item.get("number", -1))
            try:
                state = self.doc.get_layer(number)
            except Exception:
                state = {}
            configurations.append(PDFLayerConfiguration(
                number=number,
                name=str(item.get("name", "")),
                creator=str(item.get("creator", "")),
                on_xrefs=[int(v) for v in state.get("on", [])],
                off_xrefs=[int(v) for v in state.get("off", [])],
                radio_groups=[[int(v) for v in group] for group in state.get("rbgroups", [])],
                base_state=(str(state.get("basestate")) if state.get("basestate") is not None else None),
            ))
        return layers, configurations

    def extract_all(self) -> List[ExtractedElement]:
        elements: List[ExtractedElement] = []
        for page_num in range(len(self.doc)):
            page = self.doc[page_num]
            sequence_layers = self._page_sequence_layers(page)
            text_elements = self._extract_page_text(page, page_num, sequence_layers)
            drawing_elements, _, _ = self._extract_page_drawings(page, page_num)
            combined = text_elements + drawing_elements
            combined.sort(key=lambda item: (item.paint_order, item.sequence))
            for item in combined:
                elements.append(item)
        return elements

    def extract_image_occurrences(
        self, image_assets: Optional[Dict[str, PDFImageAsset]] = None
    ) -> Tuple[Dict[str, PDFImageAsset], List[PDFImagePlacement]]:
        assets = image_assets if image_assets is not None else {}
        placements: List[PDFImagePlacement] = []
        if not self.include_images:
            return assets, placements
        for page_num in range(len(self.doc)):
            page = self.doc[page_num]
            placements.extend(self._extract_page_images(
                page, page_num, self._page_sequence_layers(page), assets
            ))
        placements.sort(key=lambda item: (item.page_num, item.paint_order))
        return assets, placements

    def extract_document(self) -> PDFExtractionResult:
        self._warnings = []
        pages: List[PDFPageMetadata] = []
        for page_num in range(len(self.doc)):
            page = self.doc[page_num]
            pages.append(PDFPageMetadata(
                page_num=page_num,
                width=float(page.rect.width),
                height=float(page.rect.height),
                rotation=int(page.rotation),
                media_box=self._rect_tuple(page.mediabox),
                crop_box=self._rect_tuple(page.cropbox),
                label=(page.get_label() or None),
            ))
        elements: List[ExtractedElement] = []
        clips: List[ClipPathRecord] = []
        groups: List[TransparencyGroupRecord] = []
        for page_num in range(len(self.doc)):
            page = self.doc[page_num]
            sequence_layers = self._page_sequence_layers(page)
            page_elements = self._extract_page_text(page, page_num, sequence_layers)
            page_drawings, page_clips, page_groups = self._extract_page_drawings(page, page_num)
            page_elements.extend(page_drawings)
            page_elements.sort(key=lambda item: (item.paint_order, item.sequence))
            elements.extend(page_elements)
            clips.extend(page_clips)
            groups.extend(page_groups)
            try:
                bboxlog = page.get_bboxlog()
                known_types = {"fill-text", "stroke-text", "ignore-text", "fill-path", "stroke-path", "fill-image", "fill-image-mask", "clip-path", "group"}
                for record in bboxlog:
                    if record and record[0] not in known_types:
                        self._warnings.append(f"Page {page_num + 1}: PDF paint operation {record[0]!r} is not represented by the fidelity extractor")
                    if record and record[0] in ("fill-shade", "stroke-path"):
                        if record[0] == "fill-shade":
                            self._warnings.append(f"Page {page_num + 1}: gradient/shading paint operation is not vectorized")
            except Exception:
                pass
            if page_clips:
                self._warnings.append(f"Page {page_num + 1}: clipping paths were captured for vector paths; full text/image clip semantics are not yet reconstructed")
        elements.sort(key=lambda item: (item.page_num, item.paint_order, item.sequence))
        assets, image_placements = self.extract_image_occurrences()
        layers, layer_configs = self._extract_layers()
        if layers:
            inactive_names = [layer.name for layer in layers if layer.visible is False]
            self._warnings.append("Optional-content groups and configurations are inventoried; object-to-layer labels are retained. PyMuPDF extraction follows the active PDF visibility configuration, so geometry/text inside OFF groups may not be returned. Inactive groups: " + (", ".join(inactive_names) if inactive_names else "none recorded"))
        return PDFExtractionResult(
            pages=pages,
            elements=elements,
            image_assets=assets,
            image_placements=image_placements,
            clips=clips,
            groups=groups,
            layers=layers,
            layer_configurations=layer_configs,
            warnings=sorted(set(self._warnings)),
        )

    def extract_images(self) -> List[Tuple[bytes, str, Tuple[float, float, float, float]]]:
        """Backwards-compatible helper returning first-page image bytes and bboxes.

        New code should use ``extract_image_occurrences`` or ``extract_document``
        to retain every page, affine placement, layer, and soft mask.
        """
        page = self.page
        result: List[Tuple[bytes, str, Tuple[float, float, float, float]]] = []
        temp_assets: Dict[str, PDFImageAsset] = {}
        for placement in self._extract_page_images(page, 0, self._page_sequence_layers(page), temp_assets):
            if placement.asset_id and placement.asset_id in temp_assets:
                asset = temp_assets[placement.asset_id]
                result.append((asset.data, asset.extension, placement.bbox))
        return result

    def close(self) -> None:
        if self.doc and not self.doc.is_closed:
            self.doc.close()


class ScaleBarBuilder:
    """Build a basic scale bar from normalized vector primitives."""

    @staticmethod
    def build_scale_bar(
        x: float, y: float, width: float, num_segments: int = 4,
        units: str = "Meters", label: str = "Scale",
    ) -> List[ExtractedElement]:
        if num_segments <= 0 or width <= 0:
            raise ValueError("width and num_segments must be positive")
        elements: List[ExtractedElement] = []
        segment_width = width / num_segments
        group = GroupElement(group_type=3)
        elements.append(ExtractedElement(EANT_GROUP, group, (x,y,x+width,y+25)))
        for index in range(num_segments):
            seg_x = x + index * segment_width
            elem = RectangleElement(seg_x, y, segment_width, 10, style_index=index % 2)
            elements.append(ExtractedElement(EANT_RECTANGLE, elem, (seg_x,y,seg_x+segment_width,y+10)))
        label_elem = TextElement(x+width/2,y+15,0.0,8.0,1,0,label,0)
        elements.append(ExtractedElement(EANT_TEXT,label_elem,(x,y+15,x+width,y+25)))
        units_elem = TextElement(x+width+5,y+5,0.0,6.0,0,0,units,0)
        elements.append(ExtractedElement(EANT_TEXT,units_elem,(x+width+5,y,x+width+50,y+10)))
        return elements


class NorthArrowBuilder:
    """Build a basic north arrow from vector primitives."""

    @staticmethod
    def build_north_arrow(x: float, y: float, size: float = 30.0) -> List[ExtractedElement]:
        if size <= 0:
            raise ValueError("size must be positive")
        points = [Point(x,y), Point(x+size/2,y+size), Point(x+size,y), Point(x+size/2,y+size/3), Point(x,y)]
        return [ExtractedElement(EANT_POLYLINE, PolylineElement(points,0,True),(x,y,x+size,y+size),closed=True)]
