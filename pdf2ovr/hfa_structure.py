"""Read-only EHFA/HFA container inspection.

This module intentionally implements traversal, not serialization.  The OVR
writer remains disabled until the EANT payload schemas and ERDAS round-trip are
validated.  All offsets are read from the file; no heuristic byte scanning is
used to discover entries.
"""

from __future__ import annotations

from dataclasses import dataclass
import struct
from typing import Dict, Iterator, List, Optional


HEADER_TAG = b"EHFA_HEADER_TAG\x00"
FILE_HEADER_MIN_SIZE = 18
ENTRY_FIXED_SIZE = 124


class HFAFormatError(ValueError):
    """Raised when an HFA/EHFA file has an invalid or inconsistent layout."""


@dataclass(frozen=True)
class HFAFileHeader:
    header_offset: int
    version: int
    free_list_pointer: int
    root_entry_pointer: int
    entry_header_length: int
    dictionary_pointer: int


@dataclass(frozen=True)
class HFAEntry:
    offset: int
    next_pointer: int
    previous_pointer: int
    parent_pointer: int
    child_pointer: int
    data_pointer: int
    data_size: int
    name: str
    entry_type: str
    modified_time: int

    @property
    def data_end(self) -> int:
        return self.data_pointer + self.data_size

    def as_dict(self) -> Dict[str, object]:
        """Compatibility representation used by the older patcher API."""
        return {
            "offset": self.offset,
            "next": self.next_pointer,
            "prev": self.previous_pointer,
            "parent": self.parent_pointer,
            "child": self.child_pointer,
            "data_ptr": self.data_pointer,
            "data_size": self.data_size,
            "name": self.name,
            "type": self.entry_type,
            "mod_time": self.modified_time,
        }


