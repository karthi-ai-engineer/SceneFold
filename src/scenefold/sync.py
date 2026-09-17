"""Put every clip of an event on one shared clock, using its sound.

Every pair of clips with usable audio is compared (see audio_offset.py). The pairwise lags are then
solved together with weighted least squares, dropping pairs that disagree with the rest. Clips that
never overlap directly are still placed through the clips between them.
"""

from itertools import combinations
from pathlib import Path

import numpy as np

from scenefold import audio_offset
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


class SyncError(Exception):
    """The run cannot start: bad event name, or no usable manifest."""


def solve_offsets(
    clip_ids: list[str], pairs: list[PairMeasurement], settings: SyncSettings
) -> tuple[dict[str, float], list[PairMeasurement]]:
    """Solve clip offsets from pairwise lags (pure).

    Returns the offsets of the main group (the most clips; ties go to the most total confidence,
    then to clips listed first), with the earliest clip at 0, and the pairs marked used or rejected.
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
        else:
            candidates.append(pair)
    if not clip_ids:
        return {}, pairs

    while True:
        main = _main_group(clip_ids, candidates)
        inside = [p for p in candidates if p.clip_a in main]
        offsets = _least_squares(main, inside)
        residuals = [abs(_residual_ms(p, offsets)) for p in inside]
        if inside and max(residuals) > settings.max_residual_ms:
            worst = inside[int(np.argmax(residuals))]
            worst.rejected = "inconsistent"
            candidates.remove(worst)
            continue
        break

    for pair in candidates:
        if pair.clip_a in main:
            pair.used = True
            pair.residual_ms = round(_residual_ms(pair, offsets), 3)
        else:
            pair.rejected = "separate_group"
    earliest = min(offsets.values())
    return {clip: offset - earliest for clip, offset in offsets.items()}, pairs


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


def _least_squares(group: list[str], pairs: list[PairMeasurement]) -> dict[str, float]:
    """Offsets for one connected group with its first clip fixed at 0, weighted by confidence."""
    if len(group) == 1:
        return {group[0]: 0.0}
    column = {clip: i - 1 for i, clip in enumerate(group)}  # the first clip has no column
    rows = np.zeros((len(pairs), len(group) - 1))
    values = np.zeros(len(pairs))
    for row, pair in enumerate(pairs):
        weight = np.sqrt(pair.confidence)
        if column[pair.clip_b] >= 0:
            rows[row, column[pair.clip_b]] = weight
        if column[pair.clip_a] >= 0:
            rows[row, column[pair.clip_a]] = -weight
        values[row] = weight * pair.lag_s
    solution = np.linalg.lstsq(rows, values, rcond=None)[0]
    return {clip: 0.0 if i < 0 else float(solution[i]) for clip, i in column.items()}


def _residual_ms(pair: PairMeasurement, offsets: dict[str, float]) -> float:
    return (offsets[pair.clip_b] - offsets[pair.clip_a] - pair.lag_s) * 1000


def sync_event(
    event_name: str, data_dir: str | Path = "data", settings: SyncSettings | None = None
) -> Timeline:
    """Measure, solve, and write `timeline.json` for an ingested event."""
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
        )
        measured.append(
            PairMeasurement(
                clip_a=a.clip_id,
                clip_b=b.clip_id,
                lag_s=found.lag_s if found else None,
                confidence=found.confidence if found else None,
                overlap_s=found.overlap_s if found else None,
            )
        )

    # longest clips first, so a tie between equally matched groups keeps the longer footage
    order = sorted(usable, key=lambda clip: -clip.proxy.duration_s)
    offsets, pairs = solve_offsets([clip.clip_id for clip in order], measured, settings)

    placements = []
    for clip in manifest.clips:
        duration = clip.proxy.duration_s if clip.proxy else (clip.source.duration_s or 0.0)
        if clip.clip_id in offsets:
            best = [p.confidence for p in pairs if p.used and clip.clip_id in (p.clip_a, p.clip_b)]
            placements.append(
                ClipPlacement(
                    clip_id=clip.clip_id,
                    name=clip.source.name,
                    placed=True,
                    offset_s=round(offsets[clip.clip_id], 6),
                    duration_s=duration,
                    confidence=max(best) if best else None,
                )
            )
        else:
            placements.append(
                ClipPlacement(
                    clip_id=clip.clip_id,
                    name=clip.source.name,
                    placed=False,
                    duration_s=duration,
                    reason=reasons[clip.clip_id] or NOT_MATCHED,
                )
            )

    ends = [p.offset_s + p.duration_s for p in placements if p.placed]
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
