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
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ValidationError

SCHEMA_VERSION = 1
POSITIONS_NAME = "positions.json"
SOUND_M_PER_S = 343.0


class PositionsError(Exception):
    """The positions file exists but cannot be used."""


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
