"""JSON contract layer: serialize a sheet's rows to a C#-compatible JSON value.

Port of BakingSheet.Converters.Json/JsonSheetContractResolver.cs +
JsonSheetReferenceConverter.cs + JsonSheetConverter.cs serialization.

Output must be byte-compatible with the C# Newtonsoft output (per
``JsonExportTests.cs``):
  - Each sheet file is a JSON array of row objects.
  - Field order: data fields first, ``Id`` LAST (C# ContractResolver reorders).
  - ``enum`` -> member name string.
  - ``DateTime`` -> ``"2020-10-03T00:00:00"`` (ISO, no timezone suffix).
  - ``TimeSpan`` -> ``"02:00:00"``.
  - ``float``/``double`` -> Newtonsoft style: ``20.0`` keeps trailing zero,
    ``50.42``/``-0.002``/``5.13`` as-is.
  - ``SheetReference`` -> its Id value only.
  - ``null`` list -> ``null``; empty list -> ``[]``.
  - ``int`` dict keys -> string keys in JSON (``"2034"``).
  - ``SheetRowArray.Arr`` -> array of elem objects.
  - Compact JSON (no whitespace), ``ensure_ascii=False``.

We walk the live row objects (mirroring C# Newtonsoft over the object graph),
using the PropertyMap only to know field order and non-serialized markers.
"""
from __future__ import annotations

import datetime
import enum
from decimal import Decimal
from typing import Any, Optional, Union, get_args, get_origin

from ..core.schema import (
    NON_SERIALIZED,
    Reference,
    SheetRowArray,
    is_reference_type,
    reference_id_type,
)


def _unwrap_optional(typ: Any) -> Any:
    origin = get_origin(typ)
    if origin is Union:
        args = get_args(typ)
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1 and len(args) == 2:
            return non_none[0]
    return typ


def _is_list_type(typ: Any) -> bool:
    origin = get_origin(typ)
    return origin in (list,) or (isinstance(origin, type) and issubclass(origin, list))


def _is_dict_type(typ: Any) -> bool:
    origin = get_origin(typ)
    return origin in (dict,) or (isinstance(origin, type) and issubclass(origin, dict))


def _list_elem_type(typ: Any) -> Any:
    args = get_args(typ)
    return args[0] if args else Any


def _dict_kv_types(typ: Any) -> "tuple[Any, Any]":
    args = get_args(typ)
    if args and len(args) >= 2:
        return args[0], args[1]
    return str, Any


def _is_dataclass_type(typ: Any) -> bool:
    import dataclasses

    return dataclasses.is_dataclass(typ) and isinstance(typ, type)


def _instantiate(typ: Any) -> Any:
    """Create a zero-initialized instance of a dataclass type."""
    import dataclasses

    if _is_dataclass_type(typ):
        try:
            return typ()
        except TypeError:
            kwargs = {}
            for f in dataclasses.fields(typ):
                if f.default is not dataclasses.MISSING:
                    continue
                if f.default_factory is not dataclasses.MISSING:  # type: ignore[misc]
                    continue
                kwargs[f.name] = None
            try:
                return typ(**kwargs)
            except Exception:
                return typ.__new__(typ)
    return None


