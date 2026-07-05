"""Strawberry schema: types + resolvers, mirroring the spec SDL.

Every resolver hydrates through the DataLoaders and emits a `ResolverTrace`. The
partial-hydration rule (spec) is enforced here: a field present in a node's stub is
free (no trace-with-call); an absent core field triggers `ensure_core` (the N+1).
Re-entering an already-seen node id shows up as `cache_hit` (the T3 sharing signal).

Node objects carry:
  raw:    accumulated known fields (stub → merged core)
  origin: the resolver_id that produced this node (its children's parent edge)
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

import strawberry
from strawberry.extensions import SchemaExtension

from .enrich import chemistry_score
from .loaders import LoaderRegistry
from .tracing import RequestTracer, ResolverTrace


# ---- request context --------------------------------------------------------


@dataclass
class Context:
    loaders: LoaderRegistry
    tracer: RequestTracer


def _ctx(info: strawberry.Info) -> Context:
    return info.context


def emit(ctx: Context, resolver_id: str, parent_resolver_id: Optional[str],
         parent_type: Optional[str], meta, *, result_size: Optional[int] = None,
         local_compute_ms: float = 0.0, host: Optional[str] = None) -> None:
    ctx.tracer.record(ResolverTrace(
        resolver_id=resolver_id,
        parent_resolver_id=parent_resolver_id,
        parent_type=parent_type,
        downstream_calls=meta.downstream_calls if meta else 0,
        downstream_latency_ms=meta.latency_ms if meta else None,
        local_compute_ms=local_compute_ms,
        result_size=result_size,
        downstream_host=host if host is not None else (meta.host if meta else None),
        batch_group=meta.batch_group if meta else None,
        batch_key=meta.batch_key if meta else None,
        cache_hit=meta.cache_hit if meta else None,
        cache_key=(f"{meta.batch_group}:{meta.batch_key}" if meta else None),
    ))


def _year(raw: dict) -> Optional[int]:
    rd = raw.get("release_date") or raw.get("first_air_date")
    if rd and len(rd) >= 4 and rd[:4].isdigit():
        return int(rd[:4])
    ry = raw.get("releaseYear")
    return int(ry) if isinstance(ry, int) else None


# ---- enums ------------------------------------------------------------------


@strawberry.enum
class TimeWindow(Enum):
    DAY = "day"
    WEEK = "week"


@strawberry.enum
class MovieSort(Enum):
    POPULARITY_DESC = "POPULARITY_DESC"
    REVENUE_DESC = "REVENUE_DESC"
    RELEASE_DESC = "RELEASE_DESC"
    VOTE_DESC = "VOTE_DESC"


# ---- object types -----------------------------------------------------------


@strawberry.type
class Genre:
    id: strawberry.ID
    raw: strawberry.Private[dict]
    origin: strawberry.Private[Optional[str]]

    @strawberry.field
    async def name(self, info: strawberry.Info) -> str:
        ctx = _ctx(info)
        if self.raw.get("name"):
            return self.raw["name"]
        g, meta = await ctx.loaders.genre.load(int(self.id))
        emit(ctx, "Genre.name", self.origin, "Genre", meta, result_size=1)
        if g:
            self.raw.update(g)
        return self.raw.get("name") or ""

    @strawberry.field
    async def movies(self, info: strawberry.Info, page: int = 1) -> list["Movie"]:
        ctx = _ctx(info)
        key = (int(self.id), None, "POPULARITY_DESC", page)
        data, meta = await ctx.loaders.discover.load(key)
        stubs = data.get("results", [])
        emit(ctx, "Genre.movies", self.origin, "Genre", meta, result_size=len(stubs))
        return [_movie(s, "Genre.movies") for s in stubs]


@strawberry.type
class Collection:
    id: strawberry.ID
    raw: strawberry.Private[dict]
    origin: strawberry.Private[Optional[str]]

    @strawberry.field
    async def name(self, info: strawberry.Info) -> str:
        return self.raw.get("name") or ""

    @strawberry.field
    async def parts(self, info: strawberry.Info) -> list["Movie"]:
        ctx = _ctx(info)
        data, meta = await ctx.loaders.collection.load(int(self.id))
        stubs = data.get("parts", [])
        emit(ctx, "Collection.parts", self.origin, "Collection", meta,
             result_size=len(stubs))
        return [_movie(s, "Collection.parts") for s in stubs]


@strawberry.type
class Person:
    id: strawberry.ID
    raw: strawberry.Private[dict]
    origin: strawberry.Private[Optional[str]]

    async def _core_field(self, ctx: Context, key: str, resolver_id: str):
        if key in self.raw and self.raw[key] not in (None, ""):
            return self.raw[key]
        data, meta = await ctx.loaders.person.ensure_core(int(self.id))
        emit(ctx, resolver_id, self.origin, "Person", meta, result_size=1)
        self.raw.update(data)
        return self.raw.get(key)

    @strawberry.field
    async def name(self, info: strawberry.Info) -> str:
        v = await self._core_field(_ctx(info), "name", "Person.name")
        return v or ""

    @strawberry.field
    async def known_for_department(self, info: strawberry.Info) -> Optional[str]:
        return await self._core_field(_ctx(info), "known_for_department",
                                      "Person.knownForDepartment")

    @strawberry.field
    async def popularity(self, info: strawberry.Info) -> Optional[float]:
        v = await self._core_field(_ctx(info), "popularity", "Person.popularity")
        return float(v) if v is not None else None

    @strawberry.field
    async def filmography(self, info: strawberry.Info, limit: int = 20) -> list["Credit"]:
        ctx = _ctx(info)
        data, meta = await ctx.loaders.person_credits.load(int(self.id))
        # combined cast + crew: a person can appear in both for one movie, which is
        # exactly the repeat-id that Q6 dedups.
        entries = (data.get("cast", []) or []) + (data.get("crew", []) or [])
        entries = entries[:limit]
        emit(ctx, "Person.filmography", self.origin, "Person", meta,
             result_size=len(entries))
        return [_credit(e, "Person.filmography", movie_side=True,
                        person_id=int(self.id)) for e in entries]

    @strawberry.field
    async def known_for(self, info: strawberry.Info) -> list["Movie"]:
        ctx = _ctx(info)
        data, meta = await ctx.loaders.person_credits.load(int(self.id))
        cast = sorted(data.get("cast", []) or [],
                      key=lambda m: m.get("popularity", 0), reverse=True)[:10]
        emit(ctx, "Person.knownFor", self.origin, "Person", meta, result_size=len(cast))
        return [_movie(s, "Person.knownFor") for s in cast]


@strawberry.type
class Credit:
    raw: strawberry.Private[dict]
    origin: strawberry.Private[Optional[str]]
    # which side is the stub vs the parent context
    movie_side: strawberry.Private[bool]        # True: movie in raw, person is parent
    ctx_person_id: strawberry.Private[Optional[int]]
    ctx_movie_id: strawberry.Private[Optional[int]]

    @strawberry.field
    def character(self) -> Optional[str]:
        return self.raw.get("character")

    @strawberry.field
    def job(self) -> Optional[str]:
        return self.raw.get("job")

    @strawberry.field
    def department(self) -> Optional[str]:
        return self.raw.get("department") or self.raw.get("known_for_department")

    @strawberry.field
    def order(self) -> Optional[int]:
        o = self.raw.get("order")
        return int(o) if isinstance(o, int) else None

    @strawberry.field
    async def movie(self, info: strawberry.Info) -> "Movie":
        ctx = _ctx(info)
        if self.movie_side:
            mid = int(self.raw["id"])
            data, meta = ctx.loaders.movie.prime(mid, _movie_stub(self.raw))
            emit(ctx, "Credit.movie", self.origin, "Credit", meta, result_size=1)
            return Movie(id=str(mid), raw=data, origin="Credit.movie")
        mid = int(self.ctx_movie_id)
        data, meta = ctx.loaders.movie.prime(mid, {"id": mid})
        emit(ctx, "Credit.movie", self.origin, "Credit", meta, result_size=1)
        return Movie(id=str(mid), raw=data, origin="Credit.movie")

    @strawberry.field
    async def person(self, info: strawberry.Info) -> "Person":
        ctx = _ctx(info)
        if not self.movie_side:
            pid = int(self.raw["id"])
            data, meta = ctx.loaders.person.prime(pid, _person_stub(self.raw))
            emit(ctx, "Credit.person", self.origin, "Credit", meta, result_size=1)
            return Person(id=str(pid), raw=data, origin="Credit.person")
        pid = int(self.ctx_person_id)
        data, meta = ctx.loaders.person.prime(pid, {"id": pid})
        emit(ctx, "Credit.person", self.origin, "Credit", meta, result_size=1)
        return Person(id=str(pid), raw=data, origin="Credit.person")


@strawberry.type
class Movie:
    id: strawberry.ID
    raw: strawberry.Private[dict]
    origin: strawberry.Private[Optional[str]]

    async def _core_field(self, ctx: Context, key: str, resolver_id: str):
        if key in self.raw and self.raw[key] not in (None, ""):
            return self.raw[key]
        data, meta = await ctx.loaders.movie.ensure_core(int(self.id))
        emit(ctx, resolver_id, self.origin, "Movie", meta, result_size=1)
        self.raw.update(data)
        return self.raw.get(key)

    @strawberry.field
    async def title(self, info: strawberry.Info) -> str:
        v = await self._core_field(_ctx(info), "title", "Movie.title")
        return v or ""

    @strawberry.field
    async def release_year(self, info: strawberry.Info) -> Optional[int]:
        if _year(self.raw) is not None:
            return _year(self.raw)
        await self._core_field(_ctx(info), "release_date", "Movie.releaseYear")
        return _year(self.raw)

    @strawberry.field
    async def runtime(self, info: strawberry.Info) -> Optional[int]:
        v = await self._core_field(_ctx(info), "runtime", "Movie.runtime")
        return int(v) if v is not None else None

    @strawberry.field
    async def revenue(self, info: strawberry.Info) -> Optional[float]:
        v = await self._core_field(_ctx(info), "revenue", "Movie.revenue")
        return float(v) if v is not None else None

    @strawberry.field
    async def vote_average(self, info: strawberry.Info) -> Optional[float]:
        v = await self._core_field(_ctx(info), "vote_average", "Movie.voteAverage")
        return float(v) if v is not None else None

    @strawberry.field
    async def genres(self, info: strawberry.Info) -> list[Genre]:
        ctx = _ctx(info)
        gids = _genre_ids(self.raw)
        if gids is None:                    # core not yet loaded; pay for it
            data, meta = await ctx.loaders.movie.ensure_core(int(self.id))
            emit(ctx, "Movie.genres", self.origin, "Movie", meta,
                 result_size=None)
            self.raw.update(data)
            gids = _genre_ids(self.raw) or []
        out: list[Genre] = []
        for gid in gids:
            g, gmeta = await ctx.loaders.genre.load(int(gid))
            emit(ctx, "Movie.genres", self.origin, "Movie", gmeta, result_size=1)
            out.append(Genre(id=str(gid), raw=dict(g or {"id": gid}),
                             origin="Movie.genres"))
        return out

    @strawberry.field
    async def cast(self, info: strawberry.Info, limit: int = 20) -> list[Credit]:
        ctx = _ctx(info)
        data, meta = await ctx.loaders.credits.load(int(self.id))
        entries = (data.get("cast", []) or [])[:limit]
        emit(ctx, "Movie.cast", self.origin, "Movie", meta, result_size=len(entries))
        return [_credit(e, "Movie.cast", movie_side=False,
                        movie_id=int(self.id)) for e in entries]

    @strawberry.field
    async def crew(self, info: strawberry.Info, limit: int = 20) -> list[Credit]:
        ctx = _ctx(info)
        data, meta = await ctx.loaders.credits.load(int(self.id))
        entries = (data.get("crew", []) or [])[:limit]
        emit(ctx, "Movie.crew", self.origin, "Movie", meta, result_size=len(entries))
        return [_credit(e, "Movie.crew", movie_side=False,
                        movie_id=int(self.id)) for e in entries]

    @strawberry.field
    async def recommendations(self, info: strawberry.Info, page: int = 1) -> list["Movie"]:
        ctx = _ctx(info)
        data, meta = await ctx.loaders.recommendations.load((int(self.id), page))
        stubs = data.get("results", [])
        emit(ctx, "Movie.recommendations", self.origin, "Movie", meta,
             result_size=len(stubs))
        return [_movie(s, "Movie.recommendations") for s in stubs]

    @strawberry.field
    async def similar(self, info: strawberry.Info, page: int = 1) -> list["Movie"]:
        ctx = _ctx(info)
        data, meta = await ctx.loaders.similar.load((int(self.id), page))
        stubs = data.get("results", [])
        emit(ctx, "Movie.similar", self.origin, "Movie", meta, result_size=len(stubs))
        return [_movie(s, "Movie.similar") for s in stubs]

    @strawberry.field
    async def collection(self, info: strawberry.Info) -> Optional[Collection]:
        ctx = _ctx(info)
        btc = self.raw.get("belongs_to_collection")
        if btc is None and "belongs_to_collection" not in self.raw:
            data, meta = await ctx.loaders.movie.ensure_core(int(self.id))
            emit(ctx, "Movie.collection", self.origin, "Movie", meta, result_size=1)
            self.raw.update(data)
            btc = self.raw.get("belongs_to_collection")
        if not btc:
            return None
        return Collection(id=str(btc["id"]), raw=dict(btc), origin="Movie.collection")

    @strawberry.field
    async def chemistry_score(self, info: strawberry.Info, limit: int = 20) -> Optional[float]:
        ctx = _ctx(info)
        # reuse creditsLoader (shared with Movie.cast; no new upstream call). The
        # credits fetch is attributed to Movie.cast; this resolver's own trace is
        # pure local compute (downstream_calls=0); see #4.
        data, cmeta = await ctx.loaders.credits.load(int(self.id))
        emit(ctx, "Movie.cast", self.origin, "Movie", cmeta,
             result_size=min(limit, len(data.get("cast", []) or [])))
        cast = (data.get("cast", []) or [])[:limit]
        score, local_ms = chemistry_score(cast, limit)
        emit(ctx, "Movie.chemistryScore", self.origin, "Movie", None,
             result_size=len(cast), local_compute_ms=local_ms, host=None)
        return score

    @strawberry.field
    async def ai_summary(self, info: strawberry.Info) -> Optional[str]:
        ctx = _ctx(info)
        title = self.raw.get("title")
        if not title:
            data, _ = await ctx.loaders.movie.ensure_core(int(self.id))
            self.raw.update(data)
            title = self.raw.get("title") or ""
        summary, meta = await ctx.loaders.ai_summary.load(
            int(self.id), title, _year(self.raw))
        emit(ctx, "Movie.aiSummary", self.origin, "Movie", meta, result_size=1)
        return summary


# ---- node builders ----------------------------------------------------------


def _movie(stub: dict, origin: str) -> Movie:
    return Movie(id=str(stub["id"]), raw=dict(stub), origin=origin)


def _credit(entry: dict, origin: str, *, movie_side: bool,
            person_id: Optional[int] = None, movie_id: Optional[int] = None) -> Credit:
    return Credit(raw=dict(entry), origin=origin, movie_side=movie_side,
                  ctx_person_id=person_id, ctx_movie_id=movie_id)


def _movie_stub(raw: dict) -> dict:
    keep = ("id", "title", "release_date", "genre_ids", "vote_average",
            "runtime", "revenue", "popularity")
    return {k: raw[k] for k in keep if k in raw}


def _person_stub(raw: dict) -> dict:
    keep = ("id", "name", "known_for_department", "popularity")
    return {k: raw[k] for k in keep if k in raw}


def _genre_ids(raw: dict) -> Optional[list[int]]:
    if isinstance(raw.get("genre_ids"), list):
        return [int(g) for g in raw["genre_ids"]]
    if isinstance(raw.get("genres"), list):
        return [int(g["id"]) for g in raw["genres"] if "id" in g]
    return None


# ---- root query -------------------------------------------------------------


@strawberry.type
class Query:
    @strawberry.field
    async def movie(self, info: strawberry.Info, id: strawberry.ID) -> Optional[Movie]:
        return Movie(id=str(id), raw={"id": int(id)}, origin="Query.movie")

    @strawberry.field
    async def person(self, info: strawberry.Info, id: strawberry.ID) -> Optional[Person]:
        return Person(id=str(id), raw={"id": int(id)}, origin="Query.person")

    @strawberry.field
    async def genre(self, info: strawberry.Info, id: strawberry.ID) -> Optional[Genre]:
        return Genre(id=str(id), raw={"id": int(id)}, origin="Query.genre")

    @strawberry.field
    async def search_movies(self, info: strawberry.Info, query: str,
                            page: int = 1) -> list[Movie]:
        ctx = _ctx(info)
        data, meta = await ctx.loaders.search.load((query, page))
        stubs = data.get("results", [])
        emit(ctx, "Query.searchMovies", None, "Query", meta, result_size=len(stubs))
        return [_movie(s, "Query.searchMovies") for s in stubs]

    @strawberry.field
    async def trending_movies(self, info: strawberry.Info,
                              window: TimeWindow = TimeWindow.DAY,
                              page: int = 1) -> list[Movie]:
        ctx = _ctx(info)
        data, meta = await ctx.loaders.trending.load((window.value, page))
        stubs = data.get("results", [])
        emit(ctx, "Query.trendingMovies", None, "Query", meta, result_size=len(stubs))
        return [_movie(s, "Query.trendingMovies") for s in stubs]

    @strawberry.field
    async def discover_movies(self, info: strawberry.Info,
                             genre: Optional[strawberry.ID] = None,
                             year: Optional[int] = None,
                             sort_by: MovieSort = MovieSort.POPULARITY_DESC,
                             page: int = 1) -> list[Movie]:
        ctx = _ctx(info)
        key = (int(genre) if genre is not None else None, year, sort_by.value, page)
        data, meta = await ctx.loaders.discover.load(key)
        stubs = data.get("results", [])
        emit(ctx, "Query.discoverMovies", None, "Query", meta, result_size=len(stubs))
        return [_movie(s, "Query.discoverMovies") for s in stubs]


class TracerLifecycle(SchemaExtension):
    """Stamp the request wall time + flush traces when execution ends (BUILD §6:
    wall time is emitted at every tier)."""

    async def on_execute(self):
        yield
        ctx = self.execution_context.context
        tracer = getattr(ctx, "tracer", None)
        if tracer is not None:
            tracer.finish()


class CostTraceExtension(SchemaExtension):
    """Publish the request's T3 `cost_trace` into `response.extensions` so an
    external costQL adapter (costql/harness.py) can read the sharing signal
    over HTTP, the seam that makes this server a T3-capable calibration target."""

    def get_results(self) -> dict:
        ctx = self.execution_context.context
        tracer = getattr(ctx, "tracer", None)
        if tracer is None:
            return {}
        return {"cost_trace": tracer.cost_trace()}


schema = strawberry.Schema(query=Query,
                           extensions=[TracerLifecycle, CostTraceExtension])
