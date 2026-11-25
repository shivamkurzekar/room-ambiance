"""Settings stored in <data dir>/config.json, with defaults for a fresh install."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path

# The ESP32 firmware (ambilight_receiver.ino) has MAX_LEDS 120.
MAX_LEDS = 120

DEFAULTS: dict = {
    "http_host": "0.0.0.0",
    "http_port": 8000,
    "interface_ip": "",              # blank: auto-detect the LAN address
    "bulb_ip": "192.168.0.101",
    "bulb_port": 38899,
    "bulb_stream_hz": 6,             # max commands per second while animating the bulb
    "bulb_poll_seconds": 3,
    "multicast_group": "239.1.1.1",  # must match MCAST_IP in the ESP32's secrets.h
    "strip_port": 7777,              # must match UDP_PORT in the ESP32's secrets.h
    "led_count": 107,
    "fps": 30,
    "max_brightness": 100,           # hard cap for the strip, in percent
    # Physical strip layout for the live preview. Starts at the bottom centre of the
    # monitor and runs clockwise as seen from the front (same as the ambilight build).
    "layout": [["bottom_left", 16], ["left", 20], ["top", 35], ["right", 20], ["bottom_right", 16]],
}


def load_config(path: Path) -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    if path.exists():
        try:
            stored = json.loads(path.read_text())
            if isinstance(stored, dict):
                cfg.update({k: v for k, v in stored.items() if k in DEFAULTS})
        except (OSError, ValueError):
            pass
    return cfg


def save_json(path: Path, data: dict) -> None:
    """Atomic write so a crash never leaves a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, path)
