"""Receita oficial (manual) x estimada (premiações + bilheteria) e risco de transfer ban.

A estimativa usa só o que os dados permitem inferir: fases alcançadas/posição final (ESPN) aplicadas às
tabelas de premiação e público dos jogos em casa × ticket médio, ambos informados em finance.json.
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from . import metrics as m
from .config import COMPETITION_BY_KEY, CURRENT_SEASON, LEAGUE_KEY, TIMEZONE
from .manual import Ban, Debt, Finance, PrizeTable
from .jsonutil import normalize_name

Json = dict[str, object]

RECENT_BAN = timedelta(days=730)


def _convert(amount: float, currency: str, year: int, finance: Finance, missing: set[str]) -> float | None:
    if currency == finance.currency:
        return amount
    rate = finance.fx.get(currency, {}).get(year)
    if rate is None:
        missing.add(f"Câmbio {currency}→{finance.currency} de {year} (fx.{currency}.{year})")
        return None
    return amount * rate


def _league_prize(
    table: PrizeTable, season: int, standings: pd.DataFrame | None, simulation: Json, team_id: str | None
) -> tuple[float | None, str]:
    if season == CURRENT_SEASON and simulation.get("available"):
        dist = simulation.get("position_dist")
        if isinstance(dist, list) and table.positions:
            expected = sum(float(p) * table.positions.get(i + 1, 0.0) for i, p in enumerate(dist) if isinstance(p, (int, float)))
            return expected, "posição final esperada (simulação)"
    if standings is not None and not standings.empty and team_id:
        row = standings[standings["team_id"].astype(str) == team_id]
        if not row.empty:
            rank = int(row["rank"].iloc[0])
            if rank in table.positions:
                return table.positions[rank], f"{rank}º lugar"
    return None, ""


def _year_block(
    season: int,
    season_df: pd.DataFrame,
    finance: Finance,
    standings: pd.DataFrame | None,
    simulation: Json,
    team_id: str | None,
    missing: set[str],
) -> Json:
    done = m.played(season_df)
    home = done[(done["venue"] == "H") & (done["attendance"] > 0)]
    attendance = float(home["attendance"].sum())
    price = finance.ticket_avg_price.get(season)
    if price is None and not home.empty:
        missing.add(f"Ticket médio de {season} (ticket_avg_price.{season})")
    ticket_est = attendance * price if price is not None else None

    items: list[Json] = []
    tables = finance.prizes.get(season, {})
    for comp_key in done["comp_key"].unique():
        table = tables.get(comp_key)
        comp = COMPETITION_BY_KEY.get(comp_key)
        if comp is None:
            continue
        if table is None:
            missing.add(f"Premiação {comp.short} {season} (prizes.{season}.{comp_key})")
            continue
        if comp_key == LEAGUE_KEY:
            amount, basis = _league_prize(table, season, standings, simulation, team_id)
        else:
            reached = [s for s in done.loc[done["comp_key"] == comp_key, "stage"].unique() if s]
            normalized_stages = {normalize_name(k): (k, v) for k, v in table.stages.items()}
            paid = [normalized_stages[normalize_name(s)] for s in reached if normalize_name(s) in normalized_stages]
            amount = sum(value for _, value in paid) if paid else None
            basis = ", ".join(label for label, _ in paid)
        if amount is None:
            continue
        converted = _convert(amount, table.currency, season, finance, missing)
        if converted is not None:
            items.append({"comp": comp.short, "color": comp.color, "basis": basis, "amount": converted})

    prizes_est = sum(float(i["amount"]) for i in items) if items else None
    parts = [v for v in (ticket_est, prizes_est) if v is not None]
    official = next((o for o in finance.official if o["year"] == season), None)
    return {
        "year": season,
        "home_games": int(len(home)),
        "attendance_total": attendance,
        "attendance_avg": attendance / len(home) if len(home) else None,
        "ticket_price": price,
        "ticket_est": ticket_est,
        "prize_items": items,
        "prizes_est": prizes_est,
        "estimated_total": sum(parts) if parts else None,
        "official": official,
    }


def ban_risk(bans: list[Ban], debts: list[Debt], today: date) -> Json:
    active = [b for b in bans if b.start <= today and (b.end is None or b.end >= today)]
    factors: list[Json] = []
    if active:
        factors.append({"label": f"Ban ativo desde {active[-1].start.strftime('%d/%m/%Y')} ({active[-1].authority})", "points": 100})
    else:
        fifa_open = [d for d in debts if d.status == "aberto" and d.fifa_case]
        overdue = [d for d in debts if d.status == "aberto" and not d.fifa_case and d.due_date is not None and d.due_date < today]
        agreements = [d for d in debts if d.status == "acordo"]
        recent = [b for b in bans if b.end is not None and today - b.end <= RECENT_BAN]
        if fifa_open:
            factors.append({"label": f"{len(fifa_open)} dívida(s) em aberto com processo na FIFA", "points": min(70, 35 * len(fifa_open))})
        if overdue:
            factors.append({"label": f"{len(overdue)} dívida(s) vencida(s) sem acordo", "points": min(30, 15 * len(overdue))})
        if agreements:
            factors.append({"label": f"{len(agreements)} acordo(s) de parcelamento em andamento", "points": min(15, 5 * len(agreements))})
        if recent:
            factors.append({"label": f"Ban encerrado há menos de 2 anos ({recent[-1].end:%d/%m/%Y})", "points": 15})
    score = min(100, sum(int(f["points"]) for f in factors))
    level = "Crítico" if score >= 75 else "Alto" if score >= 50 else "Moderado" if score >= 25 else "Baixo"

    open_totals: dict[str, float] = {}
    for d in debts:
        if d.status != "pago" and d.amount is not None:
            open_totals[d.currency] = open_totals.get(d.currency, 0.0) + d.amount
    return {
        "score": score,
        "level": "Ban ativo" if active else level,
        "active": bool(active),
        "factors": factors,
        "open_totals": open_totals,
        "bans": [
            {"start": b.start.isoformat(), "end": b.end.isoformat() if b.end else None, "authority": b.authority, "reason": b.reason, "source": b.source}
            for b in reversed(bans)
        ],
        "debts": [
            {
                "creditor": d.creditor,
                "amount": d.amount,
                "currency": d.currency,
                "due_date": d.due_date.isoformat() if d.due_date else None,
                "status": d.status,
                "fifa_case": d.fifa_case,
                "source": d.source,
            }
            for d in debts
        ],
        "has_data": bool(bans or debts),
    }


def finance_block(
    matches: pd.DataFrame,
    standings_by_season: dict[int, pd.DataFrame],
    simulation: Json,
    team_id: str | None,
    finance: Finance,
    bans: list[Ban],
    debts: list[Debt],
    as_of: date | None = None,
) -> Json:
    missing: set[str] = set()
    years = [
        _year_block(int(season), group, finance, standings_by_season.get(int(season)), simulation, team_id, missing)
        for season, group in matches.groupby("season")
    ]
    if not finance.official:
        missing.add("Receita oficial dos balanços (official)")
    return {
        "currency": finance.currency,
        "years": years,
        "missing": sorted(missing),
        "methodology": finance.methodology,
        "transfer_ban": {
            **ban_risk(bans, debts, pd.Timestamp.now(tz=TIMEZONE).date()),
            "as_of": as_of.isoformat() if as_of else None,
        },
    }
