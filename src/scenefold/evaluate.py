"""Measure sync error against ground truth: moments that several clips caught.

A moment is something every phone can place in its own clip time: a clap, a flash, a whistle.
The ground truth file lists each moment's clip time in every clip that caught it:

    {"moments": [
        {"label": "first clap", "times": {"IMG_4821.MOV": 2.345, "PXL_0917.mp4": 0.512}},
        {"label": "last clap", "times": {"IMG_4821.MOV": 58.901, "PXL_0917.mp4": 57.080}}
    ]}

Clips are named by file name or clip ID; times are seconds of clip time, as a video player shows
them. If the timeline is right, every clip puts a moment at the same master time. A pair's error is
how far apart its two clips put the moment, so the result doesn't depend on where master time 0 is.
"""

import json
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path

import numpy as np
from pydantic import BaseModel, Field, ValidationError

from scenefold.manifest import normalize_event_id
from scenefold.timeline import ClipPlacement, Timeline, TimelineError, load_timeline

FRAME_S = 1 / 30  # working copies play at 30 fps


class EvaluationError(Exception):
    """The evaluation cannot run: no timeline, or unusable ground truth."""


class Moment(BaseModel):
    label: str | None = None
    times: dict[str, float]  # clip file name or clip ID -> clip time in seconds


class GroundTruth(BaseModel):
    moments: list[Moment] = Field(min_length=1)


@dataclass(frozen=True)
class PairError:
    moment: str
    clip_a: str
    clip_b: str
    error_ms: float  # master time of the moment from clip B minus from clip A


@dataclass(frozen=True)
class Evaluation:
    event_id: str
    errors: list[PairError]
    not_placed: list[str]  # clips the ground truth names but the timeline could not place

    def _abs_ms(self) -> np.ndarray:
        return np.abs([e.error_ms for e in self.errors])

    @property
    def median_ms(self) -> float | None:
        return float(np.median(self._abs_ms())) if self.errors else None

    @property
    def p95_ms(self) -> float | None:
        return float(np.percentile(self._abs_ms(), 95)) if self.errors else None

    @property
    def worst(self) -> PairError | None:
        return max(self.errors, key=lambda e: abs(e.error_ms), default=None)

    @property
    def within_frame(self) -> int:
        return int(np.sum(self._abs_ms() <= FRAME_S * 1000)) if self.errors else 0


def evaluate(timeline: Timeline, truth: GroundTruth) -> Evaluation:
    """Compare every pair of placed clips at every moment both of them caught."""
    errors: list[PairError] = []
    not_placed: list[str] = []
    for index, moment in enumerate(truth.moments, start=1):
        label = moment.label or f"moment {index}"
        seen = []
        for key, t_local in moment.times.items():
            clip = _find(timeline, key)
            if not clip.placed:
                if clip.name not in not_placed:
                    not_placed.append(clip.name)
                continue
            seen.append((clip.name, master_time(clip, t_local)))
        for (name_a, at_a), (name_b, at_b) in combinations(seen, 2):
            errors.append(PairError(label, name_a, name_b, (at_b - at_a) * 1000))
    return Evaluation(timeline.event_id, errors, not_placed)


def master_time(clip: ClipPlacement, t_local: float) -> float:
    return clip.offset_s + t_local / (1 + (clip.drift_ppm or 0.0) * 1e-6)


def moments_from_offsets(
    offsets: dict[str, float],
    durations: dict[str, float],
    drift_ppm: dict[str, float] | None = None,
    every_s: float = 10.0,
) -> GroundTruth:
    """Ground truth from known placements (synthetic clips, a dataset's sync annotations).

    Puts a moment every `every_s` master seconds, caught by each clip recording at that time.
    Offsets and drift use the timeline's meaning; the master clock can be any clip's or none.
    """
    drift_ppm = drift_ppm or {}
    ends = [offsets[name] + durations[name] for name in offsets]
    moments = []
    for t_master in np.arange(min(offsets.values()), max(ends), every_s):
        times = {}
        for name, offset in offsets.items():
            t_local = (t_master - offset) * (1 + drift_ppm.get(name, 0.0) * 1e-6)
            if 0 <= t_local <= durations[name]:
                times[name] = round(float(t_local), 6)
        if len(times) >= 2:
            moments.append(Moment(label=f"t={t_master:.1f} s", times=times))
    if not moments:
        raise EvaluationError("no two clips overlap, so there is nothing to compare")
    return GroundTruth(moments=moments)


def load_ground_truth(path: Path) -> GroundTruth:
    try:
        return GroundTruth.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))
    except OSError as exc:
        raise EvaluationError(f"cannot read ground truth {path}: {exc}") from exc
    except (ValueError, ValidationError) as exc:
        raise EvaluationError(f"{path} is not a ground truth file: {exc}") from exc


def evaluate_event(event_name: str, truth_path: Path, data_dir: str | Path = "data") -> Evaluation:
    try:
        event_dir = Path(data_dir) / normalize_event_id(event_name)
        timeline = load_timeline(event_dir)
    except (ValueError, TimelineError) as exc:
        raise EvaluationError(str(exc)) from exc
    if timeline is None:
        raise EvaluationError(f"no timeline at {event_dir}; run `scenefold sync` first")
    return evaluate(timeline, load_ground_truth(truth_path))


def _find(timeline: Timeline, key: str) -> ClipPlacement:
    by_id = [clip for clip in timeline.clips if clip.clip_id == key]
    if by_id:
        return by_id[0]
    by_name = [clip for clip in timeline.clips if clip.name == key]
    if len(by_name) == 1:
        return by_name[0]
    if not by_name:
        raise EvaluationError(f"ground truth names {key!r}, which is not a clip of this event")
    ids = ", ".join(clip.clip_id for clip in by_name)
    raise EvaluationError(f"{len(by_name)} clips are named {key!r}; name them by clip ID: {ids}")
