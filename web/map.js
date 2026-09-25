// Where the phones stood, drawn from when each one heard things (data/<event>/positions.json).
//
// Sound covers a metre in 2.9 ms. One sound only says how far a phone sat from it, which is a
// circle; several sounds made in different places pin the phones down in two dimensions. When the
// sounds cannot do that, this draws the circles instead and says why, rather than inventing a map.
//
// Names go in a list beside the drawing, never on top of it: four labels at the same distance from
// the sound land on the same line and turn into mush.

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const num = (v, digits, unit = "") => (v === null || v === undefined ? "—" : `${Number(v).toFixed(digits)}${unit}`);
const known = (v) => v !== null && v !== undefined;
const short = (name, most = 22) => (name.length > most ? `${name.slice(0, most - 1)}…` : name);

const VIEW = { w: 620, h: 520 };
const PAD = 46; // room around the drawing for the ticks and the scale bar
const MID = { x: VIEW.w / 2, y: VIEW.h / 2 };

export function renderMap(map, colorOf, other) {
  const color = (id) => colorOf[id] ?? other;
  if (!map) {
    $("map-panel").innerHTML = `<p class="muted">No map yet. Run <code>scenefold map &lt;event&gt;</code>.</p>`;
    return;
  }
  const placed = map.cameras.filter((c) => c.placed && known(c.x_m));
  const solved = map.solved && placed.length > 0;

  const stats = [
    ["Phones placed", `${placed.length} of ${map.cameras.length}`],
    ["Sounds used", String(map.events_used ?? 0)],
    ["Fit", known(map.residual_ms) ? `${map.residual_ms.toFixed(1)} ms` : "—"],
    ["Head start", map.head_start_from_pictures ? "from the pictures" : "from the sounds"],
  ];
  $("map-summary").innerHTML = stats
    .map(([label, value]) => `<div class="stat"><span class="l">${label}</span><span class="v">${value}</span></div>`)
    .join("");

  // The drawing carries numbers and the list carries names, so both must number the same phones
  // in the same order: whichever cameras that drawing can show, in the order the list prints them.
  const shown = map.cameras.filter((c) => (solved ? known(c.x_m) : known(c.from_main_sound_m)));
  const pin = new Map(shown.map((camera, index) => [camera.clip_id, index + 1]));
  $("map-drawing").innerHTML = `<div class="map-wrap">
    ${solved ? plan(map, placed, color, pin) : circles(map, color, pin)}
    ${legend(map, solved, color, pin)}
  </div>`;
  $("map-note").innerHTML = solved
    ? `Distances between the phones are real; the direction is not. Sound cannot tell a map from its
       mirror image, and nothing here points north, so the whole picture may be turned or flipped.`
    : `<b>Not enough to draw a map.</b> ${esc(map.reason || "The sounds could not place the phones.")}
       Each ring is one phone's distance from whatever made most of the noise; where on its ring the
       phone stood needs sounds from somewhere else.`;
}

// The list of phones beside the drawing: colour, name, and the number that drawing is showing.
function legend(map, solved, color, pin) {
  const rows = map.cameras
    .map((camera) => {
      const value = solved
        ? known(camera.x_m)
          ? `${num(camera.x_m, 0)} m, ${num(camera.y_m, 0)} m`
          : "not placed"
        : known(camera.from_main_sound_m)
          ? `${num(camera.from_main_sound_m, 0)} m from the sound`
          : "not measured";
      const aside = solved && known(camera.uncertainty_m) ? `± ${num(camera.uncertainty_m, 0)} m` : "";
      const why = !known(camera.x_m) && camera.reason ? `<span class="why">${esc(camera.reason)}</span>` : "";
      return `<li>
        <span class="pin-no">${pin.get(camera.clip_id) ?? "·"}</span>
        <span class="dot" style="background:${color(camera.clip_id)}"></span>
        <span class="who">${esc(short(camera.name))}</span>
        <span class="what">${esc(value)}<span class="aside">${esc(aside)}</span></span>
        ${why}
      </li>`;
    })
    .join("");
  return `<ul class="map-legend">${rows}</ul>`;
}

