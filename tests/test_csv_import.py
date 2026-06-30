"""CSV import / round-trip tests, ported from BakingSheet.Tests/Tests/CsvImportTests.cs.

Uses a real temp directory (tmp_path) instead of C#'s TestFileSystem.
Note: the C# ``Dict`` field is renamed ``DictCol`` in our schema (Python cannot
shadow ``typing.Dict``), so CSV headers use ``DictCol:``.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

import pytest

from bakingsheet import SheetContainerBase
from bakingsheet.converters import CsvSheetConverter
from bakingsheet.converters import JsonSheetExporter

from .sheets import (
    InheritedSheet,
    TestDictSheet,
    TestNestedSheet,
    TestSheet,
    TestTypeSheet,
    TestVerticalSheet,
    TestEnum,
)


@dataclass
class _Container(SheetContainerBase):
    Tests: Optional[TestSheet] = None
    Types: Optional[TestTypeSheet] = None
    Nested: Optional[TestNestedSheet] = None
    Dict: Optional[TestDictSheet] = None
    Vertical: Optional[TestVerticalSheet] = None
    Inherited: Optional[InheritedSheet] = None


def _write(path, name, content):
    p = os.path.join(path, name)
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(content)
    return p


def _bake(data_dir):
    c = _Container()
    conv = CsvSheetConverter(data_dir)
    ok = c.bake(conv)
    return c, ok


def test_import_empty_csv(tmp_path):
    _write(str(tmp_path), "Tests.csv", "Id")
    c, ok = _bake(str(tmp_path))
    assert ok
    assert len(c.Tests) == 0
    assert c.Tests.name == "Tests"


def test_import_missing_column(tmp_path):
    _write(str(tmp_path), "Tests.csv", "")
    c, ok = _bake(str(tmp_path))
    assert ok
    assert len(c.Tests) == 0
    assert c.Tests.name == "Tests"
    assert any("must be named" in e for e in c.logger.errors)


def test_import_wrong_enum(tmp_path):
    _write(str(tmp_path), "Types.csv", "Id,IntColumn\nWrongEnum,1\nAlpha,2")
    c, ok = _bake(str(tmp_path))
    assert ok
    assert len(c.Types) == 1
    assert any("Failed to set id" in e for e in c.logger.errors)


def test_import_duplicated_row(tmp_path):
    _write(
        str(tmp_path),
        "Types.csv",
        "Id,IntColumn\nAlpha,2\nCharlie,3\nAlpha,4\nBravo,5",
    )
    c, ok = _bake(str(tmp_path))
    assert ok
    assert len(c.Types) == 3
    # MyIntConverter subtracts 1: Alpha had IntColumn=2 -> 1, Bravo=5 -> 4
    assert c.Types[TestEnum.Alpha].IntColumn == 1
    assert c.Types[TestEnum.Bravo].IntColumn == 4
    assert any("Already has row" in e for e in c.logger.errors)


def test_import_nested_csv(tmp_path):
    _write(
        str(tmp_path),
        "Nested.csv",
        "Id,Struct:XInt,Struct:YFloat,Struct:ZList:1,Struct:ZList:2,"
        "StructList:1:XInt,StructList:1:YFloat,StructList:1:ZList:1,StructList:1:ZList:2,"
        "StructList:2:XInt,StructList:2:YFloat,StructList:2:ZList:1,StructList:2:ZList:2,"
        "IntList:1,IntList:2,IntList:3,IntList:4,IntList:5\n"
        "Row1,0,0,,,,,,,,,,,1,2,3,,\n"
        ",,,,,,,,,,,,,4,5,6,7,8\n"
        "Row2,10,50.42,x,y,,,,,,,,\n"
        "Row3,0,0,,,1,0.124,a,b,2,20,c,,,,,,\n",
    )
    c, ok = _bake(str(tmp_path))
    assert not c.logger.has_error
    assert ok
    assert len(c.Nested) == 3
    assert len(c.Nested["Row1"].Arr) == 2
    assert len(c.Nested["Row1"][0].IntList) == 3
    assert len(c.Nested["Row3"].StructList) == 2
    assert c.Nested["Row2"].Struct.XInt == 10
    assert c.Nested["Row3"].StructList[0].XInt == 1
    assert c.Nested["Row3"].StructList[0].ZList[1] == "b"
    assert c.Nested["Row1"].StructList is None


def test_import_dict_csv(tmp_path):
    _write(
        str(tmp_path),
        "Dict.csv",
        "Id,DictCol:A,DictCol:B,DictCol:C,NestedDict:2034:1,NestedDict:2034:2,NestedDict:2034:3,Value\r\n"
        "Dict1,10,20,,X,YYY,ZZZZZ,0\r\n"
        "Dict2,,20,10\r\n"
        "Empty,,,,,,,8\r\n"
        ",,,,,,,65\r\n",
    )
    c, ok = _bake(str(tmp_path))
    assert not c.logger.has_error
    assert ok
    assert len(c.Dict) == 3
    assert len(c.Dict["Dict1"].Arr) == 1
    assert len(c.Dict["Dict2"].Arr) == 0
    assert len(c.Dict["Dict1"].DictCol) == 2
    assert c.Dict["Dict2"].DictCol["C"] == 10.0
    assert len(c.Dict["Dict1"][0].NestedDict[2034]) == 3
    assert c.Dict["Dict1"][0].NestedDict[2034][1] == "YYY"
    assert c.Dict["Empty"].DictCol is None


def test_import_dict_csv_split(tmp_path):
    _write(
        str(tmp_path),
        "Dict.csv",
        "Id,DictCol,,,NestedDict,,,Value\r\n"
        ",A,B,C,2034,,,,\r\n"
        ",,,,1,2,3,\r\n"
        "Dict1,10,20,,X,YYY,ZZZZZ,0\r\n"
        "Dict2,,20,10\r\n"
        "Empty,,,,,,,8\r\n"
        ",,,,,,,65\r\n",
    )
    c, ok = _bake(str(tmp_path))
    assert not c.logger.has_error
    assert ok
    assert len(c.Dict) == 3
    assert len(c.Dict["Dict1"].DictCol) == 2
    assert c.Dict["Dict2"].DictCol["C"] == 10.0
    assert len(c.Dict["Dict1"][0].NestedDict[2034]) == 3
    assert c.Dict["Empty"].DictCol is None


def test_import_vertical_csv(tmp_path):
    _write(
        str(tmp_path),
        "Vertical.csv",
        "Id,Coord:X,Coord:Y,Levels:1,Levels:2,Value\n"
        "Vertical1,1,2,1,4,\n"
        ",2,3,2,5\n"
        ",,,3\n"
        "Vertical2,1,2,,4,Elem1\n"
        ",,,,5,Elem2\n"
        ",,,,,Elem3\n",
    )
    c, ok = _bake(str(tmp_path))
    assert not c.logger.has_error
    assert len(c.Vertical) == 2
    assert len(c.Vertical["Vertical1"].Arr) == 0
    assert len(c.Vertical["Vertical1"].Coord) == 2
    assert c.Vertical["Vertical1"].Coord[0].X == 1
    assert c.Vertical["Vertical1"].Coord[1].Y == 3
    assert len(c.Vertical["Vertical2"].Coord) == 1
    assert len(c.Vertical["Vertical2"].Levels) == 2
    assert c.Vertical["Vertical2"].Levels[0] is None
    assert c.Vertical["Vertical2"].Levels[1][1] == 5
    assert c.Vertical["Vertical2"][2].Value == "Elem3"


def test_import_inherited(tmp_path):
    _write(str(tmp_path), "Inherited.csv", "Id,Value\nTest,10")
    c, ok = _bake(str(tmp_path))
    assert not c.logger.has_error
    assert len(c.Inherited) == 1
    assert c.Inherited["Test"].Value == 10


def test_import_partial_sheet(tmp_path):
    _write(str(tmp_path), "Tests.001.csv", "Id,Content\nFirst,FirstContent\nSecond,SecondContent")
    _write(str(tmp_path), "Tests.002.csv", "Id,Content")
    _write(str(tmp_path), "Tests.003.csv", "Id,Content\nThird,ThirdContent\nForth,ForthContent")
    _write(str(tmp_path), "Tests.csv", "Id,Content\nZero,ZeroContent")
    c, ok = _bake(str(tmp_path))
    assert not c.logger.has_error
    assert ok
    assert len(c.Tests) == 5
    assert list(c.Tests)[0].Id == "Zero"
    assert list(c.Tests)[1].Id == "First"
    assert list(c.Tests)[4].Content == "ForthContent"


def test_csv_round_trip(tmp_path):
    """Import a nested CSV, re-export to CSV, re-import, and assert equality."""
    src = (
        "Id,Struct:XInt,Struct:YFloat,Struct:ZList:1,Struct:ZList:2,"
        "StructList:1:XInt,StructList:1:YFloat,StructList:1:ZList:1,StructList:1:ZList:2,"
        "StructList:2:XInt,StructList:2:YFloat,StructList:2:ZList:1,StructList:2:ZList:2,"
        "IntList:1,IntList:2,IntList:3,IntList:4,IntList:5\n"
        "Row1,0,0,,,,,,,,,,,1,2,3,,\n"
        ",,,,,,,,,,,,,4,5,6,7,8\n"
        "Row2,10,50.42,x,y,,,,,,,,\n"
        "Row3,0,0,,,1,0.124,a,b,2,20,c,,,,,,\n"
    )
    _write(str(tmp_path), "Nested.csv", src)
    c1, ok = _bake(str(tmp_path))
    assert ok and not c1.logger.has_error

    # export to a second dir
    out_dir = str(tmp_path / "out")
    exp = CsvSheetConverter(out_dir)
    ctx_ok = c1.store(exp)
    assert ctx_ok

    # re-import from the exported dir
    c2 = _Container()
    ok2 = c2.bake(CsvSheetConverter(out_dir))
    assert ok2 and not c2.logger.has_error

    assert len(c2.Nested) == 3
    assert c2.Nested["Row2"].Struct.XInt == 10
    assert c2.Nested["Row2"].Struct.YFloat == 50.42
    assert c2.Nested["Row3"].StructList[0].ZList[1] == "b"
    assert c2.Nested["Row1"][1].IntList == [4, 5, 6, 7, 8]


def test_csv_to_json_multi_dir(tmp_path):
    """End-to-end: CSV -> bake -> JSON to two directories."""
    _write(
        str(tmp_path),
        "Tests.csv",
        "Id,Content\nLVUP_001,Warrior's Shield\nPOTION_001,Health Potion\n",
    )
    c, ok = _bake(str(tmp_path))
    assert ok and not c.logger.has_error

    dir_a = tmp_path / "json_a"
    dir_b = tmp_path / "json_b"
    exp = JsonSheetExporter(paths=[str(dir_a), str(dir_b)])
    assert c.store(exp)
    assert (dir_a / "Tests.json").exists()
    assert (dir_b / "Tests.json").exists()
    text = (dir_a / "Tests.json").read_text(encoding="utf-8")
    assert "Warrior's Shield" in text
    assert '"Id":"LVUP_001"' in text or '"Id": "LVUP_001"' in text
