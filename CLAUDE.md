# CLAUDE.md — NWSL RosterLab

> This file is read by Claude Code at the start of every session. It is the project's source of truth. Keep it current.

## What this project is

A public, fan/creator-facing **NWSL roster + cap intelligence** web app. It turns messy public soccer data into **ranked, plain-English** answers about player value and (later) cap fit. It is a *designed product with an opinion*, not a stats table.

Full strategy + build plan lives one folder up: `../NWSL_Project_Concept_and_Build_Plan.md`. Read it if you need the "why."

## Who it's for
- **Primary users:** dedicated NWSL fans + NWSL content creators (writers/podcasters) who have no good, shareable tool today.
- **Design target (not day-1 users):** resource-strapped NWSL clubs without analytics staff.
- **Strategic audience:** sports-data employers (Stats Perform, Sportradar) — this is a portfolio/credibility asset.

## The core job
"Help me quickly understand which NWSL players are over/undervalued — and tell me **why**, in plain English I can act on or share."

## The ONE non-negotiable architecture principle
**Code does the math. The LLM does the words.**
All metrics, rankings, and comparisons are computed in deterministic Python (pandas + ready-made metrics). The LLM (Claude API) is a *thin layer* that only phrases already-computed numbers into sentences. It must NEVER calculate. Always keep the raw numbers visible next to any generated text.

## Architecture (keep the data layer decoupled from the UI)
Five layers, kept in separate modules so the data layer can later be promoted to a public product with minimal rework:
1. `src/data/` — **ingest**: pull from sources into a local cache (CSV/Parquet). No UI logic here.
2. `src/analysis/` — **transform/analyze**: clean, normalize (per-90 / per-position), compute value rankings + similarity. Pure functions.
3. `src/explain/` — **explain**: thin Claude API layer (`claude-haiku-4-5-20251001`) turning computed rows into one-line "why" sentences.
4. `src/agent/` — **agent**: agentic tool-use layer (`claude-sonnet-4-6`) for natural-language scouting queries. Three tools wrap `src/data/` and `src/analysis/`. Tools compute; the model only narrates real returned numbers. Canned searches (`canned.py`) are fully deterministic with zero LLM cost.
5. `app.py` — **present**: Streamlit UI only. It calls the layers above; it contains no data or math logic.

**Rule:** `app.py` must not contain data-fetching or metric math. That separation is deliberate.

## Value score methodology
**Position-weighted g+/90, z-scored within position.** Defined in `POSITION_WEIGHTS` dict at the top of `src/analysis/ranking.py` — edit freely to test alternative views.

Each g+ action type (shooting, dribbling, passing, receiving, interrupting, fouling) is divided by minutes/90, then multiplied by a position-specific weight and summed into `weighted_ga_p90`. That column is then z-scored within position to produce `value_score`. The raw (unweighted) `goals_added_p90` is kept as a separate reference column.

**Metric choices and their limits (documented in the UI methodology note):**
- Weights encode an editorial scouting judgment, not outcome-derived coefficients.
- Off-ball defending is structurally under-measured: g+ is on-ball only. CBs who defend without the ball won't reflect their full value.
- No team context adjustment: high-press teams inflate interrupting g+; possession teams inflate passing g+.
- Volume and availability are not in the score: per-90 removes minutes bias but doesn't reward durability.

**Goals Subtracted (g−):** No separate ASA endpoint. `goals_added_raw` already goes negative (range: −1.83 to +7.67 in 2025 data); `goals_added_total` sums these in. No separate g− column is needed.

**Canned searches** sort by per-90 action-type metrics (`ga_passing_p90`, `ga_interrupting_p90`, etc.), not season totals, to avoid favoring high-minute players.

**Data freshness:** ASA updates in-season with some lag (typically 1–3 days after matches).
The parquet file cache in `src/data/sources.py` persists until `refresh=True` is passed.
`@st.cache_data(ttl=86400)` on `load_value_table` re-runs the analysis layer at most once
per day, but does NOT re-pull from ASA — it re-reads the existing parquet.
The "Refresh data" sidebar button is the only path that forces a full re-pull from ASA
(calls all four fetch functions with `refresh=True`, then clears the Streamlit cache).

## Model split
- `claude-haiku-4-5-20251001` — per-card one-line insight lines (`src/explain/insight.py`). Fast, cheap, single-pass.
- `claude-sonnet-4-6` — Scout Assistant agentic loop (`src/agent/scout.py`). Multi-step tool reasoning.

