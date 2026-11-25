#!/usr/bin/env python3
"""Start the Room Ambiance server.

    python3 server.py               # control the real devices
    python3 server.py --dry-run     # everything works, but nothing is sent to the lights
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from ambiance import __version__  # noqa: E402
from ambiance.engine import Engine  # noqa: E402
from ambiance.web import AmbianceServer  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Local web control for the room's LED strip and WiZ bulb.")
    ap.add_argument("--host", help="address to listen on (default from config, 0.0.0.0)")
    ap.add_argument("--port", type=int, help="HTTP port (default from config, 8000)")
    ap.add_argument("--data-dir", default=str(ROOT / "data"), help="where settings and state are stored")
    ap.add_argument("--dry-run", action="store_true", help="don't send anything to the devices")
    ap.add_argument("-v", "--verbose", action="store_true", help="log every request")
    args = ap.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S")
    log = logging.getLogger("ambiance")

    engine = Engine(Path(args.data_dir), dry_run=args.dry_run)
    host = args.host or engine.cfg["http_host"]
    port = args.port or engine.cfg["http_port"]
    engine.cfg["http_port"] = port

    try:
        httpd = AmbianceServer((host, port), engine, ROOT / "web")
    except OSError as e:
        log.error("can't listen on %s:%s (%s). Is another copy already running?", host, port, e)
        return 1

    engine.start()
    log.info("Room Ambiance %s%s", __version__, " (dry run, devices untouched)" if args.dry_run else "")
    log.info("Open http://localhost:%s on this machine, or http://%s:%s from your phone",
             port, engine.local_ip, port)
    log.info("Strip: multicast %s:%s, %s LEDs | Bulb: %s",
             engine.cfg["multicast_group"], engine.cfg["strip_port"], engine.cfg["led_count"], engine.cfg["bulb_ip"])

    stop = threading.Event()

    def shutdown(*_):
        if stop.is_set():
            return
        stop.set()
        log.info("Shutting down (the lights keep their current state)")
        httpd.stopping = True
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    try:
        httpd.serve_forever(poll_interval=0.5)
    finally:
        engine.stop()
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
