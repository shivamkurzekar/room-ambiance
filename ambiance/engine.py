"""The engine owns the room state, renders effects and drives both devices.

HTTP handlers call the public methods (thread safe). A single render thread runs at
the configured frame rate, sends frames to the strip and commands to the bulb, and
pushes state and preview frames to connected browsers.
"""

from __future__ import annotations

import copy
import ipaddress
import json
import logging
import queue
import threading
import time
import uuid
from pathlib import Path

from . import __version__
from .catalog import AMBIANCE_BY_ID, AMBIANCES, WIZ_SCENE_COLOR, WIZ_SCENE_IDS, WIZ_SCENES
from .color import (PALETTE_HEX, average, clamp, hex_to_rgb, is_hex_color, kelvin_to_rgb,
                    normalize_hex, rgb_to_hex, scale)
from .config import MAX_LEDS, load_config, save_json
from .devices import (AmbilightMonitor, StripOutput, WizBulb, detect_local_ip, discover_bulbs,
                      rgb_to_wiz)
from .effects import EFFECTS, Ctx, get_effect, speed_to_rate
from .effects import catalog as effect_catalog

log = logging.getLogger("ambiance.engine")

STRIP_DEFAULT = {"power": False, "effect": "solid", "color": "#ff9a4d", "color2": "#5b6cff",
                 "brightness": 60, "speed": 35, "params": {}}
BULB_DEFAULT = {"power": True, "mode": "white", "color": "#ff9a4d", "temp": 2700, "brightness": 60,
                "scene": 11, "scene_speed": 100, "effect": "breathe", "speed": 35, "params": {}}
BULB_MODES = ("color", "white", "scene", "effect")
PARAM_KEYS = {p["key"] for cls in EFFECTS.values() for p in cls.params}
PUBLIC_SETTINGS = ("bulb_ip", "led_count", "fps", "max_brightness", "layout",
                   "multicast_group", "strip_port", "http_port")
MAX_CUSTOM = 60
PREVIEW_FPS = 15
FADE_SECONDS = 0.45


class ValidationError(ValueError):
    """Raised for bad API input; the message is shown to the user."""


# --------------------------------------------------------------------------- validation
def _num(v, lo, hi, name, integer=True):
    if isinstance(v, bool):
        raise ValidationError(f"{name} must be a number")
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise ValidationError(f"{name} must be a number") from None
    x = clamp(x, lo, hi)
    return int(round(x)) if integer else x


def _bool(v, name):
    if isinstance(v, bool):
        return v
    raise ValidationError(f"{name} must be true or false")


def _color(v, name):
    if not is_hex_color(v):
        raise ValidationError(f"{name} must be a hex color like #ff8800")
    return normalize_hex(v)


def _params(v):
    if not isinstance(v, dict):
        raise ValidationError("params must be an object")
    out = {}
    for k, val in v.items():
        if k not in PARAM_KEYS:
            continue
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            out[k] = float(val)
        elif isinstance(val, str) and len(val) <= 40:
            out[k] = val
    return out


def clean_strip_patch(patch: dict) -> dict:
    out = {}
    for k, v in patch.items():
        if k == "power":
            out[k] = _bool(v, k)
        elif k in ("color", "color2"):
            out[k] = _color(v, k)
        elif k in ("brightness", "speed"):
            out[k] = _num(v, 1, 100, k)
        elif k == "effect":
            if v not in EFFECTS:
                raise ValidationError(f"unknown effect {v!r}")
            out[k] = v
        elif k == "params":
            out[k] = _params(v)
        else:
            raise ValidationError(f"unknown strip setting {k!r}")
    return out


