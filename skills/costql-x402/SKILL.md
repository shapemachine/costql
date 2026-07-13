---
name: costql-x402
description: Wire costQL into an x402 payment flow. Use when the user wants to charge for a GraphQL query over x402 (HTTP 402), map costQL prices onto the upto scheme (authorize a ceiling, settle the actual), or convert costQL cost-units into a stablecoin amount. Covers the 402/authorize and settle steps in Python or JS.
---

# Wire costQL into x402

x402's `upto` scheme authorizes a **maximum** amount up front and settles the
**actual** amount consumed (`≤` max). That maps one-to-one onto costQL's two
bases. Full recipe: https://costql.com/docs/guides/x402/

## The mapping (this is the whole idea)

| x402 `upto` | costQL |
|---|---|
| authorized `amount` (max) | `basis: "predicted"` → `price`, the safe ceiling that **never under-prices** |
| settled `amount` (actual, `≤` max) | `basis: "measured"` → `price`, the exact receipt (`confidence: "exact"`) |

The predicted ceiling makes the authorization safe (never authorize below real
cost → no legit query rejected at settlement). The measured receipt makes
settlement fair (charge for real work). `ceiling ≥ settle` always holds.

## The one app-owned number

costQL prices in cost-units (`work_ms` / `wall_time_ms`), **never dollars**. Pick
one `RATE` (atomic units of your stablecoin per cost-unit) and apply it
**identically** to both the ceiling and the settle amount. Same rate × same
cost-units ⇒ the guarantee survives the conversion.

## Authorize (return 402)

Quote the incoming query; the predicted `price` is the authorized max. Ships
today, black-box at T1, no server instrumentation.

```python
from costql import PricingPack
pack = PricingPack.load("packs/your_pack.json")
RATE = 100  # atomic units per work_ms

quote = pack.quote(query, variables)          # basis: "predicted"
amount = round(quote["price"] * RATE)         # CEIL up when rounding; never down
# -> put `amount` (as str) in the upto PaymentRequirements: scheme, network,
#    asset, payTo, maxTimeoutSeconds
```

JS is identical via `import { PricingPack } from "costql"` and
`pack.quote(query, variables).price`. Round the ceiling **up** — the one place a
careless round could authorize below cost.

## Settle (charge the actual)

After the query runs, the measured total is `extensions.cost_trace.work_ms` (the
[T2/T3 instrumentation seam](https://costql.com/docs/instrumentation/)); it is the
`price` of the measured receipt. Convert with the **same** RATE and pass it as the
`amount` in the `PaymentRequirements` sent to the facilitator's `/settle`.

```python
measured_ms = response["extensions"]["cost_trace"]["work_ms"]
settle_amount = round(measured_ms * RATE)     # <= the authorized ceiling
```

## Guardrails

- **Round the ceiling up, the settle to nearest.** Rounding is the only spot that
  can break "never under-authorize."
- **No measured trace yet?** Settle at the ceiling using the stable `exact` scheme
  (safe, just no refund), or instrument for T2/T3 first. `upto` is experimental in
  x402 — check your facilitator supports it.
- **Don't invent a public "measured" call.** The shipped package's public settle
  input is the server's `cost_trace`; `ExactPrice`/`measured_result` are internal.
  The JS package is quote-side only.
- **Never introduce dollars into a pack or quote.** The RATE lives in the app, not
  in costQL.
- Interpreting a specific quote's fields/caveats → use `costql-quote-debug`.
