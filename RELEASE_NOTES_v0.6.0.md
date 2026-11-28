# PDF2OVR v0.6.0 — pre-release draft

**Release status:** Pre-release candidate for manual review. This draft has not
been published to PyPI, tagged in Git, or created as a GitHub Release.

## Summary

PDF2OVR is an experimental Python toolkit for inspecting and editing selected
ERDAS HFA/EANT annotation overlays, extracting PDF content, and inspecting
GeoPDF georeferencing. This release-preparation version consolidates the
project's reader, selected template-derived annotation writer, PDF extraction
and geospatial tools under the `pdf2ovr` import package.

## Breaking changes and migration

- **Python imports:** use `import pdf2ovr` and `from pdf2ovr import ...`.
  The old `ovr_map_generator` namespace remains as a deprecated compatibility
  shim for APIs that are still supported and emits `DeprecationWarning`.
- **Removed command:** `python -m ovr_map_generator.ovr_map_from_pdf ...` and
  the historical four-positional-argument PDF-to-OVR invocation are removed.
  The previous path had already been deliberately disabled and did not
  produce a general PDF-to-OVR conversion.
- **Public API:** `_filter_elements` is not a supported public export.
- **Conversion scope:** general PDF-to-OVR generation is not enabled. Do not
  use this version with the expectation that `extract` creates an `.ovr` file.

## Included functionality

- HFA/EHFA inspection through header-pointer and entry-tree traversal, embedded
  dictionary parsing, typed payload decoding and structural validation.
- EANT inspection for selected observed `Element_Eant` and
  `Element_2_Eant` structures.
- Conservative cloning and template-derived creation of text, rectangle,
  polyline and classic ellipse annotation records.
- PDF extraction to a JSON interchange package with text/vector metadata,
  styling, image placements, optional-content inventory and paint-order data.
- GeoPDF page-level georeferencing inspection, affine calibration from GCPs,
  and coordinate transformation/reprojection where the required extras are
  installed.

## Installation and optional dependencies

Install from a local source checkout while this pre-release is under review:

```bash
python -m pip install .
python -m pip install ".[pdf,geo]"
```

- `pdf`: PyMuPDF-based PDF content extraction.
- `geo`: pypdf/pyproj-based GeoPDF inspection and CRS operations.
- `test`: pytest and the PDF/geospatial test dependencies.

## Known limitations

- The package does not perform general PDF-to-OVR conversion.
- PDF extraction produces an interchange package, not EANT annotation records.
- Annotation creation requires a compatible existing OVR template and is
  limited to the observed text, rectangle, polyline and classic ellipse
  profiles. It does not synthesize arbitrary styles, symbols, groups,
  polygons/holes or all EANT versions.
- Text writing is limited to the single-byte encoding observed in the
  reference schema; unsupported characters are rejected.
- No `.map` composition writer is provided.
- HFA/EANT structural re-parsing is an internal consistency check only. The
  project has not independently validated generated/edited files by opening,
  rendering and saving them in ERDAS IMAGINE.
- GeoPDF tests are synthetic and the OVR test fixtures are synthetic. The
  original reference OVR examples are not redistributed because their
  redistribution rights have not been established.
- PDF clipping/compositing, patterns, gradients, full color management,
  embedded font programs, OCR and some hidden optional-content objects are not
  fully reconstructed.

## Testing

The self-contained regression suite uses checked-in synthetic HFA/EANT fixtures
and generated PDF/GeoPDF test documents. These exercise the project's actual
reader, writer, PDF extractor and geospatial code paths. They are not full
ERDAS-produced OVRs or independent production GeoPDF documents. No ERDAS
IMAGINE compatibility claim is made.

## Pre-release warning

This is experimental software. Back up source OVR files before testing any
writer operation. Inspect all output and treat it as unverified until it has
been tested in the target ERDAS IMAGINE version.

## Licensing review required before publication

The project metadata declares the selected MIT license, but the copyright-holder
line in `LICENSE` is intentionally a placeholder. The supplied source archive
contains no Git history or contributor provenance, so the rights to every
included contribution could not be independently verified. Confirm ownership
and replace the placeholder before publishing or distributing this release.
