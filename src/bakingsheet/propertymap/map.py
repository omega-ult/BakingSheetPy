"""PropertyMap: the schema tree mapping column headers to nested fields.

Port of BakingSheet/Src/PropertyMap/PropertyMap.cs + PropertyNode*.cs.

Each node represents a value location and carries:
  - ``get_from(parent_value, index)``: read this node's value out of its parent's
    value (object attribute / list index / dict key / root passthrough).
  - ``set_on(parent_value, index, value)``: write it back.
  - ``index_type``: ``int`` (list), a key type (dict), or ``None`` (object/root).
    When non-None, this node consumes one entry from the shared ``indexer``
    during traversal — matching C# ``Parent.GetChildIndex`` consuming an index.

``try_get_value`` / ``_modify_value`` recurse up to the root to obtain the
parent value, then apply this node's accessor. Index consumption happens when
``self.index_type is not None`` (mirroring C# ``GetChildIndex`` on the child
asking its parent).

This is a faithful but flattened port: the C# getter/setter delegates are
replaced by per-node ``_get_from``/``_set_on`` methods, dispatched by type.
"""
from __future__ import annotations

import dataclasses
import enum
import typing
from typing import Any, Iterator, List, Optional, Tuple, Union, get_args, get_origin

from .._internal.config import INDEX_DELIMITER, parse_flatten_path
from ..core.schema import (
    NON_SERIALIZED,
    Reference,
    SheetRowArray,
    VerticalList,
    get_eligible_fields,
    is_reference_type,
    is_vertical_list,
    reference_id_type,
    reference_row_type,
    vertical_list_elem_type,
)
from .converters import ContractResolver, ValueConvertingContext, default_resolver


def _unwrap_optional(typ: Any) -> "tuple[Any, bool]":
    origin = get_origin(typ)
    if origin is Union:
        args = get_args(typ)
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1 and len(args) == 2:
            return non_none[0], True
    return typ, False


def _is_list_type(typ: Any) -> bool:
    origin = get_origin(typ)
    return origin in (list, List) or (isinstance(origin, type) and issubclass(origin, list))


def _list_elem_type(typ: Any) -> Any:
    args = get_args(typ)
    return args[0] if args else Any


def _is_dict_type(typ: Any) -> bool:
    origin = get_origin(typ)
    return origin in (dict,) or (isinstance(origin, type) and issubclass(origin, dict))


def _dict_kv_types(typ: Any) -> "tuple[Any, Any]":
    args = get_args(typ)
    if args and len(args) >= 2:
        return args[0], args[1]
    return str, Any


def _is_dataclass_type(typ: Any) -> bool:
    return dataclasses.is_dataclass(typ) and isinstance(typ, type)


def _instantiate(typ: Any) -> Any:
    """Create a default instance of ``typ``. Port of ``Activator.CreateInstance``."""
    if typ is None or typ is type(None):
        return None
    if _is_list_type(typ):
        return []
    if _is_dict_type(typ):
        return {}
    if is_vertical_list(typ):
        return VerticalList()
    if is_reference_type(typ):
        return None
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
    try:
        return typ()
    except TypeError:
        return None


# --------------------------------------------------------------------------- #
# Accessor strategies: how a node reads/writes itself out of its parent value.
# --------------------------------------------------------------------------- #
def _attr_get(parent_obj: Any, attr: str, index: Any) -> Any:
    return getattr(parent_obj, attr, None) if attr is not None else parent_obj


def _attr_set(parent_obj: Any, attr: str, index: Any, value: Any) -> None:
    if attr is not None:
        setattr(parent_obj, attr, value)


def _list_get(parent_obj: Any, attr: str, index: Any) -> Any:
    lst = parent_obj
    if attr is not None:
        lst = getattr(parent_obj, attr, None)
    if isinstance(lst, list) and isinstance(index, int):
        idx = index - 1  # 1-base to 0-base
        if 0 <= idx < len(lst):
            return lst[idx]
    return None


def _list_set(parent_obj: Any, attr: str, index: Any, value: Any, child_value_type: Any) -> None:
    lst = parent_obj
    if attr is not None:
        lst = getattr(parent_obj, attr, None)
        if lst is None:
            lst = []
            setattr(parent_obj, attr, lst)
    if not isinstance(lst, list) or not isinstance(index, int):
        return
    idx = index - 1
    while len(lst) <= idx:
        if is_reference_type(child_value_type):
            lst.append(None)
        else:
            lst.append(_instantiate(child_value_type))
    lst[idx] = value


