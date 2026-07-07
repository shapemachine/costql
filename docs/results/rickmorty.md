# Case study: pricing a stranger's API (Rick & Morty, T1)

Does costQL work on an API it has never seen, that we don't own and can't change? This experiment pointed it at the public **Rick & Morty API** (`rickandmortyapi.com/graphql`): no new server, no fake data, no special access, just the real, live, public endpoint anyone on the internet can hit. The result: a **93-line adapter**, **zero engine changes**, **~96% accuracy** on held-out queries at T1, with a safe max that never under-charged, and one offline pack in the standard contract format.

## What it took to onboard: one small file

To teach costQL about a brand-new API, we wrote **one file**:
[`examples/adapters/rickmorty.py`](../../examples/adapters/rickmorty.py), **93
lines** (and about half of that is just the list of real example IDs and the
endpoint address; the only actual logic is a ~15-line function that says "this
argument is an ID, this one is a page number").

**Nothing else changed.** The engine itself (the part that does the introspecting,
measuring, modelling and pricing) was not touched at all. That is the whole point:
adopting a new API is a small, self-contained plug-in, not a rebuild. costQL read
the Rick & Morty schema live, learned its shape (characters, episodes, locations and
how they connect), and started pricing, with no schema file written by hand.

## Did it actually price accurately? Yes.

costQL measured a set of real queries against the live API to learn its costs, then
**graded itself** on a *different, held-out* set of queries it had not calibrated on
(different characters, episodes and locations: a genuine "unseen" test; see
[the evaluation methodology](../evaluation.md)).

- On the **predictable** queries (the ones a customer would actually want a price
  for), costQL's quote was **96% accurate on average**, and its safe "you will
  never pay more than this" ceiling was **never once too low** (5 out of 5).
- Every query got a price. costQL never refused, never crashed, never guessed at
  data it didn't have.

(For context, the number here is small because a public API like this is dominated
by a steady network round-trip, which is exactly the cost a black-box, outside
observer feels, and exactly what the T1 fidelity is meant to capture honestly. See
[One currency, three fidelities](../tiers.md).)

## Did it know what it *couldn't* predict? Yes.

Rick & Morty's data has a loop in it: a character points to its episodes, and each
episode points back to its characters. Asking for "a character's episodes, and all
the characters in each of those" balloons in a way that only *running* it reveals.

costQL **automatically flagged every one of these loop-shaped queries as
low-confidence** (the headline character↔episode loop came back "low", flagged as
100% inside the loop), while still handing back a safe upper-bound price and a
plain note: *"this is a worst-case estimate; run it for the exact cost."* It priced
what it could stand behind and was honest about what it couldn't. It never made up a
number to look confident.

## Is it shippable? Yes, as one portable file.

The output is a single self-contained **pricing pack** (a 66 KB file, committed at
[`packs/rickmorty_t1.json`](../../packs/rickmorty_t1.json)) that an app can carry
around and use to price queries **completely offline**: no server, no network
call, no dependency on costQL being "up." We confirmed a real app-side quote runs
straight from that file with nothing else running. And every price it produces comes
out in costQL's **frozen, stable format** (verified: zero contract violations
against [the v1.0 output contract](../contract.md)), so an app that already reads
TMDB prices reads Rick & Morty prices the exact same way.

## Bottom line

costQL is **not** a one-API trick. Pointed at a second, unrelated, live API it had
never seen, it:

1. figured the API out by itself (a 93-line adapter, zero engine changes),
2. priced unseen queries to ~96% accuracy with a safe max that never under-charged,
3. knew which queries it couldn't safely predict and said so, and
4. shipped the whole thing as one offline file in its standard, stable format.

All with **zero paid calls** and **zero fabricated data**.

## Try it yourself

This is the "try it right now" API (public, no keys, no demo server to run):

```bash
# quote against the committed pack, fully offline
costql quote --pack packs/rickmorty_t1.json '{ character(id:"1"){ name } }'

# or rebuild the pack from scratch against the live public endpoint
costql build --adapter examples/adapters/rickmorty.py:rickmorty_config --out rickmorty.json
```
