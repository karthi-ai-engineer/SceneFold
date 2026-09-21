// Who was there: the people `scenefold identify` matched up between the angles.
//
// One row per person, with the angles they were caught by and when. Clicking an angle seeks to the
// moment that phone first saw them, which is the only way to check a match: watch both angles at
// the second they are supposed to show the same person, and see whether they do.
//
// Two things are said plainly rather than hidden. Somebody only one phone filmed is shown as such,
// not left out, because that is the usual case at a real event. And a match the descriptions only
// half support is marked "worth checking", because the alternative is a page that sounds certain
// about a small model's guess at what somebody was wearing.

import { formatTime } from "./sync.js";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

const MOST_SHOWN = 40; // a crowded event finds hundreds; the rest are in people.json

/** Show who was found, or say nothing at all if nobody has been asked. */
export async function loadPeople(timeline, colorOf, other, jumpTo) {
  let found;
  try {
    const response = await fetch("/api/people");
    if (!response.ok) return hide(); // not identified yet: the report stands without it
    found = await response.json();
  } catch {
    return hide();
  }
  const people = found.people ?? [];
  if (!people.length) return hide();
  const nameOf = Object.fromEntries(timeline.clips.map((c) => [c.clip_id, c.name]));
  render(found, people, nameOf, (id) => colorOf[id] ?? other, jumpTo);
}

function hide() {
  const panel = $("people-panel");
  if (panel) panel.hidden = true;
}

function render(found, people, nameOf, colorOf, jumpTo) {
  const panel = $("people-panel");
  panel.hidden = false;
  const across = found.found?.across_angles ?? 0;
  const sure = found.found?.sure ?? 0;
  $("people-about").textContent =
    `${people.length} ${people.length === 1 ? "person" : "people"}, ` +
    `${across} caught by more than one phone (${sure} of those beyond doubt) · ` +
    `matched on what the clips say they were wearing, so click an angle and check it`;

  $("people-list").innerHTML = people
    .slice(0, MOST_SHOWN)
    .map((person) => {
      const angles = (person.seen ?? [])
        .map(
          (seen) => `<button type="button" class="who click" style="--c:${colorOf(seen.clip_id)}"
            data-at="${seen.t_master_start_s}"
            title="Watch ${formatTime(seen.t_master_start_s, 1)}, where ${esc(nameOf[seen.clip_id] ?? seen.clip_id)} first saw them">
            <i></i><span>${esc(nameOf[seen.clip_id] ?? seen.clip_id)}</span></button>`,
        )
        .join("");
      const flag = !person.across_angles
        ? `<span class="pill" title="Only one phone caught this person. That is the usual case at a real event, not a failure to match.">one angle</span>`
        : person.sure
          ? `<span class="pill good" title="The two angles described the same clothes closely enough to leave little doubt.">${person.clips.length} angles</span>`
          : `<span class="pill warning" title="The descriptions only half agree. It may be one person or two; watch both angles and see.">worth checking</span>`;
      return `<li><span class="wearing">${esc(person.wearing)}</span>
        <span class="when mono">${formatTime(person.t_master_start_s, 0)}–${formatTime(person.t_master_end_s, 0)}</span>
        <span class="angles">${angles}${flag}</span></li>`;
    })
    .join("");
  $("people-more").hidden = people.length <= MOST_SHOWN;
  $("people-more").textContent =
    people.length > MOST_SHOWN ? `and ${people.length - MOST_SHOWN} more, in people.json` : "";

  $("people-list").addEventListener("click", (event) => {
    const button = event.target.closest(".who.click");
    if (button) jumpTo(Number(button.dataset.at));
  });
}
