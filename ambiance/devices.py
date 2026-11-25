"""Device drivers: the ESP32 LED strip (UDP multicast) and the WiZ bulb (UDP JSON)."""

from __future__ import annotations

import json
import logging
import socket
import struct
import threading
import time
from typing import Callable

from .color import RGB, clamp

log = logging.getLogger("ambiance.devices")


def detect_local_ip(probe_ip: str = "192.168.0.1") -> str:
    """Return the LAN address the OS would use to reach probe_ip (no traffic is sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((probe_ip, 9))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


# --------------------------------------------------------------------------- strip
class StripOutput:
    """Sends frames in the ambilight receiver's packet format (v2).

    Header, little-endian: magic u16 0xA1B2 | version u8 2 | led count u8 |
    sequence u16 | sender time in microseconds u32, followed by R,G,B per LED.
    """

    MAGIC = 0xA1B2
    VERSION = 2
    HEADER = struct.Struct("<HBBHI")

    def __init__(self, group: str, port: int, local_ip: str, dry_run: bool = False):
        self.group, self.port, self.dry_run = group, port, dry_run
        self.seq = 0
        self.frames_sent = 0
        self.last_send = 0.0
        self.last_error: str | None = None
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
        self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 0)
        try:
            self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(local_ip))
        except OSError as e:
            log.warning("could not pin multicast to %s: %s", local_ip, e)

    @classmethod
    def build_packet(cls, seq: int, rgb: bytes, t_us: int) -> bytes:
        count = len(rgb) // 3
        return cls.HEADER.pack(cls.MAGIC, cls.VERSION, count, seq & 0xFFFF, t_us & 0xFFFFFFFF) + rgb

    def send(self, rgb: bytes) -> None:
        pkt = self.build_packet(self.seq, rgb, time.perf_counter_ns() // 1000)
        self.seq = (self.seq + 1) & 0xFFFF
        self.last_send = time.monotonic()
        if self.dry_run:
            self.frames_sent += 1
            return
        try:
            self.sock.sendto(pkt, (self.group, self.port))
            self.frames_sent += 1
            self.last_error = None
        except OSError as e:
            if self.last_error != str(e):
                log.warning("strip send failed: %s", e)
            self.last_error = str(e)

    def close(self) -> None:
        self.sock.close()


class AmbilightMonitor:
    """Listens on the strip's multicast group to notice another sender (the PC ambilight)."""

    def __init__(self, group: str, port: int, local_ip: str):
        self.local_ip = local_ip
        self.sources: dict[str, float] = {}
        self._stop = threading.Event()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        self.sock.settimeout(1.0)
        self.ok = True
        try:
            self.sock.bind(("", port))
            mreq = socket.inet_aton(group) + socket.inet_aton(local_ip)
            self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        except OSError as e:
            log.warning("ambilight monitor disabled: %s", e)
            self.ok = False
        self.thread = threading.Thread(target=self._loop, name="ambilight-monitor", daemon=True)

    def start(self):
        if self.ok:
            self.thread.start()

    def _loop(self):
        while not self._stop.is_set():
            try:
                _, (ip, _) = self.sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break
            if ip != self.local_ip:
                self.sources[ip] = time.monotonic()

    def status(self) -> dict:
        now = time.monotonic()
        active = [ip for ip, t in self.sources.items() if now - t < 3.0]
        return {"active": bool(active), "source": active[0] if active else None}

    def close(self):
        self._stop.set()
        self.sock.close()


# --------------------------------------------------------------------------- bulb
def rgb_to_wiz(rgb: RGB | None) -> dict:
    """Convert a perceived color (brightness baked in) into setPilot params."""
    if rgb is None:
        return {"state": False}
    m = max(rgb)
    if m < 4:
        return {"state": False}
    k = 255.0 / m
    return {"state": True,
            "r": int(round(clamp(rgb[0] * k, 0, 255))),
            "g": int(round(clamp(rgb[1] * k, 0, 255))),
            "b": int(round(clamp(rgb[2] * k, 0, 255))),
            "dimming": int(clamp(round(m / 255.0 * 100), 10, 100))}