def _dict_get(parent_obj: Any, attr: str, index: Any) -> Any:
    d = parent_obj
    if attr is not None:
        d = getattr(parent_obj, attr, None)
    if isinstance(d, dict) and index in d:
        return d[index]
    return None


def _dict_set(parent_obj: Any, attr: str, index: Any, value: Any) -> None:
    d = parent_obj
    if attr is not None:
        d = getattr(parent_obj, attr, None)
        if d is None:
            d = {}
            setattr(parent_obj, attr, d)
    if isinstance(d, dict):
        d[index] = value


class PropertyNode:
    """Base node. Each subclass defines ``index_type`` and its accessor."""

    def __init__(
        self,
        parent: Optional["PropertyNode"],
        full_path: Optional[str],
        value_type: Any,
        attr: Optional[str] = None,
        field_meta: Optional[dict] = None,
    ) -> None:
        self.parent = parent
        self.full_path = full_path
        self.value_type = value_type
        self.attr = attr
        self.field_meta = field_meta or {}

    # -- to override ----------------------------------------------------
    @property
    def index_type(self) -> Any:
        return None

    @property
    def is_vertical(self) -> bool:
        return False

    @property
    def is_leaf(self) -> bool:
        return False

    @property
    def column_node(self) -> "PropertyNode":
        return self

    @property
    def value_converter(self) -> Any:
        return None

    def get_child(self, subpath: str) -> Optional["PropertyNode"]:
        return None

    def has_subpath(self, subpath: str) -> bool:
        return False

    def update_index(self, obj: Any) -> None:
        pass

    def calculate_depth(self) -> int:
        return 0

    def traverse_children(self, indexes: list) -> Iterator["PropertyNode"]:
        yield self

    def get_vertical_count(self, row: Any, indexer: Iterator[Any]) -> int:
        if self.parent is not None:
            return self.parent.get_vertical_count(row, indexer)
        return 1

    # -- accessor (how this node reads/writes itself from its parent) ---
    def _get_from(self, parent_obj: Any, index: Any) -> Any:
        return _attr_get(parent_obj, self.attr, index)

    def _set_on(self, parent_obj: Any, index: Any, value: Any) -> None:
        _attr_set(parent_obj, self.attr, index, value)

    # -- index consumption (port of GetChildIndex) ----------------------
    def _consume_index(self, vindex: int, indexer: Iterator[Any]) -> Any:
        """The index this node uses to read itself from its parent.

        Mirrors C# ``Parent.GetChildIndex`` called by the child: returns the
        parent's index. List/Dict nodes (index_type != None) consume one entry
        from the shared ``indexer``; vertical lists use ``vindex+1``.
        """
        if self.index_type is None:
            return None
        return next(indexer, None)

    # -- value access (port of TryGetValue / ModifyValue) ---------------
    def try_get_value(
        self, row: Any, vindex: int, indexer: Iterator[Any]
    ) -> "tuple[bool, Any]":
        if self.parent is None:
            # root: this node's own value (e.g. the row itself, or row.Arr for
            # the Arr subtree root which is an attribute-accessor node).
            return True, self._get_from(row, None)
        ok, parent_obj = self.parent.try_get_value(row, vindex, indexer)
        if not ok or parent_obj is None:
            return False, None
        index = self.parent._consume_index(vindex, indexer)
        return True, self._get_from(parent_obj, index)

    def get_value(self, row: Any, vindex: int, indexer: Iterator[Any]) -> Any:
        ok, val = self.try_get_value(row, vindex, indexer)
        return val

    def set_value(
        self, row: Any, vindex: int, indexer: Iterator[Any], value: Any
    ) -> None:
        self._modify_value(row, vindex, indexer, lambda _: value)

    def _modify_value(
        self,
        row: Any,
        vindex: int,
        indexer: Iterator[Any],
        modifier: Any,
    ) -> None:
        if self.parent is None:
            # root: fetch this node's own value, apply modifier, write back.
            obj = self._get_from(row, None)
            if obj is None and not self.is_leaf:
                obj = self._instantiate_self()
                self._set_on(row, None, obj)
                obj = self._get_from(row, None)
            obj = modifier(obj)
            self._set_on(row, None, obj)
            return

        def parent_modifier(parent_obj: Any) -> Any:
            if parent_obj is None:
                return None
            index = self.parent._consume_index(vindex, indexer)
            obj = self._get_from(parent_obj, index)
            if obj is None and not self.is_leaf:
                obj = self._instantiate_self()
                self._set_on(parent_obj, index, obj)
                obj = self._get_from(parent_obj, index)
            obj = modifier(obj)
            self._set_on(parent_obj, index, obj)
            return parent_obj

        self.parent._modify_value(row, vindex, indexer, parent_modifier)

    def _instantiate_self(self) -> Any:
        return _instantiate(self.value_type)

    # -- helper ---------------------------------------------------------
    def _append_index(self, depth: int) -> str:
        return f"{self.full_path}{INDEX_DELIMITER}{{{depth}}}"


