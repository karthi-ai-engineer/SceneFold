"""Put every clip of an event on one shared clock, using its sound.

Every pair of clips with usable audio is compared (see audio_offset.py). Pairs that overlap too
little, match too weakly, or match in only part of their overlap (the same song on another night)
are set aside. The rest are solved together with weighted least squares, dropping pairs that
disagree with the others. Clips that never overlap directly are still placed through the clips
between them. Clock drift measured on long pairs is solved the same way, so each clip also gets
its own clock rate.

Sound arrives late from far away, so the placed clips are then matched a second time by their
pictures (see picture_offset.py). That says how much later than the nearest clip each phone heard
the event, which is what lines the pictures up rather than the sound.
"""

from collections.abc import Callable
from itertools import combinations
from pathlib import Path
from typing import NamedTuple

import numpy as np

from scenefold import audio_offset, media, picture_offset
from scenefold.manifest import (
    Clip,
    ClipStatus,
    ManifestError,
    load_manifest,
    normalize_event_id,
    now,
)
from scenefold.timeline import (
    ClipPlacement,
    PairMeasurement,
    SyncSettings,
    Timeline,
    save_timeline,
)

NOT_MATCHED = "no confident audio match with the other clips"
SEPARATE = "matches other clips, but not the main group (maybe another night or moment)"


class SyncError(Exception):
    """The run cannot start: bad event name, or no usable manifest."""


class Solution(NamedTuple):
    offsets: dict[str, float]  # t_master of each placed clip's time 0
    drift_ppm: dict[str, float | None]  # each placed clip against the master clock
    pairs: list[PairMeasurement]  # marked used or rejected


def solve_timeline(
    clip_ids: list[str], pairs: list[PairMeasurement], settings: SyncSettings
) -> Solution:
    """Solve clip offsets and clock drift from pairwise measurements (pure).

    Places the main group (the most clips; ties go to the most total confidence, then to clips
    listed first), with the earliest clip at 0. Each pair is judged against the offsets solved
    without it, so one confident wrong pair can't pull the solution toward itself and push
    correct pairs out.
    """
    pairs = [pair.model_copy() for pair in pairs]
    candidates: list[PairMeasurement] = []
    for pair in pairs:
        if pair.clip_a not in clip_ids or pair.clip_b not in clip_ids or pair.clip_a == pair.clip_b:
            raise ValueError(f"pair {pair.clip_a}-{pair.clip_b} does not join two listed clips")
        pair.used, pair.rejected, pair.residual_ms = False, None, None
        if pair.lag_s is None:
            pair.rejected = "short_overlap"
        elif pair.confidence is None or pair.confidence < settings.min_confidence:
            pair.rejected = "low_confidence"
        elif _partial_match(pair, settings):
            pair.rejected = "partial_match"
        else:
            candidates.append(pair)
    if not clip_ids:
        return Solution({}, {}, pairs)

    while True:
        main = _main_group(clip_ids, candidates)
        inside = [p for p in candidates if p.clip_a in main]
        drift = _solve_drift(main, inside)
        offsets, residuals, left_out = _least_squares(main, inside, drift)
        worst = max((abs(r) for r in left_out), default=0.0)
        if worst * 1000 <= settings.max_residual_ms:
            break
        # Around a single loop of pairs every pair disagrees equally: drop the least confident.
        tied = [p for p, r in zip(inside, left_out, strict=True) if abs(r) >= worst - 1e-6]
        dropped = min(tied, key=lambda p: p.confidence)
        dropped.rejected = "inconsistent"
        candidates.remove(dropped)

    for pair, residual in zip(inside, residuals, strict=True):
        pair.used = True
        pair.residual_ms = round(residual * 1000, 3)
    for pair in candidates:
        if pair.clip_a not in main:
            pair.rejected = "separate_group"
    earliest = min(offsets.values())
    return Solution(
        offsets={clip: offset - earliest for clip, offset in offsets.items()},
        drift_ppm={clip: drift.get(clip) for clip in main},
        pairs=pairs,
    )


