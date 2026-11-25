"use strict";

/* =========================================================================
   Room Ambiance web app. Talks to the local server over a small JSON API and
   receives live state and preview frames over Server-Sent Events.
   ========================================================================= */

// ------------------------------------------------------------------ helpers
const $ = (sel, root = document) => root.querySelector(sel);

function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "style") el.style.cssText = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k in el && typeof v !== "string") el[k] = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    el.append(c.nodeType ? c : document.createTextNode(String(c)));
  }
  return el;
}

// Replace an element's children. Accepts nested arrays and skips null/false, which
// replaceChildren() on its own would print as text ("null", "[object ...]").
function fill(el, ...children) {
  el.replaceChildren(...children.flat(Infinity).filter((c) => c != null && c !== false));
  return el;
}

const api = {
  async req(method, path, body) {
    const res = await fetch(path, {
      method,
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
    return data;
  },
  get: (p) => api.req("GET", p),
  post: (p, b) => api.req("POST", p, b || {}),
  del: (p) => api.req("DELETE", p),
};

function toast(message, kind = "info") {
  const el = h("div", { class: `toast ${kind}` }, message);
  $("#toasts").append(el);
  setTimeout(() => el.remove(), kind === "error" ? 5000 : 2600);
}

function deepMerge(a, b) {
  const out = { ...(a || {}) };
  for (const [k, v] of Object.entries(b || {})) {
    out[k] = v && typeof v === "object" && !Array.isArray(v) ? deepMerge(out[k], v) : v;
  }
  return out;
}

// ------------------------------------------------------------------ color math
function hexToRgb(hex) {
  const n = parseInt(hex.replace("#", ""), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}
function rgbToHex([r, g, b]) {
  return "#" + [r, g, b].map((v) => Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, "0")).join("");
}
function rgbToHsv(r, g, b) {
  r /= 255; g /= 255; b /= 255;
  const max = Math.max(r, g, b), min = Math.min(r, g, b), d = max - min;
  let hue = 0;
  if (d) {
    if (max === r) hue = ((g - b) / d) % 6;
    else if (max === g) hue = (b - r) / d + 2;
    else hue = (r - g) / d + 4;
    hue /= 6;
    if (hue < 0) hue += 1;
  }
  return [hue, max ? d / max : 0, max];
}
function hsvToRgb(hue, s, v) {
  const i = Math.floor(hue * 6), f = hue * 6 - i;
  const p = v * (1 - s), q = v * (1 - f * s), t = v * (1 - (1 - f) * s);
  const [r, g, b] = [[v, t, p], [q, v, p], [p, v, t], [p, q, v], [t, p, v], [v, p, q]][((i % 6) + 6) % 6];
  return [r * 255, g * 255, b * 255];
}
function luminance([r, g, b]) {
  return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255;
}
function kelvinToHex(k) {
  const t = k / 100;
  let r, g, b;
  if (t <= 66) { r = 255; g = 99.47 * Math.log(t) - 161.12; } else { r = 329.7 * (t - 60) ** -0.1332; g = 288.12 * (t - 60) ** -0.0755; }
  if (t >= 66) b = 255; else if (t <= 19) b = 0; else b = 138.52 * Math.log(t - 10) - 305.04;
  return rgbToHex([r, g, b]);
}

// ------------------------------------------------------------------ app state
let catalog = null;
let state = null;
const busy = new Set(); // controls the user is dragging; remote updates skip them

const effectById = (id) => catalog.effects.find((e) => e.id === id) || catalog.effects[0];
const paramValue = (def, params, key) => {
  const p = def.params.find((x) => x.key === key);
  const v = params ? params[key] : undefined;
  return v === undefined ? p.default : v;
};

// Coalesces rapid changes (dragging) into at most ~15 requests per second.
function makeSender(path) {
  let pending = null, timer = null, inflight = false;
  const flush = async () => {
    timer = null;
    if (inflight || !pending) return;
    const body = pending;
    pending = null;
    inflight = true;
    try { await api.post(path, body); } catch (e) { toast(e.message, "error"); }
    inflight = false;
    if (pending) schedule();
  };
  const schedule = () => { if (!timer) timer = setTimeout(flush, 65); };
  return (patch) => { pending = deepMerge(pending, patch); schedule(); };
}
const sendStrip = makeSender("/api/strip");
const sendBulb = makeSender("/api/bulb");

function setStrip(patch) {
  state.strip = deepMerge(state.strip, patch);
  state.ambiance = null;
  sendStrip(patch);
  render();
}
function setBulb(patch) {
  state.bulb = deepMerge(state.bulb, patch);
  state.ambiance = null;
  sendBulb(patch);
  render();
}
async function post(path, body, okMessage) {
  try {
    const r = await api.post(path, body);
    if (okMessage) toast(okMessage);
    return r;
  } catch (e) {
    toast(e.message, "error");
    return null;
  }
}

// ------------------------------------------------------------------ components
function Slider({ label, min, max, step = 1, unit = "", format, onInput, className = "", fill }) {
  const input = h("input", { type: "range", min, max, step, "aria-label": label });
  const out = h("span", { class: "slider-val" });
  const el = h("label", { class: `slider ${className}` }, h("div", { class: "slider-head" }, h("span", {}, label), out), input);
  const fmt = format || ((v) => `${+(+v).toFixed(2)}${unit}`);
  if (fill) input.style.setProperty("--fill", fill);
  const paint = () => {
    input.style.setProperty("--p", `${((input.value - min) / (max - min)) * 100}%`);
    out.textContent = fmt(+input.value);
  };
  const release = () => busy.delete(input);
  input.addEventListener("pointerdown", () => busy.add(input));
  input.addEventListener("pointerup", release);
  input.addEventListener("pointercancel", release);
  input.addEventListener("change", release);
  input.addEventListener("input", () => { paint(); onInput(+input.value); });
  return {
    el,
    set(v) { if (busy.has(input)) return; input.value = v; paint(); },
  };
}

const SWATCHES = ["#ff9a4d", "#ffd8a8", "#ff3b30", "#ff2d6f", "#b5179e", "#7b5cff",
  "#3a5bff", "#00b4d8", "#00e6a8", "#3ddc5f", "#ffd60a", "#ffffff"];

function ColorPicker({ onInput }) {
  const knob = h("div", { class: "wheel-knob" });
  const wheel = h("div", { class: "wheel", role: "slider", "aria-label": "Color wheel", tabindex: "0" }, knob);
  const chip = h("div", { class: "hex-chip" });
  const hexInput = h("input", { type: "text", class: "mono", maxlength: "7", spellcheck: "false", "aria-label": "Hex color" });
  const swatchEls = SWATCHES.map((c) =>
    h("button", { class: "swatch", style: `--c:${c}`, title: c, "aria-label": `Color ${c}`, onclick: () => pick(c) }));
  const el = h("div", { class: "picker" }, wheel,
    h("div", { class: "picker-side" },
      h("div", { class: "swatches" }, swatchEls),
      h("div", { class: "hex-row" }, chip, hexInput)));

  let current = "#ffffff";
  const place = (hex) => {
    const [hue, s] = rgbToHsv(...hexToRgb(hex));
    const a = hue * Math.PI * 2;
    knob.style.left = `${50 + Math.sin(a) * s * 50}%`;
    knob.style.top = `${50 - Math.cos(a) * s * 50}%`;
    knob.style.background = hex;
    chip.style.setProperty("--c", hex);
    if (document.activeElement !== hexInput) hexInput.value = hex;
    swatchEls.forEach((s2, i) => s2.classList.toggle("active", SWATCHES[i] === hex));
  };
  const pick = (hex) => { current = hex; place(hex); onInput(hex); };
  const fromPointer = (e) => {
    const r = wheel.getBoundingClientRect();
    const x = e.clientX - (r.left + r.width / 2), y = e.clientY - (r.top + r.height / 2);
    const s = Math.min(1, Math.hypot(x, y) / (r.width / 2));
    const hue = (Math.atan2(x, -y) / (Math.PI * 2) + 1) % 1;
    pick(rgbToHex(hsvToRgb(hue, s, 1)));
  };
  wheel.addEventListener("pointerdown", (e) => { wheel.setPointerCapture(e.pointerId); busy.add(wheel); fromPointer(e); });
  wheel.addEventListener("pointermove", (e) => { if (wheel.hasPointerCapture(e.pointerId)) fromPointer(e); });
  const end = () => busy.delete(wheel);
  wheel.addEventListener("pointerup", end);
  wheel.addEventListener("pointercancel", end);
  wheel.addEventListener("keydown", (e) => {
    const [hue, s] = rgbToHsv(...hexToRgb(current));
    const moves = { ArrowLeft: [-0.02, 0], ArrowRight: [0.02, 0], ArrowUp: [0, 0.05], ArrowDown: [0, -0.05] };
    if (!moves[e.key]) return;
    e.preventDefault();
    const [dh, ds] = moves[e.key];
    pick(rgbToHex(hsvToRgb((hue + dh + 1) % 1, Math.max(0, Math.min(1, s + ds)), 1)));
  });
  hexInput.addEventListener("change", () => {
    let v = hexInput.value.trim();
    if (!v.startsWith("#")) v = "#" + v;
    if (/^#[0-9a-fA-F]{6}$/.test(v)) pick(v.toLowerCase());
    else { hexInput.value = current; toast("Use a 6 digit hex color like #ff8800", "error"); }
  });
  return { el, set(hex) { if (busy.has(wheel)) return; current = hex; place(hex); } };
}

function Seg(options, value, onPick) {
  const el = h("div", { class: "seg", role: "radiogroup" },
    options.map(([v, label]) => h("button", {
      class: v === value ? "active" : "", role: "radio", "aria-checked": String(v === value), onclick: () => onPick(v),
    }, label)));
  return el;
}

function ParamControl(def, p, onInput) {
  if (p.type === "select") {
    const wrap = h("div", { class: "field" }, h("div", { class: "label" }, p.label));
    const row = h("div", { class: "chip-row" });
    const btns = p.options.map((opt) => {
      const pal = p.key === "palette" ? catalog.palettes[opt] : null;
      const b = h("button", { class: "chip", onclick: () => onInput(opt) },
        pal ? h("span", { style: `display:inline-block;width:28px;height:10px;border-radius:5px;margin-right:6px;vertical-align:middle;background:linear-gradient(90deg,${pal.join(",")})` }) : null,
        opt[0].toUpperCase() + opt.slice(1));
      row.append(b);
      return [opt, b];
    });
    wrap.append(row);
    return { el: wrap, set(v) { btns.forEach(([o, b]) => b.classList.toggle("active", o === v)); } };
  }
  const s = Slider({ label: p.label, min: p.min, max: p.max, step: p.step, unit: p.unit || "", onInput });
  return { el: s.el, set: s.set };
}

function card(...children) { return h("div", { class: "card" }, ...children); }
function cardHead(title, sub) { return h("div", { class: "card-head" }, h("h3", {}, title), sub ? h("span", { class: "muted" }, sub) : null); }

// ------------------------------------------------------------------ ambiances panel
const scenesPanel = {
  el: $("#panel-scenes"),
  sig: null,
  saving: false,
  confirmId: null,
  render() {
    const custom = state.custom_ambiances || [];
    const sig = JSON.stringify([custom.map((c) => [c.id, c.name]), this.saving, this.confirmId]);
    if (sig !== this.sig) { this.build(custom); this.sig = sig; }
    this.el.querySelectorAll(".scene-card").forEach((c) => c.classList.toggle("active", c.dataset.id === state.ambiance));
  },
  build(custom) {
    const sceneCard = (a, isCustom) => h("button", {
      class: "scene-card", "data-id": a.id,
      style: `--g: linear-gradient(135deg, ${(a.swatch.length > 1 ? a.swatch : [a.swatch[0], a.swatch[0]]).join(", ")})`,
      onclick: (e) => { if (!e.target.closest(".scene-del")) applyAmbiance(a.id); },
    },
    isCustom ? h("span", {
      class: `scene-del${this.confirmId === a.id ? " confirm" : ""}`, role: "button", tabindex: "0",
      "aria-label": `Delete ${a.name}`,
      onclick: (e) => { e.stopPropagation(); this.askDelete(a.id); },
    }, this.confirmId === a.id ? "Delete?" : "×") : null,
    h("div", { class: "scene-name" }, a.name),
    h("div", { class: "scene-sub" }, a.subtitle || ""));

    const saveArea = this.saving
      ? h("form", { class: "save-row", onsubmit: (e) => { e.preventDefault(); this.save(e.target.querySelector("input").value); } },
          h("input", { type: "text", name: "name", placeholder: "Name this ambiance", maxlength: "40", autofocus: true }),
          h("button", { class: "btn", type: "submit" }, "Save"),
          h("button", { class: "btn ghost", type: "button", onclick: () => { this.saving = false; render(); } }, "Cancel"))
      : null;

    fill(this.el, 
      h("div", { class: "section-head" },
        h("div", {}, h("h2", {}, "Ambiances"), h("p", {}, "Set the strip and the bulb together in one tap.")),
        this.saving ? null : h("button", { class: "btn ghost", onclick: () => { this.saving = true; render(); } }, "Save current")),
      saveArea,
      h("div", { class: "scene-grid" }, catalog.ambiances.map((a) => sceneCard(a, false))),
      custom.length ? h("div", { class: "section-head" }, h("div", {}, h("h2", {}, "Yours"), h("p", {}, "Ambiances you've saved."))) : null,
      custom.length ? h("div", { class: "scene-grid" }, custom.map((a) => sceneCard(a, true))) : null,
    );
    const input = this.el.querySelector("input[name=name]");
    if (input) input.focus();
  },
  async save(name) {
    if (!name.trim()) { toast("Give it a name first", "error"); return; }
    const r = await post("/api/ambiances", { name }, `Saved "${name.trim()}"`);
    if (r) { this.saving = false; render(); }
  },
  async askDelete(id) {
    if (this.confirmId !== id) {
      this.confirmId = id;
      render();
      setTimeout(() => { if (this.confirmId === id) { this.confirmId = null; render(); } }, 3000);
      return;
    }
    this.confirmId = null;
    try { await api.del(`/api/ambiances/${encodeURIComponent(id)}`); toast("Deleted"); } catch (e) { toast(e.message, "error"); }
  },
};

async function applyAmbiance(id) {
  state.ambiance = id;
  render();
  await post("/api/ambiance", { id });
}

// ------------------------------------------------------------------ strip panel
const stripPanel = {
  el: $("#panel-strip"),
  key: null,
  colorTarget: "color",
  render() {
    const s = state.strip, fx = effectById(s.effect);
    if (fx.colors < 2) this.colorTarget = "color";
    const key = `${s.effect}|${this.colorTarget}`;
    if (key !== this.key) { this.build(fx); this.key = key; }
    this.update(s, fx);
  },
  build(fx) {
    this.power = h("input", { type: "checkbox", class: "switch", "aria-label": "Strip power",
      onchange: (e) => setStrip({ power: e.target.checked }) });
    this.sub = h("div", { class: "muted small" });
    this.bright = Slider({ label: "Brightness", min: 1, max: 100, unit: "%", onInput: (v) => setStrip({ brightness: v }) });
    this.fxBtns = catalog.effects.map((e) => h("button", {
      class: "fx", onclick: () => setStrip({ effect: e.id, power: true }),
    }, h("div", { class: "fx-name" }, e.name), h("div", { class: "fx-desc" }, e.description)));
    this.speed = fx.animated ? Slider({ label: "Speed", min: 1, max: 100, unit: "%", onInput: (v) => setStrip({ speed: v }) }) : null;
    this.params = fx.params.map((p) => [p, ParamControl(fx, p, (v) => setStrip({ params: { [p.key]: v } }))]);
    this.picker = fx.colors ? ColorPicker({ onInput: (hex) => setStrip({ [this.colorTarget]: hex, power: true }) }) : null;

    const colorCard = fx.colors ? card(
      cardHead("Color", fx.colors === 2 ? null : "Tap the wheel or a swatch"),
      fx.colors === 2 ? h("div", { style: "margin-bottom:14px" }, Seg([["color", "Primary"], ["color2", "Secondary"]], this.colorTarget,
        (v) => { this.colorTarget = v; render(); })) : null,
      this.picker.el,
    ) : card(cardHead("Color"), h("p", { class: "muted small", style: "margin:0" }, `${fx.name} uses its own colors.`));

    fill(this.el, 
      card(h("div", { class: "stack" },
        h("div", { class: "power-row" }, h("div", {}, h("div", { class: "title" }, "LED strip"), this.sub), this.power),
        this.bright.el)),
      card(cardHead("Effect", fx.description),
        h("div", { class: "fx-grid" }, this.fxBtns),
        (this.speed || this.params.length) ? h("div", { class: "stack", style: "margin-top:16px" },
          this.speed ? this.speed.el : null, this.params.map(([, c]) => c.el)) : null),
      colorCard,
    );
  },
  update(s, fx) {
    this.power.checked = s.power;
    this.sub.textContent = s.power ? `${fx.name} at ${s.brightness}%` : "Off";
    this.bright.set(s.brightness);
    this.fxBtns.forEach((b, i) => b.classList.toggle("active", catalog.effects[i].id === s.effect));
    if (this.speed) this.speed.set(s.speed);
    this.params.forEach(([p, c]) => c.set(paramValue(fx, s.params, p.key)));
    if (this.picker) this.picker.set(s[this.colorTarget]);
  },
};

// ------------------------------------------------------------------ bulb panel
const WHITE_PRESETS = [[2200, "Candle"], [2700, "Warm"], [3500, "Soft"], [4000, "Neutral"], [5000, "Daylight"], [6500, "Cool"]];
const BULB_EFFECTS = () => catalog.effects.filter((e) => e.bulb);

const bulbPanel = {
  el: $("#panel-bulb"),
  key: null,
  render() {
    const b = state.bulb;
    const key = `${b.mode}|${state.link}|${b.mode === "effect" ? b.effect : ""}`;
    if (key !== this.key) { this.build(b); this.key = key; }
    this.update(b);
  },
  build(b) {
    const linked = state.link;
    this.power = h("input", { type: "checkbox", class: "switch", "aria-label": "Bulb power",
      onchange: (e) => setBulb({ power: e.target.checked }) });
    this.sub = h("div", { class: "muted small" });
    this.bright = Slider({ label: "Brightness", min: 10, max: 100, unit: "%", onInput: (v) => setBulb({ brightness: v }) });
    const modes = [["color", "Color"], ["white", "White"], ["scene", "Scenes"], ["effect", "Effects"]];
    const modeSeg = Seg(modes, b.mode, (m) => setBulb({ mode: m, power: true }));

    let content;
    this.picker = this.temp = this.sceneSpeed = this.fxSpeed = null;
    this.whiteBtns = this.sceneBtns = this.fxBtns = [];
    this.params = [];
    if (b.mode === "color") {
      this.picker = ColorPicker({ onInput: (hex) => setBulb({ color: hex, mode: "color", power: true }) });
      content = card(cardHead("Color"), this.picker.el);
    } else if (b.mode === "white") {
      this.temp = Slider({ label: "Temperature", min: 2200, max: 6500, step: 50, className: "temp",
        format: (v) => `${v}K`, onInput: (v) => setBulb({ temp: v, mode: "white", power: true }) });
      this.whiteBtns = WHITE_PRESETS.map(([k, name]) => [k, h("button", { class: "chip", onclick: () => setBulb({ temp: k, mode: "white", power: true }) }, name)]);
      content = card(cardHead("White", "From candle warm to cool daylight"),
        h("div", { class: "stack" }, this.temp.el, h("div", { class: "chip-row" }, this.whiteBtns.map(([, el]) => el))));
    } else if (b.mode === "scene") {
      this.sceneBtns = catalog.wiz_scenes.map((s) => [s, h("button", {
        class: "wiz-scene", style: `--c:${s.color}`, onclick: () => setBulb({ scene: s.id, mode: "scene", power: true }),
      }, h("span", { class: "sw" }), s.name)]);
      this.sceneSpeed = Slider({ label: "Scene speed", min: 10, max: 200, step: 5, unit: "%", onInput: (v) => setBulb({ scene_speed: v }) });
      content = card(cardHead("WiZ scenes", "Played by the bulb itself"),
        h("div", { class: "scene-chips" }, this.sceneBtns.map(([, el]) => el)),
        h("div", { style: "margin-top:16px" }, this.sceneSpeed.el));
    } else {
      const fx = effectById(b.effect);
      this.fxBtns = BULB_EFFECTS().map((e) => [e, h("button", { class: "fx", onclick: () => setBulb({ effect: e.id, mode: "effect", power: true }) },
        h("div", { class: "fx-name" }, e.name), h("div", { class: "fx-desc" }, e.description))]);
      this.fxSpeed = fx.animated ? Slider({ label: "Speed", min: 1, max: 100, unit: "%", onInput: (v) => setBulb({ speed: v }) }) : null;
      this.params = fx.params.map((p) => [p, ParamControl(fx, p, (v) => setBulb({ params: { [p.key]: v } }))]);
      this.picker = fx.colors ? ColorPicker({ onInput: (hex) => setBulb({ color: hex }) }) : null;
      content = h("div", { class: "stack" },
        card(cardHead("Effect", fx.description),
          h("div", { class: "fx-grid" }, this.fxBtns.map(([, el]) => el)),
          h("div", { class: "stack", style: "margin-top:16px" }, this.fxSpeed ? this.fxSpeed.el : null, this.params.map(([, c]) => c.el))),
        this.picker ? card(cardHead("Color"), this.picker.el) : null);
    }

    const body = h("div", { class: `stack${linked ? " card disabled" : ""}`, style: linked ? "padding:0;border:0;background:none" : "" },
      card(h("div", { class: "stack" },
        h("div", { class: "power-row" }, h("div", {}, h("div", { class: "title" }, "WiZ bulb"), this.sub), this.power),
        this.bright.el)),
      card(modeSeg),
      content);

    fill(this.el, 
      linked ? h("div", { class: "banner" }, "The bulb is following the strip's colors. ",
        h("button", { class: "btn ghost", onclick: () => post("/api/link", { on: false }) }, "Control it separately")) : null,
      body);
  },
  update(b) {
    this.power.checked = b.power;
    const st = state.status.bulb;
    const modeText = { color: "Color", white: `White ${b.temp}K`, scene: (catalog.wiz_scenes.find((s) => s.id === b.scene) || {}).name, effect: effectById(b.effect).name }[b.mode];
    this.sub.textContent = !st.online ? "Not responding" : state.link ? "Synced to strip" : b.power ? `${modeText} at ${b.brightness}%` : "Off";
    this.bright.set(b.brightness);
    if (this.picker) this.picker.set(b.color);
    if (this.temp) this.temp.set(b.temp);
    this.whiteBtns.forEach(([k, el]) => el.classList.toggle("active", k === b.temp));
    this.sceneBtns.forEach(([s, el]) => el.classList.toggle("active", s.id === b.scene));
    if (this.sceneSpeed) this.sceneSpeed.set(b.scene_speed);
    this.fxBtns.forEach(([e, el]) => el.classList.toggle("active", e.id === b.effect));
    if (this.fxSpeed) this.fxSpeed.set(b.speed);
    const fx = effectById(b.effect);
    this.params.forEach(([p, c]) => c.set(paramValue(fx, b.params, p.key)));
  },
};

// ------------------------------------------------------------------ settings panel
const settingsPanel = {
  el: $("#panel-settings"),
  built: false,
  render() {
    if (!this.built) { this.build(); this.built = true; }
    this.update();
  },
  build() {
    this.ip = h("input", { type: "text", class: "mono", inputmode: "decimal", placeholder: "192.168.0.101" });
    this.fps = Slider({ label: "Frame rate", min: 10, max: 60, unit: " fps", onInput: () => {} });
    this.cap = Slider({ label: "Strip brightness limit", min: 5, max: 100, unit: "%", onInput: () => {} });
    this.found = h("div", { class: "found" });
    this.kv = h("dl", { class: "kv" });
    const discoverBtn = h("button", { class: "btn ghost", onclick: async () => {
      discoverBtn.disabled = true;
      discoverBtn.textContent = "Searching...";
      const r = await post("/api/discover", {});
      discoverBtn.disabled = false;
      discoverBtn.textContent = "Find bulbs";
      const bulbs = (r && r.bulbs) || [];
      fill(this.found, bulbs.length
        ? bulbs.map((b) => h("button", { class: "btn ghost", onclick: () => { this.ip.value = b.ip; } },
            `${b.ip}  (${b.mac || "unknown"}${b.rssi != null ? `, ${b.rssi} dBm` : ""})`))
        : h("div", { class: "muted small" }, "No bulbs answered. Check that the bulb is powered and on this WiFi."));
    } }, "Find bulbs");

    fill(this.el, 
      h("div", { class: "section-head" }, h("div", {}, h("h2", {}, "Settings"), h("p", {}, "Devices and output limits."))),
      card(cardHead("Status"), this.kv),
      card(cardHead("WiZ bulb"),
        h("div", { class: "row" }, h("div", { class: "field" }, h("label", {}, "Bulb IP address"), this.ip), discoverBtn),
        this.found),
      card(cardHead("LED strip", "Sent to the ESP32 over multicast"),
        h("div", { class: "stack" }, this.fps.el, this.cap.el)),
      h("div", {}, h("button", { class: "btn", onclick: () => this.save() }, "Save settings")),
    );
    this.fill();
  },
  fill() {
    const s = state.settings;
    this.ip.value = s.bulb_ip;
    this.fps.set(s.fps);
    this.cap.set(s.max_brightness);
  },
  async save() {
    const body = {
      bulb_ip: this.ip.value.trim(),
      fps: +this.fps.el.querySelector("input").value,
      max_brightness: +this.cap.el.querySelector("input").value,
    };
    await post("/api/settings", body, "Settings saved");
  },
  update() {
    const st = state.status, s = state.settings, srv = state.server;
    const row = (k, v) => [h("dt", {}, k), h("dd", {}, v)];
    fill(this.kv, 
      ...row("Bulb", st.bulb.online ? `Online at ${st.bulb.ip}` : `Not responding at ${st.bulb.ip}`),
      ...row("Bulb signal", st.bulb.rssi != null ? `${st.bulb.rssi} dBm` : "Unknown"),
      ...row("Strip", st.strip.active ? `Sending, ${st.strip.fps} updates/s` : "Idle"),
      ...row("Strip output", `${s.multicast_group}:${s.strip_port}, ${s.led_count} LEDs`),
      ...row("Ambilight sender", st.ambilight.active ? `Running on ${st.ambilight.source}` : "Not running"),
      ...row("Open on your phone", srv.url),
      ...row("Version", srv.version + (srv.dry_run ? " (dry run)" : "")),
    );
  },
};

// ------------------------------------------------------------------ live preview
const preview = {
  canvas: $("#preview"),
  pts: [],
  leds: [],
  bulb: "",
  dirty: true,
  init() {
    this.ctx = this.canvas.getContext("2d");
    new ResizeObserver(() => this.layout()).observe(this.canvas);
    const loop = () => { if (this.dirty) this.draw(); requestAnimationFrame(loop); };
    requestAnimationFrame(loop);
  },
  setFrame(hex, bulb) {
    const n = hex.length / 6;
    const leds = new Array(n);
    for (let i = 0; i < n; i++) {
      const v = parseInt(hex.substr(i * 6, 6), 16);
      leds[i] = [(v >> 16) & 255, (v >> 8) & 255, v & 255];
    }
    this.leds = leds;
    this.bulb = bulb;
    this.dirty = true;
  },
  layout() {
    if (!state) return;
    const dpr = window.devicePixelRatio || 1;
    const W = this.canvas.clientWidth, H = this.canvas.clientHeight;
    if (!W || !H) return;
    this.canvas.width = Math.round(W * dpr);
    this.canvas.height = Math.round(H * dpr);
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.W = W; this.H = H;
    const mw = W * 0.6, mh = Math.min(mw * 9 / 16, H * 0.6);
    const mx = W * 0.14, my = H * 0.2;
    this.mon = { x: mx, y: my, w: mw, h: mh };
    this.bulbPos = { x: W * 0.9, y: H * 0.3, r: Math.max(9, W * 0.022) };
    const off = Math.max(7, W * 0.014);
    const x0 = mx - off, x1 = mx + mw + off, y0 = my - off, y1 = my + mh + off, cx = mx + mw / 2;
    const segs = {
      bottom_left: [[cx, y1], [x0, y1]], left: [[x0, y1], [x0, y0]], top: [[x0, y0], [x1, y0]],
      right: [[x1, y0], [x1, y1]], bottom_right: [[x1, y1], [cx, y1]],
    };
    const n = state.settings.led_count;
    const layout = state.settings.layout || [];
    const total = layout.reduce((a, [, c]) => a + c, 0);
    const pts = [];
    if (total === n && layout.every(([name]) => segs[name])) {
      for (const [name, count] of layout) {
        const [[ax, ay], [bx, by]] = segs[name];
        for (let i = 0; i < count; i++) {
          const t = (i + 0.5) / count;
          pts.push([ax + (bx - ax) * t, ay + (by - ay) * t]);
        }
      }
    } else {
      const path = [[cx, y1], [x0, y1], [x0, y0], [x1, y0], [x1, y1], [cx, y1]];
      const lens = path.slice(1).map((p, i) => Math.hypot(p[0] - path[i][0], p[1] - path[i][1]));
      const L = lens.reduce((a, b) => a + b, 0);
      for (let i = 0; i < n; i++) {
        let d = ((i + 0.5) / n) * L, k = 0;
        while (k < lens.length - 1 && d > lens[k]) { d -= lens[k]; k++; }
        const t = d / lens[k], [ax, ay] = path[k], [bx, by] = path[k + 1];
        pts.push([ax + (bx - ax) * t, ay + (by - ay) * t]);
      }
    }
    this.pts = pts;
    this.glowR = Math.max(18, W * 0.05);
    this.dirty = true;
  },
  draw() {
    this.dirty = false;
    const { ctx, W, H, mon, pts } = this;
    if (!W || !mon) return;
    ctx.globalCompositeOperation = "source-over";
    ctx.clearRect(0, 0, W, H);

    // light spill on the wall
    ctx.globalCompositeOperation = "lighter";
    for (let i = 0; i < pts.length; i++) {
      const c = this.leds[i];
      if (!c || c[0] + c[1] + c[2] < 3) continue;
      const [x, y] = pts[i];
      const g = ctx.createRadialGradient(x, y, 0, x, y, this.glowR);
      g.addColorStop(0, `rgba(${c[0]},${c[1]},${c[2]},0.32)`);
      g.addColorStop(1, `rgba(${c[0]},${c[1]},${c[2]},0)`);
      ctx.fillStyle = g;
      ctx.fillRect(x - this.glowR, y - this.glowR, this.glowR * 2, this.glowR * 2);
    }
    const bp = this.bulbPos;
    if (this.bulb) {
      const [r, g2, b] = hexToRgb(this.bulb);
      const R = W * 0.3;
      const gb = ctx.createRadialGradient(bp.x, bp.y, 0, bp.x, bp.y, R);
      gb.addColorStop(0, `rgba(${r},${g2},${b},0.55)`);
      gb.addColorStop(1, `rgba(${r},${g2},${b},0)`);
      ctx.fillStyle = gb;
      ctx.fillRect(bp.x - R, bp.y - R, R * 2, R * 2);
    }
    ctx.globalCompositeOperation = "source-over";

    // monitor
    const round = (x, y, w, hh, rr) => { ctx.beginPath(); ctx.roundRect(x, y, w, hh, rr); };
    ctx.fillStyle = "#14161f";
    ctx.fillRect(mon.x + mon.w / 2 - mon.w * 0.03, mon.y + mon.h, mon.w * 0.06, H * 0.09);
    round(mon.x + mon.w / 2 - mon.w * 0.14, mon.y + mon.h + H * 0.085, mon.w * 0.28, H * 0.025, 4);
    ctx.fill();
    round(mon.x, mon.y, mon.w, mon.h, 8);
    ctx.fillStyle = "#07080c";
    ctx.fill();
    ctx.strokeStyle = "#262a3a";
    ctx.lineWidth = 1.5;
    ctx.stroke();
    const sg = ctx.createLinearGradient(mon.x, mon.y, mon.x + mon.w, mon.y + mon.h);
    sg.addColorStop(0, "rgba(255,255,255,0.05)");
    sg.addColorStop(0.5, "rgba(255,255,255,0.0)");
    ctx.fillStyle = sg;
    round(mon.x + 5, mon.y + 5, mon.w - 10, mon.h - 10, 5);
    ctx.fill();

    // LEDs
    const dot = Math.max(2, W * 0.0055);
    for (let i = 0; i < pts.length; i++) {
      const c = this.leds[i] || [0, 0, 0];
      const m = Math.max(c[0], c[1], c[2]);
      if (m < 2) { ctx.fillStyle = "#1c1f2b"; }
      else {
        const k = (90 + 165 * (m / 255)) / m;
        ctx.fillStyle = `rgb(${c[0] * k},${c[1] * k},${c[2] * k})`;
      }
      ctx.beginPath();
      ctx.arc(pts[i][0], pts[i][1], dot, 0, Math.PI * 2);
      ctx.fill();
    }

    // bulb
    ctx.strokeStyle = "#2b2f40";
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(bp.x, 0);
    ctx.lineTo(bp.x, bp.y - bp.r - 6);
    ctx.stroke();
    ctx.fillStyle = "#2b2f40";
    ctx.fillRect(bp.x - bp.r * 0.45, bp.y - bp.r - 7, bp.r * 0.9, 8);
    ctx.beginPath();
    ctx.arc(bp.x, bp.y, bp.r, 0, Math.PI * 2);
    if (this.bulb) {
      const c = hexToRgb(this.bulb), m = Math.max(...c) || 1, k = (120 + 135 * (m / 255)) / m;
      ctx.fillStyle = `rgb(${c[0] * k},${c[1] * k},${c[2] * k})`;
    } else {
      ctx.fillStyle = "#181b26";
    }
    ctx.fill();
    ctx.strokeStyle = "#353a4f";
    ctx.stroke();
  },
};

// ------------------------------------------------------------------ top bar and stage
let timerEndsAt = null;

function renderChrome() {
  const s = state, st = s.status;
  // pills
  const pill = (cls, text) => h("span", { class: `pill ${cls}` }, h("span", { class: "dot" }), text);
  fill($("#pills"), 
    pill(s.strip.power ? "ok" : "", s.strip.power ? "Strip on" : "Strip off"),
    st.bulb.online ? pill(s.bulb.power || s.link ? "ok" : "", s.link ? "Bulb synced" : s.bulb.power ? "Bulb on" : "Bulb off") : pill("bad", "Bulb offline"),
    st.ambilight.active ? pill("warn", "Ambilight running") : null,
  );
  $("#masterPower").setAttribute("aria-pressed", String(s.strip.power || s.bulb.power));
  $("#linkToggle").checked = s.link;

  // accent follows the light
  const fx = effectById(s.strip.effect);
  let accent = s.strip.color;
  if (!fx.colors) {
    const sw = fx.id === "flow" ? catalog.palettes[paramValue(fx, s.strip.params, "palette")]
      : { rainbow: ["#a64dff"], spectrum: ["#a64dff"], fire: ["#ff5a1a"], sunrise: ["#ff8a1f"] }[fx.id];
    accent = (sw && sw[1]) || (sw && sw[0]) || accent;
  }
  if (!s.strip.power && s.bulb.power && !s.link) {
    accent = s.bulb.mode === "white" ? kelvinToHex(s.bulb.temp)
      : s.bulb.mode === "scene" ? (catalog.wiz_scenes.find((x) => x.id === s.bulb.scene) || {}).color || accent
      : s.bulb.color;
  }
  const [r, g, b] = hexToRgb(accent);
  const [hue, sat] = rgbToHsv(r, g, b);
  const vivid = rgbToHex(hsvToRgb(hue, Math.min(sat, 0.85), 1));
  document.documentElement.style.setProperty("--accent", vivid);
  document.documentElement.style.setProperty("--accent-ink", luminance(hexToRgb(vivid)) > 0.55 ? "#141009" : "#ffffff");

  // now playing
  const amb = catalog.ambiances.find((a) => a.id === s.ambiance) || (s.custom_ambiances || []).find((a) => a.id === s.ambiance);
  $("#nowPlaying").textContent = amb ? amb.name
    : !s.strip.power && !s.bulb.power ? "Lights off"
    : s.strip.power ? `${fx.name}${s.link ? ", bulb synced" : ""}` : "Bulb only";

  // banners
  const banner = $("#ambilightBanner");
  banner.hidden = !st.ambilight.active;
  if (st.ambilight.active) {
    banner.textContent = `The ambilight sender on ${st.ambilight.source} is also driving the strip. `
      + "Whichever sends last wins, so stop one of them for steady results.";
  }
  $("#dryRunBanner").hidden = !s.server.dry_run;

  // timer
  timerEndsAt = s.timer_remaining != null ? Date.now() + s.timer_remaining * 1000 : null;
  updateTimerLabel();
}

function updateTimerLabel() {
  const label = $("#timerLabel"), btn = $("#timerBtn");
  if (!timerEndsAt) { label.textContent = ""; btn.classList.remove("active"); $("#timerCancel").hidden = true; return; }
  const left = Math.max(0, Math.round((timerEndsAt - Date.now()) / 1000));
  const hh = Math.floor(left / 3600), mm = Math.floor((left % 3600) / 60), ss = left % 60;
  label.textContent = hh ? `${hh}:${String(mm).padStart(2, "0")}:${String(ss).padStart(2, "0")}` : `${mm}:${String(ss).padStart(2, "0")}`;
  btn.classList.add("active");
  $("#timerCancel").hidden = false;
}

function setupChrome() {
  $("#masterPower").addEventListener("click", () => {
    const on = !(state.strip.power || state.bulb.power);
    state.strip.power = on;
    state.bulb.power = on;
    render();
    post("/api/power", { on });
  });
  $("#linkToggle").addEventListener("change", (e) => {
    state.link = e.target.checked;
    render();
    post("/api/link", { on: e.target.checked }, e.target.checked ? "Bulb now follows the strip" : null);
  });

  const pop = $("#timerPop"), btn = $("#timerBtn");
  const close = () => { pop.hidden = true; btn.setAttribute("aria-expanded", "false"); };
  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    pop.hidden = !pop.hidden;
    btn.setAttribute("aria-expanded", String(!pop.hidden));
  });
  document.addEventListener("click", (e) => { if (!pop.hidden && !pop.contains(e.target)) close(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") close(); });
  fill($("#timerOptions"), ...[[15, "15 min"], [30, "30 min"], [60, "1 hour"], [120, "2 hours"]].map(([m, label]) =>
    h("button", { class: "chip", onclick: async () => { close(); await post("/api/timer", { minutes: m }, `Lights off in ${label}`); } }, label)));
  $("#timerCancel").addEventListener("click", async () => { close(); await post("/api/timer", { minutes: 0 }, "Timer cancelled"); });
  setInterval(updateTimerLabel, 1000);

  // tabs
  const tabs = [...document.querySelectorAll(".tabs button")];
  const show = (name) => {
    tabs.forEach((t) => t.setAttribute("aria-selected", String(t.dataset.tab === name)));
    for (const t of tabs) $(`#panel-${t.dataset.tab}`).hidden = t.dataset.tab !== name;
    try { localStorage.setItem("ambiance.tab", name); } catch (_) { /* private mode */ }
  };
  tabs.forEach((t) => t.addEventListener("click", () => show(t.dataset.tab)));
  let saved = null;
  try { saved = localStorage.getItem("ambiance.tab"); } catch (_) { /* private mode */ }
  show(tabs.some((t) => t.dataset.tab === saved) ? saved : "scenes");
}

// ------------------------------------------------------------------ render and live connection
let lastLedCount = null;
function render() {
  if (!state || !catalog) return;
  renderChrome();
  scenesPanel.render();
  stripPanel.render();
  bulbPanel.render();
  settingsPanel.render();
  if (state.settings.led_count !== lastLedCount) { lastLedCount = state.settings.led_count; preview.layout(); }
}

function connect() {
  let offlineTimer = null;
  const es = new EventSource("/api/events");
  es.addEventListener("state", (e) => {
    state = JSON.parse(e.data);
    clearTimeout(offlineTimer);
    $("#offline").hidden = true;
    render();
  });
  es.addEventListener("frame", (e) => {
    const d = JSON.parse(e.data);
    preview.setFrame(d.s, d.b);
  });
  es.onerror = () => {
    clearTimeout(offlineTimer);
    offlineTimer = setTimeout(() => { $("#offline").hidden = false; }, 1500);
  };
}

async function init() {
  try {
    [catalog, state] = await Promise.all([api.get("/api/catalog"), api.get("/api/state")]);
  } catch (e) {
    $("#offline").hidden = false;
    setTimeout(init, 2000);
    return;
  }
  setupChrome();
  preview.init();
  render();
  preview.layout();
  connect();
}

init();
