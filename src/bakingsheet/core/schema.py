"""Schema base classes: Sheet, SheetRow, SheetRowArray, Reference, VerticalList.

Port of BakingSheet/Src/Sheet.cs, SheetRow.cs, SheetReference.cs, VerticalList.cs,
NonSerializedAttribute.cs.

Schema is defined with ``@dataclass``. The PropertyMap engine introspects the
type hints (``typing.get_type_hints``) instead of C# reflection.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field, fields
from typing import (
    Any,
    Generic,
    Iterable,
    Iterator,
    Optional,
    TypeVar,
    Union,
    get_args,
    get_origin,
)

TKey = TypeVar("TKey")
TRow = TypeVar("TRow", bound="SheetRow")
TElem = TypeVar("TElem", bound="SheetRowElem")


# metadata key marking a field as non-serialized (C# [NonSerialized])
NON_SERIALIZED = "non_serialized"
# metadata key for a per-field ValueConverter instance (C# [SheetValueConverter])
CONVERTER = "converter"


def non_serialized(default: Any = None) -> Any:
    """Return a dataclass ``field`` that is skipped by the PropertyMap and JSON.

    Equivalent to C# ``[NonSerialized]``.
    """
    return field(default=default, repr=False, metadata={NON_SERIALIZED: True})


# metadata key: set of target names that may NOT see this field.
# e.g. metadata={"exclude": ["client"]} drops the field from any output whose
# target is "client". Absent => visible to all targets.
EXCLUDE_TARGETS = "exclude"
# metadata key: explicit allow-list of targets. When present, the field is only
# emitted for outputs whose target is in this list. Absent => no restriction.
INCLUDE_TARGETS = "include"


def visible_to(field_meta: dict, target: Optional[str]) -> bool:
    """Return True if a field with ``field_meta`` is visible to ``target``.

    ``target`` of ``None`` means "no target filtering" — all fields visible.
    A field is dropped for a target if:
      - ``exclude`` lists the target, OR
      - ``include`` is set and does not list the target.
    """
    if target is None:
        return True
    exclude = field_meta.get(EXCLUDE_TARGETS) if field_meta else None
    if exclude and target in exclude:
        return False
    include = field_meta.get(INCLUDE_TARGETS) if field_meta else None
    if include is not None and target not in include:
        return False
    return True


class VerticalList(list):
    """A ``list`` subclass used purely as a *type marker* in annotations.

    ``VerticalList[T]`` marks a field whose values are laid out across multiple
    physical spreadsheet rows (vertical). It behaves as a plain list at runtime;
    the PropertyMap inspects the generic argument ``T`` via ``__class_getitem__``.

    Port of BakingSheet/Src/VerticalList.cs. C# uses a dedicated ``IVerticalList``
    interface; in Python a list subclass with parameterised generics is the
    cleanest marker that also behaves as a list.
    """

    def __class_getitem__(cls, item):  # noqa: D401
        # Return a typing-style alias whose origin is VerticalList.
        return _VerticalListAlias(cls, item)


class _VerticalListAlias:
    """Parameterised alias for ``VerticalList[T]``.

    Mimics ``typing`` generics enough for ``get_origin``/``get_args`` via the
    dedicated helpers in ``propertymap``. We do not register with ``typing`` to
    avoid surprising stdlib behaviour; instead ``is_vertical_list`` checks the
    ``__origin__`` attribute directly.
    """

    def __init__(self, origin, item):
        self.__origin__ = origin
        self.__args__ = (item,)
        # capture the caller's module so string element types can be resolved
        import sys

        f = sys._getframe(1) if hasattr(sys, "_getframe") else None
        self.__module__ = f.f_globals.get("__name__") if f else getattr(origin, "__module__", None)

    def __repr__(self):
        return f"VerticalList[{self.__args__[0]!r}]"


def is_vertical_list(typ: Any) -> bool:
    """True if ``typ`` is a ``VerticalList[...]`` alias."""
    return isinstance(typ, _VerticalListAlias) and typ.__origin__ is VerticalList


def vertical_list_elem_type(typ: Any) -> Any:
    """Element type of a ``VerticalList[T]`` alias. Resolves string/ForwardRef
    element types lazily (the annotation ``VerticalList['Foo.Row']`` keeps the
    argument as a string until resolved here)."""
    import sys
    import typing

    args = getattr(typ, "__args__", None)
    if not args:
        return typ
    elem = args[0]
    if isinstance(elem, str):
        return _resolve_row_ref(elem, getattr(typ, "__module__", None))
    if isinstance(elem, typing.ForwardRef):
        module_name = getattr(typ, "__module__", None)
        globalns = vars(sys.modules[module_name]) if module_name and module_name in sys.modules else {}
        try:
            return elem._evaluate(globalns, None, recursive_guard=frozenset())
        except TypeError:
            try:
                return elem._evaluate(globalns, None, frozenset())
            except TypeError:
                return elem._evaluate(globalns, None)
    return elem


@dataclass
class SheetRow(Generic[TKey]):
    """A single record of a Sheet. ``Id`` is the mandatory first column.

    Port of BakingSheet/Src/SheetRow.cs ``SheetRow<TKey>``. ``Index`` mirrors the
    C# internal index (non-serialized). Override ``post_load`` for a hook.
    """

    Id: Any = None
    Index: int = field(default=0, repr=False, metadata={NON_SERIALIZED: True})

    def post_load(self, context: "SheetConvertingContext") -> None:  # noqa: F821
        pass

    def verify_assets(self, context: "SheetConvertingContext") -> None:  # noqa: F821
        pass


@dataclass
class SheetRowElem:
    """An element of a SheetRowArray's vertical ``Arr``.

    Port of BakingSheet/Src/SheetRow.cs ``SheetRowElem``.
    """

    Index: int = field(default=0, repr=False, metadata={NON_SERIALIZED: True})

    def post_load(self, context: "SheetConvertingContext") -> None:  # noqa: F821
        pass

    def verify_assets(self, context: "SheetConvertingContext") -> None:  # noqa: F821
        pass


@dataclass
class SheetRowArray(SheetRow[TKey], Generic[TKey, TElem]):
    """A row that vertically spans multiple physical rows via ``Arr``.

    Port of BakingSheet/Src/SheetRow.cs ``SheetRowArray<TKey,TElem>``. ``Arr``
    holds the per-physical-row elements and is the vertical subtree. Supports
    the C# ``SheetRowArray<TElem>`` shorthand (string Id).
    """

    def __class_getitem__(cls, params):  # noqa: D401
        if not isinstance(params, tuple):
            params = (str, params)
        return super().__class_getitem__(params)

    Arr: list = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.Arr is None:
            self.Arr = []

    def __getitem__(self, index: int) -> Any:
        return self.Arr[index]

    def __len__(self) -> int:
        return len(self.Arr)

    def __iter__(self) -> Iterator[Any]:
        return iter(self.Arr)

    @property
    def count(self) -> int:
        return len(self.Arr)

    def post_load(self, context: "SheetConvertingContext") -> None:  # noqa: F821
        super().post_load(context)
        for index, elem in enumerate(self.Arr):
            elem.Index = index
            elem.post_load(context)


class Reference(Generic[TKey, TRow]):
    """Cross-sheet reference column.

    Port of BakingSheet/Src/SheetReference.cs ``Sheet<TKey,TValue>.Reference``.
    Stores the target ``Id``; ``Ref`` is resolved during ``MapReferences`` and is
    not serialized. Serialized form is just the ``Id`` value.

    ``Reference[str, MonsterSheet.Row]`` — the second type argument is the target
    row type, used at runtime to locate the owning sheet via the
    ``row_type -> sheet`` registry.
    """

    __slots__ = ("Id", "Ref")

    def __init__(self, id: Any = None) -> None:
        self.Id = id
        self.Ref: Any = None

    @classmethod
    def __class_getitem__(cls, params):
        if not isinstance(params, tuple) or len(params) != 2:
            raise TypeError("Reference requires two type arguments: Reference[TKey, TRow]")
        key_t, row_t = params
        # Create a subclass capturing the type params so we can resolve them later.
        sub = type(
            f"Reference[{key_t!s}, {row_t!s}]",
            (cls,),
            {"_id_type": key_t, "_row_type": row_t},
        )
        return sub

    @property
    def id_type(self) -> Any:
        return self._id_type

    @property
    def row_type(self) -> Any:
        return self._row_type

    def is_valid(self) -> bool:
        return self.Ref is not None

    def map(self, context: "SheetConvertingContext", sheet: "Sheet") -> None:  # noqa: F821
        """Resolve the reference against ``sheet``. Mirrors ``ISheetReference.Map``."""
        if self.Ref is None:
            self.Ref = sheet[self.Id] if self.Id is not None else None
        elif sheet[self.Id] is not None and self.Ref is not sheet[self.Id]:
            context.logger.error(
                f'Found different reference than originally set for "{self.Id}"'
            )

        if self.Id is not None and self.Ref is None:
            context.logger.error(
                f'Failed to find reference "{self.Id}" on {sheet.name}'
            )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Reference):
            return NotImplemented
        return self.Id == other.Id

    def __hash__(self) -> int:
        return hash(self.Id)

    def __repr__(self) -> str:
        return "(null)" if self.Id is None else str(self.Id)


def is_reference_type(typ: Any) -> bool:
    """True if ``typ`` is a ``Reference[...]`` subclass."""
    return isinstance(typ, type) and issubclass(typ, Reference)


def reference_id_type(typ: Any) -> Any:
    """Id type of a ``Reference[K, R]`` subclass."""
    return getattr(typ, "_id_type", str)


def reference_row_type(typ: Any) -> Any:
    """Target row type of a ``Reference[K, R]`` subclass.

    The second type argument may be a string/ForwardRef (because the annotation
    ``Reference[str, 'Foo.Row']`` is evaluated at class-definition time, before
    the target exists). It is resolved lazily here against the importing
    module's globals.
    """
    import sys
    import typing

    rt = getattr(typ, "_row_type", None)
    if isinstance(rt, str):
        return _resolve_row_ref(rt, getattr(typ, "__module__", None))
    if isinstance(rt, typing.ForwardRef):
        return _resolve_forwardref(rt, getattr(typ, "__module__", None))
    return rt


def _resolve_forwardref(ref: Any, module_name: Optional[str]) -> Any:
    globalns = vars(sys.modules[module_name]) if module_name and module_name in sys.modules else {}
    try:
        return ref._evaluate(globalns, None, recursive_guard=frozenset())
    except TypeError:
        try:
            return ref._evaluate(globalns, None, frozenset())
        except TypeError:
            return ref._evaluate(globalns, None)
    except NameError:
        return ref


def _resolve_row_ref(qualname: str, module_name: Optional[str]) -> Any:
    """Resolve a dotted/qualified row-type name like ``Foo.Row`` to a class.

    Tries the defining module first, then falls back to scanning loaded modules.
    """
    import sys

    # try the reference's own module first
    candidates = []
    if module_name and module_name in sys.modules:
        candidates.append(sys.modules[module_name])

    parts = qualname.split(".")
    # qualname like "TestSheet.Row" — last is the row class, rest is path
    for mod in candidates:
        obj = _lookup_in_module(mod, parts)
        if obj is not None:
            return obj

    # fall back to scanning all loaded modules for the qualname
    head = parts[0]
    for mod in list(sys.modules.values()):
        if mod is None:
            continue
        if hasattr(mod, head):
            obj = _lookup_in_module(mod, parts)
            if obj is not None and isinstance(obj, type):
                return obj
    return qualname


def _lookup_in_module(mod: Any, parts: "list[str]") -> Any:
    obj = mod
    for p in parts:
        if obj is None:
            return None
        # support nested class lookup: attribute first, then scan nested classes
        if hasattr(obj, p):
            obj = getattr(obj, p)
        else:
            return None
    return obj


def _is_dataclass_type(obj: Any) -> bool:
    return dataclasses.is_dataclass(obj) and isinstance(obj, type)


class Sheet(Generic[TKey, TRow]):
    """A single page/table of data, keyed by row ``Id``.

    Port of BakingSheet/Src/Sheet.cs ``Sheet<TKey,TValue>``. C# inherits from
    ``KeyedCollection``; here we use a plain dict (insertion-ordered in 3.7+).
    Subclasses fix ``TKey``/``TRow`` via ``Sheet[Row]`` (defaults to str Id) or
    ``Sheet[K, Row]``.
    """

    def __class_getitem__(cls, params):  # noqa: D401
        # Support the C# ``Sheet<T>`` shorthand (string Id) in addition to
        # ``Sheet<TKey, TRow>``.
        if not isinstance(params, tuple):
            params = (str, params)
        return super().__class_getitem__(params)

    def __init__(self) -> None:
        self._rows: "dict[Any, SheetRow]" = {}
        self.name: str = ""
        self.hash_code: str = ""
        self._property_map: Any = None  # lazy, cached

    # -- collection API -------------------------------------------------
    def add(self, row: Any) -> None:
        self._rows[row.Id] = row

    def __iter__(self) -> Iterator[Any]:
        return iter(self._rows.values())

    def __getitem__(self, key: Any) -> Any:
        return self._rows.get(key)

    def __contains__(self, key: Any) -> bool:
        return key in self._rows

    def __len__(self) -> int:
        return len(self._rows)

    @property
    def keys(self) -> "dict[Any, Any].keys":
        return self._rows.keys()

    def find(self, key: Any) -> Any:
        return self._rows.get(key)

    def contains(self, key: Any) -> bool:
        return key in self._rows

    # -- schema introspection ------------------------------------------
    @property
    def row_type(self) -> Any:
        """The ``TRow`` type parameter of this Sheet subclass."""
        return _resolve_sheet_row_type(type(self))

    def get_property_map(self, context: "SheetConvertingContext") -> "PropertyMap":  # noqa: F821
        if self._property_map is None:
            from ..propertymap.map import PropertyMap

            self._property_map = PropertyMap(context, type(self))
        return self._property_map

    # -- lifecycle hooks (port of Sheet.cs) ----------------------------
    def map_references(
        self, context: "SheetConvertingContext", row_type_to_sheet: dict  # noqa: F821
    ) -> None:
        pmap = self.get_property_map(context)
        pmap.update_index(self)

        for node, indexes in pmap.traverse_leaf():
            if not is_reference_type(node.value_type):
                continue

            ref_row_type = reference_row_type(node.value_type)
            sheet = row_type_to_sheet.get(ref_row_type)
            if sheet is None:
                context.logger.error(
                    f"Failed to find sheet for {ref_row_type} reference"
                )
                continue

            for row in self:
                vcount = node.get_vertical_count(row, iter(indexes))
                for vindex in range(vcount):
                    ok, obj = node.try_get_value(row, vindex, iter(indexes))
                    if not ok or obj is None:
                        continue
                    if isinstance(obj, Reference):
                        obj.map(context, sheet)
                        node.set_value(row, vindex, iter(indexes), obj)

    def post_load(self, context: "SheetConvertingContext") -> None:  # noqa: F821
        index = -1
        for row in self:
            row.Index = index + 1
            index = row.Index
            row.post_load(context)


def _resolve_sheet_row_type(sheet_cls: type) -> Any:
    """Resolve the ``TRow`` argument of a ``Sheet[K, Row]`` subclass.

    Mirrors C# ``PropertyMap.GetGenericArgument(sheetType, Sheet<,>)[1]``.
    Walks ``__orig_bases__`` to find a ``Sheet[...]`` parameterisation. Evaluates
    any ``ForwardRef`` argument against the defining module's globals so that
    ``Sheet['Foo.Row']`` resolves to the actual nested class.
    """
    import typing
    from .schema import Sheet as _Sheet

    for base in getattr(sheet_cls, "__orig_bases__", ()):  # noqa: B020
        origin = get_origin(base)
        if origin is None:
            continue
        if _is_sheet_origin(origin):
            args = get_args(base)
            row_arg = args[-1] if len(args) >= 1 else None
            return _eval_row_type(row_arg, sheet_cls)

    for base in sheet_cls.__mro__[1:]:
        for ob in getattr(base, "__orig_bases__", ()):  # noqa: B020
            origin = get_origin(ob)
            if origin is not None and _is_sheet_origin(origin):
                args = get_args(ob)
                if args:
                    return _eval_row_type(args[-1], sheet_cls)
    raise TypeError(f"Could not resolve Sheet row type for {sheet_cls}")


def _eval_row_type(row_arg: Any, sheet_cls: type) -> Any:
    """Evaluate a ``ForwardRef`` row argument against the module globals."""
    import sys
    import typing

    if isinstance(row_arg, typing.ForwardRef):
        module_name = getattr(sheet_cls, "__module__", None)
        globalns = {}
        if module_name and module_name in sys.modules:
            globalns = vars(sys.modules[module_name])
        # Python 3.9+: _evaluate(globalns, localns, type_params=..., recursive_guard=...)
        try:
            return row_arg._evaluate(globalns, None, frozenset())
        except TypeError:
            try:
                return row_arg._evaluate(globalns, None)
            except TypeError:
                # 3.12+: recursive_guard is keyword-only
                return row_arg._evaluate(globalns, None, recursive_guard=frozenset())
        except NameError:
            return row_arg
    return row_arg


def _is_sheet_origin(origin: Any) -> bool:
    """True if ``origin`` is ``Sheet`` or a ``Sheet`` subclass origin."""
    from .schema import Sheet as _Sheet

    return origin is _Sheet or (isinstance(origin, type) and issubclass(origin, _Sheet))


def get_eligible_fields(row_type: type) -> "list[dataclasses.Field]":
    """Yield dataclass fields eligible for serialization, subclass-first.

    Port of ``Config.GetEligibleProperties``: walks the MRO from the most
    derived class up to the base, yielding each class's own declared fields
    (``DeclaredOnly``), skipping ``[NonSerialized]``. This puts subclass fields
    before inherited ones (e.g. ``Arr`` before ``Id``), matching C# reflection
    order. ``dataclasses.fields()`` returns base-first, so we rebuild manually.
    """
    result: "list[dataclasses.Field]" = []
    seen: set[str] = set()
    # collect field name -> Field from dataclasses.fields (resolves defaults etc.)
    all_fields = {f.name: f for f in dataclasses.fields(row_type)}
    # walk MRO most-derived-first, using each class's own __annotations__
    for cls in row_type.__mro__:
        anns = getattr(cls, "__dict__", {}).get("__annotations__", {})
        for name in anns:
            if name in seen:
                continue
            f = all_fields.get(name)
            if f is None:
                continue
            if f.metadata.get(NON_SERIALIZED):
                seen.add(name)
                continue
            seen.add(name)
            result.append(f)
    return result
