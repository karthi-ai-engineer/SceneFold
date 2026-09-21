"""Put every clip's account on one clock, and see where they agree.

Each clip has been watched and listened to on its own (observe.py), and the moments inside it timed
to a few hundredths of a second (moments.py). Here those moments are moved onto the shared clock
and the ones that land together are taken to be the same thing happening: a flash three phones
caught at the same instant is one event with three witnesses, not three events.

Pictures are lined up on the event itself, not on when each phone heard it (picture_offset.py), so
a phone at the back of a stadium does not have its account placed 400 ms early.

What corroboration is and is not: two clips catching a moment together makes it far likelier that
something really happened, but it says nothing about *what*. Whether their descriptions agree is a
separate question, and an unanswered one where a small model's words are all we have.
"""

from dataclasses import dataclass
from pathlib import Path

from scenefold.judge import Judge, Reading
from scenefold.knowledge import (
    NOT_IN_VIEW,
    UNRESOLVED,
    Conflict,
    Event,
    Evidence,
    open_store,
    replace_all,
)
from scenefold.manifest import ManifestError, load_manifest, normalize_event_id
from scenefold.observations import ClipObservations, Moment, load_observations
from scenefold.timeline import ClipPlacement, Timeline, TimelineError, load_timeline

# How far apart two clips may time the same thing and still be taken as one event. Sync places
# clips within a frame of each other on real footage, and moments are found to a few hundredths,
# so 0.15 s is generous enough for both without gluing separate hits together.
TOGETHER_S = 0.15
# A moment this weak is noise in one clip's own signal, not something that happened.
WORTH_KEEPING = 0.25
# How far from an event a clip's description may sit and still count as what it was showing.
DESCRIBES_S = 8.0
# A clip whose picture scores this much better than the other is taken to have had the better
# view; closer than this and the disagreement is left unresolved.
CLEARLY_BETTER = 0.12
EDGE_S = 0.001  # slack at the ends of a clip, for floating point and for frames being 33 ms apart


class FuseError(Exception):
    """Nothing can be fused: no timeline, or no clip has been watched yet."""


@dataclass(frozen=True)
class OnTheClock:
    """One clip's moment, moved onto the shared clock."""

    clip_id: str
    t_master_s: float
    t_local_s: float
    kind: str
    strength: float


def master_time(placement: ClipPlacement, t_local_s: float) -> float:
    """Where a clip's own second sits on the shared clock, with the pictures lined up.

    `heard_late_s` is added because offsets line up the moment each phone *heard* the event, and
    what happened is better told by what they saw.
    """
    rate = 1 + (placement.drift_ppm or 0.0) * 1e-6
    return placement.offset_s + (placement.heard_late_s or 0.0) + t_local_s / rate


def local_time(placement: ClipPlacement, t_master_s: float) -> float:
    rate = 1 + (placement.drift_ppm or 0.0) * 1e-6
    return (t_master_s - placement.offset_s - (placement.heard_late_s or 0.0)) * rate


def recording_at(placement: ClipPlacement, t_master_s: float) -> bool:
    """Whether a clip was filming at a shared-clock moment.

    A millisecond of slack at each end: a moment on a clip's very first frame computes to a hair
    below zero in floating point, and frames are 33 ms apart anyway.
    """
    local = local_time(placement, t_master_s)
    return -EDGE_S <= local < placement.duration_s + EDGE_S


def on_the_clock(
    placement: ClipPlacement, moments: list[Moment], worth_keeping: float = WORTH_KEEPING
) -> list[OnTheClock]:
    """One clip's moments on the shared clock, the faint ones left out."""
    return [
        OnTheClock(
            clip_id=placement.clip_id,
            t_master_s=round(master_time(placement, m.t_s), 4),
            t_local_s=m.t_s,
            kind=m.kind,
            strength=m.strength,
        )
        for m in moments
        if m.strength >= worth_keeping
    ]


def gather(moments: list[OnTheClock], together_s: float = TOGETHER_S) -> list[list[OnTheClock]]:
    """Moments that land together on the clock, grouped: one group is one thing that happened.

    A clip only ever contributes its strongest moment to a group, so a phone that shakes twice in
    a tenth of a second does not look like two witnesses.
    """
    groups: list[list[OnTheClock]] = []
    for moment in sorted(moments, key=lambda m: m.t_master_s):
        if groups and moment.t_master_s - groups[-1][0].t_master_s <= together_s:
            group = groups[-1]
            same_clip = next((m for m in group if m.clip_id == moment.clip_id), None)
            if same_clip is None:
                group.append(moment)
            elif moment.strength > same_clip.strength:
                group[group.index(same_clip)] = moment
        else:
            groups.append([moment])
    return groups


