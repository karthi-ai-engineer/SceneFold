"""Command line: `scenefold ingest <event> <videos or folders>`, `scenefold sync <event>`,
`scenefold evaluate <event> <ground truth>`, and `scenefold view <event>`."""

import argparse
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from scenefold.cut import CUT_NAME, FILM_NAME, CutError, Film, cut_event
from scenefold.evaluate import FRAME_S, EvaluationError, evaluate_event
from scenefold.fuse import FuseError, fuse_event
from scenefold.ingest import IngestError, InputResult, Outcome, ingest
from scenefold.judge import JudgeError, OllamaJudge
from scenefold.knowledge import KNOWLEDGE_NAME, counts, load_store
from scenefold.observations import OBSERVATIONS_DIR, SpeechSettings, WatchSettings
from scenefold.observe import WatchError, observe_event
from scenefold.picture_offset import metres
from scenefold.speech import SpeechError
from scenefold.story import STORY_NAME, StoryError, tell_event
from scenefold.sync import SyncError, sync_event
from scenefold.timeline import TIMELINE_NAME, ClipPlacement, Timeline
from scenefold.view import DEFAULT_PORT, ViewError, serve

SUMMARY_ORDER = [
    Outcome.ADDED,
    Outcome.UPDATED,
    Outcome.UNCHANGED,
    Outcome.DUPLICATE,
    Outcome.SKIPPED,
    Outcome.FAILED,
    Outcome.MISSING,
]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="scenefold",
        description="Scenefold: many phone videos of one event, folded into one story.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    ingest_parser = commands.add_parser(
        "ingest",
        help="add videos to an event",
        description="Add videos to an event. Originals are copied and never changed; working "
        "copies are written to <data-dir>/<event>/proxies. Running it again skips finished clips.",
    )
    ingest_parser.add_argument("event", help="event name, e.g. match-01")
    ingest_parser.add_argument(
        "paths", nargs="+", type=Path, help="video files or folders (searched recursively)"
    )
    ingest_parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data"),
        help="folder that holds event workspaces (default: ./data)",
    )
    sync_parser = commands.add_parser(
        "sync",
        help="put an event's clips on one clock",
        description="Place every ingested clip on one master timeline by comparing their sound. "
        "Writes <data-dir>/<event>/timeline.json. Clips that can't be matched are reported, "
        "never forced.",
    )
    sync_parser.add_argument("event", help="event name used with ingest, e.g. match-01")
    sync_parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data"),
        help="folder that holds event workspaces (default: ./data)",
    )
    sync_parser.add_argument(
        "--sound-only",
        action="store_true",
        help="skip matching the pictures, which measures how far each phone stood from the sound; "
        "faster, because the pictures have to be read",
    )
    evaluate_parser = commands.add_parser(
        "evaluate",
        help="measure sync error against ground truth",
        description="Compare an event's timeline.json with ground truth: moments such as claps "
        "and their clip time in every clip that caught them. Reports how many milliseconds apart "
        "each pair of clips puts the same moment.",
    )
    evaluate_parser.add_argument("event", help="event name used with sync, e.g. match-01")
    evaluate_parser.add_argument("truth", type=Path, help="ground truth JSON file")
    evaluate_parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data"),
        help="folder that holds event workspaces (default: ./data)",
    )
    observe_parser = commands.add_parser(
        "observe",
        help="ask a model what each clip shows",
        description="Watch every clip a few seconds at a time and write down what it shows, into "
        "<data-dir>/<event>/observations/. Each clip is watched on its own, so two angles stay two "
        "independent witnesses. The model runs on this computer: no footage is uploaded anywhere.",
    )
    observe_parser.add_argument("event", help="event name used with ingest, e.g. match-01")
    observe_parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data"),
        help="folder that holds event workspaces (default: ./data)",
    )
    observe_parser.add_argument(
        "--model", default=WatchSettings().model, help="the model to ask, as Ollama names it"
    )
    observe_parser.add_argument(
        "--window",
        type=float,
        default=WatchSettings().window_s,
        help="seconds each observation covers (default: %(default)s)",
    )
    observe_parser.add_argument(
        "--again",
        action="store_true",
        help="watch clips again that were already watched with the same model and question",
    )
    observe_parser.add_argument(
        "--no-speech", action="store_true", help="only watch the pictures; don't listen"
    )
    observe_parser.add_argument(
        "--speech-model",
        default=SpeechSettings().model,
        help="Whisper size to listen with: tiny, base, small, medium, large-v3 "
        "(default: %(default)s)",
    )
    fuse_parser = commands.add_parser(
        "fuse",
        help="merge what every clip saw into one account of the event",
        description="Put every clip's moments and descriptions on the shared clock, merge the "
        "ones that landed together into events, and note where the clips disagree. Writes "
        "<data-dir>/<event>/knowledge.sqlite.",
    )
    fuse_parser.add_argument("event", help="event name used with observe, e.g. match-01")
    fuse_parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data"),
        help="folder that holds event workspaces (default: ./data)",
    )
    fuse_parser.add_argument(
        "--no-reading",
        action="store_true",
        help="don't have the clips' accounts read against each other; faster, but the only "
        "disagreements found are the ones arithmetic can see",
    )
    fuse_parser.add_argument(
        "--model", default=WatchSettings().model, help="the model that reads the accounts"
    )
    story_parser = commands.add_parser(
        "story",
        help="tell what happened, with every sentence citing the footage",
        description="Write a short account of the event from what the clips agreed on, checking "
        "in code that every sentence points at a moment real footage backs up. Writes "
        "<data-dir>/<event>/story.json.",
    )
    story_parser.add_argument("event", help="event name used with fuse, e.g. match-01")
    story_parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data"),
        help="folder that holds event workspaces (default: ./data)",
    )
    story_parser.add_argument(
        "--model", default=WatchSettings().model, help="the model that writes it"
    )
    cut_parser = commands.add_parser(
        "cut",
        help="edit the angles into one film",
        description="Choose the best angle for each moment and cut the clips into one film. "
        "Writes <data-dir>/<event>/cut.json, which says why each shot was chosen, and cut.mp4.",
    )
    cut_parser.add_argument("event", help="event name used with sync, e.g. match-01")
    cut_parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data"),
        help="folder that holds event workspaces (default: ./data)",
    )
    cut_parser.add_argument(
        "--plan-only", action="store_true", help="choose the shots but don't render the film"
    )
    view_parser = commands.add_parser(
        "view",
        help="watch an event's clips together in the browser",
        description="Play every synced clip of an event side by side in the browser, on one "
        "clock. Starts a small web server that only this computer can reach (127.0.0.1) and "
        "opens the viewer. Runs until Ctrl+C.",
    )
    view_parser.add_argument("event", help="event name used with sync, e.g. match-01")
    view_parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data"),
        help="folder that holds event workspaces (default: ./data)",
    )
    view_parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"port to serve on; if it is taken, the next free one is used (default: "
        f"{DEFAULT_PORT})",
    )
    view_parser.add_argument(
        "--no-browser",
        action="store_true",
        help="only print the viewer's address; don't open a browser",
    )
    args = parser.parse_args(argv)
    _safe_console()
    run = {
        "ingest": _run_ingest,
        "sync": _run_sync,
        "evaluate": _run_evaluate,
        "observe": _run_observe,
        "fuse": _run_fuse,
        "story": _run_story,
        "cut": _run_cut,
        "view": _run_view,
    }
    return run[args.command](args)