def clean_bulb_patch(patch: dict) -> dict:
    out = {}
    for k, v in patch.items():
        if k == "power":
            out[k] = _bool(v, k)
        elif k == "mode":
            if v not in BULB_MODES:
                raise ValidationError(f"mode must be one of {', '.join(BULB_MODES)}")
            out[k] = v
        elif k == "color":
            out[k] = _color(v, k)
        elif k == "temp":
            out[k] = _num(v, 2200, 6500, k)
        elif k == "brightness":
            out[k] = _num(v, 10, 100, k)
        elif k == "scene":
            s = _num(v, -1e9, 1e9, k)
            if s not in WIZ_SCENE_IDS:
                raise ValidationError(f"unknown scene {v!r}")
            out[k] = s
        elif k == "scene_speed":
            out[k] = _num(v, 10, 200, k)
        elif k == "speed":
            out[k] = _num(v, 1, 100, k)
        elif k == "effect":
            if v not in EFFECTS or not EFFECTS[v].bulb:
                raise ValidationError(f"effect {v!r} is not available for the bulb")
            out[k] = v
        elif k == "params":
            out[k] = _params(v)
        else:
            raise ValidationError(f"unknown bulb setting {k!r}")
    return out


def _apply(target: dict, clean: dict) -> None:
    params = clean.pop("params", None)
    target.update(clean)
    if params is not None:
        target["params"] = {**target.get("params", {}), **params}


def pilot_to_bulb(res: dict) -> dict:
    """Translate a WiZ getPilot result into our bulb state fields."""
    out: dict = {"power": bool(res.get("state"))}
    if isinstance(res.get("dimming"), (int, float)):
        out["brightness"] = int(clamp(res["dimming"], 10, 100))
    sid = res.get("sceneId") or 0
    if sid in WIZ_SCENE_IDS:
        out["mode"], out["scene"] = "scene", int(sid)
        if isinstance(res.get("speed"), (int, float)):
            out["scene_speed"] = int(clamp(res["speed"], 10, 200))
    elif isinstance(res.get("temp"), (int, float)):
        out["mode"], out["temp"] = "white", int(clamp(res["temp"], 2200, 6500))
    elif all(isinstance(res.get(k), (int, float)) for k in ("r", "g", "b")):
        if res["r"] or res["g"] or res["b"]:
            out["mode"], out["color"] = "color", rgb_to_hex((res["r"], res["g"], res["b"]))
        else:
            out["mode"] = "white"
    return out


def swatch_for(strip: dict) -> list[str]:
    cls = get_effect(strip["effect"])
    params = cls.clean_params(strip.get("params"))
    if strip["effect"] == "flow":
        return PALETTE_HEX[params["palette"]][:4]
    if strip["effect"] in ("rainbow", "spectrum"):
        return ["#ff0055", "#ffcc00", "#00ff88", "#00aaff", "#aa00ff"]
    if strip["effect"] == "fire":
        return ["#a80000", "#ff3c00", "#ffb000"]
    if strip["effect"] == "sunrise":
        return ["#7a1200", "#ff8a1f", "#fff2dc"]
    return [strip["color"], strip["color2"]] if cls.colors == 2 else [strip["color"]]


