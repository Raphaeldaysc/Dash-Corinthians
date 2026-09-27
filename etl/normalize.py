"""Transforma fixtures brutos (qualquer fonte) no DataFrame de partidas do ponto de vista do Corinthians."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import COMPETITION_BY_KEY, ESPN_TEAM_ID, TEAM_NAME, TIMEZONE
from .jsonutil import normalize_name

TEAM_KEY = normalize_name(TEAM_NAME)
TEAM_IDS = {ESPN_TEAM_ID}

MATCH_COLUMNS = [
    "event_id",
    "source",
    "comp_key",
    "comp_name",
    "comp_short",
    "comp_color",
    "season",
    "date",
    "state",
    "completed",
    "venue",
    "team_id",
    "opponent",
    "opponent_id",
    "opponent_logo",
    "opponent_key",
    "gf",
    "ga",
    "pen_gf",
    "pen_ga",
    "result",
    "points",
    "stage",
    "note",
    "stadium",
    "attendance",
]


def _is_team(team_id: object, name: object) -> bool:
    return str(team_id) in TEAM_IDS or normalize_name(str(name)) == TEAM_KEY


def team_matches(fixtures: pd.DataFrame) -> pd.DataFrame:
    """Uma linha por jogo do Corinthians, com gols pró/contra, mando e resultado."""
    if fixtures.empty:
        return pd.DataFrame(columns=MATCH_COLUMNS)

    df = fixtures.copy()
    is_home = df.apply(lambda r: _is_team(r["home_id"], r["home"]), axis=1)
    is_away = df.apply(lambda r: _is_team(r["away_id"], r["away"]), axis=1)
    df = df[is_home | is_away].copy()
    home = is_home[df.index]

    def pick(home_col: str, away_col: str) -> pd.Series:
        return df[home_col].where(home, df[away_col])

    out = pd.DataFrame(
        {
            "event_id": df["event_id"],
            "source": df["source"],
            "comp_key": df["comp_key"],
            "season": df["season"],
            "date": df["date"].dt.tz_convert(TIMEZONE),
            "state": df["state"],
            "completed": df["completed"].eq(True),
            "venue": np.where(df["neutral"].eq(True), "N", np.where(home, "H", "A")),
            "team_id": pick("home_id", "away_id"),
            "opponent": pick("away", "home"),
            "opponent_id": pick("away_id", "home_id"),
            "opponent_logo": pick("away_logo", "home_logo"),
            "gf": pick("hg", "ag"),
            "ga": pick("ag", "hg"),
            "pen_gf": pick("home_pen", "away_pen"),
            "pen_ga": pick("away_pen", "home_pen"),
            "stage": df["stage"].fillna(""),
            "note": df["note"].fillna(""),
            "stadium": df["venue_name"].fillna(""),
            "attendance": df["attendance"],
        }
    )
    out["opponent_key"] = out["opponent"].map(lambda n: normalize_name(str(n)))
    comps = out["comp_key"].map(COMPETITION_BY_KEY)
    out["comp_name"] = comps.map(lambda c: c.name)
    out["comp_short"] = comps.map(lambda c: c.short)
    out["comp_color"] = comps.map(lambda c: c.color)

    done = out["completed"] & out["gf"].notna() & out["ga"].notna()
    out["result"] = np.select(
        [done & (out["gf"] > out["ga"]), done & (out["gf"] == out["ga"]), done & (out["gf"] < out["ga"])],
        ["W", "D", "L"],
        default="",
    )
    out["points"] = out["result"].map({"W": 3, "D": 1, "L": 0})
    out.loc[~done, "completed"] = False
    return out[MATCH_COLUMNS].sort_values("date").reset_index(drop=True)


def missing_rows(primary: pd.DataFrame, fallback: pd.DataFrame, tolerance_days: float = 1.5) -> pd.DataFrame:
    """Jogos da fonte fallback que não existem na primária (mesmo adversário, ±1,5 dia)."""
    if fallback.empty or primary.empty:
        return fallback

    tolerance = pd.Timedelta(days=tolerance_days)
    keep = []
    for _, row in fallback.iterrows():
        same_opp = primary[primary["opponent_key"] == row["opponent_key"]]
        duplicate = ((same_opp["date"] - row["date"]).abs() <= tolerance).any()
        keep.append(not duplicate)
    return fallback[keep]


def league_frame(fixtures: pd.DataFrame) -> pd.DataFrame:
    """Jogos da liga inteira (todas as equipes), sem duplicatas, para ratings e simulação."""
    if fixtures.empty:
        return fixtures
    df = fixtures.drop_duplicates(subset=["event_id"]).copy()
    df = df[df["home_id"].astype(str).str.len() > 0]
    return df.sort_values("date").reset_index(drop=True)
