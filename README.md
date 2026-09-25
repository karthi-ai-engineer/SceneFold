<p align="center">
  <img src="docs/scenefold-logo.gif" alt="Scenefold: five lines folding into one" width="340">
</p>

<p align="center">
  <b>Many people film the same moment.<br>
  Scenefold folds their videos into one synced, understandable, trustworthy picture of what happened.</b>
</p>

<p align="center">
  <a href="https://github.com/karthi-ai-engineer/SceneFold/actions/workflows/ci.yml"><img src="https://github.com/karthi-ai-engineer/SceneFold/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <img src="https://img.shields.io/badge/python-3.13-blue.svg" alt="Python 3.13">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-blue.svg" alt="License: Apache-2.0"></a>
  <img src="https://img.shields.io/badge/runs-on%20your%20machine-green.svg" alt="Runs locally">
</p>

<p align="center">
  <img src="docs/viewer.gif" alt="Four clips of one event playing together on one timeline" width="760">
</p>

<p align="center">
  <sub>Four clips that started at different times, on different clocks, played together on one clock.<br>
  Nobody is filmed here: the event is drawn by <code>tools/demo_event.py</code>, so you can make it yourself in a minute.</sub>
</p>

---

## What it does

| | You run | You get |
|---|---|---|
| **Line them up** | `scenefold sync` | Every clip on one clock, to within milliseconds — measured against published ground truth |
| **Watch them together** | `scenefold view` | All angles playing at once in your browser, every picture within half a frame |
| **Find out what happened** | `scenefold observe` → `fuse` | Each clip watched on its own, then merged into one account per moment |
| **Read it, question it** | `scenefold story` / `ask` | Sentences that cite the clip and second they came from, checked in code |
| **See the disagreements** | in the viewer | Where the cameras don't agree, kept visible instead of smoothed away |
| **Get a film** | `scenefold cut` | One edit of the best angles, with a stated reason for every shot |
| **See where they stood** | `scenefold map` | A top-down map of the phones, worked out from how late each one heard things |

Nothing is uploaded. Every step runs on your own machine, and the AI steps use a local model.

## Try it in two minutes

