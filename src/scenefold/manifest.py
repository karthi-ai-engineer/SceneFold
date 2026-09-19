"""The event workspace contract: what `data/<event_id>/manifest.json` holds.

Every later stage (sync, viewer, understanding, ...) finds its clips here. Paths inside the
manifest are relative to the event folder and use forward slashes, so a workspace can move
between machines.
"""

import hashlib
import json
import os
import re
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

SCHEMA_VERSION = 1
MANIFEST_NAME = "manifest.json"
ORIGINALS_DIR = "originals"
PROXIES_DIR = "proxies"

_EVENT_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
_WINDOWS_RESERVED = {"con", "prn", "aux", "nul"} | {
    f"{name}{i}" for name in ("com", "lpt") for i in range(10)
}


class ManifestError(Exception):
    """The manifest file exists but cannot be used."""


def now() -> datetime:
    return datetime.now(UTC)


def normalize_event_id(name: str) -> str:
    """Turn a user-given event name into a safe folder name, or raise ValueError."""
    event_id = name.strip().lower()
    if not _EVENT_ID.fullmatch(event_id) or event_id in _WINDOWS_RESERVED:
        raise ValueError(
            f"invalid event name {name!r}: use 1-64 letters, digits, '-' or '_', "
            "starting with a letter or digit (for example: match-01)"
        )
    return event_id


class ProxySettings(BaseModel):
    """How working copies are made. Changing any value rebuilds proxies on the next ingest."""

    model_config = ConfigDict(frozen=True)

    # shorter picture side of the working copy; smaller videos are never upscaled
    max_short_side: int = Field(720, ge=144)
    fps: int = Field(30, ge=1, le=120)
    keyframe_interval_s: float = Field(1.0, gt=0)
    crf: int = Field(23, ge=0, le=51)
    preset: str = Field("veryfast", pattern=r"^[a-z]+$")  # x264 speed preset
    audio_sample_rate: int = 48000
    audio_bitrate_kbps: int = Field(128, ge=32)
    # samples per second the sound may be stretched or squeezed to follow its timestamps, as the
    # picture does; 1 only pads the start (phones' audio clocks can disagree with their timestamps)
    audio_max_stretch: int = Field(1000, ge=1)

    def key(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()[:12]


class ClipStatus(StrEnum):
    OK = "ok"
    WARNING = "warning"  # usable, but something limits what later stages can do with it
    FAILED = "failed"  # a video we could not process; kept so the failure is visible


class Issue(BaseModel):
    code: str  # stable and machine-readable, e.g. "no_audio"
    message: str  # plain-language explanation


class VideoStream(BaseModel):
    index: int
    codec: str
    width: int
    height: int
    display_width: int  # after rotation and non-square pixels
    display_height: int
    rotation: int = 0  # clockwise degrees needed to show the picture upright
    fps: float | None = None
    variable_frame_rate: bool = False  # best guess from stream headers
    pixel_format: str | None = None
    color_transfer: str | None = None
    hdr: bool = False
    interlaced: bool = False
    start_time_s: float | None = None


class AudioStream(BaseModel):
    index: int
    codec: str
    sample_rate: int | None = None
    channels: int | None = None
    start_time_s: float | None = None


class Source(BaseModel):
    """The file as it was handed to ingest."""

    name: str
    path: str
    size_bytes: int
    modified_ns: int
    sha256: str
    container: str | None = None
    duration_s: float | None = None
    recorded_at: datetime | None = None  # from file metadata; phone clocks can be wrong
    video: VideoStream | None = None
    audio: AudioStream | None = None


class Proxy(BaseModel):
    """The working copy every later stage uses.

    Clip time 0 is the same instant in `video` and `audio`: a frame shown at t seconds and
    WAV sample round(t * 48000) were recorded together.
    """

    video: str  # e.g. "proxies/<clip_id>.mp4"
    audio: str | None = None  # mono WAV; None when the clip has no usable audio
    width: int
    height: int
    fps: int
    duration_s: float
    settings_key: str
    audio_mean_db: float | None = None
    audio_peak_db: float | None = None


class Clip(BaseModel):
    clip_id: str  # first 12 hex characters of the original's SHA-256
    status: ClipStatus
    issues: list[Issue] = []
    ingested_at: datetime
    original: str | None = None  # e.g. "originals/<clip_id>.mov"; never modified
    source: Source
    proxy: Proxy | None = None


class Manifest(BaseModel):
    schema_version: int = SCHEMA_VERSION
    event_id: str
    created_at: datetime
    updated_at: datetime
    proxy_settings: ProxySettings
    clips: list[Clip] = []

    def find(self, clip_id: str) -> Clip | None:
        return next((clip for clip in self.clips if clip.clip_id == clip_id), None)

    def upsert(self, clip: Clip) -> None:
        for i, existing in enumerate(self.clips):
            if existing.clip_id == clip.clip_id:
                self.clips[i] = clip
                return
        self.clips.append(clip)


def load_manifest(event_dir: Path) -> Manifest | None:
    """Read the event's manifest; None if it does not exist yet."""
    path = event_dir / MANIFEST_NAME
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestError(f"{path} is unreadable: {exc}") from exc
    version = data.get("schema_version") if isinstance(data, dict) else None
    if version != SCHEMA_VERSION:
        raise ManifestError(
            f"{path} has schema_version {version!r}; this Scenefold reads version {SCHEMA_VERSION}"
        )
    try:
        return Manifest.model_validate(data)
    except ValidationError as exc:
        raise ManifestError(f"{path} does not match the manifest format:\n{exc}") from exc


def save_manifest(event_dir: Path, manifest: Manifest) -> Path:
    """Write the manifest atomically, so a crash never leaves a half-written file."""
    path = event_dir / MANIFEST_NAME
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(manifest.model_dump_json(indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path
