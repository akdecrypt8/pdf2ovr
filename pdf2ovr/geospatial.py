"""Geospatial PDF inspection, page-to-map transforms, and GCP calibration.

Supported embedded georeferencing encodings:
- Adobe/ISO 32000 geospatial viewports: page /VP -> /Measure /GEO.
- OGC PDF georegistration 2.2: page /LGIDict map frames.

All embedded page coordinates are represented in PDF default user space
(bottom-left origin, before page /Rotate is applied). This is deliberately
separate from PyMuPDF's displayed-page coordinate space; explicit adapters are
provided for conversion between those spaces.

This module does not automatically map GeoPDF coordinates and bounding boxes
into the EANT coordinate spaces used by an annotation template. Phase 5 can
create selected schema-templated EANT records when the caller supplies those
coordinates explicitly; the generated records have not been validated by an
ERDAS IMAGINE open/save round trip.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence


class GeoPDFError(ValueError):
    """Raised when a PDF georeference cannot be read safely."""


class CalibrationError(ValueError):
    """Raised when GCPs cannot define a stable 2D affine transformation."""


def _deref(value: Any) -> Any:
    getter = getattr(value, "get_object", None)
    if callable(getter):
        try:
            return getter()
        except Exception:
            return value
    return value


def _pdf_name(value: Any) -> str:
    value = _deref(value)
    if value is None:
        return ""
    return str(value).lstrip("/")


def _pdf_text(value: Any) -> Optional[str]:
    value = _deref(value)
    if value is None:
        return None
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return value.decode("latin-1", errors="replace")
    return str(value)


def _finite_float(value: Any) -> float:
    value = _deref(value)
    if isinstance(value, bool):
        raise ValueError("boolean is not a coordinate")
    if isinstance(value, (int, float)):
        result = float(value)
    else:
        result = float(str(value).strip())
    if not math.isfinite(result):
        raise ValueError("coordinate is not finite")
    return result


def _numeric_array(value: Any, *, key: str, max_items: int = 1_000_000) -> list[float]:
    value = _deref(value)
    if not isinstance(value, (list, tuple)):
        raise GeoPDFError(f"{key} must be a PDF array")
    if len(value) > max_items:
        raise GeoPDFError(f"{key} exceeds the safety limit of {max_items} values")
    try:
        return [_finite_float(item) for item in value]
    except (TypeError, ValueError) as exc:
        raise GeoPDFError(f"{key} contains a non-numeric value: {exc}") from exc


def _dict_get(mapping: Any, key: str, default: Any = None) -> Any:
    mapping = _deref(mapping)
    if mapping is None or not hasattr(mapping, "get"):
        return default
    return _deref(mapping.get(key, default))


def _as_dict(value: Any) -> Any:
    value = _deref(value)
    return value if hasattr(value, "get") else None


def _pairs(values: Sequence[float], *, key: str, min_pairs: int = 3) -> list[tuple[float, float]]:
    if len(values) < min_pairs * 2 or len(values) % 2:
        raise GeoPDFError(f"{key} must contain at least {min_pairs} coordinate pairs")
    return [(values[i], values[i + 1]) for i in range(0, len(values), 2)]


def _try_import_pyproj():
    try:
        from pyproj import CRS  # type: ignore
        return CRS
    except ImportError:
        return None


def _resolve_crs_dictionary(dictionary: Any) -> tuple[Optional[str], Optional[str], list[str]]:
    """Return (CRS user input, dictionary type, warnings), never infer a CRS."""
    warnings: list[str] = []
    dictionary = _as_dict(dictionary)
    if dictionary is None:
        return None, None, ["CRS dictionary is missing or is not a dictionary"]

    kind = _pdf_name(_dict_get(dictionary, "/Type")) or None
    epsg_value = _dict_get(dictionary, "/EPSG")
    wkt_value = _pdf_text(_dict_get(dictionary, "/WKT"))

    if epsg_value is not None:
        try:
            epsg_text = str(epsg_value).strip().upper().removeprefix("EPSG:")
            epsg_number = int(epsg_text)
            if epsg_number <= 0:
                raise ValueError("EPSG code must be positive")
            candidate = f"EPSG:{epsg_number}"
            crs_class = _try_import_pyproj()
            if crs_class is not None:
                crs_class.from_user_input(candidate)
            return candidate, kind, warnings
        except Exception as exc:
            warnings.append(f"Invalid explicit EPSG value {epsg_value!r}: {exc}")

    if wkt_value:
        if len(wkt_value) > 1_000_000:
            warnings.append("Embedded WKT exceeds the 1 MB safety limit")
            warnings.append("No explicit, resolvable /EPSG or /WKT CRS was found; CRS was not guessed")
            return None, kind, warnings
        crs_class = _try_import_pyproj()
        if crs_class is None:
            warnings.append("WKT was found, but pyproj is unavailable to validate it")
            return wkt_value, kind, warnings
        try:
            crs_class.from_wkt(wkt_value)
            return wkt_value, kind, warnings
        except Exception as exc:
            warnings.append(f"Embedded WKT could not be parsed: {exc}")

    warnings.append("No explicit, resolvable /EPSG or /WKT CRS was found; CRS was not guessed")
    return None, kind, warnings


def _is_geographic(crs_input: Optional[str], gcs_type: Optional[str]) -> Optional[bool]:
    if crs_input:
        crs_class = _try_import_pyproj()
        if crs_class is not None:
            try:
                return bool(crs_class.from_user_input(crs_input).is_geographic)
            except Exception:
                pass
    if gcs_type:
        normalized = gcs_type.upper()
        if "GEOG" in normalized:
            return True
        if "PROJ" in normalized:
            return False
    return None


def _raw_dictionary_summary(value: Any, *, max_depth: int = 4, _depth: int = 0) -> Any:
    """Small JSON-safe metadata view; avoids dumping arbitrary PDF streams."""
    value = _deref(value)
    if _depth > max_depth:
        return "<max-depth>"
    if value is None or isinstance(value, (str, int, float, bool)):
        if isinstance(value, float) and not math.isfinite(value):
            return str(value)
        return str(value) if value.__class__.__name__ == "NameObject" else value
    if isinstance(value, bytes):
        return value[:128].decode("latin-1", errors="replace")
    if isinstance(value, (list, tuple)):
        return [_raw_dictionary_summary(x, max_depth=max_depth, _depth=_depth + 1) for x in list(value)[:128]]
    if hasattr(value, "items"):
        out = {}
        for key, item in list(value.items())[:128]:
            if str(key) in {"/Length", "/Filter", "/DecodeParms"}:
                continue
            out[str(key)] = _raw_dictionary_summary(item, max_depth=max_depth, _depth=_depth + 1)
        return out
    return str(value)


@dataclass(frozen=True)
class GroundControlPoint:
    """A tie point in PDF default user space and map coordinate space."""

    pdf_x: float
    pdf_y: float
    map_x: float
    map_y: float
    label: Optional[str] = None

    def __post_init__(self) -> None:
        for field_name in ("pdf_x", "pdf_y", "map_x", "map_y"):
            value = float(getattr(self, field_name))
            if not math.isfinite(value):
                raise ValueError(f"{field_name} must be finite")
            object.__setattr__(self, field_name, value)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pdf_x": self.pdf_x, "pdf_y": self.pdf_y,
            "map_x": self.map_x, "map_y": self.map_y,
            "label": self.label,
        }


@dataclass(frozen=True)
class AffineTransform:
    """2D affine transform: X=a*x+c*y+e; Y=b*x+d*y+f."""

    a: float
    b: float
    c: float
    d: float
    e: float
    f: float

    def __post_init__(self) -> None:
        for field_name in ("a", "b", "c", "d", "e", "f"):
            value = float(getattr(self, field_name))
            if not math.isfinite(value):
                raise ValueError(f"Affine coefficient {field_name} must be finite")
            object.__setattr__(self, field_name, value)

    def apply(self, x: float, y: float) -> tuple[float, float]:
        out_x = self.a * x + self.c * y + self.e
        out_y = self.b * x + self.d * y + self.f
        if not math.isfinite(out_x) or not math.isfinite(out_y):
            raise CalibrationError("Affine transform produced a non-finite coordinate")
        return out_x, out_y

    def inverse(self) -> "AffineTransform":
        determinant = self.a * self.d - self.b * self.c
        scale = max(abs(self.a), abs(self.b), abs(self.c), abs(self.d), 1e-150)
        if abs(determinant) <= 1e-14 * scale * scale:
            raise CalibrationError("Affine transform is singular or ill-conditioned")
        ia, ib, ic, id_ = self.d / determinant, -self.b / determinant, -self.c / determinant, self.a / determinant
        ie = -(ia * self.e + ic * self.f)
        iff = -(ib * self.e + id_ * self.f)
        return AffineTransform(ia, ib, ic, id_, ie, iff)

    def to_dict(self) -> dict[str, float]:
        return {"a": self.a, "b": self.b, "c": self.c, "d": self.d, "e": self.e, "f": self.f}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AffineTransform":
        return cls(*(float(data[key]) for key in ("a", "b", "c", "d", "e", "f")))


@dataclass
class AffineFit:
    transform: AffineTransform
    point_count: int
    rmse: float
    max_error: float
    residuals: list[dict[str, float]]
    target_crs: Optional[str] = None

    def __post_init__(self) -> None:
        if self.point_count < 3:
            raise ValueError("AffineFit point_count must be at least three")
        for name in ("rmse", "max_error"):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"AffineFit {name} must be finite and non-negative")
            setattr(self, name, value)

    def to_dict(self) -> dict[str, Any]:
        return {
            "transform": self.transform.to_dict(),
            "point_count": self.point_count,
            "rmse": self.rmse,
            "max_error": self.max_error,
            "residuals": self.residuals,
            "target_crs": self.target_crs,
            "input_coordinate_space": "PDF default user space",
        }


def _solve_least_squares_3col(
    rows: Sequence[Sequence[float]], vector: Sequence[float],
) -> tuple[list[float], float]:
    """Solve an overdetermined 3-column least-squares system using Householder QR.

    This avoids forming A.T*A, which squares the condition number and can make
    near-collinear GCPs look deceptively usable.
    """
    if len(rows) != len(vector) or len(rows) < 3:
        raise CalibrationError("Least-squares system needs at least three matched rows")
    matrix = [list(map(float, row)) for row in rows]
    rhs = list(map(float, vector))
    if any(len(row) != 3 for row in matrix):
        raise CalibrationError("Affine design matrix must have three columns")
    if any(not math.isfinite(value) for row in matrix for value in row) or any(not math.isfinite(value) for value in rhs):
        raise CalibrationError("GCP fit contains non-finite values")

    row_count = len(matrix)
    diagonal: list[float] = []
    for column in range(3):
        norm = math.sqrt(sum(matrix[row][column] ** 2 for row in range(column, row_count)))
        if norm == 0.0:
            raise CalibrationError("GCPs are collinear or numerically unstable; affine fit needs three non-collinear points")
        first = matrix[column][column]
        alpha = -math.copysign(norm, first if first != 0.0 else 1.0)
        vector_h = [matrix[row][column] for row in range(column, row_count)]
        vector_h[0] -= alpha
        denominator = sum(value * value for value in vector_h)
        if denominator == 0.0 or not math.isfinite(denominator):
            raise CalibrationError("GCP fit is numerically unstable")
        beta = 2.0 / denominator

        for target_column in range(column, 3):
            dot = sum(vector_h[i] * matrix[column + i][target_column] for i in range(len(vector_h)))
            factor = beta * dot
            for i, value in enumerate(vector_h):
                matrix[column + i][target_column] -= factor * value
        dot_rhs = sum(vector_h[i] * rhs[column + i] for i in range(len(vector_h)))
        factor_rhs = beta * dot_rhs
        for i, value in enumerate(vector_h):
            rhs[column + i] -= factor_rhs * value

        matrix[column][column] = alpha
        for row in range(column + 1, row_count):
            matrix[row][column] = 0.0
        diagonal.append(abs(alpha))

    largest = max(diagonal)
    ratio = min(diagonal) / largest if largest else 0.0
    if ratio < 1e-11:
        raise CalibrationError("GCP geometry is too poorly conditioned for a trustworthy affine fit")

    solution = [0.0, 0.0, 0.0]
    for row in range(2, -1, -1):
        remainder = rhs[row] - sum(matrix[row][col] * solution[col] for col in range(row + 1, 3))
        pivot = matrix[row][row]
        if abs(pivot) <= largest * 1e-14:
            raise CalibrationError("GCP fit is rank deficient")
        solution[row] = remainder / pivot
    return solution, ratio


def fit_affine(
    gcps: Iterable[GroundControlPoint], *, target_crs: Optional[str] = None,
) -> AffineFit:
    """Fit a least-squares affine map transform from >=3 non-collinear GCPs."""
    points = list(gcps)
    if len(points) < 3:
        raise CalibrationError("At least three GCPs are required for a 2D affine transform")
    if target_crs:
        crs_class = _try_import_pyproj()
        if crs_class is None:
            raise CalibrationError("pyproj is required to validate a target CRS; install pdf2ovr[geo]")
        try:
            crs_class.from_user_input(target_crs)
        except Exception as exc:
            raise CalibrationError(f"Invalid target CRS {target_crs!r}: {exc}") from exc

    cx = sum(p.pdf_x for p in points) / len(points)
    cy = sum(p.pdf_y for p in points) / len(points)
    sx = max((abs(p.pdf_x - cx) for p in points), default=0.0) or 1.0
    sy = max((abs(p.pdf_y - cy) for p in points), default=0.0) or 1.0
    rows = [((p.pdf_x - cx) / sx, (p.pdf_y - cy) / sy, 1.0) for p in points]

    coeff_x, ratio_x = _solve_least_squares_3col(rows, [p.map_x for p in points])
    coeff_y, ratio_y = _solve_least_squares_3col(rows, [p.map_y for p in points])
    if min(ratio_x, ratio_y) < 1e-11:
        raise CalibrationError("GCP geometry is too poorly conditioned for a trustworthy affine fit")

    ax, cx_norm, ex_norm = coeff_x
    by, dy_norm, fy_norm = coeff_y
    a, c = ax / sx, cx_norm / sy
    b, d = by / sx, dy_norm / sy
    e = ex_norm - a * cx - c * cy
    f = fy_norm - b * cx - d * cy
    transform = AffineTransform(a, b, c, d, e, f)

    residuals: list[dict[str, float]] = []
    squared: list[float] = []
    for index, point in enumerate(points):
        pred_x, pred_y = transform.apply(point.pdf_x, point.pdf_y)
        dx, dy = pred_x - point.map_x, pred_y - point.map_y
        error = math.hypot(dx, dy)
        squared.append(error * error)
        residuals.append({"index": index, "dx": dx, "dy": dy, "error": error})
    return AffineFit(
        transform=transform,
        point_count=len(points),
        rmse=math.sqrt(sum(squared) / len(squared)),
        max_error=max(math.sqrt(value) for value in squared),
        residuals=residuals,
        target_crs=target_crs,
    )


def reproject_point(
    x: float, y: float, source_crs: str, target_crs: str,
) -> tuple[float, float]:
    """Reproject using traditional GIS X/Y axis order (lon/lat, easting/northing)."""
    x = float(x)
    y = float(y)
    if not math.isfinite(x) or not math.isfinite(y):
        raise GeoPDFError("Input coordinates for reprojection must be finite")
    if not source_crs or not target_crs:
        raise GeoPDFError("Source and target CRS must be specified")
    try:
        from pyproj import Transformer  # type: ignore
    except ImportError as exc:
        raise GeoPDFError("pyproj is required for CRS reprojection; install pdf2ovr[geo]") from exc
    try:
        transformer = Transformer.from_crs(source_crs, target_crs, always_xy=True)
        out_x, out_y = transformer.transform(float(x), float(y), errcheck=True)
    except Exception as exc:
        raise GeoPDFError(f"Could not reproject coordinate from {source_crs} to {target_crs}: {exc}") from exc
    if not math.isfinite(out_x) or not math.isfinite(out_y):
        raise GeoPDFError("CRS reprojection produced non-finite coordinates")
    return float(out_x), float(out_y)


@dataclass
class PageGeoReference:
    page_index: int
    viewport_index: int
    encoding: str
    crs: Optional[str]
    crs_kind: Optional[str]
    crs_resolved: bool
    bbox_pdf: Optional[tuple[float, float, float, float]] = None
    neatline_pdf: list[tuple[float, float]] = field(default_factory=list)
    control_points: list[GroundControlPoint] = field(default_factory=list)
    transform: Optional[AffineTransform] = None
    fit: Optional[AffineFit] = None
    metadata: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def map_point(
        self, pdf_x: float, pdf_y: float, *, target_crs: Optional[str] = None,
    ) -> tuple[float, float]:
        if self.transform is None:
            raise GeoPDFError("This viewport does not contain enough valid tie points for an affine transform")
        x, y = self.transform.apply(pdf_x, pdf_y)
        if target_crs:
            if not self.crs:
                raise GeoPDFError("Cannot reproject because this viewport has no resolved source CRS")
            return reproject_point(x, y, self.crs, target_crs)
        return x, y

    def map_extractor_point(
        self, pdf_path: str | Path, x: float, y: float, *, target_crs: Optional[str] = None,
    ) -> tuple[float, float]:
        """Map a point in PDFExtractor's displayed coordinate space."""
        pdf_x, pdf_y = extractor_to_pdf_user_space(pdf_path, self.page_index, x, y)
        return self.map_point(pdf_x, pdf_y, target_crs=target_crs)

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_index": self.page_index,
            "viewport_index": self.viewport_index,
            "encoding": self.encoding,
            "crs": self.crs,
            "crs_kind": self.crs_kind,
            "crs_resolved": self.crs_resolved,
            "bbox_pdf": list(self.bbox_pdf) if self.bbox_pdf else None,
            "neatline_pdf": [list(p) for p in self.neatline_pdf],
            "control_points": [p.to_dict() for p in self.control_points],
            "transform": self.transform.to_dict() if self.transform else None,
            "fit": self.fit.to_dict() if self.fit else None,
            "metadata": self.metadata,
            "warnings": list(self.warnings),
        }


