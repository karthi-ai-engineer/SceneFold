"""Cut the angles together into one film: `cut.json` (with a reason per shot) and `cut.mp4`.

No AI: every choice comes from how good each angle's picture is (quality.py) and from three rules
an editor would recognise. Hold a shot for at least a few seconds. Don't cut unless the new angle
is clearly better, because a cut costs the viewer something. Don't bounce straight back to the
angle you just left. Those rules are weighed against the picture scores all at once, over the whole
event, so the film is the best sequence of shots rather than the best angle second by second.

The sound comes from one microphone and runs unbroken: the film lasts exactly as long as that clip
was recording, and the angles are cut over it. So footage outside that clip's span is not used --
the price of never cutting the sound, which a later phase can pay differently.

Pictures are lined up the way picture_offset.py allows (offset + heard_late), so every shot shows
the same instant of the event rather than the moment its phone heard it.
"""

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from scenefold import interest as interest_mod
from scenefold import media, quality
from scenefold.interest import Interest
from scenefold.manifest import (
    Clip,
    ManifestError,
    load_manifest,
    normalize_event_id,
    now,
)
from scenefold.timeline import ClipPlacement, Timeline, TimelineError, load_timeline

SCHEMA_VERSION = 1
CUT_NAME = "cut.json"
FILM_NAME = "cut.mp4"
ONLY_ANGLE = "the only angle recording"
KEPT_ROLLING = "kept rolling: cutting away would have cost more than it gained"
SAW_IT = "caught what was happening, which the other angles missed"
SAW_IT_TOO = "caught what was happening, and had the better picture of those that did"
BETTER = {
    "sharpness": "sharpest picture of the angles recording",
    "steadiness": "steadiest picture of the angles recording",
    "exposure": "best exposed of the angles recording",
}


class CutError(Exception):
    """The cut cannot be made: no timeline, no usable clips, or FFmpeg failed."""


class CutSettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    min_shot_s: float = Field(3.0, gt=0)  # long enough to see what the angle shows
    max_shot_s: float = Field(12.0, gt=0)  # long enough to settle, short enough to keep moving
    # A cut has to earn its keep: the new angle must beat the old one by this much, averaged over
    # the shot (scores run 0 to 1). Returning to the angle before last costs more still.
    cut_cost: float = Field(0.35, ge=0)
    return_cost: float = Field(0.25, ge=0)
    width: int = Field(1280, gt=0)  # the film's size; portrait clips are padded into it
    height: int = Field(720, gt=0)


class Shot(BaseModel):
    clip_id: str
    name: str
    start_s: float  # master time this shot starts
    end_s: float
    local_start_s: float  # where that is in the clip's own working copy
    local_end_s: float
    # Phone clocks differ by a few hundred parts per million, so a shot is stretched by this much
    # to run on the film's clock, which is the microphone's. Invisible; it keeps sound and picture
    # together over a long film (300 ppm adds up to 90 ms over five minutes).
    speed: float = 1.0
    score: float  # mean picture score over the shot, 0 to 1
    reason: str


class Film(BaseModel):
    schema_version: int = SCHEMA_VERSION
    event_id: str
    created_at: datetime
    settings: CutSettings
    duration_s: float  # on the master clock; the film itself runs on the microphone's clock
    start_s: float  # where the film begins on the master clock
    audio_clip_id: str  # the one microphone, heard unbroken from start to end
    audio_reason: str
    audio_end_s: float  # how much of that clip's sound the film uses, from its start
    shots: list[Shot]


