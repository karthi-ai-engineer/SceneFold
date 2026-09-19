"""Compare Scenefold's sync with two open-source baselines on the same audio and ground truth.

Scoring is the same as `scenefold evaluate`: at every ground-truth moment that two clips both
caught, each method predicts clip A's time of the moment from clip B's time, and the error is how
far off that is. The moments come from tools/jiku.py (data/_downloads/jiku/<event>_truth.json).

- scenefold_pair:   the raw pair measurement in data/<event>/timeline.json (lag and pair drift)
- scenefold_solved: the solved clip offsets and drifts in timeline.json
- audio_offset_finder (BBC, Apache-2.0): MFCC cross-correlation, one call per pair, both orders
- audalign (MIT): CorrelationRecognizer on all clips at once, then its fine_align

Every method reads the same working WAVs (data/<event>/proxies/<clip_id>.wav). Both baselines pin
an old numpy that does not install on Python 3.13, so this runs outside the project environment
and reads the JSON files directly instead of importing scenefold:

    uv run --no-project --python 3.12 --with audalign --with audio-offset-finder python tools/baselines.py jiku-saf jiku-saf-long

Needs FFmpeg on PATH. Writes data/_downloads/jiku/baselines_<event>.json and prints a summary.
"""  # noqa: E501 (the command above is kept on one line so it can be copied)

import argparse
import contextlib
import io
import json
import time
from pathlib import Path

import audalign
import numpy as np
from audio_offset_finder.audio_offset_finder import find_offset_between_files

DOWNLOADS = Path("data/_downloads/jiku")
METHODS = ["scenefold_pair", "scenefold_solved", "audio_offset_finder", "audalign", "audalign_fine"]
FRAME_MS = 1000 / 30  # one video frame at 30 fps
WRONG_MS = 1000  # further off than this is a wrong match, not an inaccurate one


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("events", nargs="+", help="events that have run scenefold sync")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    args = parser.parse_args()
    for event in args.events:
        result = compare(args.data_dir / event, DOWNLOADS / f"{event}_truth.json")
        out = DOWNLOADS / f"baselines_{event}.json"
        out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print_result(event, result)
        print(f"wrote {out}\n")


def compare(event_dir: Path, truth_path: Path) -> dict:
    manifest = json.loads((event_dir / "manifest.json").read_text(encoding="utf-8"))
    timeline = json.loads((event_dir / "timeline.json").read_text(encoding="utf-8"))
    moments = json.loads(truth_path.read_text(encoding="utf-8"))["moments"]
    names = {c["clip_id"]: c["source"]["name"] for c in manifest["clips"]}
    wavs = {c["clip_id"]: str(event_dir / c["proxy"]["audio"]) for c in manifest["clips"]}
    solved = {c["clip_id"]: (c["offset_s"], c["drift_ppm"] or 0.0) for c in timeline["clips"]}
    measured = {(p["clip_a"], p["clip_b"]): p for p in timeline["pairs"]}

    run_time_s = {}
    started = time.perf_counter()
    offset_finder = {pair: offset_finder_lag(wavs[pair[0]], wavs[pair[1]]) for pair in measured}
    run_time_s["audio_offset_finder"] = round(time.perf_counter() - started, 1)
    coarse, fine, run_time_s["audalign"], run_time_s["audalign_fine"] = audalign_shifts(wavs)

    rows = []
    for (a, b), pair in measured.items():
        lags = {
            "audio_offset_finder": offset_finder[a, b][0],
            "audalign": shift_lag(coarse, a, b),
            "audalign_fine": shift_lag(fine, a, b),
        }
        for moment in moments:
            times = moment["times"]
            if names[a] not in times or names[b] not in times:
                continue
            t_a, t_b = times[names[a]], times[names[b]]
            predicted = {
                # on A's clock B's time 0 is at lag_s, and B's clock runs drift_ppm faster
                "scenefold_pair": pair["lag_s"] + t_b / (1 + (pair["drift_ppm"] or 0.0) * 1e-6),
                "scenefold_solved": solved_time(solved[a], solved[b], t_b),
                **{m: None if lag is None else lag + t_b for m, lag in lags.items()},
            }
            rows.append(
                {
                    "clip_a": Path(names[a]).stem,
                    "clip_b": Path(names[b]).stem,
                    "moment": moment["label"],
                    "error_ms": {
                        m: None if v is None else (v - t_a) * 1e3 for m, v in predicted.items()
                    },
                    "scenefold_used": pair["used"],
                    "audio_offset_finder_score": offset_finder[a, b][1],
                }
            )

    with_20 = [r for r in rows if "_20_" in r["clip_a"] + r["clip_b"]]
    return {
        "run_time_s": run_time_s,
        "summary": {
            m: {
                "all": summarise([r["error_ms"][m] for r in rows]),
                "without_20": summarise([r["error_ms"][m] for r in rows if r not in with_20]),
            }
            for m in METHODS
        },
        # on device 20: is a method closer to the ground truth or to Scenefold's measurement?
        "device_20_median_abs_ms": {
            m: {
                "vs_truth": median_abs([r["error_ms"][m] for r in with_20]),
                "vs_scenefold_pair": median_abs(
                    [gap(r["error_ms"][m], r["error_ms"]["scenefold_pair"]) for r in with_20]
                ),
            }
            for m in METHODS
        },
        "rows": rows,
    }


