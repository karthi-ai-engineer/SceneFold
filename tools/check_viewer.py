"""Play an event in the viewer in headless Chrome and measure how well the pictures stay in sync.

    uv run --with playwright python tools/check_viewer.py jiku-saf-long

It starts the viewer's server, opens the page in the installed Google Chrome (Playwright's own
Chromium can't play H.264), then:
- plays from a few points across the timeline and reads the viewer's own per-frame measurement of
  how far each picture is from where the shared clock wants it (window.scenefold.stats());
- jumps to random points, paused and while playing, and checks every video lands in place.

Exits 1 if any clip's 95th-percentile error during playback is over one frame (33 ms), if a jump
while paused leaves a video more than half a frame away, or if a second after a jump while playing
any picture is still more than a frame off.
"""

import argparse
import random
import sys
import threading
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from scenefold.manifest import normalize_event_id
from scenefold.view import make_server

FRAME_MS = 1000 / 30


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("event")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument(
        "--seconds", type=float, default=15.0, help="play this long from each point"
    )
    parser.add_argument("--points", type=int, default=3, help="places to play from")
    parser.add_argument("--jumps", type=int, default=8, help="random jumps to check")
    parser.add_argument("--show", action="store_true", help="open a visible window instead")
    args = parser.parse_args()

    server = make_server(args.data_dir / normalize_event_id(args.event), port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    failed = False
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                channel="chrome",
                headless=not args.show,
                args=["--autoplay-policy=no-user-gesture-required"],
            )
            page = browser.new_page(viewport={"width": 1600, "height": 1000})
            page.goto(server.url)
            page.evaluate("window.scenefold.ready")
            start, end = page.evaluate("window.scenefold.span()")
            print(f"{args.event}: shared timeline {start:.1f}-{end:.1f} s")

            failed |= check_playback(page, start, end, args.seconds, args.points)
            failed |= check_jumps(page, start, end, args.jumps)
            browser.close()
    finally:
        server.shutdown()
    print("FAILED" if failed else "OK: every picture stayed within a frame")
    return 1 if failed else 0


def check_playback(page, start: float, end: float, seconds: float, points: int) -> bool:
    failed = False
    usable = max(0.0, end - start - seconds - 1)
    for k in range(points):
        at = start + usable * (k + 0.5) / points
        page.evaluate(f"window.scenefold.seek({at})")
        page.evaluate("window.scenefold.play()")
        time.sleep(0.5)  # let every video start; the viewer skips its settling frames anyway
        page.evaluate("window.scenefold.resetStats()")
        time.sleep(seconds)
        stats = page.evaluate("window.scenefold.stats()")
        page.evaluate("window.scenefold.pause()")
        print(f"\nplaying {seconds:.0f} s from {at:.1f} s")
        print(f"  {'clip':40} {'frames':>6} {'mean':>8} {'p95':>8} {'worst':>8} {'jumps':>5}")
        for s in stats:
            name = s["name"][:40]
            if not s["frames"]:
                print(f"  {name:40} {'no frames' if s['recording'] else 'not recording'}")
                continue
            bad = s["p95Abs"] > FRAME_MS
            failed |= bad
            role = " (sound)" if s["audible"] else ""
            flag = "  <- over a frame" if bad else ""
            print(
                f"  {(s['name'] + role)[:40]:40} {s['frames']:6d} {s['meanAbs']:6.1f}ms "
                f"{s['p95Abs']:6.1f}ms {s['maxAbs']:6.1f}ms {s['seeks']:5d}"
                f"  (signed mean {s['mean']:+.1f} ms, rate {s['rate']:.4f}){flag}"
            )
    return failed


def check_jumps(page, start: float, end: float, jumps: int) -> bool:
    failed = False
    rng = random.Random(7)
    worst = 0.0
    print(f"\n{jumps} random jumps (half paused, half while playing)")
    for k in range(jumps):
        t = rng.uniform(start, end - 3)
        if k % 2 == 0:  # paused: every recording video must show the requested moment
            page.evaluate(f"window.scenefold.seek({t})")
            positions = page.evaluate("window.scenefold.positions()")
            off = max((abs(p["error_ms"]) for p in positions), default=0.0)
            bad = off > FRAME_MS / 2
            detail = f"{len(positions)} videos, furthest {off:6.2f} ms"
        else:  # playing: within a second of the jump, every picture must be back within a frame
            page.evaluate("window.scenefold.play()")
            time.sleep(0.5)
            page.evaluate(f"window.scenefold.seek({t})")  # resumes playing once all have jumped
            time.sleep(1.0)  # videos restart at slightly different moments and catch up
            page.evaluate("window.scenefold.resetStats()")
            time.sleep(2.0)
            stats = [s for s in page.evaluate("window.scenefold.stats()") if s["frames"]]
            page.evaluate("window.scenefold.pause()")
            off = max((s["p95Abs"] for s in stats), default=0.0)
            bad = off > FRAME_MS
            detail = f"{len(stats)} videos, worst p95 {off:6.2f} ms, 1-3 s after"
        worst = max(worst, off)
        failed |= bad
        state = "while playing" if k % 2 else "paused"
        print(f"  jump to {t:7.2f} s {state:13} {detail}{'  <- off' if bad else ''}")
    print(f"  worst after a jump: {worst:.2f} ms")
    return failed


if __name__ == "__main__":
    sys.exit(main())