def _partial_match(pair: PairMeasurement, settings: SyncSettings) -> bool:
    """True when the sound matches in only part of a long overlap.

    That is what the same song played on another night looks like: the recorded backing track
    matches, the singing, talking, and crowd don't. It can be as confident as a true match.
    """
    return (
        (pair.windows or 0) >= settings.agreement_windows
        and pair.agreement is not None
        and pair.agreement < settings.min_agreement
    )


def _main_group(clip_ids: list[str], pairs: list[PairMeasurement]) -> list[str]:
    parent = {clip: clip for clip in clip_ids}

    def root(clip: str) -> str:
        while parent[clip] != clip:
            parent[clip] = parent[parent[clip]]
            clip = parent[clip]
        return clip

    for pair in pairs:
        parent[root(pair.clip_a)] = root(pair.clip_b)
    groups: dict[str, list[str]] = {}
    for clip in clip_ids:  # keeps clip_ids order inside each group
        groups.setdefault(root(clip), []).append(clip)

    def rank(group: list[str]) -> tuple[int, float, int]:
        members = set(group)
        confidence = sum(p.confidence or 0 for p in pairs if p.clip_a in members)
        return len(group), confidence, -clip_ids.index(group[0])

    return max(groups.values(), key=rank)


def _solve_drift(group: list[str], pairs: list[PairMeasurement]) -> dict[str, float]:
    """Drift of every clip that has a drift measurement, against the average of their clocks.

    A longer overlap pins drift down much more precisely, so pairs weigh by overlap^3.
    """
    measured = [p for p in pairs if p.drift_ppm is not None]
    clips = [c for c in group if any(c in (p.clip_a, p.clip_b) for p in measured)]
    if not measured:
        return {}
    column = {clip: i for i, clip in enumerate(clips)}
    rows = np.zeros((len(measured), len(clips)))
    values = np.zeros(len(measured))
    for row, pair in enumerate(measured):
        weight = (pair.overlap_s or 0.0) ** 1.5
        rows[row, column[pair.clip_b]] = weight
        rows[row, column[pair.clip_a]] = -weight
        values[row] = weight * pair.drift_ppm
    # the minimum-norm solution averages 0 within each connected set of clips
    solution = np.linalg.lstsq(rows, values, rcond=None)[0]
    return {clip: float(solution[i]) for clip, i in column.items()}


def _least_squares(
    group: list[str], pairs: list[PairMeasurement], drift: dict[str, float]
) -> tuple[dict[str, float], np.ndarray, np.ndarray]:
    """Offsets for one connected group with its first clip fixed at 0, weighted by confidence.

    Also returns each pair's residual (seconds) and its left-out residual: how far the pair is
    from the offsets solved without it, residual / (1 - leverage). A pair that alone joins two
    parts of the group can't be checked and gets 0.
    """
    if len(group) == 1:
        return {group[0]: 0.0}, np.zeros(0), np.zeros(0)
    column = {clip: i - 1 for i, clip in enumerate(group)}  # the first clip has no column
    rows = np.zeros((len(pairs), len(group) - 1))
    values = np.zeros(len(pairs))
    weights = np.sqrt([pair.confidence for pair in pairs])
    for row, (pair, weight) in enumerate(zip(pairs, weights, strict=True)):
        if column[pair.clip_b] >= 0:
            rows[row, column[pair.clip_b]] = weight
        if column[pair.clip_a] >= 0:
            rows[row, column[pair.clip_a]] = -weight
        # lag_s is on A's clock; master seconds are (1 + drift) times longer
        values[row] = weight * pair.lag_s / (1 + drift.get(pair.clip_a, 0.0) * 1e-6)
    solution = np.linalg.lstsq(rows, values, rcond=None)[0]
    residuals = (rows @ solution - values) / weights
    leverage = np.einsum("ij,ji->i", rows, np.linalg.pinv(rows))
    free = 1 - leverage
    left_out = np.divide(residuals, free, out=np.zeros_like(residuals), where=free > 1e-9)
    offsets = {clip: 0.0 if i < 0 else float(solution[i]) for clip, i in column.items()}
    return offsets, residuals, left_out


