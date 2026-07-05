"""Strawberry schema — a thin GraphQL passthrough over the real Northwind SQLite DB.

Every resolver does a real SQL read through a batching DataLoader (loaders.py) and
records its invocation into the RequestTracer. The edges are deliberately shaped for
HEAVY sharing: the graph funnels many requests onto a tiny set of hub entities —
only 8 categories and 77 products — so one query re-requests the same few rows
dozens–hundreds of times and the loaders coalesce them:

    Order → details → Product → Category            (line-items → 77 products → 8 categories)
    Category → products → Category                  (re-entry hub)
    Product → orders (via order-details) → …         (product ↔ order many-to-many)

`CostTraceExtension` publishes the request's `cost_trace` into `response.extensions`
so costQL's engine reads the sharing signal over HTTP — the seam that makes this a
T3-capable calibration target (identical role to tmdb-demo's extension).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import strawberry
from strawberry.extensions import SchemaExtension

from .loaders import LoaderRegistry
from .tracing import RequestTracer


@dataclass
class Context:
    loaders: LoaderRegistry
    tracer: RequestTracer


def _ctx(info: strawberry.Info) -> Context:
    return info.context


# ---- object types -----------------------------------------------------------


@strawberry.type
class Category:
    id: strawberry.ID
    raw: strawberry.Private[dict]

    @strawberry.field
    def name(self) -> str:
        return self.raw.get("CategoryName") or ""

    @strawberry.field
    def description(self) -> Optional[str]:
        return self.raw.get("Description")

    @strawberry.field
    async def products(self, info: strawberry.Info, first: int = 20) -> list["Product"]:
        ctx = _ctx(info)
        ctx.tracer.record_invocation("Category.products")
        rows = await ctx.loaders.products_by_category.load(int(self.id), "Category.products")
        return [_product(r) for r in rows[:first]]


@strawberry.type
class Supplier:
    id: strawberry.ID
    raw: strawberry.Private[dict]

    @strawberry.field
    def name(self) -> str:
        return self.raw.get("CompanyName") or ""

    @strawberry.field
    def country(self) -> Optional[str]:
        return self.raw.get("Country")

    @strawberry.field
    async def products(self, info: strawberry.Info, first: int = 20) -> list["Product"]:
        ctx = _ctx(info)
        ctx.tracer.record_invocation("Supplier.products")
        rows = await ctx.loaders.products_by_supplier.load(int(self.id), "Supplier.products")
        return [_product(r) for r in rows[:first]]


@strawberry.type
class Product:
    id: strawberry.ID
    raw: strawberry.Private[dict]

    async def _core(self, ctx) -> dict:
        if "CategoryID" not in self.raw:
            data = await ctx.loaders.product.load(int(self.id), "Product._core")
            if data:
                self.raw.update(data)
        return self.raw

    @strawberry.field
    def name(self) -> str:
        return self.raw.get("ProductName") or ""

    @strawberry.field
    def unit_price(self) -> Optional[float]:
        v = self.raw.get("UnitPrice")
        return float(v) if v is not None else None

    @strawberry.field
    async def category(self, info: strawberry.Info) -> Optional["Category"]:
        ctx = _ctx(info)
        ctx.tracer.record_invocation("Product.category")
        raw = await self._core(ctx)
        cid = raw.get("CategoryID")
        if cid is None:
            return None
        data = await ctx.loaders.category.load(int(cid), "Product.category")
        return Category(id=str(cid), raw=dict(data or {"CategoryID": cid}))

    @strawberry.field
    async def supplier(self, info: strawberry.Info) -> Optional["Supplier"]:
        ctx = _ctx(info)
        ctx.tracer.record_invocation("Product.supplier")
        raw = await self._core(ctx)
        sid = raw.get("SupplierID")
        if sid is None:
            return None
        data = await ctx.loaders.supplier.load(int(sid), "Product.supplier")
        return Supplier(id=str(sid), raw=dict(data or {"SupplierID": sid}))

    @strawberry.field
    async def orders(self, info: strawberry.Info, first: int = 20) -> list["Order"]:
        # product ↔ order many-to-many, via order-details. Re-uses the shared
        # `order` identity loader → orders coalesce across products.
        ctx = _ctx(info)
        ctx.tracer.record_invocation("Product.orders")
        details = await ctx.loaders.details_by_product.load(int(self.id), "Product.orders")
        oids = list({d["OrderID"] for d in details})[:first]
        rows = await ctx.loaders.order.load_many(oids, "Product.orders")
        return [_order(r) for r in rows if r]


@strawberry.type
class OrderDetail:
    raw: strawberry.Private[dict]

    @strawberry.field
    def quantity(self) -> int:
        return int(self.raw.get("Quantity") or 0)

    @strawberry.field
    def unit_price(self) -> Optional[float]:
        v = self.raw.get("UnitPrice")
        return float(v) if v is not None else None

    @strawberry.field
    def discount(self) -> Optional[float]:
        v = self.raw.get("Discount")
        return float(v) if v is not None else None

    @strawberry.field
    async def product(self, info: strawberry.Info) -> Optional["Product"]:
        ctx = _ctx(info)
        ctx.tracer.record_invocation("OrderDetail.product")
        pid = self.raw.get("ProductID")
        if pid is None:
            return None
        data = await ctx.loaders.product.load(int(pid), "OrderDetail.product")
        return Product(id=str(pid), raw=dict(data or {"ProductID": pid}))


@strawberry.type
class Order:
    id: strawberry.ID
    raw: strawberry.Private[dict]

    @strawberry.field
    def order_date(self) -> Optional[str]:
        return self.raw.get("OrderDate")

    @strawberry.field
    def ship_country(self) -> Optional[str]:
        return self.raw.get("ShipCountry")

    @strawberry.field
    async def details(self, info: strawberry.Info, first: int = 10) -> list["OrderDetail"]:
        ctx = _ctx(info)
        ctx.tracer.record_invocation("Order.details")
        rows = await ctx.loaders.details_by_order.load(int(self.id), "Order.details")
        return [OrderDetail(raw=r) for r in rows[:first]]

    @strawberry.field
    async def customer(self, info: strawberry.Info) -> Optional["Customer"]:
        ctx = _ctx(info)
        ctx.tracer.record_invocation("Order.customer")
        cid = self.raw.get("CustomerID")
        if cid is None:
            return None
        data = await ctx.loaders.customer.load(cid, "Order.customer")
        return Customer(id=str(cid), raw=dict(data or {"CustomerID": cid}))


@strawberry.type
class Customer:
    id: strawberry.ID
    raw: strawberry.Private[dict]

    @strawberry.field
    def name(self) -> str:
        return self.raw.get("CompanyName") or ""

    @strawberry.field
    def country(self) -> Optional[str]:
        return self.raw.get("Country")

    @strawberry.field
    async def orders(self, info: strawberry.Info, first: int = 20) -> list["Order"]:
        ctx = _ctx(info)
        ctx.tracer.record_invocation("Customer.orders")
        rows = await ctx.loaders.orders_by_customer.load(str(self.id), "Customer.orders")
        return [_order(r) for r in rows[:first]]


# ---- node builders ----------------------------------------------------------


def _product(r: dict) -> Product:
    return Product(id=str(r["ProductID"]), raw=dict(r))


def _order(r: dict) -> Order:
    return Order(id=str(r["OrderID"]), raw=dict(r))


# ---- root query -------------------------------------------------------------


@strawberry.type
class Query:
    @strawberry.field
    async def category(self, info: strawberry.Info, id: strawberry.ID) -> Optional[Category]:
        ctx = _ctx(info)
        ctx.tracer.record_invocation("Query.category")
        data = await ctx.loaders.category.load(int(id), "Query.category")
        return Category(id=str(id), raw=dict(data or {"CategoryID": int(id)})) if data else None

    @strawberry.field
    async def product(self, info: strawberry.Info, id: strawberry.ID) -> Optional[Product]:
        ctx = _ctx(info)
        ctx.tracer.record_invocation("Query.product")
        data = await ctx.loaders.product.load(int(id), "Query.product")
        return Product(id=str(id), raw=dict(data or {"ProductID": int(id)})) if data else None

    @strawberry.field
    async def order(self, info: strawberry.Info, id: strawberry.ID) -> Optional[Order]:
        ctx = _ctx(info)
        ctx.tracer.record_invocation("Query.order")
        data = await ctx.loaders.order.load(int(id), "Query.order")
        return Order(id=str(id), raw=dict(data or {"OrderID": int(id)})) if data else None

    @strawberry.field
    async def customer(self, info: strawberry.Info, id: strawberry.ID) -> Optional[Customer]:
        ctx = _ctx(info)
        ctx.tracer.record_invocation("Query.customer")
        data = await ctx.loaders.customer.load(str(id), "Query.customer")
        return Customer(id=str(id), raw=dict(data or {"CustomerID": str(id)})) if data else None

    @strawberry.field
    async def supplier(self, info: strawberry.Info, id: strawberry.ID) -> Optional[Supplier]:
        ctx = _ctx(info)
        ctx.tracer.record_invocation("Query.supplier")
        data = await ctx.loaders.supplier.load(int(id), "Query.supplier")
        return Supplier(id=str(id), raw=dict(data or {"SupplierID": int(id)})) if data else None

    @strawberry.field
    async def categories(self, info: strawberry.Info) -> list[Category]:
        ctx = _ctx(info)
        ctx.tracer.record_invocation("Query.categories")
        rows = ctx.loaders.conn.execute(
            "SELECT CategoryID, CategoryName, Description FROM Categories").fetchall()
        # the root list is a single scan; its per-row hydration is free (in the row)
        return [Category(id=str(r["CategoryID"]), raw=dict(r)) for r in rows]

    @strawberry.field
    async def products(self, info: strawberry.Info, first: int = 20) -> list[Product]:
        ctx = _ctx(info)
        ctx.tracer.record_invocation("Query.products")
        rows = ctx.loaders.conn.execute(
            f"SELECT {_root_product_cols()} FROM Products ORDER BY ProductID LIMIT ?",
            (first,)).fetchall()
        return [_product(dict(r)) for r in rows]

    @strawberry.field
    async def orders(self, info: strawberry.Info, first: int = 20) -> list[Order]:
        ctx = _ctx(info)
        ctx.tracer.record_invocation("Query.orders")
        rows = ctx.loaders.conn.execute(
            f"SELECT {_root_order_cols()} FROM Orders ORDER BY OrderID LIMIT ?",
            (first,)).fetchall()
        return [_order(dict(r)) for r in rows]

    @strawberry.field
    async def customers(self, info: strawberry.Info, first: int = 20) -> list[Customer]:
        ctx = _ctx(info)
        ctx.tracer.record_invocation("Query.customers")
        rows = ctx.loaders.conn.execute(
            "SELECT CustomerID, CompanyName, ContactName, Country, City "
            "FROM Customers ORDER BY CustomerID LIMIT ?", (first,)).fetchall()
        return [Customer(id=str(r["CustomerID"]), raw=dict(r)) for r in rows]


def _root_product_cols() -> str:
    from .loaders import PRODUCT_COLS
    return PRODUCT_COLS


def _root_order_cols() -> str:
    from .loaders import ORDER_COLS
    return ORDER_COLS


# ---- extensions -------------------------------------------------------------


class TracerLifecycle(SchemaExtension):
    async def on_execute(self):
        yield
        ctx = self.execution_context.context
        tracer = getattr(ctx, "tracer", None)
        if tracer is not None:
            tracer.finish()


class CostTraceExtension(SchemaExtension):
    """Publish the request's `cost_trace` into `response.extensions` so costQL's
    engine (costql/harness.py) can read the sharing signal over HTTP."""

    def get_results(self) -> dict:
        ctx = self.execution_context.context
        tracer = getattr(ctx, "tracer", None)
        if tracer is None:
            return {}
        return {"cost_trace": tracer.cost_trace()}


schema = strawberry.Schema(query=Query, extensions=[TracerLifecycle, CostTraceExtension])