@dataclass(frozen=True)
class Angle:
    """One placed clip, with its picture scores on the master clock."""

    placement: ClipPlacement
    clip: Clip
    scores: np.ndarray  # per master second from the film's start; NaN where it wasn't recording
    measures: dict[str, np.ndarray]  # the same for each raw measure, to explain a choice
    # What the event store says this angle was pointed at, per second, already weighted. Zero
    # everywhere when nothing has been fused yet, which leaves the picture score deciding alone.
    saw: np.ndarray | None = None

    @property
    def clip_id(self) -> str:
        return self.placement.clip_id

    @property
    def worth(self) -> np.ndarray:
        """What a second of this angle is worth: how it looks, plus what it was pointed at."""
        if self.saw is None:
            return self.scores
        return self.scores + self.saw

    def local_time(self, master_s: float) -> float:
        """Where a master time sits in this clip, with its pictures lined up (not its sound)."""
        start = self.placement.offset_s + (self.placement.heard_late_s or 0.0)
        return (master_s - start) * (1 + (self.placement.drift_ppm or 0.0) * 1e-6)


def choose_microphone(placements: list[ClipPlacement]) -> tuple[ClipPlacement, str]:
    """The clip whose sound carries the film: the longest, and of equals the nearest to the sound.

    Being nearest matters because that clip's sound is the least delayed, so it fits the pictures
    (which show the event itself) as closely as a single microphone can.
    """
    best = max(placements, key=lambda c: (round(c.duration_s, 1), -(c.heard_late_s or 0.0)))
    longest = max(c.duration_s for c in placements)
    if len(placements) == 1:
        return best, "the only clip on the clock"
    if sum(1 for c in placements if round(c.duration_s, 1) == round(longest, 1)) > 1:
        return best, "recorded longest, and nearest to the sound of those"
    return best, "recorded longest, so its sound covers the most of the event"


def plan_cut(
    timeline: Timeline,
    scores: dict[str, quality.Scored],
    clips: dict[str, Clip],
    settings: CutSettings | None = None,
    interest: Interest | None = None,
) -> Film:
    """Choose the shots (pure): which angle to show when, and why.

    `interest` is what the event store knows about this stretch of clock (interest.py). Without it
    the film is chosen on the pictures alone, which is what it did before Phase 9.
    """
    settings = settings or CutSettings()
    placed = [c for c in timeline.clips if c.placed and c.clip_id in clips]
    if not placed:
        raise CutError("no clip is on the clock; run `scenefold sync` first")
    microphone, audio_reason = choose_microphone(placed)
    start_s = microphone.offset_s + (microphone.heard_late_s or 0.0)
    seconds = int(microphone.duration_s / (1 + (microphone.drift_ppm or 0.0) * 1e-6))
    if seconds < 1:
        raise CutError("the longest clip is under a second long; there is nothing to cut")

    angles = []
    for placement in placed:
        own = scores.get(placement.clip_id)
        if own is None or not len(own.combined):
            continue
        on_clock = _on_master(placement, own.combined, start_s, seconds)
        if np.isnan(on_clock).all():
            continue  # recorded, but never while the microphone was running
        measures = {
            name: _on_master(placement, values, start_s, seconds)
            for name, values in own.measures.items()
        }
        saw = interest.worth(placement.clip_id) if interest is not None else None
        angles.append(Angle(placement, clips[placement.clip_id], on_clock, measures, saw))
    if not angles:
        raise CutError("no clip's pictures could be scored; are the working copies there?")
    shots = _choose_shots(angles, seconds, settings, interest)
    mic_rate = 1 + (microphone.drift_ppm or 0.0) * 1e-6
    return Film(
        event_id=timeline.event_id,
        created_at=now(),
        settings=settings,
        duration_s=round(float(seconds), 3),
        start_s=round(start_s, 6),
        audio_clip_id=microphone.clip_id,
        audio_reason=audio_reason,
        audio_end_s=round(seconds * mic_rate, 3),
        shots=[_describe(shot, angles, start_s, mic_rate, interest) for shot in shots],
    )


def _on_master(
    placement: ClipPlacement, own: np.ndarray, start_s: float, seconds: int
) -> np.ndarray:
    """One clip's per-second values, moved onto the master clock; NaN where it wasn't recording."""
    out = np.full(seconds, np.nan)
    offset = placement.offset_s + (placement.heard_late_s or 0.0)
    rate = 1 + (placement.drift_ppm or 0.0) * 1e-6
    for second in range(seconds):
        first = (start_s + second - offset) * rate
        # the clip has to cover the whole second, or the shot would run past what it filmed
        if first >= 0 and first + rate <= len(own):
            out[second] = own[int(first)]
    return out


