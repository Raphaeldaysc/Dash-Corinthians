"""Dados mantidos à mão em data/manual/*.json (não existem em API gratuita).

Arquivo ausente = seção vazia no dash. Entradas inválidas são ignoradas com aviso em `notes`.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date

from .config import MANUAL_DIR
from .jsonutil import JsonDict, as_dict, as_float, as_int, as_list, as_str, normalize_name

COACHES_FILE = "coaches.json"
FINANCE_FILE = "finance.json"
BANS_FILE = "transfer_bans.json"
VALUES_FILE = "market_values.json"


def _load(name: str, notes: list[str]) -> object:
    path = MANUAL_DIR / name
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        notes.append(f"data/manual/{name} inválido: {exc}")
        return None


def _date(value: object) -> date | None:
    try:
        return date.fromisoformat(as_str(value)[:10])
    except ValueError:
        return None


# ---------------------------------------------------------------- técnicos


@dataclass(frozen=True)
class Coach:
    name: str
    start: date
    end: date | None
    interim: bool


def load_coaches(notes: list[str]) -> list[Coach]:
    coaches: list[Coach] = []
    for raw in as_list(_load(COACHES_FILE, notes)):
        entry = as_dict(raw)
        name, start = as_str(entry.get("name")).strip(), _date(entry.get("start"))
        end = _date(entry.get("end")) if entry.get("end") else None
        if not name or start is None or (end is not None and end < start):
            notes.append(f"coaches.json: entrada ignorada (nome/datas inválidos): {entry}")
            continue
        coaches.append(Coach(name, start, end, entry.get("interim") is True))
    return sorted(coaches, key=lambda c: c.start)


# ---------------------------------------------------------------- finanças


@dataclass(frozen=True)
class PrizeTable:
    currency: str
    stages: dict[str, float]
    positions: dict[int, float]


@dataclass
class Finance:
    currency: str = "BRL"
    official: list[JsonDict] = field(default_factory=list)
    ticket_avg_price: dict[int, float] = field(default_factory=dict)
    fx: dict[str, dict[int, float]] = field(default_factory=dict)
    prizes: dict[int, dict[str, PrizeTable]] = field(default_factory=dict)
    methodology: JsonDict = field(default_factory=dict)


def _year_map(value: object) -> dict[int, float]:
    out: dict[int, float] = {}
    for key, raw in as_dict(value).items():
        year, amount = as_int(key), as_float(raw)
        if year is not None and amount is not None:
            out[year] = amount
    return out


def _amount_map(value: object) -> dict[str, float]:
    return {k: v for k, raw in as_dict(value).items() if (v := as_float(raw)) is not None}


def load_finance(notes: list[str]) -> Finance:
    data = as_dict(_load(FINANCE_FILE, notes))
    finance = Finance(currency=as_str(data.get("currency"), "BRL"))
    for raw in as_list(data.get("official")):
        entry = as_dict(raw)
        year = as_int(entry.get("year"))
        if year is None:
            continue
        finance.official.append(
            {
                "year": year,
                "revenue_total": as_float(entry.get("revenue_total")),
                "breakdown": _amount_map(entry.get("breakdown")),
                "debt_total": as_float(entry.get("debt_total")),
                "source": as_str(entry.get("source")),
            }
        )
    finance.ticket_avg_price = _year_map(data.get("ticket_avg_price"))
    finance.fx = {cur: _year_map(years) for cur, years in as_dict(data.get("fx")).items()}
    finance.methodology = as_dict(data.get("methodology"))
    for year_key, comps in as_dict(data.get("prizes")).items():
        year = as_int(year_key)
        if year is None:
            continue
        tables: dict[str, PrizeTable] = {}
        for comp_key, raw_table in as_dict(comps).items():
            table = as_dict(raw_table)
            positions = {p: v for k, v in _amount_map(table.get("positions")).items() if (p := as_int(k)) is not None}
            tables[comp_key] = PrizeTable(as_str(table.get("currency"), finance.currency), _amount_map(table.get("stages")), positions)
        finance.prizes[year] = tables
    return finance


# ---------------------------------------------------------------- transfer ban


@dataclass(frozen=True)
class Ban:
    start: date
    end: date | None
    authority: str
    reason: str
    source: str


@dataclass(frozen=True)
class Debt:
    creditor: str
    amount: float | None
    currency: str
    due_date: date | None
    status: str  # "aberto" | "acordo" | "pago"
    fifa_case: bool
    source: str


def load_bans(notes: list[str]) -> tuple[list[Ban], list[Debt], date | None]:
    data = as_dict(_load(BANS_FILE, notes))
    as_of = _date(data.get("as_of"))
    bans: list[Ban] = []
    for raw in as_list(data.get("bans")):
        entry = as_dict(raw)
        start = _date(entry.get("start"))
        if start is None:
            notes.append(f"transfer_bans.json: ban sem data de início ignorado: {entry}")
            continue
        end = _date(entry.get("end")) if entry.get("end") else None
        bans.append(Ban(start, end, as_str(entry.get("authority"), "FIFA"), as_str(entry.get("reason")), as_str(entry.get("source"))))
    debts: list[Debt] = []
    for raw in as_list(data.get("debts")):
        entry = as_dict(raw)
        status = as_str(entry.get("status"), "aberto").lower()
        if status not in {"aberto", "acordo", "pago"}:
            notes.append(f"transfer_bans.json: status '{status}' inválido (use aberto/acordo/pago)")
            continue
        debts.append(
            Debt(
                creditor=as_str(entry.get("creditor")),
                amount=as_float(entry.get("amount")),
                currency=as_str(entry.get("currency"), "BRL"),
                due_date=_date(entry.get("due_date")),
                status=status,
                fifa_case=entry.get("fifa_case") is True,
                source=as_str(entry.get("source")),
            )
        )
    return sorted(bans, key=lambda b: b.start), debts, as_of


# ---------------------------------------------------------------- valores de mercado


@dataclass(frozen=True)
class MarketValue:
    value_eur_m: float
    source: str


def load_market_values(notes: list[str]) -> dict[str, MarketValue]:
    """Chave: id do atleta na ESPN ou nome normalizado (o id evita homônimos)."""
    values: dict[str, MarketValue] = {}
    for raw in as_list(_load(VALUES_FILE, notes)):
        entry = as_dict(raw)
        value = as_float(entry.get("value_eur_m"))
        key = as_str(entry.get("espn_id")) or normalize_name(as_str(entry.get("player")))
        if value is None or value <= 0 or not key:
            continue
        values[key] = MarketValue(value, as_str(entry.get("source")))
    return values