class HFAReader:
    """Parse an EHFA header and only entries reachable from its root pointer."""

    def __init__(
        self, data: bytes | bytearray, *,
        max_entries: int = 1_000_000, max_depth: int = 256,
    ):
        if max_entries < 1 or max_depth < 1:
            raise ValueError("max_entries and max_depth must be positive")
        self.data = bytes(data)
        self.max_entries = max_entries
        self.max_depth = max_depth
        self.header = self._read_file_header()
        self.entries: List[HFAEntry] = []
        self.entry_by_offset: Dict[int, HFAEntry] = {}
        self._walk_reachable_entries()

    def _read_file_header(self) -> HFAFileHeader:
        if len(self.data) < FILE_HEADER_MIN_SIZE:
            raise HFAFormatError("File is too short to contain an EHFA header")
        if self.data[:16] != HEADER_TAG:
            raise HFAFormatError("Missing EHFA_HEADER_TAG signature")

        header_offset = struct.unpack_from("<I", self.data, 16)[0]
        if header_offset <= 0 or header_offset + 18 > len(self.data):
            raise HFAFormatError(
                f"Ehfa_File header pointer {header_offset} is outside the file"
            )

        version, free_list, root_entry = struct.unpack_from(
            "<III", self.data, header_offset
        )
        entry_header_length = struct.unpack_from(
            "<H", self.data, header_offset + 12
        )[0]
        dictionary_pointer = struct.unpack_from(
            "<I", self.data, header_offset + 14
        )[0]

        if entry_header_length < ENTRY_FIXED_SIZE:
            raise HFAFormatError(
                f"Invalid entryHeaderLength {entry_header_length}; "
                f"minimum supported is {ENTRY_FIXED_SIZE}"
            )
        if entry_header_length > 4096:
            raise HFAFormatError(
                f"Implausible entryHeaderLength {entry_header_length}"
            )
        if root_entry <= 0 or root_entry + entry_header_length > len(self.data):
            raise HFAFormatError("Root entry pointer is outside the file")
        if dictionary_pointer <= 0 or dictionary_pointer > len(self.data):
            raise HFAFormatError("Dictionary pointer is outside the file")

        return HFAFileHeader(
            header_offset=header_offset,
            version=version,
            free_list_pointer=free_list,
            root_entry_pointer=root_entry,
            entry_header_length=entry_header_length,
            dictionary_pointer=dictionary_pointer,
        )

    @staticmethod
    def _decode_fixed_string(raw: bytes, field_name: str, offset: int) -> str:
        value = raw.split(b"\x00", 1)[0]
        try:
            return value.decode("ascii")
        except UnicodeDecodeError as exc:
            raise HFAFormatError(
                f"Non-ASCII bytes in EHFA {field_name} at offset {offset}"
            ) from exc

    def read_entry(self, offset: int) -> HFAEntry:
        header_length = self.header.entry_header_length
        if offset <= 0 or offset + header_length > len(self.data):
            raise HFAFormatError(f"Entry header at {offset} is outside the file")
        if offset + header_length > self.header.dictionary_pointer:
            raise HFAFormatError(f"Entry header at {offset} overlaps the dictionary")

        next_p, prev_p, parent_p, child_p, data_p, data_size = struct.unpack_from(
            "<IIIIII", self.data, offset
        )
        name = self._decode_fixed_string(
            self.data[offset + 24:offset + 88], "entry name", offset + 24
        )
        entry_type = self._decode_fixed_string(
            self.data[offset + 88:offset + 120], "entry type", offset + 88
        )
        modified_time = struct.unpack_from("<I", self.data, offset + 120)[0]

        if not name:
            raise HFAFormatError(f"Entry at {offset} has an empty name")
        if data_size and not data_p:
            raise HFAFormatError(
                f"Entry {name!r} at {offset} has data_size but no data pointer"
            )
        if data_p:
            # HFA stores the data position as an independent absolute pointer.
            # Writers commonly place data immediately after the entry header,
            # but the format does not make that adjacency a safe assumption: a
            # block may have been allocated elsewhere (for example after an
            # edit/reallocation). Global range validation below detects actual
            # overlap with headers, other data blocks, the dictionary and the
            # free list.
            if data_p + data_size > len(self.data):
                raise HFAFormatError(
                    f"Entry {name!r} data block exceeds the file boundary"
                )
            if data_p + data_size > self.header.dictionary_pointer:
                raise HFAFormatError(
                    f"Entry {name!r} data block overlaps the HFA dictionary"
                )

        return HFAEntry(
            offset=offset,
            next_pointer=next_p,
            previous_pointer=prev_p,
            parent_pointer=parent_p,
            child_pointer=child_p,
            data_pointer=data_p,
            data_size=data_size,
            name=name,
            entry_type=entry_type,
            modified_time=modified_time,
        )

    def _walk_sibling_chain(
        self, first_offset: int, expected_parent: int,
        active_ancestors: set[int], depth: int,
    ) -> Iterator[HFAEntry]:
        if depth > self.max_depth:
            raise HFAFormatError(
                f"Entry nesting exceeds configured safety limit ({self.max_depth})"
            )
        current = first_offset
        previous = 0
        local_seen: set[int] = set()

        while current:
            if current in local_seen:
                raise HFAFormatError(f"Cycle in sibling chain at offset {current}")
            local_seen.add(current)
            if current in active_ancestors:
                raise HFAFormatError(
                    f"Entry tree contains an ancestor cycle at offset {current}"
                )
            if len(self.entry_by_offset) >= self.max_entries:
                raise HFAFormatError("Entry count exceeds configured safety limit")

            entry = self.read_entry(current)
            if entry.previous_pointer != previous:
                raise HFAFormatError(
                    f"Broken previous-sibling pointer for {entry.name!r} "
                    f"at {current}: expected {previous}, got {entry.previous_pointer}"
                )
            if entry.parent_pointer != expected_parent:
                raise HFAFormatError(
                    f"Broken parent pointer for {entry.name!r} at {current}: "
                    f"expected {expected_parent}, got {entry.parent_pointer}"
                )
            if current in self.entry_by_offset:
                raise HFAFormatError(
                    f"Entry at offset {current} is reachable more than once"
                )

            self.entry_by_offset[current] = entry
            self.entries.append(entry)
            yield entry

            if entry.child_pointer:
                ancestors = set(active_ancestors)
                ancestors.add(current)
                # Consume descendants before moving to the next sibling.
                for _child in self._walk_sibling_chain(
                    entry.child_pointer, current, ancestors, depth + 1
                ):
                    pass

            previous = current
            current = entry.next_pointer

    def _walk_reachable_entries(self) -> None:
        root = self.read_entry(self.header.root_entry_pointer)
        if root.parent_pointer != 0:
            raise HFAFormatError("Root EHFA entry unexpectedly has a parent")
        self.entry_by_offset[root.offset] = root
        self.entries.append(root)
        if root.child_pointer:
            for _entry in self._walk_sibling_chain(
                root.child_pointer, root.offset, {root.offset}, 1
            ):
                pass

    @property
    def dictionary(self) -> bytes:
        return self.data[self.header.dictionary_pointer:]

    def find_entries(self, name: str) -> List[HFAEntry]:
        return [entry for entry in self.entries if entry.name == name]

    def find_entry(self, name: str) -> Optional[HFAEntry]:
        return next((entry for entry in self.entries if entry.name == name), None)

    def children(self, parent: HFAEntry) -> List[HFAEntry]:
        if not parent.child_pointer:
            return []
        result: List[HFAEntry] = []
        current = parent.child_pointer
        seen: set[int] = set()
        while current:
            if current in seen:
                raise HFAFormatError("Cycle in child sibling chain")
            seen.add(current)
            child = self.entry_by_offset.get(current)
            if child is None:
                child = self.read_entry(current)
            result.append(child)
            current = child.next_pointer
        return result

    def validate(self) -> Dict[str, object]:
        """Return a structural summary after validating all reachable ranges."""
        header_length = self.header.entry_header_length
        header_ranges = sorted(
            (entry.offset, entry.offset + header_length, entry.name)
            for entry in self.entries
        )
        data_ranges = sorted(
            (entry.data_pointer, entry.data_pointer + entry.data_size, entry.name)
            for entry in self.entries
            if entry.data_pointer and entry.data_size
        )

        for previous, current in zip(header_ranges, header_ranges[1:]):
            if current[0] < previous[1]:
                raise HFAFormatError(
                    f"Entry headers overlap: {previous[2]!r} and {current[2]!r}"
                )
        for previous, current in zip(data_ranges, data_ranges[1:]):
            if current[0] < previous[1]:
                raise HFAFormatError(
                    f"Data blocks overlap: {previous[2]!r} and {current[2]!r}"
                )
        header_index = 0
        for data_start, data_end, data_name in data_ranges:
            while (
                header_index < len(header_ranges)
                and header_ranges[header_index][1] <= data_start
            ):
                header_index += 1
            if header_index < len(header_ranges):
                header_start, header_end, header_name = header_ranges[header_index]
                if header_start < data_end and data_start < header_end:
                    raise HFAFormatError(
                        f"Data block for {data_name!r} overlaps header {header_name!r}"
                    )

        free_blocks = []
        free_pointer = self.header.free_list_pointer
        seen_free_nodes = set()
        while free_pointer:
            if free_pointer in seen_free_nodes:
                raise HFAFormatError("Cycle in EHFA free-list chain")
            seen_free_nodes.add(free_pointer)
            if free_pointer + 8 > len(self.data):
                raise HFAFormatError("EHFA free-list node is outside the file")
            next_free, free_size = struct.unpack_from("<II", self.data, free_pointer)
            if free_size < 8 or free_pointer + free_size > len(self.data):
                raise HFAFormatError("EHFA free-list block has an invalid size")
            free_end = free_pointer + free_size
            if free_end > self.header.dictionary_pointer:
                raise HFAFormatError("EHFA free-list block overlaps the dictionary")
            for start, end, label in header_ranges:
                if free_pointer < end and start < free_end:
                    raise HFAFormatError(
                        f"EHFA free-list block overlaps entry header {label!r}"
                    )
            for start, end, label in data_ranges:
                if free_pointer < end and start < free_end:
                    raise HFAFormatError(
                        f"EHFA free-list block overlaps data block {label!r}"
                    )
            free_blocks.append({"offset": free_pointer, "size": free_size, "end": free_end})
            free_pointer = next_free

        return {
            "version": self.header.version,
            "header_offset": self.header.header_offset,
            "root_entry_pointer": self.header.root_entry_pointer,
            "entry_header_length": self.header.entry_header_length,
            "dictionary_pointer": self.header.dictionary_pointer,
            "entry_count": len(self.entries),
            "dictionary_size": len(self.dictionary),
            "free_list_blocks": free_blocks,
        }
