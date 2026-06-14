# NWSL RosterLab

A public NWSL roster & cap intelligence app — ranked, plain-English player-value insights built on public soccer data.

## Quick start
```bash
# 1. (recommended) create a virtual environment
python -m venv .venv
# Windows:
.venv\Scripts\activate

# 2. install dependencies
pip install -r requirements.txt

# 3. confirm data access ("hello, data")
python scripts/first_pull.py

# 4. run the app (once Phase 2 exists)
streamlit run app.py
```

## Project layout
```
NWSL-RosterLab/
  CLAUDE.md              # project context for Claude Code (read this first)
  README.md
  requirements.txt
  .gitignore
  .env                   # secrets (NOT committed) — holds ANTHROPIC_API_KEY
  app.py                 # Streamlit UI only (no data/math logic)
  scripts/
    first_pull.py        # Phase 0: verify data access
  src/
    data/                # ingest + cache (the "data layer")
    analysis/            # metrics, rankings, similarity (pure functions)
    explain/             # thin Claude API layer (phrasing only)
  data/
    raw/                 # cached source pulls (gitignored)
    processed/           # cleaned tables (gitignored)
```

## Principle
**Code does the math. The LLM does the words.** See `CLAUDE.md`.

## Data sources
American Soccer Analysis (`itscalledsoccer`, primary), StatsBomb open data (`statsbombpy`), FBref (`soccerdata`, used sparingly). See licensing notes in `CLAUDE.md`.
