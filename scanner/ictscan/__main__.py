"""Entry point: python -m ictscan  → scanner thread + dashboard on http://127.0.0.1:8765"""
import logging
import socket
import sys
import threading
from logging.handlers import RotatingFileHandler

import uvicorn

from .config import DASHBOARD_SYMBOLS, DATA_DIR, DB_PATH, LOG_PATH, SYMBOLS, WEB_HOST, WEB_PORT
from .runner import Scanner
from .store import Store
from .web.app import create_app


def port_in_use(host, port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex((host, port)) == 0


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    handlers = [RotatingFileHandler(LOG_PATH, maxBytes=2_000_000, backupCount=3, encoding="utf-8")]
    if sys.stdout is not None:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", handlers=handlers)
    log = logging.getLogger("ictscan")
    if port_in_use(WEB_HOST, WEB_PORT):
        log.warning("Port %s already in use; another scanner instance is probably running. Exiting.", WEB_PORT)
        return
    store = Store(DB_PATH)
    scanner = Scanner(store)
    threading.Thread(target=scanner.run_forever, name="scanner", daemon=True).start()
    threading.Thread(target=scanner.run_live, args=(DASHBOARD_SYMBOLS or tuple(SYMBOLS),), name="live", daemon=True).start()
    log.info("Dashboard on http://%s:%s (data in %s)", WEB_HOST, WEB_PORT, DATA_DIR)
    uvicorn.run(create_app(store, scanner), host=WEB_HOST, port=WEB_PORT, log_level="warning", log_config=None)


if __name__ == "__main__":
    main()
