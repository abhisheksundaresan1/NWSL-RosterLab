# -*- coding: utf-8 -*-
"""
Value score validation against Best XI awards and team standings.
All pure functions: DataFrames in, dicts/DataFrames out. No I/O here.

Bucket collapse (validation only — does NOT change the model):
  CB, FB                    -> DEF
  DM, CM, AM, W, ST        -> MF/FW
  GK                        -> excluded from hit-rate

Hit-rate definition:
  Computed ONLY over matched outfield players (those found in the ASA dataset
  above the min_minutes threshold). Unmatched players are listed separately.
  Headline numbers use First XI only; Second XI shown as a softer supplementary tier.

  BUCKET_SLOTS defines how many Best XI spots each bucket has (DEF=4, MF/FW=6).
  Slot-matched hit-rate = % of matched First XI players ranked <= BUCKET_SLOTS[bucket].
  Median rank is also expressed as a percentile of the bucket population.

AUC: manual Wilcoxon-Mann-Whitney implementation (no sklearn).
Spearman: pandas .corr(method='spearman').
"""

from __future__ import annotations

import difflib
import json
from pathlib import Path

import pandas as pd

from src.data.ground_truth import (
    COMPLETED_SEASONS,
    fetch_best_xi,
    fetch_standings,
    load_name_aliases,
    normalize_name,
)

_VALIDATION_CACHE = (
    Path(__file__).resolve().parents[2] / "data" / "validation" / "validation_cache.json"
)

# How many First XI slots each collapsed bucket has
BUCKET_SLOTS: dict[str, int] = {"DEF": 4, "MF/FW": 6}

# ---------------------------------------------------------------------------
# Bucket collapse
# ---------------------------------------------------------------------------

def collapse_bucket(position: str) -> str | None:
    """Map model position (7-way) to validation bucket. Returns None for GK."""
    if position in ("CB", "FB"):
        return "DEF"
    if position in ("DM", "CM", "AM", "W", "ST"):
        return "MF/FW"
    return None  # GK or unknown


# ---------------------------------------------------------------------------
# Name matching
# ---------------------------------------------------------------------------

def _build_asa_name_index(value_table: pd.DataFrame) -> dict[str, str]:
    """
    Return {normalized_name: original_player_name} for all players in value_table.
    """
    return {normalize_name(n): n for n in value_table["player_name"].dropna().unique()}


def _resolve_name(
    best_xi_name: str,
    asa_index: dict[str, str],
    alias_map: dict[str, str],
) -> str | None:
    """
    Resolve a Best XI player name to its ASA counterpart.
    Tries: (1) alias map, (2) exact normalized match, (3) difflib close match.
    Returns the ASA original_name string if found, else None.
    """
    norm = normalize_name(best_xi_name)
    # 1. Manual alias
    aliased = alias_map.get(norm)
    if aliased and aliased in asa_index:
        return asa_index[aliased]
    # 2. Exact normalized match
    if norm in asa_index:
        return asa_index[norm]
    # 3. difflib
    candidates = difflib.get_close_matches(norm, asa_index.keys(), n=3, cutoff=0.80)
    if candidates:
        return asa_index[candidates[0]]
    return None


def _alias_candidates(
    best_xi_name: str,
    asa_index: dict[str, str],
    n: int = 3,
) -> list[str]:
    """Return top-n ASA name candidates for an unmatched Best XI player."""
    norm = normalize_name(best_xi_name)
    matches = difflib.get_close_matches(norm, asa_index.keys(), n=n, cutoff=0.50)
    return [asa_index[m] for m in matches]


# ---------------------------------------------------------------------------
# Manual AUC (Wilcoxon-Mann-Whitney)
# ---------------------------------------------------------------------------

def _manual_auc(scores: list[float], labels: list[int]) -> float:
    """
    Rank-based AUC (equivalent to Wilcoxon-Mann-Whitney U statistic).
    No external libraries required.
    """
    pairs = list(zip(scores, labels))
    pos = [s for s, l in pairs if l == 1]
    neg = [s for s, l in pairs if l == 0]
    if not pos or not neg:
        return float("nan")
    concordant = sum(1 for p in pos for n in neg if p > n)
    tied       = sum(0.5 for p in pos for n in neg if p == n)
    return (concordant + tied) / (len(pos) * len(neg))


