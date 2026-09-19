"""Fetch a few clips of the Jiku Mobile Video Dataset and their sync ground truth.

Jiku (Saini et al., "The Jiku Mobile Video Dataset", ACM MMSys 2013, CC BY 4.0) holds phone videos
of real events. Guggenberger et al. ("A Synchronization Ground Truth for the Jiku Mobile Video
Dataset", MMM 2015) measured where each clip sits in time. This script downloads one subset whose
clips overlap, turns that measurement into Scenefold's ground truth file, and prints the commands
that test sync against it:

    uv run python tools/jiku.py jiku-saf
    uv run scenefold ingest jiku-saf data/_downloads/jiku/jiku-saf
    uv run scenefold sync jiku-saf
    uv run scenefold evaluate jiku-saf data/_downloads/jiku/jiku-saf_truth.json

The clips show real people, and the ground truth repository has no license file: both are fetched
into data/ (git-ignored) at run time and never committed.
"""

import argparse
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.request import Request, urlopen

import numpy as np

from scenefold.evaluate import moments_from_offsets
from scenefold.media import find_tools

CLIP_URL = "https://skulddata.cs.umass.edu/traces/mmsys/2013/jiku/dataset/{name}.mp4"
TRUTH_URL = (
    "https://raw.githubusercontent.com/protyposis/JikuMVD-SynchronizationGroundTruth/"
    "master/groundtruth/{event}.xml"
)
DOWNLOADS = Path("data/_downloads/jiku")

