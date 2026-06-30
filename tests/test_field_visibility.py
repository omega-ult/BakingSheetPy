"""Field-visibility (target) filtering tests.

A field's metadata declares which targets may NOT see it (``exclude``) or the
only targets that may (``include``). An output directory declares its ``target``;
fields filtered out for that target are omitted from the JSON.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional

import pytest

from bakingsheet import (
    EXCLUDE_TARGETS,
    INCLUDE_TARGETS,
    Sheet,
    SheetContainerBase,
    SheetRow,
    visible_to,
)
from bakingsheet.converters import JsonSheetExporter
from bakingsheet.converters import _json_contract
from bakingsheet.core.container import make_context


class _TargetSheet(Sheet["_TargetSheet.Row"]):
    @dataclass
    class Row(SheetRow):
        Name: Optional[str] = None
        SecretServerOnly: Optional[str] = field(
            default=None, metadata={EXCLUDE_TARGETS: ["client"]}
        )
        SecretClientOnly: Optional[str] = field(
            default=None, metadata={INCLUDE_TARGETS: ["client"]}
        )
        Shared: int = 0


@dataclass
class _Container(SheetContainerBase):
    Target: Optional[_TargetSheet] = None


def _build():
    c = _Container()
    s = _TargetSheet()
    r = _TargetSheet.Row()
    r.Id = "R1"
    r.Name = "n"
    r.SecretServerOnly = "server-secret"
    r.SecretClientOnly = "client-secret"
    r.Shared = 7
    s.add(r)
    c.Target = s
    c.post_load()
    return c


def _export_to_mem(c, target):
    """Return {sheet_name: json_text} for a given target."""
    outputs = {}

    class _Mem(JsonSheetExporter):
        def __init__(self, t):
            super().__init__(paths=["."], target=t)

        def export(self, context):
            for name in context.container.get_sheet_properties():
                sheet = context.container.find(name)
                if sheet is None:
                    continue
                payload = _json_contract.serialize_sheet(
                    sheet, context.container.contract_resolver, target=self._target
                )
                outputs[sheet.name] = _json_contract.dumps(payload, indent=2)
            return True

    exp = _Mem(target)
    exp.export(make_context(c, c.logger))
    return outputs


def test_no_target_keeps_all_fields():
    c = _build()
    out = _export_to_mem(c, None)
    row = json.loads(out["Target"])[0]
    assert "SecretServerOnly" in row
    assert "SecretClientOnly" in row
    assert row["Shared"] == 7


def test_client_target_drops_server_only():
    c = _build()
    out = _export_to_mem(c, "client")
    row = json.loads(out["Target"])[0]
    # exclude: client -> SecretServerOnly dropped
    assert "SecretServerOnly" not in row
    # include: client -> SecretClientOnly kept
    assert row["SecretClientOnly"] == "client-secret"
    assert row["Shared"] == 7
    assert row["Name"] == "n"


def test_server_target_drops_client_only():
    c = _build()
    out = _export_to_mem(c, "server")
    row = json.loads(out["Target"])[0]
    # SecretServerOnly has no include restriction -> kept for server
    assert row["SecretServerOnly"] == "server-secret"
    # include: client -> SecretClientOnly NOT visible to server
    assert "SecretClientOnly" not in row
    assert row["Shared"] == 7


def test_two_dirs_different_targets_via_config(tmp_path):
    """A single config produces client and server JSON in separate dirs."""
    from bakingsheet import run

    src = tmp_path / "src"
    src.mkdir()
    (src / "Target.csv").write_text(
        "Id,Name,SecretServerOnly,SecretClientOnly,Shared\nR1,n,ss,cs,7\n",
        encoding="utf-8",
    )
    # write the schema into an importable module on sys.path
    import sys, textwrap

    mod_dir = tmp_path / "mod"
    mod_dir.mkdir()
    (mod_dir / "target_sheets.py").write_text(
        textwrap.dedent(
            """
            from dataclasses import dataclass, field
            from typing import Optional
            from bakingsheet import Sheet, SheetRow, SheetContainerBase, EXCLUDE_TARGETS, INCLUDE_TARGETS
            class _TargetSheet(Sheet["_TargetSheet.Row"]):
                @dataclass
                class Row(SheetRow):
                    Name: Optional[str] = None
                    SecretServerOnly: Optional[str] = field(default=None, metadata={EXCLUDE_TARGETS: ["client"]})
                    SecretClientOnly: Optional[str] = field(default=None, metadata={INCLUDE_TARGETS: ["client"]})
                    Shared: int = 0
            @dataclass
            class GameContainer(SheetContainerBase):
                Target: Optional[_TargetSheet] = None
            """
        ),
        encoding="utf-8",
    )
    sys.path.insert(0, str(mod_dir))

    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        f"""
schema:
  module: target_sheets
  container: GameContainer
import:
  source: csv
  path: {src}
outputs:
  - path: {tmp_path}/client
    target: client
  - path: {tmp_path}/server
    target: server
options:
  timezone: UTC
""",
        encoding="utf-8",
    )
    rc = run(str(cfg))
    assert rc == 0

    client_row = json.loads((tmp_path / "client" / "Target.json").read_text("utf-8"))[0]
    server_row = json.loads((tmp_path / "server" / "Target.json").read_text("utf-8"))[0]
    assert "SecretServerOnly" not in client_row
    assert client_row["SecretClientOnly"] == "cs"
    assert "SecretClientOnly" not in server_row
    assert server_row["SecretServerOnly"] == "ss"


def test_visible_to_helper():
    assert visible_to({}, "client") is True
    assert visible_to({EXCLUDE_TARGETS: ["client"]}, "client") is False
    assert visible_to({EXCLUDE_TARGETS: ["client"]}, "server") is True
    assert visible_to({INCLUDE_TARGETS: ["client"]}, "client") is True
    assert visible_to({INCLUDE_TARGETS: ["client"]}, "server") is False
    assert visible_to({INCLUDE_TARGETS: ["client"]}, None) is True  # no filtering
