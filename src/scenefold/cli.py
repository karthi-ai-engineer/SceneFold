"""Command line: `scenefold ingest <event> <videos or folders>`, `scenefold sync <event>`,
`scenefold evaluate <event> <ground truth>`, and `scenefold view <event>`."""

import argparse
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from scenefold.evaluate import FRAME_S, EvaluationError, evaluate_event
from scenefold.ingest import IngestError, InputResult, Outcome, ingest
from scenefold.picture_offset import metres
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
    run = {"ingest": _run_ingest, "sync": _run_sync, "evaluate": _run_evaluate, "view": _run_view}
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
