"""Raw cell-grid importer / exporter engine.

Port of BakingSheet/Src/Raw/RawSheetImporter.cs, RawSheetConverter.cs,
RawSheetImporterPage.cs, RawSheetExporterPage.cs.

A ``RawSheetImporter`` is fed pages of cells (2D string grids) from a concrete
source (CSV, Excel, Google). It walks each page, parses flat/split headers,
enforces the ``Id``-first column rule, handles ``$`` comment columns and
vertical (RowArray) rows, and dispatches each cell to ``PropertyMap.set_value``.
"""
from __future__ import annotations

import datetime
from typing import Any, Iterator, List, Optional, Tuple

from .._internal.config import COMMENT, INDEX_DELIMITER
from ..core.schema import SheetRow, SheetRowArray


class RawSheetImporterPage:
    """A single page (tab/file) of cells for importing.

    Port of ``IRawSheetImporterPage`` + the extension helpers.
    """

    def __init__(self, cells: "list[list[Optional[str]]]", sub_name: Optional[str] = None) -> None:
        # cells[row][col]; pad ragged rows on demand
        self._cells = cells if cells else []
        self.sub_name = sub_name

    def get_cell(self, col: int, row: int) -> Optional[str]:
        if row < 0 or row >= len(self._cells):
            return None
        row_cells = self._cells[row]
        if col < 0 or col >= len(row_cells):
            return None
        return row_cells[col]

    def is_empty_cell(self, col: int, row: int) -> bool:
        val = self.get_cell(col, row)
        return val is None or val == ""

    def is_valid_column(self, col: int, row: int) -> bool:
        """Port of ``IsValidColumn``: a column is valid if any cell up to ``row``
        in it is non-empty."""
        for prev_row in range(row + 1):
            if not self.is_empty_cell(col, prev_row):
                return True
        return False

    def is_empty_row(self, row: int) -> bool:
        """Port of ``IsEmptyRow``: a row with no value in any valid column."""
        col = 0
        while self.is_valid_column(col, row):
            if not self.is_empty_cell(col, row):
                return False
            col += 1
        return True


class ISheetFormatter:
    """Minimal formatter interface (timezone + format provider).

    Port of ``ISheetFormatter``. Concrete importers implement this to expose
    ``timezone`` and ``format_provider``.
    """

    timezone: datetime.tzinfo = datetime.timezone.utc
    format_provider: Any = None


