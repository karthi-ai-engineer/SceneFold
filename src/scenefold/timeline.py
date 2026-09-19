"""The sync contract: what `data/<event_id>/timeline.json` holds.

Every placed clip gets an `offset_s`: where its clip time 0 sits on the master timeline (the shared
clock). Master time 0 is the start of the earliest placed clip. Phone clocks run slightly fast or
slow, so each clip also gets a `drift_ppm`: how much faster its clock ran than the master clock, in
parts per million (the master clock is the average of the clips whose drift could be measured;
None counts as 0). Drift is measured from the sound; a phone's picture usually runs within a few
ppm of its sound, but not on every device. The two times convert as:

    t_local  = (t_master - offset_s) * (1 + drift_ppm / 1e6)
    t_master = offset_s + t_local / (1 + drift_ppm / 1e6)

A player can use `1 + drift_ppm / 1e6` directly as the clip's playback rate.
"""

import json
import os
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

SCHEMA_VERSION = 1
TIMELINE_NAME = "timeline.json"


class TimelineError(Exception):
    """The timeline file exists but cannot be used."""


class SyncSettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    analysis_rate: int = Field(8000, ge=2000, le=48000)  # audio is compared at this sample rate
    phat_beta: float = Field(0.8, ge=0.0, le=1.0)  # whitening strength; 1.0 is classic GCC-PHAT
    min_overlap_s: float = Field(5.0, gt=0)  # shorter overlaps are too unreliable to use
    min_confidence: float = Field(2.0, gt=0)  # pairs below this are not used
    max_residual_ms: float = Field(20.0, gt=0)  # pairs disagreeing more than this are dropped
    window_s: float = Field(10.0, gt=0)  # drift is measured from windows of this length
    max_drift_ppm: float = Field(1000.0, ge=0)  # clock drift beyond this is not searched for
    # The same song played on two nights can match on its backing track alone, as confidently as a
    # true match (14 Coldplay clips from two nights: agreement 0.50-0.87 across nights, nearly
    # always 1.0 within a night and on Jiku). A pair whose windows agree less than this is set aside
    min_agreement: float = Field(0.9, ge=0, le=1)
    # ...when its overlap holds at least this many windows. Short clips count too: a 32 s clip whose
    # windows agreed 1 of 3 and 0 of 3 linked the two Coldplay nights when left unjudged.
    agreement_windows: int = Field(2, ge=1)


class PairMeasurement(BaseModel):
    """How far apart two clips are, measured from their sound."""

    clip_a: str
    clip_b: str
    lag_s: float | None  # on A's clock, B's time 0 is this much after A's (None: not measurable)
    confidence: float | None
    overlap_s: float | None  # how long both clips were recording at that lag
    drift_ppm: float | None = None  # how much faster B's clock ran than A's (None: too short)
    windows: int | None = None  # windows the overlap was cut into to check the match holds
    agreement: float | None = None  # share of those windows whose sound matches at this lag
    used: bool = False  # True when the solver used this pair to place clips
    # short_overlap, low_confidence, partial_match, inconsistent, or separate_group
    rejected: str | None = None
    residual_ms: float | None = None  # disagreement with the solved offsets


class ClipPlacement(BaseModel):
    clip_id: str
    name: str  # original file name, for people reading the file
    placed: bool
    offset_s: float | None = None  # t_master of this clip's time 0
    drift_ppm: float | None = None  # how much faster this clip's clock ran than the master clock
    duration_s: float
    confidence: float | None = None  # best confidence among the pairs that placed it
    reason: str | None = None  # why it could not be placed


class Timeline(BaseModel):
    schema_version: int = SCHEMA_VERSION
    event_id: str
    created_at: datetime
    settings: SyncSettings
    duration_s: float  # length of the master timeline covered by placed clips
    clips: list[ClipPlacement]
    pairs: list[PairMeasurement]


def load_timeline(event_dir: Path) -> Timeline | None:
    path = event_dir / TIMELINE_NAME
    if not path.exists():
        return None
    try:
        return Timeline.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, ValidationError) as exc:
        raise TimelineError(f"{path} is unreadable: {exc}") from exc


def save_timeline(event_dir: Path, timeline: Timeline) -> Path:
    """Write atomically, so a crash never leaves a half-written file."""
    path = event_dir / TIMELINE_NAME
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(timeline.model_dump_json(indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path
