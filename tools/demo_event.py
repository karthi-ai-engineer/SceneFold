"""Make a demo event: one imaginary show, filmed by four imaginary phones.

    uv run python tools/demo_event.py     # writes data/demo/, then `scenefold view demo`

Nobody is filmed, so the result can be published (the README picture). The show is drawn by FFmpeg:
a lit stage, three figures moving to their own rhythm, a clock, and stage lighting that pulses and
flashes. Each phone then takes its own window of it, points somewhere else, and brings its own
brightness, microphone noise, room echo, and clock drift, so sync has real work to do and the true
offsets are known.
"""

import argparse
import subprocess
import sys
import zlib
from dataclasses import dataclass
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scenefold import cli  # noqa: E402
from scenefold.media import find_tools  # noqa: E402
from tests import synth  # noqa: E402

SECONDS = 120.0
SIZE = (1280, 720)
FPS = 30
FONTS = (  # a clock in the picture shows at a glance that the clips are on the same instant
    Path("C:/Windows/Fonts/arial.ttf"),
    Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
)


@dataclass(frozen=True)
class Camera:
    """One imaginary phone: where it stands, what it frames, and how its clock behaves."""

    name: str
    start_s: float
    seconds: float
    crop: tuple[int, int, int, int]  # width, height, x, y in the show
    size: tuple[int, int]  # what the phone records
    drift_ppm: float
    brightness: float = 0.0
    gain: float = 1.0
    snr_db: float = 30.0
    echo: float = 0.0


CAMERAS = (
    Camera("wide-stage", 0.0, 95.0, (1280, 720, 0, 0), (1280, 720), 120, snr_db=34.0),
    Camera("front-row", 21.5, 85.0, (860, 484, 210, 150), (1280, 720), -240, 0.06, 1.2, 28.0, 0.25),
    Camera("left-portrait", 47.2, 70.0, (405, 720, 430, 0), (406, 720), 310, -0.04, 0.8, 26.0, 0.4),
    Camera(
        "back-of-crowd", 69.4, 50.0, (1180, 664, 50, 30), (1280, 720), -60, -0.09, 0.6, 22.0, 0.5
    ),
)


def show_filters(font: Path | None) -> str:
    """The show: stage, truss, three figures, a clock, and lighting that pulses and flashes."""
    parts = [
        "drawbox=y=470:w=1280:h=250:color=0x1c1c33:t=fill",  # the stage
        "drawbox=y=466:w=1280:h=6:color=0x3d3d66:t=fill",  # its lit front edge
        "drawbox=y=0:w=1280:h=70:color=0x262640:t=fill",  # the lighting truss
        # two spotlights sweeping out of the truss, and the band
        "drawbox=x='240+420*sin(2*PI*t/11)':y=70:w=10:h=420:color=0xfff0c0@0.30:t=fill",
        "drawbox=x='900-380*sin(2*PI*t/13+2)':y=70:w=10:h=420:color=0xc0e0ff@0.30:t=fill",
        "drawbox=x='590+260*sin(2*PI*t/9)':y=316:w=60:h=160:color=0xffb454:t=fill",
        "drawbox=x='300+90*sin(2*PI*t/5+1)':y=346:w=48:h=130:color=0x6fd3ff:t=fill",
        "drawbox=x='940+70*sin(2*PI*t/7+2)':y=346:w=48:h=130:color=0xff7ab8:t=fill",
        # the crowd in front, heads bobbing on the beat (96 to the minute)
        "drawbox=y=676:w=1280:h=44:color=0x05050c:t=fill",
    ]
    for index in range(9):
        bob = f"668+7*sin(2*PI*t*1.6+{index})"
        parts.append(f"drawbox=x={60 + index * 145}:y='{bob}':w=58:h=52:color=0x05050c:t=fill")
    if font is not None:
        where = font.as_posix().replace(":", r"\:")
        parts.append(
            f"drawtext=fontfile='{where}':text='%{{pts\\:hms}}':x=(w-tw)/2:y=560:"
            "fontsize=54:fontcolor=white:box=1:boxcolor=0x000000@0.45:boxborderw=14"
        )
    # The lighting is what `tools/check_pictures.py` reads, so it has to be as restless as real
    # stage lighting: many pulses at unrelated speeds, which never repeat and never look alike two
    # moments running. A few slow pulses would match anywhere, and single-frame flicker would not
    # survive re-encoding. A flash comes now and then on top.
    rng = np.random.default_rng(4)
    speeds = rng.uniform(0.4, 5.0, 14)
    levels = 0.10 / np.sqrt(speeds)  # slower pulses swing wider, as lighting does
    wash = "+".join(
        f"{level:.4f}*sin(2*PI*t*{speed:.4f}+{phase:.3f})"
        for level, speed, phase in zip(levels, speeds, rng.uniform(0, 6.28, 14), strict=True)
    )
    flash = "0.25*lt(mod(t*0.53+0.8*sin(2*PI*t*0.11),1),0.05)"
    parts.append(f"eq=brightness='{wash}+{flash}':eval=frame")
    return ",".join(parts)


