"""Deprecated compatibility namespace for :mod:`pdf2ovr`.

Migrate imports to ``pdf2ovr``. This shim only preserves APIs that remain
supported; the obsolete general PDF-to-OVR conversion API was removed.
"""
from __future__ import annotations

import importlib
import sys
import warnings

warnings.warn(
    "The 'ovr_map_generator' package is deprecated; import from 'pdf2ovr'. "
    "The obsolete general PDF-to-OVR conversion command is no longer available.",
    DeprecationWarning,
    stacklevel=2,
)

from pdf2ovr import *  # noqa: F401,F403
from pdf2ovr import __all__ as __all__

# Preserve common historical submodule imports without maintaining duplicate
# implementations. The removed ovr_map_from_pdf module is intentionally absent.
_COMPAT_MODULES = (
    "eant_reader",
    "eant_writer",
    "ehfa_engine",
    "element_types",
    "geospatial",
    "hfa_dictionary",
    "hfa_structure",
    "hfa_writer",
    "pdf_extractor",
)
for _module_name in _COMPAT_MODULES:
    _module = importlib.import_module(f"pdf2ovr.{_module_name}")
    sys.modules[f"{__name__}.{_module_name}"] = _module
    globals()[_module_name] = _module
