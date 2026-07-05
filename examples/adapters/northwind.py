"""Reference adapter: the Northwind SQLite Passthrough (../demos/northwind).

A DB-backed schema shaped for HEAVY entity sharing (a hub query asks for a
category 107 times; the DB reads it once). The demo emits
`response.extensions.cost_trace` with real per-loader coalescing stats and
summed SQL work-ms, so the highest fidelity it affords is **T3**: observed
sharing. Currency is `work_ms`.

Everything specific to this deployment lives here: the endpoint, the real
Northwind ids (categories 1-8, products 1-77, orders 10248-26529, the 5-char
customer codes, suppliers 1-29), argument resolution, and the calibration
shapes, including a deliberate batch-width sweep so each shared loader's
size→cost curve is fit from >=3 distinct widths. There is no auth (local DB)
and nothing paid/bounded to sample in isolation.

Build a pack with:

    costql build --adapter examples/adapters/northwind.py:northwind_config --tier T3
"""
from __future__ import annotations

from costql.config import APIConfig, MinedInputs, UNSET

# --- Deployment ------------------------------------------------------------
GRAPHQL_URL = "http://127.0.0.1:8010/"          # the running northwind demo uvicorn server

# The loader_ids the tracer keys on (dead-loader detection).
KNOWN_LOADERS = [
    "categories", "products", "orders", "customers", "suppliers",
    "order_details:byOrder", "products:byCategory", "products:bySupplier",
    "orders:byCustomer", "order_details:byProduct",
]

# Size-bearing roots + nested list edges. Every list field uses `first`, so the
# fanout counter bounds it automatically; these entries record the page cap.
SIZE_ROOTS = {
    "products": {"arg": "first", "offset": 0, "cap": 20},
    "orders": {"arg": "first", "offset": 0, "cap": 20},
    "customers": {"arg": "first", "offset": 0, "cap": 20},
    "details": {"arg": "first", "offset": 0, "cap": 10},
    "productsForCategory": {"arg": "first", "offset": 0, "cap": 20},
}

# Real Northwind ids (public reference dataset). Disjoint whale / small / held-out.
CATEGORIES = {"whale": 1, "small": 5, "heldout": 3}          # ids 1-8
PRODUCTS   = {"whale": 1, "small": 40, "heldout": 60}        # ids 1-77
ORDERS     = {"whale": 15000, "small": 16000, "heldout": 22000}   # ids 10248-26529
SUPPLIERS  = {"whale": 1, "small": 20, "heldout": 10}        # ids 1-29
CUSTOMERS  = {"whale": "ALFKI", "small": "BERGS", "heldout": "VINET"}   # 5-char codes


class NorthwindInputSource:
    """No mining needed: a local reference DB with known, stable ids."""

    def mine(self) -> MinedInputs:
        return MinedInputs(
            data={"categories": [CATEGORIES], "products": [PRODUCTS],
                  "orders": [ORDERS], "suppliers": [SUPPLIERS],
                  "customers": [CUSTOMERS]},
            tokens={}, uncovered=[])


class NorthwindArgResolver:
    def value(self, field_path, arg_name, arg_type_base, mined, size="whale",
              variant="calib"):
        key = "heldout" if variant == "heldout" else size          # whale | small | heldout
        fp = field_path.lower()

        if arg_name == "id":
            if "categor" in fp:
                return str(CATEGORIES.get(key, CATEGORIES["whale"]))
            if "product" in fp:
                return str(PRODUCTS.get(key, PRODUCTS["whale"]))
            if "order" in fp:
                return str(ORDERS.get(key, ORDERS["whale"]))
            if "customer" in fp:
                return CUSTOMERS.get(key, CUSTOMERS["whale"])
            if "supplier" in fp:
                return str(SUPPLIERS.get(key, SUPPLIERS["whale"]))
            return str(PRODUCTS.get(key, PRODUCTS["whale"]))
        if arg_name == "first":
            return 5 if size == "small" else 15
        return UNSET


