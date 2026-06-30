"""BakingSheet — Python port.

Code-defined datasheet schema. Import from Excel/CSV/Google Sheet, export JSON
to one or more directories.
"""
from __future__ import annotations

from .core.schema import (
    NON_SERIALIZED,
    EXCLUDE_TARGETS,
    INCLUDE_TARGETS,
    Sheet,
    SheetRow,
    SheetRowArray,
    SheetRowElem,
    VerticalList,
    Reference,
    non_serialized,
    is_reference_type,
    is_vertical_list,
    visible_to,
)
from .core.container import SheetContainerBase, SheetConvertingContext


def load_config(path):
    """Load a YAML run config (schema source + outputs). See :mod:`bakingsheet.config`."""
    from .config import load_config as _lc
    return _lc(path)


def run(config_path, verify=False):
    """Run the full pipeline from a YAML config file. Returns exit code."""
    from .cli import run as _r
    return _r(config_path, verify=verify)


__all__ = [
    "Sheet",
    "SheetRow",
    "SheetRowArray",
    "SheetRowElem",
    "VerticalList",
    "Reference",
    "non_serialized",
    "NON_SERIALIZED",
    "EXCLUDE_TARGETS",
    "INCLUDE_TARGETS",
    "visible_to",
    "is_reference_type",
    "is_vertical_list",
    "SheetContainerBase",
    "SheetConvertingContext",
    "load_config",
    "run",
]

__version__ = "0.1.0"
