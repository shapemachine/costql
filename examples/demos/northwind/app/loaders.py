"""Batching, per-request-caching DataLoaders over real SQLite (the sharing signal).

Each loader coalesces every `.load(key)` requested within one event-loop tick into
ONE real `SELECT ... WHERE <col> IN (?, ?, …)` against `northwind.db`, and caches
results per request. So N logical loads of the same/overlapping keys → 1 SQL call
+ (N − distinct) coalesced/cached hits — exactly the DataLoader win, and exactly
the T3 sharing signal costQL observes.

Two shapes:
  * identity loader — key = a primary id, value = one row (category/product/order/…).
  * group loader    — key = a parent id, value = a LIST of child rows
                      (Order→details, Category→products, Customer→orders, …).

Both report to the RequestTracer: every `.load` is a `requested_key` (a cache hit
when the key was already seen this request); every real SELECT is a `batch_call`
over `batched_keys` distinct keys, timed into `work_ms`. Under heavy sharing
(few categories / products fanned from many order-lines) `requested_keys` climbs
far above `batch_calls`.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Callable

from .tracing import RequestTracer


class BatchLoader:
    def __init__(self, tracer: RequestTracer, loader_id: str,
                 fetch: Callable[[list], dict], *, group: bool = False):
        self.tracer = tracer
        self.loader_id = loader_id
        self._fetch = fetch                 # distinct_keys -> {key: value}
        self._group = group                 # value default: [] (group) vs None (identity)
        self._cache: dict[Any, Any] = {}
        self._seen: set = set()
        self._queue: list[tuple[Any, asyncio.Future]] = []
        self._dispatch_scheduled = False

    async def load(self, key: Any, resolver_id: str) -> Any:
        cache_hit = key in self._seen
        self._seen.add(key)
        self.tracer.record_request(self.loader_id, resolver_id, cache_hit)
        if key in self._cache:
            return self._cache[key]
        loop = asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()
        self._queue.append((key, fut))
        if not self._dispatch_scheduled:
            self._dispatch_scheduled = True
            loop.call_soon(lambda: asyncio.ensure_future(self._dispatch()))
        return await fut

    async def load_many(self, keys: list, resolver_id: str) -> list:
        return await asyncio.gather(*(self.load(k, resolver_id) for k in keys))

    async def _dispatch(self) -> None:
        self._dispatch_scheduled = False
        batch = self._queue
        self._queue = []
        futs: dict[Any, list[asyncio.Future]] = {}
        distinct: list = []
        seen: set = set()
        for k, f in batch:
            futs.setdefault(k, []).append(f)
            if k not in seen and k not in self._cache:
                seen.add(k)
                distinct.append(k)
        if distinct:
            t0 = time.perf_counter()
            result = self._fetch(distinct)          # ONE real SELECT ... IN (...)
            dt = (time.perf_counter() - t0) * 1000.0
            self.tracer.record_batch(self.loader_id, len(distinct), dt)
            for k in distinct:
                self._cache[k] = result.get(k, [] if self._group else None)
        default = [] if self._group else None
        for k, fs in futs.items():
            val = self._cache.get(k, default)
            for f in fs:
                if not f.done():
                    f.set_result(val)


# ---- column projections (real columns, no huge Picture BLOB) ----------------

CATEGORY_COLS = "CategoryID, CategoryName, Description"
PRODUCT_COLS = ("ProductID, ProductName, UnitPrice, CategoryID, SupplierID, "
                "UnitsInStock, Discontinued")
ORDER_COLS = "OrderID, CustomerID, EmployeeID, OrderDate, ShipCountry, Freight"
DETAIL_COLS = "OrderID, ProductID, UnitPrice, Quantity, Discount"
CUSTOMER_COLS = "CustomerID, CompanyName, ContactName, Country, City"
SUPPLIER_COLS = "SupplierID, CompanyName, Country, City"


def _in_clause(n: int) -> str:
    return ",".join("?" * n)


class LoaderRegistry:
    """Fresh per request → per-request caches reset automatically."""

    def __init__(self, conn, tracer: RequestTracer):
        self.conn = conn
        self.tracer = tracer

        def identity(loader_id, table, id_col, cols):
            def fetch(keys):
                sql = f'SELECT {cols} FROM {table} WHERE {id_col} IN ({_in_clause(len(keys))})'
                rows = conn.execute(sql, keys).fetchall()
                return {r[id_col]: dict(r) for r in rows}
            return BatchLoader(tracer, loader_id, fetch, group=False)

        def group(loader_id, table, parent_col, cols):
            def fetch(keys):
                sql = f'SELECT {cols} FROM {table} WHERE {parent_col} IN ({_in_clause(len(keys))})'
                rows = conn.execute(sql, keys).fetchall()
                out: dict[Any, list] = {k: [] for k in keys}
                for r in rows:
                    out.setdefault(r[parent_col], []).append(dict(r))
                return out
            return BatchLoader(tracer, loader_id, fetch, group=True)

        # identity loaders (primary-key hubs — the shared entities)
        self.category = identity("categories", "Categories", "CategoryID", CATEGORY_COLS)
        self.product = identity("products", "Products", "ProductID", PRODUCT_COLS)
        self.order = identity("orders", "Orders", "OrderID", ORDER_COLS)
        self.customer = identity("customers", "Customers", "CustomerID", CUSTOMER_COLS)
        self.supplier = identity("suppliers", "Suppliers", "SupplierID", SUPPLIER_COLS)

        # group (one-to-many) loaders
        self.details_by_order = group("order_details:byOrder", '"Order Details"',
                                      "OrderID", DETAIL_COLS)
        self.products_by_category = group("products:byCategory", "Products",
                                          "CategoryID", PRODUCT_COLS)
        self.products_by_supplier = group("products:bySupplier", "Products",
                                          "SupplierID", PRODUCT_COLS)
        self.orders_by_customer = group("orders:byCustomer", "Orders",
                                        "CustomerID", ORDER_COLS)
        self.details_by_product = group("order_details:byProduct", '"Order Details"',
                                        "ProductID", DETAIL_COLS)
