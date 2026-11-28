"""Conservative HFA container writer.

This writer appends dictionary-typed entries while preserving every existing
entry/data block. It does not claim that an arbitrary new EANT annotation is
semantically valid; callers must supply a payload encoded for the target
annotation schema. The resulting container is re-parsed and validated before
bytes are returned.
"""
from __future__ import annotations

from dataclasses import dataclass
import struct
from typing import Callable, Dict, List, Sequence, Union

from .hfa_structure import HFAEntry, HFAFormatError, HFAReader

PayloadFactory = Union[bytes, bytearray, Callable[[int], bytes]]
ParentRef = Union[int, str]


class HFAWriteError(ValueError):
    """Raised when a requested HFA tree update cannot be made safely."""


@dataclass(frozen=True)
class HFAEntrySpec:
    key: str
    name: str
    entry_type: str
    parent: ParentRef
    payload: PayloadFactory = b""
    modified_time: int = 0


class HFAWriter:
    """Append entries to an HFA file without relocating existing data blocks."""

    def __init__(self, source: bytes | bytearray):
        self.source = bytes(source)
        self.reader = HFAReader(self.source)
        self.reader.validate()

    @staticmethod
    def _validate_pointer_targets(value, entry_key: str) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key.endswith("_pointer") and isinstance(item, dict):
                    if item.get("count", 0) and item.get("target_matches_inline_data") is False:
                        raise HFAWriteError(
                            f"Payload {entry_key!r} contains a pointer not aimed at its inline data"
                        )
                else:
                    HFAWriter._validate_pointer_targets(item, entry_key)
        elif isinstance(value, list):
            for item in value:
                HFAWriter._validate_pointer_targets(item, entry_key)

    @staticmethod
    def _fixed_ascii(value: str, size: int, field: str) -> bytes:
        if not isinstance(value, str) or not value:
            raise HFAWriteError(f"{field} must be a non-empty string")
        try:
            encoded = value.encode("ascii")
        except UnicodeEncodeError as exc:
            raise HFAWriteError(f"{field} must be ASCII in the HFA entry header") from exc
        if b"\x00" in encoded:
            raise HFAWriteError(f"{field} cannot contain NUL")
        if len(encoded) >= size:
            raise HFAWriteError(f"{field} is too long (maximum {size - 1} ASCII bytes)")
        return encoded + b"\x00" * (size - len(encoded))

    def append_entry(
        self,
        *,
        name: str,
        entry_type: str,
        parent_offset: int,
        payload: bytes | bytearray,
        modified_time: int = 0,
        require_dictionary_type: bool = True,
    ) -> bytes:
        """Append one raw typed entry to an existing parent's child chain."""
        return self.append_entries([
            HFAEntrySpec(
                key="new-entry", name=name, entry_type=entry_type,
                parent=parent_offset, payload=bytes(payload),
                modified_time=modified_time,
            )
        ], require_dictionary_type=require_dictionary_type)

    def append_entries(
        self,
        specs: Sequence[HFAEntrySpec],
        *,
        require_dictionary_type: bool = True,
    ) -> bytes:
        """Append one or more entries, optionally building nested subtrees.

        ``parent`` is either an existing entry offset or the ``key`` of another
        spec in this batch. New siblings are appended after existing siblings.
        Payload factories receive the absolute data offset for that entry, so
        nested HFA pointer fields can be encoded correctly.
        """
        if not specs:
            raise HFAWriteError("At least one entry specification is required")

        keys = [spec.key for spec in specs]
        if any(not key for key in keys) or len(set(keys)) != len(keys):
            raise HFAWriteError("Entry keys must be non-empty and unique")

        dictionary_pointer = self.reader.header.dictionary_pointer
        header_length = self.reader.header.entry_header_length
        known_types = set()
        decoder = None
        if require_dictionary_type:
            from .hfa_dictionary import HFADictionary
            decoder = HFADictionary(self.reader.dictionary)
            known_types = set(decoder.type_names())

        new_offsets: Dict[str, int] = {}
        payloads: Dict[str, bytes] = {}
        cursor = dictionary_pointer
        for spec in specs:
            self._fixed_ascii(spec.name, 64, "Entry name")
            self._fixed_ascii(spec.entry_type, 32, "Entry type")
            if require_dictionary_type and spec.entry_type not in known_types:
                raise HFAWriteError(
                    f"Entry type {spec.entry_type!r} is absent from this file's dictionary"
                )
            if not isinstance(spec.modified_time, int) or not 0 <= spec.modified_time <= 0xFFFFFFFF:
                raise HFAWriteError("modified_time must fit an unsigned 32-bit integer")

            offset = cursor
            data_offset = offset + header_length
            if callable(spec.payload):
                payload = spec.payload(data_offset)
            else:
                payload = bytes(spec.payload)
            if not isinstance(payload, (bytes, bytearray, memoryview)):
                raise HFAWriteError(f"Payload factory for {spec.key!r} did not return bytes")
            payload = bytes(payload)
            if decoder is not None and payload:
                try:
                    decoded_payload = decoder.decode_instance(
                        spec.entry_type, payload, absolute_offset=data_offset
                    )
                    self._validate_pointer_targets(decoded_payload, spec.key)
                except Exception as exc:
                    raise HFAWriteError(
                        f"Payload for {spec.key!r} does not decode as {spec.entry_type}: {exc}"
                    ) from exc
            if len(payload) > 0xFFFFFFFF:
                raise HFAWriteError("HFA entry payload exceeds the 32-bit size limit")
            new_offsets[spec.key] = offset
            payloads[spec.key] = payload
            cursor = data_offset + len(payload)

        existing_offsets = set(self.reader.entry_by_offset)
        resolved_parents: Dict[str, int] = {}
        key_positions = {spec.key: index for index, spec in enumerate(specs)}
        for spec_index, spec in enumerate(specs):
            if isinstance(spec.parent, int):
                parent_offset = spec.parent
                if parent_offset not in existing_offsets:
                    raise HFAWriteError(
                        f"Parent offset {parent_offset} is not a reachable existing entry"
                    )
            elif isinstance(spec.parent, str):
                if spec.parent not in new_offsets:
                    raise HFAWriteError(f"Unknown parent key {spec.parent!r}")
                if key_positions[spec.parent] >= spec_index:
                    raise HFAWriteError(
                        "New parent entries must appear before their child entries in the spec list"
                    )
                parent_offset = new_offsets[spec.parent]
            else:
                raise HFAWriteError("Parent must be an existing offset or a new-entry key")
            resolved_parents[spec.key] = parent_offset

        # Reject self-parenting and cycles among the newly specified entries.
        parent_keys = {new_offsets[key]: key for key in new_offsets}
        for spec in specs:
            origin = new_offsets[spec.key]
            current = resolved_parents[spec.key]
            seen = {origin}
            while current in parent_keys:
                if current in seen:
                    raise HFAWriteError("New entry specifications contain a parent cycle")
                seen.add(current)
                current_key = parent_keys[current]
                current = resolved_parents[current_key]

        new_children: Dict[int, List[str]] = {}
        for spec in specs:
            new_children.setdefault(resolved_parents[spec.key], []).append(spec.key)

        # Existing-parent chain updates are made only inside the old file prefix.
        prefix = bytearray(self.source[:dictionary_pointer])
        original_prefix = self.source[:dictionary_pointer]
        new_headers: Dict[str, bytearray] = {}
        allowed_old_file_patches: set[int] = set()

        def patch_old_entry(offset: int, field_offset: int, value: int) -> None:
            if offset not in existing_offsets:
                raise HFAWriteError(f"Refusing to patch non-existing entry at {offset}")
            if offset + field_offset + 4 > len(prefix):
                raise HFAWriteError("Existing entry pointer patch is outside the old file prefix")
            struct.pack_into("<I", prefix, offset + field_offset, value)
            allowed_old_file_patches.update(range(offset + field_offset, offset + field_offset + 4))

        # Construct the sibling topology for every parent touched by this batch.
        for parent_offset, child_keys in new_children.items():
            if parent_offset in existing_offsets:
                parent_entry = self.reader.entry_by_offset[parent_offset]
                old_children = self.reader.children(parent_entry)
                combined: List[Union[int, str]] = [child.offset for child in old_children] + child_keys
                if not old_children:
                    patch_old_entry(parent_offset, 12, new_offsets[child_keys[0]])
                elif old_children[-1].next_pointer != new_offsets[child_keys[0]]:
                    patch_old_entry(old_children[-1].offset, 0, new_offsets[child_keys[0]])
            else:
                combined = child_keys
                # Parent is a new entry; its child pointer is written below.

            for index, child_ref in enumerate(combined):
                child_offset = child_ref if isinstance(child_ref, int) else new_offsets[child_ref]
                previous_ref = combined[index - 1] if index else 0
                next_ref = combined[index + 1] if index + 1 < len(combined) else 0
                previous_offset = previous_ref if isinstance(previous_ref, int) else new_offsets[previous_ref]
                next_offset = next_ref if isinstance(next_ref, int) else new_offsets[next_ref]

                if child_offset in existing_offsets:
                    # Existing entries retain their existing parent/prev fields;
                    # only their next pointer may need to be linked to the first
                    # newly appended sibling.
                    is_last_existing_child = (
                        parent_offset in existing_offsets
                        and index == len(old_children) - 1
                    )
                    if is_last_existing_child:
                        if self.reader.entry_by_offset[child_offset].next_pointer != next_offset:
                            patch_old_entry(child_offset, 0, next_offset)
                    continue

                child_key = next(key for key, value in new_offsets.items() if value == child_offset)
                # New entry header is allocated once, and links are patched once.
                header = new_headers.get(child_key)
                if header is None:
                    header = bytearray(header_length)
                    spec = next(item for item in specs if item.key == child_key)
                    data_pointer = child_offset + header_length if payloads[child_key] else 0
                    struct.pack_into(
                        "<IIIIII", header, 0,
                        next_offset, previous_offset, parent_offset,
                        0, data_pointer, len(payloads[child_key]),
                    )
                    header[24:88] = self._fixed_ascii(spec.name, 64, "Entry name")
                    header[88:120] = self._fixed_ascii(spec.entry_type, 32, "Entry type")
                    struct.pack_into("<I", header, 120, spec.modified_time)
                    new_headers[child_key] = header
                else:
                    struct.pack_into("<III", header, 0, next_offset, previous_offset, parent_offset)

        # A new parent may be created before or after its child in the spec list.
        # Set every new parent's first-child pointer after all new headers exist.
        for parent_offset, child_keys in new_children.items():
            if parent_offset in existing_offsets:
                continue
            parent_key = parent_keys[parent_offset]
            header = new_headers[parent_key]
            struct.pack_into("<I", header, 12, new_offsets[child_keys[0]])

        # Append records in the caller's declared order. Their entry headers are
        # already linked, and each data pointer targets its own inline block.
        output = prefix
        for spec in specs:
            header = new_headers[spec.key]
            output.extend(header)
            output.extend(payloads[spec.key])

        new_dictionary_pointer = len(output)
        output.extend(self.source[dictionary_pointer:])
        dictionary_pointer_field = self.reader.header.header_offset + 14
        struct.pack_into("<I", output, dictionary_pointer_field, new_dictionary_pointer)
        allowed_old_file_patches.update(
            range(dictionary_pointer_field, dictionary_pointer_field + 4)
        )

        # Assert that the append operation changed no pre-existing bytes other
        # than the exact tree links and dictionary pointer required by HFA.
        for byte_offset, (before, after) in enumerate(zip(original_prefix, prefix)):
            if before != after and byte_offset not in allowed_old_file_patches:
                raise HFAWriteError(
                    f"Append operation unexpectedly modified original byte {byte_offset}"
                )

        result = bytes(output)
        try:
            parsed = HFAReader(result)
            summary = parsed.validate()
        except HFAFormatError as exc:
            raise HFAWriteError(f"Generated HFA container failed structural validation: {exc}") from exc

        expected_count = len(self.reader.entries) + len(specs)
        if summary["entry_count"] != expected_count:
            raise HFAWriteError(
                f"Generated HFA tree contains {summary['entry_count']} reachable entries; "
                f"expected {expected_count}"
            )
        if parsed.dictionary != self.reader.dictionary:
            raise HFAWriteError("HFA dictionary changed during append operation")
        if summary["free_list_blocks"] != self.reader.validate()["free_list_blocks"]:
            raise HFAWriteError("HFA free-list changed during append operation")
        for old_entry in self.reader.entries:
            new_entry = parsed.entry_by_offset.get(old_entry.offset)
            if new_entry is None:
                raise HFAWriteError(
                    f"Existing entry at {old_entry.offset} became unreachable"
                )
            if old_entry.data_size:
                old_payload = self.source[old_entry.data_pointer:old_entry.data_end]
                new_payload = result[new_entry.data_pointer:new_entry.data_end]
                if old_payload != new_payload:
                    raise HFAWriteError(
                        f"Existing payload for {old_entry.name!r} changed during append"
                    )
        return result
