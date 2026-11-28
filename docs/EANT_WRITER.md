# Schema-templated EANT annotation creation

PDF2OVR provides a conservative new-annotation API on top of its HFA
append writer. It creates annotation records only by using an existing,
decodable annotation in the same OVR as a template. The template supplies the
observed parent schema, child schema, style references, coordinate-unit
records, transformation matrices, and auxiliary child records.

This is **not** a general-purpose EANT schema synthesizer and is not an ERDAS
compatibility certification. The reference set currently contains only two
OVR files and no ERDAS open/save round trip has been performed.

The reference filenames in the table identify external OVR examples used to
observe these record families. Those files are not distributed with this
repository. CI uses small synthetic fixtures; see
[TEST_FIXTURES.md](TEST_FIXTURES.md). The synthetic fixtures are regression
data, not ERDAS compatibility samples.

## Supported creation profiles

| Created geometry | Parent entry | Geometry child | Reference fixture |
|---|---|---|---|
| Text | `Element_2_Eant` | `Text2` + required `TextBox_Eant` child | `lnlandc.map.ovr` |
| Rectangle | `Element_2_Eant` | `Rectangle2` | `lnlandc.map.ovr` |
| Polyline | `Element_2_Eant` | `Polyline2` with 2-row XY `BASEDATA` | `lnlandc.map.ovr` |
| Ellipse | `Element_Eant` | `Eant_Ellipse` | `colorscale.ovr` |

The observed HFA parent-entry names are schema-specific: classic
`Element_Eant` entries use the literal HFA entry name `AntElement`, while
modern `Element_2_Eant` entries use `AntElement_<id>`. The writer follows those
two observed patterns. The parent payload's `name` is the requested annotation
name. Its `description` is inherited from the template unless the caller
provides an explicit `description` value.

The writer refuses to create a requested geometry when the selected template
does not contain the corresponding observed child record. It also refuses
`Text2` templates without an observed `TextBox_Eant` child rather than
inventing one.

## Coordinate and bounding-box contract

Creation copies the template's `scaleUnits`, `positionUnits`, `xformMatrix`,
and `ixformMatrix` unchanged. Therefore:

- text origins, rectangle centers, ellipse centers and axes, and polyline
  vertices must be supplied in the **same local EANT coordinate convention**
  as the selected template geometry;
- `bbox` must supply four ordered corner pairs in the coordinate space encoded
  by the template parent's `bBox` field;
- the writer does not infer a conversion between these two spaces;
- the writer does not automatically calculate an annotation box from geometry;
- do not pass raw PDF page points, GeoPDF map coordinates, or GCP coordinates
  unless they have already been transformed into the template's EANT spaces.

The examined reference files establish that parent `bBox` is a two-row, four-column f64
`BASEDATA` block. They do not establish a universal transform/bbox derivation
rule for all schema versions. Requiring the four corners explicitly is
intentional: a wrong box can make a valid entry invisible or select incorrectly
in the target application.

## Styling

Style references are inherited from the chosen template. `Text2.textStyle`,
`Rectangle2.fillStyle`, `Polyline2.lineStyle`, and classic `Eant_Ellipse.fillStyle`
are not guessed or synthesized. New style-library entries and arbitrary PDF
style-to-EANT conversion are not implemented.

Text is encoded using the Latin-1 pointer-string representation observed in
`lnlandc.map.ovr`. Characters outside that encoding are rejected; they are
never replaced with `?`. A future Unicode strategy requires additional genuine
ERDAS fixtures and confirmation of the relevant schema/version behavior.

## Python API

```python
from pdf2ovr import EANTDocument, EANTWriter

source = "template.ovr"
doc = EANTDocument(source)
text_template = next(
    item for item in doc.iter_annotations()
    if item.element_type == "EANT_TEXT"
)

result = EANTWriter(source).create_text_annotation(
    text_template.entry_offset,
    new_name="New map label",
    description="Control point label",
    text="Control point A",
    origin=(120.0, 240.0),  # template geometry's local EANT coordinates
    bbox=[
        (120.0, 235.0), (120.0, 245.0),
        (190.0, 245.0), (190.0, 235.0),
    ],  # parent bBox coordinate space, not inferred by the writer
    text_box_width=70.0,
)

# result.data contains the new OVR bytes; prefer CLI output validation.
```

Other methods:

```python
writer.create_rectangle_annotation(...)
writer.create_polyline_annotation(...)
writer.create_ellipse_annotation(...)
```

`create_ellipse_annotation` currently supports only the classic
`Element_Eant` / `Eant_Ellipse` profile in the reference fixtures. The writer
attaches a new annotation as a sibling of its template under the same parent
(entry list or containing group), preserving the template's group context.

## JSON CLI

Create `annotation.json`:

```json
{
  "kind": "text",
  "template_entry_offset": 12691,
  "name": "New map label",
  "description": "Control point label",
  "bbox": [[120,235],[120,245],[190,245],[190,235]],
  "geometry": {
    "text": "Control point A",
    "origin": [120,240],
    "scale": 24,
    "text_box_width": 70
  }
}
```

Then run:

```bash
pdf2ovr add template.ovr --spec annotation.json --output output.ovr
```

The command uses a temporary output file, reparses the generated HFA/EANT
structure, checks typed payload decoding, and refuses to overwrite the source.
It does not update a companion `.map` file.

## Safety and limitations

- Supported record types are encoded using the exact dictionary embedded in
the source OVR.
- New parent, geometry and auxiliary child records are appended with fresh
  absolute pointers. Existing data payloads and dictionary bytes are preserved.
- The original template is never edited in place.
- Annotation IDs are unique within the parsed annotation tree.
- Parent/child/sibling links and payloads are re-read before the result is
  returned.
- A successful structural reparse does not prove that ERDAS IMAGINE will
  accept or render the file.
- Group construction, group cloning, polygon/hole semantics, symbol creation,
  custom style-library creation, unrestricted Unicode, MAP update, and
  automatic PDF-to-EANT coordinate/bbox derivation remain unsupported.
- The generated output must be treated as experimental until opened, inspected,
  and saved by the target ERDAS IMAGINE version.
