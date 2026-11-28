# PDF2OVR

**PDF2OVR is an experimental Python toolkit for inspecting and editing selected ERDAS HFA/EANT annotation overlays, extracting PDF content, and inspecting GeoPDF georeferencing.**

> **Important limitation:** general PDF-to-OVR conversion is not enabled. The PDF extractor writes a JSON interchange package; it does not automatically convert extracted PDF objects into ERDAS annotations. The annotation writer can create a small set of records from compatible annotation templates already present in an OVR file. Generated or edited OVR files have not been independently validated in ERDAS IMAGINE.

Version: `0.6.0` pre-release candidate; not yet published to PyPI.

## Implemented capabilities and limitations

### HFA/EHFA and EANT inspection

- Read the EHFA header through its header pointer and the declared entry-header length.
- Traverse the reachable HFA entry tree and validate parent, child, sibling, data and free-list references.
- Parse the embedded HFA dictionary and decode supported typed payloads.
- Inspect selected EANT annotation records, including the observed classic `Element_Eant` and modern `Element_2_Eant` families.
- Report text, rectangle, ellipse, polyline and group metadata where recognized, plus selected style, symbol, transform and map-information records.

The parser is evidence-driven and bounded. It does not claim to understand every HFA dictionary type, EANT schema version or annotation object.

### Selected OVR annotation editing

- Inspect an OVR with `pdf2ovr inspect`.
- Clone supported simple annotations from an existing OVR with `pdf2ovr clone`.
- Create selected text, rectangle, polyline and classic ellipse records with `pdf2ovr add`, using a compatible existing annotation as a schema/style/transform template.
- Re-open and validate written output using this package's HFA/EANT parser.

The writer does not create a complete OVR from scratch, synthesize arbitrary style/symbol libraries, update a companion `.map` composition, or guarantee that ERDAS IMAGINE will open or render generated output. Text uses the single-byte encoding observed in the reference schema; unsupported characters are rejected.

### PDF extraction

With the optional `pdf` extra, extract text, vector paths, styles, image occurrences, optional-content layer information, clipping/group metadata and paint-order information into an interchange package containing `manifest.json` and image assets.

Extraction is not a lossless PDF renderer. Advanced clipping/compositing, gradients, patterns, full color management, embedded font programs, some hidden optional-content objects, annotations/widgets and OCR are not fully reconstructed.

### GeoPDF inspection and calibration

With the optional `geo` extra, inspect supported page-level Adobe/ISO geospatial viewports and OGC PDF georegistration metadata. Fit affine transformations from ground-control points and optionally reproject coordinates when a source CRS is explicit and the required CRS dependency is available.

Image/Form XObject georeferencing, arbitrary legacy OGC projection-parameter conversion, 3D georegistration, EANT Map_Info updates and `.map` composition editing are not implemented. The geospatial tools do not automatically convert coordinates into a selected EANT template's local coordinate convention.

## Installation

PDF2OVR `0.6.0` is being prepared as a pre-release and has not been uploaded to PyPI. Install from a local checkout:

```bash
python -m pip install .
```

Install optional capabilities as needed:

```bash
# PDF extraction (PyMuPDF)
python -m pip install ".[pdf]"

# GeoPDF inspection, CRS handling and reprojection
python -m pip install ".[geo]"

# All test dependencies
python -m pip install -e ".[test]"
```

The core HFA/EANT inspection and editing modules do not require the optional PDF or geospatial dependencies. The `pdf`, `geo` and `test` extras are independent and remain available.

## Command-line usage

The installed command is `pdf2ovr`. The same interface is available through `python -m pdf2ovr`.

### Inspect an OVR

```bash
pdf2ovr inspect input.ovr
pdf2ovr inspect input.ovr --json
```

The JSON summary includes reachable HFA entries, decoded annotation information and entry offsets that can be used as template references by the editing commands.

### Clone an existing simple annotation

```bash
pdf2ovr clone input.ovr \
  --entry-offset 12691 \
  --name "Copy of label" \
  --output cloned.ovr
```

The entry offset must identify a supported annotation in the input file. Geometry and style references are copied from that annotation; this command does not reposition or restyle it. Existing destination files are not replaced unless `--force` is supplied. The source file cannot be used as the destination.

### Create an annotation from an existing template

Create a JSON specification such as `annotation.json`:

```json
{
  "kind": "text",
  "template_entry_offset": 12691,
  "name": "New map label",
  "description": "Control point label",
  "bbox": [[120, 235], [120, 245], [190, 245], [190, 235]],
  "geometry": {
    "text": "Control point A",
    "origin": [120, 240],
    "scale": 24,
    "text_box_width": 70
  }
}
```

Then run:

```bash
pdf2ovr add template.ovr --spec annotation.json --output output.ovr
```

Supported `kind` values are `text`, `rectangle`, `polyline` and `ellipse`. The selected template must contain the corresponding observed schema. Coordinates must already be expressed in the coordinate convention of that template. The four `bbox` points must be expressed in the parent bounding-box coordinate space. PDF points, GeoPDF coordinates and GCP coordinates are not interchangeable without an explicit transformation.

