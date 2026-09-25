// Scenefold viewer: every placed clip of an event plays together on one master clock.
//
// Every video is checked on each frame it shows (requestVideoFrameCallback). The clock follows the
// frames of the clip you are listening to, so its sound is never sped up or slowed down; every
// other video is steered onto the clock: a small error by playing up to 10% faster or slower, a
// large one by jumping. Seeking pauses everything, moves every video, waits until each has
// finished, and only then plays on.

import * as sync from "./sync.js";
import { loadDisagreements } from "./disagreements.js";
import { loadPeople } from "./people.js";
import { renderReport } from "./report.js";
import { renderMap } from "./map.js";
import { loadFilm, startFilm } from "./film.js";
import { loadStory, openStory } from "./story.js";

// categorical colors in fixed order (validated palette, dark steps); a 9th clip and beyond stay grey
export const COLORS = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"];
export const OTHER = "#8f8e88";
// After a start or jump, videos begin at slightly different moments and catch up within about a
// second (measured); the health table reports how well they then stay together.
const START_GRACE_MS = 1000;
const TABS = ["viewer", "story", "report", "film", "map"];
const FIRST_LEAD_S = 0.3; // a jump while playing aims this far ahead until the clip's own is known

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

const state = {
  timeline: null,
  clips: [], // placed clips, each with its video element and error stats
  unplaced: [],
  clock: new sync.MasterClock(),
  playing: false,
  busy: false, // a seek or start is in progress
  audible: null,
  span: [0, 0],
  dragT: null, // master time under the pointer while scrubbing the lanes
  settleUntil: 0,
  playLead: 0.06, // seconds a paused video needs between play() and moving; learned
  // Sound arrives late from far away, so the pictures and the sound cannot both line up. Watching
  // several clips at once asks for the pictures; the sound stays a click away for listening.
  align: sync.SOUND,
};

// Every conversion between the clock and a clip goes through the alignment in force, so no part of
// the viewer can be left lining up on the other one.
const localTime = (clip, t) => sync.localTime(clip, t, state.align);
const masterTime = (clip, t) => sync.masterTime(clip, t, state.align);
const recording = (clip, t) => sync.recording(clip, t, state.align);

async function fetchJson(url) {
  const response = await fetch(url);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.error || `${url} answered ${response.status}`);
  return body;
}

function makeClip(placement, color) {
  const video = document.createElement("video");
  Object.assign(video, { preload: "auto", muted: true, playsInline: true, disablePictureInPicture: true });
  video.src = `/media/${placement.clip_id}.mp4`;

  const tile = document.createElement("div");
  tile.className = "tile";
  tile.style.setProperty("--c", color);
  tile.innerHTML = `
    <div class="label" title="${esc(placement.name)}"><i></i><span>${esc(placement.name)}</span></div>
    <div class="badge" title="How far this picture is from the shared clock, last frame">—</div>
    <button class="speaker" type="button" title="Listen to this clip">Sound</button>
    <div class="idle" hidden></div>`;
  tile.prepend(video);
  const clip = {
    ...placement, color, video, tile,
    stats: new sync.ErrorStats(300), steer: new sync.Steering(), starting: false, seekLead: FIRST_LEAD_S,
  };
  clip.badge = tile.querySelector(".badge");
  clip.idle = tile.querySelector(".idle");
  tile.querySelector(".speaker").addEventListener("click", () => setAudio(clip.clip_id));
  video.addEventListener("error", () => { clip.idle.hidden = false; clip.idle.textContent = "This clip's working copy could not be played."; });
  return clip;
}

function waitReady(video, timeoutMs = 15000) {
  return new Promise((resolve) => {
    if (video.readyState >= 2) return resolve();
    const done = () => { video.removeEventListener("loadeddata", done); clearTimeout(timer); resolve(); };
    const timer = setTimeout(done, timeoutMs);
    video.addEventListener("loadeddata", done);
  });
}

/* ---------------- keeping every picture on the clock ---------------- */

function watchFrames(clip) {
  if (!("requestVideoFrameCallback" in HTMLVideoElement.prototype)) return; // tick() steers instead
  const onFrame = (_now, meta) => {
    measure(clip, meta.mediaTime, meta.expectedDisplayTime);
    clip.video.requestVideoFrameCallback(onFrame);
  };
  clip.video.requestVideoFrameCallback(onFrame);
}

