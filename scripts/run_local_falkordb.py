"""Run the installed FalkorDB Lite server with durable local state.

Linux/WSL only. The runner owns the Redis process it starts, listens on
127.0.0.1:6379, and keeps the RDB and append-only data under the user's Linux
home directory. It never installs packages, changes distros, or flushes graphs.
"""
from __future__ import annotations

import signal
import socket
import sys
import threading
import time
from pathlib import Path


HOST = "127.0.0.1"
PORT = 6379
DATA_DIR = Path.home() / ".local" / "share" / "hivemind" / "falkordb-data"
DB_FILE = DATA_DIR / "memory.rdb"


def _port_in_use() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.25)
        return probe.connect_ex((HOST, PORT)) == 0


def main() -> int:
    if sys.platform != "linux":
        print("FalkorDB runner requires Linux/WSL; run it inside the WSL distribution.", file=sys.stderr)
        return 1
    if _port_in_use():
        print(f"Cannot start local FalkorDB: TCP port {HOST}:{PORT} is already in use.", file=sys.stderr)
        return 1

    try:
        from redislite.falkordb_client import FalkorDB
    except ImportError as exc:
        print(f"FalkorDB package unavailable in this Python environment: {exc}", file=sys.stderr)
        return 1

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    server = None
    stopping = threading.Event()

    def request_stop(_signum, _frame):
        stopping.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    try:
        server = FalkorDB(
            dbfilename=str(DB_FILE),
            serverconfig={
                "port": str(PORT),
                "bind": HOST,
                "appendonly": "yes",
                "appendfsync": "everysec",
                "dir": str(DATA_DIR),
                "appenddirname": "appendonlydir",
            },
        )
        server.client.execute_command("GRAPH.QUERY", "memory", "RETURN 1")
        print(f"FalkorDB ready at {HOST}:{PORT}; data={DB_FILE}; persistence=RDB+appendonly", flush=True)
        while not stopping.wait(1.0):
            try:
                server.client.ping()
            except Exception as exc:
                print(f"FalkorDB server exited or became unavailable: {type(exc).__name__}", file=sys.stderr)
                return 1
        try:
            server.client.shutdown(save=True)
        except Exception as exc:
            print(f"FalkorDB graceful shutdown failed: {type(exc).__name__}", file=sys.stderr)
            return 1
        return 0
    except Exception as exc:
        print(f"FalkorDB startup failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    finally:
        if server is not None:
            server.close()


if __name__ == "__main__":
    raise SystemExit(main())
