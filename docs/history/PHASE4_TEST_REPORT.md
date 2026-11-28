# Phase 4 test report v0.5.0

## Environment

- Python: 3.13 runtime used for this build
- PyMuPDF: 1.26.7
- pypdf: 5.9.0
- pytest: 9.0.2
- Reference OVR fixtures: `colorscale.ovr` and `lnlandc.map.ovr`

## Result

```text
PDF2OVR_FIXTURE_DIR=/mnt/data python -m pytest -q
101 passed
```

The tests cover the pre-existing Phase 1, Phase 2 and Phase 3 suites, the two
supplied OVR fixtures, and new Phase 4 synthetic PDF cases:

- text trace ordering, baseline coordinates, font attributes and rotation;
- vector stroke/fill styles, dash patterns, opacity and fill rules;
- compound paths and subpath identity;
- cubic Bezier flattening and quadrilateral geometry;
- page-aware image occurrences, affine placement and asset deduplication;
- alpha/soft-mask image retention;
- OCG inventories, object layer names and inactive-layer warnings;
- extended clipping paths and transparency groups;
- shared paint-order sequence between text, vector paths and image placements;
- JSON serialization without embedding binary image bytes;
- CLI extraction package output and no-overwrite behavior.

## Validation boundary

The Phase 4 PDF tests use generated synthetic PDFs. The two supplied real OVR
files continue to run as regression fixtures for the HFA/EANT reader/writer.
No ERDAS IMAGINE validation was performed. No real-world third-party GeoPDF or
complex production drawing PDF was available for an integration round trip.
The test result establishes the tested extraction model's behavior on these
fixtures, not universal PDF fidelity or EANT serialization compatibility.
