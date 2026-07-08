# Changelog

## 0.2.0: 2026-07-08

The full executable query surface. Queries no longer need to be rewritten
before quoting: both engines (PyPI + npm) now parse what clients actually
send, and where the parser must guess, it guesses high so the ceiling stays
a ceiling.

- **Aliases** resolve to the schema field name; the same field under two
  aliases is priced twice (that *is* extra work).
- **Fragments** (named + inline) expand in place; a sugared query prices
  exactly like its hand-flattened equivalent. Unknown fragment names and
  fragment cycles raise. Polymorphic branches (`... on Type` where the type
  differs from the enclosing one) are priced as if every branch fires: an
  upper bound, flagged with a caveat.
- **Variables**: `PricingPack.quote(query, variables)` (Python + JS) and
  `costql quote --variables '{"n": 8}'` (CLI). Declared defaults apply when
  no value is given; a variable with neither loses its argument, so the
  ceiling's worst-case bound applies.
- **Directives** parse and are priced as included (`@skip`/`@include` can
  only remove work, so ignoring them never under-prices).
- List and input-object argument literals parse.
- Conformance corpus grows to 56 frozen quotes, including a synthetic
  union-schema pack (`conformance/union_pack.json`) that pins branch pricing
  across both engines. All 46 pre-existing oracle quotes are byte-identical:
  no price changed for the already-supported surface.

## 0.1.0: 2026-07-07

Initial public release.

- `costql` Python package: `build_pack` (calibrate a live GraphQL API into a
  pricing pack), `PricingPack.load/save/quote` (offline quoting), frozen output
  contract v1.0 (`costql.contract.validate`), `APIConfig` adapter surface.
- `costql` CLI: `probe` (check a live endpoint before an adapter exists:
  achievable tier and currency from the observed cost trace, plus where to
  harvest real IDs), `build`, `quote`, `validate`, `version`.
- Reads pricing packs of `pack_version` 1.
- Conformance corpus for the JS quote port (`conformance/quotes.json`).
