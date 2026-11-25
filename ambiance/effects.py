"""Light effects.

Every effect renders a list of RGB colors (0..255 floats), one per pixel. The strip
uses as many pixels as it has LEDs; the bulb renders the same effects with n=1.
Brightness is applied later by the engine, so effects always work at full scale.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from .color import FIRE, PALETTE_HEX, PALETTES, SUNRISE, RGB, clamp, hsv, mix, ramp, scale

TAU = math.tau


@dataclass
class Ctx:
    n: int          # pixel count
    phase: float    # animation position in cycles, advanced according to the speed slider
    rate: float     # current cycles per second (derived from speed)
    t: float        # seconds since this effect instance started
    dt: float       # seconds since the previous frame
    c1: RGB         # primary color
    c2: RGB         # secondary color
    params: dict    # validated effect parameters


def speed_to_rate(speed: float) -> float:
    """Map the 1..100 speed slider to cycles per second (gentle at the low end)."""
    s = clamp(speed, 1, 100) / 100.0
    return 0.02 + 1.6 * s * s


class Effect:
    id = ""
    name = ""
    description = ""
    colors = 1        # user colors used: 0, 1 or 2
    animated = True   # False hides the speed control
    bulb = False      # also offered for the bulb
    params: tuple = ()

    def __init__(self, n: int, seed: int | None = None):
        self.n = n
        self.rng = random.Random(seed)

    def render(self, ctx: Ctx) -> list[RGB]:
        raise NotImplementedError

    @classmethod
    def describe(cls) -> dict:
        return {
            "id": cls.id,
            "name": cls.name,
            "description": cls.description,
            "colors": cls.colors,
            "animated": cls.animated,
            "bulb": cls.bulb,
            "params": [dict(p) for p in cls.params],
        }

    @classmethod
    def clean_params(cls, params: dict | None) -> dict:
        """Return a complete, validated parameter dict for this effect."""
        params = params or {}
        out = {}
        for p in cls.params:
            v = params.get(p["key"], p["default"])
            if p["type"] == "range":
                try:
                    v = float(v)
                except (TypeError, ValueError):
                    v = p["default"]
                v = clamp(v, p["min"], p["max"])
            elif p["type"] == "select":
                if v not in p["options"]:
                    v = p["default"]
            out[p["key"]] = v
        return out


def _range(key, label, lo, hi, default, step=1.0, unit=""):
    return {"key": key, "label": label, "type": "range", "min": lo, "max": hi,
            "step": step, "default": default, "unit": unit}


def _select(key, label, options, default):
    return {"key": key, "label": label, "type": "select", "options": list(options), "default": default}


EFFECTS: dict[str, type[Effect]] = {}


def register(cls: type[Effect]) -> type[Effect]:
    EFFECTS[cls.id] = cls
    return cls


@register
class Solid(Effect):
    id, name = "solid", "Solid"
    description = "One steady color."
    animated = False

    def render(self, ctx):
        return [ctx.c1] * ctx.n


@register
class Gradient(Effect):
    id, name = "gradient", "Gradient"
    description = "Two colors blended around the room, drifting slowly."
    colors = 2
    params = (_range("repeat", "Repeat", 1, 4, 1),)

    def render(self, ctx):
        rep = ctx.params["repeat"]
        out = []
        for i in range(ctx.n):
            x = i / ctx.n * rep + ctx.phase * 0.1
            out.append(mix(ctx.c1, ctx.c2, 0.5 - 0.5 * math.cos(TAU * x)))
        return out


@register
class Breathe(Effect):
    id, name = "breathe", "Breathe"
    description = "Slowly rises and falls like breathing."
    bulb = True
    params = (_range("depth", "Depth", 20, 100, 80, unit="%"),)

    def render(self, ctx):
        depth = ctx.params["depth"] / 100.0
        wave = 0.5 - 0.5 * math.cos(TAU * ctx.phase * 0.5)
        wave = wave * wave * (3 - 2 * wave)
        return [scale(ctx.c1, (1 - depth) + depth * wave)] * ctx.n


@register
class Rainbow(Effect):
    id, name = "rainbow", "Rainbow"
    description = "The full spectrum flowing around the strip."
    colors = 0
    params = (_range("repeat", "Repeat", 0.5, 4, 1, step=0.5),
              _range("saturation", "Saturation", 30, 100, 100, unit="%"))

    def render(self, ctx):
        rep = ctx.params["repeat"]
        sat = ctx.params["saturation"] / 100.0
        return [hsv(i / ctx.n * rep + ctx.phase * 0.2, sat) for i in range(ctx.n)]


@register
class Spectrum(Effect):
    id, name = "spectrum", "Color cycle"
    description = "Everything fades through the colors together."
    colors = 0
    bulb = True
    params = (_range("saturation", "Saturation", 30, 100, 100, unit="%"),)

    def render(self, ctx):
        return [hsv(ctx.phase * 0.1, ctx.params["saturation"] / 100.0)] * ctx.n


@register
class Wave(Effect):
    id, name = "wave", "Wave"
    description = "Waves of the second color rolling over the first."
    colors = 2
    params = (_range("waves", "Waves", 1, 10, 3),)

    def render(self, ctx):
        waves = ctx.params["waves"]
        out = []
        for i in range(ctx.n):
            v = 0.5 + 0.5 * math.sin(TAU * (i / ctx.n * waves - ctx.phase * 0.3))
            out.append(mix(ctx.c1, ctx.c2, v * v * (3 - 2 * v)))
        return out


@register
class Comet(Effect):
    id, name = "comet", "Comet"
    description = "Glowing comets chasing around the room."
    colors = 2
    params = (_range("tail", "Tail", 2, 60, 18),
              _range("count", "Comets", 1, 4, 1),
              _range("background", "Background", 0, 50, 6, unit="%"))

    def render(self, ctx):
        n = ctx.n
        tail = ctx.params["tail"]
        count = int(ctx.params["count"])
        bg = scale(ctx.c2, ctx.params["background"] / 100.0)
        level = [0.0] * n
        for k in range(count):
            head = ((ctx.phase * 0.25 + k / count) % 1.0) * n
            for i in range(n):
                d = (head - i) % n
                if d < tail:
                    level[i] = max(level[i], (1 - d / tail) ** 2)
        return [mix(bg, ctx.c1, lv) for lv in level]


@register
class Twinkle(Effect):
    id, name = "twinkle", "Twinkle"
    description = "Stars sparkling over a soft background."
    colors = 2
    params = (_range("density", "Density", 1, 100, 35, unit="%"),
              _range("base", "Background", 0, 60, 12, unit="%"))

    def __init__(self, n, seed=None):
        super().__init__(n, seed)
        self.level = [0.0] * n

    def render(self, ctx):
        spawn_per_led = (ctx.params["density"] / 100.0) * 0.8 * ctx.dt * (0.5 + ctx.rate)
        decay = math.exp(-ctx.dt * (1.2 + ctx.rate * 3.0))
        base = scale(ctx.c1, ctx.params["base"] / 100.0)
        out = []
        for i in range(ctx.n):
            lv = self.level[i] * decay
            if lv < 0.15 and self.rng.random() < spawn_per_led:
                lv = 0.6 + 0.4 * self.rng.random()
            self.level[i] = lv
            out.append(mix(base, ctx.c2, lv))
        return out


class _Flicker(Effect):
    """Per-pixel smooth random flicker, shared by candle and fire."""

    def __init__(self, n, seed=None):
        super().__init__(n, seed)
        self.cur = [0.8] * n
        self.target = [0.8] * n
        self.next_change = [0.0] * n

    def _step(self, ctx, spread: float) -> list[float]:
        follow = min(1.0, ctx.dt * (5.0 + ctx.rate * 12.0))
        for i in range(ctx.n):
            if ctx.t >= self.next_change[i]:
                dip = self.rng.random() < 0.06
                self.target[i] = (1 - spread * 1.6) if dip else 1 - spread * self.rng.random()
                self.next_change[i] = ctx.t + self.rng.uniform(0.05, 0.25) / (0.4 + ctx.rate)
            self.cur[i] += (self.target[i] - self.cur[i]) * follow
        return self.cur


@register
class Candle(_Flicker):
    id, name = "candle", "Candle"
    description = "Warm, gently flickering flame."
    bulb = True
    params = (_range("flicker", "Flicker", 0, 100, 45, unit="%"),)

    def render(self, ctx):
        spread = 0.5 * ctx.params["flicker"] / 100.0
        deep = (255.0, 70.0, 0.0)
        out = []
        for lv in self._step(ctx, spread):
            lv = clamp(lv, 0.15, 1.0)
            out.append(scale(mix(deep, ctx.c1, lv), lv))
        return out


@register
class Fire(_Flicker):
    id, name = "fire", "Fire"
    description = "A lively fireplace glow."
    colors = 0
    params = (_range("intensity", "Intensity", 20, 100, 70, unit="%"),)

    def render(self, ctx):
        heat_top = 0.45 + 0.55 * ctx.params["intensity"] / 100.0
        return [ramp(FIRE, clamp(lv, 0, 1) * heat_top, cyclic=False) for lv in self._step(ctx, 0.45)]


@register
class Flow(Effect):
    id, name = "flow", "Palette flow"
    description = "A color palette flowing slowly around the room."
    colors = 0
    bulb = True
    params = (_select("palette", "Palette", PALETTE_HEX.keys(), "aurora"),
              _range("repeat", "Spread", 0.25, 3, 1, step=0.25))

    def render(self, ctx):
        pal = PALETTES[ctx.params["palette"]]
        rep = ctx.params["repeat"]
        drift = ctx.phase * 0.25
        if ctx.n == 1:
            return [ramp(pal, drift, cyclic=True)]
        return [ramp(pal, i / ctx.n * rep + drift, cyclic=True) for i in range(ctx.n)]


@register
class Sunrise(Effect):
    id, name = "sunrise", "Sunrise"
    description = "Fades up from deep red to bright warm white."
    colors = 0
    animated = False
    bulb = True
    params = (_range("minutes", "Duration", 1, 60, 15, unit=" min"),)

    def render(self, ctx):
        p = clamp(ctx.t / (ctx.params["minutes"] * 60.0), 0.0, 1.0)
        return [scale(ramp(SUNRISE, p, cyclic=False), 0.03 + 0.97 * p ** 1.4)] * ctx.n


def get_effect(effect_id: str) -> type[Effect]:
    return EFFECTS.get(effect_id, Solid)


def catalog() -> list[dict]:
    return [cls.describe() for cls in EFFECTS.values()]
