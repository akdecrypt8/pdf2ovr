# Phase 5 test report v0.6.0

The regression suite was run with both supplied reference OVR files enabled:

```bash
PYTHONPATH=. PDF2OVR_FIXTURE_DIR=/mnt/data pytest -q
```

Latest result: **119 passed** (including Phase 5 creation tests and both
external OVR fixtures).

The self-contained run without setting `PDF2OVR_FIXTURE_DIR` reported **78
passed, 41 skipped**. The skipped tests require the two external OVR fixtures.
Python bytecode compilation also completed successfully:

```bash
python -m compileall -q ovr_map_generator tests
```

Phase 5 tests cover:

- classic `Element_Eant` / `Eant_Ellipse` creation;
- modern `Element_2_Eant` / `Text2` / `TextBox_Eant` creation;
- modern `Rectangle2` creation;
- modern `Polyline2` / `VectorData_2_Eant` creation;
- style-reference, transform-metadata and auxiliary-child preservation;
- schema-specific classic/modern HFA parent-entry naming;
- optional description replacement and default description preservation;
- parent bounding-box serialization;
- nested-group sibling attachment;
- unique annotation IDs and schema-specific HFA parent entry names;
- invalid bounds, non-numeric/non-finite geometry, incompatible templates,
  unsupported text encoding and boolean IDs;
- CLI creation, output reparse and source overwrite protection.

The wheel was built with `pip wheel --no-deps --no-build-isolation` and
installed into a fresh virtual environment. The installed `pdf2ovr add`
command was exercised against both samples: it created a modern Text2
annotation in `lnlandc.map.ovr` and a classic Eant_Ellipse annotation in
`colorscale.ovr`. Both resulting files were reopened using `pdf2ovr inspect`;
the expected new annotation and HFA entry count were present, with no typed
payload decode errors. `pdf2ovr --help` and `pdf2ovr add --help` also passed.

These tests establish that generated files pass the project's own HFA/EANT
structural parser and typed-payload decoder. They do **not** establish that
ERDAS IMAGINE opens, renders or saves the generated files. Synthetic test
assertions and two public OVR fixtures are not a substitute for an application
round trip.
