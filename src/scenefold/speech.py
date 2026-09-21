"""Hear what is said in a clip, word by word, on this computer.

Whisper through faster-whisper: it runs on the laptop's GPU, or the processor if there isn't one,
and gives each word its own start and end. Those times are what later phases need — a sentence
timed to the second is no use for finding the moment somebody said something.

Two things about Whisper worth knowing, because both shape what is kept:

- It fills silence with whatever it expects to hear. Left alone on a concert recording it writes
  pages of invented lyrics, so speech is only kept where a voice was actually detected.
- It is asked one clip at a time, like the pictures are, so two angles of the same moment stay two
  independent accounts of what was said.

Installing it is optional: `uv sync --extra speech`. Without it, `scenefold observe` still watches
the pictures and says plainly that nothing listened.
"""

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

from scenefold.observations import SpeechSettings, Utterance, Word

MIN_WORDS = 1  # an utterance with nothing in it is not worth keeping


class SpeechError(Exception):
    """Nothing can listen: the engine is missing, or the sound cannot be read."""


class Transcriber(Protocol):
    """Anything that can turn a clip's sound into words with times."""

    name: str

    def hear(self, wav: Path, settings: SpeechSettings) -> list[Utterance]: ...


@dataclass
class FasterWhisper:
    """Whisper running here, through faster-whisper.

    It prefers the graphics card and falls back to the processor. A card needs NVIDIA's cuBLAS and
    cuDNN libraries beside it, which are a few hundred megabytes and not everyone has them; the
    processor is slower but always there, so a missing library is a slower run, not a failed one.
    """

    model_size: str = "small"
    device: str = "auto"  # "cuda", "cpu", or let it choose
    compute_type: str = "default"
    _model: object = None
    _using: str = ""

    @property
    def name(self) -> str:
        return f"faster-whisper {self.model_size}{f' on the {self._using}' if self._using else ''}"

    def load(self):
        """The model, loaded once and kept: loading costs seconds, hearing a clip costs less."""
        if self._model is not None:
            return self._model
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:  # pragma: no cover - depends on what is installed
            raise SpeechError(
                "faster-whisper is not installed, so nothing can listen. "
                "Install it with `uv sync --extra speech`, or run with --no-speech."
            ) from exc
        tries = (
            [("cuda", "float16"), ("cpu", "int8")]
            if self.device == "auto"
            else [(self.device, self.compute_type)]
        )
        problems = []
        for device, compute in tries:
            try:
                model = WhisperModel(self.model_size, device=device, compute_type=compute)
                # A card can load a model and only fail when asked to do arithmetic with it (a
                # missing cuBLAS), so it is tried on a moment of silence before being trusted.
                list(model.transcribe(np.zeros(16000, dtype=np.float32))[0])
            except Exception as exc:  # noqa: BLE001 - any refusal means try the next way
                problems.append(f"{device}: {exc}")
                continue
            self._model = model
            self._using = "graphics card" if device == "cuda" else "processor"
            return self._model
        raise SpeechError("nothing here can run Whisper — " + "; ".join(problems))

    def hear(self, wav: Path, settings: SpeechSettings) -> list[Utterance]:
        model = self.load()
        pieces, about = model.transcribe(
            str(wav),
            language=settings.language,
            word_timestamps=True,
            vad_filter=settings.voice_only,
            condition_on_previous_text=False,  # stops one invented line breeding the next
        )
        language = getattr(about, "language", None)
        return [said for piece in pieces if (said := _utterance(piece, language))]


def _utterance(piece, language: str | None) -> Utterance | None:
    """One of the engine's segments as an utterance, or None when it holds no words."""
    text = (getattr(piece, "text", "") or "").strip()
    words = [
        Word(
            word=(word.word or "").strip(),
            t_start_s=round(float(word.start), 3),
            t_end_s=round(float(word.end), 3),
            sureness=_rounded(getattr(word, "probability", None)),
        )
        for word in (getattr(piece, "words", None) or [])
        if (word.word or "").strip() and word.start is not None and word.end is not None
    ]
    if not text or len(words) < MIN_WORDS:
        return None
    return Utterance(
        t_start_s=round(float(piece.start), 3),
        t_end_s=round(float(piece.end), 3),
        text=text,
        words=words,
        language=language,
    )


def _rounded(value) -> float | None:
    return None if value is None else round(float(value), 3)


def listen_to_clip(
    wav: Path, settings: SpeechSettings, transcriber: Transcriber
) -> tuple[list[Utterance], float]:
    """What was said in one clip, and how long the listening took."""
    if not wav.is_file():
        raise SpeechError(f"{wav.name} is missing; run `scenefold ingest` again")
    started = time.monotonic()
    heard = transcriber.hear(wav, settings)
    return heard, round(time.monotonic() - started, 2)
