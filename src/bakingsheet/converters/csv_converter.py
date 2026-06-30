"""CSV sheet converter (import + export).

Port of BakingSheet.Converters.Csv/CsvSheetConverter.cs. Uses the stdlib
``csv`` module. Each sheet is one file ``{sheetname}.csv``; partial sheets use
``{sheetname}.{subname}.csv`` and merge by sub-name.
"""
from __future__ import annotations

import csv
import io
import os
from pathlib import Path
from typing import Any, List, Optional

from .._internal.config import parse_sheet_name
from ..raw.importer import RawSheetConverter, RawSheetExporterPage, RawSheetImporterPage


class CsvSheetConverter(RawSheetConverter):
    """Import/export CSV files. Port of ``CsvSheetConverter``."""

    def __init__(
        self,
        load_path: str,
        timezone=None,
        format_provider=None,
        split_header: bool = False,
        extension: str = "csv",
    ) -> None:
        super().__init__(timezone, format_provider, split_header)
        self._load_path = load_path
        self._extension = extension
        self._pages: "dict[str, list[RawSheetImporterPage]]" = {}

    # -- import ---------------------------------------------------------
    def load_data(self) -> bool:
        path = Path(self._load_path)
        if not path.exists():
            return True  # nothing to import; sheets will be empty
        for file in sorted(path.glob(f"*.{self._extension}")):
            self._load_file(file)
        return True

    def _load_file(self, file: Path) -> None:
        # read with utf-8; csv module handles quoting
        with open(file, "r", encoding="utf-8-sig", newline="") as f:
            rows = [row for row in csv.reader(f)]
        name = file.stem
        sheet_name, sub_name = parse_sheet_name(name)
        page = RawSheetImporterPage(rows, sub_name)
        self._pages.setdefault(sheet_name, []).append(page)

    def get_pages(self, sheet_name: str) -> List[RawSheetImporterPage]:
        return self._pages.get(sheet_name, [])

    # -- export ---------------------------------------------------------
    def create_page(self, sheet_name: str) -> RawSheetExporterPage:
        return RawSheetExporterPage(sheet_name)

    def save_data(self, pages: "list[tuple[str, RawSheetExporterPage]]") -> bool:  # type: ignore[override]
        os.makedirs(self._load_path, exist_ok=True)
        for name, page in pages:
            grid = page.to_grid()
            out_path = os.path.join(self._load_path, f"{name}.{self._extension}")
            with open(out_path, "w", encoding="utf-8", newline="") as f:
                writer = csv.writer(f)
                for row in grid:
                    writer.writerow([cell if cell is not None else "" for cell in row])
        return True
