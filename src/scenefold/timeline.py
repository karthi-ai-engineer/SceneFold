"""The sync contract: what `data/<event_id>/timeline.json` holds.

Every clip gets an `offset_s`: where its clip time 0 sits on the master timeline (the shared clock).
A frame at clip time t_local plays at t_master = offset_s + t_local. Master time 0 is the start of
the earliest placed clip.
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


class PairMeasurement(BaseModel):
    """How far apart two clips are, measured from their sound."""

    clip_a: str
    clip_b: str
    lag_s: float | None  # clip B's time 0 is lag_s seconds after clip A's (None: not measurable)
    confidence: float | None
    overlap_s: float | None  # how long both clips were recording at that lag
    used: bool = False  # True when the solver used this pair to place clips
    rejected: str | None = None  # why it was not used: short_overlap, low_confidence, inconsistent
    residual_ms: float | None = None  # disagreement with the solved offsets


class ClipPlacement(BaseModel):
    clip_id: str
    name: str  # original file name, for people reading the file
    placed: bool
    offset_s: float | None = None  # t_master of this clip's time 0
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
