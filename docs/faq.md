# FAQ

Short answers to the questions that come up most. Each links to the page with
the full story.

<details>
<summary>Does costQL call my API when I quote a query?</summary>

No. Quoting is fully offline. The build makes real calls once, to learn your
API, and saves what it learned as the pricing pack. After that, your app prices
queries against that static file with no network calls. See
[Quickstart](quickstart.md).

</details>

<details>
<summary>What is a price in? Is it dollars?</summary>

Work-time: how much real work a query causes on your server, in milliseconds
(the pack calls this a cost-unit). It is not dollars. You map cost-units to
whatever you charge in your own app. This keeps costQL honest about *cost* and
leaves *pricing* to you.

</details>

<details>
<summary>Do I have to change my server?</summary>

Not to start. Your first pack (T1) times whole queries from the outside and
needs no server changes at all. Sharper tiers (T2, T3) ask your server to
report per-field work; that's the one change, covered in
[Instrumenting for T2/T3](instrumentation.md). Most APIs get real value at T1.
See [Tier fidelity](tiers.md).

</details>

<details>
<summary>Can it price a call to a paid API, like an LLM?</summary>

Yes. costQL can't *time* a per-call fee, so you author it once and every quote
folds it in, counted once even when the call is batched. See
[Paid & external fees](paid-fees.md).

</details>

<details>
<summary>Do Python and JavaScript give the same answer?</summary>

Yes. The pack is one file; both packages read it and return the same number for
the same query. See [The JS package](js.md).

</details>

<details>
<summary>What happens when my schema changes?</summary>

Rebuild the pack (minutes of measurement). Every quote carries a `schema_hash`,
so a consumer comparing hashes notices drift. Any fees you authored survive the
rebuild.

</details>

<details>
<summary>What is the difference between typical and safe max? (the two numbers on every quote)</summary>

Every quote gives you two numbers, because a query's real cost depends on how many
items its lists return, which you cannot know for sure until you run it.

- **Typical** (the `typical_price` field) is what the query *usually* costs. costQL
  assumes each list comes back at its average size. Use it to understand normal,
  everyday cost.
- **Safe max** (the `price` field) is the *most* it could cost. costQL assumes the
  largest sizes it has seen, and it is guaranteed never to fall below the real
  cost. This is the number you **bill on**, so you never undercharge.

They are **equal** when a query's size is fixed (bounded lists, single objects) and
differ only when a query could balloon in size. Rideshare rule: *"usually $12,
never more than $18."* See [the output contract](contract.md).

</details>

<details>
<summary>What about queries costQL can't predict well?</summary>

It says so. Hard-to-predict shapes (for example, cyclic queries) come back with
`confidence: "low"` rather than a falsely precise number. The
[limitations](limitations.md) page lists what costQL does not try to do.

</details>
