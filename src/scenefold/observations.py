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


class Moment(BaseModel):
    """Something that can be timed exactly, found by arithmetic rather than by a model.

    A clap, a drum hit, a flash of light: moments.py finds them to a few hundredths of a second,
    which is far finer than any model's idea of when something happened.
    """

    t_s: float  # seconds into this clip
    kind: str  # "sound" (it suddenly grew louder) or "picture" (it suddenly changed)
    strength: float  # 0 to 1, against the rest of this clip


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
    # The moment inside this window that stands out most, when there is one. The window says what
    # was looked at; this says when, to a hundredth of a second instead of the model's ten.
    at_s: float | None = None
    at_kind: str | None = None


class SpeechSettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    model: str = "small"  # a Whisper size: tiny, base, small, medium, large-v3
    language: str | None = None  # None lets the model decide, per clip
    # Whisper fills silence with whatever it expects to hear, so speech is only kept where a voice
    # was actually detected. At a concert most of a clip is music, and this is what stops it
    # becoming pages of invented lyrics.
    voice_only: bool = True

    def key(self) -> str:
        return self.model_dump_json()


class Word(BaseModel):
    word: str
    t_start_s: float
    t_end_s: float
    sureness: float | None = None  # what the model made of its own hearing, 0 to 1


class Utterance(BaseModel):
    """A stretch of speech heard in one clip, with each word's own time."""

    t_start_s: float
    t_end_s: float
    text: str
    words: list[Word] = []
    language: str | None = None
    at_s: float | None = None  # the sound that starts it, timed exactly (moments.py)


class PeopleSettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    model: str = "qwen3.5:4b"
    window_s: float = Field(10.0, gt=0)  # one look every this many seconds
    most: int = Field(5, ge=1)  # people described from one frame; beyond that it invents them
    frame_width: int = Field(768, ge=64)  # wider than the scene questions: clothing is small
    prompt_version: int = 1

    def key(self) -> str:
        return self.model_dump_json()


class Sighting(BaseModel):
    """Somebody visible in one clip at one moment, described so another angle can be matched.

    Clothing and position, never faces and never names: enough to say "that is the same person
    the other phone filmed", and no more. It stays on this computer like everything else.
    """

    t_start_s: float  # seconds into this clip
    t_end_s: float
    wearing: str  # "a red sleeveless top", which is what makes a person findable across angles
    doing: str = ""
    where: str = ""  # roughly where in the frame, in the clip's own words
    entity_id: str | None = None  # filled in by identity.py when angles are matched up


class ClipObservations(BaseModel):
    schema_version: int = SCHEMA_VERSION
    clip_id: str
    name: str  # the original file name, for people reading the file
    created_at: datetime
    settings: WatchSettings
    duration_s: float
    seconds_taken: float  # how long the model took, so the cost of a re-run is known
    observations: list[Observation] = []
    # Everything in this clip that can be timed exactly, whether or not a model mentioned it.
    moments: list[Moment] = []
    # What was said, if anyone listened. Kept beside what was seen, cached on its own, because the
    # two are asked of different models at different times.
    speech: list[Utterance] = []
    speech_settings: SpeechSettings | None = None
    speech_seconds_taken: float | None = None
    # Who was visible, for matching the same person across angles. Cached on its own again: it is
    # a different question, asked of the same model at a different time.
    people: list[Sighting] = []
    people_settings: PeopleSettings | None = None
    people_seconds_taken: float | None = None


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
