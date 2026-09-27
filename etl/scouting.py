"""Reforços por setor: produção por jogo em outras ligas (ESPN), ponderada pelo nível da liga.

Sem valor de mercado em fonte gratuita: o custo entra só se informado em data/manual/market_values.json.
A ESPN não tem desarmes/interceptações/minutos, então defensores têm avaliação limitada (sinalizado no JSON).
"""
from __future__ import annotations

import logging

import pandas as pd

from .config import (
    CURRENT_SEASON,
    ESPN_TEAM_ID,
    SCOUT_LEAGUES,
    SCOUT_MAX_AGE,
    SCOUT_MIN_APPS,
    SCOUT_TOP_N,
    SCOUT_VALUE_POOL_N,
    TTL_WEEKLY,
)
from .http_cache import SourceUnavailable
from .jsonutil import normalize_name
from .manual import MarketValue
from .sources.espn import EspnSource

log = logging.getLogger(__name__)

Json = dict[str, object]

METRIC_LABEL = {
    "F": "(gols + 0,6×assist.) por jogo",
    "M": "(assist. + 0,7×gols + 0,05×chutes no gol) por jogo",
    "D": "titularidade + participação em gols − cartões",
    "G": "% de defesas",
}


def _pool(espn: EspnSource, notes: list[str]) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for league in SCOUT_LEAGUES:
        season = CURRENT_SEASON + league.season_offset
        try:
            table = espn.standings_by_slug(league.slug, season, TTL_WEEKLY)
        except SourceUnavailable as exc:
            notes.append(f"Scouting {league.name}: tabela indisponível ({exc})")
            continue
        for team_id in table["team_id"].astype(str) if not table.empty else []:
            if team_id == ESPN_TEAM_ID:
                continue
            try:
                roster = espn.roster(league.slug, team_id, season, TTL_WEEKLY)
            except SourceUnavailable:
                continue
            frames.append(roster.assign(league=league.name, strength=league.strength, season=season))
    frames = [f for f in frames if not f.empty]
    pool = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    log.info("Scouting: %d jogadores em %d ligas", len(pool), len(SCOUT_LEAGUES))
    return pool


def _scores(pool: pd.DataFrame) -> pd.Series:
    apps = pool["apps"].where(pool["apps"] > 0)
    starts_share = (pool["apps"] - pool["sub_ins"]) / apps
    raw = pd.Series(0.0, index=pool.index)
    pos = pool["position"]
    raw[pos == "F"] = ((pool["goals"] + 0.6 * pool["assists"]) / apps)[pos == "F"]
    raw[pos == "M"] = ((pool["assists"] + 0.7 * pool["goals"] + 0.05 * pool["shots_on_target"]) / apps)[pos == "M"]
    raw[pos == "D"] = (
        0.6 * starts_share + 0.4 * (pool["goals"] + pool["assists"]) / apps - 0.1 * (pool["yellow"] + 3 * pool["red"]) / apps
    )[pos == "D"]
    faced = pool["saves"] + pool["goals_conceded"]
    raw[pos == "G"] = (pool["saves"] / faced.where(faced > 0))[pos == "G"]
    return raw.fillna(0.0) * pool["strength"]


def scouting_block(espn: EspnSource, need: dict[str, int], values: dict[str, MarketValue], notes: list[str]) -> Json:
    pool = _pool(espn, notes)
    if pool.empty:
        return {"available": False, "reason": "Nenhuma liga de scouting respondeu."}
    pool = pool[
        (pool["apps"] >= SCOUT_MIN_APPS)
        & (pool["age"] <= SCOUT_MAX_AGE)
        & ((pool["apps"] - pool["sub_ins"]) >= 0.5 * pool["apps"])
        & pool["position"].isin(list(METRIC_LABEL))
        & ~pool["injured"]
    ].copy()
    raw_pool_size = len(pool)
    # Um atleta transferido pode aparecer em dois elencos da mesma temporada.
    # Mantemos a linha com maior amostra para não duplicar candidatos/ranking.
    pool["identity"] = pool["player_id"].astype(str)
    missing_id = pool["identity"].isin({"", "nan", "None"})
    pool.loc[missing_id, "identity"] = pool.loc[missing_id].apply(
        lambda r: f"{normalize_name(str(r['player']))}|{normalize_name(str(r['team']))}", axis=1
    )
    pool = pool.sort_values("apps", ascending=False).drop_duplicates("identity").copy()
    pool["score"] = _scores(pool)
    # Índice 0-100 = percentil dentro do próprio setor (compara atacante com atacante)
    pool["index"] = pool.groupby("position")["score"].rank(pct=True) * 100

    def market(row: pd.Series) -> MarketValue | None:
        return values.get(str(row["player_id"])) or values.get(normalize_name(str(row["player"])))

    sectors: list[Json] = []
    for code in sorted(METRIC_LABEL, key=lambda c: -need.get(c, 0)):
        top = pool[pool["position"] == code].sort_values("score", ascending=False).head(SCOUT_VALUE_POOL_N)
        candidates = []
        for _, row in top.iterrows():
            value = market(row)
            candidates.append(
                {
                    "player_id": row["player_id"],
                    "player": row["player"],
                    "age": row["age"],
                    "citizenship": row["citizenship"],
                    "team": row["team"],
                    "team_logo": row["team_logo"],
                    "league": row["league"],
                    "apps": row["apps"],
                    "starts": row["apps"] - row["sub_ins"],
                    "goals": row["goals"],
                    "assists": row["assists"],
                    "saves": row["saves"],
                    "goals_conceded": row["goals_conceded"],
                    "index": row["index"],
                    "value_eur_m": value.value_eur_m if value else None,
                    "value_source": value.source if value else "",
                    "cost_benefit": row["index"] / value.value_eur_m if value else None,
                }
            )
        # Quando existe valor documentado, mostra primeiro quem entrega mais índice
        # por € milhão. Sem valor, o atleta continua no radar, mas não recebe uma
        # classificação financeira presumida.
        candidates.sort(
            key=lambda c: (c["cost_benefit"] is not None, c["cost_benefit"] or 0.0, c["index"]),
            reverse=True,
        )
        candidates = candidates[:SCOUT_TOP_N]
        sectors.append(
            {
                "sector": code,
                "need": need.get(code, 0),
                "metric": METRIC_LABEL[code],
                "limited": code == "D",
                "candidates": candidates,
            }
        )
    return {
        "available": True,
        "leagues": [{"name": lg.name, "strength": lg.strength, "season": CURRENT_SEASON + lg.season_offset} for lg in SCOUT_LEAGUES],
        "filters": {"max_age": SCOUT_MAX_AGE, "min_apps": SCOUT_MIN_APPS},
        "pool_size": int(len(pool)),
        "raw_pool_size": int(raw_pool_size),
        "duplicates_removed": int(raw_pool_size - len(pool)),
        "sectors": sectors,
    }
