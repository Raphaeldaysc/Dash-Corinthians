from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
# Dados mantidos à mão (técnicos, finanças, transfer ban, valores de mercado): versionados no git
MANUAL_DIR = ROOT / "data" / "manual"
OUTPUT_JSON = ROOT / "web" / "data" / "dashboard.json"
TIMEZONE = "America/Sao_Paulo"


def _load_dotenv(path: Path) -> None:
    """Leitor mínimo de .env para não depender de python-dotenv."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(ROOT / ".env")


def _seasons_from_env() -> list[int]:
    raw = os.environ.get("SEASONS", "2024,2025,2026")
    seasons = sorted({int(s) for s in raw.split(",") if s.strip().isdigit()})
    return seasons or [2026]


def _float_from_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


API_FOOTBALL_KEY = os.environ.get("API_FOOTBALL_KEY", "").strip()
FOOTBALL_DATA_KEY = os.environ.get("FOOTBALL_DATA_KEY", "").strip()
UPSTASH_REDIS_REST_URL = os.environ.get("UPSTASH_REDIS_REST_URL", "").strip().rstrip("/")
UPSTASH_REDIS_REST_TOKEN = os.environ.get("UPSTASH_REDIS_REST_TOKEN", "").strip()

SEASONS = _seasons_from_env()
CURRENT_SEASON = SEASONS[-1]
TARGET_RATE = min(max(_float_from_env("TARGET_RATE", 0.60), 0.0), 1.0)

TEAM_NAME = "Corinthians"
ESPN_TEAM_ID = "874"
API_FOOTBALL_TEAM_ID = 131


@dataclass(frozen=True)
class Competition:
    key: str
    name: str
    short: str
    espn_slug: str
    api_football_id: int
    kind: str  # "league" | "cup" | "state"
    color: str


COMPETITIONS: tuple[Competition, ...] = (
    Competition("brasileirao", "Brasileirão Série A", "BR", "bra.1", 71, "league", "#E4002B"),
    Competition("paulistao", "Paulistão", "SP", "bra.camp.paulista", 475, "state", "#F2A900"),
    Competition("copa_do_brasil", "Copa do Brasil", "CdB", "bra.copa_do_brazil", 73, "cup", "#1E9E5A"),
    Competition("libertadores", "Libertadores", "LIB", "conmebol.libertadores", 13, "cup", "#C9A227"),
    Competition("sudamericana", "Sul-Americana", "SUL", "conmebol.sudamericana", 11, "cup", "#2F6FDE"),
)
COMPETITION_BY_KEY = {c.key: c for c in COMPETITIONS}
COMPETITION_BY_SLUG = {c.espn_slug: c for c in COMPETITIONS}
COMPETITION_BY_API_FOOTBALL = {c.api_football_id: c for c in COMPETITIONS}
LEAGUE_KEY = "brasileirao"

# Nome normalizado do rival -> nome do clássico
CLASSICOS: dict[str, str] = {
    "palmeiras": "Derby Paulista",
    "sao paulo": "Majestoso",
    "santos": "Clássico Alvinegro",
}

# Zonas do Brasileirão por posição (1-indexado). O Z4 é calculado a partir do total de times.
ZONES = {
    "title": (1, 1),
    "g4": (1, 4),
    "g6": (1, 6),
    "sula": (7, 12),
}
RELEGATION_SPOTS = 4

SIMULATIONS = 10_000
SIM_SEED = 42
# Peso (em jogos "fictícios" na média da liga) para encolher as forças dos times no início da temporada
RATING_SHRINK_GAMES = 5.0

MINUTE_BINS: tuple[tuple[int, int, str], ...] = (
    (0, 15, "0-15"),
    (16, 30, "16-30"),
    (31, 45, "31-45+"),
    (46, 60, "46-60"),
    (61, 75, "61-75"),
    (76, 200, "76-90+"),
)

REST_BINS: tuple[tuple[int, int, str], ...] = (
    (0, 3, "até 3 dias"),
    (4, 5, "4-5 dias"),
    (6, 9, "6-9 dias"),
    (10, 10_000, "10+ dias"),
)

# TTLs de cache em segundos (None = nunca expira)
TTL_LIVE = 30 * 60
TTL_DAILY = 12 * 60 * 60
TTL_WEEKLY = 7 * 24 * 60 * 60


@dataclass(frozen=True)
class ScoutLeague:
    slug: str
    name: str
    # Peso subjetivo do nível da liga (Brasileirão = 1.0). Ajuste à vontade.
    strength: float
    # Ligas europeias: a temporada "2025" da ESPN é 2025/26; usar a anterior garante amostra completa
    season_offset: int = 0


SCOUT_LEAGUES: tuple[ScoutLeague, ...] = (
    ScoutLeague("bra.1", "Brasileirão", 1.00),
    ScoutLeague("arg.1", "Argentina", 0.85),
    ScoutLeague("col.1", "Colômbia", 0.70),
    ScoutLeague("uru.1", "Uruguai", 0.65),
    ScoutLeague("ecu.1", "Equador", 0.65),
    ScoutLeague("por.1", "Portugal", 0.95, season_offset=-1),
)
SCOUT_MAX_AGE = 29
SCOUT_MIN_APPS = 8
SCOUT_TOP_N = 10
# Avalia custo-benefício dentro de um grupo técnico mais amplo; evita que um
# atleta barato e muito produtivo fique fora por pouco do top 10 bruto.
SCOUT_VALUE_POOL_N = 30
