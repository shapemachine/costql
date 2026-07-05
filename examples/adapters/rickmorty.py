"""Reference adapter: the public Rick & Morty GraphQL API
(https://rickandmortyapi.com/graphql).

THE minimal template: an API costQL does not own and cannot change, onboarded
with this one file and zero engine changes. The endpoint is a black box we
cannot instrument (no cost_trace extension), so the highest fidelity it affords
is **T1**: the whole-query wall-clock floor. Currency is therefore
`wall_time_ms`. That is the designed starting point for any API you can send
queries to.

Everything specific to this deployment lives here:
  * the endpoint (public, free, no key; auth is entirely empty);
  * the "mined" inputs: real, stable public ids (character 1-826, episode 1-51,
    location 1-126), never fabricated;
  * argument resolution: `id` (by entity), `page` (page-paginated roots), and a
    `name` filter value;
  * the curated calibration shapes.

There are no paid/bounded fields and no declared loaders (a black box has neither).

Build a pack with:

    costql build --adapter examples/adapters/rickmorty.py:rickmorty_config
"""
from __future__ import annotations

from costql.config import APIConfig, MinedInputs, UNSET

# --- Deployment ------------------------------------------------------------
GRAPHQL_URL = "https://rickandmortyapi.com/graphql"

# Black box: no cost_trace, so no batched loaders to declare.
KNOWN_LOADERS: list[str] = []

# Page-paginated roots: each returns a wrapper ({info, results:[...]}) whose
# `results` list is a ~20-item page selected by `page`.
SIZE_ROOTS = {
    "characters": {"arg": "page", "offset": 0, "cap": 20},
    "episodes":   {"arg": "page", "offset": 0, "cap": 20},
    "locations":  {"arg": "page", "offset": 0, "cap": 20},
}

# Real public ids. Disjoint whale / small / held-out picks. whale = id 1 (Rick /
# Pilot / Earth C-137, the most densely-connected entities).
CHARACTERS = {"whale": 1, "small": 100, "heldout": 200}     # ids 1-826
EPISODES   = {"whale": 1, "small": 20,  "heldout": 40}      # ids 1-51
LOCATIONS  = {"whale": 1, "small": 30,  "heldout": 60}      # ids 1-126
NAMES = {"whale": "rick", "small": "morty", "heldout": "summer"}


class RickMortyInputSource:
    """No dataset to mine: the API is a public remote endpoint. We supply a
    curated set of real, stable public ids as the 'mined' inputs."""

    def mine(self) -> MinedInputs:
        return MinedInputs(
            data={"characters": [CHARACTERS], "episodes": [EPISODES],
                  "locations": [LOCATIONS]},
            tokens={}, uncovered=[])


class RickMortyArgResolver:
    def value(self, field_path, arg_name, arg_type_base, mined, size="whale",
              variant="calib"):
        key = "heldout" if variant == "heldout" else size    # whale | small | heldout
        fp = field_path.lower()

        if arg_name == "id":
            if "episode" in fp:
                return str(EPISODES.get(key, EPISODES["whale"]))
            if "location" in fp:
                return str(LOCATIONS.get(key, LOCATIONS["whale"]))
            return str(CHARACTERS.get(key, CHARACTERS["whale"]))
        if arg_name == "page":                # characters/episodes/locations paging
            return 1
        if arg_name == "name":                # filter: { name: "..." } (a real name substring)
            return NAMES.get(key, NAMES["whale"])
        return UNSET                          # filter object, status, etc. -> defaults/dropped


# --- Curated calibration shapes ---------------------------------------------
# Predictable, DISTINCT-type shapes a caller would actually price. Each list edge
# crosses into a different type and does not re-enter. Cyclic shapes (e.g.
# character{ episode{ characters } }) are deliberately absent: they are what
# costQL FLAGS at quote time, not calibrates against.

def _ent(which):
    return (CHARACTERS[which], EPISODES[which], LOCATIONS[which])


def high_shapes(c, e, l):
    return [
        f'{{ character(id:"{c}"){{ name status species gender }} }}',
        f'{{ character(id:"{c}"){{ name origin{{ name dimension }} location{{ name }} }} }}',
        f'{{ character(id:"{c}"){{ name episode{{ name air_date }} }} }}',
        f'{{ episode(id:"{e}"){{ name air_date episode characters{{ name }} }} }}',
        f'{{ location(id:"{l}"){{ name type dimension residents{{ name }} }} }}',
        '{ characters { info { count } results { name status } } }',
        '{ episodes { info { count } results { name } } }',
        '{ locations { info { count } results { name } } }',
    ]


def calibration_queries(size: str) -> list[str]:
    return high_shapes(*_ent(size))


def rickmorty_config(tier: str = "T1") -> APIConfig:
    return APIConfig(
        name="rickmorty",
        graphql_url=GRAPHQL_URL,
        input_source=RickMortyInputSource(),
        arg_resolver=RickMortyArgResolver(),
        tokens={},
        root_auth={}, field_auth={}, uncoverable_fields={},
        bounded_fields={},                    # nothing paid/external to sample in isolation
        known_loaders=KNOWN_LOADERS,
        size_roots=SIZE_ROOTS,
        default_cap=20,                       # the API returns 20 results per page
        cost_currency="wall_time_ms",         # black box -> T1 wall-clock is the only currency it affords
        tier=tier,
        calibration_queries=calibration_queries,
    )