def measure_pictures(
    event_dir: Path,
    clips: dict[str, Clip],
    solution: Solution,
    settings: SyncSettings,
    progress: Callable[[str], None] | None = None,
) -> dict[str, float]:
    """How much later than the nearest clip each placed clip heard the event, from the pictures.

    Every pair of placed clips is matched again on its brightness alone, searching either side of
    where the sound put it. Matches that don't stand clear of far-away lags are ignored, and the
    rest are solved into one delay per clip. Empty when the light never changed enough to tell,
    which is the honest answer for a steadily lit room. The pair rows are filled in as it goes.
    """
    placed = [clip_id for clip_id in solution.offsets if clips[clip_id].proxy]
    if len(placed) < 2:
        return {}
    curves: dict[str, np.ndarray] = {}
    for clip_id in placed:
        if progress:
            progress(f"reading the pictures of {clips[clip_id].source.name}")
        try:
            curves[clip_id] = picture_offset.load_brightness(event_dir / clips[clip_id].proxy.video)
        except (media.ProxyError, media.MediaToolsError, OSError):
            continue  # one unreadable clip must not stop the others
    differences: list[tuple[str, str, float, float]] = []
    order: list[PairMeasurement] = []
    # Each pair is measured the way its own row reads, A then B, so the two answers for a pair
    # (what its sound says, what its pictures say) always describe the same direction.
    for row in solution.pairs:
        a, b = row.clip_a, row.clip_b
        if a not in curves or b not in curves:
            continue
        rate_a = 1 + (solution.drift_ppm.get(a) or 0.0) * 1e-6
        sound_lag = (solution.offsets[b] - solution.offsets[a]) * rate_a  # on A's clock
        found = picture_offset.match_pictures(
            curves[a],
            curves[b],
            sound_lag,
            search_s=settings.picture_search_s,
            min_overlap_s=settings.min_overlap_s,
            drift_ppm=(solution.drift_ppm.get(b) or 0.0) - (solution.drift_ppm.get(a) or 0.0),
        )
        if found is None:
            continue
        difference = (found.lag_s - sound_lag) / rate_a  # master seconds
        row.picture_lag_s = round(found.lag_s, 6)
        row.picture_clearness = round(found.clearness, 2)
        row.picture_difference_ms = round(difference * 1000, 1)
        if found.clearness < settings.min_clearness:
            continue
        differences.append((a, b, difference, float(np.sqrt(found.clearness))))
        order.append(row)

    late = picture_offset.solve_delays(
        [c for c in placed if c in curves],
        differences,
        max_residual_s=settings.max_picture_residual_ms / 1000,
    )
    for row, used in zip(order, late.used, strict=True):
        row.picture_used = used
    return late.heard_late_s


