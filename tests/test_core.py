"""Unit tests. Run with:  python3 -m unittest discover -s tests"""

from __future__ import annotations

import struct
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ambiance.catalog import AMBIANCES, WIZ_SCENE_IDS  # noqa: E402
from ambiance.color import hex_to_rgb, kelvin_to_rgb, rgb_to_hex  # noqa: E402
from ambiance.devices import StripOutput, rgb_to_wiz  # noqa: E402
from ambiance.effects import EFFECTS, Ctx, speed_to_rate  # noqa: E402
from ambiance.engine import (Engine, ValidationError, clean_bulb_patch, clean_strip_patch,  # noqa: E402
                             pilot_to_bulb)


class PacketTests(unittest.TestCase):
    def test_matches_esp32_receiver_format(self):
        rgb = bytes([1, 2, 3]) * 107
        pkt = StripOutput.build_packet(seq=0x1234, rgb=rgb, t_us=0xDEADBEEF)
        magic, ver, count, seq, t_us = struct.unpack_from("<HBBHI", pkt)
        self.assertEqual((magic, ver, count, seq, t_us), (0xA1B2, 2, 107, 0x1234, 0xDEADBEEF))
        self.assertEqual(len(pkt), 10 + 3 * 107)  # HDR_LEN + 3 * nleds, as the receiver checks
        self.assertEqual(pkt[10:13], bytes([1, 2, 3]))

    def test_sequence_and_time_wrap(self):
        pkt = StripOutput.build_packet(seq=0x1FFFF, rgb=bytes(3), t_us=2**40 + 5)
        _, _, _, seq, t_us = struct.unpack_from("<HBBHI", pkt)
        self.assertEqual(seq, 0xFFFF)
        self.assertEqual(t_us, 5)


class EffectTests(unittest.TestCase):
    def _run(self, cls, n, params, frames=40, speed=50):
        fx = cls(n, seed=1)
        clean = cls.clean_params(params)
        rate = speed_to_rate(speed)
        phase = t = 0.0
        for _ in range(frames):
            dt = 1 / 30
            phase += dt * rate
            t += dt
            out = fx.render(Ctx(n, phase, rate, t, dt, (255, 120, 40), (20, 60, 255), clean))
            self.assertEqual(len(out), n, cls.id)
            for c in out:
                self.assertEqual(len(c), 3)
                for v in c:
                    self.assertGreaterEqual(v, -1e-6, cls.id)
                    self.assertLessEqual(v, 255 + 1e-6, cls.id)

    def test_every_effect_on_strip_and_bulb_sizes(self):
        for cls in EFFECTS.values():
            for n in (1, 107, 120):
                self._run(cls, n, {})

    def test_param_extremes(self):
        for cls in EFFECTS.values():
            for p in cls.params:
                values = p["options"] if p["type"] == "select" else [p["min"], p["max"]]
                for v in values:
                    for speed in (1, 100):
                        self._run(cls, 107, {p["key"]: v}, frames=10, speed=speed)

    def test_clean_params_clamps_and_defaults(self):
        flow = EFFECTS["flow"]
        self.assertEqual(flow.clean_params({"palette": "nope", "repeat": 99})["palette"], "aurora")
        self.assertEqual(flow.clean_params({"repeat": 99})["repeat"], 3)
        self.assertEqual(flow.clean_params({"repeat": "x"})["repeat"], 1)

    def test_sunrise_starts_dark_and_ends_bright(self):
        cls = EFFECTS["sunrise"]
        fx = cls(1)
        params = cls.clean_params({"minutes": 1})
        start = fx.render(Ctx(1, 0, 0, 0.0, 0, (0, 0, 0), (0, 0, 0), params))[0]
        end = fx.render(Ctx(1, 0, 0, 61.0, 0, (0, 0, 0), (0, 0, 0), params))[0]
        self.assertLess(max(start), 10)
        self.assertGreater(min(end), 200)


