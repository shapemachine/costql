"""Reference adapter — TMDB Passthrough (the costQL demo API in ../demos/tmdb).

Everything specific to this deployment lives here: the endpoint, the mined inputs
(real TMDB movie/person/genre ids), argument resolution (which arg names map to
which id), and the curated calibration shapes. The TMDB API is fully public, so
there is no auth: root_auth, field_auth, tokens and uncoverable_fields are all
empty — the coverage generator then drops nothing for permissions. Onboarding
this API needed no change inside `costql`; only this file.

Sharing (T3) is read from `response.extensions.cost_trace`, which the demo server
emits (demos/tmdb/app/schema.py CostTraceExtension).

Build a pack with:

    costql build --adapter examples/adapters/tmdb.py:tmdb_config --tier T3
"""
from __future__ import annotations

from costql.config import APIConfig, MinedInputs, UNSET

# --- Deployment ------------------------------------------------------------
GRAPHQL_URL = "http://127.0.0.1:8000/"        # the running tmdb demo uvicorn server

# The endpoints the tracer keys loaders on (batch_group), for dead-loader checks.
KNOWN_LOADERS = [
    "/movie/{id}", "/movie/{id}/credits", "/person/{id}", "/person/{id}/movie_credits",
    "/movie/{id}/recommendations", "/movie/{id}/similar", "/collection/{id}",
    "/genre/movie/list", "/discover/movie", "/search/movie",
    "/trending/movie/{window}", "anthropic:messages",
]

# Size-bearing roots (size model). `limit` bounds fanout directly; `page`
# selects a ~20-item page (TMDB's page size == default_cap).
SIZE_ROOTS = {
    "cast": {"arg": "limit", "offset": 0, "cap": None},
    "crew": {"arg": "limit", "offset": 0, "cap": None},
    "filmography": {"arg": "limit", "offset": 0, "cap": None},
    "chemistryScore": {"arg": "limit", "offset": 0, "cap": None},
    "recommendations": {"arg": "page", "offset": 0, "cap": 20},
    "similar": {"arg": "page", "offset": 0, "cap": 20},
    "movies": {"arg": "page", "offset": 0, "cap": 20},
    "searchMovies": {"arg": "page", "offset": 0, "cap": 20},
    "discoverMovies": {"arg": "page", "offset": 0, "cap": 20},
    "trendingMovies": {"arg": "page", "offset": 0, "cap": 20},
}

# Real TMDB ids (public). Disjoint whale / small / held-out picks so a held-out
# evaluation set exercises different inputs than calibration.
MOVIES = {"whale": 27205, "small": 550, "heldout": 157336}          # Inception / Fight Club / Interstellar
PEOPLE = {"whale": 6193, "small": 287, "heldout": 500}              # DiCaprio / Pitt / Cruise
GENRES = {"whale": 28, "small": 18, "heldout": 35}                  # Action / Drama / Comedy
SEARCHES = {"whale": "the", "small": "matrix", "heldout": "star"}
LIMITS = {"whale": 5, "small": 2}       # kept low: the schema is deeply cyclic, so
                                        # fanout^depth explodes fast on a passthrough


class TMDBInputSource:
    """No dataset to mine — TMDB is a public remote API. We supply a curated set
    of real, stable ids as the 'mined' inputs. No tokens (public), nothing
    uncovered."""

    def mine(self) -> MinedInputs:
        return MinedInputs(
            data={"movies": [MOVIES], "people": [PEOPLE], "genres": [GENRES],
                  "searches": [SEARCHES]},
            tokens={}, uncovered=[])


class TMDBArgResolver:
    def value(self, field_path, arg_name, arg_type_base, mined, size="whale",
              variant="calib"):
        key = "heldout" if variant == "heldout" else size          # whale | small | heldout
        fp = field_path.lower()

        if arg_name == "id":
            if "person" in fp:
                return str(PEOPLE.get(key, PEOPLE["whale"]))
            if "genre" in fp:
                return str(GENRES.get(key, GENRES["whale"]))
            return str(MOVIES.get(key, MOVIES["whale"]))
        if arg_name == "genre":                 # discoverMovies(genre: ID)
            return str(GENRES.get(key, GENRES["whale"]))
        if arg_name == "query":                 # searchMovies(query: String!)
            return SEARCHES.get(key, SEARCHES["whale"])
        if arg_name == "limit":                 # cast/crew/filmography/chemistryScore
            return LIMITS["small"] if size == "small" else LIMITS["whale"]
        if arg_name == "page":
            return 1
        return UNSET                            # year, window, sortBy -> defaults


# --- Curated calibration shapes ---------------------------------------------
# Predictable, distinct-type-fanout queries a caller would actually price. Some
# declare their size (`limit:N`), some don't. Deep cyclic shapes are deliberately
# absent — their data-dependent recursion corrupts a linear fit; they are what
# costQL FLAGS, not calibrates against.

def _entity(which):
    return (MOVIES[which], PEOPLE[which], GENRES[which], SEARCHES[which])


def high_shapes(m, p, g, s):
    return [
        f'{{ movie(id:"{m}"){{ title genres{{ name }} }} }}',
        f'{{ movie(id:"{m}"){{ cast(limit:6){{ person{{ name }} }} }} }}',
        f'{{ movie(id:"{m}"){{ cast(limit:12){{ person{{ name }} }} }} }}',
        f'{{ movie(id:"{m}"){{ crew(limit:6){{ person{{ name }} }} }} }}',
        f'{{ person(id:"{p}"){{ name filmography{{ movie{{ title }} }} }} }}',
        f'{{ person(id:"{p}"){{ filmography(limit:8){{ movie{{ title }} }} }} }}',
        f'{{ genre(id:"{g}"){{ name movies{{ title }} }} }}',
        f'{{ searchMovies(query:"{s}"){{ title }} }}',
        f'{{ discoverMovies(genre:"{g}"){{ title }} }}',
        f'{{ trendingMovies{{ title }} }}',
    ]


def calib_extra(m, p, g, s):
    """Single-page (depth-1) recommendation/similar queries — isolating measures
    so those resolvers get a clean unit cost for the fit."""
    return [
        f'{{ movie(id:"{m}"){{ recommendations{{ title }} }} }}',
        f'{{ movie(id:"{m}"){{ similar{{ title }} }} }}',
    ]


def calibration_queries(size: str) -> list[str]:
    return high_shapes(*_entity(size)) + calib_extra(*_entity(size))


def tmdb_config(tier: str = "T3") -> APIConfig:
    return APIConfig(
        name="tmdb",
        graphql_url=GRAPHQL_URL,
        input_source=TMDBInputSource(),
        arg_resolver=TMDBArgResolver(),
        tokens={},
        root_auth={}, field_auth={}, uncoverable_fields={},
        # aiSummary is a PAID Anthropic call. Its per-invocation work-ms is a
        # constant, so we sample it once in isolation (build_isolation) instead of
        # fanning it out across the deep panel (which would cost thousands of Haiku
        # calls). The per-call FEE is authored via the adjustments file, not measured.
        bounded_fields={"Movie.aiSummary": "paid external call (api.anthropic.com); "
                        "sampled in isolation, fee authored via adjustments"},
        known_loaders=KNOWN_LOADERS,
        size_roots=SIZE_ROOTS,
        default_cap=20,                         # TMDB returns 20 results per page
        cost_currency="work_ms",                # work-ms from cost_trace (wall-clock is the T1 fallback)
        tier=tier,
        calibration_queries=calibration_queries,
    )
