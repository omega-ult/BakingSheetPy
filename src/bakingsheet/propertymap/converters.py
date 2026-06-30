"""Value converters: string cell <-> typed Python value.

Port of BakingSheet/Src/ValueConverter/*. The ContractResolver holds an ordered
list of converters and returns the first that ``can_convert`` a given type.

Conversions mirror C# semantics:
- ``PrimitiveConverter`` uses ``Convert.ChangeType`` (here: ``int(value)`` /
  ``float(value)`` / ``str``). Note C# treats ``bool`` as primitive too, but
  ``Convert.ChangeType("FALSE", bool)`` accepts TRUE/FALSE; Python's
  ``bool("False")`` is ``True``, so a dedicated ``BoolConverter`` is required.
- ``EnumConverter`` parses by name, case-insensitive (``Enum.Parse(…, true)``).
- ``DateTimeConverter`` parses local, converts to UTC via the configured tz.
- ``TimeSpanConverter`` parses ``HH:MM:SS`` (``timedelta``).
- ``NullableConverter`` unwraps ``Optional[X]``; empty -> ``None``.
"""
from __future__ import annotations

import datetime
import enum
from decimal import Decimal
from typing import Any, Optional, Union, get_args, get_origin


class ValueConvertingContext:
    """Port of ``SheetValueConvertingContext``. Holds tz + format + resolver."""

    def __init__(self, formatter: Any, resolver: "ContractResolver") -> None:
        self._format = formatter
        self._resolver = resolver

    @property
    def timezone(self) -> datetime.tzinfo:
        return self._format.timezone if self._format is not None else datetime.timezone.utc

    @property
    def format_provider(self) -> Any:
        return self._format.format_provider if self._format is not None else None

    def string_to_value(self, typ: Any, value: str) -> Any:
        conv = self._resolver.get_value_converter(typ)
        if conv is None:
            raise ValueError(f"No converter registered for type {typ}")
        return conv.string_to_value(typ, value, self)

    def value_to_string(self, typ: Any, value: Any) -> Optional[str]:
        conv = self._resolver.get_value_converter(typ)
        if conv is None:
            raise ValueError(f"No converter registered for type {typ}")
        return conv.value_to_string(typ, value, self)


class ValueConverter:
    """Base class. Override ``can_convert`` / ``string_to_value`` /
    ``value_to_string``."""

    def can_convert(self, typ: Any) -> bool:
        raise NotImplementedError

    def string_to_value(self, typ: Any, value: str, ctx: ValueConvertingContext) -> Any:
        raise NotImplementedError

    def value_to_string(
        self, typ: Any, value: Any, ctx: ValueConvertingContext
    ) -> Optional[str]:
        raise NotImplementedError


class PrimitiveConverter(ValueConverter):
    """str / int / float / Decimal. Port of ``PrimitiveValueConverter``."""

    def can_convert(self, typ: Any) -> bool:
        return typ in (str, int, float, Decimal, bytes)

    def string_to_value(self, typ: Any, value: str, ctx: ValueConvertingContext) -> Any:
        if typ is str:
            return value
        if typ is int:
            return int(value)
        if typ is float:
            return float(value)
        if typ is Decimal:
            return Decimal(value)
        if typ is bytes:
            return value.encode("utf-8")
        return typ(value)

    def value_to_string(
        self, typ: Any, value: Any, ctx: ValueConvertingContext
    ) -> Optional[str]:
        if value is None:
            return None
        return str(value)


class BoolConverter(ValueConverter):
    """Dedicated bool converter: C# ``Convert.ChangeType`` accepts ``TRUE`` /
    ``FALSE``; Python ``bool("False")`` is True, so handle explicitly.
    """

    def can_convert(self, typ: Any) -> bool:
        return typ is bool

    def string_to_value(self, typ: Any, value: str, ctx: ValueConvertingContext) -> Any:
        v = value.strip().upper()
        if v in ("TRUE", "1"):
            return True
        if v in ("FALSE", "0", ""):
            return False
        return bool(value)

    def value_to_string(
        self, typ: Any, value: Any, ctx: ValueConvertingContext
    ) -> Optional[str]:
        if value is None:
            return None
        return "TRUE" if value else "FALSE"


class EnumConverter(ValueConverter):
    """Parse enum by name, case-insensitive. Port of ``EnumValueConverter``."""

    def can_convert(self, typ: Any) -> bool:
        return isinstance(typ, type) and issubclass(typ, enum.Enum)

    def string_to_value(self, typ: Any, value: str, ctx: ValueConvertingContext) -> Any:
        # case-insensitive name match
        for member in typ:
            if member.name.lower() == value.strip().lower():
                return member
        # also accept numeric value
        try:
            return typ(int(value))
        except (ValueError, TypeError):
            pass
        raise ValueError(f"{value} is not a valid {typ.__name__}")

    def value_to_string(
        self, typ: Any, value: Any, ctx: ValueConvertingContext
    ) -> Optional[str]:
        if value is None:
            return None
        return value.name


class DateTimeConverter(ValueConverter):
    """Parse local datetime, convert to UTC. Port of ``DateTimeValueConverter``."""

    def can_convert(self, typ: Any) -> bool:
        return typ is datetime.datetime

    def string_to_value(self, typ: Any, value: str, ctx: ValueConvertingContext) -> Any:
        local = _parse_datetime(value)
        if local.tzinfo is None:
            # treat as local in configured tz, then convert to UTC
            local = local.replace(tzinfo=ctx.timezone)
        return local.astimezone(datetime.timezone.utc)

    def value_to_string(
        self, typ: Any, value: Any, ctx: ValueConvertingContext
    ) -> Optional[str]:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=datetime.timezone.utc)
        local = value.astimezone(ctx.timezone)
        return local.strftime("%Y-%m-%d %H:%M:%S")