def describing(seen: ClipObservations, t_local_s: float, within_s: float = DESCRIBES_S):
    """What a clip was showing around one of its own seconds, if anything said so."""
    covering = [o for o in seen.observations if o.t_start_s <= t_local_s < o.t_end_s]
    if covering:
        return covering[0]
    near = [
        o
        for o in seen.observations
        if min(abs(o.t_start_s - t_local_s), abs(o.t_end_s - t_local_s)) <= within_s
    ]
    return min(near, key=lambda o: abs(o.t_start_s - t_local_s)) if near else None


def _kind_of(group: list[OnTheClock]) -> str:
    kinds = {m.kind for m in group}
    return "both" if len(kinds) > 1 else next(iter(kinds))


def _disagreement(event: Event, views: dict[str, float | None]) -> Conflict | None:
    """One clip catching something that another, filming the same instant, did not.

    Settled by which camera had the better view, never by counting cameras: three phones behind a
    pillar do not outvote the one with a clear line of sight. When the clip with the best view is
    the one that missed it, that is left unresolved rather than explained away — it is exactly the
    case where the event may not have happened at all.

    Reading two descriptions and saying whether they contradict each other needs a model; that is
    the next piece of work. Until then this says only what can be told without understanding words.
    """
    saw = {e.clip_id for e in event.evidence}
    missed = [clip for clip in views if clip not in saw]
    if not missed or event.witnesses < 2:
        # One clip alone catching something the others did not is usually that clip twitching,
        # not the others failing. Only what two phones agree happened is worth anyone's attention.
        return None
    witnessed = sorted(views[clip] for clip in saw if views.get(clip) is not None)
    if not witnessed:
        return None
    # ...and only a clip whose picture was at least as good as the weakest witness's can be said
    # to have missed anything. A phone pointing at the floor is not disagreeing with anyone; it
    # simply was not looking.
    bar = witnessed[0]
    blind = [clip for clip in missed if (views.get(clip) or 0) >= bar]
    if not blind:
        return None
    explanation = (
        f"{len(saw)} of {event.recording} clips filming at this moment caught it; "
        f"{len(blind)} with as good a view did not"
    )
    scored = {clip: score for clip, score in views.items() if score is not None}
    if len(scored) < 2:
        return Conflict(NOT_IN_VIEW, explanation, None, UNRESOLVED)
    best = max(scored, key=lambda clip: scored[clip])
    others = sorted((s for c, s in scored.items() if c != best), reverse=True)
    lead = scored[best] - others[0]
    if lead < CLEARLY_BETTER:
        return Conflict(
            NOT_IN_VIEW,
            explanation,
            None,
            f"no camera had a clearly better view ({scored[best]:.2f} against {others[0]:.2f})",
        )
    if best not in saw:
        return Conflict(
            NOT_IN_VIEW,
            explanation,
            None,
            f"the clip with the best view ({scored[best]:.2f}) is one that did not catch it",
        )
    return Conflict(
        NOT_IN_VIEW,
        explanation,
        best,
        f"its picture was the better one at that moment "
        f"({scored[best]:.2f} against {others[0]:.2f})",
    )


def read_accounts(events: list[Event], judge: Judge, progress=None) -> int:
    """Have the two best-placed accounts of each event read, and note where they disagree.

    Only the best two are read: a moment with five witnesses does not need ten comparisons to show
    that the clips are not telling one story. The same pair of sentences is only ever read once,
    however many moments they cover — a twelve-second description covers a great many of them.
    """
    seen: dict[tuple[str, str], Reading] = {}
    found = 0
    for event in events:
        said = [e for e in event.evidence if e.summary]
        if len(said) < 2:
            continue
        first, second = sorted(said, key=lambda e: -(e.picture_score or 0))[:2]
        if first.summary.strip() == second.summary.strip():
            continue  # word for word the same: nothing for anyone to read
        pair = (first.summary.strip(), second.summary.strip())
        if pair not in seen:
            if progress:
                progress(f"reading two accounts of {event.t_master_s:.0f} s")
            seen[pair] = judge.read(*pair)
        reading = seen[pair]
        if reading.fit:
            continue
        who, why = _who_to_believe(event, {e.clip_id: e.picture_score for e in event.evidence})
        event.conflicts.append(
            Conflict(
                kind=reading.kind,
                explanation=f"{reading.why} (A: {first.clip_id[:8]}, B: {second.clip_id[:8]})",
                resolved_by=who,
                reason=why,
            )
        )
        found += 1
    return found