def _run_ingest(args: argparse.Namespace) -> int:
    try:
        report = ingest(args.event, args.paths, data_dir=args.data_dir, progress=_print_progress)
    except IngestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print(
            "\ninterrupted; finished clips are saved, run the same command to continue",
            file=sys.stderr,
        )
        return 130

    counts = [f"{report.count(o)} {o.value}" for o in SUMMARY_ORDER if report.count(o)]
    print(f"\nSummary: {', '.join(counts) or 'nothing to do'}")
    if report.manifest_path.exists():
        print(f"Manifest: {report.manifest_path}")
    return 1 if report.has_failures else 0


def _run_sync(args: argparse.Namespace) -> int:
    try:
        timeline = sync_event(
            args.event,
            data_dir=args.data_dir,
            pictures=not args.sound_only,
            progress=_print_step,
        )
    except SyncError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\ninterrupted; nothing was written", file=sys.stderr)
        return 130
    _print_timeline(timeline)
    print(f"Timeline: {Path(args.data_dir) / timeline.event_id / TIMELINE_NAME}")
    return 0


def _print_timeline(timeline: Timeline) -> None:
    placed = sorted((c for c in timeline.clips if c.placed), key=lambda c: c.offset_s)
    unplaced = [c for c in timeline.clips if not c.placed]
    print(
        f"Event {timeline.event_id}: {len(placed)} of {len(timeline.clips)} clips on one clock "
        f"(master timeline {timeline.duration_s:.1f} s)"
    )
    width = min(40, max((len(c.name) for c in timeline.clips), default=0))
    for clip in placed:
        where = f"{clip.offset_s:+10.3f} s  {clip.duration_s:6.1f} s"
        confidence = f"  confidence {clip.confidence:.1f}" if clip.confidence is not None else ""
        drift = f"  drift {clip.drift_ppm:+.1f} ppm" if clip.drift_ppm is not None else ""
        print(f"  {clip.name:<{width}}  {where}{confidence}{drift}")
    for clip in unplaced:
        print(f"  {clip.name:<{width}}  not placed: {clip.reason}")
    rejected = Counter(p.rejected for p in timeline.pairs if p.rejected)
    parts = [f"{len(timeline.pairs)} measured", f"{sum(p.used for p in timeline.pairs)} used"]
    parts += [f"{count} {reason}" for reason, count in sorted(rejected.items())]
    print(f"Pairs: {', '.join(parts)}")
    _print_distances(timeline, placed, width)


