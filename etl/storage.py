"""Persistência dos artefatos (JSON do dash e estado diário).

Sempre grava em disco local. Se UPSTASH_REDIS_REST_URL/TOKEN estiverem definidos, também grava no
Upstash Redis, que passa a ser a fonte da verdade. Esse modo é legado/opcional; o deploy estático
recomendado publica o JSON gerado localmente via Git.
"""
from __future__ import annotations

import base64
import gzip
import logging
from pathlib import Path

import requests

from .config import OUTPUT_JSON, ROOT, UPSTASH_REDIS_REST_TOKEN, UPSTASH_REDIS_REST_URL

log = logging.getLogger(__name__)

KEY_PREFIX = "dash-corinthians:"
LOCAL_PATHS: dict[str, Path] = {
    "dashboard": OUTPUT_JSON,
    "refresh_state": ROOT / "data" / "last_refresh.json",
    "match_details": ROOT / "data" / "match_details.json",
    "player_photos": ROOT / "data" / "player_photos.json",
}
_TIMEOUT_S = 20


class StorageError(Exception):
    """Falha ao gravar no armazenamento remoto."""


def remote_enabled() -> bool:
    return bool(UPSTASH_REDIS_REST_URL and UPSTASH_REDIS_REST_TOKEN)


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {UPSTASH_REDIS_REST_TOKEN}"}


def _pack(text: str) -> str:
    """gzip + base64: o JSON do dash encolhe ~10x e cabe folgado nos limites do plano free."""
    return base64.b64encode(gzip.compress(text.encode("utf-8"))).decode("ascii")


def _unpack(value: str) -> str:
    return gzip.decompress(base64.b64decode(value)).decode("utf-8")


def _remote_get(name: str) -> str | None:
    response = requests.get(f"{UPSTASH_REDIS_REST_URL}/get/{KEY_PREFIX}{name}", headers=_headers(), timeout=_TIMEOUT_S)
    response.raise_for_status()
    data = response.json()
    result = data.get("result") if isinstance(data, dict) else None
    return _unpack(result) if isinstance(result, str) else None


def _remote_set(name: str, text: str) -> None:
    try:
        response = requests.post(
            f"{UPSTASH_REDIS_REST_URL}/set/{KEY_PREFIX}{name}",
            headers=_headers(),
            data=_pack(text).encode("ascii"),
            timeout=_TIMEOUT_S,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise StorageError(f"Upstash: falha ao gravar '{name}': {exc}") from exc
    data = response.json()
    if isinstance(data, dict) and data.get("error"):
        raise StorageError(f"Upstash: {data['error']}")


def _local_write(name: str, text: str) -> None:
    path = LOCAL_PATHS[name]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _local_read(name: str) -> str | None:
    path = LOCAL_PATHS[name]
    return path.read_text(encoding="utf-8") if path.exists() else None


def write(name: str, text: str) -> None:
    _local_write(name, text)
    if remote_enabled():
        _remote_set(name, text)


def read(name: str) -> str | None:
    if remote_enabled():
        try:
            return _remote_get(name)
        except (requests.RequestException, ValueError, OSError) as exc:
            log.warning("Upstash indisponível ao ler '%s' (%s); usando disco local", name, exc)
    return _local_read(name)


def hydrate(name: str) -> bool:
    """Copia a versão remota para o disco local (usado ao iniciar em host com disco efêmero)."""
    if not remote_enabled():
        return False
    try:
        text = _remote_get(name)
    except (requests.RequestException, ValueError, OSError) as exc:
        log.warning("Não foi possível baixar '%s' do Upstash: %s", name, exc)
        return False
    if text is None:
        return False
    _local_write(name, text)
    return True