/** A frame at clip time `shown` appears at performance time `at`: log the error, steer the video. */
function measure(clip, shown, at) {
  if (!state.playing || state.busy || clip.video.paused || clip.starting) return;
  const tMaster = state.clock.time(at);
  if (!recording(clip, tMaster)) return;
  const error = shown - localTime(clip, tMaster);
  if (clip.joining) {
    clip.joining = false; // first frame after an early start: late means press play earlier next time
    state.playLead = clamp(state.playLead - 0.5 * error, 0, 0.4);
  }
  if (at >= Math.max(state.settleUntil, clip.settleUntil ?? 0)) clip.stats.add(error * 1000);
  if (clip === state.audible) {
    // The clock follows the frames this clip shows, so its sound is never sped up or slowed down
    // and every picture is judged the same way. If it stalled, bring it back instead.
    if (Math.abs(error) > sync.SEEK_S) jump(clip);
    else state.clock.pull(error / sync.rateOf(clip), 0.1);
    return;
  }
  const fix = clip.steer.update(error, sync.rateOf(clip), state.clock.speed);
  if (fix.seek) jump(clip);
  else if (clip.video.playbackRate !== fix.rate) clip.video.playbackRate = fix.rate;
}

/** Jump a playing video to where the clock will be once the jump is done (learned per clip). */
function jump(clip) {
  const video = clip.video, started = performance.now();
  clip.stats.seeks += 1;
  clip.steer.reset();
  video.addEventListener("seeked", () => {
    const took = (performance.now() - started) / 1000;
    clip.seekLead = clamp(0.6 * clip.seekLead + 0.4 * took, 0.05, 1.5);
  }, { once: true });
  video.currentTime = localTime(clip, state.clock.time() + clip.seekLead * state.clock.speed);
}


/** A clip whose recording is about to begin, waiting on its first frame: start it now. */
function join(clip) {
  clip.starting = true;
  clip.joining = true;
  clip.settleUntil = performance.now() + START_GRACE_MS;
  clip.steer.reset();
  clip.video.playbackRate = sync.rateOf(clip) * state.clock.speed;
  clip.video.play().catch(() => {}).finally(() => { clip.starting = false; });
}

/** A clip that should be playing but isn't (after a stall, or a join missed): start it on the clock. */
function startClip(clip, t) {
  clip.starting = true;
  clip.settleUntil = performance.now() + START_GRACE_MS;
  clip.steer.reset();
  clip.video.playbackRate = sync.rateOf(clip) * state.clock.speed;
  // usually it already waits on the right frame; if not, jump to where the clock will be
  if (Math.abs(clip.video.currentTime - localTime(clip, t)) > 0.2) {
    clip.video.currentTime = localTime(clip, t + clip.seekLead * state.clock.speed);
  }
  clip.video.play().catch(() => {}).finally(() => { clip.starting = false; });
}

let lastPanel = 0;
function tick(now) {
  const t = state.clock.time(now);
  if (state.playing && t >= state.span[1]) {
    pause();
  } else {
    for (const clip of state.clips) {
      const on = recording(clip, t);
      clip.idle.hidden = on || clip.video.error !== null;
      if (!on) {
        const local = localTime(clip, t);
        // a paused video takes a moment to get going: press play that much before its recording
        // begins (state.playLead is learned from how far off each such start lands)
        if (state.playing && !state.busy && local < 0 && -local <= state.playLead * state.clock.speed) {
          if (clip.video.paused && !clip.starting) join(clip);
          continue;
        }
        if (!clip.video.paused) clip.video.pause();
        clip.idle.textContent = local < 0 ? `Starts recording in ${(-local).toFixed(1)} s` : "Stopped recording";
        // about to start: wait on the first frame, so it can simply play when its moment comes
        if (local < 0 && local > -3 && clip.video.currentTime > 0.001 && !clip.video.seeking) {
          clip.video.currentTime = 0;
        }
        continue;
      }
      if (state.playing && !state.busy && clip.video.paused && !clip.starting) startClip(clip, t);
      if (!("requestVideoFrameCallback" in HTMLVideoElement.prototype) && !clip.video.paused) {
        measure(clip, clip.video.currentTime, now);
      }
    }
  }
  $("time").textContent = sync.formatTime(state.dragT ?? state.clock.time(now));
  drawLanes(now);
  if (now - lastPanel > 250) { lastPanel = now; updateHealth(); }
  requestAnimationFrame(tick);
}

/* ---------------- playing, pausing, seeking ---------------- */

