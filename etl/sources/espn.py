"""Adaptador da API pública (não oficial) da ESPN. Fonte principal, não exige chave."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from ..config import TTL_LIVE, Competition
from ..http_cache import CachedClient, RateLimit
from ..jsonutil import JsonDict, as_dict, as_float, as_int, as_list, as_str, dig
from .common import GOAL_COLUMNS, PLAYER_COLUMNS, ROSTER_COLUMNS, STAT_COLUMNS, fixtures_frame, stage_label

BASE_URL = "https://site.web.api.espn.com/apis"
CORE_URL = "https://sports.core.api.espn.com"
SOURCE = "espn"

_STAT_MAP = {
    "possessionPct": "possession",
    "totalShots": "shots",
    "shotsOnTarget": "shots_on_target",
    "wonCorners": "corners",
    "foulsCommitted": "fouls",
    "yellowCards": "yellow",
    "redCards": "red",
}
_ROSTER_STAT_MAP = {
    "apps": "appearances",
    "sub_ins": "subIns",
    "goals": "totalGoals",
    "assists": "goalAssists",
    "shots": "totalShots",
    "shots_on_target": "shotsOnTarget",
    "yellow": "yellowCards",
    "red": "redCards",
    "saves": "saves",
    "goals_conceded": "goalsConceded",
}
_PLAYER_STAT_MAP = {
    "shots": "totalShots",
    "shots_on_target": "shotsOnTarget",
    "yellow": "yellowCards",
    "red": "redCards",
    "saves": "saves",
    "goals_conceded": "goalsConceded",
}
# Texto típico: "Assisted by Rodrigo Garro with a cross following a set piece situation."
_ASSIST_RE = re.compile(r"Assisted by ([^.]+?)(?:\s+(?:with|following|after)\b|\.|$)")
_CLOCK_RE = re.compile(r"(\d+)'(?:\s*\+\s*(\d+)')?")


@dataclass
class MatchDetail:
    goals: list[dict[str, object]] = field(default_factory=list)
    stats: list[dict[str, object]] = field(default_factory=list)
    players: list[dict[str, object]] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {"goals": self.goals, "stats": self.stats, "players": self.players}

    @classmethod
    def from_dict(cls, data: object) -> MatchDetail | None:
        payload = as_dict(data)
        if not {"goals", "stats", "players"} <= payload.keys():
            return None
        return cls(
            goals=[as_dict(g) for g in as_list(payload.get("goals"))],
            stats=[as_dict(s) for s in as_list(payload.get("stats"))],
            players=[as_dict(p) for p in as_list(payload.get("players"))],
        )


def _score(competitor: JsonDict) -> float | None:
    raw = competitor.get("score")
    if isinstance(raw, dict):
        return as_float(raw.get("value")) if raw.get("value") is not None else as_float(raw.get("displayValue"))
    return as_float(raw)


def _logo(team: JsonDict) -> str:
    direct = as_str(team.get("logo"))
    if direct:
        return direct
    return as_str(dig(team, "logos", 0, "href"))


def parse_event(event: JsonDict, comp: Competition) -> dict[str, object] | None:
    competition = as_dict(dig(event, "competitions", 0))
    competitors = [as_dict(c) for c in as_list(competition.get("competitors"))]
    home = next((c for c in competitors if c.get("homeAway") == "home"), None)
    away = next((c for c in competitors if c.get("homeAway") == "away"), None)
    if home is None or away is None:
        return None

    status_type = as_dict(dig(competition, "status", "type")) or as_dict(dig(event, "status", "type"))
    state = as_str(status_type.get("state"), "pre")
    completed = bool(status_type.get("completed")) and state == "post"
    date_str = as_str(event.get("date")) or as_str(competition.get("date"))
    season = as_int(dig(event, "season", "year")) or (int(date_str[:4]) if date_str[:4].isdigit() else None)
    home_team, away_team = as_dict(home.get("team")), as_dict(away.get("team"))
    season_type_name = as_str(dig(event, "seasonType", "name"))

    return {
        "event_id": as_str(event.get("id")),
        "source": SOURCE,
        "comp_key": comp.key,
        "season": season,
        "date": date_str,
        "state": state,
        "completed": completed,
        "home_id": as_str(home_team.get("id")),
        "home": as_str(home_team.get("displayName")) or as_str(home_team.get("name")),
        "home_logo": _logo(home_team),
        "away_id": as_str(away_team.get("id")),
        "away": as_str(away_team.get("displayName")) or as_str(away_team.get("name")),
        "away_logo": _logo(away_team),
        "hg": _score(home) if completed else None,
        "ag": _score(away) if completed else None,
        "home_pen": as_float(home.get("shootoutScore")),
        "away_pen": as_float(away.get("shootoutScore")),
        "home_winner": home.get("winner") if isinstance(home.get("winner"), bool) else None,
        "away_winner": away.get("winner") if isinstance(away.get("winner"), bool) else None,
        "neutral": bool(competition.get("neutralSite")),
        "venue_name": as_str(dig(competition, "venue", "fullName")),
        "attendance": as_float(competition.get("attendance")),
        "stage": stage_label(season_type_name) if comp.kind != "league" else "",
        "note": as_str(dig(competition, "notes", 0, "headline")),
    }


class EspnSource:
    def __init__(self) -> None:
        self.client = CachedClient(SOURCE, BASE_URL, rate=RateLimit(min_interval_s=0.3))
        self.core = CachedClient(f"{SOURCE}_core", CORE_URL, rate=RateLimit(min_interval_s=0.3))

    def birth_date(self, athlete_id: str) -> str:
        """'AAAA-MM-DD' ou '' (usado só para confirmar a identidade do jogador em outra fonte)."""
        data = self.core.get_json(f"/v2/sports/soccer/athletes/{athlete_id}", ttl_s=None)
        return as_str(as_dict(data).get("dateOfBirth"))[:10]

    @staticmethod
    def _ttl_for(season: int) -> float | None:
        return None if season < date.today().year else TTL_LIVE

    def _events(self, path: str, params: dict[str, str | int], ttl: float | None) -> list[JsonDict]:
        data = self.client.get_json(path, params, ttl_s=ttl)
        return [as_dict(e) for e in as_list(as_dict(data).get("events"))]

    def team_matches(self, comp: Competition, team_id: str, season: int, include_fixtures: bool) -> pd.DataFrame:
        path = f"/site/v2/sports/soccer/{comp.espn_slug}/teams/{team_id}/schedule"
        events = self._events(path, {"season": season}, self._ttl_for(season))
        if include_fixtures:
            events += self._events(path, {"fixture": "true"}, TTL_LIVE)
        rows = [row for e in events if (row := parse_event(e, comp)) is not None]
        df = fixtures_frame(rows)
        return df[df["season"] == season].reset_index(drop=True)

    def standings(self, comp: Competition, season: int) -> pd.DataFrame:
        return self.standings_by_slug(comp.espn_slug, season, self._ttl_for(season))

    def standings_by_slug(self, slug: str, season: int, ttl_s: float | None) -> pd.DataFrame:
        data = self.client.get_json(f"/v2/sports/soccer/{slug}/standings", {"season": season}, ttl_s=ttl_s)
        groups = as_list(as_dict(data).get("children")) or [data]
        rows: list[dict[str, object]] = []
        for group in groups:
            for entry in as_list(dig(group, "standings", "entries")):
                entry_d = as_dict(entry)
                team = as_dict(entry_d.get("team"))
                stats = {
                    as_str(as_dict(s).get("name")): as_float(as_dict(s).get("value"))
                    for s in as_list(entry_d.get("stats"))
                }
                rows.append(
                    {
                        "team_id": as_str(team.get("id")),
                        "team": as_str(team.get("displayName")),
                        "short": as_str(team.get("abbreviation")),
                        "logo": _logo(team),
                        "rank": stats.get("rank"),
                        "gp": stats.get("gamesPlayed"),
                        "w": stats.get("wins"),
                        "d": stats.get("ties"),
                        "l": stats.get("losses"),
                        "gf": stats.get("pointsFor"),
                        "ga": stats.get("pointsAgainst"),
                        "gd": stats.get("pointDifferential"),
                        "pts": stats.get("points"),
                    }
                )
        df = pd.DataFrame(rows)
        if df.empty:
            return df
        df = df.sort_values(["rank", "pts"], ascending=[True, False], na_position="last").reset_index(drop=True)
        df["rank"] = range(1, len(df) + 1)
        return df

    def roster(self, slug: str, team_id: str, season: int, ttl_s: float | None) -> pd.DataFrame:
        """Elenco com idade e estatísticas da temporada (a ESPN não informa minutos nem valor de mercado)."""
        data = self.client.get_json(
            f"/site/v2/sports/soccer/{slug}/teams/{team_id}/roster", {"season": season}, ttl_s=ttl_s
        )
        team = as_dict(as_dict(data).get("team"))
        rows: list[dict[str, object]] = []
        for raw in as_list(as_dict(data).get("athletes")):
            athlete = as_dict(raw)
            stats = {
                as_str(as_dict(s).get("name")): as_float(as_dict(s).get("value"))
                for category in as_list(dig(athlete, "statistics", "splits", "categories"))
                for s in as_list(as_dict(category).get("stats"))
            }
            rows.append(
                {
                    "player_id": as_str(athlete.get("id")),
                    "player": as_str(athlete.get("displayName")),
                    "position": as_str(dig(athlete, "position", "abbreviation")),
                    "age": as_float(athlete.get("age")),
                    "citizenship": as_str(athlete.get("citizenship")),
                    "jersey": as_str(athlete.get("jersey")),
                    "injured": bool(as_list(athlete.get("injuries"))),
                    "team_id": team_id,
                    "team": as_str(team.get("displayName")),
                    "team_logo": _logo(team),
                    **{col: stats.get(name) or 0.0 for col, name in _ROSTER_STAT_MAP.items()},
                }
            )
        return pd.DataFrame(rows, columns=ROSTER_COLUMNS)

    def match_detail(
        self, comp: Competition, event_id: str, ttl_s: float | None = None, roster_team_id: str | None = None
    ) -> MatchDetail:
        """roster_team_id limita a súmula de jogadores a um time (o cache persistido fica ~2x menor)."""
        data = self.client.get_json(
            f"/site/v2/sports/soccer/{comp.espn_slug}/summary", {"event": event_id}, ttl_s=ttl_s
        )
        detail = MatchDetail()
        for raw in as_list(as_dict(data).get("keyEvents")):
            ev = as_dict(raw)
            if not ev.get("scoringPlay"):
                continue
            kind_text = as_str(dig(ev, "type", "text")).lower()
            if "shootout" in kind_text:
                continue
            clock = as_str(dig(ev, "clock", "displayValue"))
            match = _CLOCK_RE.search(clock)
            minute = int(match.group(1)) if match else None
            extra = int(match.group(2)) if match and match.group(2) else 0
            text = as_str(ev.get("text"))
            assist = _ASSIST_RE.search(text)
            kind = "own_goal" if "own goal" in kind_text else "penalty" if "penalty" in kind_text else "goal"
            # participants = [autor, garçom]; o texto é só reserva para o nome do garçom
            assister = as_dict(dig(ev, "participants", 1, "athlete"))
            detail.goals.append(
                {
                    "event_id": event_id,
                    "team_id": as_str(dig(ev, "team", "id")),
                    "minute": minute,
                    "extra": extra,
                    "period": as_int(dig(ev, "period", "number")),
                    "player": as_str(dig(ev, "participants", 0, "athlete", "displayName")),
                    "player_id": as_str(dig(ev, "participants", 0, "athlete", "id")),
                    "assist": as_str(assister.get("displayName")) or (assist.group(1).strip() if assist else ""),
                    "assist_id": as_str(assister.get("id")),
                    "kind": kind,
                }
            )

        for raw_team in as_list(as_dict(data).get("rosters")):
            team_d = as_dict(raw_team)
            team_id = as_str(dig(team_d, "team", "id"))
            if roster_team_id is not None and team_id != roster_team_id:
                continue
            for raw_player in as_list(team_d.get("roster")):
                entry = as_dict(raw_player)
                athlete = as_dict(entry.get("athlete"))
                stats = {
                    as_str(as_dict(s).get("name")): as_float(as_dict(s).get("value"))
                    for s in as_list(entry.get("stats"))
                }
                detail.players.append(
                    {
                        "event_id": event_id,
                        "team_id": team_id,
                        "player_id": as_str(athlete.get("id")),
                        "player": as_str(athlete.get("displayName")),
                        "full_name": as_str(athlete.get("fullName")),
                        "position": as_str(dig(entry, "position", "abbreviation")),
                        "jersey": as_str(entry.get("jersey")),
                        "starter": entry.get("starter") is True,
                        "subbed_in": entry.get("subbedIn") is True,
                        **{col: stats.get(name) or 0.0 for col, name in _PLAYER_STAT_MAP.items()},
                    }
                )

        for raw_team in as_list(dig(data, "boxscore", "teams")):
            team_d = as_dict(raw_team)
            row: dict[str, object] = {"event_id": event_id, "team_id": as_str(dig(team_d, "team", "id"))}
            for stat in as_list(team_d.get("statistics")):
                stat_d = as_dict(stat)
                key = _STAT_MAP.get(as_str(stat_d.get("name")))
                if key:
                    row[key] = as_float(as_str(stat_d.get("displayValue")).replace("%", "").strip())
            detail.stats.append(row)
        return detail


def goals_frame(details: list[MatchDetail]) -> pd.DataFrame:
    rows = [g for d in details for g in d.goals]
    return pd.DataFrame(rows, columns=GOAL_COLUMNS)


def stats_frame(details: list[MatchDetail]) -> pd.DataFrame:
    rows = [s for d in details for s in d.stats]
    return pd.DataFrame(rows, columns=STAT_COLUMNS)


def players_frame(details: list[MatchDetail]) -> pd.DataFrame:
    rows = [p for d in details for p in d.players]
    return pd.DataFrame(rows, columns=PLAYER_COLUMNS)