// A top-down plan: phones as dots, the sounds they heard as small crosses, metres as a scale bar.
function plan(map, placed, color, pin) {
  const sounds = map.sounds || [];
  const xs = [...placed.map((c) => c.x_m), ...sounds.map((s) => s.x_m)];
  const ys = [...placed.map((c) => c.y_m), ...sounds.map((s) => s.y_m)];
  const span = Math.max(20, Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys));
  const scale = Math.min(VIEW.w - 2 * PAD, VIEW.h - 2 * PAD) / (span * 1.15);
  const midX = (Math.max(...xs) + Math.min(...xs)) / 2;
  const midY = (Math.max(...ys) + Math.min(...ys)) / 2;
  const px = (x) => MID.x + (x - midX) * scale;
  const py = (y) => MID.y - (y - midY) * scale; // y up, as on a map

  const soundMarks = sounds
    .map(
      (s) => `<g class="sound"><path d="M${px(s.x_m) - 5} ${py(s.y_m)}h10M${px(s.x_m)} ${py(s.y_m) - 5}v10"/>
        <title>${esc(s.label)} at ${num(s.t_master_s, 1, " s")}</title></g>`,
    )
    .join("");

  // Numbers on the dots, names in the list: a name here would sit over the next phone.
  const phones = placed
    .map((camera) => {
      const spread = known(camera.uncertainty_m) ? Math.max(5, camera.uncertainty_m * scale) : 0;
      const x = px(camera.x_m);
      const y = py(camera.y_m);
      return `<g>
        ${spread ? `<circle class="spread" cx="${x}" cy="${y}" r="${spread.toFixed(1)}" fill="${color(camera.clip_id)}"/>` : ""}
        <circle class="phone" cx="${x}" cy="${y}" r="9" fill="${color(camera.clip_id)}"/>
        <text class="pin" x="${x}" y="${y + 4}">${pin.get(camera.clip_id) ?? ""}</text>
        <title>${esc(camera.name)}: ${num(camera.x_m, 1)} m, ${num(camera.y_m, 1)} m (give or take ${num(camera.uncertainty_m, 1, " m")})</title>
      </g>`;
    })
    .join("");

  const bar = niceLength(span / 4);
  return `<svg viewBox="0 0 ${VIEW.w} ${VIEW.h}" class="map" role="img" aria-label="Where the phones stood">
    ${soundMarks}${phones}
    <g class="scale"><path d="M${PAD} ${VIEW.h - PAD}h${(bar * scale).toFixed(1)}"/>
      <text x="${PAD}" y="${VIEW.h - PAD - 10}">${bar} m</text></g>
  </svg>`;
}

// The honest fallback: how far each phone stood from the main sound, as rings around it. Each ring
// carries its distance on its own spoke, so two phones at similar distances stay readable.
function circles(map, color, pin) {
  const known_m = map.cameras.filter((c) => known(c.from_main_sound_m));
  if (!known_m.length) {
    return `<p class="muted">Sync did not measure how far any phone stood from the sound.</p>`;
  }
  const far = Math.max(...known_m.map((c) => c.from_main_sound_m), 1);
  const scale = (Math.min(VIEW.w, VIEW.h) / 2 - PAD) / far;
  const order = [...known_m].sort((a, b) => a.from_main_sound_m - b.from_main_sound_m);

  const rings = order
    .map((camera, index) => {
      const radius = Math.max(3, camera.from_main_sound_m * scale);
      // One spoke each, fanned out, so the numbers never land on the same line.
      const angle = (-65 + (130 * index) / Math.max(1, order.length - 1)) * (Math.PI / 180);
      const x = MID.x + radius * Math.sin(angle);
      const y = MID.y - radius * Math.cos(angle);
      const right = x >= MID.x;
      return `<g>
        <circle class="ring" cx="${MID.x}" cy="${MID.y}" r="${radius.toFixed(1)}" stroke="${color(camera.clip_id)}"/>
        <circle class="pin-dot" cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="9" fill="${color(camera.clip_id)}"/>
        <text class="pin" x="${x.toFixed(1)}" y="${(y + 4).toFixed(1)}">${pin.get(camera.clip_id) ?? ""}</text>
        <text class="ring-label" x="${(x + (right ? 11 : -11)).toFixed(1)}" y="${(y + 4).toFixed(1)}"
          text-anchor="${right ? "start" : "end"}">${num(camera.from_main_sound_m, 0)} m</text>
        <title>${esc(camera.name)}: about ${num(camera.from_main_sound_m, 0)} m from the sound</title>
      </g>`;
    })
    .join("");

  return `<svg viewBox="0 0 ${VIEW.w} ${VIEW.h}" class="map rings" role="img"
      aria-label="How far each phone stood from the sound">
    ${rings}
    <circle class="source" cx="${MID.x}" cy="${MID.y}" r="6"/>
    <text class="source-label" x="${MID.x}" y="${MID.y + 24}" text-anchor="middle">the sound</text>
  </svg>`;
}

// 10, 20, 50, 100... so the scale bar is a number people read at a glance.
function niceLength(metres) {
  const power = 10 ** Math.floor(Math.log10(Math.max(1, metres)));
  const steps = [1, 2, 5, 10];
  return power * (steps.find((s) => metres / power <= s) ?? 10);
}
