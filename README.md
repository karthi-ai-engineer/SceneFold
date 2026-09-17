<p align="center">
  <img src="docs/scenefold.png" alt="Scenefold logo" width="240">
</p>

# Scenefold

**Many people film the same moment. Scenefold folds their videos into one synced, understandable,
and trustworthy picture of what happened.**

[![CI](https://github.com/karthi-ai-engineer/SceneFold/actions/workflows/ci.yml/badge.svg)](https://github.com/karthi-ai-engineer/SceneFold/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

## Status

Early development. **Ingest works today**; syncing the clips onto one clock and a synced multi-angle
viewer come next. Later phases add a cited story of the event, where the cameras disagree, and an
automatic edit. See the [roadmap](docs/ROADMAP.md) and the [project brief](docs/PROJECT_BRIEF.md).

## What ingest does

`scenefold ingest <event> <videos or folders>` prepares phone videos of one event for the later stages:

- Keeps an untouched, read-only copy of every original.
- Makes a working copy of each video: 720p, a steady 30 fps, upright, HDR converted to normal colors,
  plus a mono 48 kHz WAV of its sound. Picture and sound start at exactly the same instant.
- Records each clip in `manifest.json` as `ok`, `warning` (for example no audio, very short, or a
  cut-off file) or `failed`, always with the reason.
- Skips files that are not videos and videos it has already processed. One bad file never stops the rest.

```
data/<event>/
├─ originals/      your videos, untouched and read-only
├─ proxies/        working copies (<clip>.mp4) and their sound (<clip>.wav)
└─ manifest.json   what each clip is, where its files are, and any problems
```

## Requirements

- [uv](https://docs.astral.sh/uv/), which also installs Python 3.13 for the project.
- [FFmpeg](https://ffmpeg.org/) with `ffprobe`, on your PATH. Version 7 or newer is recommended;
  Scenefold is developed and tested with FFmpeg 9.0. The test suite needs at least FFmpeg 6.0.
  Converting HDR videos needs the `zscale` filter (FFmpeg built with libzimg).

## Install

**Windows**

```powershell
winget install --id=astral-sh.uv -e
winget install --id=Gyan.FFmpeg -e
```

**macOS**

```sh
brew install uv ffmpeg-full
echo 'export PATH="$(brew --prefix ffmpeg-full)/bin:$PATH"' >> ~/.zshrc
```

Homebrew's smaller `ffmpeg` formula also works, but it has no `zscale`, so HDR videos keep
washed-out colors and ingest marks them with a warning.

**Linux (Debian, Ubuntu)**

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
sudo apt install ffmpeg
```

Ubuntu 24.04 ships FFmpeg 6.1, which is older than the version Scenefold is tested with.

Open a new terminal, check that `uv --version` and `ffmpeg -version` both work, then get the code:

```sh
git clone https://github.com/karthi-ai-engineer/SceneFold.git
cd SceneFold
uv sync
```

## Quick start

```sh
uv run scenefold ingest my-event path/to/videos
```

```
[1/2] IMG_4821.MOV ... added: ok (clip cd0df451e898, 10.0 s, 720x1280)
[2/2] VID_20260917_153012.mp4 ... added: ok (clip 9605c47daf16, 30.0 s, 1280x720)

Summary: 2 added
Manifest: data/my-event/manifest.json
```

Run the same command again and finished clips show `unchanged`. Event names use letters, digits,
`-` and `_`. Use `--data-dir` to keep events somewhere other than `./data`.

## Development

```sh
uv run pytest                      # all tests; they generate small test videos with FFmpeg
uv run ruff format src tests
uv run ruff check src tests
```

The maintainer's clones use a commit guard that only accepts the maintainer's GitHub identity:

```sh
git config user.name "Karthi AI Engineer"
git config user.email "296384397+karthi-ai-engineer@users.noreply.github.com"
git config core.hooksPath .githooks
```

## Project layout

```
src/scenefold/   pipeline code: cli, ingest, media (FFmpeg), manifest (data format)
tests/           tests
web/             viewer and UI (planned)
docs/            project brief and roadmap
data/            your events; never committed
```

## Responsible use

- Only use footage from people who agreed to be filmed.
- Scenefold never modifies your original files; it works on copies.
- Working copies have location and device metadata removed.
- Everything runs on your own machine; nothing is uploaded. Later phases may use cloud AI services;
  the plan is to make that a clear choice and to offer face blurring first.

## License

Copyright 2026 Karthi AI Engineer. Licensed under the [Apache License 2.0](LICENSE).
