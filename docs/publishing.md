---
description: "A pack is a static JSON file. This is how to publish it so whoever prices a query — your own app, or a third party's agent — can fetch it before the call."
---

# Publishing a pack

A pack is one static JSON file (see [the pack format](pack-format.md)). Once
it's built, someone has to be able to *get* it before they can price a query.
That someone is either your own app, or — if you want callers to know the cost
before they call you — a third party's client or agent.

costQL doesn't serve packs; it builds them. Where the file lives is yours to
decide. This page recommends how, from the simplest case to the one that lets an
agent find the price with no human in the loop.

## Vendor it (your own app)

If the only thing pricing queries is an app you control, you don't publish
anything. Commit the pack into that app's repo and load it from disk. This is
the default the [quickstart](quickstart.md) shows.

```python
pack = PricingPack.load("packs/tmdb_t3.json")
```

Nothing below is needed unless you want *other people* to read the price.

## Serve it as a static file

To let callers price a query before they hit your API, put the pack somewhere
they can fetch it — an S3 bucket, a CDN, your docs site, any static host. It's
plain JSON; both engines load a fetched object directly (see [the JS
package](js.md)).

```ts
const pack = PricingPack.fromObject(await (await fetch(
  "https://api.example.com/costql/pack.json")).json());
```

Two things to get right:

- **Version the URL** (`…/costql/pack-v3.json`) or set cache headers, so a
  consumer isn't stuck on a stale copy or fighting your CDN cache.
- **Every quote echoes `schema_hash`.** A consumer compares it against your live
  schema to detect that the pack no longer matches the API — build a fresh pack
  and publish it whenever the schema moves.

## Publish it at a discoverable location (recommended)

The step that turns "price before you call" from a manual copy into something a
client or agent can do on its own: serve the pack at a **well-known path** off
your API's origin, so it's findable from the endpoint URL alone.

```
https://api.example.com/.well-known/costql.json
```

Serve either the pack itself, or a small pointer to it:

```json
{ "pack_url": "https://cdn.example.com/costql/pack-v3.json",
  "tier": "T3", "schema_hash": "26c786209ec27586" }
```

This is a convention costQL recommends, not something the library enforces —
there's no wire protocol to implement, just a file at a predictable place. An
agent that knows your GraphQL URL can then find the price without being handed a
link.

## Ship it as a package

When the consumer is another codebase rather than an ad-hoc client, publish the
pack as a versioned npm or PyPI package (`@example/costql-pack`). It then updates
through normal dependency tooling, with a changelog and pinned versions. Good for
known integrators; overkill for a public "here's what a call costs" page.

## What a published pack *is*

A pack is **your stated prices, measured by costQL** — the schema, the fitted
per-resolver costs, and any observed outside hosts, nothing more. costQL doesn't
sign, attest, or audit a pack, and there is no baked-in step that edits the
numbers: a costQL pack reports what it *measured* (see [external
calls](external-calls.md) for the one thing left to the app, and why).

So a consumer who fetches your pack is trusting your published prices the same
way they already trust your bill — reasonable, because you're the one billing
them. If you post-process the JSON before serving it, that's your own layer,
outside costQL, and costQL's "the price errs high — the ceiling stays a ceiling"
guarantee is then yours to keep. Marking a price *up* keeps the ceiling a
ceiling; marking it *down* does not.

## If the price is sensitive

Nothing says a pack must be public. Put it behind the same auth as the rest of
your API, or hand it only to integrators under contract. The distribution
choices above all work behind a login; discoverability is a convenience, not a
requirement.
