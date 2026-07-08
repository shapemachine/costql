"""Query IR: a small selection tree that both the generator emits and the
Pricer consumes, plus a serializer to a GraphQL string and a parser so the
Pricer can also accept an arbitrary GraphQL query string.

The parser covers the executable query surface (v0.2): named/anonymous query
operations, fields with arguments (scalar, enum, list, and input-object
literals), aliases (resolved to the schema field name; duplicated aliased
fields are kept and priced once each), named and inline fragments (spreads are
expanded in place; cycles and unknown names raise), variables (substituted
from the ``variables`` argument or the declared default; a variable with
neither is dropped so the ceiling's worst-case bound applies), and directives
(parsed and ignored: ``@skip``/``@include`` are priced as included, which
never under-prices).

An inline fragment (or an expanded named-fragment spread) becomes a Selection
with ``on`` set to its type condition and ``name`` = ``"on:<Type>"`` (the
``on:`` prefix cannot collide with a field: GraphQL names cannot contain a
colon). Fragment nodes carry no resolver and contribute nothing to
``count_fields``; their children are counted per spread site.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_ON_PREFIX = "on:"


@dataclass
class Selection:
    name: str
    args: dict[str, Any] = field(default_factory=dict)
    children: list[Selection] = field(default_factory=list)
    on: str | None = None    # inline-fragment type condition (name = "on:<Type>")

    def child(self, name: str) -> Selection | None:
        for c in self.children:
            if c.name == name:
                return c
        return None


# --------------------------------------------------------------------------
# Serialize IR -> GraphQL string
# --------------------------------------------------------------------------

def _fmt_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return "null"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, _Enum):
        return v.name
    if isinstance(v, list):
        return "[" + ", ".join(_fmt_value(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{" + ", ".join(f"{k}: {_fmt_value(x)}" for k, x in v.items()) + "}"
    # string / ID
    escaped = str(v).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


@dataclass
class _Enum:
    name: str


@dataclass
class _Var:
    """A ``$name`` argument value awaiting substitution (never survives
    ``parse_query``: it is replaced by the provided/default value or the
    argument is dropped)."""
    name: str


@dataclass
class _Spread:
    """A ``...Name`` fragment spread awaiting expansion (never survives
    ``parse_query``)."""
    name: str


def serialize(root_selections: list[Selection], op_name: str = "") -> str:
    def render(sel: Selection, indent: int) -> str:
        pad = "  " * indent
        head = f"... on {sel.on}" if sel.on is not None else sel.name
        args = ""
        if sel.args:
            args = "(" + ", ".join(f"{k}: {_fmt_value(v)}" for k, v in sel.args.items()) + ")"
        if sel.children:
            inner = "\n".join(render(c, indent + 1) for c in sel.children)
            return f"{pad}{head}{args} {{\n{inner}\n{pad}}}"
        return f"{pad}{head}{args}"

    body = "\n".join(render(s, 1) for s in root_selections)
    header = f"query {op_name} " if op_name else "query "
    return f"{header}{{\n{body}\n}}"


# --------------------------------------------------------------------------
# Parser GraphQL string -> [Selection]  (top-level = root fields)
# --------------------------------------------------------------------------

_TOKEN = re.compile(r"""
    (?P<ws>\s+|\#[^\n]*)
  | (?P<spread>\.\.\.)
  | (?P<punct>[{}():,\[\]!$=@])
  | (?P<string>"(?:[^"\\]|\\.)*")
  | (?P<number>-?\d+\.\d+|-?\d+)
  | (?P<name>[_A-Za-z][_A-Za-z0-9]*)