def make_show(folder: Path, seconds: float) -> Path:
    """Draw and record the show itself: the master everybody films."""
    font = next((path for path in FONTS if path.exists()), None)
    if font is None:
        print("No font found, so the clips carry no clock.")
    sound = synth.loop(seconds, bpm=96, seed=3) * 0.55 + synth.scene(seconds, seed=11)
    wav = synth.write_wav(folder / "show.wav", (sound / np.max(np.abs(sound)) * 0.7).astype("f4"))
    video = folder / "show.mp4"
    run(
        "-f", "lavfi", "-i", f"color=c=0x0a0a12:s={SIZE[0]}x{SIZE[1]}:r={FPS}:d={seconds}",
        "-i", str(wav), "-vf", show_filters(font), "-c:v", "libx264", "-preset", "medium",
        "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-shortest", str(video),
    )  # fmt: skip
    return video


def film(show: Path, sound: np.ndarray, camera: Camera, folder: Path) -> Path:
    """One phone's clip: its window of the show, its framing, its clock."""
    heard = synth.record(
        sound,
        synth.Phone(
            start_s=camera.start_s,
            seconds=camera.seconds,
            gain=camera.gain,
            snr_db=camera.snr_db,
            echo=camera.echo,
            seed=zlib.crc32(camera.name.encode()) % 1000,  # not hash(): it changes every run
            drift_ppm=camera.drift_ppm,
        ),
    )
    wav = synth.write_wav(folder / f"{camera.name}.wav", heard)
    width, height, x, y = camera.crop
    clock = 1 + camera.drift_ppm * 1e-6  # a fast clock stretches what it records
    clip = folder / f"{camera.name}.mp4"
    run(
        "-ss", f"{camera.start_s}", "-t", f"{camera.seconds / clock}", "-i", str(show),
        "-i", str(wav), "-filter_complex",
        f"[0:v]crop={width}:{height}:{x}:{y},scale={camera.size[0]}:{camera.size[1]},"
        f"eq=brightness={camera.brightness}:saturation=1.05,setpts={clock}*PTS[v]",
        "-map", "[v]", "-map", "1:a", "-c:v", "libx264", "-preset", "medium", "-crf", "21",
        "-pix_fmt", "yuv420p", "-r", str(FPS), "-c:a", "aac", "-b:a", "128k", str(clip),
    )  # fmt: skip
    return clip


def run(*args: str) -> None:
    done = subprocess.run(
        [find_tools().ffmpeg, "-v", "error", "-nostdin", "-y", *args],
        capture_output=True,
        text=True,
    )
    if done.returncode != 0:
        raise SystemExit(f"FFmpeg failed:\n{done.stderr.strip()}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--event", default="demo")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--seconds", type=float, default=SECONDS)
    args = parser.parse_args()

    folder = args.data_dir / "_downloads" / "demo"
    folder.mkdir(parents=True, exist_ok=True)
    print(f"Drawing a {args.seconds:.0f} s show")
    show = make_show(folder, args.seconds)
    sound = synth.loop(args.seconds, bpm=96, seed=3) * 0.55 + synth.scene(args.seconds, seed=11)
    sound = (sound / np.max(np.abs(sound)) * 0.7).astype("f4")

    clips = []
    for camera in CAMERAS:
        print(f"Filming {camera.name}: from {camera.start_s:.1f} s, {camera.seconds:.0f} s long")
        clips.append(str(film(show, sound, camera, folder)))
    print("\nTrue offsets: " + ", ".join(f"{c.name} {c.start_s:+.1f} s" for c in CAMERAS))

    data = ["--data-dir", str(args.data_dir)]
    if cli.main(["ingest", args.event, *clips, *data]) or cli.main(["sync", args.event, *data]):
        return 1
    print(f"\nWatch it: uv run scenefold view {args.event}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
