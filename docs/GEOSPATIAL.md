# GeoPDF georeferencing and coordinate conventions

PDF2OVR includes a read-only geospatial analysis layer. It reads geospatial
metadata and computes page-to-map transformations; it does **not** yet write
that metadata or transformed geometry into EANT `.ovr` files.

## Supported page-level encodings

### Adobe / ISO 32000 geospatial viewports

The reader follows a page's `/VP` array (or single viewport), reads its `/BBox`
and `/Measure` dictionary, and accepts `/Measure /Subtype /GEO`. It extracts:

- `/GCS`: coordinate reference system metadata;
- `/GPTS`: geographic or projected tie-point coordinates;
- `/LPTS`: local unit-square tie-point coordinates;
- `/Bounds`: optional normalized viewport boundary;
- `/PCSM`: optional 12-number projected coordinate system matrix;
- `/DCS` and `/PDU`: retained as raw display metadata.

For a geographic GCS, `/GPTS` pairs are interpreted as latitude, longitude and
normalized to GIS X/Y as longitude, latitude. For a projected GCS they are
interpreted as easting, northing. If the CRS kind cannot be determined, pair
order is preserved and a warning is emitted.

The PDF 2.0 errata says that `/LPTS` is required in PDF 2.0, though it was
optional in the earlier PDF 1.7 extension. A viewport missing `/LPTS` is still
reported with its `/GPTS`, but PDF2OVR does not invent the missing local tie
points or produce a transform.

`/PCSM` is parsed only when it contains exactly 12 finite numbers and the GCS
is known to be projected. Its two-dimensional affine slice is composed with
the page viewport `/BBox`. The PDF 2.0 errata specifies that `/PCSM` applies to
projected GCS, is ignored for geographic GCS, and takes priority over `/GPTS`
when present. This path is covered by synthetic unit tests, not yet by a
separately acquired real GeoPDF fixture.

### OGC PDF georegistration 2.2 (`/LGIDict`)

The reader accepts a single `/LGIDict` frame or an array of frames. It reads:

- `/Projection` dictionary (retained as raw metadata; explicit `/EPSG` or
  `/WKT` values are resolved);
- `/Registration` tuples (PDF X/Y followed by map X/Y);
- `/CTM` six-coefficient PDF-to-map transform; it takes precedence over `/Registration`;
- `/Neatline` polygon and `/Display` dictionary.

Three or more non-collinear registration tuples produce a least-squares affine
fit. OGC 08-139r3 defines `/CTM` directly from PDF coordinates to map
coordinates and gives it precedence over `/Registration`. When both are
available, the registration fit is retained as cross-check metadata and a
warning is emitted if it materially disagrees with the CTM. If the CTM is
invalid or absent, a valid registration fit is used. A two-point `/Neatline`
is interpreted as the diagonal of a rectangular frame, as permitted by the
specification.

A single OGC frame without `/Neatline` is treated as associated with the full
page. If there are multiple frames, the standard requires a `/Neatline` for
each frame. If two OGC frames overlap at the queried point, selection is
reported as ambiguous rather than arbitrarily choosing one. Pages containing
both `/VP` and `/LGIDict` entries are also treated as ambiguous for point-based
selection.

## CRS resolution policy

PDF2OVR does not guess EPSG codes from projection names, datum strings, UTM
zone numbers, map extents or coordinate magnitudes. It resolves explicit
`/EPSG` and `/WKT` CRS definitions with `pyproj`. A classic OGC `/Projection`
dictionary that does not contain an explicit EPSG/WKT definition is preserved
in `metadata.projection_dictionary`, but `crs` remains `null` and
`crs_resolved` is `false`.

When `pyproj` is not installed, raw explicit CRS values can still be reported
and affine transformations can still be calculated. CRS validation and
reprojection are unavailable until the `geo` extra is installed.

## Coordinate spaces

- **PDF default user space:** PDF coordinates with the standard bottom-left
  origin, before page `/Rotate` is applied. All parsed GeoPDF bounds, neatlines,
  GCPs and affine transforms use this space.
- **PDFExtractor displayed space:** the page's displayed orientation, with a
  bottom-left origin, after the PyMuPDF rotation transform. The two adapter
  functions use PyMuPDF's page transformation, rotation/derotation matrices,
  CropBox and page dimensions to move between the spaces.
- **Map coordinate space:** coordinates in the declared or user-specified CRS.
  Geographic outputs use traditional GIS X/Y order (longitude, latitude) for
  interoperability with projected easting/northing coordinates.

`PageGeoReference.map_point(x, y)` expects PDF default user-space coordinates.
`PageGeoReference.map_extractor_point(pdf_path, x, y)` accepts the displayed
bottom-left coordinates returned by this project's PDF extractor.

## GCP calibration

A 2D affine transformation requires at least three non-collinear GCPs. The
least-squares implementation uses normalized input coordinates and Householder
QR rather than normal equations. For four or more points it reports residuals,
RMSE and maximum error. It rejects rank-deficient or poorly conditioned GCP
layouts rather than silently fitting a singular transform.

For reliable calibration, distribute GCPs across the map extent, avoid
collinear points, and inspect residuals. A low residual alone does not prove
that the supplied map coordinates or CRS are correct.

Calibration JSON is versioned (`schema_version: 1`) and validated before it is
used by the `transform` command.

## CLI examples

```bash
# Inspect every page's georeferencing
pdf2ovr georef map.pdf --json

# Inspect one zero-based page and require an affine transform
pdf2ovr georef map.pdf --page 0 --strict

# Calibrate from four PDF-space/map-space control points
pdf2ovr calibrate \
  --gcp 100,100,500000,4600000 \
  --gcp 900,100,501000,4600000 \
  --gcp 900,700,501000,4599000 \
  --gcp 100,700,500000,4599000 \
  --crs EPSG:32631 \
  --output calibration.json

# Transform a PDF user-space point and reproject to WGS 84 longitude/latitude
pdf2ovr transform calibration.json --point 500,400 --target-crs EPSG:4326
```

## Explicit limitations

This implementation currently does not parse georeferencing attached to image
XObjects or Form XObjects; it only examines page `/VP` and `/LGIDict` entries.
It does not yet parse arbitrary OGC projection parameters into a CRS, handle
3D georegistration, or turn non-linear/non-affine registrations into higher
order transforms. It does not update `.map` composition files or EANT `Map_Info`
records. It is a georeference reader and transform/calibration layer, not yet an
end-to-end PDF-to-OVR exporter.

The tests build synthetic PDFs with `pypdf` to exercise both encodings and edge
cases. The public GDAL test suite documents a real Adobe-style GeoPDF sample,
but that sample could not be downloaded into this offline build environment;
therefore no claim of validation against an independent real GeoPDF fixture is
made. No generated OVR or MAP has been opened in ERDAS IMAGINE.

## References

- [PDF 2.0 geospatial-feature errata](https://pdf-issues.pdfa.org/32000-2-2020/clause12.html), especially clauses 12.10.2–12.10.3.
- [OGC 08-139r3: PDF Georegistration Encoding Best Practice 2.2](https://docs.ogc.org/bp/08-139r3/08-139r3.html), especially Tables 1–2 for `/LGIDict`, `/CTM`, `/Registration` and `/Neatline`.
- [Library of Congress: OGC PDF georegistration 2.2](https://www.loc.gov/preservation/digital/formats/fdd/fdd000312.shtml).
- [GDAL: Geospatial PDF driver](https://gdal.org/en/stable/drivers/raster/pdf.html).
- [PyMuPDF Page coordinates and rotation matrices](https://pymupdf.readthedocs.io/en/latest/page.html).
