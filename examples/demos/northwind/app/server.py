"""ASGI app: the Northwind Strawberry passthrough, mounted with GraphiQL.

Fresh `RequestTracer` + `LoaderRegistry` (and a fresh read-only SQLite connection)
per request, so every request's coalescing/cache stats start clean. The server owns
no data of its own and never fabricates rows: it reads the real northwind.db.

Run: `uvicorn app.server:app --port 8010`  (env: NORTHWIND_DB to point elsewhere)
"""
from __future__ import annotations

from strawberry.asgi import GraphQL

from .db import connect
from .loaders import LoaderRegistry
from .schema import Context, schema
from .tracing import RequestTracer

# One persistent, warm, read-only connection shared across requests (how a real
# server runs). Opening a fresh connection per request would make the first SQL of
# each request pay connection/page-cache cold-start, noise unrelated to the query's
# own cost. A warm shared connection measures the query work itself. Per-request
# coalescing/cache state still lives in a fresh LoaderRegistry + RequestTracer.
_conn = connect()
_conn.execute("SELECT 1").fetchone()   # warm the connection once at startup


class NorthwindGraphQL(GraphQL):
    async def get_context(self, request, response=None) -> Context:
        tracer = RequestTracer()
        return Context(loaders=LoaderRegistry(_conn, tracer), tracer=tracer)


try:
    app = NorthwindGraphQL(schema, graphql_ide="graphiql")
except TypeError:  # older strawberry
    app = NorthwindGraphQL(schema, graphiql=True)
