"""Parser and bounded decoder for the embedded HFA type dictionary.

The dictionary grammar and primitive widths follow GDAL's HFA
implementation (HFADictionary/HFAType/HFAField). The encoder only writes
schema-defined inline payloads and must be given their final absolute offsets.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
import struct
from typing import Any, Dict, List, Optional, Sequence, Tuple


class HFADictionaryError(ValueError):
    """Malformed or unsupported HFA dictionary/data instance."""


@dataclass(frozen=True)
class HFAFieldDef:
    count: int
    pointer: Optional[str]
    kind: str
    name: str
    object_type: Optional[str] = None
    enum_values: Tuple[str, ...] = ()
    inline_type: Optional["HFATypeDef"] = None


@dataclass(frozen=True)
class HFATypeDef:
    name: str
    fields: Tuple[HFAFieldDef, ...]


_PRIMITIVE_SIZES = {
    "1": 1, "2": 1, "4": 1, "c": 1, "C": 1,
    "e": 2, "s": 2, "S": 2,
    "t": 4, "l": 4, "L": 4, "f": 4,
    "d": 8, "m": 8, "M": 16,
}


class _DictionaryParser:
    def __init__(self, text: str):
        self.text = text
        self.pos = 0
        self.types: Dict[str, HFATypeDef] = {}

    def _read_uint(self, what: str) -> int:
        start = self.pos
        while self.pos < len(self.text) and self.text[self.pos].isdigit():
            self.pos += 1
        if self.pos == start:
            raise HFADictionaryError(f"Expected unsigned integer for {what} at {self.pos}")
        value = int(self.text[start:self.pos])
        if value > 10_000_000:
            raise HFADictionaryError(f"Unreasonable {what}: {value}")
        return value

    def _read_token(self, what: str, *, allow_empty: bool = False) -> str:
        end = self.text.find(",", self.pos)
        if end < 0:
            raise HFADictionaryError(f"Unterminated {what} at {self.pos}")
        token = self.text[self.pos:end]
        if not token and not allow_empty:
            raise HFADictionaryError(f"Empty {what} at {self.pos}")
        self.pos = end + 1
        return token

    def _parse_fields(self, nested: bool = False) -> Tuple[HFAFieldDef, ...]:
        if self.pos >= len(self.text) or self.text[self.pos] != "{":
            raise HFADictionaryError(f"Expected '{{' at {self.pos}")
        self.pos += 1
        fields: List[HFAFieldDef] = []
        while self.pos < len(self.text) and self.text[self.pos] != "}":
            count = self._read_uint("field count")
            if self.pos >= len(self.text) or self.text[self.pos] != ":":
                raise HFADictionaryError(f"Expected ':' after field count at {self.pos}")
            self.pos += 1
            pointer = None
            if self.pos < len(self.text) and self.text[self.pos] in "p*":
                pointer = self.text[self.pos]
                self.pos += 1
            if self.pos >= len(self.text):
                raise HFADictionaryError("Unexpected end in field type")
            kind = self.text[self.pos]
            self.pos += 1
            object_type = None
            enum_values: Tuple[str, ...] = ()
            inline_type = None

            if kind == "o":
                object_type = self._read_token("object type")
                # _read_token consumed the comma before the field name.
                name = self._read_token("field name")
            elif kind == "x":
                inline_fields = self._parse_fields(nested=True)
                # Inline definitions are followed by a type name, then field name.
                object_type = self._read_token("inline object type")
                name = self._read_token("field name")
                inline_type = HFATypeDef(object_type, inline_fields)
            elif kind == "e":
                enum_count = self._read_uint("enum count")
                if enum_count > 10000:
                    raise HFADictionaryError(f"Unreasonable enum count: {enum_count}")
                if self.pos >= len(self.text) or self.text[self.pos] != ":":
                    raise HFADictionaryError(f"Expected ':' after enum count at {self.pos}")
                self.pos += 1
                vals = []
                for _ in range(enum_count):
                    vals.append(self._read_token("enum value", allow_empty=True))
                enum_values = tuple(vals)
                name = self._read_token("field name")
            elif kind in _PRIMITIVE_SIZES or kind == "b":
                name = self._read_token("field name")
            else:
                raise HFADictionaryError(f"Unknown dictionary item kind {kind!r} at {self.pos - 1}")

            fields.append(HFAFieldDef(count, pointer, kind, name, object_type, enum_values, inline_type))
        if self.pos >= len(self.text) or self.text[self.pos] != "}":
            raise HFADictionaryError("Unterminated type definition")
        self.pos += 1
        return tuple(fields)

    def parse(self) -> Dict[str, HFATypeDef]:
        while self.pos < len(self.text):
            # HFA dictionaries terminate with '.', sometimes followed by NULs.
            if self.text[self.pos] == ".":
                break
            if self.text[self.pos] != "{":
                # GDAL tolerates preamble bytes before the next '{'.
                next_open = self.text.find("{", self.pos)
                if next_open < 0:
                    break
                self.pos = next_open
            fields = self._parse_fields()
            name = self._read_token("type name")
            if name in self.types:
                raise HFADictionaryError(f"Duplicate HFA type definition: {name}")
            self.types[name] = HFATypeDef(name, fields)
        if not self.types:
            raise HFADictionaryError("No HFA types found in dictionary")
        return self.types


class HFADictionary:
    """Parsed HFA dictionary with safe, bounded instance decoding."""

    def __init__(self, raw: bytes | str, *, max_depth: int = 32, max_items: int = 1_000_000):
        if isinstance(raw, bytes):
            raw = raw.split(b"\x00", 1)[0].decode("latin-1")
        self.raw_text = raw
        self.max_depth = max_depth
        self.max_items = max_items
        self.types = _DictionaryParser(raw).parse()
        self._resolve_types()

    def _resolve_types(self) -> None:
        missing = []
        for typedef in self.types.values():
            for field_def in typedef.fields:
                if field_def.kind == "o" and field_def.object_type not in self.types:
                    missing.append((typedef.name, field_def.name, field_def.object_type))
        # Missing refs are retained as opaque rather than treating unrelated
        # vendor/private types as invalid.
        self.missing_types = tuple(missing)

    def type_names(self) -> List[str]:
        return list(self.types)

    def get_type(self, name: str) -> HFATypeDef:
        try:
            return self.types[name]
        except KeyError as exc:
            raise HFADictionaryError(f"Type {name!r} is not defined in this file dictionary") from exc

    @staticmethod
    def _unpack_primitive(kind: str, data: memoryview, pos: int, count: int, enums: Sequence[str]) -> Tuple[Any, int]:
        if kind in "124":
            size = count
            if pos + size > len(data):
                raise HFADictionaryError("Bitfield exceeds instance boundary")
            raw = bytes(data[pos:pos + size])
            # Preserve packed fields exactly; the exact bit width is schema-dependent.
            return raw, pos + size
        if kind in ("c", "C"):
            if pos + count > len(data):
                raise HFADictionaryError("Character field exceeds instance boundary")
            raw = bytes(data[pos:pos + count])
            if kind == "c":
                return raw.split(b"\x00", 1)[0].decode("latin-1"), pos + count
            vals = [int.from_bytes(raw[i:i+1], "little", signed=True) for i in range(count)]
            return (vals[0] if count == 1 else vals), pos + count
        size = _PRIMITIVE_SIZES[kind]
        total = size * count
        if pos + total > len(data):
            raise HFADictionaryError(f"Primitive {kind!r} field exceeds instance boundary")
        values: List[Any] = []
        fmt = {
            "e": "<H", "s": "<H", "S": "<h", "t": "<I", "l": "<I",
            "L": "<i", "f": "<f", "d": "<d", "m": "<ff", "M": "<dd",
        }[kind]
        unit = struct.calcsize(fmt)
        for i in range(count):
            value = struct.unpack_from(fmt, data, pos + i * unit)
            if kind == "e":
                code = value[0]
                values.append({"code": code, "label": enums[code] if code < len(enums) else None})
            elif kind in ("m", "M"):
                values.append(tuple(value))
            else:
                values.append(value[0])
        return (values[0] if count == 1 else values), pos + total

    def decode_instance(
        self,
        type_name: str,
        payload: bytes,
        *,
        absolute_offset: int = 0,
        include_raw: bool = False,
    ) -> Dict[str, Any]:
        data = memoryview(payload)
        result, final_pos = self._decode_type(self.get_type(type_name), data, 0, absolute_offset, 0)
        result["_decoded_bytes"] = final_pos
        result["_payload_bytes"] = len(data)
        if include_raw:
            result["_raw"] = bytes(payload)
        if final_pos > len(data):
            raise HFADictionaryError("Decoded instance exceeds payload")
        return result

    def _decode_type(
        self,
        typedef: HFATypeDef,
        data: memoryview,
        pos: int,
        absolute_offset: int,
        depth: int,
    ) -> Tuple[Dict[str, Any], int]:
        if depth > self.max_depth:
            raise HFADictionaryError("HFA object recursion depth exceeded")
        result: Dict[str, Any] = {}
        cursor = pos
        for field_def in typedef.fields:
            count = field_def.count
            field_start = cursor
            content_cursor = cursor
            if field_def.pointer:
                if cursor + 8 > len(data):
                    raise HFADictionaryError(f"Pointer header for {typedef.name}.{field_def.name} is truncated")
                count, pointer_target = struct.unpack_from("<II", data, cursor)
                if count > self.max_items:
                    raise HFADictionaryError(f"Unreasonable pointer count in {typedef.name}.{field_def.name}: {count}")
                content_cursor += 8
                if pointer_target and absolute_offset:
                    expected = absolute_offset + content_cursor
                    # Some HFA producers store a nearby/relocated pointer; expose it,
                    # but decode from the in-block bytes as GDAL's HFAField does.
                    pointer_ok = pointer_target == expected
                else:
                    pointer_ok = pointer_target == 0 if count == 0 else None
            else:
                pointer_target = None
                pointer_ok = None
                if count > self.max_items:
                    raise HFADictionaryError(f"Unreasonable fixed count in {typedef.name}.{field_def.name}")

            if field_def.kind == "b":
                if field_def.pointer and count == 0:
                    value, cursor = None, content_cursor
                else:
                    value, cursor = self._decode_basedata(data, content_cursor, absolute_offset)
            elif field_def.kind in _PRIMITIVE_SIZES:
                value, cursor = self._unpack_primitive(field_def.kind, data, content_cursor, count, field_def.enum_values)
            else:
                obj_type = field_def.inline_type
                if obj_type is None and field_def.object_type:
                    obj_type = self.types.get(field_def.object_type)
                if obj_type is None:
                    # Keep pointer and fixed object data opaque only when its length
                    # is explicitly bounded by a future schema extension.
                    raise HFADictionaryError(f"Unresolved object type for {typedef.name}.{field_def.name}: {field_def.object_type}")
                items = []
                for _ in range(count):
                    item, cursor = self._decode_type(obj_type, data, cursor + (8 if _ == 0 and field_def.pointer else 0), absolute_offset, depth + 1)
                    items.append(item)
                    # _decode_type returns end offset; the cursor already excludes
                    # pointer header by starting at its contents.
                    if cursor > len(data):
                        raise HFADictionaryError("Object array exceeds instance boundary")
                value = items[0] if count == 1 else items
                if count == 0:
                    cursor = content_cursor
            if field_def.pointer and count == 0:
                cursor = content_cursor
            result[field_def.name] = value
            if field_def.pointer:
                result[f"_{field_def.name}_pointer"] = {
                    "count": count,
                    "target": pointer_target,
                    "target_matches_inline_data": pointer_ok,
                    "field_offset": field_start,
                }
        return result, cursor

    def _decode_basedata(self, data: memoryview, pos: int, absolute_offset: int) -> Tuple[Dict[str, Any], int]:
        if pos + 12 > len(data):
            raise HFADictionaryError("BASEDATA header is truncated")
        rows, cols, item_type, object_type = struct.unpack_from("<IIHH", data, pos)
        if rows > self.max_items or cols > self.max_items or (rows and cols > self.max_items // rows):
            raise HFADictionaryError("BASEDATA dimensions exceed safety limit")
        # HFA EPT item types; the commonly used numeric geometry encodings are
        # represented here. Unknown types remain raw rather than being guessed.
        sizes = {0: (0, "u1"), 1: (0, "u2"), 2: (0, "u4"), 3: (1, "u8"),
                 4: (1, "s8"), 5: (2, "u16"), 6: (2, "s16"), 7: (4, "u32"),
                 8: (4, "s32"), 9: (4, "f32"), 10: (8, "f64"), 11: (8, "c64"), 12: (16, "c128")}
        if item_type not in sizes:
            raise HFADictionaryError(f"Unknown BASEDATA item type code {item_type}")
        size, label = sizes[item_type]
        n = rows * cols
        start = pos + 12
        if item_type in (0, 1, 2):
            bit_width = {0: 1, 1: 2, 2: 4}[item_type]
            byte_count = (n * bit_width + 7) // 8
        else:
            byte_count = n * size
        end = start + byte_count
        if end > len(data):
            raise HFADictionaryError("BASEDATA values exceed instance boundary")
        raw = bytes(data[start:end])
        values: Any
        if item_type in (0, 1, 2):
            # Packed sub-byte arrays are retained losslessly. Bit ordering is
            # intentionally not guessed here; it is not needed for geometry.
            values = None
        elif item_type in (9, 10):
            fmt = "<f" if item_type == 9 else "<d"
            values = [struct.unpack_from(fmt, raw, i * size)[0] for i in range(n)]
        elif item_type in (11, 12):
            fmt = "<ff" if item_type == 11 else "<dd"
            values = [struct.unpack_from(fmt, raw, i * size) for i in range(n)]
        elif item_type in (5, 6, 7, 8):
            fmt = {5: "<H", 6: "<h", 7: "<I", 8: "<i"}[item_type]
            values = [struct.unpack_from(fmt, raw, i * size)[0] for i in range(n)]
        elif item_type == 4:
            values = [int.from_bytes(raw[i:i+1], "little", signed=True) for i in range(n)]
        else:
            values = [raw[i] for i in range(n)]
        return {"rows": rows, "columns": cols, "item_type": label,
                "object_type": object_type, "values": values, "raw": raw}, end

    def encode_instance(
        self,
        type_name: str,
        values: Dict[str, Any],
        *,
        absolute_offset: int = 0,
    ) -> bytes:
        """Encode one dictionary-defined object.

        Pointer payloads are serialized inline after their 8-byte count/offset
        header, matching HFA's in-entry instance representation. ``absolute_offset``
        must be the actual file offset at which the returned bytes will be stored.
        Unknown/opaque packed fields must be provided as their original raw bytes.
        """
        if not isinstance(values, dict):
            raise HFADictionaryError("Instance values must be a mapping")
        return self._encode_type(self.get_type(type_name), values, absolute_offset, 0)

    def _encode_type(
        self,
        typedef: HFATypeDef,
        values: Dict[str, Any],
        absolute_offset: int,
        depth: int,
    ) -> bytes:
        if depth > self.max_depth:
            raise HFADictionaryError("HFA object recursion depth exceeded while encoding")
        output = bytearray()
        for field_def in typedef.fields:
            value = values.get(field_def.name)
            if field_def.pointer:
                if value is None:
                    output.extend(struct.pack("<II", 0, 0))
                    continue
                content_offset = absolute_offset + len(output) + 8
                content = self._encode_field_content(
                    field_def, value, content_offset, depth + 1
                )
                if field_def.kind == "b":
                    count = 1
                elif field_def.kind in ("o", "x"):
                    count = len(value) if isinstance(value, list) else 1
                elif field_def.kind in ("c", "C"):
                    if isinstance(value, str):
                        count = len(value.encode("latin-1")) + 1
                    elif isinstance(value, (bytes, bytearray)):
                        count = len(value) + (0 if value.endswith(b"\x00") else 1)
                    else:
                        count = len(content)
                elif isinstance(value, (list, tuple)):
                    count = len(value)
                else:
                    count = 1
                if count > self.max_items:
                    raise HFADictionaryError(f"Too many items in {typedef.name}.{field_def.name}")
                output.extend(struct.pack("<II", count, content_offset if content else 0))
                output.extend(content)
            else:
                if field_def.kind == "b":
                    output.extend(self._encode_basedata(value, field_def.count))
                else:
                    output.extend(self._encode_field_content(
                        field_def, value, absolute_offset + len(output), depth + 1
                    ))
        return bytes(output)

    def _encode_field_content(
        self,
        field_def: HFAFieldDef,
        value: Any,
        absolute_offset: int,
        depth: int,
    ) -> bytes:
        kind = field_def.kind
        count = field_def.count
        if kind == "b":
            return self._encode_basedata(value, count)
        if kind in ("o", "x"):
            typedef = field_def.inline_type
            if typedef is None and field_def.object_type:
                typedef = self.types.get(field_def.object_type)
            if typedef is None:
                raise HFADictionaryError(
                    f"Unresolved object type for {field_def.name}: {field_def.object_type}"
                )
            if field_def.pointer:
                items = value if isinstance(value, list) else [value]
            elif count == 1:
                items = [value]
            elif isinstance(value, list):
                items = value
            else:
                raise HFADictionaryError(f"{field_def.name} requires {count} objects")
            if not field_def.pointer and len(items) != count:
                raise HFADictionaryError(f"{field_def.name} requires {count} objects")
            encoded = bytearray()
            for item in items:
                if not isinstance(item, dict):
                    raise HFADictionaryError(f"{field_def.name} object values must be mappings")
                encoded.extend(self._encode_type(typedef, item, absolute_offset + len(encoded), depth + 1))
            return bytes(encoded)
        if kind == "e":
            values = self._normalize_array(value, count, field_def.name, field_def.pointer)
            output = bytearray()
            for item in values:
                if isinstance(item, dict):
                    code = item.get("code")
                    if code is None and item.get("label") is not None:
                        code = self._enum_code(field_def.enum_values, item["label"])
                elif isinstance(item, str):
                    code = self._enum_code(field_def.enum_values, item)
                else:
                    code = int(item)
                if code < 0 or code > 0xFFFF:
                    raise HFADictionaryError(f"Enum code for {field_def.name} is out of range")
                output.extend(struct.pack("<H", code))
            return bytes(output)
        values = self._normalize_array(value, count, field_def.name, field_def.pointer)
        if kind in ("c", "C"):
            if kind == "c" and isinstance(value, str) and not field_def.pointer:
                raw = value.encode("latin-1")
                if len(raw) > count:
                    raise HFADictionaryError(f"Fixed string {field_def.name} exceeds {count} bytes")
                return raw + b"\x00" * (count - len(raw))
            if kind == "c" and isinstance(value, (bytes, bytearray)) and not field_def.pointer:
                raw = bytes(value)
                if len(raw) > count:
                    raise HFADictionaryError(f"Fixed string {field_def.name} exceeds {count} bytes")
                return raw + b"\x00" * (count - len(raw))
            output = bytearray()
            for item in values:
                if kind == "c":
                    if isinstance(item, str):
                        output.extend(item.encode("latin-1"))
                    else:
                        output.extend(bytes([int(item) & 0xFF]))
                else:
                    output.extend(struct.pack("<b", int(item)))
            if field_def.pointer and isinstance(value, str):
                output.extend(b"\x00")
            elif field_def.pointer and isinstance(value, (bytes, bytearray)) and not value.endswith(b"\x00"):
                output.extend(b"\x00")
            return bytes(output)

        formats = {
            "1": None, "2": None, "4": None,
            "s": "<H", "S": "<h", "t": "<I", "l": "<I", "L": "<i",
            "f": "<f", "d": "<d", "m": "<ff", "M": "<dd",
        }
        if kind in ("1", "2", "4"):
            if not isinstance(value, (bytes, bytearray)):
                raise HFADictionaryError(
                    f"Packed field {field_def.name} must be supplied as raw bytes"
                )
            raw = bytes(value)
            expected = count if not field_def.pointer else len(raw)
            if len(raw) != expected:
                raise HFADictionaryError(f"Packed field {field_def.name} has the wrong byte length")
            return raw
        fmt = formats.get(kind)
        if fmt is None:
            raise HFADictionaryError(f"Unsupported HFA field kind {kind!r}")
        output = bytearray()
        for item in values:
            if kind in ("m", "M"):
                if not isinstance(item, (list, tuple)) or len(item) != 2:
                    raise HFADictionaryError(f"Complex field {field_def.name} requires pairs")
                output.extend(struct.pack(fmt, float(item[0]), float(item[1])))
            else:
                output.extend(struct.pack(fmt, item))
        return bytes(output)

    @staticmethod
    def _normalize_array(value: Any, count: int, field_name: str, pointer: Optional[str]) -> List[Any]:
        if pointer:
            if isinstance(value, (list, tuple)):
                return list(value)
            return [value]
        if count == 0:
            return []
        if count == 1:
            return [value]
        if not isinstance(value, (list, tuple)) or len(value) != count:
            raise HFADictionaryError(f"{field_name} requires an array of {count} values")
        return list(value)

    @staticmethod
    def _enum_code(enum_values: Sequence[str], label: str) -> int:
        try:
            return list(enum_values).index(label)
        except ValueError as exc:
            raise HFADictionaryError(f"Unknown enum label {label!r}") from exc

    def _encode_basedata(self, value: Any, _count: int) -> bytes:
        if not isinstance(value, dict):
            raise HFADictionaryError("BASEDATA values must be provided as a mapping")
        rows = int(value.get("rows", 0))
        cols = int(value.get("columns", 0))
        object_type = int(value.get("object_type", 0))
        item_labels = {
            "u1": 0, "u2": 1, "u4": 2, "u8": 3, "s8": 4,
            "u16": 5, "s16": 6, "u32": 7, "s32": 8,
            "f32": 9, "f64": 10, "c64": 11, "c128": 12,
        }
        item_type_value = value.get("item_type")
        item_type = item_type_value if isinstance(item_type_value, int) else item_labels.get(str(item_type_value))
        if item_type is None or item_type not in item_labels.values():
            raise HFADictionaryError(f"Unknown BASEDATA item type {item_type_value!r}")
        if rows < 0 or cols < 0 or rows > self.max_items or cols > self.max_items or (rows and cols > self.max_items // rows):
            raise HFADictionaryError("BASEDATA dimensions exceed safety limit")
        count = rows * cols
        raw_value = value.get("raw")
        sizes = {0: (1, "u1"), 1: (1, "u2"), 2: (1, "u4"), 3: (1, "u8"),
                 4: (1, "s8"), 5: (2, "u16"), 6: (2, "s16"), 7: (4, "u32"),
                 8: (4, "s32"), 9: (4, "f32"), 10: (8, "f64"), 11: (8, "c64"), 12: (16, "c128")}
        if isinstance(raw_value, (bytes, bytearray)) and not value.get("values"):
            raw = bytes(raw_value)
        else:
            items = value.get("values")
            if not isinstance(items, list) or len(items) != count:
                raise HFADictionaryError(f"BASEDATA requires {count} values")
            if item_type in (0, 1, 2):
                raise HFADictionaryError("Packed u1/u2/u4 BASEDATA must be supplied as raw bytes")
            fmt = {
                3: "<B", 4: "<b", 5: "<H", 6: "<h", 7: "<I", 8: "<i",
                9: "<f", 10: "<d", 11: "<ff", 12: "<dd",
            }[item_type]
            packed = bytearray()
            for item in items:
                if item_type in (11, 12):
                    if not isinstance(item, (tuple, list)) or len(item) != 2:
                        raise HFADictionaryError("Complex BASEDATA values must be pairs")
                    packed.extend(struct.pack(fmt, float(item[0]), float(item[1])))
                else:
                    packed.extend(struct.pack(fmt, item))
            raw = bytes(packed)
        unit_size, _label = sizes[item_type]
        expected = ((count + 7) // 8 if item_type == 0 else
                    (count * 2 + 7) // 8 if item_type == 1 else
                    (count * 4 + 7) // 8 if item_type == 2 else count * unit_size)
        if len(raw) != expected:
            raise HFADictionaryError(
                f"BASEDATA payload has {len(raw)} bytes; expected {expected}"
            )
        return struct.pack("<IIHH", rows, cols, item_type, object_type) + raw
