# Changelog

This changelog consolidates the project's implementation milestones. Dates are
not included because the source phase logs did not record reliable release
publication dates. Version headings identify project milestones; they do not
imply that a corresponding PyPI release or Git tag exists.

## 0.6.0 pre-release candidate (not published)

### Added

- Schema-templated creation of text, rectangle, polyline and classic ellipse
  annotations from compatible records already present in an input OVR.
- Explicit four-corner parent `bBox` serialization, text origin/orientation/
  scale/alignment updates, optional TextBox width, and polyline XY pair
  serialization.
- Non-colliding annotation IDs and sibling attachment under the template's
  existing parent, preserving the observed group context.
- Strict JSON-based `pdf2ovr add` with no-clobber behavior, temporary output
  validation and source-overwrite protection.
- Strict rejection of invalid numeric inputs, unsupported text characters,
  incompatible templates and unsupported annotation profiles.
- Independently generated synthetic classic and modern HFA/EANT parser fixtures
  for the release-preparation test suite.
- The official Python import namespace is `pdf2ovr`. The previous
  `ovr_map_generator` namespace remains as a deprecated forwarding shim for
  supported APIs.
- MIT license metadata and a root `LICENSE` file are included in this
  pre-release preparation. The copyright-holder notice remains a placeholder
  pending rights verification; see `docs/LICENSE_REVIEW.md`.

### Breaking changes

- The obsolete `ovr_map_generator.ovr_map_from_pdf` module and its historical
  four-positional-argument PDF-to-OVR invocation are removed. That code path
  had already been fail-closed and did not perform general PDF-to-OVR
  conversion.
- New imports should use `pdf2ovr`. The legacy `ovr_map_generator` import
  namespace is deprecated and emits `DeprecationWarning`.
- `_filter_elements` is not part of the supported public API.

### Scope and limitations

- General PDF-to-OVR conversion is not enabled. PDF extraction writes an
  interchange package; it does not automatically create EANT annotations.
- OVR editing/creation is limited to selected, template-derived annotation
  profiles. New style/symbol libraries, groups, polygons/holes, arbitrary EANT
  types, unrestricted Unicode writing and `.map` composition editing are not
  implemented.
- PDF extraction and GeoPDF georeferencing/calibration are separate supported
  operations; neither automatically writes geospatial metadata or extracted
  PDF content into an OVR.
- Internal HFA/EANT re-parsing does not certify ERDAS IMAGINE compatibility.
  No ERDAS open/render/save round trip has been independently verified.
- The two externally supplied OVR examples are not redistributed. The test
  suite uses compact synthetic fixtures; these exercise selected parser/writer
  paths but are not complete ERDAS-produced files.

## 0.5.0 PDF extraction fidelity milestone

- Added a page-aware extraction result model and JSON interchange manifest.
- Added paint-order-aware text extraction with baselines, rotation, font/glyph,
  writing-mode, bidi, rendering-mode, color and opacity metadata.
- Added vector path command capture, adaptive cubic Bézier flattening,
  subpath metadata, stroke/fill styles, opacity, dashes, caps, joins and fill
  rules.
- Added optional-content group/configuration inventory, object layer labels,
  clipping-path and transparency-group metadata.
- Added page-aware image occurrences with affine placements, transformed
  quadrilaterals, soft-mask bytes and deduplicated assets.
- Added `pdf2ovr extract` to write `manifest.json` and assets without claiming
  complete PDF rendering or EANT serialization.
- Preserved the legacy `_join_segments` helper for compatibility during the
  extraction transition.

## 0.4.0 GeoPDF geospatial intelligence milestone

- Added page-level Adobe/ISO GeoPDF viewport inspection using `/VP`,
  `/Measure /GEO`, `/GPTS`, `/LPTS`, `/Bounds`, `/GCS` and projected `/PCSM`.
- Added page-level OGC PDF georegistration 2.2 inspection using `/LGIDict`,
  map-frame arrays, `/Registration`, `/CTM`, `/Projection`, `/Display` and
  `/Neatline`.
- Added explicit EPSG/WKT CRS resolution, affine page-to-map transformations,
  GCP calibration, residual/RMSE reporting and CRS reprojection.
- Added PDF user-space / extractor-space adapters for page rotation and
  CropBox, plus `georef`, `calibrate` and `transform` commands.
- Does not write geospatial metadata to EANT or edit MAP compositions.

## 0.3.0 HFA/EANT structural foundation milestone

- Added a bounded HFA dictionary parser and typed payload decoder/encoder for
  the types exercised by the supplied reference OVR examples.
- Added pointer-following EHFA traversal, dynamic entry-header-length handling,
  parent/sibling/data-range validation and free-list checks.
- Added semantic inspection for the observed `Element_Eant` and
  `Element_2_Eant` annotation families, including selected text, ellipse,
  rectangle, polyline, group, style, symbol and Map_Info records.
- Added an append-only HFA entry writer, conservative simple-annotation cloning,
  preservation checks, and `inspect`/`clone` CLI commands.
- Kept generic PDF-to-OVR generation blocked because the previous writer's
  annotation payload assumptions were inconsistent with the observed EANT
  parent/child structures.

## 0.2.0 safety and extraction baseline milestone

- Added a read-only pointer-following EHFA parser and structural summaries.
- Replaced heuristic entry scanning with root-pointer traversal and bounded
  validation of parent/child/sibling pointers, data blocks and free-list links.
- Improved PDF extraction safety for page identity, rotation, quadrilaterals,
  Bézier paths, closed shapes, text deduplication and draw order.
- Improved segment joining and scale-bar validation.
- Made unsafe/unsupported PDF-to-OVR writing fail closed rather than producing
  a misleading success message or mutating templates on empty extraction.
- Rejected MAP path truncation and multi-page PDF-to-map conversion where
  semantics were not established.
- Added the initial package metadata, CLI/test workflow and documentation.
