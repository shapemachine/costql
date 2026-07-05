"""Query IR — a small selection tree that both the generator emits and the
Pricer consumes, plus a serializer to a GraphQL string and a minimal parser
so the Pricer can also accept an arbitrary GraphQL query string.

This is deliberately a subset: named/anonymous query operations, fields with
scalar arguments (int, float, string, bool, enum, null), and nested selection
sets. Sufficient for pre-execution static analysis, which is all the Pricer
needs — it never executes the query.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Selection:
    name: str
    args: dict[str, Any] = field(default_factory=dict)
    children: list[Selection] = field(default_factory=list)

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
    # string / ID
    escaped = str(v).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


@dataclass
class _Enum:
    name: str


def serialize(root_selections: list[Selection], op_name: str = "") -> str:
    def render(sel: Selection, indent: int) -> str:
        pad = "  " * indent
        args = ""
        if sel.args:
            args = "(" + ", ".join(f"{k}: {_fmt_value(v)}" for k, v in sel.args.items()) + ")"
        if sel.children:
            inner = "\n".join(render(c, indent + 1) for c in sel.children)
            return f"{pad}{sel.name}{args} {{\n{inner}\n{pad}}}"
        return f"{pad}{sel.name}{args}"

    body = "\n".join(render(s, 1) for s in root_selections)
    header = f"query {op_name} " if op_name else "query "
    return f"{header}{{\n{body}\n}}"


# --------------------------------------------------------------------------
# Minimal parser GraphQL string -> [Selection]  (top-level = root fields)
# --------------------------------------------------------------------------

_TOKEN = re.compile(r"""
    (?P<ws>\s+|\#[^\n]*)
  | (?P<punct>[{}():,])
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

    def parse_operation(self) -> list[Selection]:
        k, v = self.peek()
        if v in ("query", "mutation", "subscription"):
            self.next()
            # optional operation name
            if self.peek()[0] == "name":
                self.next()
        self.expect("{")
        sels = self.parse_selection_set()
        return sels

    def parse_selection_set(self) -> list[Selection]:
        sels = []
        while True:
            k, v = self.peek()
            if v == "}":
                self.next()
                break
            if k is None:
                break
            sels.append(self.parse_field())
        return sels

    def parse_field(self) -> Selection:
        k, name = self.next()
        args = {}
        if self.peek()[1] == "(":
            args = self.parse_args()
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
        if k == "name":
            if v == "true":
                return True
            if v == "false":
                return False
            if v == "null":
                return None
            return _Enum(v)
        raise ValueError(f"Unexpected value token {v!r}")


def parse_query(s: str) -> list[Selection]:
    return _Parser(_tokenize(s)).parse_operation()


def count_fields(selections: list[Selection]) -> int:
    """Total selection nodes in the query document (static, not fanout-scaled).
    Proxy for per-request parse/validate/serialize cost, which grows with the
    size of the query text regardless of how much data comes back."""
    n = 0
    for s in selections:
        n += 1 + count_fields(s.children)
    return n
