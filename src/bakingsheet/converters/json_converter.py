"""JSON sheet exporter (multiple directories).

Port of BakingSheet.Converters.Json/JsonSheetConverter.cs, extended to support
exporting to one or more directories in a single run, with optional per-sheet
output overrides (``sheet_paths``).

Each sheet -> ``{SheetName}.json``, a JSON array of row objects serialized via
the contract layer (C#-byte-compatible).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Iterable, Optional, Union

from . import _json_contract


class JsonSheetExporter:
    """Export a container's sheets to JSON in one or more directories.

    Args:
        paths: one or more output directories (global).
        sheet_paths: optional ``{sheet_name: [paths]}`` that *replaces* the
            global paths for that sheet (mirrors C# ``sheet_paths`` semantics).
        indent: ``None`` for compact (default, C#-compatible), or an int for
            pretty-printing.
        target: optional visibility target (e.g. ``"client"`` / ``"server"``).
            Fields whose metadata excludes this target are dropped from the
            output. ``None`` disables filtering (all fields).
        sheet_targets: optional ``{sheet_name: target}`` overriding the global
            ``target`` per sheet (e.g. to mark a server-only sheet).
        only: optional allow-list of sheet names for this output.
        exclude: optional deny-list of sheet names for this output. ``exclude``
            is applied after ``only`` when both are present.
    """

    def __init__(
        self,
        paths: Union[str, os.PathLike, "Iterable[Union[str, os.PathLike]]"],
        sheet_paths: Optional["dict[str, list[Union[str, os.PathLike]]]"] = None,
        indent: Optional[int] = None,
        ensure_ascii: bool = False,
        target: Optional[str] = None,
        sheet_targets: Optional["dict[str, str]"] = None,
        only: Optional["Iterable[str]"] = None,
        exclude: Optional["Iterable[str]"] = None,
    ) -> None:
        if isinstance(paths, (str, os.PathLike)):
            self._paths = [Path(paths)]
        else:
            self._paths = [Path(p) for p in paths]
        self._sheet_paths = {
            k: [Path(p) for p in v] for k, v in (sheet_paths or {}).items()
        }
        self._indent = indent
        self._ensure_ascii = ensure_ascii
        self._target = target
        self._sheet_targets = dict(sheet_targets or {})
        self._only = set(only) if only is not None else None
        self._exclude = set(exclude or ())

    def export(self, context: Any) -> bool:
        resolver = getattr(context.container, "contract_resolver", None)
        for name in context.container.get_sheet_properties():
            if self._only is not None and name not in self._only:
                self._remove_stale_sheet(name)
                continue
            if name in self._exclude:
                self._remove_stale_sheet(name)
                continue
            with context.logger.begin_scope(name):
                sheet = context.container.find(name)
                if sheet is None:
                    continue
                # sheet_paths overrides (replaces) the global paths
                dirs = self._sheet_paths.get(name, self._paths)
                target = self._sheet_targets.get(name, self._target)
                payload = _json_contract.serialize_sheet(sheet, resolver, target=target)
                text = _json_contract.dumps(payload, indent=self._indent)
                if self._ensure_ascii:
                    text = _ascii_escape(text)
                for d in dirs:
                    d.mkdir(parents=True, exist_ok=True)
                    out_path = d / f"{sheet.name}.json"
                    # newline="" keeps \n as-is on Windows (no CRLF translation),
                    # so baked JSON stays byte-identical across platforms.
                    with out_path.open("w", encoding="utf-8", newline="") as f:
                        f.write(text)
        return True

    def _remove_stale_sheet(self, name: str) -> None:
        """Remove an older generated file when a sheet is no longer selected."""
        dirs = self._sheet_paths.get(name, self._paths)
        for directory in dirs:
            path = directory / f"{name}.json"
            if path.exists():
                path.unlink()


def _ascii_escape(text: str) -> str:
    """Re-encode non-ASCII chars as ``\\uXXXX`` if ``ensure_ascii`` is set."""
    out = []
    for ch in text:
        if ord(ch) > 127:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return "".join(out)