class LeafNode(PropertyNode):
    """Terminal node carrying a ValueConverter."""

    def __init__(self, parent, full_path, value_type, attr, field_meta, converter) -> None:
        super().__init__(parent, full_path, value_type, attr, field_meta)
        self._converter = converter

    @property
    def is_leaf(self) -> bool:
        return True

    @property
    def value_converter(self) -> Any:
        return self._converter


class ObjectNode(PropertyNode):
    """A dataclass/struct node with named children, or a leaf if convertible."""

    def __init__(
        self,
        parent: Optional[PropertyNode],
        full_path: Optional[str],
        value_type: Any,
        attr: Optional[str] = None,
        field_meta: Optional[dict] = None,
        resolver: Optional[ContractResolver] = None,
        depth: int = 0,
        is_root: bool = False,
    ) -> None:
        super().__init__(parent, full_path, value_type, attr, field_meta)
        self._resolver = resolver or default_resolver()
        self._is_root = is_root
        self._children: "dict[str, PropertyNode]" = {}
        self._converter: Any = None
        self._generate_children(depth)

    @property
    def is_leaf(self) -> bool:
        return not self._children

    @property
    def value_converter(self) -> Any:
        return self._converter

    def get_child(self, subpath: str) -> Optional[PropertyNode]:
        return self._children.get(subpath)

    def has_subpath(self, subpath: str) -> bool:
        return subpath in self._children

    def _append_path(self, subpath: str) -> str:
        if self.full_path is None:
            return subpath
        return f"{self.full_path}{INDEX_DELIMITER}{subpath}"

    def update_index(self, obj: Any) -> None:
        if self.is_leaf or obj is None:
            return
        for child in self._children.values():
            elem = child._get_from(obj, None)
            if elem is not None:
                child.update_index(elem)

    def calculate_depth(self) -> int:
        if self.is_leaf:
            return 0
        depth = 0
        for child in self._children.values():
            depth = max(depth, child.calculate_depth())
        return depth

    def traverse_children(self, indexes: list) -> Iterator[PropertyNode]:
        if self.is_leaf:
            yield self
            return
        # Id column should come first (C# behaviour for import ordering)
        id_child = self._children.get("Id")
        if id_child is not None:
            yield from id_child.traverse_children(indexes)
        for child in self._children.values():
            if child is id_child:
                continue
            yield from child.traverse_children(indexes)

    def _generate_children(self, depth: int) -> None:
        prop_conv = self.field_meta.get("converter") if self.field_meta else None
        if prop_conv is not None:
            self._converter = prop_conv
        else:
            self._converter = self._resolver.get_value_converter(self.value_type)

        if self._converter is not None:
            return

        if not _is_dataclass_type(self.value_type):
            return

        is_root = self._is_root
        row_type = self.value_type
        try:
            from ..core.container import _type_hints
            hints = _type_hints(row_type)
        except Exception:
            hints = dict(getattr(row_type, "__annotations__", {}))

        for f in get_eligible_fields(row_type):
            if is_root and f.name == "Arr":
                continue
            child_type = hints.get(f.name, f.type)
            child_path = self._append_path(f.name)
            child = create_node(
                self,
                child_path,
                child_type,
                attr=f.name,
                field_meta=dict(f.metadata) if f.metadata else {},
                resolver=self._resolver,
                depth=depth,
            )
            self._children[f.name] = child


