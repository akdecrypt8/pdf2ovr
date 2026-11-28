# EHFA / EANT format notes

## Evidence boundary

This document records observations from two OVR examples examined during development and comparison
with GDAL's generic HFA implementation. Those original OVR examples are not included in this repository; their redistribution rights have not been established. The repository tests instead use the independently generated fixtures described in [TEST_FIXTURES.md](TEST_FIXTURES.md).

It distinguishes:

- **Observed:** directly decoded from one or both external OVR examples examined during development.
- **Implementation reference:** behavior visible in GDAL's generic HFA code.
- **Unverified:** requires ERDAS IMAGINE or more representative fixtures.

The two examined OVR examples were not accompanied by the related MAP file, and no
ERDAS IMAGINE application round-trip has been independently verified.

## EHFA file header

Both reference files begin with `EHFA_HEADER_TAG\0`. The 4-byte pointer at file
offset 16 is `64` in both files. The referenced `Ehfa_File` structure contains:

| Relative offset | Field | Size | Observed value |
|---:|---|---:|---|
| 0 | `version` | 4 | 1 |
| 4 | `freeList` | 4 | 0 or 210 |
| 8 | `rootEntryPtr` | 4 | 82 |
| 12 | `entryHeaderLength` | 2 | 128 |
| 14 | `dictionaryPtr` | 4 | 23,615 or 44,683 |

The values are little-endian in the supplied samples. The entry header's
logical field block is 124 bytes, while the per-file allocation/header length
field is 128 bytes. Reader and writer code must use the declared field, not a
hardcoded stride.

## Entry header

The first 24 bytes contain six 32-bit values:

1. next sibling pointer
2. previous sibling pointer
3. parent pointer
4. first child pointer
5. data pointer
6. data size

The remaining defined fields are a 64-byte name, a 32-byte type name and a
4-byte modification-time value. The two reference files use 128-byte entry
headers, leaving 4 bytes beyond those 124 defined bytes.

Pointers in the entry header are absolute file offsets. Entry traversal starts
at `rootEntryPtr`, and follows child and sibling links. A byte-pattern scan for
plausible entry records is not a substitute for that traversal.

The data pointer is an independent absolute pointer. Newly allocated records
in GDAL's generic writer commonly place their data immediately after their
entry header, but the reader does not require that adjacency; it validates
actual ranges and overlap instead.

## Dictionary payloads

The embedded dictionary starts at `dictionaryPtr` and extends to the end of
each examined sample. The HFA parser handles the compact type grammar and decodes the fields
needed by the observed dictionary definitions: primitives, enums, nested
objects, pointer-count blocks and BASEDATA.

The development-time reference-file check encoded and decoded 45 typed entries in
`colorscale.ovr` and 139 in `lnlandc.map.ovr` (184 total). It compared
semantic decoded values. This does not prove all dictionary constructs or all
HFA files are supported.

For pointer fields, the instance includes a count/offset header followed by
inline bytes in both supplied files. The decoder records whether the pointer
matches the expected inline location. The writer only creates pointer payloads
using that inline representation and rejects a non-matching pointer target
when appending a typed payload.

Packed BASEDATA types `u1`, `u2` and `u4` are retained as raw bytes. Bit order
and unpacked values are not inferred by this codec. Other encountered numeric
BASEDATA types are decoded using the declared item type.

## Reference file inventory

### `colorscale.ovr`

- File size: 26,956 bytes.
- HFA version: 1.
- Header structure offset: 64.
- Root entry: 82.
- Entry header length: 128.
- Dictionary pointer: 23,615.
- Reachable entries: 46.
- Dictionary types: 30.
- Typed payloads decoded: 45.
- Annotation root: `AntHeader_Eant` named `Annotation`.
- Element list: `ElementNode_Eant` named `ElementList`.
- 17 top-level `Element_Eant` annotations, all `EANT_ELLIPSE`.
- Each observed ellipse has an `Eant_Ellipse` child payload.
- The file includes `StyleLibrary_Eant`, `SymbolLibrary_Eant` and
  `SymbolData_Eant` records.
- No free-list block was linked from the EHFA header.

