"""Type guards para navegar em JSON de APIs externas sem assumir o formato."""
from __future__ import annotations

import re
import unicodedata

JsonDict = dict[str, object]


def as_dict(value: object) -> JsonDict:
    return value if isinstance(value, dict) else {}


def as_list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def as_str(value: object, default: str = "") -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return default


def as_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def as_int(value: object) -> int | None:
    number = as_float(value)
    return int(number) if number is not None else None


def dig(value: object, *path: str | int) -> object:
    """Acessa caminhos aninhados (dict/list) devolvendo None se algo faltar."""
    current = value
    for key in path:
        if isinstance(key, int):
            items = as_list(current)
            if key >= len(items) or key < -len(items):
                return None
            current = items[key]
        else:
            current = as_dict(current).get(key)
    return current


_NOISE_TOKENS = {"fc", "ec", "sc", "se", "cr", "ca", "ac", "clube", "club", "de", "futebol", "esporte", "regatas"}


def normalize_name(name: str) -> str:
    """Normaliza nomes de times entre fontes: 'São Paulo FC' -> 'sao paulo'."""
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii").lower()
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    tokens = [t for t in text.split() if t not in _NOISE_TOKENS]
    return " ".join(tokens)
