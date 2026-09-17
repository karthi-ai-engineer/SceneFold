"""Compare Scenefold's sync with two open-source baselines on the same audio and ground truth.

For every pair of clips in an event, each method gives a lag: where clip B's time 0 falls on clip
A's clock. The error is that lag minus the ground truth lag (see tools/jiku.py for the data).

- scenefold_pair:   the raw pair measurement in data/<event>/timeline.json
- scenefold_solved: the lag implied by the solved clip offsets and drifts in timeline.json
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
import itertools
import json
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import audalign
import numpy as np
from audio_offset_finder.audio_offset_finder import find_offset_between_files

DOWNLOADS = Path("data/_downloads/jiku")
TRUTH_XML = DOWNLOADS / "SAF_290512_groundtruth.xml"
METHODS = ["scenefold_pair", "scenefold_solved", "audio_offset_finder", "audalign", "audalign_fine"]
FRAME_MS = 33  # one video frame at 30 fps
WRONG_MS = 1000  # further off than this is a wrong match, not an inaccurate one


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("events", nargs="+", help="events that have run scenefold sync")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    args = parser.parse_args()
    truth = read_ground_truth(TRUTH_XML)
    for event in args.events:
        result = compare(args.data_dir / event, truth)
        out = DOWNLOADS / f"baselines_{event}.json"
        out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print_result(event, result)
        print(f"wrote {out}\n")


def compare(event_dir: Path, truth: dict[str, tuple[float, float]]) -> dict:
    manifest = json.loads((event_dir / "manifest.json").read_text(encoding="utf-8"))
    timeline = json.loads((event_dir / "timeline.json").read_text(encoding="utf-8"))
    names = {c["clip_id"]: Path(c["source"]["name"]).stem for c in manifest["clips"]}
    wavs = {c["clip_id"]: str(event_dir / c["proxy"]["audio"]) for c in manifest["clips"]}
    solved = {c["clip_id"]: (c["offset_s"], c["drift_ppm"]) for c in timeline["clips"]}
    measured = {(p["clip_a"], p["clip_b"]): p for p in timeline["pairs"]}
    for a, b in itertools.combinations(names, 2):  # make sure every pair is there, in any order
        if (a, b) not in measured and (b, a) not in measured:
            raise SystemExit(f"timeline.json has no pair for {names[a]} and {names[b]}")

    run_time_s = {}
    started = time.perf_counter()
    offset_finder = {pair: offset_finder_lag(wavs[pair[0]], wavs[pair[1]]) for pair in measured}
    run_time_s["audio_offset_finder"] = round(time.perf_counter() - started, 1)
    coarse, fine, run_time_s["audalign"], run_time_s["audalign_fine"] = audalign_shifts(wavs)

    pairs = []
    for (a, b), measurement in measured.items():
        truth_lag = clock_lag(truth[names[a]], truth[names[b]])
        lags = {
            "scenefold_pair": measurement["lag_s"],
            "scenefold_solved": clock_lag(solved[a], solved[b]),
            "audio_offset_finder": offset_finder[a, b][0],
            "audalign": shift_lag(coarse, a, b),
            "audalign_fine": shift_lag(fine, a, b),
        }
        pairs.append(
            {
                "clip_a": names[a],
                "clip_b": names[b],
                "truth_lag_s": truth_lag,
                "lag_s": lags,
                "error_ms": {
                    m: None if v is None else (v - truth_lag) * 1e3 for m, v in lags.items()
                },
                "scenefold_used": measurement["used"],
                "scenefold_confidence": measurement["confidence"],
                "audio_offset_finder_score": offset_finder[a, b][1],
            }
        )

    with_20 = [p for p in pairs if "_20_" in p["clip_a"] + p["clip_b"]]
    return {
        "run_time_s": run_time_s,
        "summary": {
            m: {
                "all": summarise([p["error_ms"][m] for p in pairs]),
                "without_20": summarise([p["error_ms"][m] for p in pairs if p not in with_20]),
            }
            for m in METHODS
        },
        # on device 20 pairs: is a method closer to the ground truth or to Scenefold's measurement?
        "device_20_median_abs_ms": {
            m: {
                "vs_truth": median_abs([p["error_ms"][m] for p in with_20]),
                "vs_scenefold_pair": median_abs(
                    [lag_gap(p["lag_s"][m], p["lag_s"]["scenefold_pair"]) for p in with_20]
                ),
            }
            for m in METHODS
        },
        "pairs": pairs,
    }


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


def clock_lag(a: tuple[float, float], b: tuple[float, float]) -> float:
    """B's time 0 on A's clock, from (offset_s, drift_ppm) of each clip on a shared clock."""
    return (b[0] - a[0]) * (1 + a[1] * 1e-6)


def lag_gap(lag: float | None, other: float) -> float | None:
    return None if lag is None else (lag - other) * 1e3


def read_ground_truth(path: Path) -> dict[str, tuple[float, float]]:
    """(offset_s, drift_ppm) per recording name. Offsets are .NET TimeSpans d:hh:mm:ss.fffffff."""
    truth = {}
    for recording in ET.parse(path).getroot().iter("recording"):
        if recording.get("offset"):
            parts = [float(x) for x in recording.get("offset").split(":")]
            units = (1, 60, 3600, 86400)
            offset = sum(x * unit for x, unit in zip(reversed(parts), units, strict=False))
            truth[recording.get("name")] = (offset, (1 / float(recording.get("speed")) - 1) * 1e6)
    return truth


def summarise(errors_ms: list[float | None]) -> dict:
    found = np.abs([e for e in errors_ms if e is not None])
    if not len(found):
        return {"pairs": len(errors_ms), "missing": len(errors_ms)}
    return {
        "pairs": len(errors_ms),
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
        f"{'method':<20} {'pairs':<11} {'median':>7} {'p95':>7} {'max':>9} {'<=33ms':>7} {'>1s':>4}"
    )
    for method, subsets in result["summary"].items():
        for subset, s in subsets.items():
            if s["missing"] == s["pairs"]:
                print(f"{method:<20} {subset:<11} no result")
                continue
            print(
                f"{method:<20} {subset:<11} {s['median_ms']:7.1f} {s['p95_ms']:7.1f} "
                f"{s['max_ms']:9.1f} {s['within_frame']:4}/{s['pairs'] - s['missing']:<2} "
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
