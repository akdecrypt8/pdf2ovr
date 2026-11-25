"""
EHFA Engine - Reader/writer for ERDAS Imagine EHFA container format.

Handles the binary EHFA container used by .ovr and .map files.
Scans, parses, and rebuilds the entry tree structure.
"""

import struct
from typing import List, Dict, Optional, Tuple, Any
from dataclasses import dataclass, field


@dataclass
class EHFAEntry:
    """A single EHFA entry (node in the tree)."""
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
    """Represents a parsed EHFA file with full tree structure."""

    HEADER_TAG = b"EHFA_HEADER_TAG\x00"
    ENTRY_SIZE = 124

    def __init__(self, raw_data: bytes):
        self.raw = bytearray(raw_data)
        self.version = 0
        self.free_list = 0
        self.root_ptr = 0
        self.header_len = 0
        self.dict_ptr = 0
        self.entries: List[EHFAEntry] = []
        self.entry_by_offset: Dict[int, EHFAEntry] = {}
        self.dictionary = b""

        self._parse_header()
        self._scan_entries()
        self._read_dictionary()
        self._load_data_blocks()

    def _parse_header(self):
        if self.raw[:16] != self.HEADER_TAG:
            raise ValueError("Invalid EHFA file: missing EHFA_HEADER_TAG")

        (self.version, self.free_list, self.root_ptr,
         self.header_len, self.dict_ptr) = struct.unpack_from("<IIIII", self.raw, 16)

    def _scan_entries(self):
        candidates = []

        for i in range(0x60, len(self.raw) - self.ENTRY_SIZE):
            entry = self._try_read_entry(i)
            if entry:
                candidates.append(entry)

        offset_set = {c.offset for c in candidates}

        for c in candidates:
            has_valid_link = (
                (c.next_ptr in offset_set) or
                (c.prev_ptr in offset_set) or
                (c.parent_ptr in offset_set) or
                (c.child_ptr in offset_set) or
                c.name in ('Annotation', 'Map Composition', 'root', 'ElementList',
                           'SymbolElementList', 'EantStyleList', 'LineStyleList',
                           'FillStyleList', 'ColorList', 'TextStyleList')
            )
            pointed_to = any(
                other.next_ptr == c.offset or
                other.prev_ptr == c.offset or
                other.parent_ptr == c.offset or
                other.child_ptr == c.offset
                for other in candidates
            )
            if has_valid_link or pointed_to:
                self.entries.append(c)
                self.entry_by_offset[c.offset] = c

    def _try_read_entry(self, offset: int) -> Optional[EHFAEntry]:
        if offset + self.ENTRY_SIZE > len(self.raw):
            return None

        try:
            (next_p, prev_p, parent_p, child_p, data_p, data_s) = struct.unpack_from(
                "<IIIIII", self.raw, offset)

            name_bytes = self.raw[offset+24:offset+88]
            name = name_bytes.split(b'\x00')[0].decode('ascii', errors='ignore')

            ctype_bytes = self.raw[offset+88:offset+120]
            ctype = ctype_bytes.split(b'\x00')[0].decode('ascii', errors='ignore')

            mod_time = struct.unpack_from("<I", self.raw, offset+120)[0]

            if data_s < 0 or data_s > 500000:
                return None
            if data_p < 0 or (data_p > 0 and data_p + data_s > len(self.raw)):
                return None
            if data_p > 0 and data_p < 0x100:
                return None
            if not name or len(name) > 60:
                return None
            if not all(c.isalnum() or c in '_- ' for c in name):
                return None
            if name.isdigit():
                return None
            if len(ctype) > 40:
                return None
            if ctype and not all(c.isalnum() or c in '_-' for c in ctype):
                return None
            if data_p > 0 and data_p >= offset and data_p < offset + self.ENTRY_SIZE:
                return None

            return EHFAEntry(
                offset=offset, next_ptr=next_p, prev_ptr=prev_p,
                parent_ptr=parent_p, child_ptr=child_p,
                data_ptr=data_p, data_size=data_s,
                name=name, ctype=ctype, mod_time=mod_time
            )
        except Exception:
            return None

    def _read_dictionary(self):
        dict_markers = [b"}Ehfa_Data,.", b"}Ehfa_Data,", b"ryHeaderLength,"]
        for marker in dict_markers:
            pos = self.raw.find(marker)
            if pos > 0:
                start = max(0, pos - 200)
                end = min(len(self.raw), pos + len(marker) + 20)
                self.dictionary = bytes(self.raw[start:end])
                return

        if self.entries:
            last_entry_end = max(e.offset + self.ENTRY_SIZE for e in self.entries)
            first_data_start = min((e.data_ptr for e in self.entries if e.data_ptr > 0), default=len(self.raw))
            if first_data_start > last_entry_end:
                self.dictionary = bytes(self.raw[last_entry_end:first_data_start])

    def _load_data_blocks(self):
        for entry in self.entries:
            if entry.data_size > 0 and entry.data_ptr > 0:
                end = min(entry.data_ptr + entry.data_size, len(self.raw))
                entry.data = bytes(self.raw[entry.data_ptr:end])

    def get_entry_by_name(self, name: str) -> Optional[EHFAEntry]:
        for entry in self.entries:
            if entry.name == name:
                return entry
        return None

    def get_children(self, entry: EHFAEntry) -> List[EHFAEntry]:
        children = []
        if entry.child_ptr > 0 and entry.child_ptr in self.entry_by_offset:
            child = self.entry_by_offset[entry.child_ptr]
            children.append(child)
            while child.next_ptr > 0 and child.next_ptr in self.entry_by_offset:
                child = self.entry_by_offset[child.next_ptr]
                children.append(child)
        return children

    def get_tree(self, entry: EHFAEntry, depth: int = 0) -> str:
        lines = ["  " * depth + f"- {entry.name} ({entry.ctype}) @ {hex(entry.offset)}"]
        for child in self.get_children(entry):
            lines.append(self.get_tree(child, depth + 1))
        return "\n".join(lines)

    def find_root_entries(self) -> List[EHFAEntry]:
        return [e for e in self.entries if e.parent_ptr == 0]

    def rebuild(self) -> bytes:
        ordered = sorted(self.entries, key=lambda e: e.offset)

        max_offset = max((e.offset for e in ordered), default=0x60)
        max_data_end = max(
            (e.data_ptr + e.data_size for e in ordered if e.data_ptr > 0),
            default=0
        )

        file_size = max(max_offset + self.ENTRY_SIZE, max_data_end) + 1024
        out = bytearray(file_size)

        out[:16] = self.HEADER_TAG
        struct.pack_into("<IIIII", out, 16, self.version, self.free_list,
                         0x60, 0x60, 0)

        for entry in ordered:
            if entry.offset + self.ENTRY_SIZE > len(out):
                continue
            struct.pack_into("<IIIIII", out, entry.offset,
                             entry.next_ptr, entry.prev_ptr,
                             entry.parent_ptr, entry.child_ptr,
                             entry.data_ptr, entry.data_size)

            name_b = entry.name.encode('ascii')[:63].ljust(64, b'\x00')
            ctype_b = entry.ctype.encode('ascii')[:31].ljust(32, b'\x00')
            out[entry.offset+24:entry.offset+88] = name_b
            out[entry.offset+88:entry.offset+120] = ctype_b
            struct.pack_into("<I", out, entry.offset+120, entry.mod_time)

        for entry in ordered:
            if entry.data_size > 0 and entry.data:
                end = entry.data_ptr + len(entry.data)
                if end <= len(out):
                    out[entry.data_ptr:end] = entry.data

        if self.dictionary:
            dict_pos = self._find_gap(out, len(self.dictionary), ordered)
            if dict_pos > 0:
                end = dict_pos + len(self.dictionary)
                if end <= len(out):
                    out[dict_pos:end] = self.dictionary

        while len(out) > 0 and out[-1] == 0:
            out.pop()

        return bytes(out)

    def _find_gap(self, out: bytearray, size: int, entries: List[EHFAEntry]) -> int:
        if entries:
            last_entry_end = max(e.offset + self.ENTRY_SIZE for e in entries)
            if last_entry_end + size < len(out):
                return last_entry_end
        return 0

    def add_entry(self, name: str, ctype: str, data: bytes,
                  parent: Optional[EHFAEntry] = None) -> EHFAEntry:
        max_offset = max((e.offset for e in self.entries), default=0x60)
        max_data_end = max(
            (e.data_ptr + e.data_size for e in self.entries if e.data_ptr > 0),
            default=0
        )

        new_offset = max(max_offset + self.ENTRY_SIZE, max_data_end + 8)
        if new_offset % 8 != 0:
            new_offset += 8 - (new_offset % 8)

        entry = EHFAEntry(
            offset=new_offset,
            next_ptr=0, prev_ptr=0,
            parent_ptr=parent.offset if parent else 0,
            child_ptr=0,
            data_ptr=0,
            data_size=len(data),
            name=name, ctype=ctype, mod_time=0,
            data=data
        )

        self.entries.append(entry)
        self.entry_by_offset[new_offset] = entry

        if parent and parent.child_ptr == 0:
            parent.child_ptr = new_offset

        if parent:
            siblings = self.get_children(parent)
            if len(siblings) > 1:
                for i in range(len(siblings) - 1):
                    siblings[i].next_ptr = siblings[i + 1].offset
                siblings[-1].next_ptr = 0
                for i in range(1, len(siblings)):
                    siblings[i].prev_ptr = siblings[i - 1].offset
                siblings[0].prev_ptr = 0

        return entry

    def remove_entry(self, entry: EHFAEntry):
        for child in self.get_children(entry):
            self.remove_entry(child)

        if entry.prev_ptr > 0 and entry.prev_ptr in self.entry_by_offset:
            prev = self.entry_by_offset[entry.prev_ptr]
            prev.next_ptr = entry.next_ptr
        if entry.next_ptr > 0 and entry.next_ptr in self.entry_by_offset:
            next_e = self.entry_by_offset[entry.next_ptr]
            next_e.prev_ptr = entry.prev_ptr

        if entry.parent_ptr > 0 and entry.parent_ptr in self.entry_by_offset:
            parent = self.entry_by_offset[entry.parent_ptr]
            if parent.child_ptr == entry.offset:
                parent.child_ptr = entry.next_ptr

        if entry in self.entries:
            self.entries.remove(entry)
        if entry.offset in self.entry_by_offset:
            del self.entry_by_offset[entry.offset]


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        with open(sys.argv[1], "rb") as f:
            data = f.read()
        ef = EHFAFile(data)
        print(f"Version: {ef.version}, Entries: {len(ef.entries)}")
        print(f"Dictionary size: {len(ef.dictionary)}")
        print("\nRoot entries:")
        for root in ef.find_root_entries():
            print(ef.get_tree(root))