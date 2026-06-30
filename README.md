# bakingsheet (Python port)

Python port of [BakingSheet](https://github.com/cathei/BakingSheet): a
**datasheet ORM**. Define your sheet schema in code (dataclasses), import from
Excel/CSV/Google Sheet, validate cross-sheet references, and export JSON to one
or more directories. Field schema and input/output configuration are
**separated** — the same schema code serves different projects via different
YAML config files.

## Install

```bash
pip install -e .            # core: CSV, Excel, JSON
pip install -e ".[google]"  # + Google Sheet import
pip install -e ".[dev]"     # + pytest
```

## Define your schema (code)

```python
# my_game/sheets.py
from dataclasses import dataclass
from typing import Optional, List
from bakingsheet import Sheet, SheetRow, SheetRowArray, SheetRowElem, Reference, SheetContainerBase

class ConsumableSheet(Sheet["ConsumableSheet.Row"]):
    @dataclass
    class Row(SheetRow):
        Name: Optional[str] = None
        Price: int = 0

class HeroSheet(Sheet["HeroSheet.Row"]):
    @dataclass
    class Elem(SheetRowElem):
        Multiplier: float = 1.0
        Required: Optional[Reference[str, "ConsumableSheet.Row"]] = None
    @dataclass
    class Row(SheetRowArray["HeroSheet.Elem"]):
        Name: Optional[str] = None

@dataclass
class GameContainer(SheetContainerBase):
    Consumables: Optional[ConsumableSheet] = None
    Heroes: Optional[HeroSheet] = None
```

Supported column types: `str`, `int`/`float`/`bool`/`Decimal`, `enum`, `datetime`
/`timedelta`, `Optional[T]`, `List[T]`, `Dict[K,V]`, nested dataclasses,
`VerticalList[T]` (vertical), and cross-sheet `Reference[K, Row]`.

## Separate config (YAML)

```yaml
# game_a.yaml
schema: { module: my_game.sheets, container: GameContainer }
import: { source: excel, path: ./projects/game_a/data }
options: { timezone: Asia/Shanghai }
outputs:
  - { path: ./projects/game_a/build/json, pretty: true, indent: 2 }
  - { path: ./projects/game_a/build/json-min, pretty: false }
```

```yaml
# game_b.yaml — same schema, CSV source, sheet-specific output dir
schema: { module: my_game.sheets, container: GameContainer }
import: { source: csv, path: ./projects/game_b/data }
outputs:
  - { path: ./projects/game_b/build/json, pretty: true }
```

## Run

```bash
bakingsheet convert game_a.yaml
# or
python -m bakingsheet convert game_b.yaml
```

Programmatic API:

```python
from bakingsheet import run
run("game_a.yaml")

# or step by step
from bakingsheet import load_config
from bakingsheet.config import load_container, build_importer, build_exporters
cfg = load_config("game_a.yaml")
c = load_container(cfg)
c.bake(build_importer(cfg))
c.store(build_exporters(cfg))   # writes JSON to every output directory
```

## Multiple output directories

`JsonSheetExporter` accepts a list of paths (global) plus an optional
`sheet_paths` map that **replaces** the global paths for a given sheet:

```python
JsonSheetExporter(
    paths=["./build/json", "./build/json_mirror"],
    sheet_paths={"Heroes": ["./build/heroes_only"]},
)
```

## Field visibility (client vs. server)

Some fields must not ship to clients (e.g. cheat flags, server-only state);
others are client-only. Mark a field's **metadata** in the schema, and give
each output directory a **target**. Fields filtered out for that target are
omitted from the JSON.

```python
from bakingsheet import EXCLUDE_TARGETS, INCLUDE_TARGETS, field

@dataclass
class Row(SheetRow):
    Name: Optional[str] = None
    # never ship to clients:
    ServerCheatFlag: Optional[str] = field(default=None, metadata={EXCLUDE_TARGETS: ["client"]})
    # only for clients:
    ClientDisplayHint: Optional[str] = field(default=None, metadata={INCLUDE_TARGETS: ["client"]})
    Shared: int = 0
```

- `EXCLUDE_TARGETS: ["client"]` — the field is dropped from any output whose
  `target` is `client`.
- `INCLUDE_TARGETS: ["client"]` — the field appears **only** for outputs whose
  `target` is `client`.
- A field with no `include`/`exclude` metadata is visible to all targets.

In the YAML config, each output declares its `target`:

```yaml
outputs:
  - path: ./build/client_json
    target: client
  - path: ./build/server_json
    target: server
```

One bake → client JSON (no `ServerCheatFlag`) and server JSON (no
`ClientDisplayHint`) in separate directories. Per-sheet target overrides are
also supported via `sheet_targets: {SheetName: server}`.

## CSV / Excel layout

- First column must be named `Id`. Columns starting with `$` are comments.
- Flat headers: `Monsters:1`, `Dict:A`, `Struct:XInt`.
- Split headers: a `Dict` cell over an `A` cell (Id column empty in header rows).
- Vertical rows: a row with an empty `Id` continues the previous row's `Arr`
  (for `SheetRowArray`).
- Partial sheets: files/tabs `Tests.001.csv`, `Tests.002.csv`, `Tests.csv`
  merge into one sheet (bare name sorts first).

## JSON output (C#-compatible)

JSON is byte-compatible with the original C# Newtonsoft output:
- Each sheet → `{SheetName}.json`, a JSON array of row objects.
- `Id` is emitted **last** in each row.
- `enum` → name string; `DateTime` → `2020-10-03T00:00:00`;
  `TimeSpan` → `02:00:00`; `Reference` → its Id; `int` dict keys → strings.
- Compact by default (no whitespace). `pretty: true` enables indentation.

## Tests

```bash
pytest
```

Mirrors the C# test suite: JSON byte-comparison (`JsonExportTests`), CSV
import + round-trip (`CsvImportTests`), references (`ReferenceTests`), Excel
import, and CLI/config end-to-end.

## What's dropped vs. the C# original

Unity ScriptableObject / AssetPath / Runtime loading API, `async` (synchronous
here), `IFileSystem` (real temp files in tests). Google Sheet is import-only
(optional extra), matching the C# converter.