async function play() {
  if (state.playing || state.busy || !state.clips.length) return;
  if (state.clock.time() >= state.span[1] - 0.05) await seekTo(state.span[0]);
  state.busy = true;
  const t = state.clock.time();
  const starting = state.clips.filter((clip) => recording(clip, t));
  for (const clip of state.clips) {
    clip.steer.reset();
    clip.video.playbackRate = sync.rateOf(clip) * state.clock.speed;
  }
  // Sound takes a moment to start (up to ~0.7 s measured), and the clock follows it, so the clip
  // being heard starts first and the pictures follow once its time is really moving.
  const heard = state.audible && starting.includes(state.audible) ? state.audible : null;
  if (heard) {
    await heard.video.play().catch(() => {});
    await moving(heard.video);
  }
  await Promise.allSettled(starting.filter((clip) => clip !== heard).map((clip) => clip.video.play()));
  state.clock.set(heard ? masterTime(heard, heard.video.currentTime) : t);
  state.clock.play();
  state.settleUntil = performance.now() + START_GRACE_MS;
  state.playing = true;
  state.busy = false;
  setPlayIcon();
}

/** Resolves once a playing video's time has started to advance (or after 2 s). */
function moving(video) {
  const from = video.currentTime, until = performance.now() + 2000;
  return new Promise((resolve) => {
    const check = () => (video.currentTime > from + 0.001 || performance.now() > until ? resolve() : requestAnimationFrame(check));
    check();
  });
}

async function pause() {
  if (!state.playing) return;
  state.playing = false;
  state.clock.pause();
  for (const clip of state.clips) clip.video.pause();
  setPlayIcon();
  await seekTo(state.clock.time()); // line every picture up exactly on the paused moment
}

/** Move every video to master time t; resolves once all of them have finished seeking. */
async function seekTo(t, { resume = false } = {}) {
  const target = clamp(t, state.span[0], state.span[1]);
  const wasPlaying = state.playing || resume;
  state.busy = true;
  state.playing = false;
  state.clock.pause();
  state.clock.set(target);
  for (const clip of state.clips) {
    clip.video.pause();
    clip.stats.reset(); // the health table starts over at every jump
  }
  await Promise.all(state.clips.map((clip) => seekVideo(clip, target)));
  state.busy = false;
  setPlayIcon();
  if (wasPlaying) await play();
}

function seekVideo(clip, t) {
  const local = clamp(localTime(clip, t), 0, Math.max(0, clip.duration_s - 0.04));
  const video = clip.video;
  return new Promise((resolve) => {
    if (Math.abs(video.currentTime - local) < 0.0005 && !video.seeking) return resolve();
    const done = () => { video.removeEventListener("seeked", done); clearTimeout(timer); resolve(); };
    const timer = setTimeout(done, 4000);
    video.addEventListener("seeked", done);
    video.currentTime = local;
  });
}

async function step(frames) {
  if (state.playing) await pause();
  await seekTo(state.clock.time() + frames * sync.FRAME_S);
}

function setSpeed(speed) {
  state.clock.setSpeed(speed);
  for (const clip of state.clips) clip.video.playbackRate = sync.rateOf(clip) * speed;
  $("speed").value = String(speed);
}

/** Line the clips up by their pictures, or by the moment each phone heard the event. */
async function setAlign(align) {
  if (align === state.align) return;
  state.align = align;
  state.span = sync.span(state.clips, align);
  $("align").value = align;
  $("span").textContent = `/ ${sync.formatTime(state.span[1], 1)}`;
  // Every video now belongs at a slightly different frame, so move them all the usual way: seekTo
  // puts each one in place and plays on if it was playing.
  await seekTo(state.clock.time());
  toast(align === sync.PICTURES ? "Lined up on what the phones saw" : "Lined up on what the phones heard");
}

function setAudio(clipId) {
  state.audible = state.clips.find((clip) => clip.clip_id === clipId) ?? null;
  for (const clip of state.clips) {
    clip.video.muted = clip !== state.audible;
    clip.tile.classList.toggle("audible", clip === state.audible);
    if (clip !== state.audible) clip.video.playbackRate = sync.rateOf(clip) * state.clock.speed;
  }
  $("audio").value = clipId ?? "";
  toast(state.audible ? `Sound from ${state.audible.name}` : "Sound off");
}

function setPlayIcon() {
  $("play-icon").setAttribute("d", state.playing ? "M7 5h4v14H7zM13 5h4v14h-4z" : "M8 5v14l11-7z");
  $("play").setAttribute("aria-label", state.playing ? "Pause" : "Play");
}

