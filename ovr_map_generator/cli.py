"""Deprecated module wrapper for the supported :mod:`pdf2ovr` CLI."""
from __future__ import annotations

import warnings

warnings.warn(
    "'ovr_map_generator.cli' is deprecated; use 'pdf2ovr.cli' instead.",
    DeprecationWarning,
    stacklevel=2,
)

from pdf2ovr.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
