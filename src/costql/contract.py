"""The FROZEN costQL price-result contract (v1.0).

This is the stable, versioned shape an application budgets against. It is the
same at every fidelity tier in one respect that matters most: **`price` is always
present, in `currency` cost-units, as a safe number to bill on.** What a higher
tier adds is *explanatory detail*, not a different billable field: so an app can
integrate once against the core and simply gain richer breakdowns as the API's
instrumentation improves.

Two `basis` values, one shape:
  * "predicted": a pre-execution QUOTE from the static pricing pack (a model).
  * "measured" : a post-execution EXACT receipt from a query that actually ran.

Three `tier` values = how sharply cost could be resolved (per DECISIONS #4/#6):
  * T1: a single total only (wall-clock proxy when that is all the API affords).
         No per-resolver breakdown, no sharing.
  * T2: per-resolver work; sharing INFERRED, so not reported as observed.
  * T3: per-resolver work + OBSERVED sharing (dedup/cache) + named external hosts.

The contract is enforced by `validate()` (dependency-free). Anything that fails
validation is a contract violation, by definition: that is what "frozen" means.
"""
from __future__ import annotations

CONTRACT_VERSION = "1.0"

_TIERS = ("T1", "T2", "T3")
_BASES = ("predicted", "measured")
_CONFIDENCE = ("high", "medium", "low", "exact")

# Which optional detail sections each tier is ALLOWED to carry. The core fields
# (below) are mandatory at every tier; these are the tier-gated additions.
_ALLOWED_SECTIONS = {
    "T1": set(),                                  # total only
    "T2": {"breakdown"},                          # per-resolver work, no observed sharing
    "T3": {"breakdown", "sharing", "external_calls"},  # + observed sharing + named outside calls
}

# Mandatory core fields, present and identically-shaped at EVERY tier and basis.
_CORE_REQUIRED = {
    "contract_version": str,
    "tier": str,
    "basis": str,
    "currency": str,
    "price": (int, float),        # the billable number: a safe ceiling (predicted) / exact total (measured)
    "confidence": str,
    "schema_hash": str,
    "caveats": list,
}


def validate(result: dict) -> list[str]:
    """Return a list of contract violations (empty list == valid).

    Checks the mandatory core, enum values, types of the optional tier-gated
    sections, and: crucially; that no section appears at a tier not allowed to
    carry it (e.g. observed `sharing` at T1/T2)."""
    problems: list[str] = []
    if not isinstance(result, dict):
        return ["result is not an object"]

    for key, typ in _CORE_REQUIRED.items():
        if key not in result:
            problems.append(f"missing required field '{key}'")
        elif not isinstance(result[key], typ):
            problems.append(f"field '{key}' must be {typ}")

    if result.get("contract_version") != CONTRACT_VERSION:
        problems.append(f"contract_version must be '{CONTRACT_VERSION}'")
    if result.get("tier") not in _TIERS:
        problems.append(f"tier must be one of {_TIERS}")
    if result.get("basis") not in _BASES:
        problems.append(f"basis must be one of {_BASES}")
    if result.get("confidence") not in _CONFIDENCE:
        problems.append(f"confidence must be one of {_CONFIDENCE}")
    if isinstance(result.get("price"), (int, float)) and result["price"] < 0:
        problems.append("price must be >= 0")

    tier = result.get("tier")
    allowed = _ALLOWED_SECTIONS.get(tier, set())
    for section in ("breakdown", "sharing", "external_calls"):
        if section in result and result[section]:
            if section not in allowed:
                problems.append(
                    f"section '{section}' is not permitted at tier {tier} "
                    f"(allowed: {sorted(allowed) or 'none'})")
            elif not isinstance(result[section], list):
                problems.append(f"section '{section}' must be a list")

    # typical_price is optional but, when present, must be a number.
    if "typical_price" in result and result["typical_price"] is not None \
            and not isinstance(result["typical_price"], (int, float)):
        problems.append("typical_price must be a number or null")
    return problems


def _assemble(*, tier: str, basis: str, currency: str, schema_hash: str,
              price: float, confidence: str, typical_price: float | None = None,
              caveats: list | None = None, breakdown: list | None = None,
              sharing: list | None = None,
              external_calls: list | None = None) -> dict:
    result = {
        "contract_version": CONTRACT_VERSION,
        "tier": tier,
        "basis": basis,
        "currency": currency,
        "price": round(float(price), 4),
        "typical_price": (round(float(typical_price), 4)
                          if typical_price is not None else None),
        "confidence": confidence,
        "schema_hash": schema_hash,
        "caveats": list(caveats or []),
    }
    allowed = _ALLOWED_SECTIONS[tier]
    # Attach only the sections the tier is allowed to carry; silently drop the
    # rest so a caller can pass everything and get a tier-correct result.
    if breakdown and "breakdown" in allowed:
        result["breakdown"] = breakdown
    if sharing and "sharing" in allowed:
        result["sharing"] = sharing
    if external_calls and "external_calls" in allowed:
        result["external_calls"] = external_calls
    return result


def predicted_result(*, tier: str, currency: str, schema_hash: str,
                     price: float, typical_price: float, confidence: str,
                     caveats: list | None = None,
                     breakdown: list | None = None,
                     sharing: list | None = None,
                     external_calls: list | None = None) -> dict:
    """Shape a pre-execution QUOTE (from the pricing pack) into the contract."""
    return _assemble(tier=tier, basis="predicted", currency=currency,
                     schema_hash=schema_hash, price=price, typical_price=typical_price,
                     confidence=confidence, caveats=caveats, breakdown=breakdown,
                     sharing=sharing, external_calls=external_calls)


def measured_result(exact, schema_hash: str) -> dict:
    """Shape a post-execution EXACT receipt (ExactPrice) into the contract.

    The tier is whatever the run's trace afforded (exact.py's ladder). Confidence
    is always "exact": the query ran."""
    breakdown = [{"resolver_id": r.resolver_id, "cost": round(r.work_ms, 4),
                  "invocations": r.invocations} for r in exact.resolvers]
    sharing = [{"loader": lo.endpoint, "requested": lo.requested_keys,
                "calls": lo.actual_calls, "saved": lo.saved,
                "cache_hits": lo.cache_hits, "external": lo.external}
               for lo in exact.loaders]
    # named outside calls: which foreign hosts this run hit and how many billable
    # calls landed (after dedup). No fee: the consuming app prices those calls.
    calls_by_host: dict[str, int] = {}
    for lo in exact.loaders:
        if lo.external and lo.host:
            calls_by_host[lo.host] = calls_by_host.get(lo.host, 0) + lo.actual_calls
    external_calls = [{"host": h, "calls": calls_by_host.get(h, 0)}
                      for h in exact.external_hosts]
    return _assemble(tier=exact.tier, basis="measured", currency=exact.currency,
                     schema_hash=schema_hash, price=exact.total,
                     typical_price=exact.total, confidence="exact",
                     caveats=exact.caveats, breakdown=breakdown,
                     sharing=sharing, external_calls=external_calls)