## Scout Assistant cost architecture
- **Canned searches** (`src/agent/canned.py`): deterministic, zero LLM cost. Four one-click queries handle casual browsing — the majority of Scout tab usage.
- **Free-text Scout** (`src/agent/scout.py`): rate-limited to **8 queries per session** (tracked in `st.session_state`). Query results cached in session state keyed on normalized query string — **only successful results are cached, never error strings**. Cached results bypass the rate limit.
- **Prompt caching** (`cache_control: {type: ephemeral}` on system prompt + tool defs): GA feature, no beta header required. May not engage if the static prefix is under Anthropic's token minimum (~1,024 tokens) — applied as a minor optimization, not a guaranteed cost-saver.
- **Tool output caps**: `LEAN_COLS` only (16 columns), max 15 rows per `query_players` call.
- **Agent loop cap**: 4 iterations maximum.

## Scout agent rules (enforced via system prompt)
The agent must: call `describe_capabilities` when unsure of available positions/metrics; state plainly when a requested filter (age, salary, nationality, etc.) is not in the dataset; never invent or recall stats from training data; return output in SHORTLIST (markdown table) + REASONING (one sentence per player citing real numbers) format.

## Stack
- Python + **Streamlit** (MVP). Free hosting on **Streamlit Community Cloud**.
- Data libs: **itscalledsoccer** (American Soccer Analysis API — PRIMARY), **statsbombpy** (StatsBomb open data — deep historical), **soccerdata** (FBref — use sparingly, see licensing).
- Viz: **mplsoccer** / Streamlit charts. LLM: **anthropic** SDK (Haiku for cheap explanation lines).
- Cache: local CSV/Parquet. No database needed at MVP.

## Data sources & licensing (important)
- **American Soccer Analysis** via `itscalledsoccer` — primary source for g+, xG, player/team data. Fan-friendly public API.
- **StatsBomb open data** via `statsbombpy` — free for public use WITH attribution + logo (their Public Data User Agreement). Good for deep historical (2023 NWSL season).
- **FBref/Opta** via `soccerdata` — **down-weight this.** Redistribution is NOT allowed, and Opta terminated FBref's advanced feed in Jan 2026, so it's unstable. Use only for basic current-season cross-checks, never republish.
- **Wikidata** via SPARQL (`https://query.wikidata.org/sparql`) — free, no API key, CC0 license. Used for player birthdates only. Query: all female footballers (P106=Q937857, P21=Q6581072) with P569 (birthdate). ~86% NWSL name-match coverage. 30-day cache TTL. Manual overrides in `data/birthdates_manual.csv`. Age is computed season-aware: today for the current season, Dec 31 of season year for past seasons.
- **NWSLPA salary releases** — for the later cap features. Verify granularity before relying on it.
- If we ever expose a public dataset/API (the "#3" platform play), it may ONLY use openly-licensed slices (StatsBomb open data, ASA where permitted, Wikidata CC0) — never Opta-derived data.

## Build phases
- **Phase 0 (setup):** repo + this file + first ASA pull printing rows. ("hello, data")
- **Phase 1 (data spine):** cache ASA NWSL player data; clean/normalize; produce a defensible **value ranking by position**. This is the product's spine.
- **Phase 2 (one screen):** Streamlit view — filter by position → ranked player cards → player detail + similar players. No LLM yet.
- **Phase 3 (polish + LLM + ship):** add thin Claude "why" lines; clean visuals; shareable card; deploy; write launch post with 2-3 findings.
- Beyond: salary/cap, free-agency tracker, "who should X sign", college→pro model, optional Next.js.

## Working conventions
- **Start in Plan Mode.** Propose a plan, wait for approval, then code.
- Work in **small, testable chunks**. Verify each before moving on.
- Explain code you write (the human here is a rusty light coder re-learning).
- Write simple validation/sanity checks for data (row counts, null checks, value ranges).
- Commit often with clear messages.
- Keep secrets in a `.env` file (never commit it). The Anthropic API key goes there as `ANTHROPIC_API_KEY`.
- Scope discipline: MVP = one screen, one job. Honor the non-goals.

## Non-goals (do not build for MVP)
No betting/odds, no live match tracking, no video, no men's leagues, no Wyscout-depth clone, no tracking-data dependency.

## Definition of done (MVP)
A public URL where anyone can pick a position and see a ranked, plain-English-explained list of NWSL players by value, with a player detail + similar players; deployed; backed by a public GitHub repo; plus a short launch writeup.