def solved_time(a: tuple[float, float], b: tuple[float, float], t_b: float) -> float:
    """Clip A's time of the instant at clip B's time t_b, from (offset_s, drift_ppm) of each."""
    master = b[0] + t_b / (1 + b[1] * 1e-6)
    return (master - a[0]) * (1 + a[1] * 1e-6)


def offset_finder_lag(wav_a: str, wav_b: str) -> tuple[float, float]:
    """Lag of B on A's clock and its standard score, from the order that scores higher.

    audio-offset-finder correlates the start of one file against the other, so order matters.
    """
    forward = find_offset_between_files(wav_a, wav_b, fs=8000)
    backward = find_offset_between_files(wav_b, wav_a, fs=8000)
    if forward["standard_score"] >= backward["standard_score"]:
        return float(forward["time_offset"]), float(forward["standard_score"])
    return -float(backward["time_offset"]), float(backward["standard_score"])


def audalign_shifts(wavs: dict[str, str]) -> tuple[dict, dict, float, float]:
    """Where each clip starts on audalign's common timeline, before and after fine_align."""
    recognizer = audalign.CorrelationRecognizer()
    recognizer.config.multiprocessing = False  # one process per CPU core runs out of memory
    with contextlib.redirect_stdout(io.StringIO()):  # audalign prints a line per comparison
        started = time.perf_counter()
        results = audalign.align_files(*wavs.values(), recognizer=recognizer)
        coarse_s = time.perf_counter() - started
        coarse = shifts_by_clip(results, wavs)
        # audalign 1.3.1 bug: without multiprocessing, fine_align correlates the unshifted files
        # (CorrelationRecognizer._align drops the shifted audio). Two workers fit in memory.
        recognizer.config.multiprocessing, recognizer.config.num_processors = True, 2
        started = time.perf_counter()
        fine = shifts_by_clip(audalign.fine_align(results, recognizer=recognizer), wavs)
        fine_s = time.perf_counter() - started
    return coarse, fine, round(coarse_s, 1), round(fine_s, 1)


def shifts_by_clip(results: dict | None, wavs: dict[str, str]) -> dict[str, float]:
    results = results or {}
    return {c: results[Path(w).name] for c, w in wavs.items() if Path(w).name in results}


def shift_lag(shifts: dict[str, float], a: str, b: str) -> float | None:
    return shifts[b] - shifts[a] if a in shifts and b in shifts else None


def gap(error: float | None, other: float) -> float | None:
    return None if error is None else error - other


def summarise(errors_ms: list[float | None]) -> dict:
    found = np.abs([e for e in errors_ms if e is not None])
    if not len(found):
        return {"errors": len(errors_ms), "missing": len(errors_ms)}
    return {
        "errors": len(errors_ms),
        "missing": len(errors_ms) - len(found),
        "median_ms": round(float(np.median(found)), 1),
        "p95_ms": round(float(np.percentile(found, 95)), 1),
        "max_ms": round(float(found.max()), 1),
        "within_frame": int((found <= FRAME_MS).sum()),
        "wrong_over_1s": int((found > WRONG_MS).sum()),
    }


def median_abs(values: list[float | None]) -> float:
    return round(float(np.median(np.abs([v for v in values if v is not None]))), 1)


def print_result(event: str, result: dict) -> None:
    print(f"\n{event}  run time (s): {result['run_time_s']}")
    print(
        f"{'method':<20} {'pairs':<11} {'median':>7} {'p95':>7} {'max':>9} {'<=33ms':>9} {'>1s':>4}"
    )
    for method, subsets in result["summary"].items():
        for subset, s in subsets.items():
            if s["missing"] == s["errors"]:
                print(f"{method:<20} {subset:<11} no result")
                continue
            print(
                f"{method:<20} {subset:<11} {s['median_ms']:7.1f} {s['p95_ms']:7.1f} "
                f"{s['max_ms']:9.1f} {s['within_frame']:5}/{s['errors'] - s['missing']:<3} "
                f"{s['wrong_over_1s']:4}"
            )
    print("device 20 pairs, median |difference| in ms:")
    for method, gaps in result["device_20_median_abs_ms"].items():
        print(
            f"  {method:<20} vs truth {gaps['vs_truth']:8.1f}   vs scenefold pair "
            f"{gaps['vs_scenefold_pair']:8.1f}"
        )


if __name__ == "__main__":
    main()