class ListNode(PropertyNode):
    """A ``list[T]`` (horizontal, 1-based) or ``VerticalList[T]`` (vertical)."""

    def __init__(
        self,
        parent: Optional[PropertyNode],
        full_path: Optional[str],
        value_type: Any,
        attr: Optional[str] = None,
        field_meta: Optional[dict] = None,
        resolver: Optional[ContractResolver] = None,
        depth: int = 0,
        is_vertical: bool = False,
    ) -> None:
        super().__init__(parent, full_path, value_type, attr, field_meta)
        self._resolver = resolver or default_resolver()
        self._is_vertical = is_vertical
        self._max_count = 1
        if is_vertical:
            elem_type = vertical_list_elem_type(value_type)
            child_path = full_path
            child_depth = depth
        else:
            elem_type = _list_elem_type(value_type)
            child_path = self._append_index(depth)
            child_depth = depth + 1
        self._child = create_node(
            self,
            child_path,
            elem_type,
            attr=None,  # the child reads from the list itself, not an attribute
            field_meta=field_meta,
            resolver=self._resolver,
            depth=child_depth,
        )

    @property
    def index_type(self) -> Any:
        return int

    @property
    def is_vertical(self) -> bool:
        return self._is_vertical

    @property
    def child_value_type(self) -> Any:
        return self._child.value_type

    @property
    def column_node(self) -> PropertyNode:
        return self._child.column_node if self._is_vertical else self

    def get_child(self, subpath: str) -> Optional[PropertyNode]:
        return self._child

    def _consume_index(self, vindex: int, indexer: Iterator[Any]) -> Any:
        if self._is_vertical:
            return vindex + 1  # 0-base to 1-base
        return next(indexer, None)

    def update_index(self, obj: Any) -> None:
        if isinstance(obj, list):
            self._max_count = max(self._max_count, len(obj))
            for elem in obj:
                self._child.update_index(elem)

    def calculate_depth(self) -> int:
        return self._child.calculate_depth() + (0 if self._is_vertical else 1)

    def get_vertical_count(self, row: Any, indexer: Iterator[Any]) -> int:
        if self._is_vertical:
            obj = self.get_value(row, 0, indexer)
            if isinstance(obj, list) and len(obj) > 0:
                return len(obj)
            return 1
        if self.parent is not None:
            return self.parent.get_vertical_count(row, indexer)
        return 1

    def traverse_children(self, indexes: list) -> Iterator[PropertyNode]:
        if self._is_vertical:
            yield from self._child.traverse_children(indexes)
            return
        current = len(indexes)
        indexes.append(None)
        for i in range(1, self._max_count + 1):
            indexes[current] = i
            yield from self._child.traverse_children(indexes)
        indexes.pop()

    # ListNode itself is read as an attribute of its parent (the list object);
    # its CHILD reads an element by index. So ListNode uses the attr accessor,
    # and the child overrides _get_from/_set_on to index the list.
    def _get_from(self, parent_obj: Any, index: Any) -> Any:
        return _attr_get(parent_obj, self.attr, index)

    def _set_on(self, parent_obj: Any, index: Any, value: Any) -> None:
        _attr_set(parent_obj, self.attr, index, value)


class ListElementNode(PropertyNode):
    """A list's child: reads/writes an element by 1-based index on the list.

    This is mixed in via the child node creation: when a ListNode creates its
    child, the child's accessor is overridden to index the list.
    """

    # Implemented by wrapping the child node; see create_node.
    pass