class _JsonEncoder:
    """Produces JSON-compatible Python values, then a compact string.

    The string formatting mirrors Newtonsoft.Json's default output for the
    primitive cases that matter to BakingSheet (floats, datetimes, enums).

    ``target`` optionally filters fields by visibility (see
    ``bakingsheet.visible_to``): a field whose metadata excludes ``target`` is
    dropped from the output. ``None`` disables filtering (all fields visible).
    """

    def __init__(self, resolver: Any = None, target: Optional[str] = None) -> None:
        self._resolver = resolver
        self._target = target

    def to_jsonable(self, value: Any, typ: Any = None, field_meta: Optional[dict] = None) -> Any:
        """Convert ``value`` (of declared ``typ``) to a JSON-able Python value."""
        raw_typ = typ
        typ = _unwrap_optional(typ) if typ is not None else type(value)
        is_optional = typ is not raw_typ
        if value is None:
            # C# value-type structs are never null: a non-Optional dataclass
            # field that is None serializes as a zero-initialized instance.
            if not is_optional and _is_dataclass_type(typ):
                return self._serialize_row(_instantiate(typ), typ)
            return None
        if is_reference_type(typ if typ is not None else type(value)):
            id_type = reference_id_type(typ)
            return self.to_jsonable(value.Id, id_type)
        if isinstance(value, Reference):
            return self.to_jsonable(value.Id, type(value.Id))
        if isinstance(value, bool):
            return value
        if isinstance(value, enum.Enum):
            return value.name
        if isinstance(value, datetime.datetime):
            return _format_datetime(value)
        if isinstance(value, datetime.timedelta):
            return _format_timedelta(value)
        if isinstance(value, Decimal):
            return _JsonRaw(_format_decimal(value))
        if typ is float and isinstance(value, (int,)) and not isinstance(value, bool):
            # declared float holding an int value -> render as float (20 -> 20.0)
            return _JsonFloat(float(value))
        if isinstance(value, float):
            return _JsonFloat(value)
        if _is_list_type(typ) or isinstance(value, list):
            elem_type = _list_elem_type(typ) if _is_list_type(typ) else Any
            return [self.to_jsonable(v, elem_type) for v in value]
        if _is_dict_type(typ) or isinstance(value, dict):
            _, v_type = _dict_kv_types(typ) if _is_dict_type(typ) else (str, Any)
            return {_dict_key_to_str(k): self.to_jsonable(v, v_type) for k, v in value.items()}
        if _is_dataclass_type(typ) or (hasattr(value, "__dataclass_fields__")):
            return self._serialize_row(value, typ if _is_dataclass_type(typ) else type(value))
        # int, str, etc.
        return value

    def _serialize_row(self, row: Any, row_type: Any) -> "dict[str, Any]":
        from ..core.schema import get_eligible_fields, visible_to
        from ..core.container import _type_hints

        hints = _type_hints(row_type)
        # Order: data fields in declaration order (subclass-first, matching C#
        # Config.GetEligibleProperties which walks DeclaredOnly up the MRO), with
        # Id moved to the end. This puts Arr (SheetRowArray) before Id (SheetRow).
        ordered: "list[tuple[str, Any, dict]]" = []
        id_field = None
        for f in get_eligible_fields(row_type):
            meta = dict(f.metadata) if f.metadata else {}
            if self._target is not None and not visible_to(meta, self._target):
                continue
            if f.name == "Id":
                id_field = (f.name, hints.get(f.name, f.type), meta)
                continue
            ordered.append((f.name, hints.get(f.name, f.type), meta))
        if id_field is not None:
            ordered.append(id_field)

        result: "dict[str, Any]" = {}
        for name, ftype, meta in ordered:
            val = getattr(row, name, None)
            result[name] = self.to_jsonable(val, ftype, meta)
        return result


class _JsonRaw:
    """A raw JSON token (e.g. a number) emitted verbatim, without quoting."""

    __slots__ = ("text",)

    def __init__(self, text: str) -> None:
        self.text = text

    def __repr__(self) -> str:
        return self.text


class _JsonFloat(_JsonRaw):
    """A float wrapper that renders in Newtonsoft style when dumped."""

    def __init__(self, value: float) -> None:
        super().__init__(_format_float(value))


def _format_float(value: float) -> str:
    """Render a float the way Newtonsoft.Json does for BakingSheet's cases.

    - Integral floats keep a trailing zero: ``20.0``.
    - Others use ``repr`` (shortest round-trip): ``50.42``, ``-0.002``, ``5.13``.
    """
    if value != value:  # NaN
        return "NaN"
    if value in (float("inf"), float("-inf")):
        return "Infinity" if value > 0 else "-Infinity"
    if value == int(value) and abs(value) < 1e16:
        return f"{int(value)}.0"
    return repr(value)


