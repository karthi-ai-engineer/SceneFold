"""Tell what happened, with every sentence pointing at the footage behind it.

The story is written from the event store (knowledge.sqlite) and nowhere else. The model is given
a numbered list of moments — when, what the best-placed clip was showing, how many clips caught it,
whether they disagreed — and asked to write them up in order, ending each sentence with the number
it came from.

Then the citations are checked in code, not by the model:

- the moment cited has to exist;
- at least one clip has to have been filming at that time, and its own second has to fall inside
  what it actually recorded.

A sentence that fails is dropped and the reason kept, so a reader sees only what the footage
supports and anyone auditing can see what was thrown away. This is the difference between a story
about an event and a story that merely sounds like one: not that the model behaves, but that
nothing it writes reaches a reader without a piece of footage behind it.

What this cannot check is whether a sentence that cites a real moment describes it truthfully. A
model can cite correctly and still embroider. That needs a person, on footage they know.
"""

import json
import os
import re
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ValidationError

from scenefold.knowledge import NOT_IN_VIEW, Event

SCHEMA_VERSION = 1
STORY_NAME = "story.json"
MOST_MOMENTS = 16  # a readable account of an event, not a transcript of every flicker in it
CITE = re.compile(r"\[(\d+)\]")


class StoryError(Exception):
    """The story cannot be written or read."""


class Line(BaseModel):
    """One sentence of the account, with the footage behind it."""

    t_master_s: float  # when on the shared clock
    text: str  # the sentence itself, with its citation marks taken out
    cites: list[str] = []  # the events it rests on
    clips: list[str] = []  # the clips those events were seen in
    disputed: bool = False  # True when a cited event is one the clips disagreed about


class Story(BaseModel):
    schema_version: int = SCHEMA_VERSION
    event_id: str
    created_at: datetime
    model: str
    lines: list[Line] = []
    # Sentences the model wrote that no footage supports, kept with the reason. A story is only
    # trustworthy if what it left out is visible too.
    dropped: list[str] = []


def worth_telling(events: list[Event], most: int = MOST_MOMENTS) -> list[Event]:
    """The moments worth an account: the best-attested ones, spread across the whole event.

    Picking the strongest few would bunch them where the lights flashed most. The timeline is cut
    into as many slices as there are moments to tell, and each slice offers its best, so the story
    covers the event rather than its noisiest minute.
    """
    told = [e for e in events if e.evidence]
    if len(told) <= most:
        return sorted(told, key=lambda e: e.t_master_s)
    first, last = told[0].t_master_s, told[-1].t_master_s
    span = (last - first) or 1.0
    best: dict[int, Event] = {}
    for event in told:
        slice_of = min(most - 1, int((event.t_master_s - first) / span * most))
        standing = best.get(slice_of)
        if standing is None or _worth(event) > _worth(standing):
            best[slice_of] = event
    return sorted(best.values(), key=lambda e: e.t_master_s)


def _dispute(event: Event) -> str:
    """How the clips disagreed, in words the account can repeat without misstating it.

    The type on its own ("not in view") reads as jargon and gets paraphrased into something that
    is not quite true, so the model is told what actually happened instead.
    """
    if not event.conflicts:
        return ""
    conflict = event.conflicts[0]
    if conflict.kind == NOT_IN_VIEW:
        return "; another camera with as good a view caught nothing here"
    return f"; the clips disagree: {conflict.explanation.split(' (')[0]}"


def _worth(event: Event) -> tuple:
    """How much a moment deserves telling: witnesses first, then how much it stood out."""
    described = any(e.summary for e in event.evidence)
    return (described, event.witnesses, event.strength)


def as_moments(events: list[Event]) -> str:
    """The moments as the model sees them: numbered, timed, and no more than is known."""
    lines = []
    for number, event in enumerate(events, start=1):
        best = max(event.evidence, key=lambda e: (e.picture_score or 0, e.strength))
        said = (best.summary or "nothing was described here").strip()
        seen = f"{event.witnesses} clip{'s' if event.witnesses != 1 else ''} caught it"
        lines.append(f"[{number}] {_clock(event.t_master_s)} — {said} ({seen}{_dispute(event)})")
    return "\n".join(lines)


ASK = (
    "These are moments from one event, in order, each with the time it happened and what the "
    "clearest camera was showing.\n\n"
    "{moments}\n\n"
    "Write a short account of the event for somebody who was not there. Rules:\n"
    "- One sentence per moment, in the same order, plain words.\n"
    "- End every sentence with the moment's number in square brackets, like [3].\n"
    "- Say only what the moment says. Do not add anything you were not told, do not guess names, "
    "and do not count crowds.\n"
    "- Where the clips disagree, say so plainly rather than picking a side.\n"
    "- No heading, no list, no commentary: just the sentences."
)


