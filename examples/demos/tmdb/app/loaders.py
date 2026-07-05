"""DataLoaders: the sharing signal (BUILD §5, spec "Field → endpoint → loader").

One loader per endpoint, keyed on `(endpoint, id)` (or `(id, page)` for paged). Each
provides DataLoader semantics (request coalescing + per-request cache) plus the
metadata the tracer needs (batch_group/key, cache_hit, per-call latency).

TMDB has no multi-id batch endpoint, so "batching" here = coalescing + per-request
cache: N logical loads of one key → 1 actual upstream call + (N−1) cache hits. From
the cost model's view that is identical to server-side batching.

`load()` / `prime()` / `ensure_core()` return `(value, LoadMeta)`; the *resolver*
turns `LoadMeta` (+ its own `result_size`) into a `ResolverTrace`. Keeping the trace
build in the resolver lets it stamp the size it actually touched (the size lever, #1).

`genreListLoader` is a cross-request singleton (BUILD §5 ⚠️). `reset_caches()` zeroes
it between coverage samples so measurement runs under controlled conditions (#4).
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

TMDB_HOST = "api.themoviedb.org"
ANTHROPIC_HOST = "api.anthropic.com"


@dataclass
class LoadMeta:
    downstream_calls: int                 # actual upstream calls after dedup (0|1)
    cache_hit: bool                       # id already materialized this request?
    batch_group: str                      # = endpoint
    batch_key: str                        # = id (or "id:page")
    latency_ms: Optional[float] = None    # this call's latency (None if none)
    host: str = TMDB_HOST


def _now_ms(t0: float) -> float:
    return (time.perf_counter() - t0) * 1000.0


class CoalescingLoader:
    """Fetch-per-endpoint loader with per-request coalescing + cache. Used for the
    list/credit/collection endpoints that always hit TMDB on a miss."""

    def __init__(self, batch_group: str, fetch: Callable[[Any], Awaitable[dict]]):
        self.batch_group = batch_group
        self._fetch = fetch
        self._cache: dict[Any, dict] = {}
        self._inflight: dict[Any, asyncio.Future] = {}

    async def load(self, key: Any) -> tuple[dict, LoadMeta]:
        bk = _bkstr(key)
        if key in self._cache:
            return self._cache[key], LoadMeta(0, True, self.batch_group, bk)
        if key in self._inflight:                       # coalesced concurrent load
            val = await self._inflight[key]
            return val, LoadMeta(0, True, self.batch_group, bk)
        fut: asyncio.Future = asyncio.ensure_future(self._fetch(key))
        self._inflight[key] = fut
        t0 = time.perf_counter()
        try:
            val = await fut
        except BaseException:
            self._inflight.pop(key, None)
            raise
        latency = _now_ms(t0)
        self._cache[key] = val
        self._inflight.pop(key, None)
        return val, LoadMeta(1, False, self.batch_group, bk, latency)


class NodeLoader:
    """Identity loader for core entities (movie/person). Two entry points:

    - `prime(id, stub)`: register a node hydrated *for free* from a parent doc
      (partial-hydration rule). No upstream call; emits the dedup signal
      (`cache_hit` on repeat ids; this is what makes Q3's re-entry observable).
    - `ensure_core(id)`: fetch the node's core endpoint the first time an absent
      core-only field is requested (the N+1 detonation), deduped per request.
    """

    def __init__(self, core_group: str, fetch_core: Callable[[int], Awaitable[dict]]):
        self.batch_group = core_group
        self._fetch_core = fetch_core
        self._data: dict[int, dict] = {}
        self._seen: set[int] = set()
        self._core: set[int] = set()
        self._inflight: dict[int, asyncio.Future] = {}

    def prime(self, id: int, stub: dict) -> tuple[dict, LoadMeta]:
        cache_hit = id in self._seen
        self._seen.add(id)
        cur = self._data.setdefault(id, {})
        for k, v in stub.items():           # don't clobber richer, already-known data
            if k not in cur or cur[k] in (None, "", []):
                cur[k] = v
        return cur, LoadMeta(0, cache_hit, self.batch_group, str(id))

    async def ensure_core(self, id: int) -> tuple[dict, LoadMeta]:
        bk = str(id)
        if id in self._core:
            return self._data[id], LoadMeta(0, True, self.batch_group, bk)
        if id in self._inflight:
            # The in-flight future resolves to the MERGED data (after _fetch_and_merge
            # populates self._data). Awaiting self._data[id] directly here would race:
            # a coalesced waiter can wake from the shared future before the owner
            # merges, so it must read the future's result, not the dict.
            data = await self._inflight[id]
            return data, LoadMeta(0, True, self.batch_group, bk)
        cache_hit = id in self._seen        # node was primed before we paid for core
        fut: asyncio.Future = asyncio.ensure_future(self._fetch_and_merge(id))
        self._inflight[id] = fut
        t0 = time.perf_counter()
        try:
            data = await fut
        except BaseException:
            self._inflight.pop(id, None)
            raise
        latency = _now_ms(t0)
        self._inflight.pop(id, None)
        return data, LoadMeta(1, cache_hit, self.batch_group, bk, latency)

    async def _fetch_and_merge(self, id: int) -> dict:
        core = await self._fetch_core(id)
        cur = self._data.setdefault(id, {})
        cur.update(core)
        self._core.add(id)
        self._seen.add(id)
        return cur

    def get(self, id: int) -> dict:
        return self._data.get(id, {})


# ---- genre list: cross-request singleton (BUILD §5 ⚠️) ----------------------

_GENRE_MAP: Optional[dict[int, dict]] = None


def reset_caches() -> None:
    """Zero cross-request state (the genre list) so coverage samples run under
    controlled conditions (BUILD §5 / DECISIONS #4)."""
    global _GENRE_MAP
    _GENRE_MAP = None


class GenreLoader:
    """Singleton genre list, keyed per-genre-id. One upstream call hydrates the
    whole closed ~19-element set; every genre lookup after that is a cache hit
    (the dense-cache-hit hub signal). Cross-request cached until `reset_caches()`."""

    batch_group = "/genre/movie/list"

    def __init__(self, fetch_list: Callable[[], Awaitable[dict]]):
        self._fetch_list = fetch_list
        self._seen: set[int] = set()
        self._list_fut: Optional[asyncio.Future] = None

    async def load(self, gid: int) -> tuple[Optional[dict], LoadMeta]:
        global _GENRE_MAP
        bk = str(gid)
        req_hit = gid in self._seen
        self._seen.add(gid)
        if _GENRE_MAP is not None:
            return _GENRE_MAP.get(int(gid)), LoadMeta(0, req_hit, self.batch_group, bk)
        if self._list_fut is None:
            self._list_fut = asyncio.ensure_future(self._fetch_list())
            t0 = time.perf_counter()
            data = await self._list_fut
            latency = _now_ms(t0)
            _GENRE_MAP = {g["id"]: g for g in data.get("genres", [])}
            return _GENRE_MAP.get(int(gid)), LoadMeta(1, req_hit, self.batch_group, bk, latency)
        await self._list_fut                 # another gid is already fetching the list
        return (_GENRE_MAP or {}).get(int(gid)), LoadMeta(0, req_hit, self.batch_group, bk)


class AiSummaryLoader:
    """Paid LLM summary, keyed/deduped on movie id per request (#6). Marks the
    Anthropic host so costQL auto-flags it external/paid; the fee itself never
    enters the trace."""

    batch_group = "anthropic:messages"

    def __init__(self, summarizer):
        self._summarizer = summarizer
        self._cache: dict[int, str] = {}
        self._inflight: dict[int, asyncio.Future] = {}

    async def load(self, movie_id: int, title: str,
                   release_year: Optional[int]) -> tuple[str, LoadMeta]:
        bk = str(movie_id)
        if movie_id in self._cache:
            return self._cache[movie_id], LoadMeta(0, True, self.batch_group, bk,
                                                   host=ANTHROPIC_HOST)
        if movie_id in self._inflight:
            val = await self._inflight[movie_id]
            return val, LoadMeta(0, True, self.batch_group, bk, host=ANTHROPIC_HOST)
        fut: asyncio.Future = asyncio.ensure_future(
            self._summarizer.summarize(title, release_year))
        self._inflight[movie_id] = fut
        t0 = time.perf_counter()
        try:
            val = await fut
        except BaseException:
            self._inflight.pop(movie_id, None)
            raise
        latency = _now_ms(t0)
        self._cache[movie_id] = val
        self._inflight.pop(movie_id, None)
        return val, LoadMeta(1, False, self.batch_group, bk, latency, host=ANTHROPIC_HOST)


def _bkstr(key: Any) -> str:
    if isinstance(key, tuple):
        return ":".join(str(k) for k in key)
    return str(key)


# ---- per-request registry ---------------------------------------------------

# TMDB sort_by strings for discover
SORT_BY = {
    "POPULARITY_DESC": "popularity.desc",
    "REVENUE_DESC": "revenue.desc",
    "RELEASE_DESC": "primary_release_date.desc",
    "VOTE_DESC": "vote_average.desc",
}


class LoaderRegistry:
    """Fresh per request → per-request caches reset automatically. Only the genre
    singleton survives across requests (reset via `reset_caches()`)."""

    def __init__(self, client, summarizer):
        c = client
        self.movie = NodeLoader("/movie/{id}", lambda i: c.get(f"/movie/{i}"))
        self.person = NodeLoader("/person/{id}", lambda i: c.get(f"/person/{i}"))
        self.credits = CoalescingLoader(
            "/movie/{id}/credits", lambda i: c.get(f"/movie/{i}/credits"))
        self.person_credits = CoalescingLoader(
            "/person/{id}/movie_credits", lambda i: c.get(f"/person/{i}/movie_credits"))
        self.recommendations = CoalescingLoader(
            "/movie/{id}/recommendations",
            lambda k: c.get(f"/movie/{k[0]}/recommendations", {"page": k[1]}))
        self.similar = CoalescingLoader(
            "/movie/{id}/similar",
            lambda k: c.get(f"/movie/{k[0]}/similar", {"page": k[1]}))
        self.collection = CoalescingLoader(
            "/collection/{id}", lambda i: c.get(f"/collection/{i}"))
        self.genre = GenreLoader(lambda: c.get("/genre/movie/list"))
        self.search = CoalescingLoader(
            "/search/movie",
            lambda k: c.get("/search/movie", {"query": k[0], "page": k[1]}))
        self.trending = CoalescingLoader(
            "/trending/movie/{window}",
            lambda k: c.get(f"/trending/movie/{k[0]}", {"page": k[1]}))
        self.discover = CoalescingLoader("/discover/movie", self._discover_fetch(c))
        self.ai_summary = AiSummaryLoader(summarizer)

    @staticmethod
    def _discover_fetch(c):
        async def fetch(key):
            genre_id, year, sort, page = key
            params: dict[str, Any] = {"page": page,
                                      "sort_by": SORT_BY.get(sort, "popularity.desc")}
            if genre_id is not None:
                params["with_genres"] = genre_id
            if year is not None:
                params["primary_release_year"] = year
            return await c.get("/discover/movie", params)
        return fetch