let toastTimer = 0;
function toast(text) {
  $("toast").textContent = text;
  $("toast").hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { $("toast").hidden = true; }, 1600);
}

/* ---------------- coverage lanes ---------------- */

const LANE_H = 22, AXIS_H = 22, NAME_W = 150;

function lanesGeometry() {
  const canvas = $("lanes");
  const width = canvas.clientWidth;
  const [start, end] = state.span;
  const pad = (end - start) * 0.01 || 1;
  const x0 = NAME_W, x1 = width - 8;
  const X = (t) => x0 + ((t - (start - pad)) / (end - start + 2 * pad)) * (x1 - x0);
  const T = (x) => start - pad + ((x - x0) / (x1 - x0)) * (end - start + 2 * pad);
  return { canvas, width, X, T, x0, x1 };
}

function drawLanes(now) {
  const { canvas, width, X, x0, x1 } = lanesGeometry();
  const height = AXIS_H + LANE_H * Math.max(1, state.clips.length) + 6;
  const dpr = Math.min(2, window.devicePixelRatio || 1);
  if (canvas.width !== Math.round(width * dpr) || canvas.height !== Math.round(height * dpr)) {
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    canvas.style.height = `${height}px`;
  }
  const g = canvas.getContext("2d");
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, width, height);
  g.font = '12px "IBM Plex Sans", sans-serif';
  g.textBaseline = "middle";

  const [start, end] = state.span;
  const step = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600].find((s) => (end - start) / s <= Math.max(2, (x1 - x0) / 90)) || 600;
  g.strokeStyle = "#2b2c31";
  g.fillStyle = "#8f8e88";
  g.textAlign = "center";
  for (let t = Math.ceil(start / step) * step; t <= end; t += step) {
    g.beginPath(); g.moveTo(X(t), AXIS_H - 4); g.lineTo(X(t), height); g.stroke();
    g.fillText(sync.formatTime(t, 0), X(t), AXIS_H / 2);
  }
  state.clips.forEach((clip, i) => {
    const y = AXIS_H + i * LANE_H;
    g.fillStyle = "#1f2024";
    g.fillRect(x0, y + 3, x1 - x0, LANE_H - 6);
    g.fillStyle = clip.color;
    g.globalAlpha = clip === state.audible ? 1 : 0.8;
    const a = X(masterTime(clip, 0)), b = X(masterTime(clip, clip.duration_s));
    g.beginPath(); g.roundRect(a, y + 3, Math.max(2, b - a), LANE_H - 6, 4); g.fill();
    g.globalAlpha = 1;
    g.fillStyle = "#c3c2b7"; g.textAlign = "left";
    g.fillText(fit(g, clip.name, NAME_W - 14), 4, y + LANE_H / 2);
  });
  const t = state.dragT ?? state.clock.time(now);
  g.strokeStyle = "#9085e9"; g.lineWidth = 2;
  g.beginPath(); g.moveTo(X(t), 2); g.lineTo(X(t), height); g.stroke();
  g.lineWidth = 1;
}

function fit(g, text, width) {
  if (g.measureText(text).width <= width) return text;
  let s = text;
  while (s.length > 3 && g.measureText(s + "…").width > width) s = s.slice(0, -1);
  return s + "…";
}

function bindLanes() {
  const canvas = $("lanes");
  const timeAt = (event) => {
    const { T, x0, x1 } = lanesGeometry();
    const x = event.clientX - canvas.getBoundingClientRect().left;
    return clamp(T(clamp(x, x0, x1)), state.span[0], state.span[1]);
  };
  canvas.addEventListener("pointerdown", (event) => {
    canvas.setPointerCapture(event.pointerId);
    state.dragT = timeAt(event);
  });
  canvas.addEventListener("pointermove", (event) => { if (state.dragT !== null) state.dragT = timeAt(event); });
  const release = async (event) => {
    if (state.dragT === null) return;
    const t = timeAt(event);
    state.dragT = null;
    await seekTo(t);
  };
  canvas.addEventListener("pointerup", release);
  canvas.addEventListener("pointercancel", () => { state.dragT = null; });
}

/* ---------------- sync health ---------------- */

