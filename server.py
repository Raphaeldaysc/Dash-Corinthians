"""Servidor local do dash: serve web/ e expõe /api/status e /api/refresh.

Uso local (na raiz do projeto):  python server.py [--port 8000] [--auto]
Por padrão não atualiza dados sozinho; --auto ativa uma tentativa diária.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import threading
import time
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from etl import storage
from etl.config import ROOT
from etl.refresh_state import next_allowed, now_local, read_state, refreshed_today

WEB_DIR = ROOT / "web"
ETL_TIMEOUT_S = 30 * 60
AUTO_CHECK_INTERVAL_S = 60 * 60
REFRESH_HEADER = "X-Dash-Refresh"

log = logging.getLogger("server")


class RefreshJob:
    """Garante uma única execução do ETL por vez e guarda o resultado da última tentativa."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.running = False
        self.started_at: str | None = None
        self.last_error: str | None = None

    def try_start(self, trigger: str) -> tuple[bool, str]:
        with self._lock:
            if self.running:
                return False, "running"
            if refreshed_today():
                return False, "already_today"
            self.running = True
            self.started_at = now_local().isoformat()
            self.last_error = None
        threading.Thread(target=self._run, args=(trigger,), daemon=True).start()
        return True, "started"

    def _run(self, trigger: str) -> None:
        log.info("ETL iniciado (%s)", trigger)
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        try:
            result = subprocess.run(
                [sys.executable, "-m", "etl.build"],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=ETL_TIMEOUT_S,
            )
            if result.returncode != 0:
                tail = (result.stderr or result.stdout).strip().splitlines()[-6:]
                self.last_error = "\n".join(tail) or f"ETL saiu com código {result.returncode}"
                log.error("ETL falhou:\n%s", self.last_error)
            else:
                log.info("ETL concluído")
        except subprocess.TimeoutExpired:
            self.last_error = f"ETL excedeu {ETL_TIMEOUT_S // 60} minutos"
            log.error(self.last_error)
        finally:
            with self._lock:
                self.running = False

    def status(self) -> dict[str, object]:
        state = read_state()
        done_today = refreshed_today()
        return {
            "running": self.running,
            "started_at": self.started_at,
            "last_refresh": state.get("finished_at"),
            "last_duration_s": state.get("duration_s"),
            "refreshed_today": done_today,
            "can_refresh": not self.running and not done_today,
            "next_allowed": next_allowed().isoformat() if done_today else None,
            "last_error": self.last_error,
        }


JOB = RefreshJob()


class DashHandler(SimpleHTTPRequestHandler):
    # No Windows o registro pode mapear .js como text/plain, o que quebra <script type="module">
    extensions_map = {
        **SimpleHTTPRequestHandler.extensions_map,
        ".js": "text/javascript",
        ".css": "text/css",
        ".json": "application/json",
        ".html": "text/html",
        ".svg": "image/svg+xml",
    }

    def _json(self, status: HTTPStatus, payload: dict[str, object]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self) -> None:
        if self.path.startswith("/data/"):
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def do_GET(self) -> None:
        route = self.path.split("?")[0]
        if route == "/healthz":
            # Não toca no armazenamento: health checks frequentes não consomem a cota do Upstash
            self._json(HTTPStatus.OK, {"ok": True})
            return
        if route == "/api/status":
            self._json(HTTPStatus.OK, JOB.status())
            return
        super().do_GET()

    def do_POST(self) -> None:
        if self.path.split("?")[0] != "/api/refresh":
            self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return
        # Header customizado força preflight CORS, bloqueando POST disparado por outros sites
        if self.headers.get(REFRESH_HEADER) != "1":
            self._json(HTTPStatus.FORBIDDEN, {"error": "missing_header"})
            return
        started, reason = JOB.try_start("botão")
        if started:
            self._json(HTTPStatus.ACCEPTED, {"ok": True, **JOB.status()})
        elif reason == "running":
            self._json(HTTPStatus.CONFLICT, {"ok": False, "error": "running", **JOB.status()})
        else:
            self._json(HTTPStatus.TOO_MANY_REQUESTS, {"ok": False, "error": "already_today", **JOB.status()})

    def log_message(self, format: str, *args: object) -> None:
        if self.path.startswith("/api/"):
            log.debug(format, *args)


def auto_refresh_loop() -> None:
    while True:
        started, reason = JOB.try_start("automático")
        if not started and reason == "already_today":
            log.info("Dados já atualizados hoje; próxima janela em %s", next_allowed().strftime("%d/%m %H:%M"))
        time.sleep(AUTO_CHECK_INTERVAL_S)


def main() -> None:
    hosted = "PORT" in os.environ
    parser = argparse.ArgumentParser(description="Servidor do dash do Corinthians")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    parser.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0" if hosted else "127.0.0.1"))
    parser.add_argument("--auto", action="store_true", help="tenta rodar o ETL automaticamente uma vez por dia")
    parser.add_argument("--no-auto", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    if storage.remote_enabled():
        restored = storage.hydrate("dashboard")
        log.info("Upstash ativo; dashboard %s", "restaurado do armazenamento" if restored else "ainda não existe no armazenamento")
    if args.auto and not args.no_auto:
        threading.Thread(target=auto_refresh_loop, daemon=True).start()

    handler = partial(DashHandler, directory=str(WEB_DIR))
    server = ThreadingHTTPServer((args.host, args.port), handler)
    log.info("Dash em http://%s:%d", args.host, args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Encerrando")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
