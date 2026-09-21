"""What was happening second by second, and which angle was pointed at it.

The cut up to now judged a second of footage on the picture alone: how much detail it holds, how
steady it is, how well exposed. That is everything a camera can be wrong about and nothing about
what it was pointed at, so a sharp, steady shot of the floor beat a shaky shot of the moment
everyone came for. This is the other half.

It comes from the event store, which is already built out of what every clip caught (fuse.py). Two
numbers per second of the film:

- **how much happened** — the moments that landed on this second, weighted by how strong they were
  and by how many phones caught them. Something four people caught is likelier to matter than
  something one phone's shaking produced.
- **which angles saw it** — of what happened in this second, how much this particular clip has
  evidence for. A clip that was filming but reported nothing was pointed somewhere else.

Neither replaces the picture score; they are added to it, so an angle wins a moment by having both
seen it and been worth looking at. The weight is deliberately modest: the event store is built on a
small model's descriptions, and a cut that chased them would inherit every mistake in them. The
worst that can happen here is a slightly worse cut, which is why this is a reasonable place to use
knowledge that the story is not yet allowed to use.
"""

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from scenefold.knowledge import Event, all_events, load_store

# How much being pointed at what happened is worth, against how good the picture is (both 0 to 1).
#
# It has to cover two things, not one. The picture gap between the best angle and the next-best at
# a moment several phones caught runs about 0.1 to 0.3 on real footage. On top of that, cutting in
# and back out again costs 0.95 (0.35 each way, plus 0.25 for returning to the angle before last),
# spread over a shot of a few seconds. Under about 0.4 this changes nothing at all: the angle that
# saw the moment is worth more, and still not worth the cut.
#
# Half the picture's own weight is as far as this should ever go. The event store is built on a
# small model's descriptions, and a film that followed them further than this would be following
# an opinion over the evidence of its own eyes.
WITNESSED = 0.5
# A moment is an instant, but a second of film either side of it is part of it: the flash and the
# faces turning towards it. Interest is spread over this long, which also stops a cut landing in
# the half-second before something happens.
SPREAD_S = 2.0
# Cutting away at the moment itself is the one thing an editor would not do, so a cut there costs
# this much more than the usual. Cutting just before is free, which is what the spread allows.
DURING = 1.0


@dataclass(frozen=True)
class Interest:
    """What happened over a film, and which angle was looking at it."""

    happening: np.ndarray  # per second of the film, 0 to 1
    saw: dict[str, np.ndarray]  # clip_id -> what share of each second's happening it caught
    events: int  # how many moments this was built from

    @property
    def known(self) -> bool:
        """Whether there is anything here worth weighing."""
        return self.events > 0 and bool(self.happening.any())

    def worth(self, clip_id: str) -> np.ndarray:
        """How much to add to one angle's picture score, per second."""
        saw = self.saw.get(clip_id)
        if saw is None:
            return np.zeros_like(self.happening)
        return WITNESSED * self.happening * saw


def read_interest(event_dir: Path, start_s: float, seconds: int, clip_ids: list[str]) -> Interest:
    """What the event store knows about the stretch of clock a film covers.

    An event with no store yet is not an error: the cut falls back to judging the picture alone,
    which is what it did before any of this existed.
    """
    empty = Interest(np.zeros(max(seconds, 0)), {c: np.zeros(max(seconds, 0)) for c in clip_ids}, 0)
    if seconds < 1:
        return empty
    store = load_store(event_dir)
    if store is None:
        return empty
    try:
        events = all_events(store)
    finally:
        store.close()
    return measure(events, start_s, seconds, clip_ids)


def measure(events: list[Event], start_s: float, seconds: int, clip_ids: list[str]) -> Interest:
    """The same, from events already in hand (pure, so it can be tested without a store)."""
    happening = np.zeros(seconds)
    caught = {clip_id: np.zeros(seconds) for clip_id in clip_ids}
    counted = 0
    for event in events:
        second = event.t_master_s - start_s
        if not (-SPREAD_S <= second < seconds + SPREAD_S):
            continue
        counted += 1
        weight = _weight(event)
        spread = _around(second, seconds)
        happening += weight * spread
        # Of this moment, what share did each clip catch? A clip with no evidence saw none of it.
        witnesses = {evidence.clip_id for evidence in event.evidence}
        for clip_id in witnesses & caught.keys():
            caught[clip_id] += weight * spread
    # Against the tallest second of this event, so "how much happened" always means "compared with
    # the rest of this event". Nothing needs trimming first: a moment's strength arrives already
    # scaled against the strongest one in its own clip (moments.py), so no single bang can run away
    # with the whole film the way a raw loudness would.
    tallest = float(happening.max())
    scaled = happening / tallest if tallest > 0 else happening
    # What share of each second's happening this clip saw, before any scaling: a share stays a
    # share however tall the second was.
    share = {
        clip_id: np.divide(seen, happening, out=np.zeros(seconds), where=happening > 0)
        for clip_id, seen in caught.items()
    }
    return Interest(happening=scaled, saw=share, events=counted)


def cutting_cost(interest: Interest, at: int, cut_cost: float) -> float:
    """What a cut costs at one second: more where something is happening, normal where nothing is.

    An editor cuts on the quiet before a moment, never across the moment itself.
    """
    if at <= 0 or at >= len(interest.happening):
        return cut_cost
    return cut_cost * (1 + DURING * float(interest.happening[at]))


def _weight(event: Event) -> float:
    """How much one moment matters: how strong it was, and how many phones agree it happened."""
    return float(event.strength) * float(event.witnesses)


def _around(second: float, seconds: int) -> np.ndarray:
    """A moment spread over the seconds around it, falling off to nothing at SPREAD_S."""
    ticks = np.arange(seconds, dtype=float)
    near = np.clip(1.0 - np.abs(ticks - second) / SPREAD_S, 0.0, 1.0)
    return near