def _choose_shots(
    angles: list[Angle], seconds: int, settings: CutSettings, interest: Interest | None = None
) -> list[tuple[int, int, int]]:
    """The best sequence of shots over the whole film: (angle index, first second, last second+1).

    Every way the film could be built is weighed at once. `best[boundary]` holds, for each pair
    (the angle on screen, the angle of the shot before it), the best score of everything up to that
    boundary and how it got there, so a cut is only made when the rest of the film pays for it.

    A piece may also hold the angle it already had, which costs nothing and is merged back into one
    shot afterwards. Without that, a stretch where only one phone was filming could not be covered
    at all, since no second angle exists to cut to.
    """
    lengths = range(int(settings.min_shot_s), int(settings.max_shot_s) + 1)
    sums = [
        np.concatenate([[0.0], np.nan_to_num(angle.worth, nan=0.0).cumsum()]) for angle in angles
    ]
    # What a cut costs at each second: more where something is happening, so the film cuts on the
    # quiet before a moment rather than across it.
    costs = (
        np.array(
            [
                interest_mod.cutting_cost(interest, at, settings.cut_cost)
                for at in range(seconds + 1)
            ]
        )
        if interest is not None and interest.known
        else np.full(seconds + 1, settings.cut_cost)
    )
    # (angle, the angle before it) -> (score so far, the piece that ended here, where it came from)
    best: list[dict[tuple[int, int], tuple[float, tuple[int, int, int], tuple[int, int] | None]]]
    best = [{} for _ in range(seconds + 1)]
    best[0][(-1, -1)] = (0.0, (-1, 0, 0), None)
    for end in range(1, seconds + 1):
        for index, angle in enumerate(angles):
            for length in lengths:
                first = end - length
                if first < 0 or np.isnan(angle.scores[first:end]).any():
                    continue  # this angle was not recording throughout the piece
                gained = float(sums[index][end] - sums[index][first])
                for (previous, before), (so_far, _, _) in best[first].items():
                    held = previous == index
                    cost = 0.0 if held or first == 0 else float(costs[first])
                    if not held and before == index:
                        cost += settings.return_cost  # straight back to the angle before last
                    value = so_far + gained - cost
                    key = (index, before if held else previous)
                    if key not in best[end] or value > best[end][key][0]:
                        best[end][key] = (value, (index, first, end), (previous, before))
    if not best[seconds]:
        return _one_angle_all_through(angles, seconds)

    key = max(best[seconds], key=lambda k: best[seconds][k][0])
    pieces, at = [], seconds
    while at > 0:
        _, piece, came_from = best[at][key]
        pieces.append(piece)
        at, key = piece[1], came_from
    return _merge(list(reversed(pieces)))


def _merge(pieces: list[tuple[int, int, int]]) -> list[tuple[int, int, int]]:
    """Pieces that held the same angle are one shot."""
    shots = [pieces[0]]
    for index, first, end in pieces[1:]:
        if index == shots[-1][0]:
            shots[-1] = (index, shots[-1][1], end)
        else:
            shots.append((index, first, end))
    return shots


def _one_angle_all_through(angles: list[Angle], seconds: int) -> list[tuple[int, int, int]]:
    """When no shot fits at all (a film shorter than one shot), show the best angle throughout."""
    whole = [i for i, angle in enumerate(angles) if not np.isnan(angle.scores).any()]
    if not whole:
        whole = list(range(len(angles)))
    return [(max(whole, key=lambda i: np.nanmean(angles[i].scores)), 0, seconds)]