class TimedeltaConverter(ValueConverter):
    """Parse ``HH:MM:SS``. Port of ``TimeSpanValueConverter``."""

    def can_convert(self, typ: Any) -> bool:
        return typ is datetime.timedelta

    def string_to_value(self, typ: Any, value: str, ctx: ValueConvertingContext) -> Any:
        return _parse_timedelta(value)

    def value_to_string(
        self, typ: Any, value: Any, ctx: ValueConvertingContext
    ) -> Optional[str]:
        if value is None:
            return None
        return _format_timedelta(value)


class NullableConverter(ValueConverter):
    """Unwrap ``Optional[X]`` / ``Union[X, None]``. Empty string -> None.

    Port of ``NullableValueConverter``. Note: in the PropertyMap build step we
    usually unwrap ``Optional`` at the node level; this converter handles cases
    where the nullable type reaches the registry directly.
    """

    def can_convert(self, typ: Any) -> bool:
        return _is_optional_type(typ)

    def string_to_value(self, typ: Any, value: str, ctx: ValueConvertingContext) -> Any:
        if value is None or value == "":
            return None
        inner = _optional_inner(typ)
        return ctx.string_to_value(inner, value)

    def value_to_string(
        self, typ: Any, value: Any, ctx: ValueConvertingContext
    ) -> Optional[str]:
        if value is None:
            return None
        inner = _optional_inner(typ)
        return ctx.value_to_string(inner, value)


class ReferenceConverter(ValueConverter):
    """Build/serialize a Reference. Port of ``SheetReferenceValueConverter``.

    For the cell path (CSV round-trip) a Reference stringifies to its Id.
    JSON export handles references directly in the contract layer, not here.
    """

    def can_convert(self, typ: Any) -> bool:
        from ..core.schema import is_reference_type

        return is_reference_type(typ)

    def string_to_value(self, typ: Any, value: str, ctx: ValueConvertingContext) -> Any:
        from ..core.schema import Reference, reference_id_type

        id_type = reference_id_type(typ)
        if value is None or value == "":
            return None
        id_val = ctx.string_to_value(id_type if id_type is not None else str, value)
        return typ(id_val)

    def value_to_string(
        self, typ: Any, value: Any, ctx: ValueConvertingContext
    ) -> Optional[str]:
        from ..core.schema import reference_id_type

        if value is None:
            return None
        id_type = reference_id_type(typ)
        return ctx.value_to_string(id_type if id_type is not None else str, value.Id)


class ContractResolver:
    """Port of ``SheetContractResolver``. Ordered converter registry + cache."""

    def __init__(self, extra: "tuple[ValueConverter, ...]" = ()) -> None:
        self._converters: list[ValueConverter] = [
            NullableConverter(),
            EnumConverter(),
            BoolConverter(),
            PrimitiveConverter(),
            DateTimeConverter(),
            TimedeltaConverter(),
            ReferenceConverter(),
            *extra,
        ]
        self._cache: "dict[Any, Optional[ValueConverter]]" = {}

    def get_value_converter(self, typ: Any) -> Optional[ValueConverter]:
        if typ in self._cache:
            return self._cache[typ]
        conv = next((c for c in self._converters if c.can_convert(typ)), None)
        self._cache[typ] = conv
        return conv

    def string_to_value(self, typ: Any, value: str, formatter: Any) -> Any:
        ctx = ValueConvertingContext(formatter, self)
        return ctx.string_to_value(typ, value)

    def value_to_string(self, typ: Any, value: Any, formatter: Any) -> Optional[str]:
        ctx = ValueConvertingContext(formatter, self)
        return ctx.value_to_string(typ, value)


# default singleton, mirroring C# ``SheetContractResolver.Instance``
_DEFAULT_RESOLVER = ContractResolver()


def default_resolver() -> ContractResolver:
    return _DEFAULT_RESOLVER


# --------------------------------------------------------------------------- #
# Optional / datetime / timedelta helpers
# --------------------------------------------------------------------------- #
def _is_optional_type(typ: Any) -> bool:
    origin = get_origin(typ)
    if origin is Union:
        args = get_args(typ)
        return type(None) in args and len(args) == 2
    return False


def _optional_inner(typ: Any) -> Any:
    args = get_args(typ)
    for a in args:
        if a is not type(None):
            return a
    return str


def _parse_datetime(value: str) -> datetime.datetime:
    value = value.strip()
    # try a few common formats, falling back to fromisoformat
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
    ):
        try:
            return datetime.datetime.strptime(value, fmt)
        except ValueError:
            continue
    try:
        return datetime.datetime.fromisoformat(value)
    except ValueError:
        return datetime.datetime.strptime(value, "%Y-%m-%d")


def _parse_timedelta(value: str) -> datetime.timedelta:
    value = value.strip()
    # formats: [[+-]d.]HH:MM:SS[.ffffff] or HH:MM:SS
    negative = value.startswith("-")
    if value[0:1] in "+-":
        value = value[1:]
    parts = value.split(":")
    if len(parts) == 3:
        h, m, s = parts
    elif len(parts) == 2:
        h = "0"
        m, s = parts
    else:
        # bare seconds
        h = m = "0"
        s = parts[0]
    # handle fractional seconds and day component
    days = 0
    if "." in h:
        day_str, h = h.split(".")
        days = int(day_str)
    sec_float = float(s)
    td = datetime.timedelta(
        days=days, hours=int(h), minutes=int(m), seconds=sec_float
    )
    return -td if negative else td


def _format_timedelta(td: datetime.timedelta) -> str:
    total = td.total_seconds()
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
