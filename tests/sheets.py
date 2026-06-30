"""Canonical test schema, mirroring BakingSheet.Tests/Utils/TestSheetContainer.cs.

Defined as a real module (not inline) so forward references resolve correctly.
Fields are renamed only where Python would shadow a typing name (``Dict`` ->
``DictCol``) — that is a Python-language constraint, not a library limitation.
"""
from __future__ import annotations

import datetime
import enum
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, List, Optional

from bakingsheet import (
    Reference,
    Sheet,
    SheetRow,
    SheetRowArray,
    SheetRowElem,
    VerticalList,
    non_serialized,
)
from bakingsheet.propertymap.converters import ValueConverter, ValueConvertingContext


class TestSheet(Sheet["TestSheet.Row"]):
    @dataclass
    class Row(SheetRow):
        Content: Optional[str] = None


class TestArraySheet(Sheet["TestArraySheet.Row"]):
    @dataclass
    class Elem(SheetRowElem):
        ElemContent: Optional[str] = None

    @dataclass
    class Row(SheetRowArray["TestArraySheet.Elem"]):
        Content: Optional[str] = None


class TestNestedSheet(Sheet["TestNestedSheet.Row"]):
    @dataclass
    class NestedStruct:
        XInt: int = 0
        YFloat: float = 0.0
        ZList: Optional[List[str]] = None

    @dataclass
    class Elem(SheetRowElem):
        IntList: Optional[List[int]] = None

    @dataclass
    class Row(SheetRowArray["TestNestedSheet.Elem"]):
        Struct: "TestNestedSheet.NestedStruct" = field(
            default_factory=lambda: TestNestedSheet.NestedStruct()
        )
        StructList: Optional[List["TestNestedSheet.NestedStruct"]] = None


class TestEnum(enum.Enum):
    Alpha = 0
    Bravo = 1
    Charlie = 2


class MyIntConverter(ValueConverter):
    """Port of C# ``MyIntConverter``: ``int.Parse(value) - 1``."""

    def can_convert(self, typ):
        return typ is int and getattr(self, "_active", True)

    def string_to_value(self, typ, value, ctx: ValueConvertingContext):
        return int(value) - 1

    def value_to_string(self, typ, value, ctx: ValueConvertingContext):
        return str(value + 1)


class TestTypeSheet(Sheet[TestEnum, "TestTypeSheet.Row"]):
    @dataclass
    class Row(SheetRow[TestEnum]):
        IntColumn: int = field(default=0, metadata={"converter": MyIntConverter()})
        FloatColumn: float = 0.0
        DecimalColumn: Decimal = Decimal("0")
        DateTimeColumn: datetime.datetime = field(
            default_factory=lambda: datetime.datetime(1, 1, 1)
        )
        TimeSpanColumn: datetime.timedelta = field(
            default_factory=lambda: datetime.timedelta()
        )
        EnumColumn: Optional[TestEnum] = None


class TestReferenceSheet(Sheet["TestReferenceSheet.Row"]):
    @dataclass
    class Elem(SheetRowElem):
        NestedReferColumn: Optional[Reference[str, "TestSheet.Row"]] = None

    @dataclass
    class Row(SheetRowArray["TestReferenceSheet.Elem"]):
        ReferColumn: Optional[Reference[str, "TestSheet.Row"]] = None
        SelfReferColumn: Optional[Reference[str, "TestReferenceSheet.Row"]] = None
        ReferList: Optional[List[Reference[str, "TestSheet.Row"]]] = None


class TestDictSheet(Sheet["TestDictSheet.Row"]):
    @dataclass
    class Elem(SheetRowElem):
        NestedDict: Optional[Dict[int, List[str]]] = None
        Value: int = 0

    @dataclass
    class Row(SheetRowArray["TestDictSheet.Elem"]):
        DictCol: Optional[Dict[str, float]] = None


class TestVerticalSheet(Sheet["TestVerticalSheet.Row"]):
    @dataclass
    class Elem(SheetRowElem):
        Value: Optional[str] = None

    @dataclass
    class NestedStruct:
        X: int = 0
        Y: int = 0

    @dataclass
    class Row(SheetRowArray["TestVerticalSheet.Elem"]):
        Coord: Optional[VerticalList["TestVerticalSheet.NestedStruct"]] = None
        Levels: Optional[List[VerticalList[int]]] = None


@dataclass
class InheritBaseRow(SheetRow):
    Value: int = 0


class InheritedSheet(Sheet["InheritedSheet.Row"]):
    @dataclass
    class Row(InheritBaseRow):
        pass


@dataclass
class TestSheetContainer:
    """Container mirroring ``TestSheetContainer`` (plain attributes, not a
    SheetContainerBase subclass) — used by the JSON export tests which build
    rows directly and call PostLoad via a real container.
    """

    Tests: Optional[TestSheet] = None
    Arrays: Optional[TestArraySheet] = None
    Types: Optional[TestTypeSheet] = None
    Refers: Optional[TestReferenceSheet] = None
    Nested: Optional[TestNestedSheet] = None
    Dict: Optional[TestDictSheet] = None
    Vertical: Optional[TestVerticalSheet] = None
    Inherited: Optional[InheritedSheet] = None
