"""Where each phone stood, worked out from when it heard things.

Sound covers a metre in 2.9 ms, so a phone further from a sound hears it that much later. Sync
already uses this once: `heard_late_s` says how much later than the nearest phone each clip heard
the event, which is its extra distance from whatever makes most of the noise (`picture_offset.py`).

That single number only puts a phone on a *circle* around the stage. A map needs several sounds
made in different places — a clap by one person, a shout at the back, a hit on stage. Each sound
reaches every phone at a slightly different moment, and since sync has already put the clips on one
clock, those arrival times are directly comparable:

    arrival at camera i of sound j  =  when it was made  +  distance(camera i, sound j) / 343 m/s

Solving that for every camera and every sound at once is multilateration. The unknowns are two per
camera (x, y) and three per sound (x, y, when), and every camera-sound pair gives one equation, so

    cameras x sounds  >=  2 x cameras  +  3 x sounds  -  3

(the -3 because sliding, turning the whole map, or looking at it in a mirror change nothing).
Five cameras and five sounds solve comfortably; four cameras need five sounds and are fragile.
Mirroring is never resolvable from sound alone, so a map always says so rather than choosing.
"""

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
from pydantic import BaseModel, ValidationError
from scipy.optimize import least_squares

from scenefold.manifest import normalize_event_id, now
from scenefold.timeline import Timeline, TimelineError, load_timeline

SCHEMA_VERSION = 1
POSITIONS_NAME = "positions.json"
SOUND_M_PER_S = 343.0

MIN_CAMERAS = 3  # two clips only ever give a line, never a map
MIN_ARRIVALS_PER_EVENT = 3  # a sound heard by fewer tells nothing about where the clips are
MIN_EVENTS_PER_CAMERA = 3  # two unknowns need more than two measurements to be worth trusting
# A sound adds three unknowns and a camera two, so bare sufficiency fits almost anything. The map
# is only solved when there are this many measurements to spare.
MIN_REDUNDANCY = 3
ROBUST_SCALE_M = 3.0  # arrivals off by more than this stop pulling on the answer
MAX_MEDIAN_RESIDUAL_M = 5.0  # above this, no arrangement of clips explains the arrivals
SAME_PLACE_M = 1.0  # sounds closer together than this are one place, and give circles only
STARTS = 10  # tries from different starting guesses; the flattest fit wins


class PositionsError(Exception):
    """The positions file exists but cannot be used."""


class MapError(Exception):
    """The run cannot start: bad event name, or no usable timeline."""


class SoundEvent(BaseModel):
    """One sound, heard by several clips at slightly different moments.

    `arrivals_s` is master time: the clock sync put every clip on, so the differences between
    these numbers are travel-time differences and nothing else.
    """

    label: str  # how it was found, e.g. "clap" or "transient", for people reading the file
    arrivals_s: dict[str, float]  # clip_id -> master time this sound reached that clip
    confidence: float  # how clearly the same sound was matched across those clips
    spread_s: float  # widest gap between arrivals; more than ~0.5 s means 170 m apart


class CameraPlace(BaseModel):
    clip_id: str
    name: str  # the clip's file name, for people reading the file
    placed: bool
    x_m: float | None = None  # metres, in a frame where one camera sits at (0, 0)
    y_m: float | None = None
    uncertainty_m: float | None = None  # how far it could move and still fit the arrivals
    from_main_sound_m: float | None = None  # the circle answer: always there when sync measured it
    reason: str | None = None  # why it could not be placed


class SoundPlace(BaseModel):
    label: str
    x_m: float
    y_m: float
    t_master_s: float  # when it was made, on the shared clock
    uncertainty_m: float | None = None


class CameraMap(BaseModel):
    schema_version: int = SCHEMA_VERSION
    event_id: str
    created_at: datetime
    solved: bool  # False when the sounds could not place the cameras in 2D
    reason: str | None = None  # why not, in plain words
    mirror_ambiguous: bool = True  # sound alone cannot tell a map from its mirror image
    # Sync placed the clips by sound, so each already sits ahead by its own travel time. True when
    # sync's picture pass had measured that (`heard_late_s`); False when it was solved here too.
    head_start_from_pictures: bool = False
    residual_ms: float | None = None  # how well the arrivals fit the solved map
    events_used: int = 0
    cameras: list[CameraPlace] = []
    sounds: list[SoundPlace] = []


def metres(seconds: float) -> float:
    """How much further away something is, from how much later its sound arrives."""
    return seconds * SOUND_M_PER_S