class DictNode(PropertyNode):
    """A ``dict[K, V]``. The key becomes a path segment."""

    def __init__(
        self,
        parent: Optional[PropertyNode],
        full_path: Optional[str],
        value_type: Any,
        attr: Optional[str] = None,
        field_meta: Optional[dict] = None,
        resolver: Optional[ContractResolver] = None,
        depth: int = 0,
    ) -> None:
        super().__init__(parent, full_path, value_type, attr, field_meta)
        self._resolver = resolver or default_resolver()
        key_type, elem_type = _dict_kv_types(value_type)
        self._key_type = key_type
        self._possible_keys: "set[Any]" = set()
        child_path = self._append_index(depth)
        self._child = create_node(
            self,
            child_path,
            elem_type,
            attr=None,
            field_meta=field_meta,
            resolver=self._resolver,
            depth=depth + 1,
        )

    @property
    def index_type(self) -> Any:
        return self._key_type

    def get_child(self, subpath: str) -> Optional[PropertyNode]:
        return self._child

    def update_index(self, obj: Any) -> None:
        if isinstance(obj, dict):
            for key in list(obj.keys()):
                self._possible_keys.add(key)
            for val in obj.values():
                self._child.update_index(val)

    def calculate_depth(self) -> int:
        return self._child.calculate_depth() + 1

    def traverse_children(self, indexes: list) -> Iterator[PropertyNode]:
        if not self._possible_keys:
            return
        current = len(indexes)
        indexes.append(None)
        for key in list(self._possible_keys):
            indexes[current] = key
            yield from self._child.traverse_children(indexes)
        indexes.pop()

    # DictNode itself is an attribute; its child reads/writes by key.
    def _get_from(self, parent_obj: Any, index: Any) -> Any:
        return _attr_get(parent_obj, self.attr, index)

    def _set_on(self, parent_obj: Any, index: Any, value: Any) -> None:
        _attr_set(parent_obj, self.attr, index, value)


def create_node(
    parent: Optional[PropertyNode],
    full_path: Optional[str],
    typ: Any,
    attr: Optional[str] = None,
    field_meta: Optional[dict] = None,
    resolver: Optional[ContractResolver] = None,
    depth: int = 0,
    is_root: bool = False,
) -> PropertyNode:
    """Dispatch factory. Port of ``PropertyNode.Create`` + ``GenerateChildren``."""
    resolver = resolver or default_resolver()
    typ, _is_opt = _unwrap_optional(typ)

    if is_vertical_list(typ):
        node = ListNode(parent, full_path, typ, attr, field_meta, resolver, depth, is_vertical=True)
        _wrap_indexed_child(node, is_list=True)
        return node

    if _is_list_type(typ):
        node = ListNode(parent, full_path, typ, attr, field_meta, resolver, depth, is_vertical=False)
        _wrap_indexed_child(node, is_list=True)
        return node

    if _is_dict_type(typ):
        node = DictNode(parent, full_path, typ, attr, field_meta, resolver, depth)
        _wrap_indexed_child(node, is_list=False)
        return node

    if _is_dataclass_type(typ):
        return ObjectNode(parent, full_path, typ, attr, field_meta, resolver, depth, is_root=is_root)

    conv = None
    # per-field converter (C# [SheetValueConverter] via PropertyInfo) takes priority
    if field_meta and field_meta.get("converter") is not None:
        conv = field_meta["converter"]
    else:
        conv = resolver.get_value_converter(typ)
    if conv is not None:
        return LeafNode(parent, full_path, typ, attr, field_meta, conv)

    return ObjectNode(parent, full_path, typ, attr, field_meta, resolver, depth, is_root=is_root)


def _wrap_indexed_child(node: PropertyNode, is_list: bool) -> None:
    """Override the child's accessor to read/write an element by index/key.

    The ListNode/DictNode itself is accessed as an attribute of its parent
    (returning the list/dict object). Its child reads/writes an element of that
    list/dict by index/key. The child's ``_get_from`` receives the list/dict
    object as ``parent_obj`` (already fetched by the ListNode/DictNode accessor),
    so it indexes directly — no attribute lookup. This replaces C#'s per-node
    ``PropertyNodeList.ValueGetter`` / ``PropertyNodeDictionary.ValueGetter``.
    """
    child = node._child  # type: ignore[attr-defined]
    child_value_type = child.value_type

    if is_list:
        def get_from(parent_obj: Any, index: Any) -> Any:
            if isinstance(parent_obj, list) and isinstance(index, int):
                idx = index - 1  # 1-base to 0-base
                if 0 <= idx < len(parent_obj):
                    return parent_obj[idx]
            return None

        def set_on(parent_obj: Any, index: Any, value: Any) -> None:
            if not isinstance(parent_obj, list) or not isinstance(index, int):
                return
            idx = index - 1
            while len(parent_obj) <= idx:
                # grow with null (C# appends null for reference-type elements;
                # the value-type instantiate happens lazily in _modify_value
                # when a non-leaf is accessed).
                parent_obj.append(None)
            parent_obj[idx] = value
    else:
        def get_from(parent_obj: Any, index: Any) -> Any:
            if isinstance(parent_obj, dict) and index in parent_obj:
                return parent_obj[index]
            return None

        def set_on(parent_obj: Any, index: Any, value: Any) -> None:
            if isinstance(parent_obj, dict):
                parent_obj[index] = value

    child._get_from = get_from  # type: ignore[assignment]
    child._set_on = set_on  # type: ignore[assignment]


