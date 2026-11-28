"""Command-line interface for EHFA/EANT inspection and editing, PDF extraction, and GeoPDF tools."""
from __future__ import annotations

import argparse
import json
import math
import sys
import tempfile
import shutil
from pathlib import Path

from .eant_reader import EANTDocument
from .eant_writer import EANTWriteError, EANTWriter


def _inspect(args: argparse.Namespace) -> int:
    document = EANTDocument(args.ovr)
    summary = document.summary()
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(f"OVR: {args.ovr}")
        print(f"EHFA version: {summary['hfa']['version']}")
        print(f"Entry header length: {summary['hfa']['entry_header_length']}")
        print(f"Reachable entries: {summary['hfa']['entry_count']}")
        print(f"Dictionary types: {summary['dictionary_type_count']}")
        print(f"Top-level annotations: {summary['top_level_annotation_count']}")
        print(f"Recursive annotations: {summary['recursive_annotation_count']}")
        print(f"Annotation types: {summary['annotation_type_counts']}")
        print(f"Style records: {summary['style_entry_count']}")
        print(f"Symbol records: {summary['symbol_entry_count']}")
        print(f"Decode errors: {summary['decode_error_count']}")
        if summary["map_info"]:
            print(f"Map info: {summary['map_info']}")
    return 0 if summary["decode_error_count"] == 0 else 3


def _clone(args: argparse.Namespace) -> int:
    source = Path(args.ovr)
    if not source.is_file():
        raise FileNotFoundError(source)
    result = EANTWriter(source).clone_simple_annotation(
        args.entry_offset, new_name=args.name, new_id=args.new_id
    )
    destination = Path(args.output)
    if destination.resolve() == source.resolve():
        raise ValueError("Refusing to overwrite the source OVR")
    if destination.exists() and not args.force:
        raise FileExistsError(
            f"Destination already exists: {destination}. Pass --force to replace it."
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=f".{destination.name}.", suffix=".tmp",
            dir=destination.parent, delete=False
        ) as temporary:
            temp_path = Path(temporary.name)
            temporary.write(result.data)
            temporary.flush()
        checked = EANTDocument(temp_path)
        if checked.decode_errors:
            raise EANTWriteError("Generated OVR has typed payload decode errors")
        if destination.exists() and not args.force:
            raise FileExistsError(
                f"Destination appeared during validation: {destination}"
            )
        temp_path.replace(destination)
        temp_path = None
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
    print(f"Created: {destination}")
    print(f"New annotation entry: {result.new_entry_offset} (ID {result.new_element_id})")
    print("Note: geometry and styling were cloned from the source annotation; ERDAS compatibility is not certified.")
    return 0




