"""Check sync on the pictures alone: do the clips line up by what they show, not by their sound?

    uv run python tools/check_pictures.py jiku-saf-long

Sync places clips by sound. This reads how bright each working copy is, frame by frame (stage
lights, flashes, the crowd's phone lights), and matches those brightness curves for every pair of
placed clips, searching a couple of seconds either side of where sound put them. A picture lag that
agrees with the sound lag means the whole chain is right; a pair that disagrees says by how much.

Brightness only works where something visibly changes together: stage lighting is ideal, a steady
room is not. Pairs whose brightness match is unclear are reported and skipped.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
from scipy.signal import correlate

from scenefold.manifest import normalize_event_id
from scenefold.media import find_tools

FPS = 30  # working copies are 30 fps, starting at clip time 0
WIDTH, HEIGHT = 32, 18  # each frame is shrunk to this before its brightness is taken
SMOOTH_S = 2.0  # slow changes (a camera panning, exposure) are taken out before matching
MIN_OVERLAP_S = 5.0
# Lags close to the right one score nearly as well, because brightness changes slowly. So a match
# is judged against lags far away, where nothing should line up: it counts when it stands this many
# standard deviations above them.
FAR_S = 5.0
CLEAR = 4.0
SOUND_MS_PER_M = 1000 / 343  # sound travels about a metre every 2.9 ms


def brightness(video: Path) -> np.ndarray:
    """Mean brightness of every frame, as a signal at 30 frames a second."""
    proc = subprocess.run(
        [find_tools().ffmpeg, "-v", "error", "-nostdin", "-i", str(video),
         "-vf", f"scale={WIDTH}:{HEIGHT},format=gray", "-f", "rawvideo", "-"],
        capture_output=True,
    )  # fmt: skip
    frames = np.frombuffer(proc.stdout, dtype=np.uint8)
    usable = len(frames) // (WIDTH * HEIGHT) * (WIDTH * HEIGHT)
    return frames[:usable].reshape(-1, WIDTH * HEIGHT).mean(axis=1)


def changes(signal: np.ndarray) -> np.ndarray:
    """Brightness with slow drifts removed, scaled so clips can be compared."""
    window = max(3, int(SMOOTH_S * FPS) | 1)
    padded = np.pad(signal, window // 2, mode="edge")
    slow = np.convolve(padded, np.ones(window) / window, mode="valid")[: len(signal)]
    quick = signal - slow
    return quick / (quick.std() or 1.0)


def match(a: np.ndarray, b: np.ndarray, around: int, search: int) -> tuple[float, float] | None:
    """(lag in frames, how clearly it wins) matching b against a near `around` frames."""
    lags = np.arange(-(len(b) - 1), len(a))
    overlap = np.minimum(len(a), lags + len(b)) - np.maximum(0, lags)
    scores = correlate(a, b, mode="full", method="fft") / np.maximum(overlap, 1)
    # Short overlaps are noisy, so only lags sharing a good stretch of both clips are compared.
    usable = overlap >= max(MIN_OVERLAP_S * FPS, 0.5 * overlap.max())
    near = usable & (np.abs(lags - around) <= search)
    if not near.any():
        return None
    best = int(np.argmax(np.where(near, scores, -np.inf)))
    far = usable & (np.abs(lags - lags[best]) > FAR_S * FPS)
    if far.sum() < 10 or scores[far].std() <= 0:
        return None
    clear = (scores[best] - np.median(scores[far])) / scores[far].std()
    return float(lags[best]), float(clear)


def distances(clips: list[str], pairs: list[tuple[str, str, float]]) -> dict[str, float] | None:
    """How late each clip's sound arrives, in ms, from the pairwise picture-minus-sound differences.

    Every pair says `late[b] - late[a]` = how much later b's picture sits than the sound put it.
    Solving all pairs together gives each clip one number, counted from the clip nearest the sound.
    """
    where = {clip: i for i, clip in enumerate(clips)}
    rows = np.zeros((len(pairs) + 1, len(clips)))
    wanted = np.zeros(len(pairs) + 1)
    for row, (a, b, difference) in enumerate(pairs):
        rows[row, where[a]], rows[row, where[b]] = -1.0, 1.0
        wanted[row] = difference
    rows[-1, :] = 1.0  # the clips average out to zero, so the answer is not free to slide
    found, *_ = np.linalg.lstsq(rows, wanted, rcond=None)
    if np.linalg.matrix_rank(rows) < len(clips):
        return None  # some clip never matched: its distance can't be told
    return {clip: float(found[i] - found.min()) for clip, i in where.items()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("event")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--search", type=float, default=2.0, help="seconds to search either side")
    args = parser.parse_args()

    event_dir = args.data_dir / normalize_event_id(args.event)
    timeline = json.loads((event_dir / "timeline.json").read_text(encoding="utf-8"))
    manifest = json.loads((event_dir / "manifest.json").read_text(encoding="utf-8"))
    video = {c["clip_id"]: event_dir / c["proxy"]["video"] for c in manifest["clips"]}
    placed = [c for c in timeline["clips"] if c["placed"]]
    print(f"{args.event}: reading brightness of {len(placed)} clips")
    curves = {c["clip_id"]: changes(brightness(video[c["clip_id"]])) for c in placed}

    rate = lambda c: 1 + (c["drift_ppm"] or 0) * 1e-6  # noqa: E731
    search = int(args.search * FPS)
    header = f"{'clip A':26} {'clip B':26} {'sound lag':>10} {'picture lag':>12} {'difference':>11}"
    print(f"\n{header}  clearness")
    errors, matched = [], []
    for i, a in enumerate(placed):
        for b in placed[i + 1 :]:
            sound = (b["offset_s"] - a["offset_s"]) * rate(a)  # B's time 0 on A's clock
            found = match(curves[a["clip_id"]], curves[b["clip_id"]], round(sound * FPS), search)
            names = f"{a['name'][:26]:26} {b['name'][:26]:26}"
            if found is None:
                print(f"{names} {sound:9.3f}s    no brightness match")
                continue
            lag, clear = found
            difference = (lag / FPS - sound) * 1000
            mark = "" if clear >= CLEAR else "   (unclear, ignored)"
            if clear >= CLEAR:
                errors.append(difference)
                matched.append((a["clip_id"], b["clip_id"], difference))
            found = f"{lag / FPS:11.3f}s {difference:+10.0f} ms"
            print(f"{names} {sound:9.3f}s {found}  {clear:5.1f}{mark}")

    if not errors:
        print("\nNo pair had a clear brightness match: nothing to check here.")
        return 0
    off = np.abs(errors)
    within = int((off <= 1000 / FPS).sum())
    print(
        f"\n{len(errors)} pairs matched on brightness: median {np.median(off):.0f} ms, "
        f"worst {off.max():.0f} ms, within one frame (33 ms): {within} of {len(errors)}"
    )
    print("A picture that sits later than the sound says that phone was further from the sound.")

    late = distances([c["clip_id"] for c in placed], matched)
    if late is None:
        print("Not every clip matched, so how far each phone stood can't be told.")
        return 0
    print("\nHow far each phone stood from the sound, if the pictures are right:")
    for clip in sorted(placed, key=lambda c: late[c["clip_id"]]):
        ms = late[clip["clip_id"]]
        print(
            f"  {clip['name'][:40]:42} {ms:+7.0f} ms   about {ms / SOUND_MS_PER_M:4.0f} m further"
        )
    left = np.abs([late[b] - late[a] - difference for a, b, difference in matched])
    print(
        f"One delay per clip explains the pairs to within {left.max():.0f} ms "
        f"(median {np.median(left):.0f} ms), which is how well distance alone accounts for them."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
