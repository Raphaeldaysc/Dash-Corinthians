"""Agregações da campanha. Tudo recebe DataFrames e devolve estruturas prontas para JSON."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .config import CLASSICOS, COMPETITIONS, MINUTE_BINS, REST_BINS, TIMEZONE

Json = dict[str, object]
VENUES = ("all", "H", "A")


def clean(value: object) -> object:
    """Converte tipos numpy/pandas e NaN em tipos JSON."""
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return None if math.isnan(number) or math.isinf(number) else round(number, 4)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if value is pd.NA or value is pd.NaT:
        return None
    return value


def _score(row: pd.Series) -> str:
    base = f"{int(row['gf'])}-{int(row['ga'])}"
    if pd.notna(row.get("pen_gf")) and pd.notna(row.get("pen_ga")):
        base += f" ({int(row['pen_gf'])}-{int(row['pen_ga'])} pên.)"
    return base


def _game_ref(row: pd.Series) -> Json:
    return {
        "event_id": str(row["event_id"]),
        "source": row["source"],
        "date": row["date"].isoformat(),
        "opponent": row["opponent"],
        "logo": row["opponent_logo"],
        "comp": row["comp_short"],
        "comp_color": row["comp_color"],
        "venue": row["venue"],
        "result": row["result"],
        "score": _score(row),
        "gf": row["gf"],
        "ga": row["ga"],
    }


def played(matches: pd.DataFrame) -> pd.DataFrame:
    return matches[matches["completed"]].sort_values("date").reset_index(drop=True)


def add_rest_days(matches: pd.DataFrame) -> pd.DataFrame:
    """Dias desde o jogo anterior (qualquer competição), calculado na temporada inteira antes de filtrar."""
    df = matches.sort_values("date").copy()
    df["rest_days"] = (
        df.groupby("season")["date"].diff().dt.total_seconds().div(86_400).round().astype(float)
    )
    return df


# ---------------------------------------------------------------- blocos básicos


def kpis(d: pd.DataFrame) -> Json:
    games = len(d)
    w, dr, l = (int((d["result"] == r).sum()) for r in ("W", "D", "L"))
    pts = 3 * w + dr
    gf, ga = float(d["gf"].sum()), float(d["ga"].sum())
    out: Json = {
        "games": games,
        "w": w,
        "d": dr,
        "l": l,
        "pts": pts,
        "max_pts": 3 * games,
        "rate": pts / (3 * games) if games else None,
        "ppg": pts / games if games else None,
        "gf": gf,
        "ga": ga,
        "gd": gf - ga,
        "avg_gf": gf / games if games else None,
        "avg_ga": ga / games if games else None,
        "clean_sheets": int((d["ga"] == 0).sum()),
        "failed_to_score": int((d["gf"] == 0).sum()),
        "biggest_win": None,
        "biggest_loss": None,
    }
    if games:
        margin = d["gf"] - d["ga"]
        if (margin > 0).any():
            out["biggest_win"] = _game_ref(d.loc[margin.idxmax()])
        if (margin < 0).any():
            out["biggest_loss"] = _game_ref(d.loc[margin.idxmin()])
    return out


def points_series(d: pd.DataFrame, target_rate: float) -> list[Json]:
    if d.empty:
        return []
    n = np.arange(1, len(d) + 1)
    cum = d["points"].cumsum().to_numpy()
    rows = []
    for i, (_, row) in enumerate(d.iterrows()):
        rows.append(
            {
                **_game_ref(row),
                "n": int(n[i]),
                "pts_cum": int(cum[i]),
                "target_cum": round(n[i] * 3 * target_rate, 2),
                "rate_cum": cum[i] / (3 * n[i]),
                "rolling5": float(d["points"].iloc[max(0, i - 4) : i + 1].mean()),
            }
        )
    return rows


def streaks(d: pd.DataFrame) -> Json:
    results = d["result"].tolist()
    goals_for = d["gf"].tolist()
    goals_against = d["ga"].tolist()

    def longest(flags: list[bool]) -> int:
        best = cur = 0
        for flag in flags:
            cur = cur + 1 if flag else 0
            best = max(best, cur)
        return best

    def current(flags: list[bool]) -> int:
        count = 0
        for flag in reversed(flags):
            if not flag:
                break
            count += 1
        return count

    unbeaten = [r != "L" for r in results]
    wins = [r == "W" for r in results]
    winless = [r != "W" for r in results]
    scoring = [g > 0 for g in goals_for]
    clean_sheet = [g == 0 for g in goals_against]
    last = results[-1] if results else ""
    cur_label = {"W": "vitórias", "D": "empates", "L": "derrotas"}.get(last, "")
    cur_len = 0
    for r in reversed(results):
        if r != last:
            break
        cur_len += 1

    return {
        "longest_unbeaten": longest(unbeaten),
        "longest_wins": longest(wins),
        "longest_winless": longest(winless),
        "longest_scoring": longest(scoring),
        "longest_clean_sheets": longest(clean_sheet),
        "current_unbeaten": current(unbeaten),
        "current_winless": current(winless),
        "current_scoring": current(scoring),
        "current": {"result": last, "count": cur_len, "label": cur_label},
        "form": results[-10:],
    }


def _minute_bin(minute: float | None) -> str | None:
    if minute is None or pd.isna(minute):
        return None
    for lo, hi, label in MINUTE_BINS:
        if lo <= minute <= hi:
            return label
    return None


def goal_minutes(d: pd.DataFrame, goals: pd.DataFrame) -> Json:
    labels = [label for _, _, label in MINUTE_BINS]
    empty = {"bins": labels, "scored": [0] * len(labels), "conceded": [0] * len(labels), "available": False}
    if goals.empty or d.empty:
        return empty
    g = goals.merge(d[["event_id", "team_id"]], on="event_id", suffixes=("", "_cor"))
    if g.empty:
        return empty
    g["bin"] = g["minute"].map(_minute_bin)
    ours = g["team_id"] == g["team_id_cor"]
    scored = g[ours]["bin"].value_counts()
    conceded = g[~ours]["bin"].value_counts()
    return {
        "bins": labels,
        "scored": [int(scored.get(b, 0)) for b in labels],
        "conceded": [int(conceded.get(b, 0)) for b in labels],
        "available": True,
        "coverage": int(g["event_id"].nunique()),
    }


def scorers(d: pd.DataFrame, goals: pd.DataFrame, limit: int = 10) -> Json:
    if goals.empty or d.empty:
        return {"scorers": [], "assists": [], "own_goals_for": 0}
    g = goals.merge(d[["event_id", "team_id"]], on="event_id", suffixes=("", "_cor"))
    ours = g[g["team_id"] == g["team_id_cor"]]
    own_goals_for = int((ours["kind"] == "own_goal").sum())
    real = ours[(ours["kind"] != "own_goal") & (ours["player"] != "")]
    table = (
        real.groupby("player")
        .agg(goals=("kind", "size"), penalties=("kind", lambda s: int((s == "penalty").sum())))
        .reset_index()
        .sort_values(["goals", "penalties"], ascending=[False, True])
        .head(limit)
    )
    assists = (
        ours[ours["assist"] != ""]
        .groupby("assist")
        .size()
        .reset_index(name="assists")
        .rename(columns={"assist": "player"})
        .sort_values("assists", ascending=False)
        .head(limit)
    )
    return {
        "scorers": table.to_dict("records"),
        "assists": assists.to_dict("records"),
        "own_goals_for": own_goals_for,
    }


def first_goal_impact(d: pd.DataFrame, goals: pd.DataFrame) -> Json:
    """Resultado quando o Corinthians sai na frente x quando sofre o primeiro gol."""
    base: Json = {"scored_first": None, "conceded_first": None, "goalless": int(((d["gf"] + d["ga"]) == 0).sum())}
    if goals.empty or d.empty:
        return base
    g = goals.merge(d[["event_id", "team_id", "result"]], on="event_id", suffixes=("", "_cor"))
    if g.empty:
        return base
    g = g.assign(order=g["minute"].fillna(999) + g["extra"].fillna(0) / 100).sort_values("order")
    first = g.groupby("event_id").head(1).set_index("event_id")
    first["ours"] = first["team_id"] == first["team_id_cor"]
    for key, mask in (("scored_first", first["ours"]), ("conceded_first", ~first["ours"])):
        sub = first[mask]
        counts = sub["result"].value_counts()
        games = len(sub)
        pts = 3 * int(counts.get("W", 0)) + int(counts.get("D", 0))
        base[key] = {
            "games": games,
            "w": int(counts.get("W", 0)),
            "d": int(counts.get("D", 0)),
            "l": int(counts.get("L", 0)),
            "rate": pts / (3 * games) if games else None,
        }
    return base


def rest_analysis(d: pd.DataFrame) -> list[Json]:
    rows = []
    for lo, hi, label in REST_BINS:
        sub = d[(d["rest_days"] >= lo) & (d["rest_days"] <= hi)]
        games = len(sub)
        pts = float(sub["points"].sum())
        rows.append({"label": label, "games": games, "rate": pts / (3 * games) if games else None})
    return rows


def per_game(d: pd.DataFrame, stats: pd.DataFrame) -> list[Json]:
    if d.empty:
        return []
    merged = d
    if not stats.empty:
        ours = stats.merge(d[["event_id", "team_id"]], on=["event_id", "team_id"])
        merged = d.merge(ours.drop(columns=["team_id"]), on="event_id", how="left")
    rows = []
    for _, row in merged.iterrows():
        item = {**_game_ref(row), "stage": row["stage"], "stadium": row["stadium"], "attendance": row["attendance"]}
        for col in ("possession", "shots", "shots_on_target"):
            item[col] = row[col] if col in merged.columns else None
        rows.append(item)
    return rows


def calendar(d: pd.DataFrame) -> list[Json]:
    return [
        {
            "day": row["date"].strftime("%Y-%m-%d"),
            "points": row["points"],
            "result": row["result"],
            "label": f"{row['comp_short']} · {'x' if row['venue'] != 'A' else '@'} {row['opponent']} {_score(row)}",
        }
        for _, row in d.iterrows()
    ]


def build_view(d: pd.DataFrame, goals: pd.DataFrame, stats: pd.DataFrame, target_rate: float) -> Json:
    return {
        "kpis": kpis(d),
        "series": points_series(d, target_rate),
        "distribution": {r: int((d["result"] == r).sum()) for r in ("W", "D", "L")},
        "last5": [_game_ref(row) for _, row in d.tail(5).iterrows()],
        "games": per_game(d, stats),
        "streaks": streaks(d),
        "minutes": goal_minutes(d, goals),
        "scorers": scorers(d, goals),
        "first_goal": first_goal_impact(d, goals),
        "rest": rest_analysis(d),
        "calendar": calendar(d),
    }


# ---------------------------------------------------------------- blocos compostos


def home_away(d: pd.DataFrame) -> Json:
    home, away = d[d["venue"] == "H"], d[d["venue"] == "A"]
    k_home, k_away = kpis(home), kpis(away)
    rate_h = k_home["rate"] if isinstance(k_home["rate"], float) else None
    rate_a = k_away["rate"] if isinstance(k_away["rate"], float) else None
    return {
        "home": k_home,
        "away": k_away,
        "neutral_games": int((d["venue"] == "N").sum()),
        "gap_pp": (rate_h - rate_a) * 100 if rate_h is not None and rate_a is not None else None,
    }


def competition_cards(season_matches: pd.DataFrame, standings: pd.DataFrame | None) -> list[Json]:
    cards = []
    for comp in COMPETITIONS:
        sub = season_matches[season_matches["comp_key"] == comp.key]
        if sub.empty:
            continue
        done = played(sub)
        upcoming = sub[~sub["completed"]]
        last_stage = done["stage"].iloc[-1] if not done.empty else ""
        last_note = next((n for n in reversed(done["note"].tolist()) if n), "")
        position = None
        if comp.kind == "league" and standings is not None and not standings.empty:
            row = standings[standings["team_id"].astype(str).isin(set(done["team_id"].astype(str)))]
            if not row.empty:
                position = int(row["rank"].iloc[0])
        knockout = done[done["stage"].ne("") & done["stage"].ne("Fase de grupos") & done["stage"].ne("Fase de liga")]
        cards.append(
            {
                "key": comp.key,
                "name": comp.name,
                "short": comp.short,
                "color": comp.color,
                "kind": comp.kind,
                "kpis": kpis(done),
                "status": "Em disputa" if not upcoming.empty else "Encerrada",
                "stage": upcoming["stage"].iloc[0] if not upcoming.empty and upcoming["stage"].iloc[0] else last_stage,
                "position": position,
                "note": last_note,
                "path": [{**_game_ref(r), "stage": r["stage"], "note": r["note"]} for _, r in knockout.iterrows()],
                "form": done["result"].tail(5).tolist(),
            }
        )
    return cards


def seasons_compare(matches: pd.DataFrame, league_key: str) -> Json:
    out: Json = {"seasons": [], "all": {}, "league": {}}
    summary = []
    for season, group in played(matches).groupby("season"):
        season_i = int(season)
        rate_cum = (group["points"].cumsum() / (3 * np.arange(1, len(group) + 1))).round(4).tolist()
        league = group[group["comp_key"] == league_key]
        out["all"][str(season_i)] = rate_cum
        out["league"][str(season_i)] = league["points"].cumsum().astype(int).tolist()
        k = kpis(group)
        per_comp = {c: kpis(g)["rate"] for c, g in group.groupby("comp_key")}
        summary.append({"season": season_i, **{key: k[key] for key in ("games", "w", "d", "l", "rate", "gf", "ga", "gd")}, "by_comp": per_comp})
    out["seasons"] = summary
    return out


def classicos(matches: pd.DataFrame) -> list[Json]:
    done = played(matches)
    out = []
    for key, nickname in CLASSICOS.items():
        sub = done[done["opponent_key"] == key]
        if sub.empty:
            continue
        out.append(
            {
                "rival": sub["opponent"].iloc[-1],
                "logo": sub["opponent_logo"].iloc[-1],
                "nickname": nickname,
                "kpis": kpis(sub),
                "games": [{**_game_ref(r), "season": int(r["season"])} for _, r in sub.tail(8).iloc[::-1].iterrows()],
            }
        )
    return out


def upcoming(matches: pd.DataFrame, standings: pd.DataFrame | None, limit: int = 10) -> list[Json]:
    now = pd.Timestamp.now(tz=TIMEZONE)
    future = matches[(~matches["completed"]) & (matches["date"] >= now - pd.Timedelta(hours=3))]
    future = future.sort_values("date").head(limit)
    ranks: dict[str, int] = {}
    if standings is not None and not standings.empty:
        ranks = {str(t): int(r) for t, r in zip(standings["team_id"], standings["rank"])}
    return [
        {
            "date": r["date"].isoformat(),
            "opponent": r["opponent"],
            "logo": r["opponent_logo"],
            "comp": r["comp_short"],
            "comp_name": r["comp_name"],
            "comp_color": r["comp_color"],
            "comp_key": r["comp_key"],
            "venue": r["venue"],
            "stadium": r["stadium"],
            "stage": r["stage"],
            "opponent_rank": ranks.get(str(r["opponent_id"])) if r["comp_key"] == "brasileirao" else None,
        }
        for _, r in future.iterrows()
    ]


def opponent_tiers(d_league: pd.DataFrame, standings: pd.DataFrame | None) -> list[Json]:
    """Aproveitamento contra G6, meio da tabela e Z4 (posição atual dos adversários)."""
    if standings is None or standings.empty or d_league.empty:
        return []
    ranks = {str(t): int(r) for t, r in zip(standings["team_id"], standings["rank"])}
    n = len(standings)
    tiers = d_league.assign(rank=d_league["opponent_id"].astype(str).map(ranks)).dropna(subset=["rank"])
    buckets = (
        ("Contra o G6", tiers["rank"] <= 6),
        ("Contra o meio", (tiers["rank"] > 6) & (tiers["rank"] <= n - 4)),
        ("Contra o Z4", tiers["rank"] > n - 4),
    )
    out = []
    for label, mask in buckets:
        k = kpis(tiers[mask])
        out.append({"label": label, "games": k["games"], "rate": k["rate"], "w": k["w"], "d": k["d"], "l": k["l"]})
    return out


def standings_table(standings: pd.DataFrame | None, team_ids: set[str]) -> list[Json]:
    if standings is None or standings.empty:
        return []
    rows = standings.to_dict("records")
    for row in rows:
        row["is_team"] = str(row.get("team_id")) in team_ids
    return rows