class PropertyMap:
    """Tree of assignable properties for a sheet's row type."""

    def __init__(self, context: Any, sheet_type: Any) -> None:
        self._context = context
        self._resolver = (
            context.container.contract_resolver
            if context is not None and getattr(context.container, "contract_resolver", None)
            else default_resolver()
        )
        row_type = _resolve_sheet_row_type(sheet_type)
        self.row_type = row_type
        # resolve the Id key type from the Sheet[K, Row] generic (C# default: string)
        self._id_type = _resolve_sheet_key_type(sheet_type) or str

        self.root = ObjectNode(
            None, None, row_type, attr=None, resolver=self._resolver, depth=0, is_root=True
        )
        self._max_depth = self.root.calculate_depth()

        # patch the Id leaf node's value_type to the resolved key type, so a
        # string Id (declared ``Any`` on the base SheetRow) converts correctly.
        self._patch_id_type(self.root, self._id_type)

        self.arr: Optional[ListNode] = None
        if _is_row_array_type(row_type):
            elem_type = _resolve_row_array_elem_type(row_type)
            if elem_type is not None:
                arr_type = VerticalList[elem_type]
                self.arr = ListNode(
                    None, None, arr_type, attr="Arr", resolver=self._resolver, depth=0, is_vertical=True
                )
                _wrap_indexed_child(self.arr, is_list=True)
                self._max_depth = max(self._max_depth, self.arr.calculate_depth())

        self._warned: "set[str]" = set()
        self._indexes: list = []

    @property
    def max_depth(self) -> int:
        return self._max_depth

    def set_value(
        self, row: Any, vindex: int, path: str, value: str, formatter: Any
    ) -> None:
        """Port of ``PropertyMap.SetValue``."""
        resolver = self._resolver
        vctx = ValueConvertingContext(formatter, resolver)

        self._indexes.clear()
        indexes = self._indexes
        node: Optional[PropertyNode] = None
        is_vertical = False

        for subpath in parse_flatten_path(path):
            if node is None:
                if self.root.has_subpath(subpath):
                    node = self.root.column_node
                elif self.arr is not None and self.arr.column_node.has_subpath(subpath):
                    node = self.arr.column_node
                    is_vertical = True
                else:
                    if path not in self._warned:
                        self._context.logger.error("Column name is invalid")
                        self._warned.add(path)
                    return

            if node.index_type is not None:
                index = vctx.string_to_value(node.index_type, subpath)
                indexes.append(index)

            node = node.get_child(subpath)
            if node is None:
                self._context.logger.error(f"Column path is invalid: {path}")
                return

            if node.is_vertical:
                if is_vertical:
                    self._context.logger.error("Nested vertical list is not supported")
                    return
                is_vertical = True

            node = node.column_node

        if not is_vertical and vindex != 0:
            self._context.logger.error("There is multiple value for a non-vertical column")
            return

        if node is None:
            self._context.logger.error(f"Column path is invalid: {path}")
            return

        converter = node.value_converter
        if converter is None:
            self._context.logger.error(
                f"No converter registered for type {node.value_type}"
            )
            return

        converted = converter.string_to_value(node.value_type, value, vctx)
        node.set_value(row, vindex, iter(list(indexes)), converted)

    def update_index(self, sheet: Any) -> None:
        """Port of ``PropertyMap.UpdateIndex``."""
        for row in sheet:
            self.root.update_index(row)
            if self.arr is not None and isinstance(row, SheetRowArray):
                self.arr.update_index(row.Arr)

    def _patch_id_type(self, root: "ObjectNode", id_type: Any) -> None:
        """Set the Id column's type/converter from the Sheet's TKey.

        The base ``SheetRow.Id`` is annotated ``Any``; concrete sheets fix the
        key type via ``Sheet[K, Row]``. We patch the Id leaf so it converts
        using the resolved key type (default ``str``).
        """
        id_node = root._children.get("Id") if root._children else None
        if id_node is None:
            return
        id_type, _ = _unwrap_optional(id_type)
        if id_node.value_converter is None or id_node.value_type in (Any, type(None)):
            conv = self._resolver.get_value_converter(id_type)
            if conv is not None:
                # rebuild the Id node as a leaf with the resolved type
                new_node = LeafNode(
                    id_node.parent, id_node.full_path, id_type,
                    id_node.attr, id_node.field_meta, conv,
                )
                root._children["Id"] = new_node

    def traverse_leaf(self) -> Iterator["tuple[PropertyNode, list]"]:
        """Port of ``PropertyMap.TraverseLeaf``."""
        self._indexes.clear()
        for node in self.root.traverse_children(self._indexes):
            yield node, list(self._indexes)
        if self.arr is not None:
            for node in self.arr.traverse_children(self._indexes):
                yield node, list(self._indexes)