class ColorTests(unittest.TestCase):
    def test_hex_round_trip(self):
        self.assertEqual(rgb_to_hex(hex_to_rgb("#FF8800")), "#ff8800")
        self.assertEqual(rgb_to_hex(hex_to_rgb("abc")), "#aabbcc")

    def test_kelvin_is_warm_to_cool(self):
        warm, cool = kelvin_to_rgb(2200), kelvin_to_rgb(6500)
        self.assertGreater(warm[0] - warm[2], cool[0] - cool[2])


class WizTests(unittest.TestCase):
    def test_rgb_to_wiz_normalizes_brightness(self):
        p = rgb_to_wiz((127.5, 63.75, 0))
        self.assertEqual((p["r"], p["g"], p["b"], p["dimming"]), (255, 128, 0, 50))
        self.assertEqual(rgb_to_wiz((1, 1, 1)), {"state": False})
        self.assertEqual(rgb_to_wiz(None), {"state": False})
        self.assertEqual(rgb_to_wiz((10, 0, 0))["dimming"], 10)  # WiZ minimum

    def test_pilot_mapping(self):
        self.assertEqual(pilot_to_bulb({"state": True, "sceneId": 0, "temp": 2700, "dimming": 17}),
                         {"power": True, "brightness": 17, "mode": "white", "temp": 2700})
        self.assertEqual(pilot_to_bulb({"state": True, "sceneId": 0, "r": 255, "g": 0, "b": 0, "dimming": 100}),
                         {"power": True, "brightness": 100, "mode": "color", "color": "#ff0000"})
        m = pilot_to_bulb({"state": True, "sceneId": 5, "speed": 80, "dimming": 50})
        self.assertEqual((m["mode"], m["scene"], m["scene_speed"]), ("scene", 5, 80))


