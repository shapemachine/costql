"""ASGI app — Strawberry schema mounted with GraphiQL.

Fresh `RequestTracer` + `LoaderRegistry` per request (per-request caches reset each
request; the genre singleton persists — reset via `loaders.reset_caches()`). One
shared `TMDBClient` and `AnthropicSummarizer` across requests.

Run: `uvicorn app.server:app`  (env: COSTQL_TIER, COSTQL_TRACE_SINK, TMDB_*, ANTHROPIC_API_KEY)
"""
from __future__ import annotations

from strawberry.asgi import GraphQL

from .enrich import AnthropicSummarizer
from .loaders import LoaderRegistry
from .schema import Context, schema
from .tmdb import TMDBClient
from .tracing import RequestTracer

# Real TMDB + Anthropic. This server owns no data and never fabricates upstream
# responses — the whole point of building it is to measure costQL against a real API.
_client = TMDBClient()
_summarizer = AnthropicSummarizer()


class TMDBGraphQL(GraphQL):
    async def get_context(self, request, response=None) -> Context:
        return Context(loaders=LoaderRegistry(_client, _summarizer),
                       tracer=RequestTracer())


try:
    app = TMDBGraphQL(schema, graphql_ide="graphiql")
except TypeError:  # older strawberry
    app = TMDBGraphQL(schema, graphiql=True)
