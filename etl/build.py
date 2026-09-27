"""Pipeline completo: busca -> normaliza -> métricas -> simulação -> web/data/dashboard.json.

Uso (na raiz do projeto):  python -m etl.build
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone

import pandas as pd

from . import metrics as m
from . import storage
from .coaches import coaches_block
from .config import (
    COMPETITION_BY_KEY,
    COMPETITIONS,
    CURRENT_SEASON,
    ESPN_TEAM_ID,
    LEAGUE_KEY,
    OUTPUT_JSON,
    SEASONS,
    TARGET_RATE,
    TEAM_NAME,
    TTL_LIVE,
)
from .http_cache import SourceUnavailable
from .finance import finance_block
from .jsonutil import normalize_name
from .normalize import TEAM_KEY, league_frame, missing_rows, team_matches
from .manual import load_bans, load_coaches, load_finance, load_market_values
from .photos import PlayerRef, resolve_photos
from .refresh_state import mark_refreshed
from .simulate import simulate_league
from .scouting import scouting_block
from .squad_analysis import squad_diagnosis
from .sources.api_football import ApiFootballSource
from .sources.espn import EspnSource, MatchDetail, goals_frame, players_frame, stats_frame
from .sources.football_data import FootballDataSource
from .sources.thesportsdb import TheSportsDbSource

log = logging.getLogger("etl")

DETAILS_KEY = "match_details"
# Subir a versão invalida o cache inteiro (ex.: mudança no parser de gols/estatísticas)
DETAILS_CACHE_VERSION = 2
# Logo após o apito a ESPN pode ainda não ter todos os gols/autores; só congela depois disso
DETAILS_FINAL_AFTER = pd.Timedelta(hours=12)

SQUAD_LIMIT = 30
SQUAD_SUM_COLUMNS = ("goals", "assists", "shots", "shots_on_target", "yellow", "red", "saves", "goals_conceded")


def _concat(frames: list[pd.DataFrame]) -> pd.DataFrame:
    frames = [f for f in frames if not f.empty]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def fetch_team_matches(espn: EspnSource, af: ApiFootballSource, notes: list[str]) -> pd.DataFrame:
    frames = []
    for season in SEASONS:
        for comp in COMPETITIONS:
            try:
                frames.append(espn.team_matches(comp, ESPN_TEAM_ID, season, include_fixtures=season == CURRENT_SEASON))
            except SourceUnavailable as exc:
                notes.append(f"ESPN {comp.short} {season}: {exc}")
    matches = team_matches(_concat(frames))
    log.info("ESPN: %d jogos do %s", len(matches), TEAM_NAME)

    if not af.enabled:
        return matches
    extras = []
    for season in SEASONS:
        try:
            fallback = team_matches(af.team_matches(season))
        except SourceUnavailable as exc:
            notes.append(f"API-Football {season}: {exc}")
            continue
        missing = missing_rows(matches, fallback)
        log.info("API-Football %d: +%d jogos que faltavam na ESPN", season, len(missing))
        extras.append(missing)
    combined = _concat([matches, *extras])
    return combined.sort_values("date").reset_index(drop=True) if not combined.empty else matches


def load_details_cache() -> dict[str, object]:
    raw = storage.read(DETAILS_KEY)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict) or data.get("version") != DETAILS_CACHE_VERSION:
        return {}
    cached = data.get("matches")
    return dict(cached) if isinstance(cached, dict) else {}


def fetch_details(
    espn: EspnSource, matches: pd.DataFrame, notes: list[str]
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    details: list[MatchDetail] = []
    targets = matches[matches["completed"] & (matches["source"] == "espn")]
    cache = load_details_cache()
    final_before = pd.Timestamp.now(tz="UTC") - DETAILS_FINAL_AFTER
    hits = fetched = persisted = failures = 0

    for _, row in targets.iterrows():
        event_id = str(row["event_id"])
        cached = MatchDetail.from_dict(cache.get(event_id))
        if cached is not None:
            details.append(cached)
            hits += 1
            continue
        final = row["date"] < final_before
        try:
            detail = espn.match_detail(
                COMPETITION_BY_KEY[row["comp_key"]],
                event_id,
                ttl_s=None if final else TTL_LIVE,
                roster_team_id=ESPN_TEAM_ID,
            )
        except SourceUnavailable:
            failures += 1
            continue
        details.append(detail)
        fetched += 1
        if final:
            cache[event_id] = detail.to_dict()
            persisted += 1

    log.info("Resumos de partida: %d do cache, %d buscados, %d novos no cache", hits, fetched, persisted)
    if persisted:
        try:
            storage.write(DETAILS_KEY, json.dumps({"version": DETAILS_CACHE_VERSION, "matches": cache}, ensure_ascii=False))
        except storage.StorageError as exc:
            notes.append(f"Cache de resumos não foi salvo: {exc}")
    if failures:
        notes.append(f"ESPN: {failures} resumos de partida indisponíveis")
    return goals_frame(details), stats_frame(details), players_frame(details)


def fetch_standings(espn: EspnSource, fd: FootballDataSource, season: int, notes: list[str]) -> pd.DataFrame:
    comp = COMPETITION_BY_KEY[LEAGUE_KEY]
    try:
        table = espn.standings(comp, season)
        if not table.empty:
            return table
    except SourceUnavailable as exc:
        notes.append(f"ESPN tabela {season}: {exc}")
    if fd.enabled:
        try:
            return fd.standings(season)
        except SourceUnavailable as exc:
            notes.append(f"football-data tabela {season}: {exc}")
    return pd.DataFrame()


def team_id_in_table(standings: pd.DataFrame) -> str | None:
    if standings.empty:
        return None
    ids = standings["team_id"].astype(str)
    if ESPN_TEAM_ID in set(ids):
        return ESPN_TEAM_ID
    by_name = standings[standings["team"].map(lambda n: normalize_name(str(n)) == TEAM_KEY)]
    return str(by_name["team_id"].iloc[0]) if not by_name.empty else None


def fetch_league(espn: EspnSource, fd: FootballDataSource, standings: pd.DataFrame, notes: list[str]) -> pd.DataFrame:
    comp = COMPETITION_BY_KEY[LEAGUE_KEY]
    if not standings.empty and not str(standings["team_id"].iloc[0]).startswith("fd:"):
        frames = []
        for tid in standings["team_id"].astype(str):
            try:
                frames.append(espn.team_matches(comp, tid, CURRENT_SEASON, include_fixtures=True))
            except SourceUnavailable as exc:
                notes.append(f"ESPN calendário do time {tid}: {exc}")
        league = league_frame(_concat(frames))
        if not league.empty:
            return league
    if fd.enabled:
        try:
            return league_frame(fd.league_matches(CURRENT_SEASON))
        except SourceUnavailable as exc:
            notes.append(f"football-data calendário: {exc}")
    return pd.DataFrame()


def fetch_squads(af: ApiFootballSource, seasons: list[int], notes: list[str]) -> dict[str, object]:
    squads: dict[str, object] = {}
    if not af.enabled:
        return squads
    for season in seasons:
        try:
            df = af.squad_stats(season)
        except SourceUnavailable as exc:
            notes.append(f"API-Football elenco {season}: {exc}")
            continue
        if df.empty:
            continue
        df = df.assign(
            goals_per90=(df["goals"] * 90 / df["minutes"].where(df["minutes"] > 0)),
            ga_per90=((df["goals"] + df["assists"]) * 90 / df["minutes"].where(df["minutes"] > 0)),
        )
        squads[f"{season}|all"] = df.head(SQUAD_LIMIT).to_dict("records")
    return squads


def broad_position(code: str) -> str:
    """Posição tática da súmula ('CD-L', 'AM', 'SUB') -> G/D/M/F."""
    base = code.upper().split("-")[0]
    if base == "G":
        return "G"
    if base in {"D", "CD", "CB", "LB", "RB", "LWB", "RWB", "SW"}:
        return "D"
    if base in {"F", "CF", "ST", "LF", "RF", "LW", "RW", "SS"}:
        return "F"
    return "M" if base.endswith("M") else ""


def goal_contributions(goals: pd.DataFrame, players: pd.DataFrame) -> pd.DataFrame:
    """Gols e assistências do Corinthians por jogo e jogador (id da ESPN; nome só como reserva)."""
    columns = ["event_id", "player_id", "player", "goals", "assists"]
    ours = goals[(goals["team_id"] == ESPN_TEAM_ID) & (goals["kind"] != "own_goal")].fillna(
        {"player_id": "", "assist_id": "", "player": "", "assist": ""}
    )
    if ours.empty:
        return pd.DataFrame(columns=columns)
    by_name = (
        players.assign(name_key=players["player"].map(normalize_name))
        .drop_duplicates(["event_id", "name_key"])
        .set_index(["event_id", "name_key"])["player_id"]
    )

    def resolve(id_col: str, name_col: str) -> pd.Series:
        keys = pd.MultiIndex.from_arrays([ours["event_id"], ours[name_col].map(normalize_name)])
        fallback = pd.Series(by_name.reindex(keys).to_numpy(), index=ours.index).fillna("")
        return ours[id_col].where(ours[id_col] != "", fallback)

    scored = pd.DataFrame(
        {"event_id": ours["event_id"], "player_id": resolve("player_id", "player"), "player": ours["player"], "goals": 1, "assists": 0}
    )
    assisted = pd.DataFrame(
        {"event_id": ours["event_id"], "player_id": resolve("assist_id", "assist"), "player": ours["assist"], "goals": 0, "assists": 1}
    )
    rows = pd.concat([scored, assisted], ignore_index=True)
    rows = rows[(rows["player_id"] != "") & (rows["player"] != "")]
    return rows.groupby(["event_id", "player_id"], as_index=False).agg(
        player=("player", "first"), goals=("goals", "sum"), assists=("assists", "sum")
    )


def build_squads(matches: pd.DataFrame, goals: pd.DataFrame, players: pd.DataFrame) -> dict[str, list[dict[str, object]]]:
    """Elenco por temporada e competição ('all' = soma de todas), a partir das súmulas das partidas.

    Jogos, titularidade, finalizações e cartões vêm das escalações; gols e assistências, dos lances
    de gol (os mesmos da aba Gols). Tudo casado pelo id do atleta na ESPN.
    """
    squads: dict[str, list[dict[str, object]]] = {}
    if players.empty:
        return squads
    appeared = players[players["starter"] | players["subbed_in"]]
    per_match = appeared.merge(
        goal_contributions(goals, players), on=["event_id", "player_id"], how="outer", suffixes=("", "_goal")
    )
    per_match["player"] = per_match["player"].fillna(per_match["player_goal"])
    per_match = per_match.fillna(
        {"full_name": "", "position": "", "jersey": "", "starter": False, "subbed_in": False, "goals": 0, "assists": 0}
    ).fillna({col: 0.0 for col in ("shots", "shots_on_target", "yellow", "red", "saves", "goals_conceded")})
    per_match = per_match.assign(
        starter=per_match["starter"].astype(bool),
        subbed_in=per_match["subbed_in"].astype(bool),
        broad=per_match["position"].map(broad_position),
    )
    per_match = per_match.assign(appeared=per_match["starter"] | per_match["subbed_in"]).merge(
        matches[["event_id", "season", "comp_key", "date"]], on="event_id"
    )

    known = per_match[per_match["broad"] != ""]
    positions = known.groupby("player_id")["broad"].agg(lambda s: s.mode().iloc[0])

    for season, season_df in per_match.groupby("season"):
        for scope in ["all", *season_df["comp_key"].unique()]:
            subset = season_df if scope == "all" else season_df[season_df["comp_key"] == scope]
            agg = subset.sort_values("date").groupby("player_id", as_index=False).agg(
                player=("player", "last"),
                full_name=("full_name", "last"),
                jersey=("jersey", "last"),
                apps=("appeared", "sum"),
                starts=("starter", "sum"),
                sub_ins=("subbed_in", "sum"),
                **{col: (col, "sum") for col in SQUAD_SUM_COLUMNS},
            )
            agg["position"] = agg["player_id"].map(positions).fillna("")
            agg["photo"] = ""
            agg["ga"] = agg["goals"] + agg["assists"]
            agg["ga_per_game"] = agg["ga"] / agg["apps"].where(agg["apps"] > 0)
            agg = agg.sort_values(["ga", "goals", "apps"], ascending=False)
            squads[f"{int(season)}|{scope}"] = agg.head(SQUAD_LIMIT).to_dict("records")
    return squads


def attach_photos(
    squads: dict[str, list[dict[str, object]]], espn: EspnSource, tsdb: TheSportsDbSource, notes: list[str]
) -> None:
    refs: dict[str, PlayerRef] = {}
    for rows in squads.values():
        for row in rows:
            pid = str(row["player_id"])
            refs.setdefault(pid, PlayerRef(pid, str(row["player"]), str(row["full_name"])))
    photos = resolve_photos(espn, tsdb, list(refs.values()), notes)
    for rows in squads.values():
        for row in rows:
            row["photo"] = photos.get(str(row["player_id"]), "")


def build_views(matches: pd.DataFrame, goals: pd.DataFrame, stats: pd.DataFrame, standings_by_season: dict[int, pd.DataFrame]) -> dict[str, object]:
    views: dict[str, object] = {}
    home_away: dict[str, object] = {}
    cards: dict[str, object] = {}
    comps_by_season: dict[str, list[str]] = {}

    for season, season_df in matches.groupby("season"):
        season_i = int(season)
        present = [c.key for c in COMPETITIONS if (season_df["comp_key"] == c.key).any()]
        comps_by_season[str(season_i)] = present
        cards[str(season_i)] = m.competition_cards(season_df, standings_by_season.get(season_i))
        for comp_key in ["all", *present]:
            scope = season_df if comp_key == "all" else season_df[season_df["comp_key"] == comp_key]
            done = m.played(scope)
            home_away[f"{season_i}|{comp_key}"] = m.home_away(done)
            for venue in m.VENUES:
                subset = done if venue == "all" else done[done["venue"] == venue]
                views[f"{season_i}|{comp_key}|{venue}"] = m.build_view(subset, goals, stats, TARGET_RATE)

    return {"views": views, "home_away": home_away, "competitions": cards, "comps_by_season": comps_by_season}


def data_quality(
    matches: pd.DataFrame,
    goals: pd.DataFrame,
    stats: pd.DataFrame,
    players: pd.DataFrame,
    squads: dict[str, object],
    standings: pd.DataFrame,
) -> dict[str, object]:
    """Cobertura observável do artefato; mede presença, não precisão da fonte."""
    done = m.played(matches)
    total = len(done)
    event_ids = done["event_id"].astype(str)
    current_squad = squads.get(f"{CURRENT_SEASON}|all", [])
    squad_rows = current_squad if isinstance(current_squad, list) else []

    def coverage(count: int, denominator: int = total) -> float | None:
        return count / denominator if denominator else None

    stat_events = set(stats["event_id"].astype(str)) if not stats.empty else set()
    player_events = set(players["event_id"].astype(str)) if not players.empty else set()
    detail_events = stat_events | player_events | (set(goals["event_id"].astype(str)) if not goals.empty else set())
    duplicate_events = int(event_ids.duplicated().sum())
    photos = sum(bool(row.get("photo")) for row in squad_rows)
    indicators = [
        {"key": "attendance", "label": "Público", "count": int(done["attendance"].fillna(0).gt(0).sum()), "total": total},
        {"key": "stadium", "label": "Estádio", "count": int(done["stadium"].astype(str).ne("").sum()), "total": total},
        {"key": "details", "label": "Súmula/detalhes", "count": int(event_ids.isin(detail_events).sum()), "total": total},
        {"key": "stats", "label": "Estatísticas", "count": int(event_ids.isin(stat_events).sum()), "total": total},
        {"key": "photos", "label": "Fotos do elenco", "count": photos, "total": len(squad_rows)},
    ]
    for item in indicators:
        item["rate"] = coverage(int(item["count"]), int(item["total"]))
    issues: list[str] = []
    if duplicate_events:
        issues.append(f"{duplicate_events} partida(s) duplicada(s) por event_id.")
    for item in indicators:
        rate = item["rate"]
        if isinstance(rate, float) and rate < 0.8:
            issues.append(f"Cobertura de {str(item['label']).lower()}: {rate:.0%}.")
    if standings.empty:
        issues.append("Tabela do Brasileirão indisponível.")
    return {
        "matches": {"completed": total, "scheduled": int((~matches["completed"]).sum()), "duplicate_events": duplicate_events},
        "indicators": indicators,
        "goals": int(len(goals)),
        "squad_players": len(squad_rows),
        "standings_teams": int(len(standings)),
        "issues": issues,
        "definition": "Cobertura mede presença de campos no artefato, não certifica exatidão da fonte.",
    }


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    started = time.monotonic()
    espn, fd, af = EspnSource(), FootballDataSource(), ApiFootballSource()
    notes: list[str] = []

    matches = fetch_team_matches(espn, af, notes)
    if matches.empty:
        raise SystemExit("Nenhum jogo encontrado em nenhuma fonte. Veja os avisos acima.")
    matches = m.add_rest_days(matches)
    goals, stats, players = fetch_details(espn, matches, notes)

    seasons_found = sorted(int(s) for s in matches["season"].dropna().unique())
    standings_by_season = {s: fetch_standings(espn, fd, s, notes) for s in seasons_found}
    current_table = standings_by_season.get(CURRENT_SEASON, pd.DataFrame())
    table_team_id = team_id_in_table(current_table)

    league = fetch_league(espn, fd, current_table, notes)
    simulation = (
        simulate_league(current_table, league, table_team_id)
        if table_team_id and not league.empty
        else {"available": False, "reason": "Sem tabela/calendário do Brasileirão para simular"}
    )
    log.info("Simulação: %s", "ok" if simulation.get("available") else simulation.get("reason"))

    built = build_views(matches, goals, stats, standings_by_season)
    current_league = m.played(matches[(matches["season"] == CURRENT_SEASON) & (matches["comp_key"] == LEAGUE_KEY)])
    team_ids = {ESPN_TEAM_ID, table_team_id or ""}

    tsdb = TheSportsDbSource()
    espn_squads = build_squads(matches, goals, players)
    attach_photos(espn_squads, espn, tsdb, notes)
    missing_squad_seasons = [s for s in seasons_found if f"{s}|all" not in espn_squads]
    squads: dict[str, object] = {**espn_squads, **fetch_squads(af, missing_squad_seasons, notes)}

    # Análises ampliadas. Dados editoriais/financeiros ficam em data/manual e sempre
    # carregam fonte; dados ausentes viram seção vazia ou aviso, nunca estimativa inventada.
    coaches = coaches_block(matches, load_coaches(notes))
    current_squad = squads.get(f"{CURRENT_SEASON}|all", [])
    try:
        current_roster = espn.roster(COMPETITION_BY_KEY[LEAGUE_KEY].espn_slug, ESPN_TEAM_ID, CURRENT_SEASON, TTL_LIVE)
    except SourceUnavailable as exc:
        notes.append(f"ESPN elenco atual: {exc}")
        current_roster = pd.DataFrame()
    season_matches = matches[matches["season"] == CURRENT_SEASON]
    diagnosis = squad_diagnosis(
        current_squad if isinstance(current_squad, list) else [],
        current_roster,
        season_matches,
        goals,
        current_table,
        CURRENT_SEASON,
    )
    scouting = scouting_block(espn, diagnosis.get("need", {}), load_market_values(notes), notes)
    bans, debts, bans_as_of = load_bans(notes)
    finances = finance_block(matches, standings_by_season, simulation, table_team_id, load_finance(notes), bans, debts, bans_as_of)
    quality = data_quality(matches, goals, stats, players, squads, current_table)

    payload = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "team": TEAM_NAME,
            "team_logo": f"https://a.espncdn.com/i/teamlogos/soccer/500/{ESPN_TEAM_ID}.png",
            "seasons": seasons_found,
            "current_season": CURRENT_SEASON,
            "target_rate": TARGET_RATE,
            "competitions": [
                {"key": c.key, "name": c.name, "short": c.short, "color": c.color, "kind": c.kind} for c in COMPETITIONS
            ],
            "comps_by_season": built["comps_by_season"],
            "sources": [
                {**espn.client.stats.to_dict(), "enabled": True, "label": "ESPN (pública)"},
                {**espn.core.stats.to_dict(), "enabled": True, "label": "ESPN core (datas de nascimento)"},
                {**tsdb.client.stats.to_dict(), "enabled": True, "label": "TheSportsDB (fotos)"},
                {**fd.client.stats.to_dict(), "enabled": fd.enabled, "label": "football-data.org"},
                {**af.client.stats.to_dict(), "enabled": af.enabled, "label": "API-Football"},
            ],
            "notes": notes[:30],
            "goal_events": int(len(goals)),
        },
        "views": built["views"],
        "home_away": built["home_away"],
        "competitions": built["competitions"],
        "seasons_compare": m.seasons_compare(matches, LEAGUE_KEY),
        "classicos": m.classicos(matches),
        "upcoming": m.upcoming(matches, current_table),
        "standings": {"season": CURRENT_SEASON, "rows": m.standings_table(current_table, team_ids)},
        "opponent_tiers": m.opponent_tiers(current_league, current_table),
        "simulation": simulation,
        "squads": squads,
        "analysis": {
            "coaches": coaches,
            "squad": diagnosis,
            "scouting": scouting,
            "finance": finances,
            "quality": quality,
        },
    }

    storage.write("dashboard", json.dumps(m.clean(payload), ensure_ascii=False))
    mark_refreshed(time.monotonic() - started)
    log.info("OK: %s (%d jogos, %d gols detalhados)", OUTPUT_JSON, len(matches), len(goals))
    for note in notes:
        log.warning(note)


if __name__ == "__main__":
    main()