class ValidationTests(unittest.TestCase):
    def test_strip_patch(self):
        self.assertEqual(clean_strip_patch({"brightness": 500, "color": "FFF"}), {"brightness": 100, "color": "#ffffff"})
        for bad in ({"effect": "nope"}, {"color": "red"}, {"power": "yes"}, {"brightness": "x"}, {"hack": 1}):
            with self.assertRaises(ValidationError):
                clean_strip_patch(bad)

    def test_bulb_patch(self):
        self.assertEqual(clean_bulb_patch({"temp": 100, "brightness": 1}), {"temp": 2200, "brightness": 10})
        for bad in ({"mode": "disco"}, {"scene": 99}, {"effect": "comet"}):
            with self.assertRaises(ValidationError):
                clean_bulb_patch(bad)

    def test_unknown_params_are_dropped(self):
        self.assertEqual(clean_strip_patch({"params": {"palette": "ocean", "evil": 1}}), {"params": {"palette": "ocean"}})

    def test_every_builtin_ambiance_is_valid(self):
        ids = set()
        for a in AMBIANCES:
            self.assertNotIn(a["id"], ids)
            ids.add(a["id"])
            clean_strip_patch(dict(a["strip"]))
            bulb = clean_bulb_patch(dict(a["bulb"]))
            if bulb.get("mode") == "scene":
                self.assertIn(bulb["scene"], WIZ_SCENE_IDS)
            if bulb.get("mode") == "effect":
                self.assertTrue(EFFECTS[bulb["effect"]].bulb)
            self.assertTrue(a["swatch"])


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = Engine(Path(self.tmp.name), dry_run=True)

    def tearDown(self):
        self.engine.stop() if self.engine._thread.is_alive() else self.engine.bulb.close()
        self.tmp.cleanup()

    def test_ambiance_save_apply_delete(self):
        e = self.engine
        e.apply_ambiance("ocean")
        snap = e.snapshot()
        self.assertEqual(snap["ambiance"], "ocean")
        self.assertTrue(snap["strip"]["power"])
        self.assertEqual(snap["strip"]["params"]["palette"], "ocean")
        saved = e.save_ambiance("My blue")
        e.update_strip({"effect": "solid"})
        e.apply_ambiance(saved["id"])
        self.assertEqual(e.snapshot()["strip"]["effect"], "flow")
        e.delete_ambiance(saved["id"])
        self.assertEqual(e.snapshot()["custom_ambiances"], [])
        with self.assertRaises(ValidationError):
            e.apply_ambiance(saved["id"])

    def test_state_persists(self):
        self.engine.update_strip({"color": "#123456", "effect": "wave"})
        self.engine._save_state()
        again = Engine(Path(self.tmp.name), dry_run=True)
        try:
            self.assertEqual(again.snapshot()["strip"]["color"], "#123456")
            self.assertEqual(again.snapshot()["strip"]["effect"], "wave")
        finally:
            again.bulb.close()

    def test_settings_validation(self):
        with self.assertRaises(ValidationError):
            self.engine.update_settings({"bulb_ip": "not-an-ip"})
        with self.assertRaises(ValidationError):
            self.engine.update_settings({"led_count": 60})  # fixed by the hardware
        self.engine.update_settings({"fps": 5, "max_brightness": 500})
        self.assertEqual(self.engine.cfg["fps"], 10)
        self.assertEqual(self.engine.cfg["max_brightness"], 100)
        self.assertEqual(self.engine.cfg["led_count"], 107)

    def test_render_loop_sends_frames_and_fades(self):
        e = self.engine
        e.update_strip({"power": True, "effect": "rainbow", "brightness": 80})
        e.start()
        time.sleep(0.6)
        self.assertGreater(e.strip_out.frames_sent, 5)
        e.set_power(False)
        time.sleep(0.8)
        self.assertEqual(e._level, 0.0)
        self.assertFalse(e.snapshot()["status"]["strip"]["active"])

    def test_unsync_keeps_the_color_the_bulb_was_showing(self):
        e = self.engine
        e.update_bulb({"power": False})            # bulb was off before syncing
        e.set_link(True)
        self.assertTrue(e.snapshot()["bulb"]["power"])
        e._bulb_display = (128.0, 32.0, 64.0)      # what the bulb was mirroring
        e.set_link(False)
        b = e.snapshot()["bulb"]
        self.assertEqual((b["power"], b["mode"], b["color"], b["brightness"]), (True, "color", "#ff4080", 50))
        self.assertTrue(e._bulb_dirty)              # and that state gets sent to the bulb

    def test_unsync_with_dark_strip_turns_bulb_off(self):
        e = self.engine
        e.set_link(True)
        e._bulb_display = None
        e.set_link(False)
        self.assertFalse(e.snapshot()["bulb"]["power"])

    def test_link_off_when_already_off_changes_nothing(self):
        e = self.engine
        e.update_bulb({"power": True, "mode": "white", "temp": 3000})
        e._bulb_display = (10.0, 200.0, 10.0)
        e.set_link(False)
        b = e.snapshot()["bulb"]
        self.assertEqual((b["mode"], b["temp"]), ("white", 3000))

    def test_strip_output_moves_off_loopback_once_wifi_is_up(self):
        """Boot race: the service starts before WiFi, so it first sees only 127.0.0.1."""
        from unittest import mock
        e = self.engine
        e.local_ip = "127.0.0.1"
        e.strip_out.set_interface("127.0.0.1")
        with mock.patch("ambiance.engine.detect_local_ip", return_value="127.0.0.1"):
            self.assertFalse(e.refresh_network())       # still no network: stay put
        real_ip = __import__("ambiance.devices", fromlist=["x"]).detect_local_ip("192.168.0.1")
        if real_ip == "127.0.0.1":
            self.skipTest("this machine has no LAN address to move to")
        with mock.patch("ambiance.engine.detect_local_ip", return_value=real_ip):
            self.assertTrue(e.refresh_network())
            self.assertFalse(e.refresh_network())       # no change the second time
        self.assertEqual(e.local_ip, real_ip)
        self.assertEqual(e.strip_out.local_ip, real_ip)
        self.assertEqual(e.monitor.local_ip, real_ip)
        self.assertEqual(e.snapshot()["server"]["local_ip"], real_ip)

    def test_timer_turns_everything_off(self):
        e = self.engine
        e.update_strip({"power": True})
        e.timer_end = time.time() - 1
        e._tick(time.monotonic(), 0.03)
        snap = e.snapshot()
        self.assertFalse(snap["strip"]["power"])
        self.assertFalse(snap["bulb"]["power"])
        self.assertIsNone(snap["timer_remaining"])


if __name__ == "__main__":
    unittest.main()
