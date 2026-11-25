"""Built-in ambiances and the WiZ bulb's native scenes."""

from __future__ import annotations

# WiZ native scenes (sceneId -> name). The color is only used for the UI swatch and the
# live preview; the bulb renders the real scene itself. "dynamic" scenes accept a speed.
WIZ_SCENES = [
    {"id": 1, "name": "Ocean", "color": "#1e6fd9", "dynamic": True},
    {"id": 2, "name": "Romance", "color": "#e0457b", "dynamic": True},
    {"id": 3, "name": "Sunset", "color": "#ff7b39", "dynamic": True},
    {"id": 4, "name": "Party", "color": "#b44cff", "dynamic": True},
    {"id": 5, "name": "Fireplace", "color": "#ff6a1a", "dynamic": True},
    {"id": 6, "name": "Cozy", "color": "#ffae5c", "dynamic": True},
    {"id": 7, "name": "Forest", "color": "#3fae5a", "dynamic": True},
    {"id": 8, "name": "Pastel colors", "color": "#c9a7ff", "dynamic": True},
    {"id": 9, "name": "Wake up", "color": "#ffc27a", "dynamic": False},
    {"id": 10, "name": "Bedtime", "color": "#ff9a50", "dynamic": False},
    {"id": 11, "name": "Warm white", "color": "#ffd6a1", "dynamic": False},
    {"id": 12, "name": "Daylight", "color": "#fff1e0", "dynamic": False},
    {"id": 13, "name": "Cool white", "color": "#e8f0ff", "dynamic": False},
    {"id": 14, "name": "Night light", "color": "#ff8f3a", "dynamic": False},
    {"id": 15, "name": "Focus", "color": "#f4f7ff", "dynamic": False},
    {"id": 16, "name": "Relax", "color": "#ffc98f", "dynamic": False},
    {"id": 17, "name": "True colors", "color": "#fff7ee", "dynamic": False},
    {"id": 18, "name": "TV time", "color": "#9fb4ff", "dynamic": False},
    {"id": 19, "name": "Plant growth", "color": "#d36bff", "dynamic": False},
    {"id": 20, "name": "Spring", "color": "#8fe388", "dynamic": True},
    {"id": 21, "name": "Summer", "color": "#ffd84a", "dynamic": True},
    {"id": 22, "name": "Fall", "color": "#e0782f", "dynamic": True},
    {"id": 23, "name": "Deep dive", "color": "#1d4ed8", "dynamic": True},
    {"id": 24, "name": "Jungle", "color": "#2fae4f", "dynamic": True},
    {"id": 25, "name": "Mojito", "color": "#6fe3a1", "dynamic": True},
    {"id": 26, "name": "Club", "color": "#ff3fa4", "dynamic": True},
    {"id": 27, "name": "Christmas", "color": "#e63946", "dynamic": True},
    {"id": 28, "name": "Halloween", "color": "#ff7a00", "dynamic": True},
    {"id": 29, "name": "Candlelight", "color": "#ff9d3b", "dynamic": True},
    {"id": 30, "name": "Golden white", "color": "#ffcf8a", "dynamic": True},
    {"id": 31, "name": "Pulse", "color": "#7c83ff", "dynamic": True},
    {"id": 32, "name": "Steampunk", "color": "#c27c3a", "dynamic": True},
]
WIZ_SCENE_IDS = {s["id"] for s in WIZ_SCENES}
WIZ_SCENE_COLOR = {s["id"]: s["color"] for s in WIZ_SCENES}


def _strip(effect, brightness, color="#ffffff", color2="#000000", speed=35, **params):
    return {"power": True, "effect": effect, "color": color, "color2": color2,
            "brightness": brightness, "speed": speed, "params": params}


def _bulb_white(temp, brightness):
    return {"power": True, "mode": "white", "temp": temp, "brightness": brightness}


def _bulb_color(color, brightness):
    return {"power": True, "mode": "color", "color": color, "brightness": brightness}


def _bulb_scene(scene, brightness, speed=100):
    return {"power": True, "mode": "scene", "scene": scene, "scene_speed": speed, "brightness": brightness}


def _bulb_effect(effect, brightness, color="#ffffff", speed=35, **params):
    return {"power": True, "mode": "effect", "effect": effect, "color": color,
            "brightness": brightness, "speed": speed, "params": params}


_BULB_OFF = {"power": False}