""", re.VERBOSE)


def _tokenize(s: str) -> list[tuple[str, str]]:
    out = []
    i = 0
    while i < len(s):
        m = _TOKEN.match(s, i)
        if not m:
            raise ValueError(f"Cannot tokenize near: {s[i:i+20]!r}")
        i = m.end()
        kind = m.lastgroup
        if kind == "ws":
            continue
        out.append((kind, m.group()))
    return out


class _Parser:
    def __init__(self, tokens):
        self.t = tokens
        self.i = 0

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else (None, None)

    def next(self):
        tok = self.t[self.i]
        self.i += 1
        return tok

    def expect(self, val):
        k, v = self.next()
        if v != val:
            raise ValueError(f"Expected {val!r}, got {v!r}")

    # ---- document ---------------------------------------------------------

    def parse_document(self) -> tuple[list, dict, dict]:
        """-> (operation_selections, fragments, variable_defaults).

        fragments: name -> (type_condition, raw_children); selections may
        still contain _Spread/_Var placeholders."""
        operation = None
        fragments: dict[str, tuple[str, list]] = {}
        defaults: dict[str, Any] = {}
        while self.peek()[0] is not None:
            k, v = self.peek()
            if v == "fragment":
                self.next()
                _, fname = self.next()
                self.expect("on")
                _, cond = self.next()
                self._parse_directives()
                self.expect("{")
                fragments[fname] = (cond, self.parse_selection_set())
            elif v in ("query", "mutation", "subscription") or v == "{":
                if operation is not None:
                    raise ValueError(
                        "multiple operations in one document; pass exactly one")
                operation = self._parse_operation(defaults)
            else:
                raise ValueError(f"Unexpected token {v!r} at document level")
        if operation is None:
            raise ValueError("no operation in document")
        return operation, fragments, defaults

    def _parse_operation(self, defaults: dict) -> list:
        k, v = self.peek()
        if v in ("query", "mutation", "subscription"):
            self.next()
            if self.peek()[0] == "name":       # optional operation name
                self.next()
            if self.peek()[1] == "(":          # variable definitions
                self._parse_variable_defs(defaults)
            self._parse_directives()
        self.expect("{")
        return self.parse_selection_set()

    def _parse_variable_defs(self, defaults: dict) -> None:
        self.expect("(")
        while True:
            k, v = self.peek()
            if v == ")":
                self.next()
                break
            if v == ",":
                self.next()
                continue
            self.expect("$")
            _, vname = self.next()
            self.expect(":")
            self._consume_type()
            if self.peek()[1] == "=":
                self.next()
                defaults[vname] = self.parse_value()
            self._parse_directives()

    def _consume_type(self) -> None:
        """Consume a type reference: Name / [Type], each optionally NON_NULL."""
        k, v = self.peek()
        if v == "[":
            self.next()
            self._consume_type()
            self.expect("]")
        else:
            self.next()                        # the type name
        if self.peek()[1] == "!":
            self.next()

    def _parse_directives(self) -> None:
        """Consume `@name(args?)*` and discard: directives never lower a price,
        so @skip/@include are priced as included (a safe upper bound)."""
        while self.peek()[1] == "@":
            self.next()
            self.next()                        # directive name
            if self.peek()[1] == "(":
                self.parse_args()

    # ---- selections -------------------------------------------------------

    def parse_selection_set(self) -> list:
        sels = []
        while True:
            k, v = self.peek()
            if v == "}":
                self.next()
                break
            if k is None:
                break
            if k == "spread":
                self.next()
                sels.extend(self._parse_after_spread())
            else:
                sels.append(self.parse_field())
        return sels

    def _parse_after_spread(self) -> list:
        k, v = self.peek()
        if v == "on":                          # inline fragment with condition
            self.next()
            _, cond = self.next()
            self._parse_directives()
            self.expect("{")
            children = self.parse_selection_set()
            return [Selection(name=f"{_ON_PREFIX}{cond}", on=cond, children=children)]
        if v == "{":                           # condition-less: same type, splice
            self.next()
            return self.parse_selection_set()
        if k == "name":                        # named fragment spread
            self.next()
            self._parse_directives()
            return [_Spread(v)]
        if v == "@":                           # `... @dir { }` condition-less
            self._parse_directives()
            self.expect("{")
            return self.parse_selection_set()
        raise ValueError(f"Unexpected token {v!r} after '...'")

    def parse_field(self) -> Selection:
        k, name = self.next()
        if self.peek()[1] == ":":              # alias: keep the FIELD name; the
            self.next()                        # response key never changes cost
            _, name = self.next()
        args = {}
        if self.peek()[1] == "(":
            args = self.parse_args()
        self._parse_directives()
        children = []
        if self.peek()[1] == "{":
            self.next()
            children = self.parse_selection_set()
        return Selection(name=name, args=args, children=children)

    def parse_args(self) -> dict:
        self.expect("(")
        args = {}
        while True:
            k, v = self.peek()
            if v == ")":
                self.next()
                break
            if v == ",":
                self.next()
                continue
            _, argname = self.next()
            self.expect(":")
            args[argname] = self.parse_value()
        return args

    def parse_value(self):
        k, v = self.next()
        if k == "string":
            return v[1:-1]
        if k == "number":
            return float(v) if "." in v else int(v)
        if v == "$":
            _, vname = self.next()
            return _Var(vname)
        if v == "[":
            out = []
            while True:
                pk, pv = self.peek()
                if pv == "]":
                    self.next()
                    return out
                if pv == ",":
                    self.next()
                    continue
                out.append(self.parse_value())
        if v == "{":
            obj = {}
            while True:
                pk, pv = self.peek()
                if pv == "}":
                    self.next()
                    return obj
                if pv == ",":
                    self.next()
                    continue
                _, fieldname = self.next()
                self.expect(":")
                obj[fieldname] = self.parse_value()
        if k == "name":
            if v == "true":
                return True
            if v == "false":
                return False
            if v == "null":
                return None
            return _Enum(v)
        raise ValueError(f"Unexpected value token {v!r}")


# --------------------------------------------------------------------------
# Post-parse: fragment expansion + variable substitution
# --------------------------------------------------------------------------

def _expand_spreads(sels: list, fragments: dict, stack: tuple) -> list[Selection]:
    out: list[Selection] = []
    for s in sels:
        if isinstance(s, _Spread):
            if s.name not in fragments:
                raise ValueError(f"unknown fragment {s.name!r}")
            if s.name in stack:
                raise ValueError(
                    f"fragment cycle: {' -> '.join((*stack, s.name))}")
            cond, children = fragments[s.name]
            out.append(Selection(
                name=f"{_ON_PREFIX}{cond}", on=cond,
                children=_expand_spreads(children, fragments, (*stack, s.name))))
        else:
            s.children = _expand_spreads(s.children, fragments, stack)
            out.append(s)
    return out


def _subst(value, values: dict):
    """Replace _Var inside container literals (a missing one becomes null;
    a missing TOP-LEVEL _Var drops the argument instead, in _resolve_vars)."""
    if isinstance(value, _Var):
        return values.get(value.name)
    if isinstance(value, list):
        return [_subst(x, values) for x in value]
    if isinstance(value, dict):
        return {k: _subst(x, values) for k, x in value.items()}
    return value


def _resolve_vars(sels: list[Selection], values: dict) -> None:
    for s in sels:
        for k in list(s.args):
            v = s.args[k]
            if isinstance(v, _Var):
                if v.name in values:
                    s.args[k] = values[v.name]
                else:
                    # Undeclared and unprovided: drop the arg. An absent arg is
                    # the ceiling-safe reading (worst-case bound applies).
                    del s.args[k]
            elif isinstance(v, (list, dict)):
                s.args[k] = _subst(v, values)
        _resolve_vars(s.children, values)


def parse_query(s: str, variables: dict | None = None) -> list[Selection]:
    """Parse an executable document (one operation + any fragment definitions)
    into root selections. ``variables`` supplies values for ``$name`` usages;
    declared defaults fill the gaps; a variable with neither loses its
    argument, so the ceiling's worst-case bound applies to that field."""
    ops, fragments, defaults = _Parser(_tokenize(s)).parse_document()
    sels = _expand_spreads(ops, fragments, ())
    _resolve_vars(sels, {**defaults, **(variables or {})})
    return sels


def count_fields(selections: list[Selection]) -> int:
    """Total selection nodes in the query document (static, not fanout-scaled).
    Proxy for per-request parse/validate/serialize cost, which grows with the
    size of the query text regardless of how much data comes back. Fragment
    nodes count 0 (pure grouping); their children count once per spread site,
    so a query desugared by hand prices identically to its sugared form."""
    n = 0
    for s in selections:
        n += (0 if s.on is not None else 1) + count_fields(s.children)
    return n