# ---------------------------------------------------------------------------
# Core validation
# ---------------------------------------------------------------------------

def run_validation(
    min_minutes: int = 500,
    seasons: list[int] | None = None,
) -> dict:
    """
    Run full validation of value_score against Best XI and team standings.

    Parameters
    ----------
    min_minutes : qualifying threshold (same as the main ranking)
    seasons     : list of seasons to evaluate; defaults to COMPLETED_SEASONS

    Returns
    -------
    dict with keys:
      pooled_hit_rate_top3, pooled_hit_rate_top5   -- First XI only
      pooled_hit_rate_top3_second, pooled_hit_rate_top5_second  -- Second XI
      pooled_hit_rate_slot_matched  -- HEADLINE: % ranked <= BUCKET_SLOTS[bucket]
      median_rank, median_rank_pct  -- HEADLINE: median rank + percentile of bucket size
      per_season           -- {season: {top3, top5, n_matched, n_unmatched, ...}}
      defender_hit_rate_top3, defender_hit_rate_top5  -- First XI DEF bucket
      defender_hit_rate_slot_matched                  -- DEF: % ranked <= 4
      mffw_hit_rate_top3, mffw_hit_rate_top5          -- First XI MF/FW bucket
      mffw_hit_rate_slot_matched                      -- MF/FW: % ranked <= 6
      roc_auc              -- pooled across all seasons
      team_spearman_rho, team_spearman_p  -- team-aggregate correlation
      best_xi_ranked       -- DataFrame of all Best XI players with our rank
      unmatched            -- list of {best_xi_name, season, team_selection,
                                       diagnosis, actual_minutes, candidates}
    """
    from src.data.sources import (
        fetch_player_goals_added,
        fetch_player_xgoals,
        fetch_players,
        fetch_teams,
        fetch_player_birthdates,
    )
    from src.analysis.ranking import build_player_value_table

    if seasons is None:
        seasons = COMPLETED_SEASONS

    best_xi_all  = fetch_best_xi()
    standings_all = fetch_standings()
    alias_map    = load_name_aliases()

    # Accumulate across seasons
    all_ranks: list[dict] = []        # matched Best XI players with their rank
    unmatched: list[dict] = []        # unmatched players with alias candidates
    auc_scores: list[float] = []      # value_score per player (all seasons, all positions)
    auc_labels: list[int]   = []      # 1 = First XI, 0 = not

    per_season: dict[int, dict] = {}

    team_agg_rows: list[dict] = []    # for Spearman: {team_name, season, mean_value_score, points}

    for season in seasons:
        # ---- Build value table for this season ----
        try:
            ga = fetch_player_goals_added(season_name=str(season))
            xg = fetch_player_xgoals(season_name=str(season))
            pl = fetch_players()
            tm = fetch_teams()
            bd = fetch_player_birthdates()
            vt = build_player_value_table(
                ga, xg, pl, tm,
                birthdates=bd,
                min_minutes=min_minutes,
                season=str(season),
            )
        except Exception as exc:
            print(f"[validation] Could not build value table for {season}: {exc}")
            continue

        # Add validation bucket column
        vt["bucket"] = vt["position"].apply(collapse_bucket)
        outfield = vt[vt["bucket"].notna()].copy()

        # Within-bucket rank by value_score
        outfield["bucket_rank"] = outfield.groupby("bucket")["value_score"].rank(
            ascending=False, method="min"
        )

        # Bucket sizes (for percentile computation)
        bucket_sizes = outfield.groupby("bucket").size().to_dict()

        asa_index = _build_asa_name_index(outfield)

        # Full (unfiltered) value table for unmatched diagnosis
        try:
            vt_full = build_player_value_table(
                ga, xg, pl, tm,
                birthdates=bd,
                min_minutes=0,
                season=str(season),
            )
            full_asa_index = _build_asa_name_index(vt_full)
            # minutes lookup: normalized_name -> minutes_played
            minutes_lookup = {
                normalize_name(row["player_name"]): row["minutes_played"]
                for _, row in vt_full.iterrows()
            }
        except Exception:
            vt_full = pd.DataFrame()
            full_asa_index = asa_index
            minutes_lookup = {}

        # ---- AUC: label First XI players ----
        season_best_xi = best_xi_all[best_xi_all["season"] == season]
        first_xi_names = set(
            normalize_name(n)
            for n in season_best_xi[season_best_xi["team_selection"] == "first"]["player_name"]
        )
        for _, row in outfield.iterrows():
            auc_scores.append(float(row["value_score"]))
            auc_labels.append(1 if normalize_name(row["player_name"]) in first_xi_names else 0)

        # ---- Match Best XI players ----
        season_rows_first:  list[dict] = []
        season_rows_second: list[dict] = []

        for _, bxi in season_best_xi.iterrows():
            if bxi["position_group"] == "GK":
                continue  # excluded

            asa_name = _resolve_name(bxi["player_name"], asa_index, alias_map)

            if asa_name is None:
                # Diagnose against full (unfiltered) player list
                full_match = _resolve_name(bxi["player_name"], full_asa_index, alias_map)
                norm_bxi = normalize_name(bxi["player_name"])
                actual_minutes = minutes_lookup.get(
                    normalize_name(full_match) if full_match else norm_bxi
                )
                if full_match is not None:
                    diagnosis = "NAME-MISMATCH"
                else:
                    diagnosis = "ABSENT"
                unmatched.append({
                    "best_xi_name": bxi["player_name"],
                    "season": season,
                    "team_selection": bxi["team_selection"],
                    "position_group": bxi["position_group"],
                    "diagnosis": diagnosis,
                    "actual_minutes": actual_minutes,
                    "candidates": _alias_candidates(bxi["player_name"], full_asa_index),
                })
                record = {
                    "season": season,
                    "team_selection": bxi["team_selection"],
                    "position_group": bxi["position_group"],
                    "best_xi_name": bxi["player_name"],
                    "asa_name": None,
                    "bucket_rank": None,
                    "bucket_size": None,
                    "value_score": None,
                    "matched": False,
                    "below_minutes": False,
                }
            else:
                player_row = outfield[outfield["player_name"] == asa_name]

                if player_row.empty:
                    # Player found in ASA but below min_minutes threshold
                    norm_asa = normalize_name(asa_name)
                    actual_minutes = minutes_lookup.get(norm_asa)
                    unmatched.append({
                        "best_xi_name": bxi["player_name"],
                        "season": season,
                        "team_selection": bxi["team_selection"],
                        "position_group": bxi["position_group"],
                        "diagnosis": "BELOW-MINUTES",
                        "actual_minutes": actual_minutes,
                        "candidates": [asa_name],
                    })
                    record = {
                        "season": season,
                        "team_selection": bxi["team_selection"],
                        "position_group": bxi["position_group"],
                        "best_xi_name": bxi["player_name"],
                        "asa_name": asa_name,
                        "bucket_rank": None,
                        "bucket_size": None,
                        "value_score": None,
                        "matched": False,
                        "below_minutes": True,
                    }
                else:
                    pr = player_row.iloc[0]
                    bkt = pr["bucket"]
                    record = {
                        "season": season,
                        "team_selection": bxi["team_selection"],
                        "position_group": bxi["position_group"],
                        "best_xi_name": bxi["player_name"],
                        "asa_name": asa_name,
                        "bucket_rank": int(pr["bucket_rank"]),
                        "bucket_size": bucket_sizes.get(bkt),
                        "value_score": round(float(pr["value_score"]), 3),
                        "matched": True,
                        "below_minutes": False,
                    }

            all_ranks.append(record)
            if bxi["team_selection"] == "first":
                season_rows_first.append(record)
            else:
                season_rows_second.append(record)

        # ---- Per-season metrics ----
        def _hit_rates(records: list[dict], k3: int = 3, k5: int = 5) -> dict:
            matched = [r for r in records if r["matched"]]
            n_matched   = len(matched)
            n_unmatched = len([r for r in records if not r["matched"]])
            if n_matched == 0:
                return {
                    "top3": None, "top5": None,
                    "n_matched": 0, "n_unmatched": n_unmatched,
                    "median_rank": None,
                }
            ranks = [r["bucket_rank"] for r in matched]
            return {
                "top3":        round(sum(1 for rk in ranks if rk <= k3) / n_matched, 3),
                "top5":        round(sum(1 for rk in ranks if rk <= k5) / n_matched, 3),
                "n_matched":   n_matched,
                "n_unmatched": n_unmatched,
                "median_rank": float(pd.Series(ranks).median()),
            }

        def _bucket_hit_rates(records: list[dict], bucket: str) -> dict:
            sub = [r for r in records if r["position_group"] == bucket and r["matched"]]
            if not sub:
                return {"top3": None, "top5": None, "n": 0}
            ranks = [r["bucket_rank"] for r in sub]
            return {
                "top3": round(sum(1 for rk in ranks if rk <= 3) / len(ranks), 3),
                "top5": round(sum(1 for rk in ranks if rk <= 5) / len(ranks), 3),
                "n":    len(ranks),
            }

        per_season[season] = {
            "first":  _hit_rates(season_rows_first),
            "second": _hit_rates(season_rows_second),
            "defender_first": _bucket_hit_rates(season_rows_first, "DEF"),
            "mffw_first":     _bucket_hit_rates(season_rows_first, "MF/FW"),
        }

        # ---- Team-level aggregation for Spearman ----
        season_pts = standings_all[standings_all["season"] == season]
        if not season_pts.empty:
            team_means = (
                outfield.groupby("team_name")["value_score"]
                .mean()
                .reset_index()
                .rename(columns={"value_score": "mean_value_score"})
            )
            merged = team_means.merge(season_pts[["team_name", "points"]], on="team_name", how="inner")
            for _, tr in merged.iterrows():
                team_agg_rows.append({
                    "season": season,
                    "team_name": tr["team_name"],
                    "mean_value_score": tr["mean_value_score"],
                    "points": tr["points"],
                })

    # ---------------------------------------------------------------------------
    # Pool metrics across all seasons
    # ---------------------------------------------------------------------------

    all_ranks_df = pd.DataFrame(all_ranks) if all_ranks else pd.DataFrame()

    def _pooled_hit_rates(df: pd.DataFrame, tier: str, k: int) -> float | None:
        sub = df[(df["team_selection"] == tier) & df["matched"]] if not df.empty else pd.DataFrame()
        if sub.empty:
            return None
        return round((sub["bucket_rank"] <= k).mean(), 3)

    def _pooled_bucket_hit_rate(df: pd.DataFrame, bucket: str, k: int) -> float | None:
        sub = (
            df[(df["team_selection"] == "first") & (df["position_group"] == bucket) & df["matched"]]
            if not df.empty else pd.DataFrame()
        )
        if sub.empty:
            return None
        return round((sub["bucket_rank"] <= k).mean(), 3)

    matched_first = all_ranks_df[(all_ranks_df["team_selection"] == "first") & all_ranks_df["matched"]] if not all_ranks_df.empty else pd.DataFrame()

    # Slot-matched hit-rate: bucket_rank <= BUCKET_SLOTS[bucket]
    def _slot_matched(df: pd.DataFrame) -> float | None:
        if df.empty:
            return None
        hits = sum(
            1 for _, r in df.iterrows()
            if r["bucket_rank"] is not None
            and r["position_group"] in BUCKET_SLOTS
            and r["bucket_rank"] <= BUCKET_SLOTS[r["position_group"]]
        )
        return round(hits / len(df), 3)

    def _bucket_slot_matched(df: pd.DataFrame, bucket: str) -> float | None:
        sub = df[df["position_group"] == bucket] if not df.empty else pd.DataFrame()
        if sub.empty:
            return None
        slots = BUCKET_SLOTS.get(bucket, 5)
        return round((sub["bucket_rank"] <= slots).mean(), 3)

    # Median rank percentile
    median_rank_val = float(matched_first["bucket_rank"].median()) if not matched_first.empty else None
    if median_rank_val is not None and "bucket_size" in matched_first.columns:
        median_bucket_size = float(matched_first["bucket_size"].median())
        median_rank_pct = round(median_rank_val / median_bucket_size, 3) if median_bucket_size > 0 else None
    else:
        median_rank_pct = None

    # AUC
    roc_auc = _manual_auc(auc_scores, auc_labels) if auc_scores else float("nan")

    # Spearman (team correlation)
    team_df = pd.DataFrame(team_agg_rows)
    if not team_df.empty and len(team_df) >= 4:
        spearman_rho = float(
            team_df[["mean_value_score", "points"]].corr(method="spearman").iloc[0, 1]
        )
        # p-value via t-approximation: t = rho * sqrt((n-2)/(1-rho^2))
        import math
        n = len(team_df)
        try:
            t_stat = spearman_rho * math.sqrt((n - 2) / (1 - spearman_rho ** 2))
            # two-tailed p from t with n-2 df — use simple approximation
            # (not exact but good enough for display; we're showing directional evidence)
            from math import erfc, sqrt
            spearman_p = float(erfc(abs(t_stat) / sqrt(2)))
        except Exception:
            spearman_p = float("nan")
    else:
        spearman_rho = float("nan")
        spearman_p   = float("nan")

    return {
        # HEADLINE metrics
        "pooled_hit_rate_slot_matched": _slot_matched(matched_first),
        "median_rank":     median_rank_val,
        "median_rank_pct": median_rank_pct,
        # Pooled First XI (secondary)
        "pooled_hit_rate_top3":  _pooled_hit_rates(all_ranks_df, "first", 3),
        "pooled_hit_rate_top5":  _pooled_hit_rates(all_ranks_df, "first", 5),
        # Pooled Second XI (softer tier)
        "pooled_hit_rate_top3_second": _pooled_hit_rates(all_ranks_df, "second", 3),
        "pooled_hit_rate_top5_second": _pooled_hit_rates(all_ranks_df, "second", 5),
        # Bucket breakdown (First XI)
        "defender_hit_rate_top3":        _pooled_bucket_hit_rate(all_ranks_df, "DEF", 3),
        "defender_hit_rate_top5":        _pooled_bucket_hit_rate(all_ranks_df, "DEF", 5),
        "defender_hit_rate_slot_matched": _bucket_slot_matched(matched_first, "DEF"),
        "mffw_hit_rate_top3":            _pooled_bucket_hit_rate(all_ranks_df, "MF/FW", 3),
        "mffw_hit_rate_top5":            _pooled_bucket_hit_rate(all_ranks_df, "MF/FW", 5),
        "mffw_hit_rate_slot_matched":    _bucket_slot_matched(matched_first, "MF/FW"),
        # Aggregate stats
        "n_first_matched": int(matched_first.shape[0]) if not matched_first.empty else 0,
        "roc_auc":        round(roc_auc, 3) if not (roc_auc != roc_auc) else None,
        "team_spearman_rho": round(spearman_rho, 3) if not (spearman_rho != spearman_rho) else None,
        "team_spearman_p":   round(spearman_p, 3) if not (spearman_p != spearman_p) else None,
        "team_n_observations": len(team_df),
        # Per-season breakdown
        "per_season": per_season,
        # Detail tables
        "best_xi_ranked": all_ranks_df,
        "unmatched": unmatched,
    }