def _format_decimal(value: Decimal) -> str:
    """Render a Decimal. Newtonsoft emits ``10.03`` / ``-0.002`` (normalized)."""
    # Decimal normalizes trailing zeros differently; C# decimal keeps precision.
    # JsonExportTests expects 10.03 and -0.002 — str(Decimal) gives that for
    # values constructed without extra scale.
    s = format(value, "f")
    return s


def _format_datetime(value: datetime.datetime) -> str:
    """``2020-10-03T00:00:00``. C# DateTime (Kind=Unspecified) serializes without tz."""
    if value.tzinfo is not None:
        # convert to UTC then drop tz (C# DateTime serialized as local/unspecified)
        value = value.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    if value.microsecond:
        return value.strftime("%Y-%m-%dT%H:%M:%S.%f")
    return value.strftime("%Y-%m-%dT%H:%M:%S")


def _format_timedelta(value: datetime.timedelta) -> str:
    """``02:00:00`` / ``00:00:15``. C# TimeSpan ``c`` format."""
    total = value.total_seconds()
    negative = total < 0
    total = abs(total)
    days = int(total // 86400)
    hours = int((total % 86400) // 3600)
    minutes = int((total % 3600) // 60)
    seconds = int(total % 60)
    sign = "-" if negative else ""
    if days:
        return f"{sign}{days}.{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{sign}{hours:02d}:{minutes:02d}:{seconds:02d}"


def _dict_key_to_str(key: Any) -> str:
    if isinstance(key, bool):
        return "true" if key else "false"
    if isinstance(key, enum.Enum):
        return key.name
    return str(key)


def serialize_sheet(sheet: Any, resolver: Any = None, target: Optional[str] = None) -> Any:
    """Serialize a Sheet to a JSON-able list of row objects (Id-last each).

    ``target`` optionally filters fields by visibility metadata: fields whose
    ``exclude`` lists ``target`` (or whose ``include`` omits ``target``) are
    dropped. ``None`` disables filtering.
    """
    encoder = _JsonEncoder(resolver, target=target)
    row_type = sheet.row_type
    return [encoder._serialize_row(row, row_type) for row in sheet]


def dumps(value: Any, indent: Optional[int] = None) -> str:
    """Serialize a JSON-able value (produced by ``serialize_sheet``) to a string.

    Compact by default (no whitespace), matching Newtonsoft default. ``indent``
    enables pretty-printing.
    """
    return _dump(value, indent, 0)


def _dump(value: Any, indent: Optional[int], depth: int) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, _JsonRaw):
        return value.text
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return _format_float(value)
    if isinstance(value, Decimal):
        return _format_decimal(value)
    if isinstance(value, str):
        return _escape_string(value)
    if isinstance(value, list):
        if not value:
            return "[]"
        if indent is None:
            return "[" + ",".join(_dump(v, indent, depth + 1) for v in value) + "]"
        pad = " " * (indent * (depth + 1))
        inner = ",\n".join(pad + _dump(v, indent, depth + 1) for v in value)
        return "[\n" + inner + "\n" + " " * (indent * depth) + "]"
    if isinstance(value, dict):
        if not value:
            return "{}"
        if indent is None:
            items = ",".join(
                _escape_string(k) + ":" + _dump(v, indent, depth + 1)
                for k, v in value.items()
            )
            return "{" + items + "}"
        pad = " " * (indent * (depth + 1))
        items = ",\n".join(
            pad + _escape_string(k) + ": " + _dump(v, indent, depth + 1)
            for k, v in value.items()
        )
        return "{\n" + items + "\n" + " " * (indent * depth) + "}"
    # fallback
    return _escape_string(str(value))


def _escape_string(s: str) -> str:
    """Escape a string the way Newtonsoft does (minimal, like ``json.dumps``)."""
    out = ['"']
    for ch in s:
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\b":
            out.append("\\b")
        elif ch == "\f":
            out.append("\\f")
        elif ord(ch) < 0x20:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)
