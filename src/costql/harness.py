"""Instrumentation Harness (black-box HTTP adapter).

Runs a query against a GraphQL endpoint and measures its cost as client-observed
request latency (wall_time_ms): the signal an external T1 consumer sees, and
finer than an integer-ms server histogram. Sample-level round-robin measurement
(measure_batch) makes host/GC drift common-mode across queries.

Endpoint + credentials come from the APIConfig; nothing here is API-specific.
"""
from __future__ import annotations

import statistics
import time
from dataclasses import dataclass, field

from .config import APIConfig


def _requests():
    """Lazy import: measurement is a build-time concern. The quote path never
    touches the network, so `requests` is an optional dependency."""
    try:
        import requests
    except ImportError as e:                                  # pragma: no cover
        raise ImportError(
            "the calibration/measurement path needs the 'requests' package; "
            "install it with:  pip install 'costql[build]'") from e
    return requests


@dataclass
class AdapterManifest:
    tier: str = "T1"
    cost_currency: str = "wall_time_ms"       # client-observed request latency
    stack: str = "black-box HTTP (client-timed)"


@dataclass
class Measurement:
    ok: bool
    measured_ms: float          # trimmed-mean per-request WALL-clock ms (T1 proxy / fallback)
    samples: list[float] = field(default_factory=list)
    errors: list | None = None
    response: dict | None = None
    work_samples: list[float] = field(default_factory=list)   # per-round work-ms from cost_trace
    cost_traces: list[dict] = field(default_factory=list)     # per-round full cost_trace (measure() only)

    @property
    def cost_trace(self) -> dict:
        """T3 adapter observation from response.extensions (loaders + invocations
        + work-ms). Taken from the last round's response."""
        if isinstance(self.response, dict):
            return (self.response.get("extensions") or {}).get("cost_trace") or {}
        return {}

    def representative_trace(self) -> dict:
        """The single coherent run whose total work-ms is the median across rounds
       : the exact post-execution price of a *typical* execution (total + per-
        resolver + loaders all come from ONE run, never mixed). Falls back to the
        last-round cost_trace when per-round traces weren't captured."""
        traces = [t for t in self.cost_traces if isinstance(t, dict)]
        withwork = [t for t in traces if isinstance(t.get("work_ms"), (int, float))]
        if not withwork:
            return self.cost_trace
        withwork.sort(key=lambda t: t["work_ms"])
        return withwork[len(withwork) // 2]      # median-work run

    @property
    def work_ms(self) -> float | None:
        """The currency (DECISIONS #4): trimmed-mean summed work-ms across rounds,
        from the server's cost_trace. None when the API emits no work-ms (a T1-only
        backend): the engine then falls back to wall-clock as the low-fidelity proxy."""
        if self.work_samples:
            return _trimmed_mean(self.work_samples)
        return None

    @property
    def resolver_work_ms(self) -> dict:
        """Per-resolver observed work-ms from the last round's cost_trace: the
        exact post-execution T3 decomposition (no model)."""
        return dict(self.cost_trace.get("resolver_work_ms") or {})

    @property
    def cost_ms(self) -> float:
        """Ground-truth cost in the one currency: work-ms when observed (T2/T3),
        else the wall-clock proxy (T1). Never refuses: always returns a price."""
        w = self.work_ms
        return w if w is not None else self.measured_ms


class Harness:
    def __init__(self, config: APIConfig):
        self.url = config.graphql_url
        self.tokens = dict(config.tokens)
        self.manifest = AdapterManifest(tier=config.tier, cost_currency=config.cost_currency)

    def _headers(self, auth: str | None) -> dict:
        h = {"Content-Type": "application/json"}
        if auth and self.tokens.get(auth):
            h["Authorization"] = f"Bearer {self.tokens[auth]}"
        return h

    def measure(self, query: str, auth: str | None = None,
                repeats: int = 15, warmup: int = 2) -> Measurement:
        requests = _requests()
        headers = self._headers(auth)
        payload = {"query": query}
        last_resp = None

        for _ in range(warmup):
            r = requests.post(self.url, json=payload, headers=headers, timeout=30)
            last_resp = r.json()

        samples, work, traces = [], [], []
        for _ in range(repeats):
            r = requests.post(self.url, json=payload, headers=headers, timeout=30)
            last_resp = r.json()
            # Client-observed request latency: finer resolution than the
            # integer-ms server histogram, and exactly what a black-box (T1)
            # consumer measures. localhost network overhead is sub-0.1ms.
            samples.append(r.elapsed.total_seconds() * 1000.0)
            ct = _trace(last_resp)
            traces.append(ct)
            w = ct.get("work_ms")
            if isinstance(w, (int, float)):
                work.append(float(w))
            time.sleep(0.02)

        errors = last_resp.get("errors") if isinstance(last_resp, dict) else None
        ok = errors is None and isinstance(last_resp, dict) and last_resp.get("data") is not None
        return Measurement(ok=ok, measured_ms=_trimmed_mean(samples), samples=samples,
                           errors=errors, response=last_resp, work_samples=work,
                           cost_traces=traces)


    def measure_batch(self, jobs: list[tuple], rounds: int = 20,
                      warmup_rounds: int = 2, timeout: float = 60.0) -> dict:
        """Measure many queries with sample-level round-robin: each round runs
        every query once, so every query's samples span the SAME full time
        window. Low-frequency host/GC drift then affects all queries in common
        rather than as a systematic offset between separately-bursted queries.

        jobs: list of (key, query, auth). Returns {key: Measurement}. A request
        that times out or errors is recorded as a failed Measurement for that key
        rather than aborting the whole batch.
        """
        requests = _requests()
        samples: dict = {k: [] for k, _q, _a in jobs}
        work: dict = {k: [] for k, _q, _a in jobs}
        last: dict = {}
        failed: dict = {}

        def _post(q, a):
            return requests.post(self.url, json={"query": q},
                                 headers=self._headers(a), timeout=timeout)

        for _ in range(warmup_rounds):
            for k, q, a in jobs:
                try:
                    _post(q, a)
                except requests.RequestException:
                    pass
        for _ in range(rounds):
            for k, q, a in jobs:
                if k in failed:
                    continue
                try:
                    r = _post(q, a)
                except requests.RequestException as e:
                    failed[k] = str(e)
                    continue
                samples[k].append(r.elapsed.total_seconds() * 1000.0)
                resp = r.json()
                last[k] = resp
                w = _work_ms(resp)
                if w is not None:
                    work[k].append(w)
        out = {}
        for k, _q, _a in jobs:
            if k in failed or not samples[k]:
                out[k] = Measurement(ok=False, measured_ms=float("nan"),
                                     errors=[failed.get(k, "no samples")])
                continue
            resp = last[k]
            errs = resp.get("errors") if isinstance(resp, dict) else None
            ok = errs is None and isinstance(resp, dict) and resp.get("data") is not None
            out[k] = Measurement(ok=ok, measured_ms=_trimmed_mean(samples[k]),
                                 samples=samples[k], errors=errs, response=resp,
                                 work_samples=work[k])
        return out


def _trace(resp) -> dict:
    """The response's cost_trace ({} if absent)."""
    if not isinstance(resp, dict):
        return {}
    return (resp.get("extensions") or {}).get("cost_trace") or {}


def _work_ms(resp) -> float | None:
    """Pull the request's summed work-ms out of a response's cost_trace, or None
    if the API emits no work-ms (T1-only backend)."""
    w = _trace(resp).get("work_ms")
    return float(w) if isinstance(w, (int, float)) else None


def _trimmed_mean(xs: list[float]) -> float:
    """Central estimate robust to slow-window spikes: drop the two highest and
    one lowest sample, then average. Stabilises ground truth so the ceiling
    bounds the query's typical structural cost, not host/GC jitter."""
    if not xs:
        return float("nan")
    if len(xs) <= 4:
        return statistics.median(xs)
    trimmed = sorted(xs)[1:-2]
    return statistics.mean(trimmed)