See [the EANT writer guide](docs/EANT_WRITER.md) for the per-geometry JSON fields and limitations.

### Extract a PDF interchange package

Install the `pdf` extra, then run:

```bash
pdf2ovr extract input.pdf --output extracted_package
pdf2ovr extract input.pdf --output extracted_package --curve-tolerance 0.25
pdf2ovr extract input.pdf --output extracted_package --no-images
```

The output directory contains `manifest.json` and, unless `--no-images` is used, deduplicated assets. The command refuses to overwrite an existing output directory.

### Inspect GeoPDF georeferencing

Install the `geo` extra, then run:

```bash
pdf2ovr georef map.pdf --json
pdf2ovr georef map.pdf --page 0 --strict
```

The `--strict` option returns a non-zero status when the selected page has no usable georeferencing transform.

### Calibrate and transform coordinates

Fit a two-dimensional affine transform using at least three non-collinear ground-control points. Each GCP is `PDF_X,PDF_Y,MAP_X,MAP_Y`, with PDF coordinates in PDF default user space:

```bash
pdf2ovr calibrate \
  --gcp 100,100,500000,4600000 \
  --gcp 900,100,501000,4600000 \
  --gcp 900,700,501000,4599000 \
  --gcp 100,700,500000,4599000 \
  --crs EPSG:32631 \
  --output calibration.json

pdf2ovr transform calibration.json --point 500,400 --target-crs EPSG:4326
```

A CRS is never inferred from coordinate magnitudes, names or extents. Reprojection requires an explicit source CRS and the optional CRS dependency.

## Python API

The official import package is `pdf2ovr`:

```python
from pdf2ovr import EANTDocument, EANTWriter

source = "template.ovr"
document = EANTDocument(source)
template = next(
    item for item in document.iter_annotations()
    if item.element_type == "EANT_TEXT"
)

result = EANTWriter(source).create_text_annotation(
    template.entry_offset,
    new_name="Control point A",
    text="Control point A",
    origin=(120.0, 240.0),
    bbox=[
        (120.0, 235.0), (120.0, 245.0),
        (190.0, 245.0), (190.0, 235.0),
    ],
    text_box_width=70.0,
)

# result.data contains the new OVR bytes. The source file is not modified.
```

For PDF extraction:

```python
from pdf2ovr import PDFExtractor

extractor = PDFExtractor("input.pdf", curve_tolerance=0.25)
try:
    result = extractor.extract_document()
    manifest = result.to_dict()
finally:
    extractor.close()
```

For the full public surface, inspect `pdf2ovr.__all__` and the module documentation under [`docs/`](docs/).

### Legacy import migration

The former `ovr_map_generator` import namespace remains as a deprecated compatibility shim for supported APIs. It emits a `DeprecationWarning` and forwards to the single `pdf2ovr` implementation. New code should migrate to `pdf2ovr`.

The obsolete `ovr_map_generator.ovr_map_from_pdf` module and its four-positional-argument PDF-to-OVR command have been removed. The old command is not a supported migration path.

## Testing and synthetic fixtures

Run the self-contained test suite:

```bash
python -m pip install -e ".[test]"
python -m pytest -q
```

The repository includes two independently generated, compact synthetic HFA/EANT fixtures. They exercise the actual HFA reader, embedded dictionary decoder, EANT reader and selected writer paths. The fixture generator is `tests/fixtures/build_synthetic_ovr.py`; the committed binaries are checked against regenerated output in the test suite.

These fixtures reproduce selected structures observed in the supplied reference examples, but they are not complete ERDAS-produced OVR files and do not prove application compatibility. The original `colorscale.ovr` and `lnlandc.map.ovr` examples are not redistributed because their redistribution rights have not been established. Tests do not depend on those external files.

The existing CI workflow is configured for Windows and Ubuntu with Python 3.10–3.13. Local release-preparation validation is performed on Python 3.13; other matrix jobs must run in GitHub Actions before release approval.

## Compatibility and known limitations

- No end-to-end PDF-to-OVR conversion is currently enabled.
- No `.map` composition writer is provided.
- OVR editing is limited to observed HFA/EANT schema profiles and requires a compatible input template.
- New style libraries, symbol libraries, groups, polygons/holes and arbitrary annotation types are not synthesized.
- Full Unicode text writing is not implemented.
- No generated or edited OVR has been independently opened, rendered and saved by ERDAS IMAGINE in this project validation.
- HFA/EANT structural re-parsing is an internal consistency check, not proof of ERDAS compatibility.
- PDF and GeoPDF capabilities are limited to the documented APIs and are not a claim of full PDF fidelity.

Read the detailed limitations in [the HFA/EANT format notes](docs/EHFA_EANT_FORMAT.md), [writer guide](docs/EANT_WRITER.md), [geospatial guide](docs/GEOSPATIAL.md) and [PDF extraction guide](docs/PDF_FIDELITY.md).

## License

The selected license is MIT; see [`LICENSE`](LICENSE). The copyright-holder
notice remains a placeholder pending confirmation that all included code may
be licensed by the named holder. Complete that notice and verify contributor
rights before any public distribution.