def _who_to_believe(event: Event, views: dict[str, float | None]) -> tuple[str | None, str]:
    """Which clip's account to take: the one that could see best, or nobody."""
    scored = {clip: score for clip, score in views.items() if score is not None}
    if len(scored) < 2:
        return None, UNRESOLVED
    best = max(scored, key=lambda clip: scored[clip])
    second = max(score for clip, score in scored.items() if clip != best)
    if scored[best] - second < CLEARLY_BETTER:
        return (
            None,
            f"no camera had a clearly better view ({scored[best]:.2f} against {second:.2f})",
        )
    return best, f"its picture was the better one ({scored[best]:.2f} against {second:.2f})"


def fuse_event(
    event_name: str,
    data_dir: str | Path = "data",
    *,
    worth_keeping: float = WORTH_KEEPING,
    judge: Judge | None = None,
    progress=None,
) -> list[Event]:
    """Merge every clip's account into events on the shared clock, and write them down.

    With a `judge`, the accounts of each event are also read against each other, which finds the
    disagreements that arithmetic cannot: two clips describing the same moment differently.
    """
    try:
        event_id = normalize_event_id(event_name)
    except ValueError as exc:
        raise FuseError(str(exc)) from exc
    event_dir = Path(data_dir) / event_id
    try:
        manifest, timeline = load_manifest(event_dir), load_timeline(event_dir)
    except (ManifestError, TimelineError) as exc:
        raise FuseError(str(exc)) from exc
    if manifest is None or timeline is None:
        raise FuseError(f"{event_dir} has no timeline; run `scenefold sync` first")

    placed = {c.clip_id: c for c in timeline.clips if c.placed}
    watched = {
        clip_id: seen
        for clip_id in placed
        if (seen := load_observations(event_dir, clip_id)) is not None
    }
    if not watched:
        raise FuseError("no clip has been watched yet; run `scenefold observe` first")

    moments: list[OnTheClock] = []
    for clip_id, seen in watched.items():
        moments += on_the_clock(placed[clip_id], seen.moments, worth_keeping)
    if not moments and not any(seen.moments for seen in watched.values()):
        raise FuseError(
            "no clip has any timed moments; these were watched before moments existed, so run "
            "`scenefold observe` again to find them"
        )
    events = _events_from(gather(moments), placed, watched, timeline)
    if judge is not None:
        read_accounts(events, judge, progress)

    store = open_store(event_dir)
    try:
        replace_all(store, [_clip_row(placed[c], watched[c]) for c in watched], events)
    finally:
        store.close()
    return events


def _events_from(
    groups: list[list[OnTheClock]],
    placed: dict[str, ClipPlacement],
    watched: dict[str, ClipObservations],
    timeline: Timeline,
) -> list[Event]:
    events = []
    for number, group in enumerate(groups):
        when = round(sum(m.t_master_s for m in group) / len(group), 4)
        views: dict[str, float | None] = {}  # every clip filming then, and how good its picture was
        evidence = []
        for moment in group:
            said = describing(watched[moment.clip_id], moment.t_local_s)
            evidence.append(
                Evidence(
                    clip_id=moment.clip_id,
                    t_local_s=moment.t_local_s,
                    kind=moment.kind,
                    strength=moment.strength,
                    summary=said.summary if said else None,
                    picture_score=said.picture_score if said else None,
                )
            )
        for clip_id, placement in placed.items():
            if clip_id in watched and recording_at(placement, when):
                nearby = describing(watched[clip_id], local_time(placement, when))
                views[clip_id] = nearby.picture_score if nearby else None
        event = Event(
            event_id=f"{timeline.event_id}-{number:05d}",
            t_master_s=when,
            kind=_kind_of(group),
            strength=round(max(m.strength for m in group), 4),
            recording=len(views),
            summary=_best_summary(evidence),
            evidence=evidence,
        )
        if (conflict := _disagreement(event, views)) is not None:
            event.conflicts.append(conflict)
        events.append(event)
    return events


def _best_summary(evidence: list[Evidence]) -> str | None:
    """What the clip with the best picture was showing: the account worth quoting first."""
    said = [e for e in evidence if e.summary]
    if not said:
        return None
    return max(said, key=lambda e: (e.picture_score or 0, e.strength)).summary


def _clip_row(placement: ClipPlacement, seen: ClipObservations) -> dict:
    return {
        "clip_id": placement.clip_id,
        "name": placement.name,
        "offset_s": placement.offset_s,
        "heard_late_s": placement.heard_late_s,
        "drift_ppm": placement.drift_ppm,
        "duration_s": seen.duration_s,
    }
