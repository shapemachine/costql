# Changelog

## 0.1.0: unreleased

Initial public release.

- `costql` Python package: `build_pack` (calibrate a live GraphQL API into a
  pricing pack), `PricingPack.load/save/quote` (offline quoting), frozen output
  contract v1.0 (`costql.contract.validate`), `APIConfig` adapter surface.
- `costql` CLI: `probe` (check a live endpoint before an adapter exists:
  achievable tier and currency from the observed cost trace, plus where to
  harvest real IDs), `build`, `quote`, `validate`, `version`.
- Reads pricing packs of `pack_version` 1.
- Conformance corpus for the JS quote port (`conformance/quotes.json`).
