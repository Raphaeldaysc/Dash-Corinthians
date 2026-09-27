from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Mapping

import requests

from .config import RAW_DIR

log = logging.getLogger(__name__)

Params = Mapping[str, str | int]


class SourceUnavailable(Exception):
    """A fonte não respondeu, estourou o limite ou recusou a requisição."""


@dataclass(frozen=True)
class RateLimit:
    min_interval_s: float = 0.25
    daily_budget: int | None = None


@dataclass
class SourceStats:
    name: str
    calls: int = 0
    cache_hits: int = 0
    stale_hits: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "calls": self.calls,
            "cache_hits": self.cache_hits,
            "stale_hits": self.stale_hits,
            "errors": self.errors[:5],
        }


class CachedClient:
    """GET JSON com cache em disco, TTL, intervalo mínimo entre chamadas e orçamento diário."""

    def __init__(
        self,
        name: str,
        base_url: str,
        headers: Mapping[str, str] | None = None,
        rate: RateLimit = RateLimit(),
        timeout_s: float = 20.0,
    ) -> None:
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.rate = rate
        self.timeout_s = timeout_s
        self.stats = SourceStats(name)
        self._dir = RAW_DIR / name
        self._dir.mkdir(parents=True, exist_ok=True)
        self._budget_file = self._dir / "_budget.json"
        self._last_call = 0.0
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "dash-corinthians/1.0", "Accept": "application/json"})
        if headers:
            self._session.headers.update(dict(headers))

    def _cache_path(self, path: str, params: Params | None) -> Path:
        items = sorted((k, str(v)) for k, v in (params or {}).items())
        key = hashlib.sha1(f"{path}?{items}".encode("utf-8")).hexdigest()[:20]
        return self._dir / f"{key}.json"

    def _read_budget(self) -> int:
        if not self._budget_file.exists():
            return 0
        try:
            data = json.loads(self._budget_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return 0
        used = data.get(date.today().isoformat(), 0) if isinstance(data, dict) else 0
        return used if isinstance(used, int) else 0

    def _spend_budget(self) -> None:
        today = date.today().isoformat()
        self._budget_file.write_text(json.dumps({today: self._read_budget() + 1}), encoding="utf-8")

    def _throttle(self) -> None:
        wait = self.rate.min_interval_s - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()

    def _fetch(self, path: str, params: Params | None) -> object:
        if self.rate.daily_budget is not None and self._read_budget() >= self.rate.daily_budget:
            raise SourceUnavailable(f"{self.name}: orçamento diário de {self.rate.daily_budget} requisições esgotado")

        url = f"{self.base_url}{path}"
        last_error = ""
        for attempt in range(3):
            self._throttle()
            try:
                response = self._session.get(url, params=dict(params or {}), timeout=self.timeout_s)
            except requests.RequestException as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                time.sleep(1.5 * (attempt + 1))
                continue

            self.stats.calls += 1
            if self.rate.daily_budget is not None:
                self._spend_budget()

            if response.status_code == 429 or response.status_code >= 500:
                last_error = f"HTTP {response.status_code}"
                retry_after = response.headers.get("Retry-After", "")
                time.sleep(float(retry_after) if retry_after.isdigit() else 2.0 * (attempt + 1))
                continue
            if response.status_code >= 400:
                raise SourceUnavailable(f"{self.name}: HTTP {response.status_code} em {path}")
            try:
                return response.json()
            except ValueError as exc:
                raise SourceUnavailable(f"{self.name}: resposta não é JSON em {path}") from exc

        raise SourceUnavailable(f"{self.name}: falhou após 3 tentativas em {path} ({last_error})")

    def get_json(self, path: str, params: Params | None = None, ttl_s: float | None = 3600) -> object:
        """ttl_s=None faz o cache nunca expirar (jogos encerrados, temporadas passadas)."""
        cache_file = self._cache_path(path, params)
        if cache_file.exists():
            fresh = ttl_s is None or (time.time() - cache_file.stat().st_mtime) < ttl_s
            if fresh:
                self.stats.cache_hits += 1
                return self._read_cache(cache_file)

        try:
            data = self._fetch(path, params)
        except SourceUnavailable as exc:
            self.stats.errors.append(str(exc))
            if cache_file.exists():
                log.warning("%s — usando cache vencido", exc)
                self.stats.stale_hits += 1
                return self._read_cache(cache_file)
            raise

        envelope = {"path": path, "params": dict(params or {}), "fetched_at": time.time(), "data": data}
        cache_file.write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
        return data

    @staticmethod
    def _read_cache(cache_file: Path) -> object:
        envelope = json.loads(cache_file.read_text(encoding="utf-8"))
        return envelope.get("data") if isinstance(envelope, dict) else None
