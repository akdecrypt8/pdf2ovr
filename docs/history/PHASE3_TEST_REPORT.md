# Phase 3 test report — v0.4.0

## Test strategy

The tests use generated PDFs with `pypdf` to exercise the supported page-level
GeoPDF dictionaries. Existing Phase 1/2 tests continue to run, including the
two user-supplied OVR fixtures when `PDF2OVR_FIXTURE_DIR` points to them.

## Covered cases

- ISO `/VP` + `/Measure /GEO` with projected EPSG and geographic EPSG.
- `/GPTS`/`/LPTS` pairing, optional `/Bounds`, and missing `/LPTS`.
- Multiple pages and multiple ISO viewports.
- Projected `/PCSM` precedence and geographic `/PCSM` ignoring.
- OGC single `/LGIDict` frames, frame arrays, PDF-to-map `/CTM` precedence over
  `/Registration`, two-point diagonal `/Neatline`, and map-frame selection.
- Invalid/singular CTM, malformed bounds/neatline and invalid page rotation.
- Overlapping OGC map frames and mixed ISO/OGC encodings are treated as
  ambiguous.
- CRS reprojection, GCP calibration, residual statistics, affine inversion,
  collinear/rank-deficient rejection and malformed calibration JSON.
- CropBox/rotation coordinate adapter round trips.
- CLI JSON inspection, strict failure, calibration, transform and no-clobber.

## Latest local result

With the two supplied OVR fixtures:

```text
90 passed (latest run)
```

Without the optional external OVR fixtures:

```text
67 passed, 23 skipped (latest run)
```

The count includes Phase 1/2 regression tests, synthetic GeoPDF tests and,
when available, fixture-backed binary/EANT tests. All 23 skipped tests require
the two externally supplied `.ovr` reference files.

The `pdf2ovr-0.4.0-py3-none-any.whl` wheel also built successfully with the
installed build tools, installed into a fresh virtual environment, displayed
CLI help, and ran `pdf2ovr georef` against a generated ISO-viewport PDF.

## Limitations of the evidence

- GeoPDF tests are synthetic; a public GDAL Adobe-style GeoPDF test document
  was identified but could not be downloaded in this offline environment. The
  GeoPDF-specific cases are therefore synthetic fixtures, not real-world samples.
- No GDAL PDF driver cross-check was available in the local environment.
- No OVR generated from Phase 3 was opened in ERDAS IMAGINE because Phase 3
  does not write OVR geospatial metadata.
- The existing two OVRs test the HFA/EANT regression suite, not GeoPDF parsing.
