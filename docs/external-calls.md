---
description: "When a field calls an outside service (an LLM, a paid API), costQL names the call and observes its host; your app puts the price on it."
---

# External calls

costQL prices your API by timing it. But some of your fields call an **outside
service** for you: an LLM, a licensed data feed, another paid API. costQL can't
time what leaves your account there, and it can't know what that service charges
you. So it doesn't guess. It **names the call**, and your app puts the price on it.

**If none of your fields call an outside service, skip this page: nothing here is
required.** This is only for fields that reach a host that isn't yours (a call to
`api.anthropic.com`, say). A database query is your own work, and costQL measures
it for you.

## Step 1: flag the field in your adapter

List the field under `bounded_fields`. This tells the build to sample it once, on
its own, instead of fanning it out during calibration.

```python
bounded_fields={
    "Movie.aiSummary": "outside call (api.anthropic.com); "
                       "sampled in isolation, host observed, priced by the app",
}
```

See [the adapter guide](adapters.md) for what every field does.

## Step 2: build

Build as usual:

```
costql build --adapter tmdb.py:build --out tmdb.json
```

When costQL samples that field, it watches the call and records the **host** it
saw. That host is now saved in the pack (in `model.external_hosts`). Nothing else
changes; no number is invented.

## What shows up in a quote

At **T3** (the only tier that names the host), a quote that hits the field carries
an `external_calls` line:

```json
"external_calls": [
  { "resolver_id": "Movie.aiSummary", "host": "api.anthropic.com", "calls": 1 }
]
```

- `host` is the outside address costQL saw.
- `calls` is how many times this query hits it (the safe ceiling count).
- There is **no fee**. costQL never knows what the outside service charges.

See [the output contract](contract.md) for the full field shape.

## Your app puts the price on it

The pack tells your app *where* the outside call is and *how many* there are. Your
app already knows the rest: it is the one making the call, so it knows the request,
and it knows what that host charges. So your app reads `external_calls`, prices
each one, and adds it to the quote's `price`.

That split is on purpose. costQL gives you the true cost it can measure; the money
for an outside call is yours to add, because only you can know it.

## One limit

`external_calls` is a **T3** thing: T3 is the only tier that names the host. And to
match an outside call to the field that made it, your app reads the per-resolver
`breakdown`, which is **T2 or T3**. At **T1** there is no per-resolver detail at
all, so app-side pricing of outside calls needs at least T2.
