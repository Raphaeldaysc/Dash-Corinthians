"""Projeção do Brasileirão: forças de ataque/defesa (Poisson) + Monte Carlo dos jogos restantes."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import RATING_SHRINK_GAMES, RELEGATION_SPOTS, SIM_SEED, SIMULATIONS, ZONES

Json = dict[str, object]

_DEFAULT_HOME_GOALS = 1.45
_DEFAULT_AWAY_GOALS = 1.05
_MIN_GAMES_FOR_AVERAGES = 20


def _num(value: object) -> float:
    return 0.0 if value is None or pd.isna(value) else float(value)


def _ratings(teams: list[str], completed: pd.DataFrame, standings: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, float, float]:
    if len(completed) >= _MIN_GAMES_FOR_AVERAGES:
        avg_home, avg_away = float(completed["hg"].mean()), float(completed["ag"].mean())
    else:
        avg_home, avg_away = _DEFAULT_HOME_GOALS, _DEFAULT_AWAY_GOALS
    mean_goals = (avg_home + avg_away) / 2

    if not completed.empty:
        home = completed.groupby("home_id").agg(gf=("hg", "sum"), ga=("ag", "sum"), gp=("hg", "size"))
        away = completed.groupby("away_id").agg(gf=("ag", "sum"), ga=("hg", "sum"), gp=("ag", "size"))
        totals = home.add(away, fill_value=0).reindex(teams).fillna(0)
    else:
        totals = standings.set_index("team_id")[["gf", "ga", "gp"]].reindex(teams).fillna(0)

    k = RATING_SHRINK_GAMES
    attack = ((totals["gf"] + k * mean_goals) / (totals["gp"] + k) / mean_goals).to_numpy(dtype=float)
    defense = ((totals["ga"] + k * mean_goals) / (totals["gp"] + k) / mean_goals).to_numpy(dtype=float)
    return attack, defense, avg_home, avg_away


def simulate_league(
    standings: pd.DataFrame,
    league: pd.DataFrame,
    team_id: str,
    n_sims: int = SIMULATIONS,
    seed: int = SIM_SEED,
) -> Json:
    if standings.empty or team_id not in set(standings["team_id"].astype(str)):
        return {"available": False, "reason": "Tabela do Brasileirão indisponível"}

    table = standings.copy()
    table["team_id"] = table["team_id"].astype(str)
    teams = table["team_id"].tolist()
    idx = {t: i for i, t in enumerate(teams)}
    n_teams = len(teams)

    league = league.copy()
    league["home_id"] = league["home_id"].astype(str)
    league["away_id"] = league["away_id"].astype(str)
    in_table = league["home_id"].isin(idx) & league["away_id"].isin(idx)
    completed = league[in_table & league["completed"].eq(True) & league["hg"].notna()]
    remaining = league[in_table & ~league["completed"].eq(True)].sort_values("date")

    if remaining.empty:
        return {"available": False, "reason": "Não há jogos restantes do Brasileirão nesta temporada"}

    attack, defense, avg_home, avg_away = _ratings(teams, completed, table)
    hi = remaining["home_id"].map(idx).to_numpy()
    ai = remaining["away_id"].map(idx).to_numpy()
    lam_home = avg_home * attack[hi] * defense[ai]
    lam_away = avg_away * attack[ai] * defense[hi]

    rng = np.random.default_rng(seed)
    n_matches = len(remaining)
    goals_h = rng.poisson(lam_home, size=(n_sims, n_matches))
    goals_a = rng.poisson(lam_away, size=(n_sims, n_matches))

    def base(col: str) -> np.ndarray:
        return np.tile(table[col].fillna(0).to_numpy(dtype=float), (n_sims, 1))

    pts, wins, gd, gf = base("pts"), base("w"), base("gd"), base("gf")
    rows = np.arange(n_sims)
    for j in range(n_matches):
        h, a = hi[j], ai[j]
        gh, ga = goals_h[:, j], goals_a[:, j]
        home_win, draw, away_win = gh > ga, gh == ga, gh < ga
        pts[:, h] += 3 * home_win + draw
        pts[:, a] += 3 * away_win + draw
        wins[:, h] += home_win
        wins[:, a] += away_win
        gd[:, h] += gh - ga
        gd[:, a] += ga - gh
        gf[:, h] += gh
        gf[:, a] += ga

    # Critérios: pontos, vitórias, saldo, gols pró; ruído mínimo desempata o resto
    score = pts * 1e9 + wins * 1e6 + (gd + 500) * 1e3 + gf + rng.random((n_sims, n_teams)) * 0.5
    order = np.argsort(-score, axis=1)
    positions = np.empty_like(order)
    positions[rows[:, None], order] = np.arange(1, n_teams + 1)

    t = idx[team_id]
    team_pos = positions[:, t]
    team_pts = pts[:, t]
    z4_from = n_teams - RELEGATION_SPOTS + 1

    def prob_between(pos: np.ndarray, lo: int, hi_: int) -> float:
        return float(((pos >= lo) & (pos <= hi_)).mean())

    zone_probs = {name: prob_between(team_pos, lo, hi_) for name, (lo, hi_) in ZONES.items()}
    zone_probs["z4"] = prob_between(team_pos, z4_from, n_teams)

    sorted_pts = np.sort(pts, axis=1)[:, ::-1]
    cutoffs = {
        f"pos{p}": float(np.median(sorted_pts[:, p - 1])) for p in (1, 4, 6, 12, z4_from - 1, z4_from) if p <= n_teams
    }

    values, counts = np.unique(team_pts.astype(int), return_counts=True)
    team_fixtures = remaining[(remaining["home_id"] == team_id) | (remaining["away_id"] == team_id)]
    fixture_probs = []
    for j_label, row in team_fixtures.iterrows():
        j = remaining.index.get_loc(j_label)
        is_home = row["home_id"] == team_id
        mine, theirs = (goals_h[:, j], goals_a[:, j]) if is_home else (goals_a[:, j], goals_h[:, j])
        opp = row["away_id"] if is_home else row["home_id"]
        opp_row = table.iloc[idx[opp]]
        fixture_probs.append(
            {
                "date": row["date"].isoformat(),
                "opponent": opp_row["team"],
                "logo": opp_row.get("logo", ""),
                "opponent_rank": int(opp_row["rank"]),
                "venue": "H" if is_home else "A",
                "win": float((mine > theirs).mean()),
                "draw": float((mine == theirs).mean()),
                "loss": float((mine < theirs).mean()),
                "xg_for": float(lam_home[j] if is_home else lam_away[j]),
                "xg_against": float(lam_away[j] if is_home else lam_home[j]),
            }
        )

    projection = pd.DataFrame(
        {
            "team_id": teams,
            "team": table["team"],
            "short": table.get("short", table["team"]),
            "logo": table.get("logo", ""),
            "pts": table["pts"],
            "exp_pts": pts.mean(axis=0),
            "exp_pos": positions.mean(axis=0),
            "p_title": (positions == 1).mean(axis=0),
            "p_g6": (positions <= 6).mean(axis=0),
            "p_z4": (positions >= z4_from).mean(axis=0),
            "attack": attack,
            "defense": defense,
        }
    ).sort_values("exp_pts", ascending=False)

    team_row = table.iloc[t]
    team_gp = _num(team_row["gp"])
    return {
        "available": True,
        "n_sims": n_sims,
        "remaining_matches": int(n_matches),
        "team": {
            "pts": _num(team_row["pts"]),
            "gp": team_gp,
            "rank": int(team_row["rank"]),
            "remaining": int(len(team_fixtures)),
            "total_games": int(team_gp) + int(len(team_fixtures)),
        },
        "probs": zone_probs,
        "points": {
            "mean": float(team_pts.mean()),
            "p10": float(np.percentile(team_pts, 10)),
            "p25": float(np.percentile(team_pts, 25)),
            "p50": float(np.percentile(team_pts, 50)),
            "p75": float(np.percentile(team_pts, 75)),
            "p90": float(np.percentile(team_pts, 90)),
            "min": float(team_pts.min()),
            "max": float(team_pts.max()),
            "hist": [{"pts": int(v), "p": float(c) / n_sims} for v, c in zip(values, counts)],
        },
        "position_dist": [float((team_pos == p).mean()) for p in range(1, n_teams + 1)],
        "expected_position": float(team_pos.mean()),
        "cutoffs": cutoffs,
        "z4_from": z4_from,
        "fixtures": fixture_probs,
        "table": projection.to_dict("records"),
        "model": {
            "avg_home_goals": avg_home,
            "avg_away_goals": avg_away,
            "shrink_games": RATING_SHRINK_GAMES,
            "completed_games": int(len(completed)),
        },
    }