class RawSheetImporter(ISheetFormatter):
    """Generic importer for cell-based sources. Port of ``RawSheetImporter``."""

    def __init__(
        self,
        timezone: Optional[datetime.tzinfo] = None,
        format_provider: Any = None,
    ) -> None:
        self.timezone = timezone or datetime.timezone.utc
        self.format_provider = format_provider
        self._loaded = False

    # -- to override ----------------------------------------------------
    def load_data(self) -> bool:
        raise NotImplementedError

    def get_pages(self, sheet_name: str) -> List[RawSheetImporterPage]:
        raise NotImplementedError

    # -- import flow ----------------------------------------------------
    def reset(self) -> None:
        self._loaded = False

    def import_(self, context: Any) -> bool:
        if not self._loaded:
            success = self.load_data()
            if not success:
                context.logger.error("Failed to load data")
                return False
            self._loaded = True

        for name in context.container.get_sheet_properties():
            with context.logger.begin_scope(name):
                pages = self.get_pages(name)
                sheet = context.container.find(name)
                if sheet is None:
                    # lazily create the sheet from the field type
                    sheet = self._create_sheet(context, name)
                if sheet is None:
                    context.logger.error(f"Failed to create sheet of type for {name}")
                    continue
                # order pages by sub_name (None sorts first), matching C# OrderBy(SubName)
                for page in sorted(pages, key=lambda p: p.sub_name or ""):
                    self._import_page(page, context, sheet)
        return True

    def _create_sheet(self, context: Any, name: str) -> Any:
        import dataclasses
        import typing

        from ..core.container import _type_hints
        from ..core.schema import Sheet

        hints = _type_hints(type(context.container))
        attr_type = hints.get(name)
        if attr_type is None:
            return None
        # unwrap Optional[Sheet]
        origin = typing.get_origin(attr_type)
        args = typing.get_args(attr_type)
        if origin is typing.Union and args:
            for a in args:
                if isinstance(a, type) and issubclass(a, Sheet):
                    attr_type = a
                    break
        if isinstance(attr_type, type) and issubclass(attr_type, Sheet):
            sheet = attr_type()
            setattr(context.container, name, sheet)
            return sheet
        return None

    def _import_page(self, page: RawSheetImporterPage, context: Any, sheet: Any) -> None:
        """Port of ``ImportPage``."""
        id_column_name = page.get_cell(0, 0)
        if id_column_name != "Id":
            context.logger.error(f'First column "{id_column_name}" must be named "Id"')
            return

        column_names: List[str] = []
        header_rows: List[Optional[str]] = [None]  # first row is always a header row

        # if id column is empty in rows 1..N, they are split header rows
        page_row = 1
        while page.is_empty_cell(0, page_row) and not page.is_empty_row(page_row):
            header_rows.append(None)
            page_row += 1

        # build column names by scanning columns top-down across header rows
        page_column = 0
        while True:
            last_valid_row = -1
            for hr in range(len(header_rows)):
                if not page.is_empty_cell(page_column, hr):
                    last_valid_row = hr
                    header_rows[hr] = page.get_cell(page_column, hr)
            if last_valid_row == -1:
                break
            column_names.append(INDEX_DELIMITER.join(header_rows[: last_valid_row + 1]))
            page_column += 1

        property_map = sheet.get_property_map(context)

        sheet_row: Optional[Any] = None
        row_id: Optional[str] = None
        vindex = 0

        page_row = len(header_rows)
        while not page.is_empty_row(page_row):
            id_cell_value = page.get_cell(0, page_row)

            if id_cell_value:  # non-empty
                if id_cell_value.startswith(COMMENT):
                    page_row += 1
                    continue
                row_id = id_cell_value
                sheet_row = sheet.row_type() if isinstance(sheet.row_type, type) else None
                vindex = 0

            if sheet_row is None:
                page_row += 1
                continue

            with context.logger.begin_scope(row_id):
                try:
                    self._import_row(page, context, sheet_row, property_map, column_names, vindex, page_row)
                except Exception as ex:
                    # failed to convert; skip this row
                    sheet_row = None
                    page_row += 1
                    continue

                if vindex == 0:
                    if sheet.contains(sheet_row.Id):
                        context.logger.error(f'Already has row with id "{sheet_row.Id}"')
                    else:
                        sheet.add(sheet_row)

                vindex += 1
            page_row += 1

    def _import_row(
        self,
        page: RawSheetImporterPage,
        context: Any,
        sheet_row: Any,
        property_map: Any,
        column_names: List[str],
        vindex: int,
        page_row: int,
    ) -> None:
        """Port of ``ImportRow``."""
        for page_column in range(len(column_names)):
            column_value = column_names[page_column]
            if column_value.startswith(COMMENT):
                continue
            with context.logger.begin_scope(column_value):
                cell_value = page.get_cell(page_column, page_row)
                # empty cell -> value not set (property keeps default)
                if cell_value is None or cell_value == "":
                    continue
                try:
                    property_map.set_value(sheet_row, vindex, column_value, cell_value, self)
                except Exception as ex:
                    if page_column == 0:
                        # Id column failure: log and re-raise to drop the row
                        context.logger.error(f'Failed to set id "{cell_value}"', exc=ex)
                        raise
                    context.logger.error(f'Failed to set value "{cell_value}"', exc=ex)


