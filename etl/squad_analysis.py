"""Diagnóstico do elenco da temporada atual por setor, com regras explícitas (sem caixa-preta).

Cada alerta traz o número que o disparou; os limites abaixo são heurísticas editáveis, não verdades.
"""
from __future__ import annotations

import pandas as pd

from .config import ESPN_TEAM_ID

Json = dict[str, object]

SECTORS = {"G": "Goleiros", "D": "Defesa", "M": "Meio-campo", "F": "Ataque"}
# Titulares por jogo esperados em cada setor (4-3-3/4-2-3-1 simplificado)
SLOTS = {"G": 1, "D": 4, "M": 3, "F": 3}
REGULAR_SHARE = 0.30  # titular em >= 30% dos jogos = peça da rotação
VETERAN_AGE = 31
LATE_MINUTE = 76
SEVERITY = {"alta": 3, "media": 2, "baixa": 1}


def _flag(flags: list[Json], sector: str, severity: str, title: str, detail: str) -> None:
    flags.append({"sector": sector, "sector_label": SECTORS.get(sector, "Geral"), "severity": severity, "title": title, "detail": detail})


def _league_ranks(standings: pd.DataFrame) -> Json | None:
    if standings.empty or ESPN_TEAM_ID not in set(standings["team_id"].astype(str)):
        return None
    table = standings.assign(
        gf_rank=standings["gf"].rank(method="min", ascending=False),
        ga_rank=standings["ga"].rank(method="min", ascending=True),
    )
    row = table[table["team_id"].astype(str) == ESPN_TEAM_ID].iloc[0]
    return {"gf_rank": int(row["gf_rank"]), "ga_rank": int(row["ga_rank"]), "teams": len(table), "gf": row["gf"], "ga": row["ga"]}