def _add(args: argparse.Namespace) -> int:
    """Create one supported EANT annotation from a strict JSON specification."""
    source = Path(args.ovr)
    if not source.is_file():
        raise FileNotFoundError(source)
    spec_path = Path(args.spec)
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    if not isinstance(spec, dict):
        raise ValueError("Annotation specification must be a JSON object")
    allowed_top = {"kind", "template_entry_offset", "name", "description", "bbox", "geometry", "new_id"}
    unknown = set(spec) - allowed_top
    if unknown:
        raise ValueError(f"Unknown annotation specification keys: {', '.join(sorted(unknown))}")
    kind = spec.get("kind")
    if kind not in {"text", "rectangle", "polyline", "ellipse"}:
        raise ValueError("kind must be one of: text, rectangle, polyline, ellipse")
    if not isinstance(spec.get("geometry"), dict):
        raise ValueError("geometry must be a JSON object")
    geometry = spec["geometry"]
    allowed_geometry = {
        "text": {"text", "origin", "orientation", "scale", "horizontal_alignment", "vertical_alignment", "text_box_width"},
        "rectangle": {"center", "width", "height", "orientation"},
        "polyline": {"points"},
        "ellipse": {"center", "semi_major_axis", "semi_minor_axis", "orientation"},
    }[kind]
    unknown_geometry = set(geometry) - allowed_geometry
    if unknown_geometry:
        raise ValueError(f"Unknown {kind} geometry keys: {', '.join(sorted(unknown_geometry))}")
    required_geometry = {
        "text": {"text", "origin"},
        "rectangle": {"center", "width", "height"},
        "polyline": {"points"},
        "ellipse": {"center", "semi_major_axis", "semi_minor_axis"},
    }[kind]
    missing = required_geometry - set(geometry)
    if missing:
        raise ValueError(f"Missing {kind} geometry keys: {', '.join(sorted(missing))}")

    writer = EANTWriter(source)
    common = {
        "new_name": spec.get("name"),
        "description": spec.get("description"),
        "bbox": spec.get("bbox"),
        "new_id": spec.get("new_id"),
    }
    offset = spec.get("template_entry_offset")
    if isinstance(offset, bool) or not isinstance(offset, int):
        raise ValueError("template_entry_offset must be an integer entry offset from pdf2ovr inspect --json")
    method = getattr(writer, f"create_{kind}_annotation")
    result = method(offset, **common, **geometry)

    destination = Path(args.output)
    if destination.resolve() == source.resolve():
        raise ValueError("Refusing to overwrite the source OVR")
    if destination.exists() and not args.force:
        raise FileExistsError(
            f"Destination already exists: {destination}. Pass --force to replace it."
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=f".{destination.name}.", suffix=".tmp",
            dir=destination.parent, delete=False
        ) as temporary:
            temp_path = Path(temporary.name)
            temporary.write(result.data)
            temporary.flush()
        checked = EANTDocument(temp_path)
        if checked.decode_errors:
            raise EANTWriteError("Generated OVR has typed payload decode errors")
        if destination.exists() and not args.force:
            raise FileExistsError(f"Destination appeared during validation: {destination}")
        temp_path.replace(destination)
        temp_path = None
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)

    print(json.dumps({
        "created": str(destination),
        "entry_offset": result.new_entry_offset,
        "geometry_offset": result.new_geometry_offset,
        "element_id": result.new_element_id,
        "name": result.new_name,
        "element_schema": result.element_schema,
        "geometry_entry_type": result.geometry_entry_type,
        "validation": "HFA/EANT structural and typed-payload reparse passed; ERDAS application compatibility not certified",
    }, ensure_ascii=False, indent=2))
    return 0


