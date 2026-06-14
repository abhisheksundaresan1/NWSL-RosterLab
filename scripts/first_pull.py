"""
Phase 0 — "hello, data".

Run this to confirm you can pull NWSL data from American Soccer Analysis.
    python scripts/first_pull.py

If it prints rows and columns, your data pipe works and you're ready for Phase 1.
(Requires internet. Built on the documented itscalledsoccer API.)
"""

from itscalledsoccer.client import AmericanSoccerAnalysis
import pandas as pd

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 200)


def main() -> None:
    asa = AmericanSoccerAnalysis()

    # Player expected-goals data for the NWSL
    xg = asa.get_player_xgoals(leagues="nwsl")
    print(f"\nNWSL player xG rows: {len(xg)}")
    print("Columns:", list(xg.columns))
    print(xg.head(5).to_string(), "\n")

    # Player goals added (g+) — ASA's all-phase on-ball value metric
    gplus = asa.get_player_goals_added(leagues="nwsl")
    print(f"NWSL player g+ rows: {len(gplus)}")
    print("Columns:", list(gplus.columns))
    print(gplus.head(5).to_string(), "\n")

    print("If you can see rows above, the data pipe works. On to Phase 1.")


if __name__ == "__main__":
    main()