AMBIANCES = [
    {"id": "warm-evening", "name": "Warm evening", "subtitle": "Soft amber, relaxed",
     "swatch": ["#ff9a4d", "#ffcf8a"],
     "strip": _strip("solid", 45, "#ff9a4d"), "bulb": _bulb_white(2700, 45)},
    {"id": "reading", "name": "Reading", "subtitle": "Bright and easy on the eyes",
     "swatch": ["#ffd8a8", "#fff4e0"],
     "strip": _strip("solid", 55, "#ffd8a8"), "bulb": _bulb_white(4000, 100)},
    {"id": "focus", "name": "Focus", "subtitle": "Crisp daylight to work in",
     "swatch": ["#e6f0ff", "#ffffff"],
     "strip": _strip("solid", 70, "#e6f0ff"), "bulb": _bulb_white(5200, 100)},
    {"id": "movie", "name": "Movie night", "subtitle": "Deep blue bias light",
     "swatch": ["#1a2cff", "#5b2bff"],
     "strip": _strip("gradient", 30, "#1a2cff", "#5b2bff", speed=8), "bulb": _BULB_OFF},
    {"id": "candlelight", "name": "Candlelight", "subtitle": "Flickering warm flames",
     "swatch": ["#ff8a2a", "#ff5a00"],
     "strip": _strip("candle", 55, "#ff9a3c", speed=30, flicker=45), "bulb": _bulb_scene(29, 40, 60)},
    {"id": "fireplace", "name": "Fireplace", "subtitle": "A crackling fire glow",
     "swatch": ["#ff3c00", "#ffb000"],
     "strip": _strip("fire", 65, speed=45, intensity=70), "bulb": _bulb_scene(5, 55, 80)},
    {"id": "sunset", "name": "Sunset", "subtitle": "Purple to gold, slowly drifting",
     "swatch": ["#3a0ca3", "#d53369", "#ffb347"],
     "strip": _strip("flow", 60, speed=12, palette="sunset", repeat=1), "bulb": _bulb_scene(3, 60, 40)},
    {"id": "ocean", "name": "Ocean", "subtitle": "Rolling blues and teals",
     "swatch": ["#00507a", "#0096c7", "#48cae4"],
     "strip": _strip("flow", 55, speed=18, palette="ocean", repeat=1.5), "bulb": _bulb_scene(1, 55, 60)},
    {"id": "aurora", "name": "Aurora", "subtitle": "Northern lights overhead",
     "swatch": ["#00ff9d", "#00b4d8", "#7b2ff7"],
     "strip": _strip("flow", 60, speed=14, palette="aurora", repeat=1),
     "bulb": _bulb_effect("flow", 50, speed=14, palette="aurora", repeat=1)},
    {"id": "forest", "name": "Forest", "subtitle": "Calm greens",
     "swatch": ["#0b3d0b", "#2e8b57", "#9acd32"],
     "strip": _strip("flow", 50, speed=10, palette="forest", repeat=1), "bulb": _bulb_scene(7, 50, 50)},
    {"id": "romance", "name": "Romance", "subtitle": "Slow pink breathing",
     "swatch": ["#ff2d6f", "#b5179e"],
     "strip": _strip("breathe", 45, "#ff2d6f", speed=12, depth=55), "bulb": _bulb_scene(2, 40, 50)},
    {"id": "chill", "name": "Chill", "subtitle": "Violet and teal",
     "swatch": ["#7b5cff", "#00c2c7"],
     "strip": _strip("wave", 45, "#7b5cff", "#00c2c7", speed=10, waves=2), "bulb": _bulb_color("#7b5cff", 35)},
    {"id": "neon", "name": "Neon", "subtitle": "Synthwave pink and cyan",
     "swatch": ["#ff00a0", "#7000ff", "#00e5ff"],
     "strip": _strip("flow", 70, speed=25, palette="neon", repeat=2), "bulb": _bulb_color("#ff00a0", 60)},
    {"id": "party", "name": "Party", "subtitle": "Fast rainbow and color pops",
     "swatch": ["#ff0055", "#ffcc00", "#00ff88", "#00aaff"],
     "strip": _strip("rainbow", 80, speed=60, repeat=1, saturation=100), "bulb": _bulb_scene(4, 100, 150)},
    {"id": "starry", "name": "Starry night", "subtitle": "Twinkling stars on deep blue",
     "swatch": ["#0a1a4a", "#e8eeff"],
     "strip": _strip("twinkle", 55, "#0a1a4a", "#e8eeff", speed=20, density=30, base=25),
     "bulb": _bulb_color("#2235a8", 15)},
    {"id": "night-light", "name": "Night light", "subtitle": "Barely there, for sleeping",
     "swatch": ["#ff5a1f", "#3d1200"],
     "strip": _strip("solid", 8, "#ff5a1f"), "bulb": _BULB_OFF},
    {"id": "wake-up", "name": "Wake up", "subtitle": "15 minute sunrise",
     "swatch": ["#7a1200", "#ff8a1f", "#fff2dc"],
     "strip": _strip("sunrise", 100, minutes=15), "bulb": _bulb_effect("sunrise", 100, minutes=15)},
]
AMBIANCE_BY_ID = {a["id"]: a for a in AMBIANCES}
