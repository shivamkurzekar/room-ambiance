# Room Ambiance

A local web app to control the room's lighting: the WS2812B LED strip (driven by the
ESP32 from the ambilight build) and the WiZ smart bulb. Pick an ambiance, run effects,
set colors and brightness, sync the bulb to the strip, or set a sleep timer, from this
Mac or from your phone.

Everything stays on your home network. Nothing goes through the cloud, and the ESP32
firmware doesn't need any changes: it's the receiver from
[ambilight](https://github.com/shivamkurzekar/ambilight), and this app speaks the same packet format.

```
Browser (Mac or phone) ──HTTP──► server.py on this Mac ──UDP multicast 239.1.1.1:7777──► ESP32 ► LED strip
                                                       └─UDP JSON :38899──────────────► WiZ bulb
```

## Running it

Requires Python 3.9 or newer. There's nothing to install: it only uses the standard library.

```bash
cd source/room-ambiance
python3 server.py
```

Then open **http://localhost:8000** on the Mac, or the address it prints
(for example `http://192.168.0.104:8000`) on your phone. On a phone, "Add to Home
Screen" makes it open like an app.

| Option | What it does |
|---|---|
| `--dry-run` | Runs everything but sends nothing to the lights. Good for trying the UI. |
| `--port 8080` | Use a different port. |
| `--data-dir PATH` | Where settings and state are stored (default `./data`). |
| `-v` | Log every request. |

Stop it with `Ctrl+C`. The lights keep whatever they were showing.

### Running it always on (Raspberry Pi)

Any always-on Linux box on the same network works. On a Raspberry Pi 3B+ with Raspberry Pi
OS Lite, the heaviest effect uses about 15% of one core. Set it up as a systemd user service,
no `sudo` needed:

```bash
# copy the project to the Pi, then on the Pi:
loginctl enable-linger $USER          # let user services start at boot without a login
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/room-ambiance.service <<'EOF'
[Unit]
Description=Room Ambiance
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=%h/room-ambiance
ExecStart=/usr/bin/python3 %h/room-ambiance/server.py --port 8000
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload
systemctl --user enable --now room-ambiance
```

Then open `http://<pi-hostname>.local:8000` from any device. Logs:
`journalctl --user-unit room-ambiance -f`. To update, copy the new files over (keep `data/`)
and run `systemctl --user restart room-ambiance`.

Only run one copy at a time: two servers would both drive the strip.

## Features

- **Ambiances**: 17 one-tap presets that set the strip and the bulb together
  (Warm evening, Reading, Focus, Movie night, Candlelight, Fireplace, Sunset, Ocean,
  Aurora, Forest, Romance, Chill, Neon, Party, Starry night, Night light, Wake up).
  Save the current look as your own ambiance and delete it later.
- **Strip effects**: Solid, Gradient, Breathe, Rainbow, Color cycle, Wave, Comet,
  Twinkle, Candle, Fire, Palette flow (9 palettes) and a timed Sunrise. Each has its
  own speed and settings.
- **Bulb**: any color, tunable white (2200 K to 6500 K), all 32 built-in WiZ scenes
  with speed, or software effects (Breathe, Color cycle, Candle, Palette flow, Sunrise).
- **Sync bulb**: the bulb follows the strip's average color live, so both match.
- **Sleep timer**: 15 minutes to 2 hours. The lights fade over the last minute, then turn off.
- **Live preview** of the strip around the monitor and the bulb's color, updated
  15 times a second.
- **Stays in sync** with other devices: open it on the Mac and the phone and both update
  live. Changes made in the WiZ app are picked up within a few seconds.
- **Remembers state** across restarts.
- Warns you when the PC ambilight sender is also driving the strip.

## Settings

The Settings tab covers the bulb's IP (with a "Find bulbs" search), the frame rate and a
brightness limit for the strip. The LED count is fixed at 107 to match the hardware. Everything else lives in
`data/config.json`, which is created on first save:

| Key | Default | Notes |
|---|---|---|
| `bulb_ip` | `192.168.0.101` | Reserve this address in the router so it doesn't change. |
| `multicast_group`, `strip_port` | `239.1.1.1`, `7777` | Must match `MCAST_IP` / `UDP_PORT` in the ESP32's `secrets.h`. |
| `led_count` | `107` | Matches the installed strip. Not editable in the app. |
| `fps` | `30` | Strip frame rate. |
| `max_brightness` | `100` | Hard cap for the strip, in percent. Lower it if your power supply is small. |
| `layout` | ambilight layout | Strip segments for the live preview, starting at the bottom centre and running clockwise. |
| `http_port` | `8000` | |
| `interface_ip` | auto | Set this if the Mac has several network interfaces. |

## Notes

- **Router addresses.** The bulb and ESP32 got new IP addresses when the router
  rebooted. Reserve them in the router (TP-Link: Advanced, Network, DHCP Server,
  Address Reservation) so the bulb's address stays fixed. The strip uses multicast,
  so its address doesn't matter.
- **Ambilight and this app both drive the same strip.** Whichever sends last wins,
  so stop one before using the other. Turning the strip off here stops this app from
  sending, so the ambilight takes over again.
- **If devices stop responding** but still show in the router, the router may have
  stopped passing traffic between WiFi devices. Rebooting it fixed this before.

## Project layout

```
server.py              entry point
ambiance/
    engine.py          state, validation, render loop, bulb sync
    effects.py         light effects (strip and bulb share them)
    devices.py         ESP32 multicast output, WiZ bulb driver, discovery
    catalog.py         built-in ambiances and WiZ scenes
    color.py           color math and palettes
    config.py          settings file
    web.py             HTTP server, JSON API, live event stream
web/                   the web app (HTML, CSS, JavaScript, no build step)
tests/                 unit tests
```

Run the tests with `python3 -m unittest discover -s tests`.

## API

All endpoints take and return JSON.

| Method | Path | Body |
|---|---|---|
| GET | `/api/state` | Current state and device status |
| GET | `/api/catalog` | Effects, palettes, WiZ scenes, ambiances |
| GET | `/api/events` | Server-Sent Events: `state` and `frame` |
| POST | `/api/strip` | Any of `power`, `effect`, `color`, `color2`, `brightness`, `speed`, `params` |
| POST | `/api/bulb` | Any of `power`, `mode`, `color`, `temp`, `brightness`, `scene`, `scene_speed`, `effect`, `speed`, `params` |
| POST | `/api/ambiance` | `{"id": "ocean"}` |
| POST | `/api/ambiances` | `{"name": "My look"}` saves the current state |
| DELETE | `/api/ambiances/<id>` | Deletes a saved ambiance |
| POST | `/api/link` | `{"on": true}` |
| POST | `/api/power` | `{"on": false}` |
| POST | `/api/timer` | `{"minutes": 30}`, `0` cancels |
| POST | `/api/settings` | `bulb_ip`, `fps`, `max_brightness` |
| POST | `/api/discover` | Finds WiZ bulbs on the network |

## License

MIT, see [LICENSE](LICENSE).