# Scenefold event name -> (Jiku event, clips). Each has three phone models recording at once.
# On both SAF_290512_20 clips (Nexus S, 16 kHz AAC) Scenefold and the ground truth disagree by
# about 85 ms, while every other pair agrees within a frame. Which side is right is not known yet.
SUBSETS = {
    "jiku-saf": (  # about 786 MB, all six overlap for about 80 s
        "SAF_290512",
        [
            "SAF_290512_14_1338292608049",
            "SAF_290512_10_1338292596396",
            "SAF_290512_12_1338292599810",
            "SAF_290512_0_1338292601130",
            "SAF_290512_20_1338292798008",
            "SAF_290512_13_1338292623418",
        ],
    ),
    "jiku-saf-long": (  # about 1.54 GB, all six overlap for about 174 s
        "SAF_290512",
        [
            "SAF_290512_0_1338291067070",
            "SAF_290512_10_1338291056844",
            "SAF_290512_12_1338291068847",
            "SAF_290512_13_1338291067230",
            "SAF_290512_14_1338291074546",
            "SAF_290512_20_1338291241416",
        ],
    ),
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("event", choices=SUBSETS, help="which subset to fetch")
    parser.add_argument("--dir", type=Path, default=DOWNLOADS, help=f"default: {DOWNLOADS}")
    args = parser.parse_args()

    jiku_event, names = SUBSETS[args.event]
    clip_dir = args.dir / args.event
    clip_dir.mkdir(parents=True, exist_ok=True)
    for number, name in enumerate(names, start=1):
        print(f"[{number}/{len(names)}] {name}.mp4")
        download(CLIP_URL.format(name=name), clip_dir / f"{name}.mp4")

    truth_xml = args.dir / f"{jiku_event}_groundtruth.xml"
    if not truth_xml.exists():
        download(TRUTH_URL.format(event=jiku_event), truth_xml)
    speeds, offsets = read_ground_truth(truth_xml, names)

    # The ground truth puts raw clip time t at offset + speed * t on its own clock; Scenefold
    # writes the same as t = (t_master - offset) * (1 + drift_ppm / 1e6). Both count clip time
    # from the file's time 0 (checked: every clip here starts its audio at 0).
    offsets_s, durations, drift_ppm = {}, {}, {}
    starts = {}
    for name in names:
        file_name = f"{name}.mp4"
        starts[name] = probe(clip_dir / file_name)
        offsets_s[file_name] = offsets[name]
        durations[file_name] = starts[name]["duration"]
        drift_ppm[file_name] = (1 / speeds[name] - 1) * 1e6
    truth = moments_from_offsets(offsets_s, durations, drift_ppm)
    # The ground truth counts decoded audio samples; Scenefold's clip time follows the file's
    # timestamps, like the picture. Some phones disagree (a Galaxy S II writes 314 ppm more samples
    # than its timestamps say), so move each moment to where the file's timestamps put it.
    to_file_time = {f"{name}.mp4": sample_clock(clip_dir / f"{name}.mp4") for name in names}
    for moment in truth.moments:
        moment.times = {n: round(to_file_time[n](t), 6) for n, t in moment.times.items()}

    # timeline.json measures drift against the average clip, so show it that way to compare
    mean = sum(drift_ppm.values()) / len(drift_ppm)
    print("\nclip                          audio start  video start  duration  GT drift vs average")
    for name in names:
        info, ppm = starts[name], drift_ppm[f"{name}.mp4"] - mean
        print(
            f"{name:<28}  {info['audio_start']:9.3f} s  {info['video_start']:9.3f} s  "
            f"{info['duration']:6.1f} s  {ppm:+6.1f} ppm"
        )
    truth_path = args.dir / f"{args.event}_truth.json"
    truth_path.write_text(truth.model_dump_json(indent=2) + "\n", encoding="utf-8")

    print(f"\nGround truth: {truth_path} ({len(truth.moments)} moments)")
    print("Next:")
    print(f"  uv run scenefold ingest {args.event} {clip_dir.as_posix()}")
    print(f"  uv run scenefold sync {args.event}")
    print(f"  uv run scenefold evaluate {args.event} {truth_path.as_posix()}")
    return 0


def download(url: str, dest: Path) -> None:
    """Download to dest, skipping a complete file and resuming a partial one (dest.part)."""
    size = int(urlopen(Request(url, method="HEAD"), timeout=60).headers["Content-Length"])
    if dest.exists() and dest.stat().st_size == size:
        print("  already downloaded")
        return
    part = dest.with_name(dest.name + ".part")
    have = part.stat().st_size if part.exists() else 0
    if have >= size:
        have = 0  # a stale or oversized leftover: start over
    request = Request(url, headers={"Range": f"bytes={have}-"} if have else {})
    with urlopen(request, timeout=60) as response:
        resumed = have and response.status == 206  # 200 means the server sent the whole file
        with part.open("ab" if resumed else "wb") as out:
            done, shown = (have if resumed else 0), -1
            while chunk := response.read(1 << 20):
                out.write(chunk)
                done += len(chunk)
                if done * 10 // size > shown:
                    shown = done * 10 // size
                    print(f"  {done / 1e6:6.0f} of {size / 1e6:.0f} MB", flush=True)
    if part.stat().st_size != size:
        sys.exit(f"{dest.name} is incomplete ({part.stat().st_size} of {size} bytes); run again")
    part.replace(dest)


def read_ground_truth(path: Path, names: list[str]) -> tuple[dict[str, float], dict[str, float]]:
    """Speed and offset (seconds) of each clip, after checking syncpoints connect them all.

    Clips not linked by a chain of syncpoints are only placed by their file timestamps, which is
    not ground truth. Chains may pass through clips outside the subset.
    """
    root = ET.parse(path).getroot()
    speeds, offsets = {}, {}
    for recording in root.iter("recording"):
        if recording.get("offset"):
            speeds[recording.get("name")] = float(recording.get("speed"))
            offsets[recording.get("name")] = timespan_s(recording.get("offset"))
    missing = [name for name in names if name not in offsets]
    if missing:
        sys.exit(f"the ground truth has no placement for {', '.join(missing)}")

    group: dict[str, str] = {}

    def root_of(name: str) -> str:
        while group.setdefault(name, name) != name:
            name = group[name]
        return name

    for point in root.iter("syncpoint"):
        a, b = point.find("recording1").get("name"), point.find("recording2").get("name")
        group[root_of(a)] = root_of(b)
    if len({root_of(name) for name in names}) > 1:
        sys.exit(
            "these clips are not all linked by syncpoints, so their ground truth is unreliable"
        )
    return speeds, offsets


def timespan_s(text: str) -> float:
    """Seconds in a .NET TimeSpan string such as '0:04:30:07.5689899' (d:hh:mm:ss.fffffff)."""
    seconds = 0.0
    for part, unit in zip(reversed(text.split(":")), (1, 60, 3600, 86400), strict=False):
        seconds += float(part) * unit
    return seconds


def sample_clock(path: Path):
    """Map time counted in decoded audio samples to the file's own timestamps (both from 0)."""
    options = (
        "-v error -select_streams a:0 -show_entries frame=pts_time,nb_samples:stream=sample_rate"
    )
    proc = subprocess.run(
        [find_tools().ffprobe, *options.split(), "-of", "json", str(path)],
        capture_output=True,
        text=True,
    )
    data = json.loads(proc.stdout)
    rate = int(data["streams"][0]["sample_rate"])
    frames = [f for f in data["frames"] if "pts_time" in f]
    stamps = np.array([float(f["pts_time"]) for f in frames])
    counted = (
        stamps[0]
        + np.concatenate([[0], np.cumsum([int(f["nb_samples"]) for f in frames])[:-1]]) / rate
    )
    return lambda t: float(np.interp(t + stamps[0], counted, stamps))


def probe(path: Path) -> dict[str, float]:
    """Audio and video start times, and the clip's length in Scenefold clip time."""
    options = "-v error -print_format json -show_entries stream=codec_type,start_time,duration"
    proc = subprocess.run(
        [find_tools().ffprobe, *options.split(), str(path)], capture_output=True, text=True
    )
    streams = {s["codec_type"]: s for s in json.loads(proc.stdout or "{}").get("streams", [])}
    if "audio" not in streams:
        sys.exit(f"{path.name} has no audio track")
    audio, video = streams["audio"], streams.get("video", {})
    audio_start = float(audio.get("start_time", 0))
    return {
        "audio_start": audio_start,
        "video_start": float(video.get("start_time", 0)),
        # the working WAV starts at clip time 0, so it lasts the audio start plus the audio
        "duration": audio_start + float(audio["duration"]),
    }


if __name__ == "__main__":
    raise SystemExit(main())
