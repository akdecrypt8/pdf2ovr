# pdf2ovr

Generate ERDAS Imagine `.ovr` (annotation overlay) and `.map` (map composition) files from PDF content using the EHFA container format. 

## Installation

```bash
pip install pymupdf
```

## Usage

### Command Line

```bash
python -m ovr_map_generator.ovr_map_from_pdf input.pdf template.ovr template.map output_prefix
```

Example:
```bash
python -m ovr_map_generator.ovr_map_from_pdf my_map.pdf template.ovr template.map my_output
```

This produces:
- `my_output.map.ovr` - Annotation overlay file
- `my_output.map` - Map composition file (references the .ovr)

### Python API

```python
from ovr_map_generator import generate

generate(
    input_pdf="my_map.pdf",
    template_ovr="template.ovr",
    template_map="template.map",
    output_prefix="my_output"
)
```

### Extract PDF Elements Only

```python
from ovr_map_generator import extract_pdf_elements, _filter_elements

elements = extract_pdf_elements("my_map.pdf")
filtered = _filter_elements(elements)

for e in filtered:
    print(f"{e.elem_type}: {e.content} @ ({e.x:.1f}, {e.y:.1f})")
```

## Supported Elements

- **Text2** - Text annotations with position, font size, and content
- **Rectangle2** - Rectangular shapes
- **Polyline2** - Lines and multi-vertex polylines
- **Symbol2** - Symbols/markers (not extracted from PDF)
- **Group_Eant** - Group containers (not extracted from PDF)
- **Ellipse2** - Elliptical shapes (not extracted from PDF)

## EHFA Format

The `.ovr` and `.map` files use the ERDAS Imagine EHFA container format:

- **Header**: `EHFA_HEADER_TAG\0` (16 bytes) + version info + pointers
- **Entries**: 124 bytes each (next, prev, parent, child, data_ptr, data_size, name[64], type[32], mod_time)
- **Tree**: Annotation root with ElementList, SymbolLibrary, StyleLibrary, Map_Info
- **Element payloads**: Type-specific binary formats with coordinate transforms

## Requirements

- Python 3.8+
- PyMuPDF (`pip install pymupdf`)

## License

MIT
