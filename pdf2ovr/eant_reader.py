"""Semantic reader for the EANT annotation structures observed in OVR files.

The reader preserves unknown records and exposes decoded dictionaries instead
of claiming that every annotation family is understood. Coordinate transforms
are retained as metadata and are not silently applied to geometry.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Union

from .hfa_dictionary import HFADictionary, HFADictionaryError
from .hfa_structure import HFAEntry, HFAFormatError, HFAReader


_ELEMENT_TYPES = {"Element_Eant", "Element_2_Eant"}


@dataclass
class EANTAnnotation:
    entry_offset: int
    element_schema: str
    name: str
    description: str
    element_id: Optional[int]
    element_type_code: Optional[int]
    element_type: str
    geometry_entry_type: Optional[str]
    geometry: Dict[str, Any] = field(default_factory=dict)
    group_metadata: Optional[Dict[str, Any]] = None
    auxiliary_entries: List[Dict[str, Any]] = field(default_factory=list)
    transform: Dict[str, Any] = field(default_factory=dict)
    children: List["EANTAnnotation"] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entry_offset": self.entry_offset,
            "element_schema": self.element_schema,
            "name": self.name,
            "description": self.description,
            "element_id": self.element_id,
            "element_type_code": self.element_type_code,
            "element_type": self.element_type,
            "geometry_entry_type": self.geometry_entry_type,
            "geometry": self.geometry,
            "group_metadata": self.group_metadata,
            "auxiliary_entries": self.auxiliary_entries,
            "transform": self.transform,
            "children": [child.to_dict() for child in self.children],
        }


class EANTReadError(ValueError):
    """Raised when an EANT structure cannot be interpreted safely."""


class EANTDocument:
    """Read HFA/EHFA container and the EANT structures found in an OVR."""

    def __init__(self, source: Union[str, Path, bytes, bytearray]):
        if isinstance(source, (str, Path)):
            self.path: Optional[Path] = Path(source)
            raw = self.path.read_bytes()
        else:
            self.path = None
            raw = bytes(source)
        self.raw = raw
        self.hfa = HFAReader(raw)
        self.hfa_summary = self.hfa.validate()
        try:
            self.dictionary = HFADictionary(self.hfa.dictionary)
        except HFADictionaryError as exc:
            raise EANTReadError(f"Could not parse embedded HFA dictionary: {exc}") from exc
        self.decoded: Dict[int, Dict[str, Any]] = {}
        self.decode_errors: List[Dict[str, Any]] = []
        self._decode_reachable_entries()
        self.annotation_root = self._find_annotation_root()
        self.element_list = self._find_element_list()
        self.annotations: List[EANTAnnotation] = []
        if self.element_list is not None:
            self.annotations = [
                self._read_annotation(entry)
                for entry in self.hfa.children(self.element_list)
                if entry.entry_type in _ELEMENT_TYPES
            ]

    def _decode_reachable_entries(self) -> None:
        for entry in self.hfa.entries:
            if not entry.data_size or entry.entry_type not in self.dictionary.types:
                continue
            payload = self.raw[entry.data_pointer:entry.data_end]
            try:
                self.decoded[entry.offset] = self.dictionary.decode_instance(
                    entry.entry_type, payload,
                    absolute_offset=entry.data_pointer,
                )
            except HFADictionaryError as exc:
                self.decode_errors.append({
                    "entry_offset": entry.offset,
                    "entry_name": entry.name,
                    "entry_type": entry.entry_type,
                    "error": str(exc),
                })

    def _find_annotation_root(self) -> Optional[HFAEntry]:
        candidates = [
            entry for entry in self.hfa.entries
            if entry.entry_type == "AntHeader_Eant"
        ]
        if not candidates:
            return None
        named = next((entry for entry in candidates if entry.name == "Annotation"), None)
        return named or candidates[0]

    def _find_element_list(self) -> Optional[HFAEntry]:
        if self.annotation_root is None:
            return None
        return next((
            child for child in self.hfa.children(self.annotation_root)
            if child.entry_type == "ElementNode_Eant" and child.name == "ElementList"
        ), None)

    @staticmethod
    def _enum(value: Any) -> tuple[Optional[int], str]:
        if isinstance(value, dict) and "code" in value:
            code = value.get("code")
            label = value.get("label")
            return (int(code) if isinstance(code, int) else None, str(label or "UNKNOWN"))
        if isinstance(value, int):
            return value, "UNKNOWN"
        return None, str(value or "UNKNOWN")

    @staticmethod
    def _strip_internal(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: EANTDocument._strip_internal(item) for key, item in value.items() if not key.startswith("_")}
        if isinstance(value, list):
            return [EANTDocument._strip_internal(item) for item in value]
        return value

    @staticmethod
    def _coord_pair(value: Any) -> Optional[tuple[float, float]]:
        if not isinstance(value, dict):
            return None
        x, y = value.get("x"), value.get("y")
        if isinstance(x, (float, int)) and isinstance(y, (float, int)):
            return float(x), float(y)
        return None

    @staticmethod
    def _points_from_vector(vector: Any) -> List[tuple[float, float]]:
        """Decode the observed row-major XY pair representation when present."""
        if not isinstance(vector, dict):
            return []
        basedata = vector.get("coords")
        if not isinstance(basedata, dict):
            return []
        values = basedata.get("values")
        if not isinstance(values, list) or len(values) % 2:
            return []
        return [
            (float(values[index]), float(values[index + 1]))
            for index in range(0, len(values), 2)
            if isinstance(values[index], (int, float))
            and isinstance(values[index + 1], (int, float))
        ]

    def _geometry_payload(self, entry: Optional[HFAEntry]) -> Dict[str, Any]:
        if entry is None:
            return {}
        values = self.decoded.get(entry.offset, {})
        result: Dict[str, Any] = self._strip_internal(values)
        result["entry_type"] = entry.entry_type

        if entry.entry_type in {"Polyline2", "Polyline_Eant"}:
            coords = values.get("coords")
            result["points"] = self._points_from_vector(coords)
        elif entry.entry_type == "Text2":
            origin = self._coord_pair(values.get("origin"))
            if origin is not None:
                result["origin_xy"] = origin
            text_data = values.get("text")
            if isinstance(text_data, dict):
                result["text_value"] = text_data.get("string", "")
                result["text_source"] = self._enum(text_data.get("source"))[1]
            result["text_style_index"] = values.get("textStyle")
            result["orientation"] = values.get("orientation")
            result["scale"] = values.get("scale")
            result["alignment"] = self._strip_internal(values.get("alignment"))
        elif entry.entry_type == "Rectangle2":
            center = self._coord_pair(values.get("center"))
            if center is not None:
                result["center_xy"] = center
        elif entry.entry_type == "Eant_Ellipse":
            center = self._coord_pair(values.get("center"))
            if center is not None:
                result["center_xy"] = center
        return result

    def _entry_summary(self, entry: HFAEntry) -> Dict[str, Any]:
        return {
            "entry_offset": entry.offset,
            "name": entry.name,
            "entry_type": entry.entry_type,
            "values": self._strip_internal(self.decoded.get(entry.offset, {})),
            "children": [self._entry_summary(child) for child in self.hfa.children(entry)],
        }

    def _read_annotation(self, entry: HFAEntry) -> EANTAnnotation:
        values = self.decoded.get(entry.offset, {})
        type_code, type_label = self._enum(values.get("elmType"))
        children = self.hfa.children(entry)
        nested_entries = [child for child in children if child.entry_type in _ELEMENT_TYPES]
        info_entries = [child for child in children if child.entry_type not in _ELEMENT_TYPES]

        geometry_entry = None
        # A group can have a Group_Eant metadata entry followed by nested
        # Element_* siblings. For a non-group, the first typed info child is
        # the geometry payload. Keep additional info children in raw metadata.
        for candidate in info_entries:
            if candidate.entry_type in {
                "Text2", "Rectangle2", "Polyline2", "Eant_Ellipse",
                "Polyline_Eant", "Eant_Rectangle", "Eant_Text",
            }:
                geometry_entry = candidate
                break

        transform = {
            key: self._strip_internal(values.get(key))
            for key in ("scaleUnits", "positionUnits", "xformMatrix", "ixformMatrix", "bBox")
            if key in values
        }
        geometry = self._geometry_payload(geometry_entry)
        if geometry_entry is not None:
            geometry["child_entries"] = [
                self._entry_summary(child) for child in self.hfa.children(geometry_entry)
            ]
        auxiliary_entries = [
            self._entry_summary(item) for item in info_entries if item is not geometry_entry
        ]
        group_metadata = None
        group_entry = next((item for item in info_entries if item.entry_type == "Group_Eant"), None)
        if group_entry is not None:
            group_values = self.decoded.get(group_entry.offset, {})
            group_code, group_label = self._enum(group_values.get("type"))
            group_metadata = {
                "group_type_code": group_code,
                "group_type": group_label,
                "fields": self._strip_internal(group_values),
                "child_entries": [
                    self._entry_summary(child) for child in self.hfa.children(group_entry)
                ],
            }
        return EANTAnnotation(
            entry_offset=entry.offset,
            element_schema=entry.entry_type,
            name=str(values.get("name") or entry.name),
            description=str(values.get("description") or ""),
            element_id=values.get("id") if isinstance(values.get("id"), int) else None,
            element_type_code=type_code,
            element_type=type_label,
            geometry_entry_type=geometry_entry.entry_type if geometry_entry else None,
            geometry=geometry,
            group_metadata=group_metadata,
            auxiliary_entries=auxiliary_entries,
            transform=transform,
            children=[self._read_annotation(child) for child in nested_entries],
        )

    def iter_annotations(self, *, recursive: bool = True) -> Iterator[EANTAnnotation]:
        def walk(items: Iterable[EANTAnnotation]) -> Iterator[EANTAnnotation]:
            for item in items:
                yield item
                if recursive:
                    yield from walk(item.children)
        yield from walk(self.annotations)

    def find_entries(self, entry_type: str) -> List[HFAEntry]:
        return [entry for entry in self.hfa.entries if entry.entry_type == entry_type]

    def map_info(self) -> Optional[Dict[str, Any]]:
        entries = self.find_entries("Eprj_MapInfo")
        if not entries:
            return None
        values = self.decoded.get(entries[0].offset)
        if values is None:
            return None
        return self._strip_internal(values)

    def style_entries(self) -> List[Dict[str, Any]]:
        style_types = {"StyleLibrary_Eant", "StyleLibrary_5_Eant", "EantStyleList", "TextStyle", "FillStyle2", "LineStyle4", "Color2"}
        result = []
        for entry in self.hfa.entries:
            if entry.entry_type in style_types:
                result.append({
                    "name": entry.name,
                    "entry_type": entry.entry_type,
                    "entry_offset": entry.offset,
                    "values": self._strip_internal(self.decoded.get(entry.offset, {})),
                })
        return result

    def symbol_entries(self) -> List[Dict[str, Any]]:
        symbol_types = {"SymbolLibrary_Eant", "SymbolLibrary_4_Eant", "SymbolData_Eant"}
        return [
            {"name": entry.name, "entry_type": entry.entry_type,
             "entry_offset": entry.offset, "values": self._strip_internal(self.decoded.get(entry.offset, {}))}
            for entry in self.hfa.entries if entry.entry_type in symbol_types
        ]

    def summary(self) -> Dict[str, Any]:
        flat = list(self.iter_annotations(recursive=True))
        type_counts: Dict[str, int] = {}
        schema_counts: Dict[str, int] = {}
        for annotation in flat:
            type_counts[annotation.element_type] = type_counts.get(annotation.element_type, 0) + 1
            schema_counts[annotation.element_schema] = schema_counts.get(annotation.element_schema, 0) + 1
        return {
            "hfa": self.hfa_summary,
            "dictionary_type_count": len(self.dictionary.types),
            "unresolved_dictionary_references": list(self.dictionary.missing_types),
            "annotation_root_offset": self.annotation_root.offset if self.annotation_root else None,
            "element_list_offset": self.element_list.offset if self.element_list else None,
            "top_level_annotation_count": len(self.annotations),
            "recursive_annotation_count": len(flat),
            # Keep the CLI's clone workflow discoverable without dumping every
            # geometry/style field into the default inspection summary.
            "annotations": [
                {
                    "entry_offset": item.entry_offset,
                    "element_id": item.element_id,
                    "name": item.name,
                    "element_type": item.element_type,
                    "element_schema": item.element_schema,
                    "geometry_entry_type": item.geometry_entry_type,
                }
                for item in flat
            ],
            "annotation_type_counts": type_counts,
            "element_schema_counts": schema_counts,
            "entry_type_counts": {
                entry_type: sum(1 for entry in self.hfa.entries if entry.entry_type == entry_type)
                for entry_type in sorted({entry.entry_type for entry in self.hfa.entries})
            },
            "style_entry_count": len(self.style_entries()),
            "symbol_entry_count": len(self.symbol_entries()),
            "decode_error_count": len(self.decode_errors),
            "decode_errors": self.decode_errors,
            "map_info": self.map_info(),
        }
