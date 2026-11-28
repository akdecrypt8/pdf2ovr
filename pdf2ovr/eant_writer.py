"""Schema-aware, conservative EANT cloning and annotation creation.

New annotations are constructed only from schema/style/transform templates
observed in an existing OVR. Geometry coordinates and parent bounding boxes
remain explicit caller inputs; unknown record semantics are never invented.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from numbers import Real
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from .eant_reader import EANTDocument, EANTReadError
from .hfa_dictionary import HFADictionary, HFADictionaryError
from .hfa_writer import HFAEntrySpec, HFAWriteError, HFAWriter


class EANTWriteError(ValueError):
    """Raised when an annotation cannot be reproduced without guessing."""


@dataclass(frozen=True)
class EANTCloneResult:
    data: bytes
    new_entry_offset: int
    new_element_id: int
    new_name: str
    source_entry_offset: int


@dataclass(frozen=True)
class EANTCreateResult:
    data: bytes
    new_entry_offset: int
    new_geometry_offset: int
    new_element_id: int
    new_name: str
    element_schema: str
    geometry_entry_type: str


class EANTWriter:
    """Create observed simple annotation schemas and clone known annotations.

    Creation uses a real annotation as a schema/style/transform template. It
    does not synthesize unknown EANT defaults or infer coordinate/bounding-box
    semantics that are not established by the source OVR.
    """

    def __init__(self, source: Union[str, bytes, bytearray]):
        self.document = EANTDocument(source)
        if self.document.annotation_root is None or self.document.element_list is None:
            raise EANTWriteError("The OVR has no recognized Annotation/ElementList structure")
        self.hfa_writer = HFAWriter(self.document.raw)
        self.dictionary: HFADictionary = self.document.dictionary

    @staticmethod
    def _clean(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: EANTWriter._clean(item) for key, item in value.items() if not key.startswith("_")}
        if isinstance(value, list):
            return [EANTWriter._clean(item) for item in value]
        return value

    def _find_source(self, offset: int):
        entry = self.document.hfa.entry_by_offset.get(offset)
        if entry is None or entry.entry_type not in {"Element_Eant", "Element_2_Eant"}:
            raise EANTWriteError(f"Offset {offset} is not a reachable EANT element entry")
        return entry

    def _max_annotation_id(self) -> int:
        values = [item.element_id for item in self.document.iter_annotations() if item.element_id is not None]
        return max(values, default=-1)

    def _choose_annotation_id(self, new_id: Optional[int]) -> int:
        if new_id is None:
            chosen = self._max_annotation_id() + 1
        else:
            if isinstance(new_id, bool) or not isinstance(new_id, int):
                raise EANTWriteError("new_id must be an integer")
            chosen = new_id
        if chosen < 0 or chosen > 0x7FFFFFFF:
            raise EANTWriteError("new_id must be a non-negative 32-bit signed integer")
        used = {item.element_id for item in self.document.iter_annotations() if item.element_id is not None}
        if chosen in used:
            raise EANTWriteError(f"Annotation ID {chosen} is already in use")
        return chosen

    @staticmethod
    def _finite_scalar(value: Any, field_name: str) -> float:
        if isinstance(value, bool) or not isinstance(value, Real):
            raise EANTWriteError(f"{field_name} must be a real number")
        try:
            result = float(value)
        except (OverflowError, ValueError) as exc:
            raise EANTWriteError(f"{field_name} must be a finite real number") from exc
        if not math.isfinite(result):
            raise EANTWriteError(f"{field_name} must be finite")
        return result

    @classmethod
    def _finite_pair(cls, value: Sequence[float], field_name: str) -> Tuple[float, float]:
        if not isinstance(value, (tuple, list)) or len(value) != 2:
            raise EANTWriteError(f"{field_name} must contain exactly two coordinates")
        return (
            cls._finite_scalar(value[0], f"{field_name} X"),
            cls._finite_scalar(value[1], f"{field_name} Y"),
        )

    @classmethod
    def _bbox_values(cls, bbox: Sequence[Sequence[float]]) -> Dict[str, Any]:
        """Encode four caller-supplied corners in the parent bbox coordinate space.

        The sample files establish a 2x4 f64 BASEDATA representation. They do
        not establish a universal way to derive this box from local geometry, so
        Phase 5 requires it explicitly instead of guessing.
        """
        if not isinstance(bbox, (tuple, list)) or len(bbox) != 4:
            raise EANTWriteError("bbox must contain four (x, y) corner pairs")
        points = [cls._finite_pair(point, "bbox") for point in bbox]
        # Reject a fully degenerate quadrilateral. It is valid for a line's
        # bbox to have zero height or width, but not for all four corners to
        # collapse to the same point.
        if len(set(points)) < 2:
            raise EANTWriteError("bbox must span at least two distinct points")
        return {
            "rows": 2, "columns": 4, "item_type": "f64", "object_type": 2,
            "values": [coordinate for point in points for coordinate in point],
        }

    def _create_from_template(
        self,
        template_entry_offset: int,
        *,
        new_name: str,
        bbox: Sequence[Sequence[float]],
        description: Optional[str],
        expected_geometry_type: str,
        update_geometry,
        new_id: Optional[int] = None,
        update_children=None,
    ) -> EANTCreateResult:
        if not isinstance(new_name, str) or not new_name or "\x00" in new_name:
            raise EANTWriteError("new_name must be a non-empty string without NUL")
        try:
            encoded_name = new_name.encode("latin-1")
        except UnicodeEncodeError as exc:
            raise EANTWriteError(
                "Annotation names must be representable in the observed HFA single-byte text encoding"
            ) from exc
        if len(encoded_name) > 255:
            raise EANTWriteError("Annotation name exceeds the conservative 255-byte limit")

        source_entry = self._find_source(template_entry_offset)
        parent_source = self.document.decoded.get(source_entry.offset)
        if parent_source is None:
            raise EANTWriteError("Template parent payload could not be decoded")
        type_info = parent_source.get("elmType")
        type_label = type_info.get("label") if isinstance(type_info, dict) else None
        if type_label == "EANT_GROUP":
            raise EANTWriteError("Group templates cannot be used to create simple annotations")
        expected_profiles = {
            "Text2": ("Element_2_Eant", "EANT_TEXT"),
            "Rectangle2": ("Element_2_Eant", "EANT_RECTANGLE"),
            "Polyline2": ("Element_2_Eant", "EANT_POLYLINE"),
            "Eant_Ellipse": ("Element_Eant", "EANT_ELLIPSE"),
        }
        expected_profile = expected_profiles.get(expected_geometry_type)
        if expected_profile is None:
            raise EANTWriteError(f"Creation of {expected_geometry_type!r} is not supported")
        if (source_entry.entry_type, type_label) != expected_profile:
            raise EANTWriteError(
                f"Template profile must be {expected_profile[0]} / {expected_profile[1]}, "
                f"found {source_entry.entry_type} / {type_label}"
            )

        source_children = self.document.hfa.children(source_entry)
        geometry_entry = next((
            child for child in source_children
            if child.entry_type == expected_geometry_type
        ), None)
        if geometry_entry is None:
            available = ", ".join(child.entry_type for child in source_children) or "none"
            raise EANTWriteError(
                f"Template {source_entry.name!r} has no {expected_geometry_type} child (found: {available})"
            )
        geometry_values = self.document.decoded.get(geometry_entry.offset)
        if geometry_values is None:
            raise EANTWriteError("Template geometry payload could not be decoded")
        geometry_children = self.document.hfa.children(geometry_entry)
        if expected_geometry_type == "Text2":
            text_data = geometry_values.get("text")
            text_source = text_data.get("source") if isinstance(text_data, dict) else None
            if not isinstance(text_source, dict) or text_source.get("label") != "EANT_LOCAL":
                raise EANTWriteError("Only local Text2 strings are supported by the observed writer profile")
            if geometry_values.get("coords") not in (None, []):
                raise EANTWriteError("Text along a path is not supported by this Text2 writer profile")
            if not any(child.entry_type == "TextBox_Eant" for child in geometry_children):
                raise EANTWriteError(
                    "Text2 template has no observed TextBox_Eant child; refusing to invent its structure"
                )
        if expected_geometry_type == "Polyline2":
            vector = geometry_values.get("coords")
            vector_source = vector.get("source") if isinstance(vector, dict) else None
            if not isinstance(vector_source, dict) or vector_source.get("label") != "EANT_LOCAL":
                raise EANTWriteError("Only local Polyline2 coordinate vectors are supported")
            basedata = vector.get("coords") if isinstance(vector, dict) else None
            if not isinstance(basedata, dict) or basedata.get("item_type") != "f64":
                raise EANTWriteError("Polyline2 template does not use the observed f64 coordinate data")

        # Choose an ID that is not used anywhere in the annotation tree.
        chosen_id = self._choose_annotation_id(new_id)

        parent_values = self._clean(parent_source)
        parent_values["id"] = chosen_id
        parent_values["name"] = new_name
        if description is not None:
            if not isinstance(description, str) or "\x00" in description:
                raise EANTWriteError("description must be a string without NUL")
            try:
                description.encode("latin-1")
            except UnicodeEncodeError as exc:
                raise EANTWriteError("description is not representable in the observed Latin-1 encoding") from exc
            parent_values["description"] = description
        parent_values["bBox"] = self._bbox_values(bbox)

        updated_geometry = self._clean(geometry_values)
        update_geometry(updated_geometry)

        specs: List[HFAEntrySpec] = []
        parent_key = "new-annotation-parent"
        # The supplied classic Element_Eant sample uses the literal HFA entry
        # name "AntElement" for every ellipse; the modern Element_2_Eant
        # sample uses "AntElement_<id>". Keep these schema-specific names.
        new_hfa_entry_name = (
            "AntElement" if source_entry.entry_type == "Element_Eant"
            else f"AntElement_{chosen_id}"
        )
        specs.append(HFAEntrySpec(
            key=parent_key,
            name=new_hfa_entry_name,
            entry_type=source_entry.entry_type,
            parent=source_entry.parent_pointer,
            payload=lambda absolute, values=parent_values, type_name=source_entry.entry_type:
                self.dictionary.encode_instance(type_name, values, absolute_offset=absolute),
            modified_time=source_entry.modified_time,
        ))

        geometry_key = "new-annotation-geometry"
        specs.append(HFAEntrySpec(
            key=geometry_key,
            name=geometry_entry.name,
            entry_type=geometry_entry.entry_type,
            parent=parent_key,
            payload=lambda absolute, values=updated_geometry, type_name=geometry_entry.entry_type:
                self.dictionary.encode_instance(type_name, values, absolute_offset=absolute),
            modified_time=geometry_entry.modified_time,
        ))

        def add_preserved_subtree(parent_key_or_offset, entry, prefix: str, *, first_values=None):
            decoded = self.document.decoded.get(entry.offset)
            if decoded is None:
                raise EANTWriteError(
                    f"Template child {entry.name!r} ({entry.entry_type}) could not be decoded"
                )
            values = self._clean(decoded) if first_values is None else first_values
            key = f"{prefix}-{len(specs)}"
            specs.append(HFAEntrySpec(
                key=key, name=entry.name, entry_type=entry.entry_type,
                parent=parent_key_or_offset,
                payload=lambda absolute, vals=values, type_name=entry.entry_type:
                    self.dictionary.encode_instance(type_name, vals, absolute_offset=absolute),
                modified_time=entry.modified_time,
            ))
            for child in self.document.hfa.children(entry):
                add_preserved_subtree(key, child, prefix)

        # Preserve the template's ancillary sibling records and the geometry
        # subtree (e.g. TextBox_Eant beneath Text2) in their original order.
        for child in source_children:
            if child.offset == geometry_entry.offset:
                for nested in self.document.hfa.children(child):
                    nested_values = self._clean(self.document.decoded[nested.offset])
                    if update_children is not None:
                        nested_values = update_children(nested.entry_type, nested_values)
                    add_preserved_subtree(geometry_key, nested, "new-geometry-child", first_values=nested_values)
            else:
                add_preserved_subtree(parent_key, child, "new-annotation-aux")

        try:
            output = self.hfa_writer.append_entries(specs)
        except (HFAWriteError, HFADictionaryError) as exc:
            raise EANTWriteError(f"Failed to create {expected_geometry_type} annotation: {exc}") from exc

        verified = EANTDocument(output)
        if verified.decode_errors:
            raise EANTWriteError(
                f"Generated file has {len(verified.decode_errors)} typed payload decode errors"
            )
        created = next((
            entry for entry in verified.hfa.entries
            if entry.name == new_hfa_entry_name
            and entry.entry_type == source_entry.entry_type
            and verified.decoded.get(entry.offset, {}).get("id") == chosen_id
        ), None)
        if created is None:
            raise EANTWriteError("New annotation parent is not reachable after serialization")
        created_children = verified.hfa.children(created)
        created_geometry = next((item for item in created_children if item.entry_type == expected_geometry_type), None)
        if created_geometry is None:
            raise EANTWriteError("New annotation geometry is not reachable after serialization")
        parsed_annotation = next((
            item for item in verified.iter_annotations() if item.entry_offset == created.offset
        ), None)
        if parsed_annotation is None or parsed_annotation.element_id != chosen_id:
            raise EANTWriteError("New annotation did not survive EANT semantic re-parsing")

        return EANTCreateResult(
            data=output, new_entry_offset=created.offset,
            new_geometry_offset=created_geometry.offset, new_element_id=chosen_id,
            new_name=new_name, element_schema=source_entry.entry_type,
            geometry_entry_type=expected_geometry_type,
        )

    def create_text_annotation(
        self, template_entry_offset: int, *, new_name: str, text: str,
        origin: Sequence[float], bbox: Sequence[Sequence[float]],
        description: Optional[str] = None,
        orientation: Optional[float] = None, scale: Optional[float] = None,
        horizontal_alignment: Optional[str] = None, vertical_alignment: Optional[str] = None,
        text_box_width: Optional[float] = None, new_id: Optional[int] = None,
    ) -> EANTCreateResult:
        """Create Text2 using an observed modern-schema text annotation as template.

        ``origin`` and ``bbox`` must use the template's respective EANT
        coordinate spaces. Text is encoded using the observed Latin-1 pointer
        string representation; unsupported Unicode is rejected, never replaced.
        Orientation is passed as the raw EANT schema value (unit conventions
        are not inferred).
        """
        if not isinstance(text, str) or "\x00" in text:
            raise EANTWriteError("text must be a string without NUL")
        try:
            text.encode("latin-1")
        except UnicodeEncodeError as exc:
            raise EANTWriteError(
                "Text is not representable in the observed Latin-1 EANT string encoding; refusing data loss"
            ) from exc
        x, y = self._finite_pair(origin, "origin")
        orientation_value = None if orientation is None else self._finite_scalar(orientation, "orientation")
        scale_value = None if scale is None else self._finite_scalar(scale, "scale")
        text_box_width_value = None if text_box_width is None else self._finite_scalar(text_box_width, "text_box_width")
        if scale_value is not None and scale_value <= 0:
            raise EANTWriteError("scale must be greater than zero")
        if text_box_width_value is not None and text_box_width_value < 0:
            raise EANTWriteError("text_box_width cannot be negative")

        def update(values):
            if not isinstance(values.get("text"), dict) or not isinstance(values.get("origin"), dict):
                raise EANTWriteError("Text2 template is missing observed text/origin structures")
            values["origin"].update({"x": x, "y": y})
            values["text"]["string"] = text
            if orientation_value is not None:
                values["orientation"] = orientation_value
            if scale_value is not None:
                values["scale"] = scale_value
            if horizontal_alignment is not None:
                labels = [field.enum_values for field in self.dictionary.get_type("Eevg_Align").fields if field.name == "xalign"]
                if not labels or horizontal_alignment not in labels[0]:
                    raise EANTWriteError(f"Unsupported horizontal alignment {horizontal_alignment!r}")
                values.setdefault("alignment", {})["xalign"] = {"code": labels[0].index(horizontal_alignment), "label": horizontal_alignment}
            if vertical_alignment is not None:
                labels = [field.enum_values for field in self.dictionary.get_type("Eevg_Align").fields if field.name == "yalign"]
                if not labels or vertical_alignment not in labels[0]:
                    raise EANTWriteError(f"Unsupported vertical alignment {vertical_alignment!r}")
                values.setdefault("alignment", {})["yalign"] = {"code": labels[0].index(vertical_alignment), "label": vertical_alignment}

        def update_nested(entry_type, values):
            if entry_type == "TextBox_Eant" and text_box_width_value is not None:
                values["boxWidth"] = text_box_width_value
            return values

        return self._create_from_template(
            template_entry_offset, new_name=new_name, bbox=bbox, description=description,
            expected_geometry_type="Text2", update_geometry=update,
            new_id=new_id, update_children=update_nested,
        )

    def create_rectangle_annotation(
        self, template_entry_offset: int, *, new_name: str,
        center: Sequence[float], width: float, height: float,
        bbox: Sequence[Sequence[float]], description: Optional[str] = None,
        orientation: Optional[float] = None,
        new_id: Optional[int] = None,
    ) -> EANTCreateResult:
        """Create Rectangle2, inheriting style, units and transforms from a template."""
        x, y = self._finite_pair(center, "center")
        width = self._finite_scalar(width, "rectangle width")
        height = self._finite_scalar(height, "rectangle height")
        if width <= 0 or height <= 0:
            raise EANTWriteError("rectangle width and height must be greater than zero")
        orientation_value = None if orientation is None else self._finite_scalar(orientation, "orientation")

        def update(values):
            if not isinstance(values.get("center"), dict):
                raise EANTWriteError("Rectangle2 template is missing its center coordinate")
            values["center"].update({"x": x, "y": y})
            values["width"] = width
            values["height"] = height
            if orientation_value is not None:
                values["orientation"] = orientation_value

        return self._create_from_template(
            template_entry_offset, new_name=new_name, bbox=bbox, description=description,
            expected_geometry_type="Rectangle2", update_geometry=update, new_id=new_id,
        )

    def create_polyline_annotation(
        self, template_entry_offset: int, *, new_name: str,
        points: Sequence[Sequence[float]], bbox: Sequence[Sequence[float]],
        description: Optional[str] = None,
        new_id: Optional[int] = None,
    ) -> EANTCreateResult:
        """Create Polyline2 with a two-row, interleaved XY f64 BASEDATA vector."""
        if not isinstance(points, (list, tuple)) or len(points) < 2:
            raise EANTWriteError("polyline requires at least two points")
        normalized = [self._finite_pair(point, "polyline point") for point in points]

        def update(values):
            vector = values.get("coords")
            if not isinstance(vector, dict) or not isinstance(vector.get("coords"), dict):
                raise EANTWriteError("Polyline2 template is missing VectorData_2_Eant coordinates")
            old = vector["coords"]
            vector["coords"] = {
                "rows": 2, "columns": len(normalized), "item_type": "f64",
                "object_type": 2,
                "values": [coordinate for point in normalized for coordinate in point],
            }

        return self._create_from_template(
            template_entry_offset, new_name=new_name, bbox=bbox, description=description,
            expected_geometry_type="Polyline2", update_geometry=update, new_id=new_id,
        )

    def create_ellipse_annotation(
        self, template_entry_offset: int, *, new_name: str,
        center: Sequence[float], semi_major_axis: float, semi_minor_axis: float,
        bbox: Sequence[Sequence[float]], description: Optional[str] = None,
        orientation: Optional[float] = None,
        new_id: Optional[int] = None,
    ) -> EANTCreateResult:
        """Create the observed classic Eant_Ellipse schema from a classic template."""
        x, y = self._finite_pair(center, "center")
        major = self._finite_scalar(semi_major_axis, "ellipse semi-major axis")
        minor = self._finite_scalar(semi_minor_axis, "ellipse semi-minor axis")
        if major <= 0 or minor <= 0:
            raise EANTWriteError("ellipse semi-axes must be greater than zero")
        orientation_value = None if orientation is None else self._finite_scalar(orientation, "orientation")

        def update(values):
            if not isinstance(values.get("center"), dict):
                raise EANTWriteError("Eant_Ellipse template is missing its center coordinate")
            values["center"].update({"x": x, "y": y})
            values["semiMajorAxis"] = major
            values["semiMinorAxis"] = minor
            if orientation_value is not None:
                values["orientation"] = orientation_value

        return self._create_from_template(
            template_entry_offset, new_name=new_name, bbox=bbox, description=description,
            expected_geometry_type="Eant_Ellipse", update_geometry=update, new_id=new_id,
        )

    def clone_simple_annotation(
        self,
        source_entry_offset: int,
        *,
        new_name: str,
        new_id: Optional[int] = None,
    ) -> EANTCloneResult:
        """Append a non-group annotation with identical geometry/style.

        Parent metadata and all non-element child records are re-encoded from
        the source dictionary. Nested element groups are rejected because they
        need group-specific semantics and ID remapping.
        """
        if not new_name or "\x00" in new_name:
            raise EANTWriteError("new_name must be a non-empty string without NUL")
        try:
            encoded_name = new_name.encode("latin-1")
        except UnicodeEncodeError as exc:
            raise EANTWriteError(
                "new_name must be representable in the current HFA single-byte text encoding"
            ) from exc
        if len(encoded_name) > 255:
            raise EANTWriteError("new_name exceeds the conservative 255-byte text limit")

        source_entry = self._find_source(source_entry_offset)
        source_values = self.document.decoded.get(source_entry.offset)
        if source_values is None:
            raise EANTWriteError("Source element payload could not be decoded")
        element_type = source_values.get("elmType")
        type_label = element_type.get("label") if isinstance(element_type, dict) else None
        if type_label == "EANT_GROUP":
            raise EANTWriteError("Group cloning is not implemented")

        child_entries = self.document.hfa.children(source_entry)
        if not child_entries:
            raise EANTWriteError("Source element has no typed geometry child")
        if any(child.entry_type in {"Element_Eant", "Element_2_Eant"} for child in child_entries):
            raise EANTWriteError("Nested element children are not supported by simple clone")

        # Determine a fresh ID while allowing a caller-specified non-colliding ID.
        chosen_id = self._choose_annotation_id(new_id)

        parent_values = self._clean(source_values)
        parent_values["id"] = chosen_id
        parent_values["name"] = new_name

        specs: List[HFAEntrySpec] = []
        parent_key = "cloned-element"
        specs.append(HFAEntrySpec(
            key=parent_key,
            name=f"AntElement_{chosen_id}",
            entry_type=source_entry.entry_type,
            parent=source_entry.parent_pointer,
            payload=lambda absolute, values=parent_values, type_name=source_entry.entry_type:
                self.dictionary.encode_instance(type_name, values, absolute_offset=absolute),
            modified_time=source_entry.modified_time,
        ))

        def add_subtree(parent_key_or_offset: Union[str, int], entry, key_prefix: str) -> None:
            decoded = self.document.decoded.get(entry.offset)
            if decoded is None:
                raise EANTWriteError(
                    f"Child {entry.name!r} ({entry.entry_type}) could not be decoded"
                )
            clean_values = self._clean(decoded)
            key = f"{key_prefix}-{len(specs)}"
            specs.append(HFAEntrySpec(
                key=key,
                name=entry.name,
                entry_type=entry.entry_type,
                parent=parent_key_or_offset,
                payload=lambda absolute, values=clean_values, type_name=entry.entry_type:
                    self.dictionary.encode_instance(type_name, values, absolute_offset=absolute),
                modified_time=entry.modified_time,
            ))
            for child in self.document.hfa.children(entry):
                if child.entry_type in {"Element_Eant", "Element_2_Eant"}:
                    raise EANTWriteError("Nested elements cannot be cloned as simple annotation children")
                add_subtree(key, child, key_prefix)

        for child in child_entries:
            add_subtree(parent_key, child, "cloned-child")

        try:
            output = self.hfa_writer.append_entries(specs)
        except (HFAWriteError, HFADictionaryError) as exc:
            raise EANTWriteError(f"Failed to encode cloned EANT element: {exc}") from exc

        # Re-open the newly written file and ensure the annotation is reachable
        # and decodable before returning it to the caller.
        verified = EANTDocument(output)
        new_entry = next((
            entry for entry in verified.hfa.entries
            if entry.name == f"AntElement_{chosen_id}"
            and entry.entry_type == source_entry.entry_type
        ), None)
        if new_entry is None:
            raise EANTWriteError("New EANT entry is not reachable after serialization")
        if verified.decode_errors:
            raise EANTWriteError(
                f"Generated file has {len(verified.decode_errors)} typed payload decode errors"
            )
        return EANTCloneResult(
            data=output,
            new_entry_offset=new_entry.offset,
            new_element_id=chosen_id,
            new_name=new_name,
            source_entry_offset=source_entry_offset,
        )
