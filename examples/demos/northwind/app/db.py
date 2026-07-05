"""Thin SQLite access over the REAL Northwind database.

No ORM, no cache of our own — every read is a real `SELECT` against the on-disk
`northwind.db` (the jpwhite3/northwind-SQLite3 reference dataset: 8 categories,
77 products, 16k orders, 609k order-details). The point of this demo is to measure
costQL against genuine DB query work, so nothing here fabricates rows.

A fresh connection is opened per request (cheap, and keeps SQLite objects on one
thread). Only the `SELECT` execution itself is timed by the caller (connection
open is not counted as query work).
"""
from __future__ import annotations

import os
import sqlite3
from typing import Any

DB_PATH = os.environ.get(
    "NORTHWIND_DB",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "northwind.db"),
)


def connect() -> sqlite3.Connection:
    # read-only URI so the demo can never mutate the reference dataset.
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def rows_to_dicts(rows) -> list[dict[str, Any]]:
    return [dict(r) for r in rows]
