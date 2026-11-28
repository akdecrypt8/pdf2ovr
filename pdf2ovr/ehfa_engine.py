"""Read-only EHFA compatibility API.

The former implementation guessed entry positions by scanning file bytes and
rebuilt headers at fixed offsets. Those behaviors are unsafe for real EHFA
files. This module now delegates all parsing to :mod:`hfa_structure` and
intentionally disables mutation until a schema-aware writer is implemented.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .hfa_structure import HFAEntry, HFAReader


@dataclass
class EHFAEntry:
    """Compatibility view of a parsed HFA entry."""

    offset: int
    next_ptr: int
    prev_ptr: int
    parent_ptr: int
    child_ptr: int
    data_ptr: int
    data_size: int
    name: str
    ctype: str
    mod_time: int
    data: bytes = field(repr=False, default=b"")


class EHFAFile:
    """Read-only HFA file representation with legacy inspection methods."""

    HEADER_TAG = b"EHFA_HEADER_TAG\x00"
    ENTRY_SIZE = 124  # Serialized fields; allocation stride is header.entryHeaderLength.

    def __init__(self, raw_data: bytes):
        self.raw = bytes(raw_data)
        self.reader = HFAReader(self.raw)
        header = self.reader.header
        self.version = header.version
        self.free_list = header.free_list_pointer
        self.root_ptr = header.root_entry_pointer
        self.header_len = header.entry_header_length
        self.dict_ptr = header.dictionary_pointer
        self.dictionary = self.reader.dictionary
        self.entries: List[EHFAEntry] = []
        self.entry_by_offset: Dict[int, EHFAEntry] = {}

        for entry in self.reader.entries:
            payload = (
                self.raw[entry.data_pointer:entry.data_end]
                if entry.data_pointer and entry.data_size else b""
            )
            compatibility_entry = EHFAEntry(
                offset=entry.offset,
                next_ptr=entry.next_pointer,
                prev_ptr=entry.previous_pointer,
                parent_ptr=entry.parent_pointer,
                child_ptr=entry.child_pointer,
                data_ptr=entry.data_pointer,
                data_size=entry.data_size,
                name=entry.name,
                ctype=entry.entry_type,
                mod_time=entry.modified_time,
                data=payload,
            )
            self.entries.append(compatibility_entry)
            self.entry_by_offset[compatibility_entry.offset] = compatibility_entry

    def _parse_header(self) -> None:
        """Compatibility no-op: header is parsed during construction."""
        header = self.reader.header
        self.version = header.version
        self.free_list = header.free_list_pointer
        self.root_ptr = header.root_entry_pointer
        self.header_len = header.entry_header_length
        self.dict_ptr = header.dictionary_pointer

    def _scan_entries(self) -> None:
        """Compatibility no-op: entries come from root-pointer traversal."""
        return None

    def _try_read_entry(self, offset: int) -> Optional[EHFAEntry]:
        parsed = self.reader.read_entry(offset)
        payload = (
            self.raw[parsed.data_pointer:parsed.data_end]
            if parsed.data_pointer and parsed.data_size else b""
        )
        return EHFAEntry(
            offset=parsed.offset,
            next_ptr=parsed.next_pointer,
            prev_ptr=parsed.previous_pointer,
            parent_ptr=parsed.parent_pointer,
            child_ptr=parsed.child_pointer,
            data_ptr=parsed.data_pointer,
            data_size=parsed.data_size,
            name=parsed.name,
            ctype=parsed.entry_type,
            mod_time=parsed.modified_time,
            data=payload,
        )

    def _read_dictionary(self) -> None:
        self.dictionary = self.reader.dictionary

    def _load_data_blocks(self) -> None:
        # Data blocks are copied during __init__; retained for API compatibility.
        return None

    def get_entry_by_name(self, name: str) -> Optional[EHFAEntry]:
        return next((entry for entry in self.entries if entry.name == name), None)

    def get_children(self, entry: EHFAEntry) -> List[EHFAEntry]:
        result: List[EHFAEntry] = []
        current = entry.child_ptr
        seen = set()
        while current:
            if current in seen:
                raise ValueError(f"Cycle in child list at offset {current}")
            seen.add(current)
            child = self.entry_by_offset.get(current)
            if child is None:
                raise ValueError(f"Child pointer {current} is not reachable")
            result.append(child)
            current = child.next_ptr
        return result

    def get_tree(self, entry: EHFAEntry, depth: int = 0) -> str:
        lines = ["  " * depth + f"- {entry.name} ({entry.ctype}) @ {hex(entry.offset)}"]
        for child in self.get_children(entry):
            lines.append(self.get_tree(child, depth + 1))
        return "\n".join(lines)

    def find_root_entries(self) -> List[EHFAEntry]:
        return [entry for entry in self.entries if entry.parent_ptr == 0]

    def rebuild(self) -> bytes:
        raise NotImplementedError(
            "EHFA rebuilding is disabled until dictionary, free-list, entry "
            "allocation, and EANT schema serialization are fully implemented."
        )

    def add_entry(self, *args, **kwargs) -> EHFAEntry:
        raise NotImplementedError(
            "EHFA mutation is disabled; use the read-only parser until the writer is validated."
        )

    def remove_entry(self, *args, **kwargs) -> None:
        raise NotImplementedError(
            "EHFA mutation is disabled; use the read-only parser until the writer is validated."
        )


if __name__ == "__main__":
    import argparse
    import json
    from pathlib import Path

    parser = argparse.ArgumentParser(description="Inspect an EHFA/OVR file (read-only)")
    parser.add_argument("file")
    args = parser.parse_args()
    parsed = EHFAFile(Path(args.file).read_bytes())
    print(json.dumps({
        "version": parsed.version,
        "free_list": parsed.free_list,
        "root_pointer": parsed.root_ptr,
        "entry_header_length": parsed.header_len,
        "dictionary_pointer": parsed.dict_ptr,
        "entry_count": len(parsed.entries),
        "dictionary_size": len(parsed.dictionary),
    }, indent=2))
    for root in parsed.find_root_entries():
        print(parsed.get_tree(root))
