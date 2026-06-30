"""Reference resolution tests, ported from BakingSheet.Tests/Tests/ReferenceTests.cs."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pytest

from bakingsheet import Reference, Sheet, SheetContainerBase, SheetRow

from .sheets import TestReferenceSheet, TestSheet


class _RefTargetSheet(Sheet["_RefTargetSheet.Row"]):
    class Row(SheetRow):
        Name: Optional[str] = None


@dataclass
class _Container(SheetContainerBase):
    Tests: Optional[TestSheet] = None
    Refers: Optional[TestReferenceSheet] = None


def _make_container():
    tests = TestSheet()
    t = TestSheet.Row(); t.Id = "Test"; tests.add(t)
    refers = TestReferenceSheet()
    c = _Container(Tests=tests, Refers=refers)
    return c, refers


def test_reference_resolves():
    c, refers = _make_container()
    rr = TestReferenceSheet.Row()
    rr.Id = "Refer"
    rr.ReferColumn = Reference[str, "TestSheet.Row"]("Test")
    refers.add(rr)
    c.post_load()
    assert not c.logger.has_error
    assert c.Refers["Refer"].ReferColumn.is_valid()


def test_reference_missing_logs_error():
    c, refers = _make_container()
    rr = TestReferenceSheet.Row()
    rr.Id = "Refer"
    rr.ReferColumn = Reference[str, "TestSheet.Row"]("DoesNotExist")
    refers.add(rr)
    c.post_load()
    assert c.logger.has_error
    assert any("Failed to find reference" in e for e in c.logger.errors)
    # Id is preserved even when the reference is unresolved
    assert c.Refers["Refer"].ReferColumn.Id == "DoesNotExist"


def test_reference_list_resolves():
    c, refers = _make_container()
    rr = TestReferenceSheet.Row()
    rr.Id = "Refer"
    rr.ReferList = [
        Reference[str, "TestSheet.Row"]("Test"),
        Reference[str, "TestSheet.Row"]("Test"),
    ]
    refers.add(rr)
    c.post_load()
    assert not c.logger.has_error
    assert all(r.is_valid() for r in c.Refers["Refer"].ReferList)


def test_self_reference_resolves():
    c, refers = _make_container()
    rr = TestReferenceSheet.Row()
    rr.Id = "Refer"
    rr.SelfReferColumn = Reference[str, "TestReferenceSheet.Row"]("Refer")
    refers.add(rr)
    c.post_load()
    assert not c.logger.has_error
    assert c.Refers["Refer"].SelfReferColumn.is_valid()
