"""Adaptador da API-Football (plano free: 100 req/dia, temporadas limitadas).

Uso: fallback do calendário do Corinthians e enriquecimento do elenco (minutos, jogos, assistências).
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from ..config import API_FOOTBALL_KEY, API_FOOTBALL_TEAM_ID, COMPETITION_BY_API_FOOTBALL, TTL_DAILY
from ..http_cache import CachedClient, RateLimit, SourceUnavailable
from ..jsonutil import as_dict, as_float, as_int, as_list, as_str, dig
from .common import fixtures_frame, stage_label

BASE_URL = "https://v3.football.api-sports.io"
SOURCE = "api_football"
_FINISHED = {"FT", "AET", "PEN", "AWD", "WO"}
_LIVE = {"1H", "HT", "2H", "ET", "BT", "P", "LIVE", "INT"}
_MAX_PLAYER_PAGES = 3


class ApiFootballSource:
    def __init__(self) -> None:
        self.enabled = bool(API_FOOTBALL_KEY)
        self.client = CachedClient(
            SOURCE,
            BASE_URL,
            headers={"x-apisports-key": API_FOOTBALL_KEY} if self.enabled else None,
            rate=RateLimit(min_interval_s=6.5, daily_budget=95),
        )

    def _require(self) -> None:
        if not self.enabled:
            raise SourceUnavailable("API-Football: API_FOOTBALL_KEY não configurada")

    @staticmethod
    def _ttl_for(season: int) -> float | None:
        return None if season < date.today().year else TTL_DAILY

    def _get(self, path: str, params: dict[str, str | int], season: int) -> dict[str, object]:
        self._require()
        data = as_dict(self.client.get_json(path, params, ttl_s=self._ttl_for(season)))
        errors = data.get("errors")
        if (isinstance(errors, dict) and errors) or (isinstance(errors, list) and errors):
            raise SourceUnavailable(f"API-Football: {errors}")
        return data

    def team_matches(self, season: int) -> pd.DataFrame:
        data = self._get("/fixtures", {"team": API_FOOTBALL_TEAM_ID, "season": season}, season)
        rows: list[dict[str, object]] = []
        for raw in as_list(data.get("response")):
            item = as_dict(raw)
            comp = COMPETITION_BY_API_FOOTBALL.get(as_int(dig(item, "league", "id")) or -1)
            if comp is None:
                continue
            status = as_str(dig(item, "fixture", "status", "short"))
            completed = status in _FINISHED
            home, away = as_dict(dig(item, "teams", "home")), as_dict(dig(item, "teams", "away"))
            rows.append(
                {
                    "event_id": as_str(dig(item, "fixture", "id")),
                    "source": SOURCE,
                    "comp_key": comp.key,
                    "season": season,
                    "date": as_str(dig(item, "fixture", "date")),
                    "state": "post" if completed else "in" if status in _LIVE else "pre",
                    "completed": completed,
                    "home_id": f"af:{as_str(home.get('id'))}",
                    "home": as_str(home.get("name")),
                    "home_logo": as_str(home.get("logo")),
                    "away_id": f"af:{as_str(away.get('id'))}",
                    "away": as_str(away.get("name")),
                    "away_logo": as_str(away.get("logo")),
                    "hg": as_float(dig(item, "goals", "home")) if completed else None,
                    "ag": as_float(dig(item, "goals", "away")) if completed else None,
                    "home_pen": as_float(dig(item, "score", "penalty", "home")),
                    "away_pen": as_float(dig(item, "score", "penalty", "away")),
                    "home_winner": home.get("winner") if isinstance(home.get("winner"), bool) else None,
                    "away_winner": away.get("winner") if isinstance(away.get("winner"), bool) else None,
                    "neutral": False,
                    "venue_name": as_str(dig(item, "fixture", "venue", "name")),
                    "attendance": None,
                    "stage": stage_label(as_str(dig(item, "league", "round"))) if comp.kind != "league" else "",
                    "note": "",
                }
            )
        return fixtures_frame(rows)

    def squad_stats(self, season: int) -> pd.DataFrame:
        """Minutos, jogos, gols e assistências por jogador (somando todas as competições)."""
        rows: list[dict[str, object]] = []
        page, total_pages = 1, 1
        while page <= min(total_pages, _MAX_PLAYER_PAGES):
            data = self._get("/players", {"team": API_FOOTBALL_TEAM_ID, "season": season, "page": page}, season)
            total_pages = as_int(dig(data, "paging", "total")) or 1
            for raw in as_list(data.get("response")):
                item = as_dict(raw)
                player = as_dict(item.get("player"))
                for stat in as_list(item.get("statistics")):
                    stat_d = as_dict(stat)
                    if as_int(dig(stat_d, "team", "id")) != API_FOOTBALL_TEAM_ID:
                        continue
                    rows.append(
                        {
                            "player": as_str(player.get("name")),
                            "photo": as_str(player.get("photo")),
                            "position": as_str(dig(stat_d, "games", "position")),
                            "apps": as_float(dig(stat_d, "games", "appearences")) or 0.0,
                            "minutes": as_float(dig(stat_d, "games", "minutes")) or 0.0,
                            "goals": as_float(dig(stat_d, "goals", "total")) or 0.0,
                            "assists": as_float(dig(stat_d, "goals", "assists")) or 0.0,
                            "rating": as_float(dig(stat_d, "games", "rating")),
                        }
                    )
            page += 1
        df = pd.DataFrame(rows)
        if df.empty:
            return df
        agg = df.groupby(["player", "photo"], as_index=False).agg(
            position=("position", "first"),
            apps=("apps", "sum"),
            minutes=("minutes", "sum"),
            goals=("goals", "sum"),
            assists=("assists", "sum"),
            rating=("rating", "mean"),
        )
        return agg.sort_values(["goals", "assists", "minutes"], ascending=False).reset_index(drop=True)