@dataclass
class GeoPDFPage:
    page_index: int
    media_box_pdf: Optional[list[float]] = None
    crop_box_pdf: Optional[list[float]] = None
    rotation: int = 0
    viewports: list[PageGeoReference] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @staticmethod
    def _point_in_polygon(x: float, y: float, polygon: Sequence[tuple[float, float]]) -> bool:
        if len(polygon) < 3:
            return False
        inside = False
        j = len(polygon) - 1
        for i, (xi, yi) in enumerate(polygon):
            xj, yj = polygon[j]
            # Include boundary points with a small numeric tolerance.
            cross = (x - xi) * (yj - yi) - (y - yi) * (xj - xi)
            if abs(cross) <= 1e-9 and min(xi, xj) - 1e-9 <= x <= max(xi, xj) + 1e-9 and min(yi, yj) - 1e-9 <= y <= max(yi, yj) + 1e-9:
                return True
            intersects = ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-300) + xi)
            if intersects:
                inside = not inside
            j = i
        return inside

    def select_viewport(self, pdf_x: float, pdf_y: float) -> Optional[PageGeoReference]:
        """Select a unique georeference containing a PDF user-space point.

        ISO 32000 defines the viewport array in drawing order, so overlapping
        ISO viewports are considered from last to first. OGC map frames may
        overlap too, but the encoding does not define an equivalent z-order;
        overlapping OGC frames are therefore ambiguous. Mixed ISO/OGC entries
        are also not assigned an invented precedence.
        """
        encodings = {item.encoding for item in self.viewports}
        if len(encodings) > 1:
            return None

        if len(self.viewports) == 1:
            only = self.viewports[0]
            # OGC PDF georegistration 2.2 permits a single map frame without
            # Neatline; it describes the full page. An invalid Neatline must
            # never be silently replaced with that default.
            if (
                only.encoding == "ogc-geopdf-lgidict"
                and only.bbox_pdf is None
                and not only.neatline_pdf
                and not only.metadata.get("neatline_invalid")
            ):
                return only

        matches: list[PageGeoReference] = []
        for viewport in reversed(self.viewports):
            if viewport.metadata.get("neatline_invalid"):
                continue
            if viewport.bbox_pdf is not None:
                x0, y0, x1, y1 = viewport.bbox_pdf
                if not (x0 <= pdf_x <= x1 and y0 <= pdf_y <= y1):
                    continue
            if viewport.neatline_pdf and len(viewport.neatline_pdf) >= 3:
                if not self._point_in_polygon(pdf_x, pdf_y, viewport.neatline_pdf):
                    continue
            elif viewport.bbox_pdf is None:
                continue
            matches.append(viewport)

        if not matches:
            return None
        if matches[0].encoding == "ogc-geopdf-lgidict" and len(matches) > 1:
            return None
        return matches[0]

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_index": self.page_index,
            "media_box_pdf": self.media_box_pdf,
            "crop_box_pdf": self.crop_box_pdf,
            "rotation": self.rotation,
            "georeferenced": bool(self.viewports),
            "viewport_count": len(self.viewports),
            "viewports": [v.to_dict() for v in self.viewports],
            "warnings": list(self.warnings),
        }


