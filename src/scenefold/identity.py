"""Decide when two angles are filming the same person.

people.py has each clip saying who it can see, in its own words. Here those words are joined up:
first within a clip, so that the red top in one window and the red top in the next are one person
rather than two, and then across clips on the shared clock, so that the red top one phone filmed
from the left is the red top another filmed from the right.

Both joins are the same problem — a set of people over here, a set over there, and at most one of
each can be the other — so both are solved the same way, by optimal assignment (scipy's
`linear_sum_assignment`). Greedy matching would happily give two angles' best-looking pair to each
other and leave a third person matched to nobody; assignment weighs the whole set at once.

**Unknown is an answer.** A tracklet that matches nothing stays one person seen from one angle.
Nothing is forced: the cost of a wrong join here is that the story says two different people were
the same person, which is worse than saying nothing. Two people at the same event really can wear
the same black t-shirt, and no amount of description will separate them — when that happens the
honest result is a match this module is not confident about, reported as such.

The words come from a small model, so they are treated as a weak signal throughout: a match needs
clothing that actually distinguishes (a colour *and* a garment, not "a shirt"), and how much two
descriptions agree is scored on those pairs rather than on words in general.
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from scipy.optimize import linear_sum_assignment

from scenefold.fuse import master_time
from scenefold.manifest import ManifestError, load_manifest, normalize_event_id, now
from scenefold.observations import Sighting, load_observations
from scenefold.timeline import ClipPlacement, TimelineError, load_timeline

# How alike two descriptions must be to be the same person. Below this they are left apart.
# Measured on the drawn event and on real stage clips: same-person pairs score 0.5-1.0, different
# people wearing different colours score 0.0-0.2, and different people in similar dark clothing
# land in between — which is why the bar sits above them and those pairs are left unmatched.
SAME_PERSON = 0.45
# Above this, a cross-angle match is reported as confident. Between the two it is reported as a
# match somebody should check, because that is what it is.
SURE = 0.7
# Within one clip, how long somebody may be out of frame and still be taken up again as the same
# person. Clothes do not change during an event, but a gap this long is usually a different scene.
APART_S = 90.0
# Two angles catching somebody at the same time is evidence for, not proof of, one person. It is
# worth this much added to how alike they look — enough to break a tie, never enough to make one.
TOGETHER_BONUS = 0.1
# A description with fewer than this many colour-and-garment pairs describes a type, not a person.
# "a man in a shirt" matches everybody; "a man in a red shirt" matches somebody.
NEEDS_PAIRS = 1
PEOPLE_NAME = "people.json"

COLOURS = {
    "red", "blue", "green", "black", "white", "yellow", "orange", "purple", "pink", "brown",
    "grey", "gray", "maroon", "navy", "beige", "cream", "gold", "silver", "tan", "teal", "olive",
    "burgundy", "turquoise", "lavender", "khaki", "denim", "striped", "floral", "plaid",
    "checked", "patterned", "camouflage", "neon", "pastel",
}  # fmt: skip
# "dark" and "light" only say something next to a real colour, so they are kept as shades and
# never counted as a colour of their own.
SHADES = {"dark", "light", "pale", "bright", "deep"}
GARMENTS = {
    "shirt", "t-shirt", "tshirt", "tee", "top", "blouse", "dress", "skirt", "trousers", "pants",
    "jeans", "shorts", "jacket", "coat", "hoodie", "sweater", "sweatshirt", "jumper", "cardigan",
    "vest", "suit", "tie", "hat", "cap", "beanie", "scarf", "uniform", "jersey", "gown",
    "leggings", "overalls", "poncho", "robe", "kimono", "saree", "sari", "kurta", "lehenga",
}  # fmt: skip
# Said about a garment rather than about a colour, but they separate people just as well, so they
# ride along with the garment: a sleeveless top is not the same top as a long-sleeved one.
CUTS = {"sleeveless", "long-sleeve", "long-sleeved", "short-sleeve", "short-sleeved", "strapless"}
WORD = re.compile(r"[a-z]+(?:-[a-z]+)*")
NEAR = 3  # how many words before a garment are looked at for its colour


class IdentityError(Exception):
    """Nobody can be matched: no timeline, or no clip has been asked who was in it."""


@dataclass(frozen=True)
class Join:
    """One angle matched to another, and how much the two descriptions agreed."""

    one: str  # tracklet ids
    other: str
    score: float
    held: bool  # whether this join is what made the two one person, or only agreed with it


@dataclass(frozen=True)
class Tracklet:
    """One person, in one clip, over the stretch of it they were visible for."""

    tracklet_id: str
    clip_id: str
    t_start_s: float  # in the clip's own seconds
    t_end_s: float
    t_master_start_s: float  # and on the shared clock
    t_master_end_s: float
    wearing: str  # the fullest description of the ones that were joined
    doing: str
    sightings: int  # how many windows this person was seen in
    said: tuple[str, ...] = ()  # every description joined into this one, for looking back at


@dataclass
class Person:
    """Somebody at the event, as far as the footage can tell: one or more angles of one person."""

    person_id: str
    wearing: str
    tracklets: list[Tracklet] = field(default_factory=list)
    joins: list[Join] = field(default_factory=list)  # what was matched to what, and how well

    @property
    def clips(self) -> list[str]:
        return sorted({t.clip_id for t in self.tracklets})

    @property
    def weakest(self) -> float:
        """The weakest join this person is actually held together by.

        Only the joins that did the joining count. A pair that turned out to be the same person by
        some other route is extra evidence, and extra evidence should not lower confidence.
        """
        needed = [join.score for join in self.joins if join.held]
        return min(needed) if needed else 1.0

    @property
    def sure(self) -> bool:
        """Whether this person is held together by joins that leave little doubt."""
        return self.weakest >= SURE

    @property
    def t_master_start_s(self) -> float:
        return min(t.t_master_start_s for t in self.tracklets)

    @property
    def t_master_end_s(self) -> float:
        return max(t.t_master_end_s for t in self.tracklets)


def pairs(text: str) -> set[tuple[str, str]]:
    """The colour-and-garment pairs in a description: 'red sleeveless top' -> {(red, top), ...}.

    A garment takes the colours and cuts said just before it, which is how clothes are described
    in every answer the model gives. A colour with no garment after it is not a pair; it is caught
    separately by `colours`, and counts for less.
    """
    words = WORD.findall(text.lower())
    found: set[tuple[str, str]] = set()
    for index, word in enumerate(words):
        if word not in GARMENTS:
            continue
        for before in words[max(0, index - NEAR) : index]:
            if before in COLOURS or before in CUTS:
                found.add((before, word))
            elif before in SHADES:
                colour = words[index - 1] if words[index - 1] in COLOURS else None
                # Both, when there is a colour under the shade: one angle's "light grey top" and
                # another's "grey top" are one top, and only the shade of the light disagrees.
                found.add((f"{before}-{colour}" if colour else before, word))
                if colour:
                    found.add((colour, word))
    return found


def colours(text: str) -> set[str]:
    """Every colour named in a description, whatever it was said about."""
    return {word for word in WORD.findall(text.lower()) if word in COLOURS}


def look_alike(one: str, other: str) -> float:
    """How alike two descriptions of a person are, from 0 to 1.

    Weighted towards colour-and-garment pairs, because those are what separate one person from the
    next. Bare colours count for less: two people at a lit concert are both partly "black".
    """
    mine, theirs = pairs(one), pairs(other)
    if not mine or not theirs:
        return 0.0
    both_pairs = _overlap(mine, theirs)
    both_colours = _overlap(colours(one), colours(other))
    return round(0.75 * both_pairs + 0.25 * both_colours, 4)


def telling(text: str) -> set[tuple[str, str]]:
    """The pairs that actually separate one person from another.

    "dark jeans" is half the people at any event after sunset, so a shade with no colour behind it
    does not count as having described somebody. "dark blue jeans" does.
    """
    return {(what, garment) for what, garment in pairs(text) if what not in SHADES}


def describes_somebody(wearing: str) -> bool:
    """Whether a description picks out a person at all, rather than a kind of person."""
    return len(telling(wearing)) >= NEEDS_PAIRS


def link_clip(
    clip_id: str,
    sightings: list[Sighting],
    placement: ClipPlacement | None = None,
    *,
    same_person: float = SAME_PERSON,
    apart_s: float = APART_S,
) -> list[Tracklet]:
    """Join one clip's sightings into people: the red top in each window is one person, not five.

    Window by window, because two people in the same frame are two people however alike they look.
    Each window's sightings are assigned to the people already being followed, at most one each.
    """
    usable = [s for s in sightings if describes_somebody(s.wearing)]
    if not usable:
        return []
    open_tracks: list[dict] = []
    for _, window in _by_window(usable):
        alike = np.array(
            [
                [
                    look_alike(track["wearing"], seen.wearing)
                    if seen.t_start_s - track["t_end_s"] <= apart_s
                    else 0.0
                    for seen in window
                ]
                for track in open_tracks
            ],
            dtype=float,
        ).reshape(len(open_tracks), len(window))
        taken: set[int] = set()
        if open_tracks:
            rows, columns = linear_sum_assignment(-alike)
            for row, column in zip(rows, columns, strict=True):
                if alike[row][column] >= same_person:
                    _extend(open_tracks[row], window[column])
                    taken.add(column)
        for index, seen in enumerate(window):
            if index not in taken:
                open_tracks.append(_start(seen))
    return [
        _tracklet(clip_id, f"{clip_id[:8]}-{number}", track, placement)
        for number, track in enumerate(
            sorted(_rejoin(open_tracks), key=lambda t: t["t_start_s"]), 1
        )
    ]


def _rejoin(tracks: list[dict], *, sure: float = SURE) -> list[dict]:
    """Take up again somebody who left the frame and came back, if the clothes leave no doubt.

    The window-by-window pass will not reach across a long gap, which leaves one person filmed
    throughout a clip split into pieces — and a split person cannot be matched to another angle,
    because each angle's people are matched one to one. Bridging a gap is a bigger claim than
    following somebody frame to frame, so it takes a description that is not merely similar.

    Two stretches that overlap in time are never joined: one phone cannot film one person twice
    at once, however alike the two look.
    """
    joined = sorted(tracks, key=lambda t: t["t_start_s"])
    merging = True
    while merging:
        merging = False
        for first in range(len(joined)):
            for second in range(first + 1, len(joined)):
                one, other = joined[first], joined[second]
                if one["t_end_s"] > other["t_start_s"]:  # on screen together
                    continue
                if look_alike(one["wearing"], other["wearing"]) < sure:
                    continue
                one["t_end_s"] = max(one["t_end_s"], other["t_end_s"])
                one["sightings"] += other["sightings"]
                one["said"] += other["said"]
                if len(other["wearing"]) > len(one["wearing"]):
                    one["wearing"] = other["wearing"]
                one["doing"] = one["doing"] or other["doing"]
                joined.pop(second)
                merging = True
                break
            if merging:
                break
    return joined


def match_clips(
    mine: list[Tracklet], theirs: list[Tracklet], *, same_person: float = SAME_PERSON
) -> list[tuple[Tracklet, Tracklet, float]]:
    """Which of one clip's people are which of another's. At most one each, and often none.

    Being visible in both angles at the same moment adds a little to a pair's score. It cannot
    make a match on its own: at a concert everybody is visible at the same moment.
    """
    if not mine or not theirs:
        return []
    scores = np.zeros((len(mine), len(theirs)))
    for row, one in enumerate(mine):
        for column, other in enumerate(theirs):
            alike = look_alike(one.wearing, other.wearing)
            if alike <= 0:
                continue
            scores[row][column] = min(1.0, alike + TOGETHER_BONUS * _together(one, other))
    rows, columns = linear_sum_assignment(-scores)
    return [
        (mine[row], theirs[column], round(float(scores[row][column]), 4))
        for row, column in zip(rows, columns, strict=True)
        if scores[row][column] >= same_person
    ]


def find_people(tracklets: list[Tracklet], *, same_person: float = SAME_PERSON) -> list[Person]:
    """Every person at the event, each one the angles of them that were matched up.

    A tracklet matched to nothing is still a person — somebody one phone filmed and the others
    missed. That is the common case at a real event and not a failure to report.
    """
    by_clip: dict[str, list[Tracklet]] = {}
    for track in tracklets:
        by_clip.setdefault(track.clip_id, []).append(track)
    same = {track.tracklet_id: track.tracklet_id for track in tracklets}
    found: list[tuple[str, str, float]] = []
    clips = sorted(by_clip)
    for first in range(len(clips)):
        for second in range(first + 1, len(clips)):
            found += [
                (one.tracklet_id, other.tracklet_id, score)
                for one, other, score in match_clips(
                    by_clip[clips[first]], by_clip[clips[second]], same_person=same_person
                )
            ]

    # Best evidence first, because a join can be refused and the one it was competing with taken
    # instead. Two angles can each match a third, and the two they leave behind may then be two
    # people who were on screen together — which one person cannot be.
    grouped_by_root: dict[str, list[Tracklet]] = {}
    for track in tracklets:
        grouped_by_root.setdefault(track.tracklet_id, []).append(track)
    joins: list[Join] = []
    for one, other, score in sorted(found, key=lambda j: -j[2]):
        mine, theirs = _root(same, one), _root(same, other)
        if mine == theirs:
            joins.append(Join(one, other, score, held=False))  # one person already, another way
            continue
        if _at_once(grouped_by_root[mine], grouped_by_root[theirs]):
            continue  # both on screen at once somewhere: not one person, whatever they wore
        _join(same, one, other)
        kept = _root(same, one)
        grouped_by_root[kept] = grouped_by_root.pop(mine) + grouped_by_root.pop(theirs)
        joins.append(Join(one, other, score, held=True))

    grouped: dict[str, list[Tracklet]] = {}
    for track in tracklets:
        grouped.setdefault(_root(same, track.tracklet_id), []).append(track)
    people = []
    for number, (_, group) in enumerate(
        sorted(grouped.items(), key=lambda kv: -len({t.clip_id for t in kv[1]})), 1
    ):
        inside = {t.tracklet_id for t in group}
        people.append(
            Person(
                person_id=f"person-{number:02d}",
                wearing=max((t.wearing for t in group), key=len),
                tracklets=sorted(group, key=lambda t: t.t_master_start_s),
                joins=[j for j in joins if j.one in inside and j.other in inside],
            )
        )
    return people


def identify_event(
    event_name: str,
    data_dir: str | Path = "data",
    *,
    same_person: float = SAME_PERSON,
    progress=None,
) -> list[Person]:
    """Match up everybody across the angles of one event, and write down who was found."""
    try:
        event_id = normalize_event_id(event_name)
    except ValueError as exc:
        raise IdentityError(str(exc)) from exc
    event_dir = Path(data_dir) / event_id
    try:
        manifest = load_manifest(event_dir)
        timeline = load_timeline(event_dir)
    except (ManifestError, TimelineError) as exc:
        raise IdentityError(str(exc)) from exc
    if manifest is None:
        raise IdentityError(f"no ingested event at {event_dir}; run `scenefold ingest` first")
    if timeline is None:
        raise IdentityError(f"no timeline yet; run `scenefold sync {event_id}` first")
    placed = {p.clip_id: p for p in timeline.clips}

    tracklets: list[Tracklet] = []
    asked = 0
    for clip in manifest.clips:
        done = load_observations(event_dir, clip.clip_id)
        if done is None or done.people_settings is None:
            continue
        asked += 1
        if progress:
            progress(f"following {len(done.people)} sightings through {clip.source.name}")
        tracklets += link_clip(
            clip.clip_id, done.people, placed.get(clip.clip_id), same_person=same_person
        )
    if not asked:
        raise IdentityError(
            f"no clip has been asked who was in it; run `scenefold people {event_id}` first"
        )
    people = find_people(tracklets, same_person=same_person)
    _write(event_dir, event_id, people, asked)
    return people


def load_people(event_dir: Path) -> dict | None:
    """Who was found at an event, as it was written down, or None if nobody has looked."""
    path = Path(event_dir) / PEOPLE_NAME
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write(event_dir: Path, event_id: str, people: list[Person], clips_asked: int) -> Path:
    across = [p for p in people if len(p.clips) > 1]
    out = {
        "event_id": event_id,
        "created_at": now().isoformat(),
        "clips_asked": clips_asked,
        "people": [
            {
                "person_id": person.person_id,
                "wearing": person.wearing,
                "clips": person.clips,
                "across_angles": len(person.clips) > 1,
                "sure": person.sure,
                "weakest_join": round(person.weakest, 4),
                "t_master_start_s": round(person.t_master_start_s, 3),
                "t_master_end_s": round(person.t_master_end_s, 3),
                "seen": [
                    {
                        "tracklet_id": t.tracklet_id,
                        "clip_id": t.clip_id,
                        "t_start_s": t.t_start_s,
                        "t_end_s": t.t_end_s,
                        "t_master_start_s": t.t_master_start_s,
                        "t_master_end_s": t.t_master_end_s,
                        "wearing": t.wearing,
                        "doing": t.doing,
                        "sightings": t.sightings,
                    }
                    for t in person.tracklets
                ],
                "joins": [
                    {"from": j.one, "to": j.other, "score": j.score, "held_by": j.held}
                    for j in person.joins
                ],
            }
            for person in people
        ],
        "found": {
            "people": len(people),
            "across_angles": len(across),
            "sure": len([p for p in across if p.sure]),
        },
    }
    path = Path(event_dir) / PEOPLE_NAME
    path.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    return path


def _by_window(sightings: list[Sighting]) -> list[tuple[float, list[Sighting]]]:
    """Sightings grouped by the window they were seen in, in time order."""
    windows: dict[float, list[Sighting]] = {}
    for seen in sightings:
        windows.setdefault(round(seen.t_start_s, 3), []).append(seen)
    return sorted(windows.items())


def _start(seen: Sighting) -> dict:
    return {
        "t_start_s": seen.t_start_s,
        "t_end_s": seen.t_end_s,
        "wearing": seen.wearing,
        "doing": seen.doing,
        "sightings": 1,
        "said": [seen.wearing],
    }


def _extend(track: dict, seen: Sighting) -> None:
    track["t_end_s"] = max(track["t_end_s"], seen.t_end_s)
    track["sightings"] += 1
    track["said"].append(seen.wearing)
    # Keep the fullest description of the person: a later window often sees more of them.
    if len(seen.wearing) > len(track["wearing"]):
        track["wearing"] = seen.wearing
    if seen.doing and not track["doing"]:
        track["doing"] = seen.doing


def _tracklet(
    clip_id: str, tracklet_id: str, track: dict, placement: ClipPlacement | None
) -> Tracklet:
    starts, ends = track["t_start_s"], track["t_end_s"]
    on_clock = (
        (master_time(placement, starts), master_time(placement, ends))
        if placement is not None
        else (starts, ends)
    )
    return Tracklet(
        tracklet_id=tracklet_id,
        clip_id=clip_id,
        t_start_s=round(starts, 3),
        t_end_s=round(ends, 3),
        t_master_start_s=round(on_clock[0], 3),
        t_master_end_s=round(on_clock[1], 3),
        wearing=track["wearing"],
        doing=track["doing"],
        sightings=track["sightings"],
        said=tuple(track["said"]),
    )


def _at_once(mine: list[Tracklet], theirs: list[Tracklet]) -> bool:
    """Whether joining these two groups would put one person in two places in the same clip.

    One phone cannot film the same person twice at once. If two tracklets from the same clip
    overlap on the clock, they are two people, and no chain of resemblances makes them one.
    """
    return any(
        one.clip_id == other.clip_id
        and one.t_master_start_s < other.t_master_end_s
        and other.t_master_start_s < one.t_master_end_s
        for one in mine
        for other in theirs
    )


def _together(one: Tracklet, other: Tracklet) -> float:
    """How much of the two angles' time overlaps on the shared clock, from 0 to 1."""
    start = max(one.t_master_start_s, other.t_master_start_s)
    end = min(one.t_master_end_s, other.t_master_end_s)
    if end <= start:
        return 0.0
    shorter = min(
        one.t_master_end_s - one.t_master_start_s, other.t_master_end_s - other.t_master_start_s
    )
    return min(1.0, (end - start) / shorter) if shorter > 0 else 1.0


def _overlap(mine: set, theirs: set) -> float:
    """How much two sets share, counted against the smaller one.

    Against the smaller, not the union: one angle sees a person's whole outfit and another only
    their top half, and they should still be one person.
    """
    if not mine or not theirs:
        return 0.0
    return len(mine & theirs) / min(len(mine), len(theirs))


def _root(same: dict[str, str], key: str) -> str:
    while same[key] != key:
        same[key] = same[same[key]]
        key = same[key]
    return key


def _join(same: dict[str, str], one: str, other: str) -> None:
    mine, theirs = _root(same, one), _root(same, other)
    if mine != theirs:
        same[theirs] = mine
