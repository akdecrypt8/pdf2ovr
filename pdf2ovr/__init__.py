"""PDF extraction, GeoPDF georeferencing, and EHFA/EANT inspection/editing.

General PDF-to-OVR conversion is not enabled. See the README for supported
operations and the explicit compatibility limitations.
"""

from .hfa_structure import HFAEntry, HFAFileHeader, HFAFormatError, HFAReader
from .hfa_dictionary import HFADictionary, HFADictionaryError
from .hfa_writer import HFAEntrySpec, HFAWriteError, HFAWriter
from .eant_reader import EANTAnnotation, EANTDocument, EANTReadError
from .eant_writer import EANTCloneResult, EANTCreateResult, EANTWriteError, EANTWriter
from .geospatial import (
    AffineFit,
    AffineTransform,
    CalibrationError,
    GeoPDFDocument,
    GeoPDFError,
    GeoPDFPage,
    GroundControlPoint,
    PageGeoReference,
    extractor_to_pdf_user_space,
    fit_affine,
    load_affine_fit,
    pdf_user_to_extractor_space,
    reproject_point,
)
from .pdf_extractor import (
    PDFExtractor,
    PDFStyle,
    TextRunMetadata,
    ClipPathRecord,
    TransparencyGroupRecord,
    PDFLayerRecord,
    PDFLayerConfiguration,
    PDFPageMetadata,
    PDFImageAsset,
    PDFImagePlacement,
    ExtractedElement,
    PDFExtractionResult,
    ScaleBarBuilder,
    NorthArrowBuilder,
)

__version__ = "0.6.0"

__all__ = [
    "__version__",
    "PDFExtractor",
    "PDFStyle",
    "TextRunMetadata",
    "ClipPathRecord",
    "TransparencyGroupRecord",
    "PDFLayerRecord",
    "PDFLayerConfiguration",
    "PDFPageMetadata",
    "PDFImageAsset",
    "PDFImagePlacement",
    "ExtractedElement",
    "PDFExtractionResult",
    "ScaleBarBuilder",
    "NorthArrowBuilder",
    "HFAEntry",
    "HFAFileHeader",
    "HFAFormatError",
    "HFAReader",
    "HFADictionary",
    "HFADictionaryError",
    "HFAEntrySpec",
    "HFAWriteError",
    "HFAWriter",
    "EANTAnnotation",
    "EANTDocument",
    "EANTReadError",
    "EANTCloneResult",
    "EANTCreateResult",
    "EANTWriteError",
    "EANTWriter",
    "AffineFit",
    "AffineTransform",
    "CalibrationError",
    "GeoPDFDocument",
    "GeoPDFError",
    "GeoPDFPage",
    "GroundControlPoint",
    "PageGeoReference",
    "extractor_to_pdf_user_space",
    "fit_affine",
    "load_affine_fit",
    "pdf_user_to_extractor_space",
    "reproject_point",
]