def _resolve_sheet_row_type(sheet_type: Any) -> Any:
    from ..core.schema import _resolve_sheet_row_type as _resolve

    return _resolve(sheet_type)


def _resolve_sheet_key_type(sheet_type: Any) -> Any:
    """The ``TKey`` argument of a ``Sheet[K, Row]`` subclass (C# default: str)."""
    import sys
    import typing
    from typing import get_args, get_origin

    from ..core.schema import Sheet as _Sheet

    def _eval(arg: Any) -> Any:
        if isinstance(arg, typing.ForwardRef):
            module_name = getattr(sheet_type, "__module__", None)
            globalns = vars(sys.modules[module_name]) if module_name and module_name in sys.modules else {}
            try:
                return arg._evaluate(globalns, None, recursive_guard=frozenset())
            except TypeError:
                try:
                    return arg._evaluate(globalns, None, frozenset())
                except TypeError:
                    return arg._evaluate(globalns, None)
            except NameError:
                return arg
        return arg

    for base in getattr(sheet_type, "__orig_bases__", ()):
        origin = get_origin(base)
        if origin is not None and isinstance(origin, type) and issubclass(origin, _Sheet):
            args = get_args(base)
            if args:
                return _eval(args[0])
    for base in sheet_type.__mro__[1:]:
        for ob in getattr(base, "__orig_bases__", ()):
            origin = get_origin(ob)
            if origin is not None and isinstance(origin, type) and issubclass(origin, _Sheet):
                args = get_args(ob)
                if args:
                    return _eval(args[0])
    return None


def _is_row_array_type(row_type: Any) -> bool:
    from ..core.schema import SheetRowArray

    return isinstance(row_type, type) and issubclass(row_type, SheetRowArray)


def _resolve_row_array_elem_type(row_type: Any) -> Any:
    """The ``TElem`` argument of a ``SheetRowArray[K, Elem]`` subclass."""
    import sys
    import typing
    from typing import get_args, get_origin

    from ..core.schema import SheetRowArray

    for base in getattr(row_type, "__orig_bases__", ()):
        origin = get_origin(base)
        if origin is None:
            continue
        if isinstance(origin, type) and issubclass(origin, SheetRowArray):
            args = get_args(base)
            if args:
                elem = args[-1]
                if isinstance(elem, typing.ForwardRef):
                    module_name = getattr(row_type, "__module__", None)
                    globalns = vars(sys.modules[module_name]) if module_name and module_name in sys.modules else {}
                    try:
                        return elem._evaluate(globalns, None, recursive_guard=frozenset())
                    except TypeError:
                        try:
                            return elem._evaluate(globalns, None, frozenset())
                        except TypeError:
                            return elem._evaluate(globalns, None)
                return elem
    for base in row_type.__mro__[1:]:
        for ob in getattr(base, "__orig_bases__", ()):
            origin = get_origin(ob)
            if origin is not None and isinstance(origin, type) and issubclass(origin, SheetRowArray):
                args = get_args(ob)
                if args:
                    return args[-1]
    return None
