"""Method — how the value score works, and whether it holds up.

Merges the former "Model Validation" and "About" tabs. Both answered the same
user question — *should I trust this?* — and About's "Does it hold up?" section
restated the validation figures in prose, so keeping them apart guaranteed they
would drift. Validation numbers lead, the narrative follows, and the metric
glossary plus the data-freshness/refresh controls (previously stranded in the
global sidebar) live here too.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from src.analysis.validation import run_validation, load_validation_cache, save_validation_cache
from src.analysis.season import IN_SEASON_YEAR
from src.data.sources import (
    fetch_player_goals_added, fetch_player_xgoals, fetch_players, fetch_teams,
    fetch_player_birthdates,
)
from src.ui import loaders, state, theme

_ROOT = Path(__file__).resolve().parents[3]

# The floor run_validation() uses. Kept as a named constant so the disclosure
# below and the call site cannot drift apart.
VALIDATION_MIN_MINUTES = 500


def _current_default_floor() -> int:
    """The qualifying floor the app would use for the selected season.

    Computed rather than read from session state: state.get_min_minutes() seeds
    a value when it is first called, and calling it here (where games_est is
    unknown) both returned the wrong number for the in-season year and polluted
    the value Players later relies on.
    """
    season = state.get_season()
    if season == IN_SEASON_YEAR:
        snap = loaders.latest_snapshot()
        if snap:
            return state.default_min_minutes(season, loaders.snapshot_games_est(snap))
    return state.default_min_minutes(season)


@st.cache_data(show_spinner="Running validation across all seasons…", ttl=86400)
def _load_validation() -> dict:
    cached = load_validation_cache()
    if cached is not None:
        return cached
    result = run_validation(min_minutes=VALIDATION_MIN_MINUTES)
    save_validation_cache(result)
    return result


def _pct(val) -> str:
    if val is None or (isinstance(val, float) and val != val):
        return "—"
    return f"{val:.0%}"


def _fmt(val, decimals=2) -> str:
    if val is None or (isinstance(val, float) and val != val):
        return "—"
    return f"{val:.{decimals}f}"


def render() -> None:
    theme.section(
        "Method",
        subtitle="How player value is calculated, and how well it stands up to scrutiny.",
        eyebrow_text="NWSL ROSTERLAB",
    )

    # Above the sub-tabs deliberately: these are the things a reader needs
    # before any number on this page means anything, and sub-tab content is not
    # even in the page until its tab is clicked.
    st.warning(
        "**The position weights are my editorial judgement, not fitted parameters.** "
        "Each action type is multiplied by a weight chosen to reflect what a position "
        "is for — a centre back's defensive actions count 1.6×, her shooting 0.2×; a "
        "striker is the reverse. Those numbers were picked by me as a scouting opinion "
        "and were **not** estimated from outcome data. Disagreeing with them is "
        "reasonable; they live in one editable dictionary (`POSITION_WEIGHTS`).",
        icon=":material/warning:",
    )
    st.info(
        "**Value scores are not comparable across seasons.** The in-season year is "
        "Bayesian-shrunk toward the position mean with **K = 300** minutes and then "
        "re-standardised, so its spread differs from a completed season even though "
        "the metric carries the same name. They are also not comparable across "
        "positions in any season.",
        icon=":material/info:",
    )
    _sources_and_freshness()
    theme.rule()

    tab_holds, tab_how, tab_data = st.tabs(
        ["Does it hold up?", "How it works", "Data & glossary"]
    )
    with tab_holds:
        _validation()
    with tab_how:
        _how_it_works()
    with tab_data:
        _data_and_glossary()


# --- Validation --------------------------------------------------------------

def _sources_and_freshness() -> None:
    """Attribution + data age, rendered above the sub-tabs so it is always in the
    page rather than one click away."""
    season = state.get_season()
    line = (
        "Player data from **[American Soccer Analysis](https://www.americansocceranalysis.com/)** "
        "(Goals Added, xG, xA) · ages from Wikidata · Best XI from Wikipedia · "
        "college data from the NCAA."
    )
    if season == IN_SEASON_YEAR:
        snap = loaders.latest_snapshot()
        if snap:
            line += f" In-season data refreshes weekly; latest snapshot **{snap}**."
    else:
        line += f" {season} is a completed season — its data is static."
    st.caption(line)


def _validation() -> None:
    st.caption(
        "Testing whether the value score picks out the same players the NWSL's own "
        "Best XI voters chose. Fully deterministic — no AI involved."
    )
    floor = _current_default_floor()
    if floor == VALIDATION_MIN_MINUTES:
        st.caption(
            f"**Computed at a {VALIDATION_MIN_MINUTES:,}-minute qualifying floor**, which "
            f"matches the pool currently displayed for this season."
        )
    else:
        st.caption(
            f"**These statistics were computed at a {VALIDATION_MIN_MINUTES:,}-minute "
            f"qualifying floor**, which is not the pool the app currently displays — the "
            f"default floor for the selected season is **{floor:,} minutes**. Read them as "
            f"a property of the model, not a measurement of the players now on screen."
        )

    if "validation_result" not in st.session_state:
        with st.spinner("Loading validation results…"):
            st.session_state["validation_result"] = _load_validation()
    v = st.session_state["validation_result"]

    m = st.columns(5)
    slot_pct = v.get("median_rank_pct")
    m[0].metric("Slot-matched", _pct(v.get("pooled_hit_rate_slot_matched")),
                help="% ranked within their bucket's Best XI quota (DEF≤4, MF/FW≤6)")
    m[1].metric("Median rank %ile", f"{slot_pct:.1%}" if slot_pct is not None else "—",
                help="Median within-bucket rank ÷ bucket size — lower is better")
    m[2].metric("ROC-AUC", _fmt(v.get("roc_auc"), 3),
                help="0.5 = random, 1.0 = perfect. Pooled across all seasons and positions")
    m[3].metric("Top-3 hit-rate", _pct(v.get("pooled_hit_rate_top3")),
                help="% ranking top-3 in their bucket")
    m[4].metric("Matched", str(v.get("n_first_matched", "—")),
                help="First XI outfield players matched to the ASA dataset")

    st.caption(
        f"**Read this as:** given one Best XI player and one who wasn't selected, the model "
        f"rates the Best XI player higher about {_fmt(v.get('roc_auc'), 2)} of the time. The "
        f"top-3 hit-rate is deliberately shown too — it is the least flattering number here."
    )

    theme.rule()
    st.markdown("**By position bucket** — First XI")
    d, f = st.columns(2)
    with d:
        st.markdown("Defenders (CB + FB)")
        st.info(f"Slot-matched: **{_pct(v.get('defender_hit_rate_slot_matched'))}** · "
                f"Top-3: **{_pct(v.get('defender_hit_rate_top3'))}** · "
                f"Top-5: **{_pct(v.get('defender_hit_rate_top5'))}**")
        st.caption("Expected to be the weakest bucket — g+ is on-ball only, so off-ball "
                   "defending is under-measured.")
    with f:
        st.markdown("Midfielders & forwards (DM/CM/AM/W/ST)")
        st.info(f"Slot-matched: **{_pct(v.get('mffw_hit_rate_slot_matched'))}** · "
                f"Top-3: **{_pct(v.get('mffw_hit_rate_top3'))}** · "
                f"Top-5: **{_pct(v.get('mffw_hit_rate_top5'))}**")

    with st.expander("Second XI hit-rate (softer tier)", expanded=False):
        s = st.columns(2)
        s[0].metric("Top-3 (2nd XI)", _pct(v.get("pooled_hit_rate_top3_second")))
        s[1].metric("Top-5 (2nd XI)", _pct(v.get("pooled_hit_rate_top5_second")))

    theme.rule()
    st.markdown("**Team level** — does a high-value roster win more points?")
    st.caption(f"Spearman correlation between team-average value score and regular-season "
               f"points. N = {v.get('team_n_observations', '—')} team-seasons.")
    t = st.columns(2)
    t[0].metric("Spearman ρ", _fmt(v.get("team_spearman_rho"), 2))
    t[1].metric("p-value", _fmt(v.get("team_spearman_p"), 2))
    st.caption("A sanity check rather than a prediction — good players winning games is close "
               "to tautological. It would be a red flag if this were *not* positive.")

    with st.expander("Per-season breakdown", expanded=False):
        rows = []
        for s, mm in sorted(v.get("per_season", {}).items()):
            first, def_m, mf_m = mm.get("first", {}), mm.get("defender_first", {}), mm.get("mffw_first", {})
            rows.append({
                "Season": str(s), "Top-3": _pct(first.get("top3")), "Top-5": _pct(first.get("top5")),
                "Matched": first.get("n_matched", 0), "Unmatched": first.get("n_unmatched", 0),
                "Def top-3": _pct(def_m.get("top3")), "MF/FW top-3": _pct(mf_m.get("top3")),
                "Median rank": _fmt(first.get("median_rank"), 1),
            })
        if rows:
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

    bxi = v.get("best_xi_ranked", pd.DataFrame())
    if not bxi.empty:
        with st.expander("Every Best XI player and our rank for her", expanded=False):
            d2 = bxi.copy()
            d2["Season"] = d2["season"].astype(str)
            d2["XI"] = d2["team_selection"].str.capitalize()
            d2["Bucket"] = d2["position_group"]
            d2["Player"] = d2["best_xi_name"]
            d2["ASA name"] = d2["asa_name"].fillna("—")
            # Nullable Int64 so Arrow serializes cleanly and gaps render blank.
            d2["Our rank"] = d2["bucket_rank"].astype("Int64")
            d2["Value score"] = d2["value_score"].apply(lambda x: f"{x:.2f}" if pd.notna(x) else "—")
            d2["Status"] = d2.apply(
                lambda r: "✓ matched" if r["matched"]
                else ("⚠ below minutes" if r["below_minutes"] else "✗ not found"), axis=1)
            st.dataframe(
                d2[["Season", "XI", "Bucket", "Player", "ASA name", "Our rank", "Value score", "Status"]],
                hide_index=True, width="stretch",
                column_config={"Our rank": st.column_config.NumberColumn("Our rank", format="%d")},
            )

    unmatched = v.get("unmatched", [])
    if unmatched:
        counts: dict[str, int] = {}
        for u in unmatched:
            counts[u.get("diagnosis", "?")] = counts.get(u.get("diagnosis", "?"), 0) + 1
        with st.expander(
            f"Unmatched Best XI players ({len(unmatched)}: "
            + ", ".join(f"{n} {k}" for k, n in counts.items()) + ")", expanded=False
        ):
            st.caption("ABSENT = not in ASA's database at all. NAME-MISMATCH = present but below "
                       "the minutes threshold that season. Aliases live in "
                       "data/validation/name_aliases.csv.")
            for u in unmatched:
                mins = u.get("actual_minutes")
                st.markdown(
                    f"{'🔴' if u.get('diagnosis') == 'ABSENT' else '🟡'} **{u['best_xi_name']}** "
                    f"({u['season']} {u['team_selection']} {u['position_group']}) "
                    f"`{u.get('diagnosis', '?')}`"
                    + (f" — {int(mins)} min" if mins is not None else "")
                    + f" → _{', '.join(u['candidates']) if u['candidates'] else 'no close matches'}_"
                )

    with st.expander("Caveats worth reading", expanded=False):
        st.warning(f"""
