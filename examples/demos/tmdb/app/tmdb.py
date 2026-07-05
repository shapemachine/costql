"""TMDB upstream client + adaptive, zero-config throttle (BUILD §4, DECISIONS #5).

One shared async client. Auth is v4 Bearer (`TMDB_ACCESS_TOKEN`); the v3 `?api_key=`
is a fallback only. The throttle backs off on *either* rising p50 latency *or*
429/503 responses (a hard rate limit shows up as 429, not slowness), and ramps
concurrency back up while healthy. No user knob.
"""
from __future__ import annotations

import asyncio
import os
import time
from collections import deque
from typing import Any, Awaitable, Callable, Optional

import httpx

BASE_URL = "https://api.themoviedb.org/3"


class RateLimitError(Exception):
    """Raised after the client exhausts backoff retries on 429/503."""


class AdaptiveThrottle:
    """Concurrency governor. Starts gentle, ramps while healthy, backs off on
    rising latency or throttle responses. Purely reactive — no configuration.

    Concurrency is gated by an `asyncio.Condition` against a mutable `limit` (no
    Semaphore, so `limit` can shrink/grow freely and it's created lazily in the
    running loop — safe across the per-test event loops)."""

    def __init__(self, start: int = 4, floor: int = 1, ceiling: int = 16):
        self.floor = floor
        self.ceiling = ceiling
        self.limit = start
        self._active = 0
        self._cond: Optional[asyncio.Condition] = None
        self._latencies: deque[float] = deque(maxlen=20)
        self._baseline_p50: Optional[float] = None
        self._healthy_streak = 0
        self.backoffs = 0                     # observable for tests
        self.ramps = 0

    def _get_cond(self) -> asyncio.Condition:
        if self._cond is None:
            self._cond = asyncio.Condition()
        return self._cond

    async def __aenter__(self):
        cond = self._get_cond()
        async with cond:
            while self._active >= self.limit:
                await cond.wait()
            self._active += 1
        return self

    async def __aexit__(self, *exc):
        cond = self._get_cond()
        async with cond:
            self._active -= 1
            cond.notify_all()               # limit or active changed → re-check

    def _p50(self) -> Optional[float]:
        if len(self._latencies) < 4:
            return None
        s = sorted(self._latencies)
        return s[len(s) // 2]

    def _shrink(self) -> None:
        if self.limit > self.floor:
            self.limit -= 1
        self._healthy_streak = 0
        self.backoffs += 1

    def _grow(self) -> None:
        if self.limit < self.ceiling:
            self.limit += 1
            self.ramps += 1
        self._healthy_streak = 0

    def observe(self, latency_ms: float, *, throttled: bool) -> None:
        """Feed one call's outcome back into the governor."""
        if throttled:
            self._shrink()
            return
        self._latencies.append(latency_ms)
        p50 = self._p50()
        if p50 is None:
            return
        if self._baseline_p50 is None:
            self._baseline_p50 = p50
            return
        # Rising latency (>1.8x the established baseline) => back off.
        if p50 > self._baseline_p50 * 1.8:
            self._shrink()
            # re-baseline upward so we don't thrash
            self._baseline_p50 = p50
            return
        # Healthy: slowly re-learn baseline downward and ramp concurrency.
        self._baseline_p50 = min(self._baseline_p50, p50)
        self._healthy_streak += 1
        if self._healthy_streak >= 5:
            self._grow()


SendFn = Callable[[str, dict[str, Any]], Awaitable[httpx.Response]]


class TMDBClient:
    """Async TMDB REST client. `send_fn` is injectable so tests can stand in a
    fake upstream (and count real downstream calls) without touching the network."""

    def __init__(self, access_token: Optional[str] = None, api_key: Optional[str] = None,
                 send_fn: Optional[SendFn] = None, max_retries: int = 3):
        self.access_token = access_token or os.environ.get("TMDB_ACCESS_TOKEN")
        self.api_key = api_key or os.environ.get("TMDB_API_KEY")
        self._send_fn = send_fn
        self._client: Optional[httpx.AsyncClient] = None
        self.throttle = AdaptiveThrottle()
        self.max_retries = max_retries
        self.call_count = 0                          # observable for tests
        self.calls_by_path: dict[str, int] = {}

    def _headers(self) -> dict[str, str]:
        if self.access_token:
            return {"Authorization": f"Bearer {self.access_token}",
                    "accept": "application/json"}
        return {"accept": "application/json"}

    async def _client_or_create(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(base_url=BASE_URL, headers=self._headers(),
                                             timeout=15.0)
        return self._client

    async def _default_send(self, path: str, params: dict[str, Any]) -> httpx.Response:
        client = await self._client_or_create()
        # v3 api_key fallback only when no bearer token is configured.
        if not self.access_token and self.api_key:
            params = {**params, "api_key": self.api_key}
        return await client.get(path, params=params)

    async def get(self, path: str, params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """GET a TMDB endpoint through the adaptive throttle, with backoff on
        429/503. Returns parsed JSON. Counts every *actual* upstream call."""
        params = params or {}
        send = self._send_fn or self._default_send
        attempt = 0
        while True:
            async with self.throttle:
                t0 = time.perf_counter()
                resp = await send(path, params)
                latency_ms = (time.perf_counter() - t0) * 1000.0
                self.call_count += 1
                self.calls_by_path[path] = self.calls_by_path.get(path, 0) + 1
                throttled = resp.status_code in (429, 503)
                self.throttle.observe(latency_ms, throttled=throttled)
            if throttled:
                attempt += 1
                if attempt > self.max_retries:
                    raise RateLimitError(f"{resp.status_code} on {path} after "
                                         f"{self.max_retries} retries")
                retry_after = resp.headers.get("retry-after")
                delay = float(retry_after) if retry_after else min(2 ** attempt * 0.1, 2.0)
                await asyncio.sleep(delay)
                continue
            resp.raise_for_status()
            return resp.json()

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