def _describe(
    shot: tuple[int, int, int],
    angles: list[Angle],
    start_s: float,
    mic_rate: float,
    interest: Interest | None = None,
) -> Shot:
    """Turn a chosen shot into the record of it, with why that angle won."""
    index, first, end = shot
    angle = angles[index]
    mine = float(np.nanmean(angle.scores[first:end]))
    others = {
        other.clip_id: float(np.nanmean(other.scores[first:end]))
        for position, other in enumerate(angles)
        if position != index and not np.isnan(other.scores[first:end]).all()
    }
    rivals = [
        other
        for position, other in enumerate(angles)
        if position != index and not np.isnan(other.scores[first:end]).all()
    ]
    alone = sum(
        1 for second in range(first, end) if all(np.isnan(other.scores[second]) for other in rivals)
    )
    if not rivals or alone > (end - first) / 2:
        reason = ONLY_ANGLE
    elif (watching := _saw_most(angle, rivals, first, end)) is not None:
        # It won on what it was pointed at, so say that rather than naming a picture measure: it
        # is the truer reason, and the one a person would give.
        reason = watching
    elif mine < max(others.values()):
        reason = KEPT_ROLLING
    else:
        reason = BETTER[_strongest(angle, rivals, first, end)]
    rate = 1 + (angle.placement.drift_ppm or 0.0) * 1e-6
    return Shot(
        clip_id=angle.clip_id,
        name=angle.clip.source.name,
        start_s=round(start_s + first, 3),
        end_s=round(start_s + end, 3),
        local_start_s=round(max(0.0, angle.local_time(start_s + first)), 3),
        local_end_s=round(angle.local_time(start_s + end), 3),
        speed=round(mic_rate / rate, 9),
        score=round(mine, 4),
        reason=reason,
    )


def _saw_most(angle: Angle, rivals: list[Angle], first: int, end: int) -> str | None:
    """Whether this angle won the shot by being pointed at what happened, and how clearly.

    None when nothing much happened here, or when the angles that saw it are level: then the
    picture decided, and the reason should say so.
    """
    mine = _mean(angle.saw, first, end)
    if mine <= 0:
        return None
    theirs = max((_mean(other.saw, first, end) for other in rivals), default=0.0)
    if mine <= theirs:
        return None
    # It saw more of what happened than any rival. Did the others see it at all?
    return SAW_IT if theirs <= 0 else SAW_IT_TOO


def _strongest(angle: Angle, rivals: list[Angle], first: int, end: int) -> str:
    """Which measure this angle beat the others by most, over the shot.

    Each measure is compared after stretching it over the range the event shows, so sharpness and
    steadiness can be weighed against each other at all.
    """
    best, lead = "sharpness", -np.inf
    for name in quality.WEIGHTS:
        mine = _mean(angle.measures.get(name), first, end)
        theirs = max(_mean(other.measures.get(name), first, end) for other in rivals)
        if mine - theirs > lead:
            best, lead = name, mine - theirs
    return best


def _mean(values: np.ndarray | None, first: int, end: int) -> float:
    """A measure's average over a stretch of the film, ignoring seconds it was not recording."""
    if values is None or not len(values):
        return 0.0
    piece = values[first:end]
    return 0.0 if np.isnan(piece).all() else float(np.nanmean(piece))


