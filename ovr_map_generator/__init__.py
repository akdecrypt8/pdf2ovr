"""
OVR/MAP Generator from PDF

Generate .ovr (annotation overlay) and .map (map composition) files
from PDF content using the EHFA container format.

Main entry point: ovr_map_from_pdf.generate()
"""

from .ovr_map_from_pdf import (
    generate,
    extract_pdf_elements,
    PDFElement,
    OVRPatcher,
    MAPPatcher,
    _filter_elements,
)

__all__ = [
    "generate",
    "extract_pdf_elements",
    "PDFElement",
    "OVRPatcher",
    "MAPPatcher",
    "_filter_elements",
]