def _print_distances(timeline: Timeline, placed: list[ClipPlacement], width: int) -> None:
    """How far each phone stood from the sound, when the pictures could tell."""
    known = [c for c in placed if c.heard_late_s is not None]
    if not known:
        if any(p.picture_lag_s is not None for p in timeline.pairs):
            print("Distance: the pictures never matched clearly enough to tell (steady light?)")
        return
    print(f"Sound travel, from the pictures ({len(known)} of {len(placed)} clips):")
    for clip in sorted(known, key=lambda c: c.heard_late_s):
        late_ms = clip.heard_late_s * 1000
        away = metres(clip.heard_late_s)
        print(f"  {clip.name:<{width}}  heard it {late_ms:+7.0f} ms late, about {away:4.0f} m away")
    print("  (counted from the nearest clip; add it to a clip's offset to line up the pictures)")


def _run_evaluate(args: argparse.Namespace) -> int:
    try:
        result = evaluate_event(args.event, args.truth, data_dir=args.data_dir)
    except EvaluationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    moments = len({error.moment for error in result.errors})
    print(f"Event {result.event_id}: {len(result.errors)} pair errors at {moments} moments")
    if result.not_placed:
        print(f"  not placed: {', '.join(result.not_placed)}")
    worst = result.worst
    if worst is None:
        print("  no two placed clips caught the same moment, so nothing could be compared")
        return 1
    print(
        f"  error median {result.median_ms:.1f} ms, 95th percentile {result.p95_ms:.1f} ms, "
        f"worst {worst.error_ms:+.1f} ms ({worst.clip_a} and {worst.clip_b} at {worst.moment})"
    )
    frame_ms = FRAME_S * 1000
    share = result.within_frame / len(result.errors)
    print(
        f"  within one frame ({frame_ms:.0f} ms): {result.within_frame} of {len(result.errors)} "
        f"({share:.0%})"
    )
    return 0


def _run_observe(args: argparse.Namespace) -> int:
    settings = WatchSettings(model=args.model, window_s=args.window)
    speech = None if args.no_speech else SpeechSettings(model=args.speech_model)
    try:
        watched = observe_event(
            args.event,
            data_dir=args.data_dir,
            settings=settings,
            speech=speech,
            again=args.again,
            progress=_print_step,
        )
    except SpeechError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except WatchError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\ninterrupted; finished clips are saved, run the same command to continue")
        return 130
    seen = sum(len(clip.observations) for clip in watched if clip)
    took = sum(clip.seconds_taken for clip in watched if clip)
    said = sum(len(clip.speech) for clip in watched if clip)
    heard_took = sum(clip.speech_seconds_taken or 0 for clip in watched if clip)
    print(f"\nEvent {args.event}: {seen} observations from {len(watched)} clips, {took:.0f} s")
    if speech is not None:
        words = sum(len(u.words) for clip in watched if clip for u in clip.speech)
        print(f"Heard {said} stretches of speech, {words} words, in {heard_took:.0f} s")
    for clip in watched:
        if clip is None or not clip.observations:
            continue
        first = clip.observations[0]
        print(f"  {clip.name} ({len(clip.observations)}): {first.summary[:90]}")
        if clip.speech:
            spoken = clip.speech[0]
            print(f"      said at {spoken.t_start_s:.0f} s: {spoken.text[:80]}")
    print(f"Observations: {Path(args.data_dir) / args.event / OBSERVATIONS_DIR}")
    return 0


