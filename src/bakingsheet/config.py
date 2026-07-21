"""YAML configuration: separate field schema (code) from input/output config.

A config file points at a shared schema module (``SheetContainerBase``
subclass) and describes the import source plus one or more output directories.
The same schema code serves different projects via different config files.
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional


@dataclass
class OutputConfig:
    path: str
    pretty: bool = True
    indent: Optional[int] = 2
    only: Optional[List[str]] = None
    exclude: Optional[List[str]] = None
    ensure_ascii: bool = False
    target: Optional[str] = None  # visibility target, e.g. "client" / "server"
    sheet_targets: Optional["dict[str, str]"] = None  # per-sheet target override


@dataclass
class ImportConfig:
    source: str  # "csv" | "excel" | "google"
    path: Optional[str] = None
    extension: Optional[str] = None
    split_header: bool = False
    google: Optional["GoogleConfig"] = None


@dataclass
class GoogleConfig:
    spreadsheet_id: str
    credentials: str  # path to service-account JSON


@dataclass
class OptionsConfig:
    timezone: str = "UTC"
    locale: Optional[str] = None
    comment_prefix: str = "$"
    index_delimiter: str = ":"


@dataclass
class SchemaConfig:
    module: str
    container: str


@dataclass
class RunConfig:
    schema: SchemaConfig
    import_: ImportConfig
    outputs: List[OutputConfig]
    options: OptionsConfig = field(default_factory=OptionsConfig)


def load_config(path: str) -> RunConfig:
    """Load a YAML config file into a ``RunConfig``."""
    import yaml

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    schema_raw = raw["schema"]
    schema = SchemaConfig(module=schema_raw["module"], container=schema_raw["container"])

    imp_raw = raw["import"]
    google = None
    if imp_raw.get("google"):
        g = imp_raw["google"]
        google = GoogleConfig(spreadsheet_id=g["spreadsheet_id"], credentials=g["credentials"])
    imp = ImportConfig(
        source=imp_raw["source"],
        path=imp_raw.get("path"),
        extension=imp_raw.get("extension"),
        split_header=imp_raw.get("split_header", False),
        google=google,
    )

    opts_raw = raw.get("options") or {}
    options = OptionsConfig(
        timezone=opts_raw.get("timezone", "UTC"),
        locale=opts_raw.get("locale"),
        comment_prefix=opts_raw.get("comment_prefix", "$"),
        index_delimiter=opts_raw.get("index_delimiter", ":"),
    )

    outputs = []
    for o in raw["outputs"]:
        outputs.append(
            OutputConfig(
                path=o["path"],
                pretty=o.get("pretty", True),
                indent=o.get("indent", 2),
                only=o.get("only"),
                exclude=o.get("exclude"),
                ensure_ascii=o.get("ensure_ascii", False),
                target=o.get("target"),
                sheet_targets=o.get("sheet_targets"),
            )
        )

    return RunConfig(schema=schema, import_=imp, outputs=outputs, options=options)


def load_container(cfg: RunConfig):
    """Import the schema module and instantiate the container class."""
    module = importlib.import_module(cfg.schema.module)
    cls = getattr(module, cfg.schema.container)
    return cls()


def _timezone(name: str):
    import datetime

    if not name or name == "UTC":
        return datetime.timezone.utc
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:
        return datetime.timezone.utc


def build_importer(cfg: RunConfig):
    """Construct the importer described by the config."""
    tz = _timezone(cfg.options.timezone)
    src = cfg.import_.source.lower()
    if src == "csv":
        from .converters.csv_converter import CsvSheetConverter

        return CsvSheetConverter(
            cfg.import_.path,
            timezone=tz,
            extension=cfg.import_.extension or "csv",
            split_header=cfg.import_.split_header,
        )
    if src == "excel":
        from .converters.excel_converter import ExcelSheetConverter

        return ExcelSheetConverter(
            cfg.import_.path,
            timezone=tz,
            split_header=cfg.import_.split_header,
        )
    if src == "google":
        from .converters.google_converter import GoogleSheetConverter

        g = cfg.import_.google
        return GoogleSheetConverter(
            spreadsheet_id=g.spreadsheet_id,
            credential_json=Path(g.credentials).read_text(encoding="utf-8"),
            timezone=tz,
        )
    raise ValueError(f"Unknown import source: {src}")


def build_exporters(cfg: RunConfig):
    """Construct the list of JSON exporters (one per output directory)."""
    from .converters.json_converter import JsonSheetExporter

    exporters = []
    for o in cfg.outputs:
        exporters.append(
            JsonSheetExporter(
                paths=[o.path],
                indent=o.indent if o.pretty else None,
                ensure_ascii=o.ensure_ascii,
                target=o.target,
                sheet_targets=o.sheet_targets,
                only=o.only,
                exclude=o.exclude,
            )
        )
    return exporters
