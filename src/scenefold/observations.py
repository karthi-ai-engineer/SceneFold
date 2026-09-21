"""What one clip shows, moment by moment: `data/<event_id>/observations/<clip_id>.json`.

Every clip is watched on its own, so two angles of the same moment are two independent witnesses.
That is the point: later phases compare them, and a disagreement only means something if neither
account was written with the other in view.

Times are `t_local`: seconds into that clip's working copy, never the shared clock. Putting the
observations on the shared clock is `timeline.json`'s job, so re-running sync never invalidates
what a clip was seen to show.

Nothing here is taken as true. An observation is what a model said about a few frames, kept with
the reason to doubt it: how good the picture was (quality.py), which model said it, and when.
"""

import json
import os
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

SCHEMA_VERSION = 1
OBSERVATIONS_DIR = "observations"
# Raise this when the wording of what we ask changes, so old answers are asked again instead of
# being mixed with new ones. 2: stopped telling the model it was watching a live event.
PROMPT_VERSION = 2


class ObservationError(Exception):
    """The observations for a clip exist but cannot be used."""


class WatchSettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    model: str = "qwen3.5:4b"  # whatever the provider calls it
    window_s: float = Field(10.0, gt=0)  # one observation covers this much of the clip
    frames_per_window: int = Field(4, ge=1)  # spread evenly across the window
    frame_width: int = Field(640, ge=64)  # frames are shrunk before the model sees them
    prompt_version: int = PROMPT_VERSION

    def key(self) -> str:
        """What the cached answers belong to: ask again when any of it changes."""
        return self.model_dump_json()


class Observation(BaseModel):
    """What was seen in one window of one clip."""

    t_start_s: float  # seconds into this clip
    t_end_s: float
    summary: str  # what happens, in a sentence or two
    subjects: list[str] = []  # short phrases: what stands out
    on_screen_text: str = ""  # words readable in the picture, empty when there are none
    # How much the picture was worth looking at over this window, 0 to 1 (quality.py). A blurred,
    # shaken or blown-out window gives a weak account, whatever the model says about it.
    picture_score: float | None = None


class ClipObservations(BaseModel):
    schema_version: int = SCHEMA_VERSION
    clip_id: str
    name: str  # the original file name, for people reading the file
    created_at: datetime
    settings: WatchSettings
    duration_s: float
    seconds_taken: float  # how long the model took, so the cost of a re-run is known
    observations: list[Observation] = []


def observations_path(event_dir: Path, clip_id: str) -> Path:
    return event_dir / OBSERVATIONS_DIR / f"{clip_id}.json"


def save_observations(event_dir: Path, seen: ClipObservations) -> Path:
    """Write atomically, so a crash never leaves a half-written file."""
    path = observations_path(event_dir, seen.clip_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(seen.model_dump_json(indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def load_observations(event_dir: Path, clip_id: str) -> ClipObservations | None:
    path = observations_path(event_dir, clip_id)
    if not path.exists():
        return None
    try:
        return ClipObservations.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, ValidationError) as exc:
        raise ObservationError(f"{path} is unreadable: {exc}") from exc