# ---------------------------------------------------------------------------
# JSON persistence helpers
# ---------------------------------------------------------------------------

def _to_json_safe(obj):
    """Recursively convert non-JSON-serializable types."""
    if isinstance(obj, pd.DataFrame):
        return obj.to_dict(orient="records")
    if isinstance(obj, dict):
        return {k: _to_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_json_safe(i) for i in obj]
    if isinstance(obj, float) and obj != obj:  # nan
        return None
    if hasattr(obj, "item"):  # numpy scalar
        return obj.item()
    return obj


def save_validation_cache(result: dict) -> None:
    """Persist validation result to JSON for instant cold loads."""
    _VALIDATION_CACHE.parent.mkdir(parents=True, exist_ok=True)
    with open(_VALIDATION_CACHE, "w", encoding="utf-8") as f:
        json.dump(_to_json_safe(result), f, indent=2, default=str)
    print(f"[validation] Saved cache to {_VALIDATION_CACHE}")


def load_validation_cache() -> dict | None:
    """Load previously saved validation result. Returns None if not found or stale."""
    if not _VALIDATION_CACHE.exists():
        return None
    try:
        with open(_VALIDATION_CACHE, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Reconstruct best_xi_ranked DataFrame
        if "best_xi_ranked" in data and isinstance(data["best_xi_ranked"], list):
            data["best_xi_ranked"] = pd.DataFrame(data["best_xi_ranked"])
        return data
    except Exception as exc:
        print(f"[validation] Cache load failed: {exc}")
        return None


# ---------------------------------------------------------------------------
# CLI entry point — run verification steps
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 60)
    print("Running NWSL value score validation")
    print("=" * 60)

    result = run_validation(min_minutes=500)

    print(f"\n=== HEADLINE metrics ===")
    print(f"  ROC-AUC:              {result['roc_auc']}")
    print(f"  Team Spearman rho:    {result['team_spearman_rho']}  p={result['team_spearman_p']}  n={result['team_n_observations']}")
    print(f"  Slot-matched hit-rate (pooled): {result['pooled_hit_rate_slot_matched']}")
    print(f"    DEF  (top-4):  {result['defender_hit_rate_slot_matched']}")
    print(f"    MF/FW (top-6): {result['mffw_hit_rate_slot_matched']}")
    pct = result['median_rank_pct']
    print(f"  Median rank percentile: {f'{pct:.1%}' if pct else 'N/A'}  (raw rank: {result['median_rank']})")
    print(f"  n matched First XI: {result['n_first_matched']}")

    print(f"\n--- Secondary hit-rates (First XI) ---")
    print(f"  Top-3: {result['pooled_hit_rate_top3']}   Top-5: {result['pooled_hit_rate_top5']}")
    print(f"  Defender  Top-3: {result['defender_hit_rate_top3']}   Top-5: {result['defender_hit_rate_top5']}")
    print(f"  MF/FW     Top-3: {result['mffw_hit_rate_top3']}   Top-5: {result['mffw_hit_rate_top5']}")

    print(f"\n--- Second XI (softer tier) ---")
    print(f"  Top-3: {result['pooled_hit_rate_top3_second']}   Top-5: {result['pooled_hit_rate_top5_second']}")

    print(f"\n--- Per-season ---")
    for season, metrics in sorted(result["per_season"].items()):
        first = metrics["first"]
        print(f"  {season}: top3={first['top3']}  top5={first['top5']}  matched={first['n_matched']}  unmatched={first['n_unmatched']}")

    print(f"\n--- Unmatched Best XI players (with diagnosis) ---")
    for u in result["unmatched"]:
        cands = ", ".join(u.get("candidates", [])) or "none"
        mins  = u.get("actual_minutes")
        mins_str = f"{int(mins)} min" if mins is not None else "? min"
        diag  = u.get("diagnosis", "?")
        print(f"  [{u['season']} {u['team_selection']:6s} {u['position_group']:5s}] "
              f"{u['best_xi_name']:<22s}  {diag:<15s} {mins_str:>8s}  -> {cands}")

    print(f"\n--- Sanity check: Chawinga / Banda / S. Smith ---")
    df = result["best_xi_ranked"]
    if not df.empty:
        checks = ["chawinga", "banda", "smith"]
        for check in checks:
            matches = df[df["best_xi_name"].str.lower().str.contains(check)]
            for _, row in matches.iterrows():
                print(
                    f"  {row['best_xi_name']} ({row['season']} {row['team_selection']}) "
                    f"-> bucket_rank={row['bucket_rank']}  value_score={row['value_score']}  matched={row['matched']}"
                )

    # Save cache
    save_validation_cache(result)
    print("\nDone. Cache saved.")
