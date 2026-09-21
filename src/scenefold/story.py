"""Tell what happened, with every sentence pointing at the footage behind it.

The story is written from the event store (knowledge.sqlite) and nowhere else. The model is given
a numbered list of moments — when, what the best-placed clip was showing, how many clips caught it,
whether they disagreed — and asked to write them up in order, ending each sentence with the number
it came from.

The marks stay in the sentences, renumbered so that [1] is the first clip cited in that sentence,
because a model writes them into the grammar ("the screens at [3] and [4]") and pulling them out
leaves "the screens at and".

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
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ValidationError

from scenefold.knowledge import NOT_IN_VIEW, Event

SCHEMA_VERSION = 1
STORY_NAME = "story.json"
MOST_MOMENTS = 16  # a readable account of an event, not a transcript of every flicker in it
MOST_MARKS = 4  # citations shown in one sentence; a model will happily attach twenty
CITE = re.compile(r"\[(\d+)\]")


class StoryError(Exception):
    """The story cannot be written or read."""


class Line(BaseModel):
    """One sentence of the account, with the footage behind it."""

    t_master_s: float  # when on the shared clock
    # The sentence, keeping its citation marks where the writer put them and numbered from one
    # within the sentence: [1] is the first event in `cites`, [2] the second.
    text: str
    cites: list[str] = []  # the events it rests on, in the order the marks appear
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
    return _cited_lines(ask(ASK.format(moments=as_moments(chosen))), chosen)


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


ANSWER = (
    "Here is what is known about one event, moment by moment.\n\n"
    "{moments}\n\n"
    "Question: {question}\n\n"
    "Answer it from these moments alone, in one to three plain sentences, ending each with the "
    "number of the moment it rests on, like [3]. Cite at most three moments in a sentence, the "
    "ones that show it best. If the moments do not say, answer exactly: "
    "The footage does not show this. Do not guess, do not add what you were not told, and do not "
    "name anyone."
)
NOT_SHOWN = "The footage does not show this."
ABOUT = 24  # moments handed over for a question: enough to answer from, few enough to read


def about(events: list[Event], question: str, most: int = ABOUT) -> list[Event]:
    """The moments most likely to bear on a question: the ones whose words it shares.

    No cleverness, and that is deliberate — a question about confetti should reach the moments
    that mention confetti. Where nothing matches, the account's own spread is handed over instead,
    so a general question ("what happened?") still gets the whole event rather than nothing.
    """
    asked = _words(question)
    scored = []
    for event in events:
        said = " ".join(e.summary or "" for e in event.evidence)
        shared = asked & _words(said)
        if shared:
            scored.append((len(shared), event.strength, event))
    if not scored:
        return worth_telling(events, most)
    scored.sort(key=lambda row: (-row[0], -row[1]))
    return sorted((event for _, _, event in scored[:most]), key=lambda e: e.t_master_s)


def answer_question(events: list[Event], question: str, ask) -> tuple[list[Line], list[str]]:
    """Answer from what is known, keeping only the sentences the footage supports."""
    chosen = about(events, question)
    if not chosen:
        return [], []
    text = ask(ANSWER.format(moments=as_moments(chosen), question=question.strip()))
    # Only a refusal when that is the whole answer. "The footage does not show this clearly, but
    # the screens are lit [2]" says something, and throwing it away would lose it.
    if _bare(text) == _bare(NOT_SHOWN):
        return [], []
    return _cited_lines(text, chosen)


# Sentences that hold up, and the reasons the others did not: shared by the account and the answers.
def _cited_lines(text: str, chosen: list[Event]) -> tuple[list[Line], list[str]]:
    lines: list[Line] = []
    dropped: list[str] = []
    for sentence in _sentences(text):
        if not CITE.search(sentence):
            bare = _tidy(sentence)
            if bare:
                dropped.append(f"{bare} — points at no moment")
            continue
        clean, behind = _renumber(sentence, chosen)
        if not clean:
            continue
        if not behind:
            dropped.append(f"{clean} — points at a moment that does not exist")
            continue
        clips = sorted({e.clip_id for event in behind for e in event.evidence})
        if not clips:
            dropped.append(f"{clean} — no clip stands behind it")
            continue
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


def _renumber(sentence: str, chosen: list[Event]) -> tuple[str, list[Event]]:
    """A sentence with its citation marks kept, numbered from one within the sentence itself.

    The marks stay in the words because a model writes them into the grammar — "the screens at [3]
    and [4]" — and pulling them out leaves "the screens at and". Numbering them per sentence means
    a reader sees [1] and [2] beside the two clips that back them, in the order they are claimed.
    """
    order: list[Event] = []

    def mark(found: re.Match) -> str:
        number = int(found.group(1))
        if not 1 <= number <= len(chosen):
            return ""  # a mark pointing at nothing goes, and the sentence is judged on the rest
        event = chosen[number - 1]
        if event in order:
            return f"[{order.index(event) + 1}]"
        if len(order) >= MOST_MARKS:
            return ""  # a sentence trailing twenty marks is worse read than a sentence with four
        order.append(event)
        return f"[{len(order)}]"

    return _tidy(CITE.sub(mark, sentence)), order


def _bare(text: str) -> str:
    """A sentence stripped to its words, for comparing what was said with what was expected."""
    return re.sub(r"[^a-z ]", "", (text or "").lower()).strip()


def _tidy(text: str) -> str:
    """A sentence with its citation marks taken out, and the punctuation they leave behind.

    A model that cites several moments at once writes "...in the footage [3], [4] and [5]." Taking
    the marks out on their own leaves ", , and ." trailing after the words.
    """
    text = re.sub(r"\s+", " ", text or "").strip()
    text = re.sub(r"(?:\s*,)+", ",", text)  # ", , ," is one comma at most
    text = re.sub(r",\s*and\s*(?=[.;:]|$)", "", text)  # "..., and ." had a list in it
    text = re.sub(r",\s*(?=[.;:]|$)", "", text)  # "..., ." likewise
    text = re.sub(r"\s+([,.;:])", r"\1", text)  # punctuation belongs against its word
    return text.strip().strip(",").strip()


def _words(text: str) -> set[str]:
    """The words worth matching on: long enough to mean something."""
    return {word for word in re.findall(r"[a-z]{4,}", (text or "").lower())} - _COMMON


_COMMON = {
    "this", "that", "with", "from", "have", "what", "when", "were", "they", "them", "there",
    "which", "while", "about", "does", "show", "shows", "shown", "into", "over", "their", "then",
    "some", "more", "most", "very", "like", "also", "much", "many", "видео",
}  # fmt: skip


def ask_event(
    event_name: str,
    question: str,
    data_dir: str | Path = "data",
    *,
    model: str = "qwen3.5:4b",
    ask=None,
) -> tuple[list[Line], list[str]]:
    """Answer a question about an event from its store, with every sentence cited."""
    from scenefold.judge import OllamaJudge
    from scenefold.knowledge import all_events, load_store
    from scenefold.manifest import normalize_event_id

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
    if ask is None:
        judge = OllamaJudge(model)
        ask = lambda prompt: judge.plain(prompt)  # noqa: E731
    lines, dropped = answer_question(events, question, ask)
    said = Story(event_id=event_id, created_at=datetime.now(UTC), model=model, lines=lines)
    wrong = check_citations(said, events, spans)
    if wrong:
        bad = {reason.split(" — ")[0] for reason in wrong}
        lines = [line for line in lines if line.text not in bad]
        dropped += wrong
    return lines, dropped
