# How costQL measures its own accuracy

Any accuracy number needs three pieces: a **model** whose prediction is on trial, a **baseline** (ground truth) to compare against, and a **dataset** the comparison runs over. This doc pins down exactly what each is in costQL, and — importantly — what each is *not*. It complements the [tier overview](tiers.md) and the step-by-step [pricing flow](architecture.md); the measured results themselves live in the [TMDB](results/tmdb.md), [Rick & Morty](results/rickmorty.md), and [Northwind](results/northwind.md) case studies.

---

## 1. The three pieces

| Piece | In costQL | Produced by |
|---|---|---|
| **Model (on trial)** | the pricing pack's **prediction** for a query — static analysis, the query is *not* run | `costql/pricer.py` (`Pricer.price`), served via `PricingPack.quote()` (`costql/pack.py`) |
| **Baseline (ground truth)** | the query's **actual measured cost** from really running it against the live API | `costql/harness.py` (`Measurement.cost_ms`) |
| **Dataset** | real GraphQL queries against a real API | `costql/heldout.py` (calibration + held-out sets) |

**Error** = predicted cost vs. measured cost, per query, over the dataset.

The baseline is **reality** (measured execution), not a rival estimator we are trying to
beat. We are measuring *"how close is the prediction to the truth,"* not *"are we better
than some other model."*

---

## 2. Two disjoint datasets: calibration vs. held-out

`costql/heldout.py` builds **two deliberately non-overlapping** query sets:

| | Calibration set | Held-out set |
|---|---|---|
| Purpose | **fit** the model | **grade** the model |
| Shapes | full-depth coverage ops, depths `[2, 5, 9, 12]`, whale (max) inputs | shallow/mid depths `[3, 7]`, small-vs-whale pagination, alternate inputs, adversarial fixtures |
| Seen by the model? | yes — it is fit on these | **no** — a distribution the model never saw |

Fitting and grading on the same queries would flatter the model. The held-out set proves
it generalizes to shapes it was **not** fit on. (Generalization is also shown across
*APIs*: a brand-new API — [Rick & Morty](results/rickmorty.md) — is priced with only a
~90-line adapter and zero core changes.)

---

## 3. How the model is built (calibration → rates, once)

At build time only (`costql build`, or `build_pack()` from Python):

1. Run every calibration query and record `(invocations_by_resolver, measured_cost)`.
2. Solve, by non-negative least squares (`costql/pricer.py`):
   ```
   op_cost  ≈  Σ_resolver  invocations[resolver] × unit_cost[resolver]
   ```
   → **one reusable `unit_cost` per resolver.** Only resolvers that do real work carry
   cost; scalar leaf reads are ~0.

The regression is needed because it *separates* fixed per-request overhead from marginal
per-call cost across many query shapes. An adapter can supply curated calibration shapes
via the `APIConfig.calibration_queries` hook (a callable taking a size — `"whale"` or
`"small"` — and returning query strings).

**What ships is the rate table, not the queries.** The pricing pack contains
schema + `unit_cost` table + batch curves + observed sizes + authored fees. It does **not**
contain the calibration queries or their measured prices. The calibration queries dissolve
into the rate table; they do not survive as retrievable entries.

---

## 4. How the baseline (ground truth) is measured — per tier

To grade a held-out query, `Pricer` predicts it (no run) and the harness **runs it for
real** and reads its total cost (`costql/harness.py`, `Measurement.cost_ms`). What "total
cost" means sharpens with the tier:

| Tier | Ground-truth total = | Note |
|---|---|---|
| **T1** (black box) | trimmed-mean whole-request **wall-clock ms** | the only signal a black-box consumer can see; a blurrier proxy for work-ms |
| **T2** (per-resolver timings) | **sum of every fired resolver's work-ms** + fixed per-query overhead | complete accounting of the real work of that run |
| **T3** (timings + sharing trace) | same sum, with shared/batched calls counted **as they actually coalesced** | real sharing, observed not inferred |

Key properties of the baseline, at T2/T3:

- **It includes every resolver's cost.** The total is the sum over *all* invocations that
  fired, plus parse/validate/serialize overhead. We don't *surface* the per-resolver
  breakdown for held-out grading, but it is all baked into the number.
- **It is measurement, not estimation.** There is no model inside the baseline. That is
  what makes it trustworthy as ground truth.
- **It reflects real sharing.** Batched/deduped calls are counted the way the real loader
  actually coalesced them — which is exactly the thing the T2 *prediction* only *infers*.
  That gap is what the held-out comparison is designed to expose.

Repeated rounds + a trimmed mean cancel sub-ms measurement jitter.

---

## 5. Why there is no "already priced this" lookup

A recurring misconception: that a query already seen in calibration gets served its known
measured price. **It does not — there is no query→price lookup anywhere.**

- `PricingPack.quote()` has one path: parse → fanout → ceiling sum → **predict**. It never
  checks "did we measure this one before," because the pack stores no query prices.
- A calibration query fed back through the pricer is re-derived as a *predicted ceiling*
  from scratch — it does **not** get its stored measurement. (The predicted ceiling will
  even differ from its calibration measurement: calibration is fit at the observed
  operating point, pricing evaluates worst-case.)
- The only way to get a measured number for a specific query is to **run it** — that is
  execution, not a lookup. This is the opt-in exact path (`costql.exact`, which reads a
  real run's cost-trace receipt), reached only when a caller wants an exact figure on a
  low-confidence quote.

The model collapses infinitely many possible queries into a few dozen resolver rates. That
compression is the product; a lookup table can only remember what already happened.

---

## 6. What "accurate" means here (and its honest scope)

The held-out baseline is accurate **by construction** — it is the real summed work of the
run. Two scoping caveats worth stating plainly:

- **That run, that data.** The measured total is the true cost of *this* execution against
  *today's* data. Against a larger table the true total changes — which is why the model
  predicts a worst-case **ceiling** (safe billable number) separately from a **typical**
  estimate (fair average), and why data-dependent-size queries are confidence-flagged
  (see [Honest limitations](limitations.md)).
- **Jitter.** A single timing has sub-ms noise; the ground truth is a trimmed mean over
  repeated rounds, not one stopwatch reading.

---

## 7. Where this lives in code

| Concern | File |
|---|---|
| Calibration + held-out set construction (disjoint) | `src/costql/heldout.py` |
| Static prediction (fanout × unit_cost, ceiling/typical) | `src/costql/fanout.py`, `src/costql/pricer.py` |
| Model fit (NNLS regression → unit costs) | `src/costql/pricer.py` (`CostModelBuilder.build`) |
| Ground-truth measurement (work-ms / wall-clock, trimmed mean) | `src/costql/harness.py` |
| Local quote from the pack (no run, no lookup) | `src/costql/pack.py` (`PricingPack.quote`) |
| Opt-in exact price (reads a real run's receipt) | `src/costql/exact.py` |

---

## In one paragraph

costQL is graded like any predictor: the **model** is the pricing pack's static prediction
for a query; the **baseline** is that query's real measured cost from actually running it;
the **dataset** is a held-out set of real queries, deliberately disjoint from the
calibration set the model was fit on. Ground truth at T2/T3 is the complete sum of every
resolver's measured work-ms for the run (real sharing included), so it is trustworthy by
construction — it contains no model. There is no query→price lookup: every query, seen or
unseen, is predicted from the rate table, and the only measured numbers come from actually
executing a query.