**Best XI is consensus, not ground truth.** It is a vote — collective opinion, not objective
performance. A player can be excellent and miss the XI.

**Small samples.** Roughly 8 matched outfield Best XI players per season, so per-season
hit-rates are directional, not precise.

**Goalkeepers are excluded.** The model doesn't score them.

**Position buckets are approximate.** Seven model positions collapse to two validation buckets,
so a winger and a defensive mid compete in the same one.

**{len(unmatched)} Best XI players are unmatched** (below the minutes threshold or a name
mismatch). Hit-rate covers only the matched subset, which may skew toward higher-minute players.

**Team correlation uses regular-season points only** — no playoffs, home/away or
strength-of-schedule adjustment.
""")

    st.button("Re-run validation", help="Re-pulls Wikipedia data and recomputes. ~30 seconds.",
              on_click=_rerun_validation)


def _rerun_validation() -> None:
    _load_validation.clear()
    st.session_state["validation_result"] = run_validation(min_minutes=VALIDATION_MIN_MINUTES)
    save_validation_cache(st.session_state["validation_result"])


# --- Narrative ---------------------------------------------------------------

def _how_it_works() -> None:
    st.markdown(
        '_"Goals added by the players. Words added by Claude. Value added by, hopefully, me."_'
    )
    st.markdown(
        "RosterLab turns free public data into ranked, explained, position-aware player value "
        "for the National Women's Soccer League, plus an AI scout you can ask things like "
        '"find me a ball-progressing center back under 23."'
    )

    st.markdown("### Why I built it")
    st.markdown(
        "I'm a product manager, and I'm a little obsessed with soccer. The NWSL is one of the "
        "fastest-rising leagues in American sports, and it just reshaped its roster rules: no more "
        "college draft, new free agency, a tight salary cap. But the public tools for understanding "
        "player value are still raw stat tables built for analysts, and the polished ones (Wyscout, "
        "StatsBomb, Opta) cost far more than fans or smaller clubs can spend. RosterLab is my attempt "
        "to close that gap with something opinionated, transparent, and free."
    )

    st.markdown("### How the value score works")
    st.markdown(
        "Every player's value starts from American Soccer Analysis's Goals Added (g+), a measure of "
        "total on-ball contribution across six action types: shooting, dribbling, passing, receiving, "
        "defending, and fouling. I convert those to per-90, weight them by position (a center back is "
        "judged mostly on defending and progression, a striker on finishing), and standardize within "
        "position into a single value score. The weights are an editorial scouting judgment, not a "
        "black box, so you are free to disagree with them. The plain-English note on each player is "
        "written by an AI layer that only phrases the numbers already computed. It never invents a stat."
    )
    st.info(
        "**On-pitch value only.** There is no salary, contract or cap-space data anywhere in this "
        "app — the scout assistant will tell you so if you ask. That is a roadmap ambition, not a "
        "feature.", icon=":material/info:",
    )

    st.markdown("### Where it's weakest")
    st.markdown(
        "Goals added is an **on-ball** metric, so a center back who defends by positioning — never "
        "needing the tackle — is under-measured. The validation confirms it: defenders are the "
        "weakest bucket. The score also carries no team context (a high-pressing side inflates "
        "defensive g+), doesn't reward durability, and is **not comparable across positions**."
    )

    st.markdown("### Not affiliated")
    st.markdown(
        "Built independently from public data. Not affiliated with or endorsed by the NWSL, any "
        "club, or American Soccer Analysis."
    )
    theme.rule()
    st.markdown(
        "Built by **Abhishek Sundaresan** · "
        "[LinkedIn](https://www.linkedin.com/in/abhishek-sundaresan/) · "
        "[GitHub](https://github.com/abhisheksundaresan1/NWSL-RosterLab)"
    )
    st.caption("Feedback welcome, especially from NWSL fans and people working in soccer analytics.")


# --- Data, glossary, freshness ----------------------------------------------

def _data_and_glossary() -> None:
    season = state.get_season()

    st.markdown("### Where the data comes from")
    st.markdown(
        "- **American Soccer Analysis** — goals added (g+), xG, xA, minutes, positions. The backbone.\n"
        "- **Wikidata** (CC0) — player birthdates for ages. About 10% of players have no match.\n"
        "- **Wikipedia** — NWSL Best XI selections, used only to validate.\n"
        "- **NCAA** — college statistics for the Prospect Board."
    )

    st.markdown("### Freshness")
    if season == IN_SEASON_YEAR:
        snap = loaders.latest_snapshot()
        if snap:
            st.markdown(f"In-season snapshots refresh weekly via an automated job. "
                        f"Latest snapshot: **{snap}**.")
            path = _ROOT / "data" / "snapshots" / f"value_{IN_SEASON_YEAR}_{snap}.parquet"
        else:
            path = None
    else:
        path = _ROOT / "data" / "raw" / f"nwsl_player_goals_added_{season}.parquet"
        st.markdown(f"{season} is a completed season — its data is static.")
    if path is not None and path.exists():
        st.caption(f"Data file last written: "
                   f"{datetime.fromtimestamp(path.stat().st_mtime):%b %d, %Y %H:%M}")

    with st.expander("Refresh data from source", expanded=False):
        st.caption("Re-pulls from ASA and Wikidata. Takes ~20 seconds and is rarely needed — "
                   "completed seasons don't change, and the in-season snapshot updates itself weekly.")
        if st.button("Refresh now", key="method_refresh"):
            with st.spinner("Pulling fresh data…"):
                if season != IN_SEASON_YEAR:
                    fetch_player_goals_added(season_name=season, refresh=True)
                    fetch_player_xgoals(season_name=season, refresh=True)
                fetch_players(refresh=True)
                fetch_teams(refresh=True)
                fetch_player_birthdates(refresh=True)
            st.cache_data.clear()
            st.rerun()

    theme.rule()
    st.markdown("### Metric glossary")
    st.markdown("""