# --------------------------------------------------------------------------- engine
class Engine:
    def __init__(self, data_dir: Path, dry_run: bool = False):
        self.data_dir = Path(data_dir)
        self.dry_run = dry_run
        self.config_path = self.data_dir / "config.json"
        self.state_path = self.data_dir / "state.json"
        self.cfg = load_config(self.config_path)
        self.lock = threading.RLock()
        self.state = self._load_state()
        self.timer_end: float | None = None

        self.local_ip = self.cfg["interface_ip"] or detect_local_ip(self.cfg["bulb_ip"])
        self.strip_out = StripOutput(self.cfg["multicast_group"], self.cfg["strip_port"],
                                     self.local_ip, dry_run=dry_run)
        self.monitor = AmbilightMonitor(self.cfg["multicast_group"], self.cfg["strip_port"], self.local_ip)
        self.bulb = WizBulb(self.cfg["bulb_ip"], self.cfg["bulb_port"], self.cfg["bulb_stream_hz"],
                            self.cfg["bulb_poll_seconds"], on_pilot=self._on_bulb_pilot, dry_run=dry_run)

        self._subs: list[queue.Queue] = []
        self._subs_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="engine", daemon=True)

        self._fx: dict = {"strip": None, "bulb": None}
        self._fx_key: dict = {"strip": None, "bulb": None}
        self._fx_start = {"strip": 0.0, "bulb": 0.0}
        self._phase = {"strip": 0.0, "bulb": 0.0}
        self._level = 1.0 if self.state["strip"]["power"] else 0.0
        self._off_frames = 99            # never blank the strip just because we started
        self._last_wire: bytes | None = None
        self._bulb_dirty = False         # static bulb state needs sending
        self._bulb_resend_at: float | None = None
        self._bulb_display = None
        self._save_at: float | None = None
        self._sent_window = (0, time.monotonic(), 0.0)  # frames, window start, fps

    # ---------------------------------------------------------------- lifecycle
    def start(self) -> None:
        self.bulb.start()
        self.monitor.start()
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)
        self._save_state()
        self.bulb.close()
        self.monitor.close()
        self.strip_out.close()

    def _load_state(self) -> dict:
        state = {"strip": copy.deepcopy(STRIP_DEFAULT), "bulb": copy.deepcopy(BULB_DEFAULT),
                 "link": False, "ambiance": None, "custom_ambiances": []}
        try:
            stored = json.loads(self.state_path.read_text())
        except (OSError, ValueError):
            return state
        for key, cleaner in (("strip", clean_strip_patch), ("bulb", clean_bulb_patch)):
            try:
                _apply(state[key], cleaner(dict(stored.get(key, {}))))
            except ValidationError as e:
                log.warning("ignoring stored %s state: %s", key, e)
        state["link"] = bool(stored.get("link", False))
        state["ambiance"] = stored.get("ambiance") if isinstance(stored.get("ambiance"), str) else None
        customs = stored.get("custom_ambiances", [])
        if isinstance(customs, list):
            state["custom_ambiances"] = [c for c in customs if isinstance(c, dict) and "id" in c][:MAX_CUSTOM]
        return state

    def _save_state(self) -> None:
        with self.lock:
            data = copy.deepcopy(self.state)
        try:
            save_json(self.state_path, data)
        except OSError as e:
            log.warning("could not save state: %s", e)

    # ---------------------------------------------------------------- pub/sub
    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=64)
        with self._subs_lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._subs_lock:
            if q in self._subs:
                self._subs.remove(q)

    def _publish(self, event: str, data: str) -> None:
        with self._subs_lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait((event, data))
            except queue.Full:
                pass

    def _changed(self) -> None:
        self._save_at = time.monotonic() + 1.0
        self._publish("state", json.dumps(self.snapshot()))

    # ---------------------------------------------------------------- read
    def catalog(self) -> dict:
        return {"version": __version__, "effects": effect_catalog(), "palettes": PALETTE_HEX,
                "wiz_scenes": WIZ_SCENES, "ambiances": AMBIANCES, "max_leds": MAX_LEDS}

    def snapshot(self) -> dict:
        with self.lock:
            s = copy.deepcopy(self.state)
            remaining = None if self.timer_end is None else max(0.0, self.timer_end - time.time())
            s["timer_remaining"] = None if remaining is None else round(remaining)
            s["settings"] = {k: self.cfg[k] for k in PUBLIC_SETTINGS}
            s["status"] = {
                "strip": {"active": self._level > 0, "fps": round(self._sent_window[2], 1),
                          "frames_sent": self.strip_out.frames_sent, "error": self.strip_out.last_error},
                "bulb": self.bulb.status(),
                "ambilight": self.monitor.status(),
            }
            s["server"] = {"version": __version__, "local_ip": self.local_ip, "dry_run": self.dry_run,
                           "url": f"http://{self.local_ip}:{self.cfg['http_port']}"}
        return s

    # ---------------------------------------------------------------- mutations
    def update_strip(self, patch: dict) -> None:
        clean = clean_strip_patch(patch)
        with self.lock:
            _apply(self.state["strip"], clean)
            self.state["ambiance"] = None
            self._changed()

    def update_bulb(self, patch: dict) -> None:
        clean = clean_bulb_patch(patch)
        with self.lock:
            _apply(self.state["bulb"], clean)
            self.state["ambiance"] = None
            self._bulb_dirty = True
            self._changed()

    def set_link(self, on: bool) -> None:
        """Sync the bulb to the strip, or hand it back.

        Turning sync off keeps the bulb showing the color it had while synced, instead of
        jumping back to an older (possibly "off") state, which felt like the switch worked
        backwards. Turning sync on marks the bulb as on for the same reason.
        """
        on = _bool(on, "link")
        with self.lock:
            bulb = self.state["bulb"]
            if on:
                bulb["power"] = True
            elif self.state["link"]:
                shown = self._bulb_display
                if shown is not None and max(shown) >= 4:
                    wiz = rgb_to_wiz(shown)
                    bulb.update({"power": True, "mode": "color", "brightness": wiz["dimming"],
                                 "color": rgb_to_hex((wiz["r"], wiz["g"], wiz["b"]))})
                else:
                    bulb["power"] = False   # the strip was off or dark, so the bulb was too
            self.state["link"] = on
            self.state["ambiance"] = None
            self._bulb_dirty = True
            self._changed()

    def set_power(self, on: bool) -> None:
        on = _bool(on, "on")
        with self.lock:
            self.state["strip"]["power"] = on
            self.state["bulb"]["power"] = on
            if not on:
                self.timer_end = None
            self._bulb_dirty = True
            self._changed()

    def set_timer(self, minutes) -> None:
        minutes = _num(minutes, 0, 600, "minutes")
        with self.lock:
            self.timer_end = None if minutes == 0 else time.time() + minutes * 60
            self._changed()

    def apply_ambiance(self, ambiance_id: str) -> None:
        with self.lock:
            preset = AMBIANCE_BY_ID.get(ambiance_id) or next(
                (c for c in self.state["custom_ambiances"] if c.get("id") == ambiance_id), None)
            if preset is None:
                raise ValidationError("that ambiance no longer exists")
            strip, bulb = copy.deepcopy(self.state["strip"]), copy.deepcopy(self.state["bulb"])
            _apply(strip, clean_strip_patch(copy.deepcopy(preset.get("strip", {}))))
            _apply(bulb, clean_bulb_patch(copy.deepcopy(preset.get("bulb", {}))))
            self.state["strip"], self.state["bulb"] = strip, bulb
            self.state["link"] = bool(preset.get("link", False))
            self.state["ambiance"] = ambiance_id
            self._fx_key = {"strip": None, "bulb": None}   # restart effects together, in sync
            self._bulb_dirty = True
            self._changed()

    def save_ambiance(self, name) -> dict:
        if not isinstance(name, str) or not name.strip():
            raise ValidationError("give the ambiance a name")
        name = name.strip()[:40]
        with self.lock:
            if len(self.state["custom_ambiances"]) >= MAX_CUSTOM:
                raise ValidationError(f"you can save up to {MAX_CUSTOM} ambiances")
            entry = {"id": "custom-" + uuid.uuid4().hex[:8], "name": name, "subtitle": "Saved by you",
                     "swatch": swatch_for(self.state["strip"]), "custom": True,
                     "strip": copy.deepcopy(self.state["strip"]), "bulb": copy.deepcopy(self.state["bulb"]),
                     "link": self.state["link"]}
            self.state["custom_ambiances"].append(entry)
            self.state["ambiance"] = entry["id"]
            self._changed()
            return entry

    def delete_ambiance(self, ambiance_id: str) -> None:
        with self.lock:
            before = len(self.state["custom_ambiances"])
            self.state["custom_ambiances"] = [c for c in self.state["custom_ambiances"]
                                              if c.get("id") != ambiance_id]
            if len(self.state["custom_ambiances"]) == before:
                raise ValidationError("that ambiance no longer exists")
            if self.state["ambiance"] == ambiance_id:
                self.state["ambiance"] = None
            self._changed()

    def update_settings(self, patch: dict) -> None:
        clean = {}
        for k, v in patch.items():
            if k == "bulb_ip":
                try:
                    ip = ipaddress.IPv4Address(str(v).strip())
                except ValueError:
                    raise ValidationError("bulb IP must look like 192.168.0.101") from None
                clean[k] = str(ip)
            elif k == "fps":
                clean[k] = _num(v, 10, 60, k)
            elif k == "max_brightness":
                clean[k] = _num(v, 5, 100, k)
            else:
                raise ValidationError(f"{k!r} can't be changed here")
        with self.lock:
            self.cfg.update(clean)
            if "bulb_ip" in clean:
                self.bulb.set_target(clean["bulb_ip"])
                self._bulb_dirty = True
            try:
                save_json(self.config_path, {k: self.cfg[k] for k in self.cfg})
            except OSError as e:
                raise ValidationError(f"could not save settings: {e}") from None
            self._changed()

    def discover(self) -> list[dict]:
        return [] if self.dry_run else discover_bulbs(self.local_ip)

    # ---------------------------------------------------------------- bulb sync
    def _on_bulb_pilot(self, res: dict) -> None:
        """Adopt changes made outside this app (the WiZ app, a wall switch, a schedule)."""
        with self.lock:
            b = self.state["bulb"]
            animating = self.state["link"] or (b["power"] and b["mode"] == "effect")
            if animating or self._bulb_dirty or time.monotonic() - self.bulb.last_command < 5:
                return
            derived = pilot_to_bulb(res)
            if not derived.get("power", True):
                derived = {"power": False}
            changes = {k: v for k, v in derived.items() if b.get(k) != v}
            if not changes:
                return
            b.update(changes)
            self.state["ambiance"] = None
            self._changed()

    @staticmethod
    def _bulb_static_params(b: dict) -> dict:
        if not b["power"]:
            return {"state": False}
        dim = int(b["brightness"])
        if b["mode"] == "white":
            return {"state": True, "temp": int(b["temp"]), "dimming": dim}
        if b["mode"] == "scene":
            p = {"state": True, "sceneId": int(b["scene"]), "dimming": dim}
            if next((s["dynamic"] for s in WIZ_SCENES if s["id"] == b["scene"]), False):
                p["speed"] = int(b["scene_speed"])
            return p
        r, g, bl = hex_to_rgb(b["color"])
        if max(r, g, bl) < 1:
            return {"state": False}
        return {"state": True, "r": int(r), "g": int(g), "b": int(bl), "dimming": dim}

    @staticmethod
    def _bulb_static_display(b: dict):
        if not b["power"]:
            return None
        k = b["brightness"] / 100.0
        if b["mode"] == "white":
            return scale(kelvin_to_rgb(b["temp"]), k)
        if b["mode"] == "scene":
            return scale(hex_to_rgb(WIZ_SCENE_COLOR.get(b["scene"], "#ffffff")), k)
        return scale(hex_to_rgb(b["color"]), k)

    # ---------------------------------------------------------------- render loop
    def _run(self) -> None:
        last = time.monotonic()
        next_preview = 0.0
        while not self._stop.is_set():
            start = time.monotonic()
            dt = min(0.25, start - last)
            last = start
            try:
                display = self._tick(start, dt)
                if start >= next_preview:
                    next_preview = start + 1.0 / PREVIEW_FPS
                    self._push_preview(display)
                if self._save_at is not None and start >= self._save_at:
                    self._save_at = None
                    self._save_state()
            except Exception:
                log.exception("render tick failed")
            self._stop.wait(max(0.0, 1.0 / self.cfg["fps"] - (time.monotonic() - start)))

    def _render(self, kind: str, st: dict, n: int, dt: float, now: float):
        key = (st["effect"], n)
        if self._fx_key[kind] != key or self._fx[kind] is None:
            self._fx[kind] = get_effect(st["effect"])(n)
            self._fx_key[kind] = key
            self._fx_start[kind] = now
            self._phase[kind] = 0.0
        fx = self._fx[kind]
        rate = speed_to_rate(st["speed"])
        self._phase[kind] += dt * rate
        ctx = Ctx(n=n, phase=self._phase[kind], rate=rate, t=now - self._fx_start[kind], dt=dt,
                  c1=hex_to_rgb(st["color"]), c2=hex_to_rgb(st.get("color2", st["color"])),
                  params=type(fx).clean_params(st.get("params")))
        return fx.render(ctx)

    def _tick(self, now: float, dt: float):
        with self.lock:
            strip = copy.deepcopy(self.state["strip"])
            bulb = copy.deepcopy(self.state["bulb"])
            link = self.state["link"]
            n = int(self.cfg["led_count"])
            cap = self.cfg["max_brightness"] / 100.0
            timer_end = self.timer_end

        timer_k = 1.0
        if timer_end is not None:
            remaining = timer_end - time.time()
            if remaining <= 0:
                with self.lock:
                    self.timer_end = None
                    self.state["strip"]["power"] = False
                    self.state["bulb"]["power"] = False
                    self._bulb_dirty = True
                    self._changed()
                return None
            timer_k = clamp(remaining / 60.0, 0.0, 1.0)  # fade out over the final minute

        # ---- strip
        target = 1.0 if strip["power"] else 0.0
        step = dt / FADE_SECONDS
        self._level = min(target, self._level + step) if target > self._level else max(target, self._level - step)
        display = None
        if self._level > 0:
            frame = self._render("strip", strip, n, dt, now)
            p = strip["brightness"] / 100.0 * self._level * timer_k
            display = [scale(c, p) for c in frame]
            wk = p * p * cap   # perceptual curve: the eye sees p, the LEDs get p squared
            wire = bytes(int(clamp(v * wk + 0.5, 0, 255)) for c in frame for v in c)
            if wire != self._last_wire or now - self.strip_out.last_send > 0.5:
                self.strip_out.send(wire)
                self._count_frame(now)
                self._last_wire = wire
            self._off_frames = 0
        elif self._off_frames < 5:
            self.strip_out.send(bytes(n * 3))   # a few black frames, then go quiet
            self._off_frames += 1
            self._last_wire = None

        # ---- bulb
        if link:
            avg = average(display) if display else None
            self.bulb.stream(avg)
            self._bulb_display = avg
            self._bulb_dirty = False
        elif bulb["power"] and bulb["mode"] == "effect":
            c = scale(self._render("bulb", bulb, 1, dt, now)[0], bulb["brightness"] / 100.0 * timer_k)
            self.bulb.stream(c)
            self._bulb_display = c
            self._bulb_dirty = False
        else:
            if self._bulb_dirty:
                self._bulb_dirty = False
                self.bulb.set_pilot(self._bulb_static_params(bulb))
                self._bulb_resend_at = now + 1.2          # UDP has no delivery guarantee
            elif self._bulb_resend_at is not None and now >= self._bulb_resend_at:
                self._bulb_resend_at = None
                self.bulb.set_pilot(self._bulb_static_params(bulb))
            self._bulb_display = self._bulb_static_display(bulb)
        return display

    def _count_frame(self, now: float) -> None:
        frames, t0, fps = self._sent_window
        frames += 1
        if now - t0 >= 1.0:
            fps, frames, t0 = frames / (now - t0), 0, now
        self._sent_window = (frames, t0, fps)

    def _push_preview(self, display) -> None:
        with self._subs_lock:
            if not self._subs:
                return
        n = int(self.cfg["led_count"])
        strip_hex = "".join(rgb_to_hex(c)[1:] for c in display) if display else "000000" * n
        bulb_hex = rgb_to_hex(self._bulb_display) if self._bulb_display else ""
        self._publish("frame", json.dumps({"s": strip_hex, "b": bulb_hex}))