def _extract(args: argparse.Namespace) -> int:
    """Export a fidelity-preserving interchange package, not an OVR file."""
    from .pdf_extractor import PDFExtractor

    source = Path(args.pdf)
    if not source.is_file():
        raise FileNotFoundError(source)
    destination = Path(args.output)
    if destination.exists():
        raise FileExistsError(f"Output directory already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent))
    try:
        extractor = PDFExtractor(
            str(source),
            curve_tolerance=args.curve_tolerance,
            include_images=not args.no_images,
        )
        try:
            result = extractor.extract_document()
        finally:
            extractor.close()

        payload = result.to_dict()
        assets_dir = temp_dir / "assets"
        if result.image_assets:
            assets_dir.mkdir(parents=True, exist_ok=True)
        asset_paths = {}
        for asset_id, asset in result.image_assets.items():
            safe_ext = "".join(ch for ch in asset.extension.lower() if ch.isalnum())[:8] or "bin"
            relative_path = f"assets/{asset_id}.{safe_ext}"
            (temp_dir / relative_path).write_bytes(asset.data)
            mask_relative = None
            if asset.soft_mask_data:
                mask_relative = f"assets/{asset_id}.smask.bin"
                (temp_dir / mask_relative).write_bytes(asset.soft_mask_data)
            asset_paths[asset_id] = {
                "path": relative_path,
                "soft_mask_path": mask_relative,
            }
        for asset_record in payload["image_assets"]:
            asset_record.update(asset_paths.get(asset_record["asset_id"], {}))

        payload["source"] = {
            "filename": source.name,
            "size_bytes": source.stat().st_size,
        }
        payload["coordinate_convention"] = {
            "element_geometry": "displayed-page bottom-left coordinates in PDF points",
            "text_baselines": "displayed-page bottom-left coordinates in PDF points",
            "image_placement": "displayed-page bottom-left coordinates; full source affine matrix retained",
            "note": "This is an interchange extraction package, not an ERDAS OVR or a claim of EANT serialization compatibility.",
        }
        manifest_path = temp_dir / "manifest.json"
        manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if destination.exists():
            raise FileExistsError(f"Output directory appeared during extraction: {destination}")
        temp_dir.replace(destination)
        temp_dir = None
    finally:
        if temp_dir is not None:
            shutil.rmtree(temp_dir, ignore_errors=True)

    print(f"Extraction package: {destination}")
    print(f"Pages: {len(result.pages)}")
    print(f"Text/vector elements: {len(result.elements)}")
    print(f"Image placements: {len(result.image_placements)} ({len(result.image_assets)} unique assets)")
    print(f"Clips: {len(result.clips)}; transparency groups: {len(result.groups)}")
    print(f"Optional-content groups: {len(result.layers)}")
    print(f"Warnings: {len(result.warnings)}")
    return 0

def _georef(args: argparse.Namespace) -> int:
    from .geospatial import GeoPDFDocument

    document = GeoPDFDocument(args.pdf, password=args.password)
    summary = document.summary()
    if args.page is not None:
        if args.page < 0 or args.page >= len(document.pages):
            raise ValueError(f"Page index {args.page} is outside the PDF")
        selected = document.pages[args.page]
        summary["pages"] = [selected.to_dict()]
        summary["georeferenced_page_count"] = int(bool(selected.viewports))
        summary["georeferenced_pages"] = [args.page] if selected.viewports else []
        summary["viewport_count"] = len(selected.viewports)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(f"PDF: {args.pdf}")
        print(f"Pages: {summary['page_count']}")
        print(f"Georeferenced pages: {summary['georeferenced_pages']}")
        print(f"Viewports/map frames: {summary['viewport_count']}")
        for page in summary["pages"]:
            print(f"  Page {page['page_index']}: rotation={page['rotation']}, viewports={page['viewport_count']}")
            for viewport in page["viewports"]:
                crs_label = viewport["crs"] or "unknown"
                if viewport["crs"] and not viewport["crs_resolved"]:
                    crs_label += " (not validated)"
                print(f"    {viewport['encoding']}: CRS={crs_label}, GCPs={len(viewport['control_points'])}, affine={'yes' if viewport['transform'] else 'no'}")
                for warning in viewport["warnings"]:
                    print(f"      WARNING: {warning}")
            for warning in page["warnings"]:
                print(f"    WARNING: {warning}")
    incomplete = any(not viewport.get("transform") for page in summary["pages"] for viewport in page["viewports"])
    return 4 if args.strict and (not summary["georeferenced_pages"] or incomplete) else 0


def _parse_gcp(raw: str):
    from .geospatial import GroundControlPoint
    parts = [part.strip() for part in raw.split(",")]
    if len(parts) != 4:
        raise ValueError(f"GCP must be pdf_x,pdf_y,map_x,map_y, got {raw!r}")
    values = [float(part) for part in parts]
    if not all(math.isfinite(value) for value in values):
        raise ValueError(f"GCP contains a non-finite value: {raw!r}")
    return GroundControlPoint(*values)


def _calibrate(args: argparse.Namespace) -> int:
    from .geospatial import fit_affine

    gcps = [_parse_gcp(value) for value in args.gcp]
    fit = fit_affine(gcps, target_crs=args.crs)
    payload = fit.to_dict()
    payload["control_points"] = [point.to_dict() for point in gcps]
    payload["schema_version"] = 1
    destination = Path(args.output)
    if destination.exists() and not args.force:
        raise FileExistsError(f"Destination already exists: {destination}; pass --force to replace it")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent, delete=False) as stream:
            temp_path = Path(stream.name)
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
        # Re-read and validate before atomically replacing the destination.
        checked = json.loads(temp_path.read_text(encoding="utf-8"))
        from .geospatial import AffineTransform
        AffineTransform.from_dict(checked["transform"])
        if destination.exists() and not args.force:
            raise FileExistsError(f"Destination appeared during calibration: {destination}")
        temp_path.replace(destination)
        temp_path = None
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
    print(f"Calibration written: {destination}")
    print(f"GCPs: {fit.point_count}; RMSE: {fit.rmse:.6g}; max residual: {fit.max_error:.6g}")
    return 0


