// The "What happened" tab: the account of the event, with the footage behind every sentence.
//
// `scenefold story` writes story.json — sentences in order, each carrying the moments it rests on,
// the clips those moments were seen in, and whether the clips disagreed about that moment. The page
// writes nothing and decides nothing: it shows what survived the citation check, and what did not.
//
// Clicking a sentence is the point of the tab. It switches to the Viewer and seeks to the moment,
// so a reader checks a claim against the footage instead of taking its word for it.
//
// Why the clips disagreed is not in story.json but in the event store, so it is asked for from
// /api/events — a second around each disputed sentence, and only once someone opens the tab,
// because the clips hold every connection the browser allows to one site while they load.

import { formatTime } from "./sync.js";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

const NEARBY_S = 0.5; // how far either side of a sentence its own moments are looked for

let disputed = []; // the sentences whose moments the clips disagreed about, waiting for the reason
let asked = false; // those reasons have been asked for once; never ask twice

/** Show the account, or say plainly that this event has none yet. */
export async function loadStory(timeline, colorOf, other, jumpTo) {
  let story;
  try {
    const response = await fetch("/api/story");
    const body = await response.json().catch(() => ({}));
    if (response.status === 404) {
      return note(`No account yet — run \`scenefold story ${timeline.event_id}\`.`);
    }
    if (!response.ok) throw new Error(body.error || `/api/story answered ${response.status}`);
    story = body;
  } catch (error) {
    return note(`Could not load the account: ${error.message}`);
  }
  const nameOf = Object.fromEntries(timeline.clips.map((c) => [c.clip_id, c.name]));
  render(story, nameOf, (id) => colorOf[id] ?? other, jumpTo);
}

/** Someone opened the tab: fill in how the clips disagreed, where they did. */
export function openStory() {
  if (asked || !disputed.length) return;
  asked = true;
  explain();
}

/** A sentence with its citation marks made into things a reader can click. */
function marked(line, index) {
  const times = line.cite_times_s ?? [];
  return esc(line.text).replace(/\[(\d+)\]/g, (whole, number) => {
    const at = times[Number(number) - 1];
    if (at === undefined) return whole; // an older story without the times: leave the mark alone
    return `<span class="cite" role="button" tabindex="0" data-line="${index}" data-mark="${number}"
      title="Watch ${formatTime(at, 1)}, the moment this rests on">${whole}</span>`;
  });
}

function note(text) {
  $("story-note").textContent = text;
  $("story-note").hidden = false;
  $("story-wrap").hidden = true;
}

function render(story, nameOf, colorOf, jumpTo) {
  const lines = story.lines ?? [];
  const dropped = story.dropped ?? [];
  $("story-note").hidden = true;
  $("story-wrap").hidden = false;

  const written = new Date(story.created_at);
  const when = Number.isNaN(written.getTime()) ? "" : `, ${written.toLocaleDateString()}`;
  $("story-about").textContent = lines.length
    ? `${lines.length} sentence${lines.length === 1 ? "" : "s"}, each one kept only because a clip ` +
      `filmed it · click a sentence to watch that moment · written by ${story.model}${when}`
    : `Nothing the footage supports was left · written by ${story.model}${when}`;

  $("story-lines").innerHTML = lines
    .map((line, index) => {
      const cites = line.cites ?? [];
      const clips = (line.clips ?? [])
        .map((id) => `<span class="who" style="--c:${colorOf(id)}" title="${esc(nameOf[id] ?? id)}">
          <i></i><span>${esc(nameOf[id] ?? id)}</span></span>`)
        .join("");
      const flag = line.disputed
        ? `<span class="pill warning" title="The clips that caught this moment do not agree about it.">clips disagreed</span>`
        : "";
      const moments = `${cites.length} moment${cites.length === 1 ? "" : "s"} in the store`;
      return `<li><button type="button" class="line" data-line="${index}"
        title="Watch ${formatTime(line.t_master_s, 1)} in the viewer">
        <span class="when mono">${formatTime(line.t_master_s, 1)}</span>
        <span class="said">${marked(line, index)}</span>
        <span class="behind"><span class="muted small" title="${esc(cites.join(", "))}">${moments}:</span>${clips}${flag}</span>
        <span class="why muted small" data-why="${index}" hidden></span>
      </button></li>`;
    })
    .join("");

  $("story-lines").addEventListener("click", (event) => {
    // A mark inside a sentence points at its own moment, which is often not where the sentence
    // starts: "the screens showed the band [1] and later the crowd [2]" is two different times.
    const cite = event.target.closest(".cite");
    if (cite) {
      const line = lines[Number(cite.dataset.line)];
      const at = (line.cite_times_s ?? [])[Number(cite.dataset.mark) - 1];
      jumpTo(at ?? line.t_master_s);
      return;
    }
    const button = event.target.closest(".line");
    if (button) jumpTo(lines[Number(button.dataset.line)].t_master_s);
  });

  $("story-dropped").hidden = !dropped.length;
  $("dropped-count").textContent = dropped.length ? `· ${dropped.length}` : "";
  $("dropped-list").innerHTML = dropped.map((reason) => `<li>${esc(reason)}</li>`).join("");

  disputed = lines
    .map((line, index) => ({ index, t: line.t_master_s, cites: line.cites ?? [] }))
    .filter(({ index }) => lines[index].disputed);
  if (!$("story").hidden) openStory(); // the tab was already open while this loaded
}

/** Ask the event store why the clips disagreed, one sentence at a time. */
async function explain() {
  for (const line of disputed) {
    let events;
    try {
      const response = await fetch(`/api/events?from=${line.t - NEARBY_S}&to=${line.t + NEARBY_S}`);
      if (!response.ok) return; // no store yet, or it cannot be read: the flag stands on its own
      events = (await response.json()).events ?? [];
    } catch {
      return;
    }
    const said = events
      .filter((event) => line.cites.includes(event.event_id))
      .flatMap((event) => event.conflicts ?? [])
      .map((conflict) => `${conflict.explanation} — ${conflict.reason}`);
    const where = $("story-lines").querySelector(`[data-why="${line.index}"]`);
    if (where && said.length) {
      where.textContent = said.join("; ");
      where.hidden = false;
    }
  }
}
