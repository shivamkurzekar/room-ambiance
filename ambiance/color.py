"""Color helpers shared by the effects, the devices and the API."""

from __future__ import annotations

import colorsys
import math
import re

RGB = tuple  # (r, g, b) floats in 0..255

_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


def is_hex_color(value: object) -> bool:
    return isinstance(value, str) and bool(_HEX_RE.match(value.strip()))


def hex_to_rgb(value: str) -> RGB:
    h = value.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        raise ValueError(f"not a hex color: {value!r}")
    return (float(int(h[0:2], 16)), float(int(h[2:4], 16)), float(int(h[4:6], 16)))


def rgb_to_hex(rgb: RGB) -> str:
    return "#%02x%02x%02x" % tuple(int(clamp(round(c), 0, 255)) for c in rgb)


def normalize_hex(value: str) -> str:
    return rgb_to_hex(hex_to_rgb(value))


def hsv(h: float, s: float = 1.0, v: float = 1.0) -> RGB:
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, clamp(s, 0, 1), clamp(v, 0, 1))
    return (r * 255.0, g * 255.0, b * 255.0)


def mix(a: RGB, b: RGB, t: float) -> RGB:
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t)


def scale(c: RGB, k: float) -> RGB:
    return (c[0] * k, c[1] * k, c[2] * k)


def average(frame: list[RGB]) -> RGB:
    if not frame:
        return (0.0, 0.0, 0.0)
    n = len(frame)
    return (sum(c[0] for c in frame) / n, sum(c[1] for c in frame) / n, sum(c[2] for c in frame) / n)


def kelvin_to_rgb(kelvin: float) -> RGB:
    """Approximate the color of a white light at the given temperature."""
    t = clamp(kelvin, 1000, 40000) / 100.0
    if t <= 66:
        r = 255.0
        g = 99.4708025861 * math.log(t) - 161.1195681661
    else:
        r = 329.698727446 * ((t - 60) ** -0.1332047592)
        g = 288.1221695283 * ((t - 60) ** -0.0755148492)
    if t >= 66:
        b = 255.0
    elif t <= 19:
        b = 0.0
    else:
        b = 138.5177312231 * math.log(t - 10) - 305.0447927307
    return (clamp(r, 0, 255), clamp(g, 0, 255), clamp(b, 0, 255))


def ramp(stops: list[RGB], x: float, cyclic: bool) -> RGB:
    """Interpolate along color stops. x is 0..1 (wrapped when cyclic)."""
    if len(stops) == 1:
        return stops[0]
    if cyclic:
        x %= 1.0
        pos = x * len(stops)
        i = int(pos) % len(stops)
        return mix(stops[i], stops[(i + 1) % len(stops)], _smooth(pos - int(pos)))
    x = clamp(x, 0.0, 1.0)
    pos = x * (len(stops) - 1)
    i = min(int(pos), len(stops) - 2)
    return mix(stops[i], stops[i + 1], _smooth(pos - i))


def _smooth(t: float) -> float:
    return t * t * (3 - 2 * t)


def _stops(*hexes: str) -> list[RGB]:
    return [hex_to_rgb(h) for h in hexes]


PALETTE_HEX: dict[str, list[str]] = {
    "aurora": ["#00ff9d", "#00b4d8", "#7b2ff7", "#00e6a8", "#0f9b8e"],
    "ocean": ["#001d3d", "#00507a", "#0096c7", "#48cae4", "#00507a"],
    "sunset": ["#3a0ca3", "#d53369", "#ff6a3d", "#ffb347", "#d53369"],
    "forest": ["#0b3d0b", "#2e8b57", "#9acd32", "#556b2f", "#1e5631"],
    "lava": ["#3a0000", "#ff2200", "#ff7700", "#ffc300", "#ff2200"],
    "party": ["#ff0055", "#ffcc00", "#00ff88", "#00aaff", "#aa00ff"],
    "pastel": ["#ffb3c6", "#ffd6a5", "#fdffb6", "#caffbf", "#9bf6ff", "#bdb2ff"],
    "neon": ["#ff00a0", "#7000ff", "#00e5ff", "#7000ff"],
    "ember": ["#5c1a00", "#ff5e00", "#ff9e2c", "#c63d00"],
}
PALETTES: dict[str, list[RGB]] = {name: _stops(*hexes) for name, hexes in PALETTE_HEX.items()}

FIRE = _stops("#000000", "#3d0000", "#a80000", "#ff3c00", "#ff8c1a", "#ffd27a")
SUNRISE = _stops("#000000", "#2b0300", "#7a1200", "#ff3d00", "#ff8a1f", "#ffc48a", "#fff2dc")
