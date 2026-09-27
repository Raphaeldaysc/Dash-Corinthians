"""Desempenho dos últimos técnicos, com os jogos atribuídos pelo período informado em coaches.json."""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from . import metrics as m
from .config import COMPETITIONS, TIMEZONE
from .manual import Coach

LAST_N = 5
# Recorte para comparar trabalhos de tamanhos diferentes em igualdade ("primeiros N jogos")
CURVE_GAMES = 40


def _played_local(matches: pd.DataFrame) -> tuple[pd.DataFrame, date | None]:
    done = m.played(matches)
    local = done["date"].dt.tz_convert(TIMEZONE).dt.date
    return done.assign(local_date=local), (local.min() if not done.empty else None)


def coaches_block(matches: pd.DataFrame, coaches: list[Coach]) -> m.Json:
    if not coaches:
        return {"available": False, "reason": "Preencha data/manual/coaches.json com nome e período de cada técnico."}
    done, first_loaded = _played_local(matches)
    today = pd.Timestamp.now(tz=TIMEZONE).date()
    out: list[m.Json] = []
    for coach in coaches[-LAST_N:]:
        end = coach.end or today
        games = done[(done["local_date"] >= coach.start) & (done["local_date"] <= end)].reset_index(drop=True)
        k = m.kpis(games)
        ha = m.home_away(games)
        by_comp = [
            {"comp_key": c.key, "comp": c.short, "color": c.color, **{key: kc[key] for key in ("games", "rate", "w", "d", "l")}}
            for c in COMPETITIONS
            if (kc := m.kpis(games[games["comp_key"] == c.key]))["games"]
        ]
        curve = (games["points"].cumsum() / (3 * np.arange(1, len(games) + 1))).head(CURVE_GAMES)
        out.append(
            {
                "name": coach.name,
                "start": coach.start.isoformat(),
                "end": coach.end.isoformat() if coach.end else None,
                "current": coach.end is None,
                "interim": coach.interim,
                "days": (end - coach.start).days + 1,
                "partial": first_loaded is not None and coach.start < first_loaded,
                "kpis": k,
                "home_rate": ha["home"]["rate"],
                "away_rate": ha["away"]["rate"],
                "by_comp": by_comp,
                "curve": curve.round(4).tolist(),
                "form": games["result"].tail(5).tolist(),
            }
        )
    return {
        "available": True,
        "first_loaded": first_loaded.isoformat() if first_loaded else None,
        "curve_games": CURVE_GAMES,
        "coaches": out,
    }