const FRAME_MS = sync.FRAME_S * 1000;
const level = (ms) => (ms === null ? "" : ms <= FRAME_MS / 2 ? "good" : ms <= FRAME_MS ? "warning" : "critical");
const ms = (v) => (v === null || v === undefined ? "—" : `${v.toFixed(1)} ms`);

function updateHealth() {
  const t = state.clock.time();
  const rows = state.clips.map((clip) => {
    const s = clip.stats.summary();
    const on = recording(clip, t);
    clip.badge.className = `badge ${on && s.last !== null ? level(Math.abs(s.last)) : ""}`;
    clip.badge.textContent = on && s.last !== null ? `${s.last >= 0 ? "+" : "−"}${Math.abs(s.last).toFixed(0)} ms` : "—";
    const verdict = s.p95Abs === null ? "" : `<span class="pill ${level(s.p95Abs)}">${s.p95Abs <= FRAME_MS ? "within a frame" : "off by over a frame"}</span>`;
    return `<tr><td><span class="dot" style="background:${clip.color}"></span>${esc(clip.name)}${clip === state.audible ? ' <span class="muted">(sound, sets the clock)</span>' : ""}</td>
      <td class="num">${s.frames}</td><td class="num">${ms(s.meanAbs)}</td><td class="num">${ms(s.p95Abs)}</td><td class="num">${ms(s.maxAbs)}</td>
      <td class="num">${s.seeks}</td><td class="num">${((clip.video.playbackRate / state.clock.speed - 1) * 100).toFixed(2)}%</td><td>${verdict}</td></tr>`;
  });
  $("health-table").innerHTML = `<thead><tr><th>Clip</th><th class="num">Frames</th><th class="num">Mean error</th><th class="num">95% under</th><th class="num">Worst</th><th class="num">Jumps</th><th class="num">Speed nudge</th><th>Last 10 s</th></tr></thead><tbody>${rows.join("")}</tbody>`;
}

/* ---------------- start up ---------------- */

/** Show one tab and hide the others; a sentence in the account uses it to get back to the clips. */
function showTab(tab) {
  for (const other of TABS) {
    $(other).hidden = other !== tab;
    $(`tab-${other}`).setAttribute("aria-selected", String(other === tab));
  }
  // one sound at a time: the film and the clips both have some
  if (tab === "film") {
    pause();
    startFilm(); // the film itself is only fetched once someone wants to watch it
  } else {
    $("film-video").pause();
  }
  if (tab === "story") openStory(); // likewise, why the clips disagreed is asked for only now
}

function bindControls() {
  $("play").addEventListener("click", () => (state.playing ? pause() : play()));
  $("back-frame").addEventListener("click", () => step(-1));
  $("next-frame").addEventListener("click", () => step(1));
  $("back-5").addEventListener("click", () => seekTo(state.clock.time() - 5));
  $("next-5").addEventListener("click", () => seekTo(state.clock.time() + 5));
  $("speed").addEventListener("change", (event) => setSpeed(Number(event.target.value)));
  $("audio").addEventListener("change", (event) => setAudio(event.target.value || null));
  $("align").addEventListener("change", (event) => setAlign(event.target.value));
  for (const tab of TABS) $(`tab-${tab}`).addEventListener("click", () => showTab(tab));
  document.addEventListener("keydown", (event) => {
    if (event.target.closest("select, input, textarea") || event.ctrlKey || event.metaKey || event.altKey) return;
    if (!$("film").hidden) return; // the film has its own controls; these keys drive the clips
    const keys = {
      " ": () => (state.playing ? pause() : play()),
      ArrowLeft: () => seekTo(state.clock.time() - 5),
      ArrowRight: () => seekTo(state.clock.time() + 5),
      ",": () => step(-1),
      ".": () => step(1),
    };
    if (keys[event.key]) { event.preventDefault(); keys[event.key](); return; }
    const n = Number(event.key);
    if (n >= 1 && n <= state.clips.length) setAudio(state.clips[n - 1].clip_id);
  });
  bindLanes();
}

