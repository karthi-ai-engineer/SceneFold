"""Record the viewer playing an event, as a GIF for the README.

    uv run --with playwright python tools/record_viewer.py demo --out docs/viewer.gif

It starts the viewer's server, opens the page in the installed Google Chrome (Playwright's own
Chromium can't play H.264), plays from a point where every clip is recording, and turns Chrome's
own recording into a GIF with FFmpeg. Only use footage everyone in it agreed to publish:
`tools/demo_event.py` makes an event with nobody in it.
"""

import argparse
import subprocess
import sys
import threading
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from scenefold.manifest import normalize_event_id
from scenefold.media import find_tools
from scenefold.view import make_server

SIZE = {"width": 1280, "height": 740}  # the whole viewer, without empty page below it


def record(url: str, at_s: float, seconds: float, folder: Path) -> float:
    """Play the viewer from `at_s`, saving pictures of the page. Returns the rate they came at.

    Chrome's own recorder needs a binary Playwright downloads separately, so the page is
    photographed instead, as fast as it allows. The GIF then plays back at that same rate.
    """
    with sync_playwright() as play:
        browser = play.chromium.launch(
            channel="chrome",
            headless=True,
            args=["--autoplay-policy=no-user-gesture-required"],
        )
        page = browser.new_page(viewport=SIZE)
        page.goto(url)
        page.evaluate("window.scenefold.ready")
        page.evaluate(f"window.scenefold.seek({at_s})")
        time.sleep(1.5)  # let every video land and draw its first frame
        page.evaluate("window.scenefold.play()")
        started, frames = time.monotonic(), 0
        while time.monotonic() - started < seconds:
            page.screenshot(path=str(folder / f"frame_{frames:04d}.png"))
            frames += 1
        taken = time.monotonic() - started
        page.evaluate("window.scenefold.pause()")
        browser.close()
    return frames / taken


def to_gif(folder: Path, out: Path, rate: float, width: int, skip: int) -> None:
    """The pictures into a GIF, at the rate they were taken, with one palette for the whole clip."""
    steps = (
        f"scale={width}:-1:flags=lanczos,split[a][b];"
        "[a]palettegen=stats_mode=diff:max_colors=160[p];[b][p]paletteuse=dither=bayer:bayer_scale=3"
    )
    done = subprocess.run(
        [find_tools().ffmpeg, "-v", "error", "-nostdin", "-y", "-framerate", f"{rate:.3f}",
         "-start_number", str(skip), "-i", str(folder / "frame_%04d.png"),
         "-filter_complex", steps, "-loop", "0", str(out)],
        capture_output=True, text=True,
    )  # fmt: skip
    if done.returncode != 0:
        raise SystemExit(f"FFmpeg failed:\n{done.stderr.strip()}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("event")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--out", type=Path, default=Path("docs/viewer.gif"))
    parser.add_argument("--at", type=float, default=75.0, help="play from this shared-clock second")
    parser.add_argument("--seconds", type=float, default=9.0, help="how long to play")
    parser.add_argument("--skip", type=int, default=3, help="drop this many pictures of the start")
    parser.add_argument("--width", type=int, default=900)
    args = parser.parse_args()

    server = make_server(args.data_dir / normalize_event_id(args.event), port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        folder = args.out.parent / "_recording"
        folder.mkdir(parents=True, exist_ok=True)
        print(f"Recording {args.event} from {args.at:.0f} s for {args.seconds:.0f} s")
        rate = record(server.url, args.at, args.seconds, folder)
        print(f"{rate:.1f} pictures a second")
        to_gif(folder, args.out, rate, args.width, args.skip)
        for picture in folder.glob("frame_*.png"):
            picture.unlink()
        folder.rmdir()
    finally:
        server.shutdown()
    print(f"{args.out}: {args.out.stat().st_size / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