class RawSheetExporterPage:
    """A single page for exporting: a growable 2D grid. Port of ``IRawSheetExporterPage``."""

    def __init__(self, sheet_name: str) -> None:
        self.name = sheet_name
        self._cells: "dict[tuple[int, int], str]" = {}
        self._max_col = -1
        self._max_row = -1

    def set_cell(self, col: int, row: int, data: Optional[str]) -> None:
        if data is None:
            return
        self._cells[(col, row)] = data
        self._max_col = max(self._max_col, col)
        self._max_row = max(self._max_row, row)

    def to_grid(self) -> "list[list[Optional[str]]]":
        """Return the page as a 2D grid (rows x cols), empty cells as ``""``."""
        grid: "list[list[Optional[str]]]" = []
        for r in range(self._max_row + 1):
            row: "list[Optional[str]]" = []
            for c in range(self._max_col + 1):
                row.append(self._cells.get((c, r), ""))
            grid.append(row)
        return grid


class RawSheetConverter(RawSheetImporter):
    """Generic cell-based converter supporting both import and export.

    Port of ``RawSheetConverter``.
    """

    def __init__(
        self,
        timezone: Optional[datetime.tzinfo] = None,
        format_provider: Any = None,
        split_header: bool = False,
    ) -> None:
        super().__init__(timezone, format_provider)
        self.split_header = split_header

    # -- to override ----------------------------------------------------
    def save_data(self) -> bool:
        raise NotImplementedError

    def create_page(self, sheet_name: str) -> RawSheetExporterPage:
        raise NotImplementedError

    # -- export flow ----------------------------------------------------
    def export(self, context: Any) -> bool:
        pages: "list[tuple[str, RawSheetExporterPage]]" = []
        for name in context.container.get_sheet_properties():
            with context.logger.begin_scope(name):
                sheet = context.container.find(name)
                if sheet is None:
                    continue
                page = self.create_page(sheet.name)
                self._export_page(page, context, sheet)
                pages.append((sheet.name, page))

        success = self.save_data(pages)
        if not success:
            context.logger.error("Failed to save data")
            return False
        return True

    def save_data(self, pages: "list[tuple[str, RawSheetExporterPage]]") -> bool:  # type: ignore[override]
        raise NotImplementedError

    def _export_page(self, page: RawSheetExporterPage, context: Any, sheet: Any) -> None:
        """Port of ``ExportPage``."""
        from ..propertymap.converters import ValueConvertingContext

        property_map = sheet.get_property_map(context)
        resolver = context.container.contract_resolver
        property_map.update_index(sheet)

        leafs = list(property_map.traverse_leaf())
        page_column = 0
        value_context = ValueConvertingContext(self, resolver)

        header_rows: List[Optional[str]] = []
        arguments: List[Any] = [None] * property_map.max_depth

        for node, indexes in leafs:
            i = 0
            for index in indexes:
                arg = value_context.value_to_string(type(index), index)
                arguments[i] = arg
                i += 1
            column_name = node.full_path
            if column_name and "{" in column_name:
                # format placeholders {0},{1},... with collected arguments
                try:
                    column_name = column_name.format(*arguments)
                except (IndexError, KeyError):
                    pass

            if self.split_header:
                temp_row = 0
                for path in column_name.split(INDEX_DELIMITER) if column_name else []:
                    while len(header_rows) <= temp_row:
                        header_rows.append(None)
                    if header_rows[temp_row] != path:
                        header_rows[temp_row] = path
                        page.set_cell(page_column, temp_row, path)
                    temp_row += 1
            else:
                page.set_cell(page_column, 0, column_name)
            page_column += 1

        page_row = len(header_rows) if self.split_header else 1
        for sheet_row in sheet:
            max_vertical_count = 1
            page_column = 0
            for node, indexes in leafs:
                vertical_count = node.get_vertical_count(sheet_row, iter(list(indexes)))
                for vindex in range(vertical_count):
                    value = node.get_value(sheet_row, vindex, iter(list(indexes)))
                    value_string = None
                    if value is not None:
                        conv = node.value_converter
                        if conv is not None:
                            value_string = conv.value_to_string(node.value_type, value, value_context)
                        else:
                            value_string = str(value)
                    page.set_cell(page_column, page_row + vindex, value_string)
                if max_vertical_count < vertical_count:
                    max_vertical_count = vertical_count
                page_column += 1
            page_row += max_vertical_count
