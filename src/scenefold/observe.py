"""Ask a model what each clip shows, a few seconds at a time.

The model never sees the whole event: one clip, one window of a few seconds, a handful of frames.
That keeps every clip an independent witness, which is what makes a disagreement between two
angles worth anything later.

It runs on this computer. `qwen3.5:4b` through Ollama needs about three seconds to look at a
window, costs nothing, and nobody's footage leaves the machine — which matters, because most
footage is of people who agreed to be filmed by their friend, not to be uploaded anywhere.

What a small model is good at, measured on real concert clips: saying what is in front of it
("a wide shot of a crowd holding up glowing lights", "a hand waving in the foreground"). What it
is bad at: counting ("5,000 people"), reading its own reliability (it answered 1.0 every time),
and names. So it is asked for what it can see, and how much to trust a window comes from how good
the picture was, not from the model's opinion of itself.
"""

import json
import time
import urllib.error
import urllib.request
from base64 import b64encode
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

from scenefold import media, quality
from scenefold.manifest import Clip, ManifestError, load_manifest, normalize_event_id, now
from scenefold.observations import (
    ClipObservations,
    Observation,
    SpeechSettings,
    WatchSettings,
    load_observations,
    save_observations,
)
from scenefold.speech import FasterWhisper, Transcriber, listen_to_clip

# Neutral on purpose: telling the model it is watching a concert would have it describe a concert.
ASK = (
    "These pictures are moments from one video, in order, a few seconds apart. "
    "Say what is happening across them.\n"
    "- summary: one or two plain sentences, including anything that changes between the pictures.\n"
    "- subjects: a few short phrases for what stands out, each a few words.\n"
    "- on_screen_text: any words you can actually read in the picture, otherwise empty.\n"
    "Describe only what you can see. Never guess anyone's name, and never count a crowd."
)
ANSWER = {  # what the model must answer with; Ollama holds it to this shape
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "subjects": {"type": "array", "items": {"type": "string"}},
        "on_screen_text": {"type": "string"},
    },
    "required": ["summary", "subjects"],
}
OLLAMA_HOST = "http://127.0.0.1:11434"
MAX_SUBJECTS = 8


class WatchError(Exception):
    """The clips cannot be watched: no event, or no model to watch them with."""


class Watcher(Protocol):
    """Anything that can look at a few pictures and say what they show."""

    name: str

    def describe(self, pictures: list[bytes]) -> dict: ...


