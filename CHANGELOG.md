# Changelog

## 0.1.0 — unreleased

Initial public release.

- `costql` Python package: `build_pack` (calibrate a live GraphQL API into a
  pricing pack), `PricingPack.load/save/quote` (offline quoting), frozen output
  contract v1.0 (`costql.contract.validate`), `APIConfig` adapter surface.
- `costql` CLI: `build`, `quote`, `validate`, `version`.
- Reads pricing packs of `pack_version` 1.
- Conformance corpus for the JS quote port (`conformance/quotes.json`).
