"""Schema comum que todos os adaptadores devolvem."""
from __future__ import annotations

import re

import pandas as pd

FIXTURE_COLUMNS = [
    "event_id",
    "source",
    "comp_key",
    "season",
    "date",
    "state",  # "pre" | "in" | "post"
    "completed",
    "home_id",
    "home",
    "home_logo",
    "away_id",
    "away",
    "away_logo",
    "hg",
    "ag",
    "home_pen",
    "away_pen",
    "home_winner",
    "away_winner",
    "neutral",
    "venue_name",
    "attendance",
    "stage",
    "note",
]

GOAL_COLUMNS = [
    "event_id", "team_id", "minute", "extra", "period", "player", "player_id", "assist", "assist_id", "kind",
]
STAT_COLUMNS = ["event_id", "team_id", "possession", "shots", "shots_on_target", "corners", "fouls", "yellow", "red"]
# Elenco de um time numa liga/temporada (endpoint de roster da ESPN)
ROSTER_COLUMNS = [
    "player_id", "player", "position", "age", "citizenship", "jersey", "injured", "team_id", "team", "team_logo",
    "apps", "sub_ins", "goals", "assists", "shots", "shots_on_target", "yellow", "red", "saves", "goals_conceded",
]
# Uma linha por jogador relacionado na súmula (inclui reservas não utilizados: starter=subbed_in=False)
PLAYER_COLUMNS = [
    "event_id", "team_id", "player_id", "player", "full_name", "position", "jersey", "starter", "subbed_in",
    "shots", "shots_on_target", "yellow", "red", "saves", "goals_conceded",
]


def empty_fixtures() -> pd.DataFrame:
    return pd.DataFrame(columns=FIXTURE_COLUMNS)


def fixtures_frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    if not rows:
        return empty_fixtures()
    df = pd.DataFrame(rows, columns=FIXTURE_COLUMNS)
    df["date"] = pd.to_datetime(df["date"], utc=True, errors="coerce")
    df["season"] = pd.to_numeric(df["season"], errors="coerce")
    for col in ("hg", "ag", "home_pen", "away_pen", "attendance"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["date", "season"]).drop_duplicates(subset=["source", "event_id"])
    df["season"] = df["season"].astype(int)
    return df


_STAGES_PT: tuple[tuple[str, str], ...] = (
    (r"round of 32", "16 avos"),
    (r"round of 16", "Oitavas"),
    (r"quarter", "Quartas"),
    (r"semi", "Semifinal"),
    (r"third place", "3º lugar"),
    (r"knockout round play", "Playoffs"),
    (r"playoff", "Playoffs"),
    (r"group", "Fase de grupos"),
    (r"league stage", "Fase de liga"),
    (r"first round|1st round", "1ª fase"),
    (r"second round|2nd round", "2ª fase"),
    (r"third round|3rd round", "3ª fase"),
    (r"fourth round|4th round", "4ª fase"),
    (r"fifth round|5th round", "5ª fase"),
    (r"qualif|preliminary", "Qualificatória"),
    (r"\bfinal", "Final"),
)


def stage_label(raw: str) -> str:
    """Traduz o nome da fase ('2026 Copa do Brasil, Round of 16') para PT-BR."""
    text = raw.lower()
    for pattern, label in _STAGES_PT:
        if re.search(pattern, text):
            return label
    return ""
