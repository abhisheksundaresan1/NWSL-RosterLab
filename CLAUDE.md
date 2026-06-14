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
Four layers, kept in separate modules so the data layer can later be promoted to a public product with minimal rework:
1. `src/data/` — **ingest**: pull from sources into a local cache (CSV/Parquet). No UI logic here.
2. `src/analysis/` — **transform/analyze**: clean, normalize (per-90 / per-position), compute value rankings + similarity. Pure functions.
3. `src/explain/` — **explain**: thin Claude API layer turning computed rows into one-line "why" sentences.
4. `app.py` — **present**: Streamlit UI only. It calls the layers above; it contains no data or math logic.

**Rule:** `app.py` must not contain data-fetching or metric math. That separation is deliberate.

## Stack
- Python + **Streamlit** (MVP). Free hosting on **Streamlit Community Cloud**.
- Data libs: **itscalledsoccer** (American Soccer Analysis API — PRIMARY), **statsbombpy** (StatsBomb open data — deep historical), **soccerdata** (FBref — use sparingly, see licensing).
- Viz: **mplsoccer** / Streamlit charts. LLM: **anthropic** SDK (Haiku for cheap explanation lines).
- Cache: local CSV/Parquet. No database needed at MVP.

## Data sources & licensing (important)
- **American Soccer Analysis** via `itscalledsoccer` — primary source for g+, xG, player/team data. Fan-friendly public API.
- **StatsBomb open data** via `statsbombpy` — free for public use WITH attribution + logo (their Public Data User Agreement). Good for deep historical (2023 NWSL season).
- **FBref/Opta** via `soccerdata` — **down-weight this.** Redistribution is NOT allowed, and Opta terminated FBref's advanced feed in Jan 2026, so it's unstable. Use only for basic current-season cross-checks, never republish.
- **NWSLPA salary releases** — for the later cap features. Verify granularity before relying on it.
- If we ever expose a public dataset/API (the "#3" platform play), it may ONLY use openly-licensed slices (StatsBomb open data, ASA where permitted) — never Opta-derived data.

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