You need [uv](https://docs.astral.sh/uv/) and [FFmpeg](https://ffmpeg.org/) ([install commands](#install)).

```sh
git clone https://github.com/karthi-ai-engineer/SceneFold.git
cd SceneFold
uv sync
uv run python tools/demo_event.py    # draws an imaginary event, then ingests and syncs it
uv run scenefold view demo           # opens the viewer above at http://127.0.0.1:8765
```

## With your own videos

```sh
uv run scenefold ingest my-event path/to/videos   # copies originals, makes working copies
uv run scenefold sync my-event                    # puts them all on one clock
uv run scenefold view my-event                    # watch every angle together
uv run scenefold cut my-event                     # render one film of the best angles
```

Then, if you want it to understand the footage, with a model on your own computer
([Ollama](https://ollama.com), no account, no cost):

```sh
ollama pull qwen3.5:4b
uv run scenefold observe my-event                 # what each clip shows and what was said
uv run scenefold fuse my-event                    # merge the clips into one account per moment
uv run scenefold story my-event                   # the account, every sentence citing the footage
uv run scenefold ask my-event "what happened at the end?"
```

A story reads like this, and each line names the clips behind it:

```
  2:48.2  A performer stands on a circular stage while a massive audience holds up numerous
          red lights creating a dense field of light that occasionally dims, though one
          camera caught nothing here [1]. (the clips disagree here)
          from 34b92d86, 60780912
```

## How the pieces fit

```
 phone videos ─┬─▶ ingest ──▶ sync ──▶ VIEWER      every angle on one clock, within half a frame
               │                 │
               │                 ├───▶ cut ──────▶ FILM    one film, with a reason for every shot
               │                 │      ▲
               └─▶ observe ──────┤      │         what each clip saw and said, watched alone
                     │           │      │
                     ├─▶ people ─┤      │         who is visible, and who is the same across angles
                     │           │      │
                     └─▶ fuse ───┴──────┘         one clock, one account per moment, disagreements
                            │
                            └──▶ story / ask ──▶ ACCOUNT   every sentence citing the footage
```

Each step reads what the earlier ones wrote, so everything after `sync` is optional.
**[How it works, step by step →](docs/HOW_IT_WORKS.md)**

## What it has been measured at

On six phones at a real performance with published ground truth (`jiku-saf-long`), five YouTube
uploads of one stadium concert (`coldplay-jan26`), and a drawn event whose answers are known.

| What | Measured |
|---|---|
| **Sync error** vs. published ground truth | **2.1 ms** median, 6.4 ms worst — every moment inside one frame |
| Against other tools, same clips | **Best of four.** audio-offset-finder 6.4/30.4 ms, audalign 14.6/47.9 ms |
| One phone out of six | 70–110 ms out, cause unresolved — [written up](docs/ROADMAP.md), not hidden |
| **Viewer drift** | Every tile within **half a frame** of the shared clock, in headless Chrome |
| Moments seen by more than one phone | 219 of 298 (drawn event); 118 of 311 (concert, phones pointing different ways) |
| Disagreements found | 91, of which 74 left **unresolved** because no camera had the better view |
| Story citations | 15 sentences, 0 dropped, 4 flagged as disputed — each checked in code |
| People matched across angles | 16 of 26, 7 beyond doubt, 0 split inside a clip |
| Cost per event | **£0**, nothing leaves the machine. 20 min of footage: 4.6 min to watch, 8.5 min to find people |

**What is not measured.** Whether a sentence that cites a real moment describes it *truthfully*,
whether those 91 disagreements are real, and whether a matched person is the *right* person. All
three need an event where somebody already knows what happened, which is why `v0.5.0` and `v1.0.0`
are still untagged. [How you could help →](#the-test-event-this-project-still-needs)

---

<a name="install"></a>
<details>
<summary><b>Install</b> (Windows, macOS, Linux)</summary>

FFmpeg 7 or newer is recommended; Scenefold is developed and tested with 9.0, and the test suite
needs at least 6.0. Converting HDR videos needs FFmpeg's `zscale` filter (built with libzimg).

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

Homebrew's smaller `ffmpeg` has no `zscale`, so HDR videos keep washed-out colours and ingest says so.

**Linux (Debian, Ubuntu)**

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
sudo apt install ffmpeg
```

Ubuntu 24.04 ships FFmpeg 6.1, older than the version Scenefold is tested with.

Open a new terminal, check `uv --version` and `ffmpeg -version`, then clone the repo and run `uv sync`.
</details>

<details>
<summary><b>All commands</b></summary>

```sh
scenefold ingest <event> <videos or folders>   # originals kept read-only; working copies + manifest
scenefold sync <event>                         # one clock for every clip → timeline.json
scenefold sync <event> --sound-only            # skip the picture pass (distances); faster
scenefold evaluate <event> <truth.json>        # sync error against moments you timed yourself
scenefold observe <event>                      # what each clip shows and says (local model)
scenefold people <event>                       # who each clip can see, and what they wear
scenefold identify <event>                     # who is the same person across angles
scenefold fuse <event>                         # merge the clips into knowledge.sqlite
scenefold story <event>                        # a cited account → story.json
scenefold ask <event> "<question>"             # answered from the store, with citations
scenefold cut <event>                          # the shot list and cut.mp4
scenefold map <event>                          # where each phone stood → positions.json
scenefold view <event>                         # the synced viewer, at 127.0.0.1:8765
```

An event lives in one folder: originals, working copies, and one file per stage.

```
data/<event>/
├─ originals/          your videos, untouched and read-only
├─ proxies/            working copies (.mp4) and their sound (.wav)
├─ manifest.json       what each clip is, and any problems      (ingest)
├─ timeline.json       where each clip sits on the shared clock (sync)
├─ observations/       what each clip shows                     (observe)
├─ knowledge.sqlite    events, evidence, disagreements          (fuse)
├─ story.json          the cited account                        (story)
├─ cut.json, cut.mp4   the shot list and the film               (cut)
└─ positions.json      where each phone stood                    (map)
```
</details>

<details>
<summary><b>Development</b></summary>

```sh
uv run pytest                                                    # tests; they draw their own videos
uv run ruff format src tests tools && uv run ruff check src tests tools
node --test web/tests/sync.test.mjs                              # the viewer's timing rules
uv run --with playwright python tools/check_viewer.py my-event   # viewer sync, headless Chrome
uv run python tools/demo_event.py                                # the imaginary event above
```

Every push runs the lot on Windows, macOS and Linux. Project layout:

```
src/scenefold/   the pipeline: ingest, sync, observe, fuse, story, cut, view, and their data formats
web/             the viewer: plain HTML, CSS and JavaScript, no build step
tools/           developer scripts: public dataset, demo event, viewer checks, baselines
tests/  docs/    tests; brief, roadmap, and how it works
data/            your events; never committed
```
</details>

## Responsible use

- **Only footage from people who agreed to be filmed.**
- **Your originals are never modified**, and working copies have location and device metadata removed.
- **Nothing is uploaded.** If a cloud model is ever offered, it will be a clear choice, and face
  blurring will come first.
- **`scenefold people` describes clothing, actions and rough position** so one person can be found in
  another angle — never faces, never names, nothing measured off a body. It stays with the footage on
  your machine, and should be deleted with the event. It is for joining two angles of one afternoon,
  not for identifying a stranger.

### The test event this project still needs

Three of the measurements above are missing for one reason: checking them needs somebody who already
knows what happened. If you want to help, film one:

- **Three to five friends**, filming the same twenty minutes on their own phones, starting and
  stopping whenever they like, everyone agreeing to be filmed and to the footage being used this way.
- **Clothes that tell people apart** — not five people in black t-shirts, the one case no description
  can separate.
- **A few claps** at the start, middle and end, loud enough for every phone. Those are the moments
  sync gets scored against.
- **Write down what happened** as you go: a few times and a sentence each. That is what the account
  gets checked against.

Then run the chain and compare. That answers whether the story is faithful, whether the flagged
disagreements are real, and whether the people matched across angles are the right people.

---

**Docs:** [How it works](docs/HOW_IT_WORKS.md) · [Roadmap and results](docs/ROADMAP.md) ·
[Project brief](docs/PROJECT_BRIEF.md) · [Related work](docs/RELATED_WORK.md)

Copyright 2026 Karthi AI Engineer. Licensed under the [Apache License 2.0](LICENSE).