def load_positions(event_dir: Path) -> CameraMap | None:
    path = event_dir / POSITIONS_NAME
    if not path.exists():
        return None
    try:
        return CameraMap.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, ValidationError) as exc:
        raise PositionsError(f"{path} is unreadable: {exc}") from exc


def save_positions(event_dir: Path, camera_map: CameraMap) -> Path:
    """Write atomically, so a crash never leaves a half-written file."""
    path = event_dir / POSITIONS_NAME
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(camera_map.model_dump_json(indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


# --- solving the map


@dataclass
class _Fit:
    cameras: np.ndarray  # (C, 2) metres
    sounds: np.ndarray  # (E, 2) metres
    times: np.ndarray  # (E,) master seconds each sound was made
    head_start_s: np.ndarray  # (C,) how far ahead sync put each clip, when it is solved here
    residuals_m: np.ndarray  # one per arrival: how far off its distance is
    cost: float


def solve_map(
    events: list[SoundEvent],
    clips: dict[str, str],
    from_main_sound_m: dict[str, float | None] | None = None,
) -> CameraMap:
    """Where the clips stood, from when each of them heard each sound (pure: no files).

    `clips` maps clip id to file name, and sets the order the cameras are reported in.
    `from_main_sound_m` carries sync's circle answer through, solved or not. The returned map has
    an empty `event_id`; `map_event` fills it in.
    """
    circles = from_main_sound_m or {}

    def refuse(reason: str, per_camera: dict[str, str] | None = None) -> CameraMap:
        specific = per_camera or {}
        return CameraMap(
            event_id="",
            created_at=now(),
            solved=False,
            reason=reason,
            cameras=[
                CameraPlace(
                    clip_id=clip_id,
                    name=name,
                    placed=False,
                    from_main_sound_m=_rounded(circles.get(clip_id), 1),
                    reason=specific.get(clip_id, reason),
                )
                for clip_id, name in clips.items()
            ],
        )

    usable = [
        event
        for event in events
        if len({c for c in event.arrivals_s if c in clips}) >= MIN_ARRIVALS_PER_EVENT
    ]
    if not usable:
        return refuse(
            f"no sound was heard by {MIN_ARRIVALS_PER_EVENT} clips at once, so there is nothing "
            "to place them by"
        )

    heard = {clip_id: sum(clip_id in event.arrivals_s for event in usable) for clip_id in clips}
    # A clip that caught every sound there was has nothing to answer for; too few sounds for
    # everybody is the counting rule's business, further down.
    floor = min(MIN_EVENTS_PER_CAMERA, len(usable))
    quiet = {
        clip_id: f"heard only {count} of the {len(usable)} sounds"
        for clip_id, count in heard.items()
        if count < floor
    }
    cameras = [clip_id for clip_id in clips if clip_id not in quiet]
    if len(cameras) < MIN_CAMERAS:
        return refuse(
            f"only {len(cameras)} clips heard enough of the same sounds; a map needs {MIN_CAMERAS}",
            quiet,
        )
    usable = [
        event
        for event in usable
        if len({c for c in event.arrivals_s if c in cameras}) >= MIN_ARRIVALS_PER_EVENT
    ]

    # Sync lined the clips up by sound, so each clip's offset already swallowed its own travel time
    # from whatever makes most of the noise: a far phone's pictures run ahead by exactly that much.
    # The picture pass measures it (`heard_late_s`, handed here as metres), so where every clip has
    # it, adding it back leaves arrivals that depend only on where the clips and sounds were.
    # Without it, that head start becomes a third unknown per clip, and more sounds are needed.
    known_head_start = all(circles.get(clip_id) is not None for clip_id in cameras)

    measurements = sum(len({c for c in event.arrivals_s if c in cameras}) for event in usable)
    if known_head_start:
        needed = 2 * len(cameras) + 3 * len(usable) - 3 + MIN_REDUNDANCY
        also = ""
    else:
        needed = 3 * len(cameras) + 3 * len(usable) - 4
        also = (
            ", because sync never measured how far each clip stood from the sound, so that has to "
            "be worked out here too"
        )
    if measurements < needed:
        return refuse(
            f"{len(usable)} sounds heard by {len(cameras)} clips give {measurements} measurements; "
            f"placing them needs at least {needed}{also}. More separate sounds would settle it",
            quiet,
        )
    if _one_place_only(cameras, usable):
        return refuse(
            "every sound reached the clips the same way, so they all came from one place: that "
            "puts each clip on a circle around it, not on a map",
            quiet,
        )

    # the two clips in most events anchor the map: one at (0, 0), one due east of it
    order = sorted(cameras, key=lambda clip_id: (-heard[clip_id], cameras.index(clip_id)))
    cam_index = {clip_id: i for i, clip_id in enumerate(order)}
    rows = [
        (cam_index[clip_id], j, seconds)
        for j, event in enumerate(usable)
        for clip_id, seconds in sorted(event.arrivals_s.items())
        if clip_id in cam_index
    ]
    cam_idx = np.array([r[0] for r in rows])
    snd_idx = np.array([r[1] for r in rows])
    arrivals = np.array([r[2] for r in rows])
    if known_head_start:
        arrivals = arrivals + np.array(
            [circles[order[i]] / SOUND_M_PER_S for i in cam_idx]  # type: ignore[operator]
        )

    solve_head_start = not known_head_start
    fit = _fit_once(cam_idx, snd_idx, arrivals, len(order), len(usable), solve_head_start)
    if fit is None:
        return refuse("the arrival times could not be turned into positions at all", quiet)

    median_residual = float(np.median(np.abs(fit.residuals_m)))
    if median_residual > MAX_MEDIAN_RESIDUAL_M:
        return refuse(
            "no arrangement of the clips explains these arrival times (they are off by about "
            f"{median_residual:.0f} m); the sounds are probably not the same sounds",
            quiet,
        )
    if _spread(fit.sounds) < SAME_PLACE_M:
        return refuse(
            "the sounds all come out in one place, so the clips only sit on circles around it, "
            "not on a map",
            quiet,
        )

    fit = _canonical(fit)
    spread_m = _spread(fit.cameras)
    doubt = _uncertainty(cam_idx, snd_idx, arrivals, len(order), len(usable), fit, solve_head_start)
    limit = max(2.0, 0.5 * spread_m)

    places = []
    for clip_id, name in clips.items():
        circle = _rounded(circles.get(clip_id), 1)
        if clip_id in quiet:
            places.append(
                CameraPlace(
                    clip_id=clip_id,
                    name=name,
                    placed=False,
                    from_main_sound_m=circle,
                    reason=quiet[clip_id],
                )
            )
            continue
        i = cam_index[clip_id]
        loose = doubt is not None and doubt[i] > limit
        places.append(
            CameraPlace(
                clip_id=clip_id,
                name=name,
                placed=not loose,
                x_m=None if loose else round(float(fit.cameras[i, 0]), 2),
                y_m=None if loose else round(float(fit.cameras[i, 1]), 2),
                uncertainty_m=None if doubt is None else round(float(doubt[i]), 2),
                from_main_sound_m=circle,
                reason=(
                    f"the sounds do not pin this clip down (give or take {doubt[i]:.0f} m "
                    f"across a {spread_m:.0f} m map)"
                    if loose
                    else None
                ),
            )
        )

    sounds = [
        SoundPlace(
            label=event.label,
            x_m=round(float(fit.sounds[j, 0]), 2),
            y_m=round(float(fit.sounds[j, 1]), 2),
            t_master_s=round(float(fit.times[j]), 3),
        )
        for j, event in enumerate(usable)
    ]
    return CameraMap(
        event_id="",
        created_at=now(),
        solved=any(place.placed for place in places),
        reason=None if any(place.placed for place in places) else "no clip could be pinned down",
        mirror_ambiguous=True,
        head_start_from_pictures=known_head_start,
        residual_ms=round(float(np.sqrt(np.mean(fit.residuals_m**2))) / SOUND_M_PER_S * 1000, 2),
        events_used=len(usable),
        cameras=places,
        sounds=sounds,
    )


def map_event(event_name: str, data_dir: str | Path = "data") -> CameraMap:
    """Read the event's timeline and sounds, solve, write `positions.json`, return the map."""
    try:
        event_id = normalize_event_id(event_name)
    except ValueError as exc:
        raise MapError(str(exc)) from exc
    event_dir = Path(data_dir) / event_id
    try:
        timeline = load_timeline(event_dir)
    except TimelineError as exc:
        raise MapError(str(exc)) from exc
    if timeline is None:
        raise MapError(f"no synced event at {event_dir}; run `scenefold sync` first")

    placed = [clip for clip in timeline.clips if clip.placed]
    clips = {clip.clip_id: clip.name for clip in placed}
    circles = {
        clip.clip_id: (None if clip.heard_late_s is None else metres(clip.heard_late_s))
        for clip in placed
    }
    if len(placed) < MIN_CAMERAS:
        camera_map = CameraMap(
            event_id=event_id,
            created_at=now(),
            solved=False,
            reason=f"only {len(placed)} clips are on the shared clock; a map needs {MIN_CAMERAS}",
            cameras=[
                CameraPlace(
                    clip_id=clip.clip_id,
                    name=clip.name,
                    placed=False,
                    from_main_sound_m=_rounded(circles[clip.clip_id], 1),
                    reason="not enough clips are synced to place any of them",
                )
                for clip in placed
            ],
        )
    else:
        camera_map = solve_map(_load_events(event_dir, timeline), clips, circles)
        camera_map.event_id = event_id
    save_positions(event_dir, camera_map)
    return camera_map


def _load_events(event_dir: Path, timeline: Timeline) -> list[SoundEvent]:
    """The sounds several clips caught (see sound_events.py); patched out in tests."""
    from scenefold import sound_events

    return sound_events.find_sound_events(event_dir, timeline)


def _one_place_only(cameras: list[str], events: list[SoundEvent]) -> bool:
    """True when every sound reached the clips in the same pattern: one place made them all."""
    shared = sorted(set(cameras).intersection(*(set(e.arrivals_s) for e in events)))
    if len(shared) < MIN_ARRIVALS_PER_EVENT:
        return False  # too little in common to tell
    heard = np.array([[event.arrivals_s[clip_id] for clip_id in shared] for event in events])
    patterns = heard - heard.mean(axis=1, keepdims=True)  # when each sound was made drops out
    return SOUND_M_PER_S * float(np.max(np.std(patterns, axis=0))) < SAME_PLACE_M


def _fit_once(
    cam_idx: np.ndarray,
    snd_idx: np.ndarray,
    arrivals: np.ndarray,
    n_cameras: int,
    n_sounds: int,
    head_starts: bool = False,
    guess: _Fit | None = None,
    starts: int = STARTS,
) -> _Fit | None:
    """Positions that best explain the arrivals, from several starting guesses (the flattest wins).

    Sliding, turning, and mirroring the whole map change nothing, so the first camera is pinned at
    (0, 0) and the second on the +x axis. What is left is one number for that second camera, two
    for every other, and three for every sound (where it was, and when it was made).

    With `head_starts`, each clip also gets its own head start on the clock, because sync placed it
    by sound. Shifting every head start one way and every sound's moment the other changes nothing,
    so the first clip's is pinned at zero too.
    """
    span_m = SOUND_M_PER_S * max(
        (
            float(np.ptp(arrivals[snd_idx == j]))
            for j in range(n_sounds)
            if np.count_nonzero(snd_idx == j) > 1
        ),
        default=0.0,
    )
    if span_m <= 0:
        return None
    first = np.array([np.min(arrivals[snd_idx == j]) for j in range(n_sounds)])

    def residuals(params: np.ndarray) -> np.ndarray:
        cameras, sounds, times, ahead = _unpack(params, n_cameras, n_sounds, head_starts)
        gap = cameras[cam_idx] - sounds[snd_idx]
        distance = np.hypot(gap[:, 0], gap[:, 1])
        return SOUND_M_PER_S * (arrivals + ahead[cam_idx] - times[snd_idx]) - distance

    best: _Fit | None = None
    rng = np.random.default_rng(20260925)  # fixed, so the same arrivals give the same map
    for start in range(starts):
        if guess is not None and start == 0:
            cameras, sounds = guess.cameras.copy(), guess.sounds.copy()
            times = guess.times.copy()
        elif start == 0:  # evenly around a circle, then random tries
            angle = np.linspace(0, 2 * np.pi, n_cameras, endpoint=False)
            cameras = span_m / 2 * np.column_stack([np.cos(angle), np.sin(angle)])
            sounds = span_m * rng.uniform(-1, 1, (n_sounds, 2))
            times = first - span_m / 2 / SOUND_M_PER_S
        else:
            cameras = span_m * rng.uniform(-1, 1, (n_cameras, 2))
            sounds = 1.5 * span_m * rng.uniform(-1, 1, (n_sounds, 2))
            times = first - span_m * rng.uniform(0, 1, n_sounds) / SOUND_M_PER_S
        ahead = guess.head_start_s if guess is not None and start == 0 else np.zeros(n_cameras)
        try:
            answer = least_squares(
                residuals,
                _pack(cameras, sounds, times, ahead, head_starts),
                loss="soft_l1",
                f_scale=ROBUST_SCALE_M,
                max_nfev=2000,
            )
        except ValueError:
            continue
        if best is None or answer.cost < best.cost:
            found = _unpack(answer.x, n_cameras, n_sounds, head_starts)
            best = _Fit(*found, residuals(answer.x), float(answer.cost))
        if best.cost < 1e-6 * len(arrivals):
            break  # the arrivals are explained to the millimetre; other starts cannot beat that
    return best


def _pack(
    cameras: np.ndarray,
    sounds: np.ndarray,
    times: np.ndarray,
    ahead: np.ndarray,
    head_starts: bool,
) -> np.ndarray:
    parts = [[cameras[1, 0]], cameras[2:].ravel(), sounds.ravel(), times]
    if head_starts:
        parts.append(ahead[1:])  # the first clip's is pinned at zero
    return np.concatenate(parts)


def _unpack(
    params: np.ndarray, n_cameras: int, n_sounds: int, head_starts: bool
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    cameras = np.zeros((n_cameras, 2))
    cameras[1, 0] = params[0]
    free = 1 + 2 * (n_cameras - 2)
    cameras[2:] = params[1:free].reshape(-1, 2)
    sounds = params[free : free + 2 * n_sounds].reshape(-1, 2)
    times = params[free + 2 * n_sounds : free + 3 * n_sounds]
    ahead = np.zeros(n_cameras)
    if head_starts:
        ahead[1:] = params[free + 3 * n_sounds :]
    return cameras, sounds, times, ahead


def _canonical(fit: _Fit) -> _Fit:
    """The same map, reported the same way every time: second camera east, sounds mostly north."""
    cameras, sounds = fit.cameras.copy(), fit.sounds.copy()
    if cameras[1, 0] < 0:  # turned half way round, which the gauge allows
        cameras[:, :], sounds[:, :] = -cameras, -sounds
    if np.sum(sounds[:, 1]) < 0:  # the mirror nobody can resolve: pick one and say so
        cameras[:, 1], sounds[:, 1] = -cameras[:, 1], -sounds[:, 1]
    return _Fit(cameras, sounds, fit.times, fit.head_start_s, fit.residuals_m, fit.cost)


def _uncertainty(
    cam_idx: np.ndarray,
    snd_idx: np.ndarray,
    arrivals: np.ndarray,
    n_cameras: int,
    n_sounds: int,
    fit: _Fit,
    head_starts: bool,
) -> np.ndarray | None:
    """How far each camera moves when each sound is left out in turn, in metres.

    Leaving one sound out and solving again says how much that camera's place rests on any single
    sound. None when there are too few sounds to leave any out.
    """
    unknowns = 3 if head_starts else 2
    enough = unknowns * n_cameras + 3 * (n_sounds - 1) - 3
    tries = []
    for drop in range(n_sounds):
        keep = snd_idx != drop
        if np.count_nonzero(keep) < enough:
            continue  # without that sound there is not enough left to solve at all
        renumber = np.where(snd_idx[keep] > drop, snd_idx[keep] - 1, snd_idx[keep])
        # deliberately not started from the solved map: a leave-one-out fit has to be free to
        # land somewhere else, or arrivals that fit nothing would come out looking certain
        again = _fit_once(
            cam_idx[keep], renumber, arrivals[keep], n_cameras, n_sounds - 1, head_starts, starts=4
        )
        if again is not None:
            tries.append(_align_points(_canonical(again).cameras, fit.cameras))
    if len(tries) < 2:
        return None
    moved = np.array(tries)
    middle = moved.mean(axis=0)
    spread = np.sqrt(
        (len(tries) - 1) / len(tries) * np.sum((moved - middle) ** 2, axis=0).sum(axis=-1)
    )
    return np.maximum(spread, np.linalg.norm(middle - fit.cameras, axis=1))


def _align_points(source: np.ndarray, target: np.ndarray) -> np.ndarray:
    """`source` slid, turned, and mirrored if that fits better, onto `target` (same order)."""
    middle_source, middle_target = source.mean(axis=0), target.mean(axis=0)
    centred, wanted = source - middle_source, target - middle_target
    best: tuple[float, np.ndarray] | None = None
    for mirror in (1.0, -1.0):
        flipped = centred * np.array([1.0, mirror])
        u, _, vt = np.linalg.svd(flipped.T @ wanted)
        if np.linalg.det(u @ vt) < 0:  # a rotation, never another reflection
            u = u @ np.diag([1.0, -1.0])
        moved = flipped @ (u @ vt)
        error = float(np.sum((moved - wanted) ** 2))
        if best is None or error < best[0]:
            best = (error, moved)
    assert best is not None
    return best[1] + middle_target


def _spread(points: np.ndarray) -> float:
    """The widest gap between any two of them, in metres."""
    if len(points) < 2:
        return 0.0
    gaps = points[:, None, :] - points[None, :, :]
    return float(np.max(np.hypot(gaps[:, :, 0], gaps[:, :, 1])))


def _rounded(value: float | None, digits: int) -> float | None:
    return None if value is None else round(value, digits)
