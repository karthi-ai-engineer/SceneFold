"""Ask each clip who is visible in it, in terms another angle could recognise.

This is the half of cross-angle identity that looks at pictures. It asks the same local model one
narrow question — who can you see, and what are they wearing — once every few seconds, and writes
the answers down beside everything else known about the clip. `identity.py` is what then decides
that the person in a red top over here is the person in a red top over there.

**What is written down, and what is not.** Clothing, what somebody is doing, roughly where they
are in the frame. Not faces, not names, not anything measured off a body. The model is told to
refuse names even when it can read one on a shirt, and the descriptions stay on this computer with
the footage. The point is to join two angles of the same afternoon, not to identify a stranger.

Measured on real footage before any of this was built: on stage clips, where people are large and
lit, the model gives genuinely separating descriptions — "black sleeveless top and dark pants",
"red sleeveless top" — and two phones filming the same second independently produced the same two.
On a wide crowd shot it manages "dark clothing", which separates nobody. So this is expected to
work where people are big in the frame and to quietly find nothing where they are not, which is
the honest outcome rather than a failure.
"""

import time
from pathlib import Path
from typing import Protocol

from scenefold import media
from scenefold.manifest import Clip, ManifestError, load_manifest, normalize_event_id
from scenefold.observations import (
    ClipObservations,
    PeopleSettings,
    Sighting,
    load_observations,
    save_observations,
)
from scenefold.observe import Ollama, WatchError

ASK = (
    "Look at the people in this picture. For each person you can see clearly, describe them so "
    "that somebody watching a different camera angle of the same moment could tell which person "
    "you mean.\n"
    "- wearing: colour first, then the garment — 'red sleeveless top, black trousers'.\n"
    "- doing: what they are doing, a few words.\n"
    "- where: roughly where they are in the frame — 'left of centre', 'front row'.\n"
    "Only people you can actually make out. Skip a distant crowd you could not describe one by "
    "one; it is right to answer with nobody. Never guess or report anyone's name, even if it is "
    "written on the picture."
)
ANSWER = {
    "type": "object",
    "properties": {
        "people": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "wearing": {"type": "string"},
                    "doing": {"type": "string"},
                    "where": {"type": "string"},
                },
                "required": ["wearing"],
            },
        }
    },
    "required": ["people"],
}
# Descriptions that describe nobody. The model reaches for these on crowds and dark frames, and a
# tracklet built on one of them would match every other one of them.
VAGUE = {
    "",
    "a person",
    "person",
    "people",
    "a crowd",
    "crowd",
    "unknown",
    "not visible",
    "unclear",
    "dark clothing",
    "dark clothes",
    "casual clothing",
    "casual clothes",
    "various clothing",
}
LONGEST = 120  # a description longer than this is a paragraph about the scene, not about a person


class Looker(Protocol):
    """Anything that can look at one picture and say who is in it."""

    name: str

    def ask(self, pictures: list[bytes], prompt: str, shape: dict, warmth: float = 0.2) -> dict: ...


def see_people(
    video: Path,
    duration_s: float,
    settings: PeopleSettings,
    looker: Looker,
    progress=None,
) -> tuple[list[Sighting], float]:
    """Go through one clip a window at a time, writing down who is visible. Also, how long it took.

    One frame per window, not several: a person's clothes do not change between frames, and the
    second frame costs as much as the first while telling the model the same thing twice.
    """
    pictures = media.jpeg_frames(video, settings.frame_width, 1 / settings.window_s)
    if not pictures:
        return [], 0.0
    started = time.monotonic()
    found: list[Sighting] = []
    for index, picture in enumerate(pictures):
        t_start = index * settings.window_s
        t_end = min(duration_s, t_start + settings.window_s) if duration_s else t_start
        if progress:
            progress(f"{t_start:.0f}-{t_end:.0f} s")
        try:
            said = looker.ask([picture], ASK, ANSWER)
        except WatchError:
            raise
        except Exception:  # noqa: BLE001 - one bad window must not lose the whole clip
            continue
        for person in (said.get("people") or [])[: settings.most]:
            if not isinstance(person, dict):
                continue
            wearing = _tidy(person.get("wearing"))
            if not _worth_keeping(wearing):
                continue
            found.append(
                Sighting(
                    t_start_s=round(t_start, 3),
                    t_end_s=round(t_end, 3),
                    wearing=wearing,
                    doing=_tidy(person.get("doing")),
                    where=_tidy(person.get("where")),
                )
            )
    return found, round(time.monotonic() - started, 2)


def watch_people(
    event_name: str,
    data_dir: str | Path = "data",
    settings: PeopleSettings | None = None,
    *,
    looker: Looker | None = None,
    again: bool = False,
    progress=None,
) -> list[ClipObservations]:
    """Ask every clip of an event who was in it, and keep the answers beside the rest.

    A clip already asked the same question by the same model is left alone, so this is cheap to
    run twice. It needs `scenefold observe` to have run first: the answers live in that file.
    """
    settings = settings or PeopleSettings()
    looker = looker or Ollama(settings.model)
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
    if isinstance(looker, Ollama) and (wrong := looker.ready()):
        raise WatchError(wrong)

    out: list[ClipObservations] = []
    for clip in clips:
        done = load_observations(event_dir, clip.clip_id)
        if done is None:
            raise WatchError(
                f"{clip.source.name} has not been watched yet; run "
                f"`scenefold observe {event_id}` first"
            )
        if not again and _already(done, settings):
            out.append(done)
            continue
        if progress:
            progress(f"looking for people in {clip.source.name}")
        found, took = see_people(
            event_dir / clip.proxy.video, clip.proxy.duration_s, settings, looker
        )
        done = done.model_copy(
            update={"people": found, "people_settings": settings, "people_seconds_taken": took}
        )
        save_observations(event_dir, done)
        out.append(done)
    return out


def _already(done: ClipObservations, settings: PeopleSettings) -> bool:
    """True when this clip was asked the same question by the same model."""
    return done.people_settings is not None and done.people_settings.key() == settings.key()


def _tidy(text) -> str:
    return " ".join(str(text or "").split())[:LONGEST]


def _worth_keeping(wearing: str) -> bool:
    """A description nobody could be picked out by is worse than no description."""
    plain = wearing.lower().strip(" .,")
    for prefix in ("a ", "an ", "the "):
        plain = plain.removeprefix(prefix)
    return bool(plain) and plain not in VAGUE and len(plain) > 3


def people_of(clips: list[Clip], event_dir: Path) -> dict[str, list[Sighting]]:
    """Who each clip saw, for the clips that have been asked."""
    seen = {}
    for clip in clips:
        done = load_observations(event_dir, clip.clip_id)
        if done is not None and done.people:
            seen[clip.clip_id] = done.people
    return seen