async function boot() {
  let timeline;
  try {
    timeline = await fetchJson("/api/timeline");
  } catch (error) {
    $("event-name").textContent = "Could not load this event";
    $("tiles").innerHTML = `<p class="muted">${esc(error.message)}. Run <code>scenefold sync</code>, then <code>scenefold view</code> again.</p>`;
    return;
  }
  state.timeline = timeline;
  const placed = timeline.clips.filter((c) => c.placed);
  const colorOf = Object.fromEntries(placed.map((c, i) => [c.clip_id, COLORS[i] ?? OTHER]));
  // Ask for the shot list before the clips' videos do, because they hold every connection the
  // browser allows to one site (six in Chrome) for as long as they are loading.
  loadFilm(timeline, colorOf, OTHER);
  // Every sentence of the account is checkable in one click: it takes the reader to that moment.
  loadStory(timeline, colorOf, OTHER, (t) => {
    showTab("viewer");
    seekTo(t);
    toast(`Jumped to ${sync.formatTime(t, 1)}`);
  });
  state.clips = placed.map((c) => makeClip(c, colorOf[c.clip_id]));
  state.unplaced = timeline.clips.filter((c) => !c.placed);
  // Watching several clips is the whole point of this page, so start on the pictures whenever sync
  // worked out how late each phone heard the event; otherwise there is nothing to choose between.
  const knowsDistance = state.clips.some((c) => (c.heard_late_s ?? null) !== null);
  state.align = knowsDistance ? sync.PICTURES : sync.SOUND;
  state.span = sync.span(state.clips, state.align);

  document.title = `${timeline.event_id} · Scenefold Viewer`;
  $("event-name").textContent = timeline.event_id;
  $("event-meta").textContent = `${state.clips.length} of ${timeline.clips.length} clips on one clock · ${sync.formatTime(state.span[1] - state.span[0], 1)}`;
  $("span").textContent = `/ ${sync.formatTime(state.span[1], 1)}`;
  $("tiles").replaceChildren(...state.clips.map((clip) => clip.tile));
  $("audio").innerHTML = `<option value="">No sound</option>` + state.clips.map((c, i) => `<option value="${c.clip_id}">${i + 1}. ${esc(c.name)}</option>`).join("");
  $("align").value = state.align;
  $("align").disabled = !knowsDistance;
  if (!knowsDistance) {
    $("align-label").title = "The pictures of these clips never matched clearly enough to tell how far each phone stood from the sound, so they can only be lined up on what the phones heard.";
  }
  $("unplaced").textContent = state.unplaced.length ? `Not on the clock: ${state.unplaced.map((c) => `${c.name} (${c.reason})`).join("; ")}.` : "";
  if (!state.clips.length) {
    $("tiles").innerHTML = `<p class="muted">No clip could be placed on the clock. See the sync report.</p>`;
  }
  renderReport(timeline, colorOf, OTHER);
  // The map is optional: an event only has one once `scenefold map` has run.
  fetchJson("/api/positions")
    .then((positions) => renderMap(positions, colorOf, OTHER))
    .catch(() => renderMap(null, colorOf, OTHER));
  const watchAt = (t) => {
    showTab("viewer");
    seekTo(t);
    toast(`Jumped to ${sync.formatTime(t, 1)}`);
  };
  // Where the footage argues with itself, and who was matched across the angles. Both answer the
  // same way: by taking you to the second in question.
  loadDisagreements(timeline, colorOf, OTHER, watchAt);
  loadPeople(timeline, colorOf, OTHER, watchAt);
  bindControls();

  await Promise.all(state.clips.map((clip) => waitReady(clip.video)));
  state.clips.forEach(watchFrames);
  const longest = [...state.clips].sort((a, b) => b.duration_s - a.duration_s)[0];
  if (longest) setAudio(longest.clip_id);
  await seekTo(state.span[0]);
  requestAnimationFrame(tick);
}

// A small handle for automated checks (tools/check_viewer.py) and the browser console.
const ready = boot();
window.scenefold = {
  ready,
  play,
  pause,
  seek: (t) => seekTo(t),
  step,
  setSpeed,
  setAudio,
  setAlign,
  align: () => state.align,
  time: () => state.clock.time(),
  span: () => [...state.span],
  playing: () => state.playing,
  resetStats: () => state.clips.forEach((clip) => clip.stats.reset()),
  // after a seek, while paused: how far each recording video sits from where the clock wants it
  positions: () => {
    const t = state.clock.time();
    return state.clips.filter((clip) => recording(clip, t)).map((clip) => ({
      clip_id: clip.clip_id, name: clip.name,
      error_ms: (clip.video.currentTime - localTime(clip, t)) * 1000,
    }));
  },
  stats: () => {
    const t = state.clock.time();
    return state.clips.map((clip) => ({
      clip_id: clip.clip_id, name: clip.name, audible: clip === state.audible,
      recording: recording(clip, t), rate: clip.video.playbackRate, ...clip.stats.summary(),
    }));
  },
};
