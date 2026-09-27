"""Fotos de jogadores via TheSportsDB, com identidade confirmada e cache persistente (disco/Upstash).

Uma foto só é aceita se o nome bater exatamente E (o time no TheSportsDB for o Corinthians OU a data
de nascimento for igual à da ESPN). Na dúvida, fica sem foto (o front mostra as iniciais).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, timedelta

from . import storage
from .http_cache import SourceUnavailable
from .jsonutil import JsonDict, as_dict, as_str, normalize_name
from .normalize import TEAM_KEY
from .sources.espn import EspnSource
from .sources.thesportsdb import TheSportsDbSource, player_name, player_photo, player_team

log = logging.getLogger(__name__)

PHOTOS_KEY = "player_photos"
PHOTOS_CACHE_VERSION = 1
RECHECK_MISS = timedelta(days=7)
RECHECK_HIT = timedelta(days=60)


@dataclass(frozen=True)
class PlayerRef:
    player_id: str
    name: str
    full_name: str


def _load_cache() -> dict[str, object]:
    raw = storage.read(PHOTOS_KEY)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict) or data.get("version") != PHOTOS_CACHE_VERSION:
        return {}
    players = data.get("players")
    return dict(players) if isinstance(players, dict) else {}


def _is_fresh(entry: JsonDict, today: date) -> bool:
    try:
        checked = date.fromisoformat(as_str(entry.get("checked")))
    except ValueError:
        return False
    return today - checked < (RECHECK_HIT if entry.get("photo") else RECHECK_MISS)


def _match(espn: EspnSource, tsdb: TheSportsDbSource, ref: PlayerRef) -> str:
    birth: str | None = None
    queries = [ref.name] + ([ref.full_name] if ref.full_name and ref.full_name != ref.name else [])
    for query in queries:
        wanted = normalize_name(query)
        exact = [p for p in tsdb.search_players(query) if normalize_name(player_name(p)) == wanted]
        for candidate in exact:
            if normalize_name(player_team(candidate)) == TEAM_KEY and player_photo(candidate):
                return player_photo(candidate)
        if not exact:
            continue
        if birth is None:
            birth = espn.birth_date(ref.player_id)
        for candidate in exact:
            if birth and as_str(candidate.get("dateBorn")) == birth and player_photo(candidate):
                return player_photo(candidate)
    return ""


def resolve_photos(
    espn: EspnSource, tsdb: TheSportsDbSource, players: list[PlayerRef], notes: list[str]
) -> dict[str, str]:
    """player_id da ESPN -> URL da foto ('' quando não foi possível confirmar)."""
    cache = _load_cache()
    today = date.today()
    resolved: dict[str, str] = {}
    looked_up = found = failures = 0

    for ref in players:
        entry = as_dict(cache.get(ref.player_id))
        if _is_fresh(entry, today):
            resolved[ref.player_id] = as_str(entry.get("photo"))
            continue
        try:
            photo = _match(espn, tsdb, ref)
        except SourceUnavailable:
            failures += 1
            resolved[ref.player_id] = as_str(entry.get("photo"))
            continue
        looked_up += 1
        found += bool(photo)
        resolved[ref.player_id] = photo
        cache[ref.player_id] = {"name": ref.name, "photo": photo, "checked": today.isoformat()}

    log.info("Fotos: %d consultadas agora (%d encontradas), %d com foto no total", looked_up, found, sum(map(bool, resolved.values())))
    if looked_up:
        try:
            storage.write(PHOTOS_KEY, json.dumps({"version": PHOTOS_CACHE_VERSION, "players": cache}, ensure_ascii=False))
        except storage.StorageError as exc:
            notes.append(f"Cache de fotos não foi salvo: {exc}")
    if failures:
        notes.append(f"Fotos: {failures} jogadores não consultados (TheSportsDB/ESPN indisponível)")
    return resolved