class GeoPDFDocument:
    """Read Adobe/ISO and OGC GeoPDF georeferencing without modifying the PDF."""

    def __init__(self, source: str | Path | bytes | bytearray, *, password: Optional[str] = None):
        try:
            from pypdf import PdfReader  # type: ignore
        except ImportError as exc:
            raise GeoPDFError("pypdf is required for GeoPDF inspection; install pdf2ovr[geo]") from exc

        if isinstance(source, (str, Path)):
            self.path: Optional[Path] = Path(source)
            stream: Any = str(self.path)
        else:
            from io import BytesIO
            self.path = None
            stream = BytesIO(bytes(source))
        try:
            self.reader = PdfReader(stream, strict=False)
            if self.reader.is_encrypted:
                if password is None or self.reader.decrypt(password) == 0:
                    raise GeoPDFError("PDF is encrypted; provide a valid password")
        except GeoPDFError:
            raise
        except Exception as exc:
            raise GeoPDFError(f"Could not read PDF: {exc}") from exc

        if not self.reader.pages:
            raise GeoPDFError("PDF contains no pages")
        self.pdf_header = _pdf_text(getattr(self.reader, "pdf_header", None))
        self.catalog_version = _pdf_text(_dict_get(self.reader.trailer.get("/Root"), "/Version"))
        self.pages: list[GeoPDFPage] = []
        for index, page_obj in enumerate(self.reader.pages):
            self.pages.append(self._read_page(index, page_obj))

    @staticmethod
    def _read_pdf_box(value: Any, label: str) -> Optional[list[float]]:
        if value is None:
            return None
        try:
            values = _numeric_array(value, key=label)
        except GeoPDFError:
            return None
        return values if len(values) == 4 else None

    def _read_page(self, page_index: int, page_obj: Any) -> GeoPDFPage:
        page_rotation = 0
        page_warnings: list[str] = []
        try:
            raw_rotation = getattr(page_obj, "rotation", 0) or 0
            numeric_rotation = _finite_float(raw_rotation)
            if not numeric_rotation.is_integer() or int(numeric_rotation) % 90 != 0:
                page_warnings.append(
                    f"Invalid /Rotate value {raw_rotation!r}; using 0 degrees for page metadata"
                )
            else:
                page_rotation = int(numeric_rotation) % 360
        except Exception as exc:
            page_warnings.append(f"Could not read /Rotate ({exc}); using 0 degrees for page metadata")

        page = GeoPDFPage(
            page_index=page_index,
            media_box_pdf=self._read_pdf_box(page_obj.mediabox, "MediaBox"),
            crop_box_pdf=self._read_pdf_box(page_obj.cropbox, "CropBox"),
            rotation=page_rotation,
            warnings=page_warnings,
        )

        vp = _dict_get(page_obj, "/VP")
        if vp is not None:
            viewports = list(vp) if isinstance(vp, (list, tuple)) else [vp]
            for index, viewport in enumerate(viewports):
                try:
                    parsed = self._read_iso_viewport(page_index, index, viewport)
                    if parsed is not None:
                        page.viewports.append(parsed)
                except GeoPDFError as exc:
                    page.warnings.append(f"ISO viewport {index}: {exc}")

        lgi = _dict_get(page_obj, "/LGIDict")
        if lgi is not None:
            frames = list(lgi) if isinstance(lgi, (list, tuple)) else [lgi]
            ogc_refs: list[PageGeoReference] = []
            for index, frame in enumerate(frames):
                try:
                    parsed = self._read_ogc_frame(page_index, index, frame)
                    if parsed is not None:
                        page.viewports.append(parsed)
                        ogc_refs.append(parsed)
                except GeoPDFError as exc:
                    page.warnings.append(f"OGC map frame {index}: {exc}")
            if len(frames) > 1:
                for parsed in ogc_refs:
                    if not parsed.neatline_pdf:
                        parsed.warnings.append(
                            "Multiple OGC map frames exist but this frame has no /Neatline; "
                            "its page region cannot be selected safely"
                        )

        if not page.viewports:
            page.warnings.append("No supported /VP geospatial measure or /LGIDict map frame was found")
        if len({item.encoding for item in page.viewports}) > 1:
            page.warnings.append(
                "Both ISO /VP and OGC /LGIDict georeferencing were found; "
                "their precedence is not defined here, so point-based viewport selection is disabled"
            )
        return page

    def _read_iso_viewport(
        self, page_index: int, viewport_index: int, viewport_obj: Any,
    ) -> Optional[PageGeoReference]:
        viewport = _as_dict(viewport_obj)
        if viewport is None:
            raise GeoPDFError("viewport is not a dictionary")
        bbox_raw = _numeric_array(_dict_get(viewport, "/BBox"), key="/BBox")
        if len(bbox_raw) != 4:
            raise GeoPDFError("/BBox must contain four coordinates")
        x0, y0, x1, y1 = bbox_raw
        if x1 <= x0 or y1 <= y0:
            raise GeoPDFError("/BBox must have positive width and height")

        measure = _as_dict(_dict_get(viewport, "/Measure"))
        if measure is None:
            return None
        if _pdf_name(_dict_get(measure, "/Subtype")) != "GEO":
            return None

        gcs = _dict_get(measure, "/GCS")
        crs, crs_kind, warnings = _resolve_crs_dictionary(gcs)
        geographic = _is_geographic(crs, crs_kind)
        gpts = _pairs(_numeric_array(_dict_get(measure, "/GPTS"), key="/GPTS"), key="/GPTS")
        lpts_raw = _dict_get(measure, "/LPTS")
        if lpts_raw is None:
            # LPTS was optional in the original PDF 1.7 ExtensionLevel 3
            # encoding, but is required by the corrected PDF 2.0 specification.
            # Keep the discoverable viewport and GPTS; do not invent local tie
            # point positions when none are encoded.
            warnings.append(
                "/LPTS is missing; viewport and GPTS are retained, but no "
                "PDF-to-map transform is inferred (PDF 2.0 requires /LPTS)"
            )
            bounds = _dict_get(measure, "/Bounds")
            neatline: list[tuple[float, float]] = []
            neatline_invalid = False
            if bounds is not None:
                try:
                    normalized_bounds = _pairs(_numeric_array(bounds, key="/Bounds"), key="/Bounds")
                    neatline = [(x0 + u * (x1 - x0), y0 + v * (y1 - y0)) for u, v in normalized_bounds]
                except GeoPDFError as exc:
                    neatline_invalid = True
                    warnings.append(f"Could not interpret /Bounds as normalized pairs: {exc}")
            else:
                neatline = [(x0, y0), (x0, y1), (x1, y1), (x1, y0)]
            metadata = {
                "viewport_name": _pdf_text(_dict_get(viewport, "/Name")),
                "gcs_dictionary": _raw_dictionary_summary(gcs),
                "measure_dictionary": _raw_dictionary_summary(measure),
                "display_coordinate_system": _raw_dictionary_summary(_dict_get(measure, "/DCS")),
                "preferred_display_units": _raw_dictionary_summary(_dict_get(measure, "/PDU")),
                "gpts_pairs": [list(pair) for pair in gpts],
                "neatline_invalid": neatline_invalid,
            }
            return PageGeoReference(
                page_index=page_index, viewport_index=viewport_index,
                encoding="adobe-iso32000-geo", crs=crs, crs_kind=crs_kind,
                crs_resolved=bool(crs and _try_import_pyproj() is not None),
                bbox_pdf=(x0, y0, x1, y1), neatline_pdf=neatline,
                control_points=[], transform=None, fit=None, metadata=metadata,
                warnings=warnings,
            )
        lpts = _pairs(_numeric_array(lpts_raw, key="/LPTS"), key="/LPTS")
        if len(gpts) != len(lpts):
            raise GeoPDFError(f"/GPTS has {len(gpts)} pairs but /LPTS has {len(lpts)}")

        gcps: list[GroundControlPoint] = []
        for index, ((u, v), (g0, g1)) in enumerate(zip(lpts, gpts)):
            if not (-1e-7 <= u <= 1.0000001 and -1e-7 <= v <= 1.0000001):
                warnings.append(f"LPTS tie point {index} lies outside the normalized unit square")
            pdf_x = x0 + u * (x1 - x0)
            pdf_y = y0 + v * (y1 - y0)
            if geographic is True:
                # ISO 32000 defines geographic GPTS pairs as latitude, longitude.
                map_x, map_y = g1, g0
            else:
                # Projected CRS uses easting, northing. If CRS kind is unknown,
                # retain pair order and report that axis semantics are unresolved.
                map_x, map_y = g0, g1
                if geographic is None:
                    warnings.append("GCS axis semantics are unresolved; GPTS pair order was preserved")
            gcps.append(GroundControlPoint(pdf_x, pdf_y, map_x, map_y, f"tiepoint-{index + 1}"))

        bounds = _dict_get(measure, "/Bounds")
        neatline: list[tuple[float, float]] = []
        neatline_invalid = False
        if bounds is not None:
            try:
                normalized_bounds = _pairs(_numeric_array(bounds, key="/Bounds"), key="/Bounds")
                neatline = [
                    (x0 + u * (x1 - x0), y0 + v * (y1 - y0))
                    for u, v in normalized_bounds
                ]
            except GeoPDFError as exc:
                neatline_invalid = True
                warnings.append(f"Could not interpret /Bounds as normalized pairs: {exc}")
        else:
            neatline = [(x0, y0), (x0, y1), (x1, y1), (x1, y0)]

        pcsm = _dict_get(measure, "/PCSM")
        if pcsm is not None and geographic is True:
            # PDF 2.0 explicitly states PCSM is ignored for geographic GCS.
            warnings.append("/PCSM is present but ignored because /GCS is geographic; /GPTS and /LPTS are used")
            pcsm = None

        if pcsm is not None and geographic is False:
            # PCSM is a 12-value row-major 4x4 affine matrix with the final
            # column [0, 0, 0, 1] implicit. It maps local XObject position
            # coordinates [u, v, 0, 1] to projected coordinates. For a page
            # viewport, LPTS supplies local unit-square positions; composing
            # this transform with /BBox gives PDF-user-space -> projected XY.
            try:
                matrix = _numeric_array(pcsm, key="/PCSM")
                if len(matrix) != 12:
                    raise GeoPDFError("/PCSM must contain exactly 12 numeric values")
                m00, m01, _m02, m10, m11, _m12, _m20, _m21, _m22, m30, m31, _m32 = matrix
                width = x1 - x0
                height = y1 - y0
                transform = AffineTransform(
                    m00 / width, m01 / width,
                    m10 / height, m11 / height,
                    m30 - (m00 * x0 / width) - (m10 * y0 / height),
                    m31 - (m01 * x0 / width) - (m11 * y0 / height),
                )
                fit = None
                warnings.append("/PCSM takes priority over /GPTS for projected GCS; transform uses the 2D slice of the 12-value matrix")
            except (GeoPDFError, CalibrationError, ZeroDivisionError) as exc:
                warnings.append(f"Could not apply projected /PCSM: {exc}")
                transform = fit = None
        elif pcsm is not None:
            # We cannot decide whether PCSM overrides GPTS if the GCS is not
            # explicitly recognized as geographic or projected.
            warnings.append("/PCSM is present but /GCS type is unresolved; neither PCSM nor GPTS transform is selected")
            transform = fit = None
        else:
            try:
                validated_crs = crs if crs and _try_import_pyproj() is not None else None
                fit = fit_affine(gcps, target_crs=validated_crs)
                transform = fit.transform
            except CalibrationError as exc:
                warnings.append(f"Could not fit affine transform from GeoPDF tie points: {exc}")
                transform = fit = None

        metadata = {
            "viewport_name": _pdf_text(_dict_get(viewport, "/Name")),
            "gcs_dictionary": _raw_dictionary_summary(gcs),
            "measure_dictionary": _raw_dictionary_summary(measure),
            "display_coordinate_system": _raw_dictionary_summary(_dict_get(measure, "/DCS")),
            "preferred_display_units": _raw_dictionary_summary(_dict_get(measure, "/PDU")),
            "neatline_invalid": neatline_invalid,
        }
        return PageGeoReference(
            page_index=page_index, viewport_index=viewport_index,
            encoding="adobe-iso32000-geo", crs=crs, crs_kind=crs_kind,
            crs_resolved=bool(crs and _try_import_pyproj() is not None),
            bbox_pdf=(x0, y0, x1, y1), neatline_pdf=neatline,
            control_points=gcps, transform=transform, fit=fit,
            metadata=metadata, warnings=warnings,
        )

    def _read_ogc_frame(
        self, page_index: int, viewport_index: int, frame_obj: Any,
    ) -> Optional[PageGeoReference]:
        frame = _as_dict(frame_obj)
        if frame is None:
            raise GeoPDFError("LGIDict map frame is not a dictionary")
        type_name = _pdf_name(_dict_get(frame, "/Type"))
        if type_name and type_name != "LGIDict":
            raise GeoPDFError(f"unexpected /LGIDict /Type /{type_name}")

        projection = _dict_get(frame, "/Projection")
        crs, crs_kind, warnings = _resolve_crs_dictionary(projection)
        geographic = _is_geographic(crs, crs_kind)
        gcps: list[GroundControlPoint] = []
        registration = _dict_get(frame, "/Registration")
        if registration is not None:
            for index, row_obj in enumerate(registration):
                row = _numeric_array(row_obj, key=f"/Registration[{index}]")
                if len(row) != 4:
                    warnings.append(f"Registration point {index} does not contain four numbers")
                    continue
                pdf_x, pdf_y, g0, g1 = row
                if geographic is True:
                    # OGC registration records are described as PDF X/Y then
                    # geospatial X/Y; leave their declared X/Y order unchanged.
                    map_x, map_y = g0, g1
                else:
                    map_x, map_y = g0, g1
                gcps.append(GroundControlPoint(pdf_x, pdf_y, map_x, map_y, f"registration-{index + 1}"))

        ctm_values = _dict_get(frame, "/CTM")
        parsed_ctm: Optional[list[float]] = None
        ctm_transform: Optional[AffineTransform] = None
        if ctm_values is not None:
            try:
                parsed_ctm = _numeric_array(ctm_values, key="/CTM")
            except GeoPDFError as exc:
                warnings.append(str(exc))
                parsed_ctm = None
        if parsed_ctm is not None:
            values = parsed_ctm
            if len(values) != 6:
                warnings.append("/CTM does not contain six affine coefficients")
            else:
                a, b, c, d, e, f = values
                try:
                    # OGC 08-139r3 defines /CTM as PDF -> projected/geodetic
                    # coordinates and gives it precedence over /Registration.
                    # The coefficient order follows the PDF `cm` operator.
                    ctm_transform = AffineTransform(a, b, c, d, e, f)
                    ctm_transform.inverse()  # Validate that the matrix is invertible.
                except CalibrationError as exc:
                    warnings.append(f"/CTM is singular: {exc}")
                    ctm_transform = None

        registration_fit: Optional[AffineFit] = None
        transform: Optional[AffineTransform] = None
        validated_crs = crs if crs and _try_import_pyproj() is not None else None
        if len(gcps) >= 3:
            try:
                registration_fit = fit_affine(gcps, target_crs=validated_crs)
            except CalibrationError as exc:
                warnings.append(f"Could not fit affine transform from /Registration: {exc}")
        elif gcps:
            warnings.append("At least three non-collinear /Registration points are needed for an affine fit")

        if ctm_transform is not None:
            # The best-practice specification says a valid CTM takes
            # precedence. Registration points are only cross-check evidence.
            transform = ctm_transform
            if registration_fit is not None:
                disagreements = []
                for point in gcps:
                    from_gcp = registration_fit.transform.apply(point.pdf_x, point.pdf_y)
                    from_ctm = ctm_transform.apply(point.pdf_x, point.pdf_y)
                    disagreements.append(math.hypot(from_gcp[0] - from_ctm[0], from_gcp[1] - from_ctm[1]))
                if max(disagreements, default=0.0) > max(1e-6, registration_fit.rmse * 5):
                    warnings.append("/CTM materially disagrees with /Registration; the higher-precedence /CTM was selected")
        elif registration_fit is not None:
            transform = registration_fit.transform

        neatline: list[tuple[float, float]] = []
        neatline_invalid = False
        neatline_raw = _dict_get(frame, "/Neatline")
        if neatline_raw is not None:
            try:
                neatline_pairs = _pairs(
                    _numeric_array(neatline_raw, key="/Neatline"),
                    key="/Neatline", min_pairs=2,
                )
                if len(neatline_pairs) == 2:
                    # OGC 08-139r3 permits a two-point diagonal as shorthand
                    # for the lower-left / upper-right rectangle corners.
                    (x0, y0), (x1, y1) = neatline_pairs
                    left, right = sorted((x0, x1))
                    bottom, top = sorted((y0, y1))
                    neatline = [(left, bottom), (left, top), (right, top), (right, bottom)]
                else:
                    neatline = neatline_pairs
            except GeoPDFError as exc:
                neatline_invalid = True
                warnings.append(f"Could not parse /Neatline: {exc}")

        metadata = {
            "version": _pdf_text(_dict_get(frame, "/Version")),
            "projection_dictionary": _raw_dictionary_summary(projection),
            "display_dictionary": _raw_dictionary_summary(_dict_get(frame, "/Display")),
            "ctm": parsed_ctm,
            "registration_count": len(gcps),
            "registration_fit": registration_fit.to_dict() if registration_fit else None,
            "ctm_precedence": ctm_transform is not None,
            "neatline_invalid": neatline_invalid,
        }
        return PageGeoReference(
            page_index=page_index, viewport_index=viewport_index,
            encoding="ogc-geopdf-lgidict", crs=crs, crs_kind=crs_kind,
            crs_resolved=bool(crs and _try_import_pyproj() is not None), bbox_pdf=None, neatline_pdf=neatline,
            control_points=gcps, transform=transform, fit=None,
            metadata=metadata, warnings=warnings,
        )

    @property
    def georeferenced_pages(self) -> list[int]:
        return [page.page_index for page in self.pages if page.viewports]

    def summary(self) -> dict[str, Any]:
        return {
            "path": str(self.path) if self.path else None,
            "pdf_header": self.pdf_header,
            "catalog_version": self.catalog_version,
            "page_count": len(self.pages),
            "georeferenced_page_count": len(self.georeferenced_pages),
            "georeferenced_pages": self.georeferenced_pages,
            "viewport_count": sum(len(page.viewports) for page in self.pages),
            "pages": [page.to_dict() for page in self.pages],
        }


