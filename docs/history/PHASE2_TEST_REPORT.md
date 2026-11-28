# Phase 2 validation report — pdf2ovr 0.3.0

Date: 2026-11-27

## Scope

Phase 2 adds a generic HFA container/dictionary reader and conservative append
writer, an EANT semantic reader for the two schema families observed in the
provided OVR samples, a limited simple-annotation clone operation, and an
inspection/clone CLI.

The original PDF-to-OVR generation path remains deliberately blocked. The
writer does not yet construct arbitrary EANT geometry from PDF objects.

## Automated tests

With `PDF2OVR_FIXTURE_DIR` set to the directory containing both supplied files:

```text
54 passed
```

Without external OVR fixtures:

```text
31 passed, 23 skipped
```

The skipped tests require one or both supplied OVR fixtures. The independent
unit tests still run without them.

## Fixture coverage

| Fixture | Reachable entries | Recursive annotations | Typed decode errors |
|---|---:|---:|---:|
| `colorscale.ovr` | 46 | 17 | 0 |
| `lnlandc.map.ovr` | 142 | 41 | 0 |

The dictionary codec round-trips 184 typed payloads across the two samples and
compares semantic values after decoding again.

Clone regression tests cover an ellipse in the classic `Element_Eant` family,
and text and rectangle annotations in the modern `Element_2_Eant` family.
Group cloning is explicitly rejected.

## CLI and package smoke tests

- `pdf2ovr --help`: passed.
- `pdf2ovr inspect <sample> --json`: passed for both samples.
- `inspect --json` includes annotation entry offsets suitable for the clone command.
- CLI clone of a classic ellipse: output reopened; 0 typed decode errors.
- CLI clone of a modern text annotation: output reopened; 0 typed decode errors.
- CLI refused to overwrite an existing destination without `--force`.
- Wheel `pdf2ovr-0.3.0-py3-none-any.whl` built successfully using already-installed build tools.
- Wheel installed in a clean virtual environment with `--no-deps`; installed CLI inspected both OVRs without PyMuPDF installed.
- Python compile check: passed.

The first isolated wheel build attempt tried to download `setuptools>=68` and
failed because network access was unavailable. The build was then rerun
successfully with `--no-build-isolation` and the installed setuptools/wheel.

## Safety boundaries and known limitations

- No ERDAS IMAGINE open/save round-trip has been performed. Structural
  self-consistency is not proof of ERDAS application compatibility.
- The companion `lnlandc.map` file was not supplied; MAP composition references
  are not tested or modified.
- HFA append-only operations do not consume the free list, delete, replace,
  compact, rewrite dictionaries or create an OVR from scratch.
- EANT cloning only supports known simple, non-group annotations already present
  in an existing file. It copies geometry and style references; it does not
  create new geometry or change coordinates/text content.
- Group cloning and unknown annotation types are not implemented.
- Full Unicode annotation writing, full CRS semantics, transform application,
  and arbitrary PDF-to-EANT conversion remain unimplemented.
- Keep original OVR files backed up and treat cloned output as experimental
  until ERDAS validation is available.

## Input checksums

The following are SHA-256 identifiers for the input files used in this session.
They are included for reproducibility; the original project ZIP and OVR inputs
were read as source/reference files and were not written by the Phase 2 tools.

3408e09c6e46f843cd886de23b6faed77f785a007bcd2b9aaf6035b68f2e3113  /mnt/data/pdf2ovr-main.zip
a824bb47b0a2c9b51d86fc364512d89cefcd54d96c09ce109b7f8f0f7e814109  /mnt/data/colorscale.ovr
ea261a9bb29cef9e0a1162f023b0178358b2b3c6814fd7895725fed34cf3a3d4  /mnt/data/lnlandc.map.ovr
