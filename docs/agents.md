# Agent-assisted onboarding

The fastest way to onboard an API is to hand it to a coding agent (Claude
Code, Cursor, or any tool that can read a repo and run a CLI) instead of
writing the adapter by hand. Give the agent one skill to follow,
[`skills/costql-adapter`](https://github.com/shapemachine/costql/blob/main/skills/costql-adapter),
and one CLI command, `costql probe`, that removes the guesswork. Everything
else it needs is already in the repo.

## What the human provides

Three answers. Everything else is discoverable.

1. **The endpoint URL.**
2. **An auth header**, if the API needs one.
3. **Do you run the server?** If yes and you want sharper prices later, the
   agent will point you at [the one trace extension](instrumentation.md)
   to emit; if no, T1 is the honest ceiling and needs nothing from anyone.

## What the agent does

```
costql probe https://rickandmortyapi.com/graphql
```

```text
endpoint   : https://rickandmortyapi.com/graphql
schema     : 14 types · hash d0af9f9975b12932 · 9 root fields
cost trace : absent
tier today : T1 · cost_currency "wall_time_ms"   (set these in the adapter as-is)
id sources : real IDs are harvestable without a curated list:
    { characters { results { id } } }   -> Character ids, paginate via page
    { locations { results { id } } }   -> Location ids, paginate via page
    { episodes { results { id } } }   -> Episode ids, paginate via page
note       : no cost_trace observed: if your server gates tracing (e.g. behind an env var), enable it and re-probe
next       : write the adapter (skills/costql-adapter walks an agent through it; docs at https://costql.com/docs/adapters/)
```

The probe settles the two things people used to get wrong by hand:

- **Tier and currency come from observation.** The probe sends one query and
  checks the response for `extensions.cost_trace`. No trace means T1 and
  `wall_time_ms` (one stopwatch on the whole request: clock time). A trace
  with `work_ms` means T2; loaders on top of that mean T3, both priced in
  `work_ms` (summed real work). The adapter copies what the probe reports;
  nobody hand-picks a currency again. (`costql build` double-checks: it
  downgrades any pack whose server stops emitting the trace.)
- **Real IDs without a curated list.** The *id sources* are argument-free
  root paths that reach id-bearing entities. The agent runs those harvest
  queries, samples a few candidates to find a densely-connected `whale`, a
  light `small`, and a reserved `heldout`, and verifies each id resolves
  live before using it. You can still hand the agent a curated list; you
  just don't have to.

From there the agent follows [the adapter guide](adapters.md): template, arg
resolver, calibration shapes, `costql build`, `costql validate`, and the
sanity checks (a wider `first:` never prices lower; a cyclic query flags
`confidence: low`).

## What ships for agents

| where | what |
| --- | --- |
| `skills/costql-adapter` | write an adapter: probe-first, can self-harvest IDs, runs from just a URL |
| `skills/costql-build-pack` | build or rebuild a pack, handle outside-call fields, explain T1 downgrades |
| `skills/costql-quote-debug` | interpret a quote: price vs typical, confidence, breakdown, sharing folds |
| `AGENTS.md` | repo map, commands, and conventions for any agent working in the repo |
| [`llms.txt`](https://costql.com/llms.txt) | this documentation, indexed for tools that read the web |
| `.claude-plugin/` | the same skills packaged as a Claude Code plugin |

The skills are plain markdown in the [repo](https://github.com/shapemachine/costql):
tool-agnostic, no framework required.

## A prompt that works

> Onboard `https://api.example.com/graphql` to costQL. Auth header:
> `Authorization: Bearer …`. Start with `costql probe`, follow
> `skills/costql-adapter`, harvest your own IDs, and finish with a built,
> validated pack plus one quoted example query. I run this server, so if the
> probe reports T1, show me what to emit for T2/T3 before you build.