@dataclass(frozen=True)
class Ollama:
    """A model running on this computer, through Ollama."""

    model: str
    host: str = OLLAMA_HOST
    timeout_s: float = 300.0

    @property
    def name(self) -> str:
        return self.model

    def describe(self, pictures: list[bytes]) -> dict:
        body = json.dumps({
            "model": self.model,
            "prompt": ASK,
            "images": [b64encode(picture).decode() for picture in pictures],
            "format": ANSWER,
            "stream": False,
            "think": False,  # this model can think out loud; it is slower and adds nothing here
            "options": {"temperature": 0.2},
        }).encode()  # fmt: skip
        request = urllib.request.Request(
            f"{self.host}/api/generate", body, {"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                answer = json.loads(response.read())
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise WatchError(
                f"could not reach the model at {self.host}: {exc}. Is Ollama running "
                f"(`ollama serve`) and is `{self.model}` pulled (`ollama pull {self.model}`)?"
            ) from exc
        if "error" in answer:
            raise WatchError(f"the model refused: {answer['error']}")
        return _read_answer(answer.get("response", ""))

    def ready(self) -> str | None:
        """None when the model is there, otherwise what to do about it."""
        try:
            with urllib.request.urlopen(f"{self.host}/api/tags", timeout=10) as response:
                have = {tag["name"] for tag in json.loads(response.read()).get("models", [])}
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            return (
                f"no model is running at {self.host}. Install Ollama (ollama.com), then "
                f"`ollama pull {self.model}`."
            )
        if self.model not in have and f"{self.model}:latest" not in have:
            return f"`{self.model}` is not pulled here; run `ollama pull {self.model}`."
        return None


def watch_clip(
    video: Path,
    duration_s: float,
    settings: WatchSettings,
    watcher: Watcher,
    scores: np.ndarray | None = None,
    progress=None,
) -> tuple[list[Observation], float]:
    """Look at one clip window by window. Returns what was seen and how long it took."""
    every = settings.window_s / settings.frames_per_window
    if 0 < duration_s < 2 * every:
        every = duration_s / 2  # a clip shorter than one step still gets looked at, twice
    pictures = media.jpeg_frames(video, settings.frame_width, 1 / every)
    if not pictures:
        return [], 0.0
    started = time.monotonic()
    seen: list[Observation] = []
    for first in range(0, len(pictures), settings.frames_per_window):
        window = pictures[first : first + settings.frames_per_window]
        t_start = first * every
        t_end = min(duration_s, t_start + len(window) * every)
        if progress:
            progress(f"{t_start:.0f}-{t_end:.0f} s")
        try:
            said = watcher.describe(window)
        except WatchError:
            raise
        except Exception:  # noqa: BLE001 - one bad window must not lose the whole clip
            continue
        summary = str(said.get("summary") or "").strip()
        if not summary:
            continue
        seen.append(
            Observation(
                t_start_s=round(t_start, 3),
                t_end_s=round(t_end, 3),
                summary=summary,
                subjects=[str(s).strip() for s in said.get("subjects") or []][:MAX_SUBJECTS],
                on_screen_text=str(said.get("on_screen_text") or "").strip(),
                picture_score=_score_over(scores, t_start, t_end),
            )
        )
    return seen, round(time.monotonic() - started, 2)


def observe_event(
    event_name: str,
    data_dir: str | Path = "data",
    settings: WatchSettings | None = None,
    *,
    watcher: Watcher | None = None,
    speech: SpeechSettings | None = None,
    transcriber: Transcriber | None = None,
    again: bool = False,
    progress=None,
) -> list[ClipObservations]:
    """Watch every clip of an event, listen to it, and write down what it shows and says.

    Clips already watched with the same model and the same question are left alone, so running
    this twice costs nothing the second time. `again` watches them anyway. Pass `speech=None` to
    watch only; the two halves are cached separately, so adding speech later does not re-watch.
    """
    settings = settings or WatchSettings()
    watcher = watcher or Ollama(settings.model)
    try:
        event_id = normalize_event_id(event_name)
    except ValueError as exc:
        raise WatchError(str(exc)) from exc
    event_dir = Path(data_dir) / event_id
    try:
        manifest = load_manifest(event_dir)
    except ManifestError as exc:
        raise WatchError(str(exc)) from exc
    if manifest is None:
        raise WatchError(f"no ingested event at {event_dir}; run `scenefold ingest` first")
    clips = [clip for clip in manifest.clips if clip.proxy is not None]
    if not clips:
        raise WatchError("this event has no working copies; run `scenefold ingest` again")
    if isinstance(watcher, Ollama) and (wrong := watcher.ready()):
        raise WatchError(wrong)

    todo = [clip for clip in clips if again or not _already(event_dir, clip, settings)]
    scores = _picture_scores(event_dir, todo, progress)
    waiting = {clip.clip_id for clip in todo}
    # One engine for the whole event: loading a Whisper model costs seconds, and trying the
    # graphics card before falling back to the processor costs more than that.
    ears = transcriber if speech is None else transcriber or FasterWhisper(speech.model)
    out = []
    for clip in clips:
        done = load_observations(event_dir, clip.clip_id)
        if clip.clip_id in waiting:
            if progress:
                progress(f"watching {clip.source.name}")
            seen, took = watch_clip(
                event_dir / clip.proxy.video,
                clip.proxy.duration_s,
                settings,
                watcher,
                scores.get(clip.clip_id),
            )
            done = ClipObservations(
                clip_id=clip.clip_id,
                name=clip.source.name,
                created_at=now(),
                settings=settings,
                duration_s=clip.proxy.duration_s,
                seconds_taken=took,
                observations=seen,
                # what was heard before still holds: the pictures were re-watched, not the sound
                speech=done.speech if done else [],
                speech_settings=done.speech_settings if done else None,
                speech_seconds_taken=done.speech_seconds_taken if done else None,
            )
            save_observations(event_dir, done)
        if speech is not None and done is not None:
            done = _listen(event_dir, clip, done, speech, ears, again, progress)
        out.append(done)
    return out


def _listen(
    event_dir: Path,
    clip: Clip,
    done: ClipObservations,
    settings: SpeechSettings,
    ears: Transcriber,
    again: bool,
    progress=None,
) -> ClipObservations:
    """Add what was said to what was seen, unless it was already heard the same way."""
    if clip.proxy.audio is None:
        return done
    heard_already = (
        done.speech_settings is not None and done.speech_settings.key() == settings.key()
    )
    if heard_already and not again:
        return done
    if progress:
        progress(f"listening to {clip.source.name}")
    heard, took = listen_to_clip(event_dir / clip.proxy.audio, settings, ears)
    done = done.model_copy(
        update={"speech": heard, "speech_settings": settings, "speech_seconds_taken": took}
    )
    save_observations(event_dir, done)
    return done


def _already(event_dir: Path, clip: Clip, settings: WatchSettings) -> bool:
    """True when this clip was watched with the same model and the same question."""
    done = load_observations(event_dir, clip.clip_id)
    return done is not None and done.settings.key() == settings.key()


def _picture_scores(event_dir: Path, clips: list[Clip], progress=None) -> dict[str, np.ndarray]:
    """How good each clip's picture is, second by second, to weigh what is said about it."""
    measured = []
    for clip in clips:
        if progress:
            progress(f"looking at {clip.source.name}")
        measured.append(quality.score_clip(clip.clip_id, event_dir / clip.proxy.video))
    return {clip_id: scored.combined for clip_id, scored in quality.combine(measured).items()}


def _score_over(scores: np.ndarray | None, t_start: float, t_end: float) -> float | None:
    if scores is None or not len(scores):
        return None
    piece = scores[int(t_start) : max(int(t_start) + 1, int(t_end))]
    return round(float(piece.mean()), 4) if len(piece) else None


def _read_answer(text: str) -> dict:
    """The model's answer as a dictionary, even when it wrapped it in words."""
    text = text.strip()
    try:
        answer = json.loads(text)
    except json.JSONDecodeError:
        first, last = text.find("{"), text.rfind("}")
        if first < 0 or last <= first:
            return {"summary": text}  # it answered in plain words; keep them rather than nothing
        try:
            answer = json.loads(text[first : last + 1])
        except json.JSONDecodeError:
            return {"summary": text}
    return answer if isinstance(answer, dict) else {"summary": text}
