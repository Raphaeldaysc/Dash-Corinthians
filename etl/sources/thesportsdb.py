"""TheSportsDB (chave pública gratuita "3"): usado só para fotos de jogadores."""
from __future__ import annotations

import html

from ..http_cache import CachedClient, RateLimit
from ..jsonutil import JsonDict, as_dict, as_list, as_str

BASE_URL = "https://www.thesportsdb.com/api/v1/json/3"
SOURCE = "thesportsdb"
# Plano free: 30 requisições/minuto
_MIN_INTERVAL_S = 2.1
_TTL_SEARCH_S = 30 * 86400
# Sufixo oficial de miniatura (~35 KB em vez de ~180 KB do original)
_IMAGE_SIZE = "/small"


class TheSportsDbSource:
    def __init__(self) -> None:
        self.client = CachedClient(SOURCE, BASE_URL, rate=RateLimit(min_interval_s=_MIN_INTERVAL_S))

    def search_players(self, name: str) -> list[JsonDict]:
        """A busca é aproximada ('Hugo' devolve Hugo Lloris): quem chama precisa confirmar a identidade."""
        data = self.client.get_json("/searchplayers.php", {"p": name}, ttl_s=_TTL_SEARCH_S)
        players = [as_dict(p) for p in as_list(as_dict(data).get("player"))]
        return [p for p in players if as_str(p.get("strSport")) == "Soccer"]


def player_name(player: JsonDict) -> str:
    return html.unescape(as_str(player.get("strPlayer")))


def player_team(player: JsonDict) -> str:
    return html.unescape(as_str(player.get("strTeam")))


def player_photo(player: JsonDict) -> str:
    url = as_str(player.get("strCutout")) or as_str(player.get("strThumb"))
    return f"{url}{_IMAGE_SIZE}" if url else ""
