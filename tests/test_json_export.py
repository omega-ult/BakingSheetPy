"""JSON export byte-compatibility tests.

Direct port of BakingSheet.Tests/Tests/JsonExportTests.cs. Asserts the exact
JSON string the C# Newtonsoft output produces, to guarantee byte-compatibility.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

import pytest

from bakingsheet import SheetContainerBase
from bakingsheet.converters import JsonSheetExporter
from bakingsheet.core.container import make_context

from .sheets import (
    TestDictSheet,
    TestNestedSheet,
    TestReferenceSheet,
    TestSheet,
    TestTypeSheet,
    TestEnum,
)


class _MemExporter(JsonSheetExporter):
    """Capture serialized output in memory instead of writing files."""

    def __init__(self):
        super().__init__(paths=["."])
        self.outputs: dict[str, str] = {}

    def export(self, context):
        from bakingsheet.converters import _json_contract

        resolver = getattr(context.container, "contract_resolver", None)
        for name in context.container.get_sheet_properties():
            sheet = context.container.find(name)
            if sheet is None:
                continue
            payload = _json_contract.serialize_sheet(sheet, resolver)
            self.outputs[sheet.name] = _json_contract.dumps(payload, indent=None)
        return True


@dataclass
class _Container(SheetContainerBase):
    Tests: Optional[TestSheet] = None
    Refers: Optional[TestReferenceSheet] = None
    Nested: Optional[TestNestedSheet] = None
    Dict: Optional[TestDictSheet] = None
    Types: Optional[TestTypeSheet] = None


def _container_with(**sheets):
    c = _Container()
    for k, v in sheets.items():
        setattr(c, k, v)
    return c


def test_export_reference_json():
    import datetime as _  # noqa
    from bakingsheet import Reference

    refers = TestReferenceSheet()
    refer_row = TestReferenceSheet.Row()
    refer_row.Id = "Refer"
    refer_row.ReferColumn = Reference[str, "TestSheet.Row"]("Test")
    refer_row.ReferList = [
        Reference[str, "TestSheet.Row"]("Test"),
        Reference[str, "TestSheet.Row"]("Test"),
    ]
    refers.add(refer_row)

    tests = TestSheet()
    test_row = TestSheet.Row()
    test_row.Id = "Test"
    tests.add(test_row)

    c = _container_with(Tests=tests, Refers=refers)
    c.post_load()
    assert not c.logger.has_error

    exp = _MemExporter()
    exp.export(make_context(c, c.logger))
    assert exp.outputs["Refers"] == (
        '[{"ReferColumn":"Test","SelfReferColumn":null,'
        '"ReferList":["Test","Test"],"Arr":[],"Id":"Refer"}]'
    )


def test_export_nested_json():
    from bakingsheet import Reference  # noqa

    nested = TestNestedSheet()

    row1 = TestNestedSheet.Row(Id="Row1", StructList=[])
    row2 = TestNestedSheet.Row(
        Id="Row2",
        Struct=TestNestedSheet.NestedStruct(XInt=10, YFloat=50.42, ZList=["x", "y"]),
        StructList=None,
    )
    row3 = TestNestedSheet.Row(
        Id="Row3",
        StructList=[
            TestNestedSheet.NestedStruct(XInt=1, YFloat=0.124, ZList=["a", "b"]),
            TestNestedSheet.NestedStruct(XInt=2, YFloat=20, ZList=["c"]),
        ],
    )
    row1.Arr.append(TestNestedSheet.Elem(IntList=[1, 2, 3]))
    row1.Arr.append(TestNestedSheet.Elem(IntList=[4, 5, 6, 7, 8]))
    row3.Arr.append(TestNestedSheet.Elem(IntList=None))
    nested.add(row1)
    nested.add(row2)
    nested.add(row3)

    c = _container_with(Nested=nested)
    c.post_load()
    assert not c.logger.has_error

    exp = _MemExporter()
    exp.export(make_context(c, c.logger))
    expected = (
        '[{"Struct":{"XInt":0,"YFloat":0.0,"ZList":null},'
        '"StructList":[],'
        '"Arr":[{"IntList":[1,2,3]},{"IntList":[4,5,6,7,8]}],"Id":"Row1"},'
        '{"Struct":{"XInt":10,"YFloat":50.42,"ZList":["x","y"]},'
        '"StructList":null,"Arr":[],"Id":"Row2"},'
        '{"Struct":{"XInt":0,"YFloat":0.0,"ZList":null},'
        '"StructList":[{"XInt":1,"YFloat":0.124,"ZList":["a","b"]},'
        '{"XInt":2,"YFloat":20.0,"ZList":["c"]}],'
        '"Arr":[{"IntList":null}],"Id":"Row3"}]'
    )
    assert exp.outputs["Nested"] == expected


def test_export_dict_json():
    from typing import Dict as TDict, List as TList

    sheet = TestDictSheet()
    row1 = TestDictSheet.Row(Id="Dict1", DictCol={"A": 10.0, "B": 20.0})
    row2 = TestDictSheet.Row(Id="Dict2", DictCol={"C": 10.0, "B": 20.0})
    row3 = TestDictSheet.Row(Id="Empty")
    row1.Arr.append(TestDictSheet.Elem(NestedDict={2034: ["X", "YYY", "ZZZZZ"]}))
    row3.Arr.append(TestDictSheet.Elem(Value=8))
    row3.Arr.append(TestDictSheet.Elem(Value=65))
    sheet.add(row1)
    sheet.add(row2)
    sheet.add(row3)

    c = _container_with(Dict=sheet)
    c.post_load()
    assert not c.logger.has_error

    exp = _MemExporter()
    exp.export(make_context(c, c.logger))
    expected = (
        '[{"DictCol":{"A":10.0,"B":20.0},'
        '"Arr":[{"NestedDict":{"2034":["X","YYY","ZZZZZ"]},"Value":0}],"Id":"Dict1"},'
        '{"DictCol":{"C":10.0,"B":20.0},"Arr":[],"Id":"Dict2"},'
        '{"DictCol":null,"Arr":[{"NestedDict":null,"Value":8},'
        '{"NestedDict":null,"Value":65}],"Id":"Empty"}]'
    )
    assert exp.outputs["Dict"] == expected


def test_export_types_json():
    import datetime

    sheet = TestTypeSheet()
    sheet.add(
        TestTypeSheet.Row(
            Id=TestEnum.Alpha,
            IntColumn=123,
            FloatColumn=5.13,
            DecimalColumn=Decimal("10.03"),
            EnumColumn=TestEnum.Charlie,
            DateTimeColumn=datetime.datetime(2020, 10, 3),
            TimeSpanColumn=datetime.timedelta(hours=2),
        )
    )
    sheet.add(
        TestTypeSheet.Row(
            Id=TestEnum.Bravo,
            IntColumn=-999,
            FloatColumn=-12.13,
            DecimalColumn=Decimal("-0.002"),
            EnumColumn=None,
            DateTimeColumn=datetime.datetime(1994, 5, 13),
            TimeSpanColumn=datetime.timedelta(seconds=15),
        )
    )

    c = _container_with(Types=sheet)
    c.post_load()
    assert not c.logger.has_error

    exp = _MemExporter()
    exp.export(make_context(c, c.logger))
    expected = (
        '[{"IntColumn":123,"FloatColumn":5.13,"DecimalColumn":10.03,'
        '"DateTimeColumn":"2020-10-03T00:00:00","TimeSpanColumn":"02:00:00",'
        '"EnumColumn":"Charlie","Id":"Alpha"},'
        '{"IntColumn":-999,"FloatColumn":-12.13,"DecimalColumn":-0.002,'
        '"DateTimeColumn":"1994-05-13T00:00:00","TimeSpanColumn":"00:00:15",'
        '"EnumColumn":null,"Id":"Bravo"}]'
    )
    assert exp.outputs["Types"] == expected
