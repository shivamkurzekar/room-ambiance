"""HTTP server: static web app, JSON API and a Server-Sent Events stream."""

from __future__ import annotations

import json
import logging
import mimetypes
import queue
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from . import __version__
from .engine import Engine, ValidationError

log = logging.getLogger("ambiance.web")
MAX_BODY = 64 * 1024
mimetypes.add_type("application/manifest+json", ".webmanifest")


class AmbianceServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, engine: Engine, web_root: Path):
        super().__init__(address, Handler)
        self.engine = engine
        self.web_root = web_root.resolve()
        self.stopping = False


class Handler(BaseHTTPRequestHandler):
    server: AmbianceServer
    server_version = f"RoomAmbiance/{__version__}"
    protocol_version = "HTTP/1.1"

    # ------------------------------------------------------------ plumbing
    def log_message(self, fmt, *args):
        log.debug("%s %s", self.address_string(), fmt % args)

    def _json(self, data, status=HTTPStatus.OK):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, message, status=HTTPStatus.BAD_REQUEST):
        self._json({"error": message}, status)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise ValidationError("request too large")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw or b"{}")
        except ValueError:
            raise ValidationError("body must be JSON") from None
        if not isinstance(data, dict):
            raise ValidationError("body must be a JSON object")
        return data

    # ------------------------------------------------------------ routes
    def do_GET(self):
        path = urlparse(self.path).path
        eng = self.server.engine
        if path == "/api/state":
            return self._json(eng.snapshot())
        if path == "/api/catalog":
            return self._json(eng.catalog())
        if path == "/api/events":
            return self._events()
        if path == "/api/health":
            return self._json({"ok": True, "version": __version__})
        return self._static(path)

    def do_POST(self):
        path = urlparse(self.path).path
        eng = self.server.engine
        try:
            body = self._body()
            if path == "/api/strip":
                eng.update_strip(body)
            elif path == "/api/bulb":
                eng.update_bulb(body)
            elif path == "/api/link":
                eng.set_link(body.get("on"))
            elif path == "/api/power":
                eng.set_power(body.get("on"))
            elif path == "/api/timer":
                eng.set_timer(body.get("minutes", 0))
            elif path == "/api/ambiance":
                eng.apply_ambiance(str(body.get("id", "")))
            elif path == "/api/ambiances":
                return self._json(eng.save_ambiance(body.get("name")), HTTPStatus.CREATED)
            elif path == "/api/settings":
                eng.update_settings(body)
            elif path == "/api/discover":
                return self._json({"bulbs": eng.discover()})
            else:
                return self._error("not found", HTTPStatus.NOT_FOUND)
        except ValidationError as e:
            return self._error(str(e))
        except Exception:
            log.exception("POST %s failed", path)
            return self._error("something went wrong on the server", HTTPStatus.INTERNAL_SERVER_ERROR)
        return self._json({"ok": True})

    def do_DELETE(self):
        path = urlparse(self.path).path
        prefix = "/api/ambiances/"
        if not path.startswith(prefix):
            return self._error("not found", HTTPStatus.NOT_FOUND)
        try:
            self.server.engine.delete_ambiance(path[len(prefix):])
        except ValidationError as e:
            return self._error(str(e))
        return self._json({"ok": True})

    # ------------------------------------------------------------ static files
    def _static(self, path: str):
        if path in ("", "/"):
            path = "/index.html"
        target = (self.server.web_root / path.lstrip("/")).resolve()
        if self.server.web_root not in target.parents or not target.is_file():
            return self._error("not found", HTTPStatus.NOT_FOUND)
        body = target.read_bytes()
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    # ------------------------------------------------------------ live events
    def _events(self):
        eng = self.server.engine
        q = eng.subscribe()
        try:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            self._sse("state", json.dumps(eng.snapshot()))
            idle = 0
            while not self.server.stopping:
                try:
                    event, data = q.get(timeout=1.0)
                except queue.Empty:
                    idle += 1
                    if idle % 2 == 0:  # refresh device status every couple of seconds
                        self._sse("state", json.dumps(eng.snapshot()))
                    continue
                self._sse(event, data)
        except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
            pass
        finally:
            eng.unsubscribe(q)
            self.close_connection = True

    def _sse(self, event: str, data: str):
        self.wfile.write(f"event: {event}\ndata: {data}\n\n".encode())
        self.wfile.flush()