| Metric | What it means |
|---|---|
| **Value score** | Position-weighted g+/90, z-scored within position. 0 = position average, +2 = elite. **Not comparable across positions.** |
| **Weighted g+ / 90** | Position-weighted sum of per-90 action-type g+. Strikers weight shooting; center backs weight interrupting. This drives the ranking. |
| **Goals added (g+)** | Value added across all on-ball actions — how much each touch changed the team's chance of scoring. ASA's primary metric. |
| **g+ / 90 (raw)** | Unweighted goals added per 90, all action types equal. Shown for reference. |
| **xG / 90** | Expected goals per 90 — shot *quality*, from location, angle and assist type. |
| **xAssists / 90** | Expected assists per 90 — credit for passes that created shots, whether or not they were scored. |
| **xG+xA / 90** | Combined expected goal involvement. A familiar cross-check; not part of the value score. |
| **g+ Shooting** | Value from shots — getting good chances and finishing them. |
| **g+ Dribbling** | Value from carrying the ball and beating opponents. |
| **g+ Passing** | Value from passing. Often slightly negative for strikers, who pass under pressure. |
| **g+ Receiving** | Value from getting into good positions to receive — off-ball movement, credited on the touch. |
| **g+ Interrupting** | Value from defensive actions: interceptions, tackles, blocks. Shown as "Defending". |
| **g+ Fouling** | Net value from fouls — drawn minus committed. Positive means she wins more than she gives away. |
""")