def sync_event(
    event_name: str,
    data_dir: str | Path = "data",
    settings: SyncSettings | None = None,
    *,
    pictures: bool = True,
    progress: Callable[[str], None] | None = None,
) -> Timeline:
    """Measure, solve, and write `timeline.json` for an ingested event.

    With `pictures`, the placed clips are matched a second time by their brightness, which says how
    far each phone stood from the sound. It has to decode every working copy, so it is the slow
    part of a sync; leave it out to place clips by sound alone.
    """
    settings = settings or SyncSettings()
    try:
        event_id = normalize_event_id(event_name)
    except ValueError as exc:
        raise SyncError(str(exc)) from exc
    event_dir = Path(data_dir) / event_id
    try:
        manifest = load_manifest(event_dir)
    except ManifestError as exc:
        raise SyncError(str(exc)) from exc
    if manifest is None:
        raise SyncError(f"no ingested event at {event_dir}; run `scenefold ingest` first")

    reasons = {clip.clip_id: _unusable_reason(clip, event_dir) for clip in manifest.clips}
    usable = [clip for clip in manifest.clips if reasons[clip.clip_id] is None]
    audio = {
        clip.clip_id: audio_offset.load_audio(event_dir / clip.proxy.audio, settings.analysis_rate)
        for clip in usable
    }
    measured = []
    for a, b in combinations(usable, 2):
        found = audio_offset.measure_offset(
            audio[a.clip_id],
            audio[b.clip_id],
            settings.analysis_rate,
            beta=settings.phat_beta,
            min_overlap_s=settings.min_overlap_s,
            window_s=settings.window_s,
            max_drift_ppm=settings.max_drift_ppm,
        )
        measured.append(
            PairMeasurement(
                clip_a=a.clip_id,
                clip_b=b.clip_id,
                lag_s=found.lag_s if found else None,
                confidence=found.confidence if found else None,
                overlap_s=found.overlap_s if found else None,
                drift_ppm=_rounded(found.drift_ppm if found else None, 3),
                windows=found.windows if found else None,
                agreement=_rounded(found.agreement if found else None, 3),
            )
        )

    # longest clips first, so a tie between equally matched groups keeps the longer footage
    order = sorted(usable, key=lambda clip: -clip.proxy.duration_s)
    solution = solve_timeline([clip.clip_id for clip in order], measured, settings)
    pairs = solution.pairs

    late: dict[str, float] = {}
    if pictures:
        by_id = {clip.clip_id: clip for clip in manifest.clips}
        late = measure_pictures(event_dir, by_id, solution, settings, progress)

    placements = []
    for clip in manifest.clips:
        duration = clip.proxy.duration_s if clip.proxy else (clip.source.duration_s or 0.0)
        if clip.clip_id in solution.offsets:
            best = [p.confidence for p in pairs if p.used and clip.clip_id in (p.clip_a, p.clip_b)]
            placements.append(
                ClipPlacement(
                    clip_id=clip.clip_id,
                    name=clip.source.name,
                    placed=True,
                    offset_s=round(solution.offsets[clip.clip_id], 6),
                    drift_ppm=_rounded(solution.drift_ppm[clip.clip_id], 2),
                    duration_s=duration,
                    confidence=max(best) if best else None,
                    heard_late_s=_rounded(late.get(clip.clip_id), 6),
                )
            )
        else:
            separate = any(
                p.rejected == "separate_group" and clip.clip_id in (p.clip_a, p.clip_b)
                for p in pairs
            )
            placements.append(
                ClipPlacement(
                    clip_id=clip.clip_id,
                    name=clip.source.name,
                    placed=False,
                    duration_s=duration,
                    reason=reasons[clip.clip_id] or (SEPARATE if separate else NOT_MATCHED),
                )
            )

    ends = [
        p.offset_s + p.duration_s / (1 + (p.drift_ppm or 0) * 1e-6) for p in placements if p.placed
    ]
    timeline = Timeline(
        event_id=event_id,
        created_at=now(),
        settings=settings,
        duration_s=round(max(ends), 6) if ends else 0.0,
        clips=placements,
        pairs=pairs,
    )
    save_timeline(event_dir, timeline)
    return timeline


def _rounded(value: float | None, digits: int) -> float | None:
    return None if value is None else round(value, digits)


def _unusable_reason(clip: Clip, event_dir: Path) -> str | None:
    if clip.status is ClipStatus.FAILED or clip.proxy is None:
        return "ingest could not process this clip"
    if clip.proxy.audio is None:
        return "no usable audio"
    if any(issue.code == "silent_audio" for issue in clip.issues):
        return "the audio is silent"
    if not (event_dir / clip.proxy.audio).is_file():
        return "its audio file is missing; run `scenefold ingest` again"
    return None