class WizBulb:
    """Local UDP control of a WiZ bulb on port 38899 (no cloud)."""

    def __init__(self, ip: str, port: int, stream_hz: float, poll_seconds: float,
                 on_pilot: Callable[[dict], None] | None = None, dry_run: bool = False):
        self.ip, self.port = ip, port
        self.stream_hz = stream_hz
        self.poll_seconds = poll_seconds
        self.on_pilot = on_pilot
        self.dry_run = dry_run
        self.pilot: dict = {}
        self.last_seen = 0.0
        self.last_command = 0.0
        self._last_stream_params: dict | None = None
        self._last_stream_t = 0.0
        self._stop = threading.Event()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("", 0))
        self.sock.settimeout(1.0)
        self._threads = [threading.Thread(target=self._recv_loop, name="wiz-recv", daemon=True),
                         threading.Thread(target=self._poll_loop, name="wiz-poll", daemon=True)]

    def start(self):
        for t in self._threads:
            t.start()

    def set_target(self, ip: str, port: int | None = None):
        self.ip = ip
        if port:
            self.port = port
        self.pilot, self.last_seen = {}, 0.0
        self._last_stream_params = None

    # ---- sending
    def _send(self, method: str, params: dict) -> None:
        if self.dry_run:
            return
        try:
            self.sock.sendto(json.dumps({"method": method, "params": params}).encode(), (self.ip, self.port))
        except OSError as e:
            log.debug("bulb send failed: %s", e)

    def set_pilot(self, params: dict) -> None:
        """Send a command now (used for static states)."""
        self.last_command = time.monotonic()
        self._last_stream_params = None
        self._send("setPilot", params)

    def stream(self, rgb: RGB | None) -> None:
        """Feed an animated color every frame; sends are rate limited and de-duplicated."""
        now = time.monotonic()
        if now - self._last_stream_t < 1.0 / self.stream_hz:
            return
        params = rgb_to_wiz(rgb)
        last = self._last_stream_params
        if last is not None and now - self._last_stream_t < 2.0 and _close(last, params):
            return
        self._last_stream_t = now
        self._last_stream_params = params
        self.last_command = now
        self._send("setPilot", params)

    def request_pilot(self) -> None:
        self._send("getPilot", {})

    # ---- receiving
    def _recv_loop(self):
        while not self._stop.is_set():
            try:
                data, (ip, _) = self.sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            if ip != self.ip:
                continue
            try:
                msg = json.loads(data)
            except ValueError:
                continue
            self.last_seen = time.monotonic()
            if msg.get("method") == "getPilot" and isinstance(msg.get("result"), dict):
                self.pilot = msg["result"]
                if self.on_pilot:
                    try:
                        self.on_pilot(dict(self.pilot))
                    except Exception:  # never let a callback kill the receiver
                        log.exception("on_pilot failed")

    def _poll_loop(self):
        while not self._stop.is_set():
            self.request_pilot()
            self._stop.wait(self.poll_seconds)

    def status(self) -> dict:
        if self.dry_run:
            return {"online": True, "ip": self.ip, "rssi": None, "seen_ago": 0, "dry_run": True}
        seen_ago = time.monotonic() - self.last_seen if self.last_seen else None
        return {"online": seen_ago is not None and seen_ago < max(10.0, self.poll_seconds * 3),
                "ip": self.ip, "rssi": self.pilot.get("rssi"),
                "seen_ago": None if seen_ago is None else round(seen_ago, 1)}

    def close(self):
        self._stop.set()
        self.sock.close()


def _close(a: dict, b: dict) -> bool:
    if a.get("state") != b.get("state"):
        return False
    if not b.get("state"):
        return True
    diff = sum(abs(a.get(k, 0) - b.get(k, 0)) for k in ("r", "g", "b"))
    return diff < 9 and abs(a.get("dimming", 0) - b.get("dimming", 0)) < 3


def discover_bulbs(local_ip: str, timeout: float = 2.5) -> list[dict]:
    """Broadcast a read-only getPilot and collect every WiZ bulb that answers."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.bind((local_ip, 0))
    s.settimeout(0.25)
    query = json.dumps({"method": "getPilot", "params": {}}).encode()
    subnet_bcast = ".".join(local_ip.split(".")[:3] + ["255"])
    found: dict[str, dict] = {}
    end = time.monotonic() + timeout
    try:
        while time.monotonic() < end:
            for target in (subnet_bcast, "255.255.255.255"):
                try:
                    s.sendto(query, (target, 38899))
                except OSError:
                    pass
            try:
                while True:
                    data, (ip, _) = s.recvfrom(4096)
                    try:
                        res = json.loads(data).get("result", {})
                    except ValueError:
                        continue
                    found[ip] = {"ip": ip, "mac": res.get("mac"), "rssi": res.get("rssi"),
                                 "state": res.get("state")}
            except socket.timeout:
                pass
    finally:
        s.close()
    return sorted(found.values(), key=lambda d: tuple(int(p) for p in d["ip"].split(".")))
