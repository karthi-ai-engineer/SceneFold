// The Film tab: the automatic cut, playing as one video with its shot list beside it.
//
// `scenefold cut` writes cut.json (which angle is on screen when, and why) and cut.mp4 (the film
// itself, one unbroken soundtrack with the angles cut over it). The film is an ordinary video with
// its own controls, so nothing here steers it: the page only follows it and says what it is showing.
//
// Film time runs from 0; a shot's times are on the shared clock, so the film is `start_s` behind.
//
// The shot list is read at start-up, but the film itself only once the tab is opened (startFilm):
// a browser opens few connections to one site (six in Chrome), and the clips hold all of them
// while they load, so anything asked for meanwhile waits its turn.

import { formatTime } from "./sync.js";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

let hasFilm = false; // the cut is loaded and its shots are on the page
let wanted = false; // someone has opened the tab, so the film is worth fetching

/** Load the cut and show its shot list, or say plainly that this event has no film yet. */
export async function loadFilm(timeline, colorOf, other) {
  let cut;
  try {
    const response = await fetch("/api/cut");
    const body = await response.json().catch(() => ({}));
    if (response.status === 404) {
      return note(`No film yet — run \`scenefold cut ${timeline.event_id}\`.`);
    }
    if (!response.ok) throw new Error(body.error || `/api/cut answered ${response.status}`);
    cut = body;
  } catch (error) {
    return note(`Could not load the film: ${error.message}`);
  }
  const nameOf = Object.fromEntries(timeline.clips.map((c) => [c.clip_id, c.name]));
  render(cut, nameOf, colorOf, other);
  hasFilm = true;
  if (wanted) startFilm(); // the tab was opened while this was still loading
}

/** Start loading the film itself; called whenever someone opens the tab. */
export function startFilm() {
  wanted = true;
  if (hasFilm && !$("film-video").src) $("film-video").src = "/film.mp4";
}

function note(text) {
  $("film-note").textContent = text;
  $("film-note").hidden = false;
  $("film-grid").hidden = true;
}

function render(cut, nameOf, colorOf, other) {
  const shots = cut.shots ?? [];
  const video = $("film-video");
  // Where each shot begins in the film itself, so the playhead can be turned into a shot. The
  // first one starts at 0: cut.json rounds to milliseconds, which can put it a hair before the film.
  const starts = shots.map((shot) => Math.max(0, shot.start_s - cut.start_s));
  const heard = nameOf[cut.audio_clip_id] ?? cut.audio_clip_id;

  $("film-note").hidden = true;
  $("film-grid").hidden = false;
  $("shot-count").textContent = shots.length ? `· ${shots.length}` : "";
  $("film-about").textContent =
    `${formatTime(cut.duration_s, 0)} · ${shots.length} shot${shots.length === 1 ? "" : "s"} · ` +
    `sound from ${heard} (${cut.audio_reason})`;
  $("shot-list").innerHTML = shots
    .map((shot, index) => {
      const end = shot.end_s - cut.start_s;
      return `<li><button type="button" class="shot" data-shot="${index}"
        style="--c:${colorOf[shot.clip_id] ?? other}" title="Picture score ${shot.score.toFixed(2)} of 1">
        <span class="when">${formatTime(starts[index], 0)} – ${formatTime(end, 0)}</span>
        <span class="who">${esc(shot.name)}</span>
        <span class="why">${esc(shot.reason)}</span></button></li>`;
    })
    .join("");

  let showing = -1;
  const highlight = () => {
    const at = showing;
    showing = shotAt(starts, video.currentTime);
    if (showing === at) return;
    const buttons = $("shot-list").querySelectorAll(".shot");
    buttons[at]?.removeAttribute("aria-current");
    buttons[showing]?.setAttribute("aria-current", "true");
    // a long film has more shots than fit: keep the one playing in view, without moving the page
    buttons[showing]?.scrollIntoView({ block: "nearest" });
  };
  const follow = () => {
    highlight();
    if (!video.paused && !video.ended) requestAnimationFrame(follow);
  };
  for (const event of ["loadedmetadata", "timeupdate", "seeked", "pause"]) {
    video.addEventListener(event, highlight);
  }
  video.addEventListener("play", follow);
  $("shot-list").addEventListener("click", (event) => {
    const button = event.target.closest(".shot");
    if (button) video.currentTime = starts[Number(button.dataset.shot)];
  });
  video.addEventListener("error", () => note("The film could not be played; run `scenefold cut` again."));
  highlight(); // mark the opening shot before anyone presses play
}

/** Which shot the film is showing at film time `t`: the last one that has begun. */
function shotAt(starts, t) {
  let index = 0;
  while (index + 1 < starts.length && starts[index + 1] <= t + 0.001) index += 1;
  return index;
}