def _transform(args: argparse.Namespace) -> int:
    from .geospatial import load_affine_fit, reproject_point

    fit = load_affine_fit(args.calibration)
    x, y = (float(value) for value in args.point.split(","))
    if not math.isfinite(x) or not math.isfinite(y):
        raise ValueError("Point coordinates must be finite")
    out_x, out_y = fit.transform.apply(x, y)
    if args.target_crs:
        if not fit.target_crs:
            raise ValueError("Calibration does not define a source CRS; cannot reproject")
        out_x, out_y = reproject_point(out_x, out_y, fit.target_crs, args.target_crs)
    print(json.dumps({"x": out_x, "y": out_y, "crs": args.target_crs or fit.target_crs}, indent=2))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pdf2ovr",
        description=(
            "Inspect and conservatively edit HFA/EANT OVR files; inspect GeoPDF "
            "georeferencing, calibrate page-to-map transforms, and reproject coordinates."
        ),
    )
    subparsers = parser.add_subparsers(dest="command")

    inspect = subparsers.add_parser("inspect", help="Inspect an OVR without modifying it")
    inspect.add_argument("ovr")
    inspect.add_argument("--json", action="store_true", help="Print machine-readable JSON")

    clone = subparsers.add_parser("clone", help="Clone a simple annotation into a new OVR file")
    clone.add_argument("ovr")
    clone.add_argument("--entry-offset", type=int, required=True)
    clone.add_argument("--name", required=True)
    clone.add_argument("--new-id", type=int)
    clone.add_argument("--output", required=True)
    clone.add_argument(
        "--force", action="store_true",
        help="Replace an existing destination file (never the source file)",
    )

    add = subparsers.add_parser(
        "add", help="Create a text, rectangle, polyline or classic ellipse annotation from an observed template"
    )
    add.add_argument("ovr", help="Existing OVR containing a compatible annotation template")
    add.add_argument("--spec", required=True, help="JSON annotation specification; see docs/EANT_WRITER.md")
    add.add_argument("--output", required=True, help="New output OVR path")
    add.add_argument("--force", action="store_true", help="Replace an existing destination (never the source)")

    extract = subparsers.add_parser("extract", help="Extract PDF text, vectors, styles, layers, clips and images into an interchange package")
    extract.add_argument("pdf")
    extract.add_argument("--output", required=True, help="New output directory (must not already exist)")
    extract.add_argument("--curve-tolerance", type=float, default=0.35, help="Bezier flattening tolerance in displayed PDF points")
    extract.add_argument("--no-images", action="store_true", help="Skip image payload extraction")

    georef = subparsers.add_parser("georef", help="Inspect embedded GeoPDF georeferencing")
    georef.add_argument("pdf")
    georef.add_argument("--page", type=int, help="Inspect one zero-based page index")
    georef.add_argument("--password", help="Password for an encrypted PDF")
    georef.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    georef.add_argument("--strict", action="store_true", help="Exit with code 4 if georeferencing or an affine transform is missing")

    calibrate = subparsers.add_parser("calibrate", help="Fit an affine page-to-map transform from ground control points")
    calibrate.add_argument("--gcp", action="append", required=True, metavar="PX,PY,MAPX,MAPY", help="PDF default user-space X/Y then map X/Y; repeat at least three times")
    calibrate.add_argument("--crs", help="Explicit CRS for the map coordinates, e.g. EPSG:32631")
    calibrate.add_argument("--output", required=True, help="Output calibration JSON")
    calibrate.add_argument("--force", action="store_true", help="Replace an existing calibration JSON")

    transform = subparsers.add_parser("transform", help="Apply a saved affine calibration to one PDF-space point")
    transform.add_argument("calibration")
    transform.add_argument("--point", required=True, metavar="X,Y", help="Point in PDF default user space")
    transform.add_argument("--target-crs", help="Optional output CRS; requires a source CRS in the calibration")
    return parser


def main(argv: list[str] | None = None) -> None:
    if argv is None:
        argv = sys.argv[1:]
    try:
        parser = _build_parser()
        args = parser.parse_args(argv)
        if args.command == "inspect":
            raise SystemExit(_inspect(args))
        if args.command == "clone":
            raise SystemExit(_clone(args))
        if args.command == "add":
            raise SystemExit(_add(args))
        if args.command == "extract":
            raise SystemExit(_extract(args))
        if args.command == "georef":
            raise SystemExit(_georef(args))
        if args.command == "calibrate":
            raise SystemExit(_calibrate(args))
        if args.command == "transform":
            raise SystemExit(_transform(args))
        parser.print_help()
    except SystemExit:
        raise
    except Exception as exc:
        print(f"pdf2ovr: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
