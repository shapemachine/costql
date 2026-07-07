# Paid & external fees

costQL prices your API by timing it. Some costs never show up on a stopwatch:
when a field calls a **paid** service for you (an LLM, a licensed data feed, a
metered third-party API), the money leaves your account whether or not the call
took any time. So you tell costQL that fee once, and every quote folds it in.

**If none of your fields call a paid third party, skip this page: nothing here
is required.** This is only for fields that cost real money downstream (a
per-call fee, a per-access license) and aren't already part of your server's own
work-time. A database query is measured for you; a call to `api.anthropic.com`
is not.

## Step 1: flag the field in your adapter

List the paid resolver under `bounded_fields`. This tells the build to sample
the field in isolation (instead of fanning it out during calibration) and to
expect a hand-authored fee for it.

```python
bounded_fields={
    "Movie.aiSummary": "paid external call (api.anthropic.com); "
                       "fee authored via adjustments",
}
```

The adapter only *flags* the field. It never carries the number.

See [the adapter guide](adapters.md) for what every field does.

## Step 2: build, and let costQL write the template

Build as usual:

```
costql build --adapter tmdb.py:build --out tmdb.json
```

Because `Movie.aiSummary` is a bounded field with no fee given yet, the pack
ships with a zero-fee template for it. Nothing is charged until you fill it in.

## Step 3: set the fee

Copy the `adjustments` section out of the pack into its own JSON file and set
`added_unit_cost`: the per-call fee, in **cost-units** (the same currency the
rest of the pack uses), never dollars.

```json
{
  "adjustments": {
    "Movie.aiSummary": {
      "added_unit_cost": 6.0,
      "reason": "paid Anthropic call",
      "downstream_host": "api.anthropic.com"
    }
  }
}
```

Rebuild with the file, and the fee is baked into the pack:

```
costql build --adapter tmdb.py:build --out tmdb.json --adjustments fees.json
```

Your edits survive future rebuilds; you don't re-author them each time.

## What shows up in a quote

The fee is added to that resolver's cost and then follows the normal rules,
including sharing: a batched call is counted **once**, not once per row. A T3
quote also names the paid host on its own line, so your app can show it:

```json
"external_costs": [
  { "resolver_id": "Movie.aiSummary", "host": "api.anthropic.com",
    "authored_fee": 6.0, "measured_fee": false }
]
```

`measured_fee: false` is the honest label: costQL didn't measure this number,
you authored it. See [the output contract](contract.md) for the full field
shape.

## What this is not

This is for **real, factual costs** you can't measure: a per-call fee, a
per-access license. It is not a markup or a margin knob. Deciding what to
*charge your users* is pricing, and pricing belongs in your app, not in the
pack. costQL gives you the true cost; the markup is yours to add on top.
