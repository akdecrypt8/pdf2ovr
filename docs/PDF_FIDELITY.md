# PDF fidelity extraction model

PDF2OVR provides a page-aware PDF extraction interchange model. It is deliberately
separate from the HFA/EANT writer: extracting an object correctly does not mean
that its semantics can already be encoded in an ERDAS annotation record.

## Coordinate convention

`ExtractedElement.bbox`, normalized element geometry, text baselines, clip
scissors, and image placement quads are expressed in **displayed page space
with a bottom-left origin**, in PDF points. Page metadata retains PyMuPDF's
native MediaBox and CropBox values plus displayed page width, height, and
rotation. Image placements also retain the source six-value affine matrix.

For PDF-to-map conversion, use the geospatial API and explicitly choose
the matching viewport or GCP calibration. Do not treat page points as map
coordinates automatically.

## Vector paths

The extractor reads `Page.get_drawings(extended=True)` and retains:

- path operation type (fill, stroke, or fill-and-stroke);
- stroke and fill colors returned by MuPDF;
- independent stroke/fill opacity;
- line width, dash pattern, caps and joins;
- even-odd versus nonzero fill rule;
- original path command type and transformed control/vertex coordinates;
- cubic Bézier control points plus an adaptive flattened polyline;
- rectangle and quadrilateral geometry;
- subpath index and total subpath count;
- source paint-order sequence and optional-content layer label.

Curves are flattened using adaptive de Casteljau subdivision. The tolerance is
specified in displayed PDF points (`PDFExtractor(curve_tolerance=...)` or
`pdf2ovr extract --curve-tolerance`). Original cubic control points remain in
`path_metadata.source_commands`, so downstream tooling can reconstruct curves
instead of relying only on the flattened polyline.

Compound paths are emitted as per-subpath normalized elements while the full
source command list and fill rule are retained on each subpath record. The
extractor warns when a compound filled path contains multiple subpaths because
turning those into independent EANT fills can lose hole and winding semantics.

## Text

Text is primarily extracted from `Page.get_texttrace()`, which provides paint
sequence numbers, per-character Unicode/glyph information, origins, boxes,
font metadata, writing mode, bidirectional level, render mode, colors and
opacity. The ordinary text dictionary is used to recover complete span strings
where available. Text elements preserve:

- baseline position rather than a bounding-box corner;
- text direction/rotation after page rotation is applied;
- font name, font size and font flags;
- character flags, writing mode and bidirectional level;
- color, opacity and rendering mode;
- per-character glyph IDs, Unicode values, origins and bounding boxes.

This is extraction metadata, not a font embedding system. It does not guarantee
that the target application has the same font, that an unsupported glyph can
be rendered, or that Arabic/Indic shaping can be recreated by the future EANT
writer. Glyph IDs and Unicode values are both retained to help later mapping.

## Images

`extract_document()` and `extract_image_occurrences()` process every page.
The extractor uses image occurrence metadata rather than assuming one image
occupies a whole page. Each placement retains:

- page number and paint-order sequence;
- four transformed image corners and an axis-aligned displayed bbox;
- the original six-value image-to-page transform;
- pixel dimensions, xref and optional-content association;
- the associated layer label when the page display list provides one.

Unique image payloads are deduplicated into `PDFImageAsset` records by a SHA-256
key. Extractable soft-mask bytes are retained separately. Inline images without
recoverable binary content are still represented as placements with a warning.

`extract_images()` remains as a backwards-compatible first-page helper. New
code should use `extract_document()` or `extract_image_occurrences()`.

## Layers and optional content

The result inventories Optional Content Groups (OCGs), their names, default
visibility state, intent, usage and alternate configurations. Object-to-layer
associations are retained for text, vector paths and images when exposed by
MuPDF.

Important limitation: extraction follows the PDF visibility configuration
that MuPDF exposes for the page. Objects in OCGs that are OFF may not be
returned by its display-list extraction APIs. The current extraction engine
does not parse raw PDF marked-content operators to recover every hidden OCG or
OCMD-controlled object. An OFF layer's metadata is recorded and the result
contains a warning; it is not claimed that all hidden geometry has been
extracted.

## Clipping paths and transparency groups

`get_drawings(extended=True)` exposes hierarchical clip and transparency-group
records. The extractor preserves clip commands, scissor bounds, fill rule,
levels, group isolation/knockout flags, blend mode and opacity. Vector elements
carry the active clip and group IDs for the scopes exposed by the API.

The current model does **not** fully reconstruct how those clips apply to text
and image paint operations. A page with clips therefore carries a warning.
Transparency groups are described, but their blend/knockout compositing is not
flattened or re-rendered by this phase.

## Paint order

Text and drawing sequence numbers come from PyMuPDF's display-list extraction.
Image occurrences are matched to the `fill-image` records in the page bbox log.
The bbox-log index is the paint-order sequence used by the corresponding text
and drawing APIs. Compound fill/stroke drawing paths can represent more than
one low-level paint operation; a normalized vector path's order refers to the
first sequence number returned for that path.

## Current limitations

- Gradient/shading (`fill-shade`) operations are detected and warned about, not
  reconstructed as vector gradients.
- Complex fill-rule relationships between compound subpaths are retained as
  metadata, not materialized as standalone geometry with holes.
- PDF pattern fills, spot colors, overprint, calibrated color spaces and full
  ICC color-management semantics are not preserved as editable vector styles.
- Text font programs are not embedded; the source font name and glyph metadata
  are retained only.
- Text/image clipping scopes are not fully associated to all content types.
- Transparency-group compositing is not flattened.
- Forms and image XObjects can be encountered through MuPDF's page display
  list, but this package does not expose a fully recursive raw PDF object graph.
- PDF annotations, widgets, form fields and OCR for scanned pages are outside
  this phase.
- None of these extracted objects are written into EANT by this phase.

## Interchange package

Run:

```bash
pdf2ovr extract input.pdf --output extracted_package
```

The output directory contains:

- `manifest.json`: page metadata, elements, styles, source path commands,
  text run/glyph metadata, image placements, clips, groups, layers and warnings;
- `assets/`: deduplicated image payloads;
- optional `*.smask.bin` files for image soft masks.

The command refuses to overwrite an existing output directory. The manifest
contains relative asset paths and byte counts, not binary data embedded as
hex. This package is an extraction interchange format, not an OVR file.

## API

```python
from pdf2ovr import PDFExtractor

extractor = PDFExtractor("input.pdf", curve_tolerance=0.25)
try:
    result = extractor.extract_document()
    print(result.summary())
    payload = result.to_dict()  # JSON serializable; image bytes remain separate
finally:
    extractor.close()
```