def squad_diagnosis(
    squad: list[dict[str, object]],
    roster: pd.DataFrame,
    season_matches: pd.DataFrame,
    goals: pd.DataFrame,
    standings: pd.DataFrame,
    season: int,
) -> Json:
    team_games = int(season_matches["completed"].sum()) if not season_matches.empty else 0
    if not squad or team_games == 0:
        return {"available": False, "reason": "Sem jogos com súmula na temporada atual."}

    players = pd.DataFrame(squad)
    info = roster[["player_id", "age", "injured", "citizenship"]].drop_duplicates("player_id") if not roster.empty else None
    if info is not None:
        players = players.merge(info, on="player_id", how="left")
    else:
        players = players.assign(age=float("nan"), injured=False, citizenship="")
    players["injured"] = players["injured"].eq(True)
    players["ga"] = players["goals"] + players["assists"]
    players["regular"] = players["starts"] >= REGULAR_SHARE * team_games

    flags: list[Json] = []
    team_goals = float(players["goals"].sum())
    team_ga = float(players["ga"].sum())
    sectors: list[Json] = []
    for code, label in SECTORS.items():
        group = players[players["position"] == code]
        regulars = group[group["regular"]]
        weights = group["starts"].where(group["age"].notna(), 0)
        avg_age = float((group["age"].fillna(0) * weights).sum() / weights.sum()) if weights.sum() > 0 else None
        veterans = regulars[regulars["age"] >= VETERAN_AGE]
        ga_share = float(group["ga"].sum()) / team_ga if team_ga else None
        injured = group[group["injured"]]
        sectors.append(
            {
                "sector": code,
                "label": label,
                "players": int((group["apps"] > 0).sum()),
                "regulars": int(len(regulars)),
                "slots": SLOTS[code],
                "avg_age": avg_age,
                "veterans": veterans["player"].tolist(),
                "goals": float(group["goals"].sum()),
                "assists": float(group["assists"].sum()),
                "ga_share": ga_share,
                "injured": injured["player"].tolist(),
            }
        )
        if code != "G" and len(regulars) < SLOTS[code]:
            severity = "alta" if len(regulars) < SLOTS[code] - 1 else "media"
            _flag(flags, code, severity, "Rotação curta",
                  f"Só {len(regulars)} jogador(es) começaram ≥ {REGULAR_SHARE:.0%} dos jogos, para {SLOTS[code]} vagas de titular.")
        if avg_age is not None and avg_age >= 30:
            _flag(flags, code, "media", "Setor envelhecido", f"Idade média ponderada por titularidade: {avg_age:.1f} anos.")
        if len(veterans) >= 2:
            _flag(flags, code, "alta" if len(veterans) >= 3 else "media", "Dependência de veteranos",
                  f"{len(veterans)} titulares frequentes com {VETERAN_AGE}+ anos: {', '.join(veterans['player'].tolist())}.")
        if len(injured) >= 2:
            _flag(flags, code, "media", "Desfalques", f"{len(injured)} lesionados no setor: {', '.join(injured['player'].tolist())}.")

    gk = players[(players["position"] == "G") & (players["apps"] > 0)]
    faced = float(gk["saves"].sum() + gk["goals_conceded"].sum())
    save_pct = float(gk["saves"].sum()) / faced if faced else None
    if save_pct is not None and faced >= 30 and save_pct < 0.65:
        _flag(flags, "G", "media", "Poucas defesas", f"Goleiros defenderam {save_pct:.0%} das finalizações no gol (referência ~70%).")

    ranks = _league_ranks(standings)
    if ranks:
        teams = int(ranks["teams"])
        for code, key, what in (("F", "gf_rank", "gols marcados"), ("D", "ga_rank", "gols sofridos")):
            rank = int(ranks[key])
            if rank > teams * 0.5:
                _flag(flags, code, "alta" if rank > teams * 0.75 else "media", f"{what.capitalize()} abaixo da média",
                      f"{rank}º em {what} entre {teams} times do Brasileirão.")

    top = players.sort_values("ga", ascending=False).head(3)
    top1_share = float(top["ga"].iloc[0]) / team_ga if team_ga and not top.empty else None
    top3_share = float(top["ga"].sum()) / team_ga if team_ga else None
    if top1_share is not None and top1_share >= 0.30:
        _flag(flags, "F", "alta" if top1_share >= 0.40 else "media", "Dependência de um jogador",
              f"{top['player'].iloc[0]} participou de {top1_share:.0%} dos gols/assistências do time.")

    season_events = set(season_matches.loc[season_matches["completed"], "event_id"].astype(str))
    conceded = goals[goals["event_id"].astype(str).isin(season_events) & (goals["team_id"] != ESPN_TEAM_ID)]
    late = int((conceded["minute"] >= LATE_MINUTE).sum())
    late_share = late / len(conceded) if len(conceded) else None
    if late_share is not None and len(conceded) >= 10 and late_share >= 0.33:
        _flag(flags, "D", "media", "Gols sofridos no fim",
              f"{late_share:.0%} dos gols sofridos saíram a partir dos {LATE_MINUTE}' ({late} de {len(conceded)}).")

    reds = int(players["red"].sum())
    if reds >= 5:
        _flag(flags, "", "baixa", "Indisciplina", f"{reds} expulsões na temporada.")

    need = {code: sum(SEVERITY[str(f["severity"])] for f in flags if f["sector"] == code) for code in SECTORS}
    flags.sort(key=lambda f: -SEVERITY[str(f["severity"])])
    scatter = players[players["apps"] > 0]
    return {
        "available": True,
        "season": season,
        "team_games": team_games,
        "flags": flags,
        "need": need,
        "sectors": sectors,
        "ranks": ranks,
        "save_pct": save_pct,
        "late_conceded": {"share": late_share, "late": late, "total": int(len(conceded))},
        "dependency": {
            "top1_share": top1_share,
            "top3_share": top3_share,
            "top": [{"player": r["player"], "ga": r["ga"], "share": r["ga"] / team_ga if team_ga else None} for _, r in top.iterrows()],
            "team_goals": team_goals,
        },
        "players": [
            {
                "player": r["player"],
                "position": r["position"],
                "age": r["age"],
                "apps": r["apps"],
                "starts": r["starts"],
                "ga": r["ga"],
                "injured": bool(r["injured"]),
            }
            for _, r in scatter.iterrows()
        ],
    }
