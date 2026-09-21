// Where the footage argues with itself.
//
// The account only ever shows the handful of moments it chose to write a sentence about. Most of
// what `scenefold fuse` found never reaches it, and the disagreements are the part most worth
// seeing: a moment two phones caught and described differently, or one a phone with a clear view
// somehow missed.
//
// Every row is one disagreement, with what each clip said about it and what kind of difference it
// looks like. Clicking a row takes you to that second in the viewer, because the only real answer
// to "do these two accounts disagree?" is to watch it. A burst of moments half a second apart that
// the same clips describe in the same words is one row marked "4x", not four rows: the store is
// right to hold them separately and a reader is not helped by reading the same argument four times.
//
// Nothing here is a verdict. Most of these are left unresolved on purpose, because no camera had
// a better view than the others, and the page says so rather than picking a winner.

import { formatTime } from "./sync.js";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

const MOST_SHOWN = 60; // a long event finds hundreds; the rest are in knowledge.sqlite
// A burst of moments a fraction of a second apart, described by the same clips in the same words,
// is one disagreement seen several times over, not several disagreements. Rows that close together
// with the same accounts are shown as one, with how many moments were behind it.
const ONE_ARGUMENT_S = 3.0;

// What each kind of disagreement means, in the words a person would use.
const KINDS = {
  not_in_view: "was pointed elsewhere",
  occluded: "something was in the way",
  read_differently: "same moment, told differently",
  model_error: "one account looks simply wrong",
  sound_delay: "sound reached the phones at different times",
  unresolved: "no camera had the better view",
};

/** Show where the clips disagree, or nothing at all if no store has been built. */
export async function loadDisagreements(timeline, colorOf, other, jumpTo) {
  let found;
  try {
    const response = await fetch("/api/events?conflicts=1");
    if (!response.ok) return hide();
    found = await response.json();
  } catch {
    return hide();
  }
  const events = found.events ?? [];
  if (!events.length) return hide();
  const nameOf = Object.fromEntries(timeline.clips.map((c) => [c.clip_id, c.name]));
  render(group(events), events.length, nameOf, (id) => colorOf[id] ?? other, jumpTo);
}

/** What each clip said about a moment, as one string, for telling two arguments apart. */
function saidAt(event) {
  return (event.evidence ?? [])
    .filter((e) => e.summary)
    .map((e) => `${e.clip_id}:${e.summary}`)
    .sort()
    .join("|");
}

/** Moments close together that the same clips describe the same way are one disagreement. */
function group(events) {
  const grouped = [];
  for (const event of events) {
    const last = grouped[grouped.length - 1];
    if (last && event.t_master_s - last.t_last_s <= ONE_ARGUMENT_S && saidAt(event) === last.said) {
      last.t_last_s = event.t_master_s;
      last.moments += 1;
      continue;
    }
    grouped.push({ ...event, t_last_s: event.t_master_s, moments: 1, said: saidAt(event) });
  }
  return grouped;
}

function hide() {
  const panel = $("disagree-panel");
  if (panel) panel.hidden = true;
}

function render(events, moments, nameOf, colorOf, jumpTo) {
  const panel = $("disagree-panel");
  panel.hidden = false;
  const unresolved = events.filter((e) => (e.conflicts ?? []).every((c) => !c.resolved_by)).length;
  const over = moments > events.length ? `, over ${moments} moments` : "";
  $("disagree-about").textContent =
    `${events.length} disagreement${events.length === 1 ? "" : "s"}${over}` +
    `${unresolved ? `, ${unresolved} left unresolved because no camera had the better view` : ""}` +
    ` · click one to watch it and judge for yourself`;

  $("disagree-list").innerHTML = events
    .slice(0, MOST_SHOWN)
    .map((event, index) => {
      const said = (event.evidence ?? [])
        .filter((e) => e.summary)
        .map(
          (e) => `<li><span class="who" style="--c:${colorOf(e.clip_id)}"><i></i><span>${esc(nameOf[e.clip_id] ?? e.clip_id)}</span></span>
            <span class="said">${esc(e.summary)}</span></li>`,
        )
        .join("");
      const why = (event.conflicts ?? [])
        .map((c) => {
          const kind = KINDS[c.kind] ?? c.kind;
          const settled = c.resolved_by
            ? `taken from ${esc(nameOf[c.resolved_by] ?? c.resolved_by)}, which had the better view`
            : esc(c.reason ?? "unresolved");
          return `<span class="pill ${c.resolved_by ? "" : "warning"}" title="${esc(c.explanation ?? "")}">${esc(kind)}</span>
            <span class="muted small">${settled}</span>`;
        })
        .join(" ");
      const again =
        event.moments > 1
          ? `<span class="pill" title="The same two accounts, over ${event.moments} moments between ${formatTime(event.t_master_s, 1)} and ${formatTime(event.t_last_s, 1)}.">${event.moments}×</span>`
          : "";
      return `<li><button type="button" class="disagree" data-at="${event.t_master_s}"
        title="Watch ${formatTime(event.t_master_s, 1)}">
        <span class="when mono">${formatTime(event.t_master_s, 1)}</span>
        <span class="body"><span class="why">${why}${again}</span>
          <ul class="accounts">${said || '<li class="muted small">no clip described this moment</li>'}</ul></span>
      </button></li>`;
    })
    .join("");
  $("disagree-more").hidden = events.length <= MOST_SHOWN;
  $("disagree-more").textContent =
    events.length > MOST_SHOWN ? `and ${events.length - MOST_SHOWN} more, in knowledge.sqlite` : "";

  $("disagree-list").addEventListener("click", (event) => {
    const row = event.target.closest(".disagree");
    if (row) jumpTo(Number(row.dataset.at));
  });
}
