// The sync report: which clips were placed and why, and every pair measurement behind it.

import { REJECTED, formatTime } from "./sync.js";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const num = (v, digits, unit = "") => (v === null || v === undefined ? "—" : `${Number(v).toFixed(digits)}${unit}`);
const signedNum = (v, digits, unit = "") => (v === null || v === undefined ? "—" : `${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(digits)}${unit}`);

const SOUND_M_PER_S = 343; // how far sound travels in a second, so how far a late clip stood away
const CLEAR_ENOUGH = 4; // a picture match below this never stood clear of far-away lags
const known = (v) => v !== null && v !== undefined;

export function renderReport(timeline, colorOf, other) {
  const clips = timeline.clips;
  const nameOf = Object.fromEntries(clips.map((c) => [c.clip_id, c.name]));
  const color = (id) => colorOf[id] ?? other;
  const placed = clips.filter((c) => c.placed);
  const used = timeline.pairs.filter((p) => p.used);
  const confidences = used.map((p) => p.confidence).sort((a, b) => a - b);

  // The phone that heard the event last stood furthest from the sound; sound needs 2.9 ms a metre.
  const latest = Math.max(0, ...placed.filter((c) => known(c.heard_late_s)).map((c) => c.heard_late_s));
  const stats = [
    ["Clips on one clock", `${placed.length} of ${clips.length}`],
    ["Pairs used", `${used.length} of ${timeline.pairs.length}`],
    ["Median confidence", confidences.length ? confidences[confidences.length >> 1].toFixed(1) : "—"],
    ["Shared timeline", formatTime(timeline.duration_s, 1)],
  ];
  if (latest > 0) stats.push(["Furthest from the sound", `${(latest * 1000).toFixed(0)} ms · ${(latest * SOUND_M_PER_S).toFixed(0)} m`]);
  $("report-summary").innerHTML = stats
    .map(([label, value]) => `<div class="stat"><span class="l">${label}</span><span class="v">${value}</span></div>`).join("");

  $("clip-table").innerHTML = `<thead><tr><th>Clip</th><th class="num">Starts at</th><th class="num">Drift</th>
    <th class="num" title="How much later than the nearest clip this phone heard the event. The sound places its pictures that much late, so the viewer takes it off again.">Heard late</th>
    <th class="num" title="About how much further from the sound this phone stood, from how much later it heard it (sound travels 343 m a second).">Further away</th>
    <th class="num">Confidence</th><th class="num">Length</th><th>Status</th></tr></thead><tbody>${clips.map((c) => `
    <tr><td><span class="dot" style="background:${color(c.clip_id)}"></span>${esc(c.name)}</td>
    <td class="num">${c.placed ? formatTime(c.offset_s) : "—"}</td>
    <td class="num">${c.placed && c.drift_ppm !== null ? signedNum(c.drift_ppm, 1, " ppm") : "—"}</td>
    <td class="num">${signedNum(known(c.heard_late_s) ? c.heard_late_s * 1000 : null, 0, " ms")}</td>
    <td class="num">${num(known(c.heard_late_s) ? c.heard_late_s * SOUND_M_PER_S : null, 0, " m")}</td>
    <td class="num">${num(c.confidence, 1)}</td><td class="num">${num(c.duration_s, 1, " s")}</td>
    <td>${c.placed ? '<span class="pill good">placed</span>' : `<span class="pill critical">not placed</span> <span class="muted">${esc(c.reason ?? "")}</span>`}</td></tr>`).join("")}</tbody>`;

  const agreement = (p) => (p.windows ? `${Math.round((p.agreement ?? 0) * p.windows)} of ${p.windows}` : "—");
  // A pair is measured twice: on its sound, and again on the brightness of its pictures. The two
  // disagree by however much further one phone stood from the sound.
  const pictureMatch = (p) =>
    !known(p.picture_lag_s) ? "—"
    : p.picture_used ? '<span class="pill good">used</span>'
    : `<span class="pill warning">${(p.picture_clearness ?? 0) < CLEAR_ENOUGH ? "too vague to believe" : "set aside"}</span>`;
  $("pair-table").innerHTML = `<thead><tr><th>Clip A</th><th>Clip B</th><th class="num" title="Where B's time 0 sits on A's clock by the sound, which is what the clips are placed on">Sound puts B at</th><th class="num">Confidence</th><th class="num" title="10 s windows whose sound matches at this lag">Agreement</th><th class="num">Overlap</th><th class="num">Drift of B</th><th class="num">Residual</th><th>Status</th>
    <th class="num" title="Where B's time 0 sits on A's clock by the pictures alone, matching how their brightness changes">Pictures put B at</th>
    <th class="num" title="How far the picture match stood above lags more than 5 s away; ${CLEAR_ENOUGH} is the least that is believed">Clearness</th>
    <th class="num" title="How much later than the sound the pictures put B: about 2.9 ms for every extra metre B stood from the sound">Pictures later by</th>
    <th title="Whether this picture match fed the heard-late times in the clip table">Picture match</th></tr></thead><tbody>${timeline.pairs.map((p) => `
    <tr><td class="name" title="${esc(nameOf[p.clip_a])}"><span class="dot" style="background:${color(p.clip_a)}"></span>${esc(nameOf[p.clip_a])}</td>
    <td class="name" title="${esc(nameOf[p.clip_b])}"><span class="dot" style="background:${color(p.clip_b)}"></span>${esc(nameOf[p.clip_b])}</td>
    <td class="num">${p.lag_s === null ? "—" : signedNum(p.lag_s, 3, " s")}</td><td class="num">${num(p.confidence, 1)}</td>
    <td class="num">${agreement(p)}</td>
    <td class="num">${num(p.overlap_s, 1, " s")}</td><td class="num">${p.drift_ppm === null ? "—" : signedNum(p.drift_ppm, 1, " ppm")}</td>
    <td class="num">${p.residual_ms === null ? "—" : signedNum(p.residual_ms, 2, " ms")}</td>
    <td>${p.used ? '<span class="pill good">used</span>' : `<span class="pill ${p.rejected === "inconsistent" ? "critical" : "warning"}">${esc(REJECTED[p.rejected] ?? p.rejected ?? "not used")}</span>`}</td>
    <td class="num">${signedNum(p.picture_lag_s, 3, " s")}</td>
    <td class="num">${num(p.picture_clearness, 1)}</td>
    <td class="num">${signedNum(p.picture_difference_ms, 1, " ms")}</td>
    <td>${pictureMatch(p)}</td></tr>`).join("")}</tbody>`;

  drawGraph(timeline, color, nameOf);
}

