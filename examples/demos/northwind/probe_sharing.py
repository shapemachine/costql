"""Fire heavy-sharing queries and print the observed coalescing from the trace."""
import json, sys, requests

URL = "http://127.0.0.1:8010/"

def fire(q):
    r = requests.post(URL, json={"query": q}, timeout=120)
    return r.json()

QUERIES = {
    "orders->details->product->category (hub fan)":
        '{ orders(first:40){ details(first:15){ product { category { name } } } } }',
    "customer->orders->details->product":
        '{ customer(id:"ALFKI"){ orders(first:30){ details(first:10){ product{ name } } } } }',
    "category->products->supplier":
        '{ category(id:"1"){ products(first:30){ supplier{ country } } } }',
    "distinct simple (low sharing)":
        '{ product(id:"40"){ name category{ name } supplier{ name } } }',
}

for label, q in QUERIES.items():
    d = fire(q)
    if d.get("errors"):
        print(f"\n### {label}\nERRORS:", json.dumps(d["errors"])[:800]); continue
    ct = d.get("extensions", {}).get("cost_trace", {})
    tot_req = sum(s["requested_keys"] for s in ct.get("loaders", {}).values())
    tot_batch = sum(s["batch_calls"] for s in ct.get("loaders", {}).values())
    tot_keys = sum(s["batched_keys"] for s in ct.get("loaders", {}).values())
    print(f"\n### {label}")
    print(f"  work_ms={ct.get('work_ms')}  total_requested={tot_req} total_batch_calls={tot_batch} "
          f"total_batched_keys={tot_keys} saved_by_coalescing={tot_req-tot_keys}")
    for lid, st in ct.get("loaders", {}).items():
        print(f"    {lid:24} req={st['requested_keys']:5} calls={st['batch_calls']:4} "
              f"keys={st['batched_keys']:4} hits={st['cache_hits']:5}")
