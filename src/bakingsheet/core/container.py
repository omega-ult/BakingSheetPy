"""SheetContainer and converting context.

Port of BakingSheet/Src/SheetContainerBase.cs and SheetConvertingContext.cs.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, fields, is_dataclass
from typing import Any, Optional

from .schema import Sheet


class _ContainerLogger:
    """A thin logger wrapper that records whether any error was logged.

    Mirrors the C# ``ILogger`` + test ``VerifyNoError`` pattern. Uses the
    stdlib ``logging`` module under the hood and tracks an error flag.
    """

    def __init__(self, logger: Optional[logging.Logger] = None) -> None:
        self._logger = logger or logging.getLogger("bakingsheet")
        self.has_error = False
        self.errors: list[str] = []
        self._scope: list[str] = []

    def _prefix(self) -> str:
        return ":".join(self._scope) + ": " if self._scope else ""

    def begin_scope(self, name: Any) -> "_Scope":
        return _Scope(self, str(name))

    def error(self, msg: str, exc: Optional[BaseException] = None) -> None:
        self.has_error = True
        full = self._prefix() + msg
        self.errors.append(full)
        if exc is not None:
            self._logger.error(full, exc_info=exc)
        else:
            self._logger.error(full)

    def warning(self, msg: str) -> None:
        self._logger.warning(self._prefix() + msg)

    def info(self, msg: str) -> None:
        self._logger.info(self._prefix() + msg)

    def verify_no_error(self) -> None:
        if self.has_error:
            raise AssertionError("Expected no errors but got:\n" + "\n".join(self.errors))


class _Scope:
    def __init__(self, logger: _ContainerLogger, name: str) -> None:
        self._logger = logger
        self._name = name

    def __enter__(self) -> "_Scope":
        self._logger._scope.append(self._name)
        return self

    def __exit__(self, *exc) -> None:
        self._logger._scope.pop()


@dataclass
class SheetConvertingContext:
    """Port of ``SheetConvertingContext``. Carries container + logger."""

    container: Any = None
    logger: Any = None
    verifiers: tuple = ()


def make_context(container: Any, logger: Any) -> SheetConvertingContext:
    return SheetConvertingContext(container=container, logger=logger)


@dataclass
class SheetContainerBase:
    """Base class for a workbook holding multiple Sheets.

    Port of ``SheetContainerBase``. Subclasses are ``@dataclass``-es whose fields
    are ``Sheet`` instances (or ``None`` to be lazily created during ``bake``).
    The field name is the sheet name (matching C# property-name == sheet name).

    Subclasses should define a ``__post_init__`` only if they need custom setup;
    the base ``__post_init__`` initializes the logger and contract resolver.
    """

    def __post_init__(self) -> None:
        self._logger = _ContainerLogger(None)
        from ..propertymap.converters import default_resolver

        self._contract_resolver = default_resolver()

    # Allow constructing without dataclass machinery (back-compat / tests).
    def __init__(self, logger: Any = None) -> None:
        self._logger = _ContainerLogger(
            logger if isinstance(logger, logging.Logger) else None
        )
        from ..propertymap.converters import default_resolver

        self._contract_resolver = default_resolver()

    # -- logger ---------------------------------------------------------
    @property
    def logger(self) -> _ContainerLogger:
        return self._logger

    @property
    def contract_resolver(self) -> Any:
        return self._contract_resolver

    @contract_resolver.setter
    def contract_resolver(self, value: Any) -> None:
        self._contract_resolver = value

    # -- sheet discovery ------------------------------------------------
    def get_sheet_properties(self) -> "dict[str, str]":
        """Return ``{sheet_name: attribute_name}`` in declaration order.

        Port of ``GetSheetProperties``: fields whose value is a ``Sheet``
        instance, or whose declared type is a ``Sheet`` subclass (lazily
        created).
        """
        result: "dict[str, str]" = {}
        if not is_dataclass(self):
            return result
        hints = _type_hints(type(self))
        for f in fields(self):
            attr_type = hints.get(f.name, f.type)
            if _is_sheet_type(attr_type):
                result[f.name] = f.name
        return result

    def find(self, name: str) -> Optional[Sheet]:
        return getattr(self, name, None)

    def find_typed(self, name: str) -> Optional[Sheet]:
        return self.find(name)

    # -- lifecycle ------------------------------------------------------
    def bake(self, *importers: Any) -> bool:
        """Import data from one or more importers, then post-load.

        Port of ``SheetContainerBase.Bake``: clears existing sheets, runs each
        importer, then ``PostLoad``.
        """
        context = make_context(self, self._logger)

        for name in self.get_sheet_properties():
            # clear currently assigned sheets
            attr_type = _type_hints(type(self)).get(name)
            if attr_type is not None:
                setattr(self, name, None)

        for importer in importers:
            if not importer.import_(context):
                return False

        self.post_load()
        return True

    def store(self, exporters: Any) -> bool:
        """Export data via one exporter or a list of exporters.

        Port of ``SheetContainerBase.Store``. Extended to accept a list of
        exporters (multiple output directories in one run).
        """
        context = make_context(self, self._logger)
        if isinstance(exporters, (list, tuple)):
            for exp in exporters:
                if not exp.export(context):
                    return False
            return True
        return exporters.export(context)

    def post_load(self) -> None:
        """Port of ``SheetContainerBase.PostLoad``.

        Builds ``row_type -> sheet`` registry, sets sheet names, then runs
        ``MapReferences`` for all sheets BEFORE ``PostLoad`` on each sheet.
        """
        context = make_context(self, self._logger)
        properties = self.get_sheet_properties()

        row_type_to_sheet: "dict[type, Sheet]" = {}

        for name in properties:
            sheet = getattr(self, name, None)
            if sheet is None:
                # a sheet that is null (not provided this run) is a soft
                # condition, not a hard error — mirrors C# TestLogger.VerifyNoError
                # which explicitly tolerates "Failed to find sheet" messages.
                context.logger.warning(f"Failed to find sheet: {name}")
                continue

            sheet.name = name

            try:
                rt = sheet.row_type
            except TypeError:
                context.logger.error(f"Could not resolve row type for {name}")
                continue

            if rt in row_type_to_sheet:
                context.logger.error(f"Duplicated Row type is used for {name}")
                continue

            row_type_to_sheet[rt] = sheet

        # make sure all references are mapped before calling PostLoad
        for sheet in row_type_to_sheet.values():
            sheet.map_references(context, row_type_to_sheet)

        for sheet in row_type_to_sheet.values():
            sheet.post_load(context)

    def verify(self, *verifiers: Any) -> None:
        context = make_context(self, self._logger)
        context.verifiers = verifiers
        for name in self.get_sheet_properties():
            sheet = getattr(self, name, None)
            if isinstance(sheet, Sheet):
                sheet.verify_assets(context)


def _type_hints(cls: type) -> "dict[str, Any]":
    import sys
    import typing

    try:
        return typing.get_type_hints(cls, include_extras=True)
    except Exception:
        # Build a globalns that includes typing names (Optional, List, Dict, ...)
        # plus the defining module's globals, so annotations like
        # ``Optional[TestSheet]`` resolve even when the class was defined in a
        # scope where those names aren't module-level.
        globalns = dict(vars(typing))
        mod = sys.modules.get(getattr(cls, "__module__", None))
        if mod is not None:
            globalns.update(vars(mod))
        try:
            return typing.get_type_hints(cls, globalns=globalns, localns=None, include_extras=True)
        except Exception:
            return dict(getattr(cls, "__annotations__", {}))


def _is_sheet_type(typ: Any) -> bool:
    from typing import Union, get_args, get_origin

    origin = get_origin(typ)
    if origin is Union:
        # Optional[Sheet] / Union[Sheet, None]
        for a in get_args(typ):
            if _is_sheet_type(a):
                return True
        return False
    if origin is not None:
        return isinstance(origin, type) and issubclass(origin, Sheet)
    return isinstance(typ, type) and issubclass(typ, Sheet)
