"""CLI + config + multi-output end-to-end tests."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from bakingsheet import run
from bakingsheet.config import build_exporters, build_importer, load_config, load_container


CONFIG_A = """
schema:
  module: tests.sample_sheets
  container: GameContainer
import:
  source: csv
  path: {src}
outputs:
  - path: {out_a}
    pretty: true
    indent: 2
  - path: {out_b}
    pretty: false
options:
  timezone: UTC
"""


CONFIG_B = """
schema:
  module: tests.sample_sheets
  container: GameContainer
import:
  source: csv
  path: {src}
outputs:
  - path: {out_c}
    pretty: true
    indent: 2
    only: [Consumables]
options:
  timezone: Asia/Shanghai
"""


def _write_csv(path, content):
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(content)


def test_config_load_and_share_schema(tmp_path):
    src = tmp_path / "src"; src.mkdir()
    _write_csv(src / "Consumables.csv", "Id,Name,Price\nLVUP_001,Shield,100\nPOTION_001,Potion,30\n")
    cfg_a = tmp_path / "a.yaml"
    cfg_b = tmp_path / "b.yaml"
    cfg_a.write_text(
        CONFIG_A.format(src=str(src), out_a=str(tmp_path / "out_a"), out_b=str(tmp_path / "out_b")),
        encoding="utf-8",
    )
    cfg_b.write_text(
        CONFIG_B.format(src=str(src), out_c=str(tmp_path / "out_c")),
        encoding="utf-8",
    )

    # both configs load the same schema/container class
    ra = load_config(str(cfg_a))
    rb = load_config(str(cfg_b))
    ca = load_container(ra)
    cb = load_container(rb)
    assert type(ca) is type(cb)

    # run project A -> two output dirs
    rc = run(str(cfg_a))
    assert rc == 0
    a_json = (tmp_path / "out_a" / "Consumables.json").read_text(encoding="utf-8")
    b_json = (tmp_path / "out_b" / "Consumables.json").read_text(encoding="utf-8")
    assert "Shield" in a_json and "Potion" in b_json
    # pretty (A) has newlines, compact (B) does not
    assert "\n" in a_json
    assert "\n" not in b_json

    # run project B -> one dir, only Consumables
    rc2 = run(str(cfg_b))
    assert rc2 == 0
    c_json = (tmp_path / "out_c" / "Consumables.json").read_text(encoding="utf-8")
    assert "Shield" in c_json


def test_sheet_paths_override(tmp_path):
    """A sheet listed in sheet_paths is written only to the override dir."""
    src = tmp_path / "src"; src.mkdir()
    _write_csv(src / "Consumables.csv", "Id,Name,Price\nX,Item,5\n")
    from bakingsheet.converters import JsonSheetExporter
    from bakingsheet import SheetContainerBase
    from tests.sample_sheets import GameContainer

    c = GameContainer()
    imp = build_importer(load_config(_write_cfg(tmp_path, src)))
    # build importer directly via a minimal config
    from bakingsheet.converters.csv_converter import CsvSheetConverter

    c.bake(CsvSheetConverter(str(src)))

    global_dir = tmp_path / "global"
    only_dir = tmp_path / "only"
    exp = JsonSheetExporter(
        paths=[str(global_dir)],
        sheet_paths={"Consumables": [str(only_dir)]},
        indent=2,
    )
    assert c.store(exp)
    # Consumables goes only to the override dir, not the global one
    assert (only_dir / "Consumables.json").exists()
    assert not (global_dir / "Consumables.json").exists()


def _write_cfg(tmp_path, src):
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        f"schema:\n  module: tests.sample_sheets\n  container: GameContainer\n"
        f"import:\n  source: csv\n  path: {src}\noutputs:\n  - path: {tmp_path}/out\n",
        encoding="utf-8",
    )
    return str(cfg)


def test_cli_exit_code(tmp_path, capsys):
    src = tmp_path / "src"; src.mkdir()
    _write_csv(src / "Consumables.csv", "Id,Name,Price\nA,Apple,1\n")
    cfg = tmp_path / "a.yaml"
    cfg.write_text(
        CONFIG_A.format(src=str(src), out_a=str(tmp_path / "oa"), out_b=str(tmp_path / "ob")),
        encoding="utf-8",
    )
    from bakingsheet.cli import main

    rc = main(["convert", str(cfg)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Exported sheet 'Consumables'" in out