def render_cut(event_dir: Path, film: Film, clips: dict[str, Clip]) -> Path:
    """Make the film itself: each shot trimmed from its working copy, over one unbroken sound.

    Clips are filmed at different shapes, so every shot is fitted into one frame with black bars
    where it doesn't fill it, rather than cropping away what someone chose to film.
    """
    if not film.shots:
        raise CutError("the cut has no shots")
    used = list(dict.fromkeys(shot.clip_id for shot in film.shots))
    inputs: list[str] = []
    for clip_id in [*used, film.audio_clip_id]:
        clip = clips.get(clip_id)
        if clip is None or clip.proxy is None:
            raise CutError(f"clip {clip_id} has no working copy; run `scenefold ingest` again")
        inputs += ["-i", str(event_dir / clip.proxy.video)]
    width, height = film.settings.width, film.settings.height
    fit = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1"
    )
    steps, parts = [], []
    for number, shot in enumerate(film.shots):
        steps.append(
            f"[{used.index(shot.clip_id)}:v]"
            f"trim=start={shot.local_start_s}:end={shot.local_end_s},"
            f"setpts=(PTS-STARTPTS)*{shot.speed},fps=30,{fit}[v{number}]"
        )
        parts.append(f"[v{number}]")
    steps.append(f"{''.join(parts)}concat=n={len(parts)}:v=1:a=0[v]")
    # apad, then -shortest: a microphone that stops early leaves quiet, never a shortened film
    steps.append(
        f"[{len(used)}:a]atrim=start=0:end={film.audio_end_s},asetpts=PTS-STARTPTS,apad[a]"
    )

    out = event_dir / FILM_NAME
    failed = media.render(
        [*inputs, "-filter_complex", ";".join(steps),
         "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "medium", "-crf", "21",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
         "-shortest", str(out)]
    )  # fmt: skip
    if failed:
        raise CutError(f"FFmpeg could not render the film: {failed}")
    return out


def cut_event(
    event_name: str,
    data_dir: str | Path = "data",
    settings: CutSettings | None = None,
    *,
    render: bool = True,
    progress=None,
) -> Film:
    """Score every placed clip, choose the shots, write `cut.json`, and render `cut.mp4`."""
    try:
        event_id = normalize_event_id(event_name)
    except ValueError as exc:
        raise CutError(str(exc)) from exc
    event_dir = Path(data_dir) / event_id
    try:
        manifest, timeline = load_manifest(event_dir), load_timeline(event_dir)
    except (ManifestError, TimelineError) as exc:
        raise CutError(str(exc)) from exc
    if manifest is None:
        raise CutError(f"no ingested event at {event_dir}; run `scenefold ingest` first")
    if timeline is None:
        raise CutError(f"no timeline at {event_dir}; run `scenefold sync` first")

    clips = {clip.clip_id: clip for clip in manifest.clips}
    measured = []
    for placement in timeline.clips:
        clip = clips.get(placement.clip_id)
        if not placement.placed or clip is None or clip.proxy is None:
            continue
        if progress:
            progress(f"looking at {clip.source.name}")
        measured.append(quality.score_clip(clip.clip_id, event_dir / clip.proxy.video))
    film = plan_cut(
        timeline,
        quality.combine(measured),
        clips,
        settings,
        interest=_what_happened(event_dir, timeline, clips, settings, progress),
    )
    save_cut(event_dir, film)
    if render:
        if progress:
            progress(f"rendering {len(film.shots)} shots")
        render_cut(event_dir, film, clips)
    return film


def _what_happened(
    event_dir: Path,
    timeline: Timeline,
    clips: dict[str, Clip],
    settings: CutSettings | None,
    progress=None,
) -> Interest | None:
    """What the event store knows about the stretch the film will cover, if anything does.

    The film's span is worked out the same way plan_cut does, because interest has to line up
    second for second with the picture scores it is added to.
    """
    placed = [c for c in timeline.clips if c.placed and c.clip_id in clips]
    if not placed:
        return None
    microphone, _ = choose_microphone(placed)
    start_s = microphone.offset_s + (microphone.heard_late_s or 0.0)
    seconds = int(microphone.duration_s / (1 + (microphone.drift_ppm or 0.0) * 1e-6))
    found = interest_mod.read_interest(event_dir, start_s, seconds, [c.clip_id for c in placed])
    if progress:
        progress(
            f"reading what happened: {found.events} moments"
            if found.known
            else "no event store yet, so the pictures decide alone"
        )
    return found if found.known else None


def save_cut(event_dir: Path, film: Film) -> Path:
    """Write atomically, so a crash never leaves a half-written file."""
    path = event_dir / CUT_NAME
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(film.model_dump_json(indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def load_cut(event_dir: Path) -> Film | None:
    path = event_dir / CUT_NAME
    if not path.exists():
        return None
    try:
        return Film.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, ValidationError) as exc:
        raise CutError(f"{path} is unreadable: {exc}") from exc
