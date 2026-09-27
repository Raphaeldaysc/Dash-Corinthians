"""Adaptador do football-data.org (plano free: só Brasileirão). Fallback de tabela e calendário da liga."""
from __future__ import annotations

from datetime import date

import pandas as pd

from ..config import FOOTBALL_DATA_KEY, TTL_LIVE
from ..http_cache import CachedClient, RateLimit, SourceUnavailable
from ..jsonutil import as_dict, as_float, as_list, as_str, dig
from .common import fixtures_frame

BASE_URL = "https://api.football-data.org/v4"
SOURCE = "football_data"
COMPETITION_CODE = "BSA"


class FootballDataSource:
    def __init__(self) -> None:
        self.enabled = bool(FOOTBALL_DATA_KEY)
        self.client = CachedClient(
            SOURCE,
            BASE_URL,
            headers={"X-Auth-Token": FOOTBALL_DATA_KEY} if self.enabled else None,
            rate=RateLimit(min_interval_s=6.5),
        )

    def _require(self) -> None:
        if not self.enabled:
            raise SourceUnavailable("football-data.org: FOOTBALL_DATA_KEY não configurada")

    @staticmethod
    def _ttl_for(season: int) -> float | None:
        return None if season < date.today().year else TTL_LIVE

    def standings(self, season: int) -> pd.DataFrame:
        self._require()
        data = self.client.get_json(
            f"/competitions/{COMPETITION_CODE}/standings", {"season": season}, ttl_s=self._ttl_for(season)
        )
        tables = [as_dict(t) for t in as_list(as_dict(data).get("standings"))]
        total = next((t for t in tables if t.get("type") == "TOTAL"), tables[0] if tables else {})
        rows = []
        for raw in as_list(total.get("table")):
            row = as_dict(raw)
            team = as_dict(row.get("team"))
            rows.append(
                {
                    "team_id": f"fd:{as_str(team.get('id'))}",
                    "team": as_str(team.get("shortName")) or as_str(team.get("name")),
                    "short": as_str(team.get("tla")),
                    "logo": as_str(team.get("crest")),
                    "rank": as_float(row.get("position")),
                    "gp": as_float(row.get("playedGames")),
                    "w": as_float(row.get("won")),
                    "d": as_float(row.get("draw")),
                    "l": as_float(row.get("lost")),
                    "gf": as_float(row.get("goalsFor")),
                    "ga": as_float(row.get("goalsAgainst")),
                    "gd": as_float(row.get("goalDifference")),
                    "pts": as_float(row.get("points")),
                }
            )
        return pd.DataFrame(rows)

    def league_matches(self, season: int) -> pd.DataFrame:
        self._require()
        data = self.client.get_json(
            f"/competitions/{COMPETITION_CODE}/matches", {"season": season}, ttl_s=self._ttl_for(season)
        )
        rows: list[dict[str, object]] = []
        for raw in as_list(as_dict(data).get("matches")):
            match = as_dict(raw)
            status = as_str(match.get("status"))
            completed = status == "FINISHED"
            home, away = as_dict(match.get("homeTeam")), as_dict(match.get("awayTeam"))
            rows.append(
                {
                    "event_id": as_str(match.get("id")),
                    "source": SOURCE,
                    "comp_key": "brasileirao",
                    "season": season,
                    "date": as_str(match.get("utcDate")),
                    "state": "post" if completed else "in" if status in {"IN_PLAY", "PAUSED"} else "pre",
                    "completed": completed,
                    "home_id": f"fd:{as_str(home.get('id'))}",
                    "home": as_str(home.get("shortName")) or as_str(home.get("name")),
                    "home_logo": as_str(home.get("crest")),
                    "away_id": f"fd:{as_str(away.get('id'))}",
                    "away": as_str(away.get("shortName")) or as_str(away.get("name")),
                    "away_logo": as_str(away.get("crest")),
                    "hg": as_float(dig(match, "score", "fullTime", "home")) if completed else None,
                    "ag": as_float(dig(match, "score", "fullTime", "away")) if completed else None,
                    "home_pen": None,
                    "away_pen": None,
                    "home_winner": None,
                    "away_winner": None,
                    "neutral": False,
                    "venue_name": as_str(match.get("venue")),
                    "attendance": None,
                    "stage": "",
                    "note": "",
                }
            )
        return fixtures_frame(rows)
