// Where the phones stood, drawn from when each one heard things (data/<event>/positions.json).
//
// Sound covers a metre in 2.9 ms. One sound only says how far a phone sat from it, which is a
// circle; several sounds made in different places pin the phones down in two dimensions. When the
// sounds cannot do that, this draws the circles instead and says why, rather than inventing a map.

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const num = (v, digits, unit = "") => (v === null || v === undefined ? "—" : `${Number(v).toFixed(digits)}${unit}`);

const PAD = 28; // pixels kept clear around the drawing, so labels are not cut off
const VIEW = { w: 760, h: 520 };

export function renderMap(map, colorOf, other) {
  const color = (id) => colorOf[id] ?? other;
  if (!map) {
    $("map-panel").innerHTML = `<p class="muted">No map yet. Run <code>scenefold map &lt;event&gt;</code>.</p>`;
    return;
  }
  const placed = map.cameras.filter((c) => c.placed && c.x_m !== null);
  $("map-summary").innerHTML = [
    ["Phones placed", `${placed.length} of ${map.cameras.length}`],
    ["Sounds used", String(map.events_used ?? 0)],
    ["Fit", map.residual_ms === null || map.residual_ms === undefined ? "—" : `${map.residual_ms.toFixed(1)} ms`],
    ["Head start", map.head_start_from_pictures ? "measured from the pictures" : "solved from the sounds"],
  ]
    .map(([label, value]) => `<div class="stat"><span class="l">${label}</span><span class="v">${value}</span></div>`)
    .join("");

  $("map-drawing").innerHTML = map.solved && placed.length ? plan(map, placed, color) : circles(map, color);
  $("map-note").innerHTML = map.solved
    ? `Distances are real; the direction is not. Sound cannot tell a map from its mirror image, and
       nothing here says which way is north, so the whole picture may be flipped or turned.`
    : `<b>Not enough to draw a map.</b> ${esc(map.reason || "The sounds could not place the phones.")}
       Each ring below is how far that phone stood from whatever made most of the noise.`;
}

// A top-down plan: phones as dots, the sounds they heard as small crosses, metres as a scale bar.
function plan(map, placed, color) {
  const sounds = map.sounds || [];
  const xs = [...placed.map((c) => c.x_m), ...sounds.map((s) => s.x_m)];
  const ys = [...placed.map((c) => c.y_m), ...sounds.map((s) => s.y_m)];
  const span = Math.max(20, Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys));
  const scale = Math.min(VIEW.w - 2 * PAD, VIEW.h - 2 * PAD) / (span * 1.15);
  const midX = (Math.max(...xs) + Math.min(...xs)) / 2;
  const midY = (Math.max(...ys) + Math.min(...ys)) / 2;
  const px = (x) => VIEW.w / 2 + (x - midX) * scale;
  const py = (y) => VIEW.h / 2 - (y - midY) * scale; // y up, as on a map

  const soundMarks = sounds
    .map(
      (s) => `<g class="sound"><path d="M${px(s.x_m) - 5} ${py(s.y_m)}h10M${px(s.x_m)} ${py(s.y_m) - 5}v10"/>
        <title>${esc(s.label)} at ${num(s.t_master_s, 1, " s")}</title></g>`,
    )
    .join("");

  const phones = placed
    .map((c) => {
      const spread = c.uncertainty_m ? Math.max(4, c.uncertainty_m * scale) : 0;
      return `<g>
        ${spread ? `<circle class="spread" cx="${px(c.x_m)}" cy="${py(c.y_m)}" r="${spread.toFixed(1)}" fill="${color(c.clip_id)}"/>` : ""}
        <circle class="phone" cx="${px(c.x_m)}" cy="${py(c.y_m)}" r="7" fill="${color(c.clip_id)}"/>
        <text x="${px(c.x_m) + 12}" y="${py(c.y_m) + 4}">${esc(c.name)}</text>
        <title>${esc(c.name)}: ${num(c.x_m, 1)} m, ${num(c.y_m, 1)} m (give or take ${num(c.uncertainty_m, 1, " m")})</title>
      </g>`;
    })
    .join("");

  const bar = niceLength(span / 4);
  return `<svg viewBox="0 0 ${VIEW.w} ${VIEW.h}" class="map" role="img" aria-label="Where the phones stood">
    ${soundMarks}${phones}
    <g class="scale"><path d="M${PAD} ${VIEW.h - PAD}h${(bar * scale).toFixed(1)}"/>
      <text x="${PAD}" y="${VIEW.h - PAD - 8}">${bar} m</text></g>
  </svg>`;
}

// The honest fallback: how far each phone stood from the main sound, as rings around it.
function circles(map, color) {
  const known = map.cameras.filter((c) => c.from_main_sound_m !== null && c.from_main_sound_m !== undefined);
  if (!known.length) return `<p class="muted">Sync did not measure how far any phone stood from the sound.</p>`;
  const far = Math.max(...known.map((c) => c.from_main_sound_m), 1);
  const scale = (Math.min(VIEW.w, VIEW.h) / 2 - PAD) / far;
  const rings = known
    .map(
      (c) => `<g><circle class="ring" cx="${VIEW.w / 2}" cy="${VIEW.h / 2}" r="${(c.from_main_sound_m * scale).toFixed(1)}"
        stroke="${color(c.clip_id)}"/>
        <text x="${VIEW.w / 2 + c.from_main_sound_m * scale + 6}" y="${VIEW.h / 2 - 6}">${esc(c.name)} · ${num(c.from_main_sound_m, 0, " m")}</text></g>`,
    )
    .join("");
  return `<svg viewBox="0 0 ${VIEW.w} ${VIEW.h}" class="map rings" role="img" aria-label="How far each phone stood from the sound">
    ${rings}<circle class="source" cx="${VIEW.w / 2}" cy="${VIEW.h / 2}" r="5"/>
    <text class="source-label" x="${VIEW.w / 2 + 10}" y="${VIEW.h / 2 + 4}">the sound</text>
  </svg>`;
}

// 10, 20, 50, 100... so the scale bar is a number people read at a glance.
function niceLength(metres) {
  const power = 10 ** Math.floor(Math.log10(Math.max(1, metres)));
  const steps = [1, 2, 5, 10];
  return power * (steps.find((s) => metres / power <= s) ?? 10);
}
