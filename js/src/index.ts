/** costql (JS/TS) — quote GraphQL query costs offline from a costQL pricing
 * pack. This package is the QUOTE side only: packs are built with the Python
 * `costql` package (`pip install costql[build]`), then consumed anywhere.
 *
 *     import { PricingPack } from "costql";
 *     const pack = PricingPack.fromObject(await (await fetch("/packs/tmdb_t3.json")).json());
 *     const quote = pack.quote('{ movie(id:"27205"){ title } }');
 *     quote.price   // safe billable ceiling, in cost-units (never dollars)
 *
 * Conformance: every release is verified against the Python engine's frozen
 * oracle (conformance/quotes.json) — same pack + same query => same result.
 */
export { PricingPack, PackVersionError, PACK_VERSION } from "./pack.js";
export { CONTRACT_VERSION, validate, predictedResult } from "./contract.js";
export type { QuoteResult } from "./contract.js";
export { parseQuery, countFields } from "./queryIr.js";
export type { Selection } from "./queryIr.js";
export { TypeGraph } from "./introspect.js";
export { assess } from "./confidence.js";
export { Pricer, DOC_FEATURE } from "./pricer.js";
export type { CostModelData } from "./pricer.js";
