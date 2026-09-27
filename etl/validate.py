"""Validação offline do contrato e das invariantes de web/data/dashboard.json.

Uso: python -m etl.validate
Não consulta APIs e não altera arquivos.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from .config import OUTPUT_JSON


class ValidationError(Exception):
    pass


def _require(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def _finite(value: Any, path: str, errors: list[str]) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        errors.append(f"Número não finito em {path}")
    elif isinstance(value, dict):
        for key, child in value.items():
            _finite(child, f"{path}.{key}", errors)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _finite(child, f"{path}[{index}]", errors)


def validate(path: Path = OUTPUT_JSON) -> dict[str, int]:
    errors: list[str] = []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"JSON ilegível: {exc}") from exc

    required = {
        "meta", "views", "home_away", "competitions", "seasons_compare", "classicos",
        "upcoming", "standings", "opponent_tiers", "simulation", "squads", "analysis",
    }
    _require(required <= set(data), f"Chaves de topo ausentes: {sorted(required - set(data))}", errors)
    meta = data.get("meta", {})
    seasons = meta.get("seasons", [])
    _require(bool(seasons), "meta.seasons está vazio", errors)

    all_games: list[dict[str, Any]] = []
    for season in seasons:
        key = f"{season}|all|all"
        view = data.get("views", {}).get(key)
        _require(isinstance(view, dict), f"Visão obrigatória ausente: {key}", errors)
        if not isinstance(view, dict):
            continue
        games = view.get("games", [])
        kpis = view.get("kpis", {})
        _require(kpis.get("games") == len(games), f"{key}: KPIs e lista de jogos divergem", errors)
        _require(
            kpis.get("games") == sum(int(kpis.get(x, 0)) for x in ("w", "d", "l")),
            f"{key}: vitórias + empates + derrotas divergem do total",
            errors,
        )
        gf, ga, gd = kpis.get("gf"), kpis.get("ga"), kpis.get("gd")
        if all(isinstance(x, (int, float)) for x in (gf, ga, gd)):
            _require(abs((gf - ga) - gd) < 1e-9, f"{key}: saldo de gols inconsistente", errors)
        for game in games:
            _require(bool(game.get("event_id")), f"{key}: jogo sem event_id", errors)
            _require(bool(game.get("source")), f"{key}: jogo sem source", errors)
        all_games.extend(games)

    identities = [(str(g.get("source")), str(g.get("event_id"))) for g in all_games]
    _require(len(identities) == len(set(identities)), "Partidas duplicadas nas visões anuais", errors)

    analysis = data.get("analysis", {})
    analysis_keys = {"coaches", "squad", "scouting", "finance", "quality"}
    _require(analysis_keys <= set(analysis), f"Análises ausentes: {sorted(analysis_keys - set(analysis))}", errors)
    coaches = analysis.get("coaches", {}).get("coaches", [])
    _require(len(coaches) <= 5, "Mais de cinco técnicos publicados", errors)
    quality = analysis.get("quality", {})
    _require(quality.get("matches", {}).get("completed") == len(all_games), "Qualidade: total de jogos divergente", errors)

    _finite(data, "$", errors)
    if errors:
        raise ValidationError("\n".join(f"- {error}" for error in errors))
    return {
        "seasons": len(seasons),
        "completed_matches": len(all_games),
        "views": len(data.get("views", {})),
        "squad_scopes": len(data.get("squads", {})),
    }


def main() -> None:
    try:
        summary = validate()
    except ValidationError as exc:
        raise SystemExit(f"FALHOU\n{exc}") from exc
    print(
        "OK: "
        f"{summary['seasons']} temporadas, "
        f"{summary['completed_matches']} jogos concluídos, "
        f"{summary['views']} visões e "
        f"{summary['squad_scopes']} recortes de elenco"
    )


if __name__ == "__main__":
    main()