### `lnlandc.map.ovr`

- File size: 51,078 bytes.
- HFA version: 1.
- Header structure offset: 64.
- Root entry: 82.
- Entry header length: 128.
- Dictionary pointer: 44,683.
- Reachable entries: 142.
- Dictionary types: 42.
- Typed payloads decoded: 139.
- One free-list block: offset 210, size 298, end 508.
- The Annotation root begins at offset 508; `ElementList` is at offset 12,555.
- Two top-level elements expand to 41 recursively enumerated annotations:
  14 text, 16 group, 10 rectangle and 1 polyline.
- Modern annotation parent records use `Element_2_Eant`.
- Observed child payload types include `Text2`, `Rectangle2`, `Polyline2`,
  `TextBox_Eant`, `Group_Eant` and `Legend4`.
- Style records include `EantStyleList`, `FillStyle2`, `LineStyle4`, `Color2`
  and `TextStyle5`; a symbol library is also present.
- `Eprj_MapInfo` decodes `proName = Unknown` and `units = inches`. This is not
  evidence of a recognized geographic CRS.

## Observed EANT schema families

### Classic family

```text
AntHeader_Eant (Annotation)
└── ElementNode_Eant (ElementList)
    └── Element_Eant
        └── Eant_Ellipse
```

The examined `colorscale.ovr` contains 17 direct ellipse annotations in this
family. The parent element carries its annotation metadata and the child
carries ellipse-specific geometry/style data.

### Modern family

```text
AntHeader_Eant (Annotation)
└── ElementNode_Eant (ElementList)
    ├── Element_2_Eant
    │   └── Text2
    │       └── TextBox_Eant
    └── Element_2_Eant (group)
        ├── Group_Eant
        │   └── Legend4
        └── Element_2_Eant ... (nested children)
```

This diagram represents structures observed in the examined `lnlandc.map.ovr`; it is not a
complete specification of every ERDAS version or annotation type. Rectangle
and polyline parent records also have type-specific child records (`Rectangle2`
and `Polyline2`).

The reader preserves transformation fields such as `xformMatrix`,
`ixformMatrix`, `bBox`, unit enums and child metadata. It does not silently
apply transforms to geometry because the precise coordinate convention and
composition context have not been confirmed.

## Current writer contract

The generic HFA append writer:

- uses each file's `entryHeaderLength`;
- appends new entry headers and their data blocks before the dictionary;
- moves the dictionary bytes to the end and updates the dictionary pointer;
- updates only the exact parent/last-sibling links needed to attach the new
  entries;
- preserves the free-list chain and all original typed payload blocks;
- reopens the generated file and checks entry count, pointers, ranges, payload
  decode and dictionary equality before returning bytes.

The EANT writer supports cloning known simple annotations and creates four schema-templated profiles: modern `Text2` + `TextBox_Eant`,
`Rectangle2`, `Polyline2`, and classic `Eant_Ellipse`. Creation copies a real
annotation parent and its typed child records, retains its style references,
coordinate-unit definitions and transform matrices, replaces its geometry
fields and requires an explicit four-corner parent `bBox`. New entries are
attached as siblings under the template's existing parent so group context is
not silently flattened.

The writer does not yet synthesize unknown EANT defaults, style-library
records, symbols, groups, polygons or arbitrary geometry schemas. It refuses
text outside the Latin-1 representation observed in the sample. See
[`EANT_WRITER.md`](EANT_WRITER.md) for the coordinate contract and exact API.

A successful HFA/EANT structural reparse is not proof of ERDAS acceptance; no
application round-trip is available in this environment.

## Not established

The following remain unknown or unverified:

- ERDAS acceptance and round-trip behavior of generated output.
- Exact semantics of all group types, legend objects and all 18 types reported
  by other partial OVR readers.
- Full style and symbol reference semantics across versions.
- Whether a companion MAP file imposes required cross-file metadata.
- All coordinate/unit transformation rules and whether they describe paper,
  map or viewport space for each annotation version.
- Full dictionary grammar coverage for arbitrary HFA files.
- Free-list allocation/reuse, deletion, compaction, dictionary updates and
  template-free file construction.