def _run_fuse(args: argparse.Namespace) -> int:
    judge = None if args.no_reading else OllamaJudge(args.model)
    try:
        events = fuse_event(args.event, data_dir=args.data_dir, judge=judge, progress=_print_step)
    except (FuseError, JudgeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    event_dir = Path(args.data_dir) / args.event
    store = load_store(event_dir)
    try:
        known = counts(store) if store else {}
    finally:
        if store:
            store.close()
    both = known.get("corroborated", 0)
    print(
        f"\nEvent {args.event}: {len(events)} moments on the shared clock, "
        f"{both} of them caught by more than one clip"
    )
    unresolved = known.get("unresolved", 0)
    if known.get("conflicts"):
        print(
            f"Where the clips disagree: {known['conflicts']} "
            f"({unresolved} left unresolved, which is the honest answer when no camera had a "
            f"clearly better view)"
        )
    for event in sorted(events, key=lambda e: (-len(e.evidence), -e.strength))[:5]:
        seen_by = ", ".join(sorted(e.clip_id[:8] for e in event.evidence))
        print(f"  {event.t_master_s:8.2f} s  {event.kind:7} seen by {seen_by}")
        if event.summary:
            print(f"      {event.summary[:100]}")
    print(f"Knowledge: {event_dir / KNOWLEDGE_NAME}")
    return 0


def _run_story(args: argparse.Namespace) -> int:
    try:
        story = tell_event(
            args.event, data_dir=args.data_dir, model=args.model, progress=_print_step
        )
    except (StoryError, JudgeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"\nWhat happened at {story.event_id}, as the footage has it:\n")
    for line in story.lines:
        minutes, rest = divmod(line.t_master_s, 60)
        mark = " (the clips disagree here)" if line.disputed else ""
        clips = ", ".join(clip[:8] for clip in line.clips)
        print(f"  {int(minutes)}:{rest:04.1f}  {line.text}{mark}")
        print(f"          from {clips}")
    if story.dropped:
        print(f"\nLeft out, having nothing behind it: {len(story.dropped)}")
        for reason in story.dropped[:3]:
            print(f"  {reason[:110]}")
    print(f"\nStory: {Path(args.data_dir) / story.event_id / STORY_NAME}")
    return 0


def _run_cut(args: argparse.Namespace) -> int:
    try:
        film = cut_event(
            args.event, data_dir=args.data_dir, render=not args.plan_only, progress=_print_step
        )
    except CutError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\ninterrupted; nothing was written", file=sys.stderr)
        return 130
    minutes, seconds = divmod(film.duration_s, 60)
    print(
        f"\nFilm of {film.event_id}: {len(film.shots)} shots, {int(minutes)}:{seconds:04.1f} long, "
        f"sound from {_name_of(film, film.audio_clip_id)} ({film.audio_reason})"
    )
    width = min(34, max((len(shot.name) for shot in film.shots), default=0))
    for shot in film.shots:
        when = f"{shot.start_s - film.start_s:6.1f}-{shot.end_s - film.start_s:6.1f} s"
        print(f"  {when}  {shot.name[:34]:<{width}}  {shot.reason}")
    event_dir = Path(args.data_dir) / film.event_id
    print(f"Shots: {event_dir / CUT_NAME}")
    if not args.plan_only:
        print(f"Film: {event_dir / FILM_NAME}")
    return 0


def _name_of(film: Film, clip_id: str) -> str:
    return next((shot.name for shot in film.shots if shot.clip_id == clip_id), clip_id)


def _run_view(args: argparse.Namespace) -> int:
    try:
        serve(args.event, data_dir=args.data_dir, port=args.port, open_browser=not args.no_browser)
    except ViewError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        pass  # Ctrl+C before the server was answering; stopping is still the normal end
    print("stopped")
    return 0


def _print_step(message: str) -> None:
    print(f"  {message}", flush=True)


def _print_progress(number: int, total: int, path: Path, result: InputResult | None) -> None:
    if result is None:
        print(f"[{number}/{total}] {path.name} ... ", end="", flush=True)
        return
    print(_describe(result), flush=True)
    if result.outcome in (Outcome.ADDED, Outcome.UPDATED) and result.clip is not None:
        for issue in result.clip.issues:
            print(f"      - {issue.message}")


def _describe(result: InputResult) -> str:
    clip = result.clip
    if result.outcome in (Outcome.ADDED, Outcome.UPDATED) and clip and clip.proxy:
        proxy = clip.proxy
        return (
            f"{result.outcome.value}: {clip.status.value} (clip {clip.clip_id}, "
            f"{proxy.duration_s:.1f} s, {proxy.width}x{proxy.height})"
        )
    return f"{result.outcome.value}: {result.message}" if result.message else result.outcome.value


def _safe_console() -> None:
    """Never crash on file names the console encoding can't show (e.g. Japanese in cp1252)."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")


if __name__ == "__main__":
    raise SystemExit(main())