# --- Curated calibration shapes ---------------------------------------------

def _ent(which):
    return (CATEGORIES[which], PRODUCTS[which], ORDERS[which],
            CUSTOMERS[which], SUPPLIERS[which])


def calib_shapes(cat, prod, order, cust, sup):
    """Clean, isolating, PREDICTABLE distinct-type shapes. Each fires a hub
    loader with invocations == requested_keys so the sharing model learns the
    fold, and gives every non-leaf resolver a real per-call unit cost."""
    return [
        f'{{ product(id:"{prod}"){{ name unitPrice category{{ name description }} }} }}',
        f'{{ product(id:"{prod}"){{ supplier{{ name country }} }} }}',
        f'{{ order(id:"{order}"){{ orderDate details(first:15){{ quantity product{{ name }} }} }} }}',
        f'{{ order(id:"{order}"){{ customer{{ name country }} }} }}',
        f'{{ category(id:"{cat}"){{ name products(first:15){{ name }} }} }}',
        f'{{ supplier(id:"{sup}"){{ name products(first:15){{ name }} }} }}',
        f'{{ customer(id:"{cust}"){{ name orders(first:15){{ orderDate }} }} }}',
        f'{{ products(first:40){{ name unitPrice }} }}',
        f'{{ orders(first:40){{ orderDate shipCountry }} }}',
        f'{{ categories{{ name description }} }}',
        f'{{ customers(first:20){{ name country }} }}',
    ]


def calib_sweep_shapes(cat, prod, order, cust, sup):
    """Deliberately sweep each BATCHED loader across >=3 distinct batch widths
    so a curve (not just a point) can be fit for it, and so the loader's
    observed cardinality (e.g. the 8-row categories table) is learned."""
    return [
        # categories loader: narrow -> saturating (<=8 distinct)
        f'{{ order(id:"{order}"){{ details(first:3){{ product{{ category{{ name }} }} }} }} }}',
        f'{{ order(id:"{order}"){{ details(first:15){{ product{{ category{{ name }} }} }} }} }}',
        f'{{ orders(first:20){{ details(first:15){{ product{{ category{{ name }} }} }} }} }}',
        # products loader: small -> large distinct-product batches
        f'{{ order(id:"{order}"){{ details(first:5){{ product{{ name }} }} }} }}',
        f'{{ orders(first:10){{ details(first:12){{ product{{ name }} }} }} }}',
        f'{{ orders(first:25){{ details(first:15){{ product{{ name }} }} }} }}',
        f'{{ orders(first:40){{ details(first:15){{ product{{ name }} }} }} }}',
        # order_details:byOrder loader: 10 -> 25 -> 40 order keys
        f'{{ orders(first:10){{ details(first:5){{ quantity }} }} }}',
        f'{{ orders(first:25){{ details(first:5){{ quantity }} }} }}',
        f'{{ orders(first:40){{ details(first:5){{ quantity }} }} }}',
        # suppliers loader: narrow -> saturating (<=29 distinct)
        f'{{ category(id:"{cat}"){{ products(first:15){{ supplier{{ country }} }} }} }}',
        f'{{ products(first:40){{ supplier{{ country }} }} }}',
    ]


def calibration_queries(size: str) -> list[str]:
    return calib_shapes(*_ent(size)) + calib_sweep_shapes(*_ent(size))


def northwind_config(tier: str = "T3") -> APIConfig:
    return APIConfig(
        name="northwind",
        graphql_url=GRAPHQL_URL,
        input_source=NorthwindInputSource(),
        arg_resolver=NorthwindArgResolver(),
        tokens={},
        root_auth={}, field_auth={}, uncoverable_fields={},
        bounded_fields={},                    # nothing paid/external to isolate
        known_loaders=KNOWN_LOADERS,
        size_roots=SIZE_ROOTS,
        default_cap=20,
        cost_currency="work_ms",              # T3-capable: work-ms from cost_trace
        tier=tier,
        calibration_queries=calibration_queries,
    )