def write_story(events: list[Event], model: str, ask) -> tuple[list[Line], list[str]]:
    """Ask for the account and keep only the sentences the footage supports."""
    chosen = worth_telling(events)
    if not chosen:
        return [], []
    text = ask(ASK.format(moments=as_moments(chosen)))
    lines, dropped = [], []
    for sentence in _sentences(text):
        cited = CITE.findall(sentence)
        clean = CITE.sub("", sentence).strip().rstrip(",").strip()
        if not clean:
            continue
        if not cited:
            dropped.append(f"{clean} — points at no moment")
            continue
        wanted = [int(number) for number in cited if 1 <= int(number) <= len(chosen)]
        if not wanted:
            dropped.append(f"{clean} — points at a moment that does not exist")
            continue
        behind = [chosen[number - 1] for number in wanted]
        clips = sorted({e.clip_id for event in behind for e in event.evidence})
        if not clips:
            dropped.append(f"{clean} — no clip stands behind it")
            continue
        # Two moments a few seconds apart often share one twelve-second description, and the model
        # dutifully writes the same sentence twice. One sentence, both citations.
        if lines and lines[-1].text == clean:
            lines[-1].cites += [event.event_id for event in behind]
            lines[-1].clips = sorted(set(lines[-1].clips) | set(clips))
            lines[-1].disputed = lines[-1].disputed or any(event.conflicts for event in behind)
            continue
        lines.append(
            Line(
                t_master_s=behind[0].t_master_s,
                text=clean,
                cites=[event.event_id for event in behind],
                clips=clips,
                disputed=any(event.conflicts for event in behind),
            )
        )
    return lines, dropped


def check_citations(
    story: Story, events: list[Event], clips: dict[str, tuple[float, float]]
) -> list[str]:
    """Every line's citations, checked against the store: what does not hold up is named.

    `clips` gives each clip's span on the shared clock. A line survives only if a moment it cites
    exists and at least one clip that saw it was really filming then.
    """
    known = {event.event_id: event for event in events}
    wrong = []
    for line in story.lines:
        for event_id in line.cites:
            event = known.get(event_id)
            if event is None:
                wrong.append(f"{line.text} — cites {event_id}, which is not in the store")
                continue
            covered = False
            for evidence in event.evidence:
                span = clips.get(evidence.clip_id)
                if span and span[0] - 1 <= event.t_master_s <= span[1] + 1:
                    covered = True
            if not covered:
                wrong.append(
                    f"{line.text} — cites {event_id}, which no clip was filming at "
                    f"{_clock(event.t_master_s)}"
                )
    return wrong


def _sentences(text: str) -> list[str]:
    """The model's answer, split into sentences, ignoring headings and list markers.

    A citation written after the full stop ("the lights drop. [1]") would otherwise be split off
    and read as belonging to the next sentence, which would credit one sentence's footage to
    another — and leave the first with none. A mark at the start of a fragment belongs to the
    sentence before it.
    """
    out: list[str] = []
    for raw in re.split(r"(?<=[.!?])\s+|\n+", text or ""):
        line = raw.strip().lstrip("-*•").strip()
        if len(line) <= 1:
            continue
        carried = re.match(r"^((?:\s*\[\d+\])+)\s*(.*)$", line, re.S)
        if carried and out:
            out[-1] += " " + carried.group(1).strip()
            rest = carried.group(2).strip()
            if len(rest) > 1:
                out.append(rest)
            continue
        out.append(line)
    return out


def _clock(seconds: float) -> str:
    minutes, rest = divmod(max(0.0, seconds), 60)
    return f"{int(minutes)}:{rest:04.1f}"


def save_story(event_dir: Path, story: Story) -> Path:
    path = event_dir / STORY_NAME
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(story.model_dump_json(indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def load_story(event_dir: Path) -> Story | None:
    path = event_dir / STORY_NAME
    if not path.exists():
        return None
    try:
        return Story.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, ValidationError) as exc:
        raise StoryError(f"{path} is unreadable: {exc}") from exc


def tell_event(
    event_name: str,
    data_dir: str | Path = "data",
    *,
    model: str = "qwen3.5:4b",
    ask=None,
    progress=None,
) -> Story:
    """Write the account of an event from what is known about it, and keep only what holds up."""
    from scenefold.judge import OllamaJudge  # local: the story needs no model to be imported
    from scenefold.knowledge import all_events, load_store
    from scenefold.manifest import normalize_event_id, now

    try:
        event_id = normalize_event_id(event_name)
    except ValueError as exc:
        raise StoryError(str(exc)) from exc
    event_dir = Path(data_dir) / event_id
    store = load_store(event_dir)
    if store is None:
        raise StoryError(f"{event_dir} holds no event knowledge; run `scenefold fuse` first")
    try:
        events = all_events(store)
        spans = {
            row["clip_id"]: (
                row["offset_s"] or 0.0,
                (row["offset_s"] or 0.0) + (row["duration_s"] or 0.0),
            )
            for row in store.execute("SELECT clip_id, offset_s, duration_s FROM clips")
        }
    finally:
        store.close()
    if not events:
        raise StoryError("nothing is known about this event yet; run `scenefold fuse` first")

    if ask is None:
        judge = OllamaJudge(model)
        ask = lambda prompt: judge.plain(prompt)  # noqa: E731
    if progress:
        progress(f"writing from {len(events)} moments")
    lines, dropped = write_story(events, model, ask)
    story = Story(event_id=event_id, created_at=now(), model=model, lines=lines, dropped=dropped)
    # The last word belongs to the store, not the model: anything it cannot back up goes.
    wrong = check_citations(story, events, spans)
    if wrong:
        bad = {reason.split(" — ")[0] for reason in wrong}
        story.lines = [line for line in story.lines if line.text not in bad]
        story.dropped += wrong
    save_story(event_dir, story)
    return story
