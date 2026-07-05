"""The two coverage-extension fields (BUILD §7).

`chemistryScore`: local O(limit²) compute over the already-loaded cast. 0 downstream
calls; its whole cost is work-ms (DECISIONS #4). This is the resolver `downstream_calls`
would price at 0 while it is genuinely expensive.

`aiSummary`: a paid Anthropic call keyed/deduped per movie id. Its real cost is a
per-call token fee (dollars) invisible to latency/count (DECISIONS #6). The fee itself
never enters the trace; the trace only marks `downstream_host="api.anthropic.com"` so
costQL auto-flags it external/paid.
"""
from __future__ import annotations

import math
import os
import time
from typing import Any, Optional

ANTHROPIC_HOST = "api.anthropic.com"
SUMMARY_MODEL = "claude-haiku-4-5-20251001"


def chemistry_score(cast: list[dict[str, Any]], limit: int = 20) -> tuple[float, float]:
    """O(limit²) pairwise "chemistry" over the top `limit` cast members. Returns
    `(score, local_compute_ms)`.

    The constant factor is kept deliberately high so limit=5 vs 20 vs 50 separate
    cleanly above measurement noise (BUILD §7 / DECISIONS #1 super-linear curve).
    Pure local CPU (no I/O) for a low-noise signal.
    """
    members = cast[:limit]
    n = len(members)
    t0 = time.perf_counter()
    total = 0.0
    # O(n^2) all-pairs. Inner body is intentionally non-trivial (trig + hashing)
    # to give a measurable per-pair cost without being absurd.
    for i in range(n):
        a = members[i]
        ai = _affinity(a)
        for j in range(i + 1, n):
            b = members[j]
            bj = _affinity(b)
            # a few flops per pair, repeated to lift the constant factor above noise
            # (high enough that the O(n²) pair term dominates fixed overhead, so the
            # super-linear curve is unmistakable across limit=5/20/50; see BUILD §7 / #1)
            acc = 0.0
            for _ in range(120):
                acc += math.sin(ai * 1.7 + bj * 0.3) * math.cos(ai - bj)
            total += abs(acc)
    pairs = n * (n - 1) / 2
    score = (total / pairs) if pairs else 0.0
    local_ms = (time.perf_counter() - t0) * 1000.0
    return round(score, 6), local_ms


def _affinity(credit: dict[str, Any]) -> float:
    pid = credit.get("id") or credit.get("person", {}).get("id") or 0
    order = credit.get("order")
    order = order if isinstance(order, int) else 0
    return float(int(pid) % 997) + 1.0 / (order + 1)


class AnthropicSummarizer:
    """Wraps the Anthropic client for `aiSummary`. Injectable so tests substitute a
    fake and never spend tokens. Set `stub_text` to force a canned response without
    a client (used by mocked tests)."""

    def __init__(self, client: Optional[Any] = None, stub_text: Optional[str] = None,
                 model: str = SUMMARY_MODEL, max_tokens: int = 150):
        self._client = client
        self.stub_text = stub_text
        self.model = model
        self.max_tokens = max_tokens

    def _ensure_client(self):
        if self._client is None:
            import anthropic  # imported lazily so tests need no key
            self._client = anthropic.AsyncAnthropic(
                api_key=os.environ.get("ANTHROPIC_API_KEY"))
        return self._client

    async def summarize(self, title: str, release_year: Optional[int]) -> str:
        if self.stub_text is not None:
            return self.stub_text
        year = release_year if release_year is not None else "unknown year"
        prompt = f"Summarize this movie in two sentences: {title} ({year})."
        client = self._ensure_client()
        msg = await client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        # concatenate text blocks
        parts = [b.text for b in msg.content if getattr(b, "type", None) == "text"]
        return " ".join(parts).strip()