def pdf_user_to_extractor_space(pdf_path: str | Path, page_index: int, x: float, y: float) -> tuple[float, float]:
    """Convert PDF default user-space point to PDFExtractor's displayed coords.

    PDFExtractor uses PyMuPDF displayed page space with a bottom-left origin.
    PyMuPDF's page transformation, rotation matrix, CropBox and UserUnit are
    used rather than duplicating those PDF page-coordinate rules here.
    """
    try:
        import pymupdf  # type: ignore
    except ImportError as exc:
        raise GeoPDFError("PyMuPDF is required for page coordinate adaptation; install pdf2ovr[pdf]") from exc
    with pymupdf.open(str(pdf_path)) as document:
        if page_index < 0 or page_index >= len(document):
            raise GeoPDFError(f"Page index {page_index} is outside the PDF")
        page = document[page_index]
        mupdf_point = pymupdf.Point(float(x), float(y)) * page.transformation_matrix
        displayed = mupdf_point * page.rotation_matrix
        return float(displayed.x), float(page.rect.height - displayed.y)


def extractor_to_pdf_user_space(pdf_path: str | Path, page_index: int, x: float, y: float) -> tuple[float, float]:
    """Convert PDFExtractor displayed coords back to PDF default user space."""
    try:
        import pymupdf  # type: ignore
    except ImportError as exc:
        raise GeoPDFError("PyMuPDF is required for page coordinate adaptation; install pdf2ovr[pdf]") from exc
    with pymupdf.open(str(pdf_path)) as document:
        if page_index < 0 or page_index >= len(document):
            raise GeoPDFError(f"Page index {page_index} is outside the PDF")
        page = document[page_index]
        displayed = pymupdf.Point(float(x), float(page.rect.height - y))
        unrotated = displayed * page.derotation_matrix
        inverse = page.transformation_matrix
        if inverse.invert() != 0:
            raise GeoPDFError("Could not invert PDF page transformation matrix")
        pdf_point = unrotated * inverse
        return float(pdf_point.x), float(pdf_point.y)


def load_affine_fit(path: str | Path) -> AffineFit:
    """Load and validate a calibration JSON file created by the `calibrate` CLI."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("schema_version") != 1:
            raise CalibrationError("Unsupported calibration JSON schema version")
        transform_data = data["transform"]
        if not isinstance(transform_data, dict):
            raise CalibrationError("Calibration transform must be an object")
        transform = AffineTransform.from_dict(transform_data)
        residuals = data.get("residuals", [])
        if not isinstance(residuals, list) or any(not isinstance(item, dict) for item in residuals):
            raise CalibrationError("Calibration residuals must be an array of objects")
        return AffineFit(
            transform=transform,
            point_count=int(data["point_count"]),
            rmse=float(data["rmse"]),
            max_error=float(data["max_error"]),
            residuals=residuals,
            target_crs=data.get("target_crs"),
        )
    except CalibrationError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise CalibrationError(f"Invalid calibration file {path}: {exc}") from exc