function drawGraph(timeline, color, nameOf) {
  const clips = timeline.clips, cx = 260, cy = 200, radius = clips.length > 1 ? 150 : 0;
  const at = Object.fromEntries(clips.map((c, i) => {
    const angle = -Math.PI / 2 + (2 * Math.PI * i) / Math.max(1, clips.length);
    return [c.clip_id, [cx + radius * Math.cos(angle), cy + radius * Math.sin(angle)]];
  }));
  const stroke = (p) => (p.used ? "#0ca30c" : p.rejected === "inconsistent" ? "#d03b3b" : "#55554f");
  let svg = "";
  for (const p of timeline.pairs) {
    const [x1, y1] = at[p.clip_a], [x2, y2] = at[p.clip_b];
    const dash = p.used || p.rejected === "inconsistent" ? "" : ' stroke-dasharray="5 5"';
    const title = `${nameOf[p.clip_a]} – ${nameOf[p.clip_b]}: ${p.used ? "used" : REJECTED[p.rejected] ?? "not used"}, confidence ${num(p.confidence, 1)}`;
    svg += `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${stroke(p)}" stroke-width="${p.used ? 2.5 : 1.5}"${dash}><title>${esc(title)}</title></line>`;
  }
  clips.forEach((c, i) => {
    const [x, y] = at[c.clip_id];
    const fill = c.placed ? color(c.clip_id) : "transparent";
    svg += `<circle cx="${x}" cy="${y}" r="17" fill="${fill}" stroke="${c.placed ? "#0f1012" : "#8f8e88"}" stroke-width="3"><title>${esc(c.name)}</title></circle>`;
    svg += `<text x="${x}" y="${y + 4}" text-anchor="middle" style="font-weight:600;fill:${c.placed ? "#fff" : "#8f8e88"}">${i + 1}</text>`;
    const label = c.name.length > 22 ? `${c.name.slice(0, 20)}…` : c.name;
    svg += `<text x="${x}" y="${y + 33}" text-anchor="middle" style="font-size:11px">${esc(label)}</text>`;
  });
  $("pair-graph").innerHTML = svg;
}
