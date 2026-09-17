"""Command line: `scenefold ingest <event> <videos or folders>`."""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from scenefold.ingest import IngestError, InputResult, Outcome, ingest

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
    args = parser.parse_args(argv)
    _safe_console()
    return _run_ingest(args)


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
