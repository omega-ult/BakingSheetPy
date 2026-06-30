"""Excel import tests, mirroring the CSV nested/dict tests."""
from __future__ import annotations

import os

import pytest

openpyxl = pytest.importorskip("openpyxl")

from bakingsheet import SheetContainerBase
from bakingsheet.converters import ExcelSheetConverter

from .sheets import TestDictSheet, TestNestedSheet


def _container():
    from dataclasses import dataclass
    from typing import Optional

    @dataclass
    class C(SheetContainerBase):
        Nested: Optional[TestNestedSheet] = None
        Dict: Optional[TestDictSheet] = None

    return C()


def _write_nested_xlsx(path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Nested"
    header = [
        "Id", "Struct:XInt", "Struct:YFloat", "Struct:ZList:1", "Struct:ZList:2",
        "StructList:1:XInt", "StructList:1:YFloat", "StructList:1:ZList:1", "StructList:1:ZList:2",
        "StructList:2:XInt", "StructList:2:YFloat", "StructList:2:ZList:1", "StructList:2:ZList:2",
        "IntList:1", "IntList:2", "IntList:3", "IntList:4", "IntList:5",
    ]
    ws.append(header)
    ws.append(["Row1", 0, 0, None, None, None, None, None, None, None, None, None, None, 1, 2, 3, None, None])
    ws.append([None, None, None, None, None, None, None, None, None, None, None, None, None, 4, 5, 6, 7, 8])
    ws.append(["Row2", 10, 50.42, "x", "y", None, None, None, None, None, None, None, None, None, None, None, None, None])
    ws.append(["Row3", 0, 0, None, None, 1, 0.124, "a", "b", 2, 20, "c", None, None, None, None, None, None])
    wb.save(path)
    wb.close()


def test_excel_import_nested(tmp_path):
    path = tmp_path / "sheets.xlsx"
    _write_nested_xlsx(str(path))
    c = _container()
    conv = ExcelSheetConverter(str(tmp_path))
    ok = c.bake(conv)
    assert ok and not c.logger.has_error, f"errors={c.logger.errors}"
    assert len(c.Nested) == 3
    assert c.Nested["Row2"].Struct.XInt == 10
    assert c.Nested["Row3"].StructList[0].ZList[1] == "b"
    assert c.Nested["Row1"][1].IntList == [4, 5, 6, 7, 8]
    assert c.Nested["Row2"].Struct.XInt == 10
    assert c.Nested["Row3"].StructList[0].ZList[1] == "b"
    assert c.Nested["Row1"][1].IntList == [4, 5, 6, 7, 8]


def test_excel_partial_sheet(tmp_path):
    """Tabs named Sheet.001 / Sheet.002 merge into one sheet."""
    wb = openpyxl.Workbook()
    ws1 = wb.active; ws1.title = "Nested.001"
    ws1.append(["Id", "Struct:XInt"])
    ws1.append(["Row1", 5])
    ws2 = wb.create_sheet("Nested.002")
    ws2.append(["Id", "Struct:XInt"])
    ws2.append(["Row2", 9])
    path = tmp_path / "sheets.xlsx"
    wb.save(str(path)); wb.close()

    c = _container()
    conv = ExcelSheetConverter(str(tmp_path))
    ok = c.bake(conv)
    assert ok and not c.logger.has_error
    assert len(c.Nested) == 2
    assert c.Nested["Row1"].Struct.XInt == 5
    assert c.Nested["Row2"].Struct.XInt == 9
