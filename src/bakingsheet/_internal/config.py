"""Internal configuration constants and helpers.

Port of BakingSheet/Src/Internal/Config.cs.
"""
from __future__ import annotations

from typing import Optional, Tuple

# Columns whose name starts with this are treated as comments and ignored.
COMMENT = "$"
# Delimiter used to flatten a column path, e.g. "Monsters:1:Name".
INDEX_DELIMITER = ":"
# Delimiter used to split a SheetName.SubName, e.g. "Tests.001".
SHEET_NAME_DELIMITER = "."


def parse_sheet_name(name: str) -> Tuple[str, Optional[str]]:
    """Split ``SheetName.SubName`` format.

    Returns ``(name, sub_name)`` where ``sub_name`` is ``None`` when no
    delimiter is present. Mirrors ``Config.ParseSheetName``.
    """
    idx = name.find(SHEET_NAME_DELIMITER)
    if idx == -1:
        return (name, None)
    return (name[:idx], name[idx + 1:])


def parse_flatten_path(path: str) -> list[str]:
    """Split a flattened column path on the index delimiter.

    Port of ``PropertyMap.ParseFlattenPath``.
    """
    return path.split(INDEX_DELIMITER)
