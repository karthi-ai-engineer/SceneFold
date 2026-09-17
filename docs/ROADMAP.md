# Scenefold Roadmap

We build Scenefold one phase at a time. Each phase ends with something that runs, is tested, and is measured.
The vision and firm principles live in [`PROJECT_BRIEF.md`](PROJECT_BRIEF.md). This file covers **what to build next** and **when a phase counts as done**.

Last updated: 2026-09-17

## Status

| Phase | Name | Brief outcomes | Rough size (3 h sessions) | Status |
|---|---|---|---|---|
| 0 | Foundation | — | 1 | Done |
| 1 | Ingest | 1 | 1 | Done on generated clips; recheck with real phone footage |
| 2 | Sync | 2 | 1–2 | In progress: `scenefold sync` works on synthetic audio; real-footage checks next |
| 3 | Synced viewer → **v0.1.0** | 3 | 1–2 | Not started |
| 4 | Quality cut (no AI) | 9 (basic) | 1 | Not started |
| 5 | Clip understanding | 4 | 2 | Not started |
| 6 | Event knowledge + conflicts | 6, 8 | 2 | Not started |
| 7 | Story + Q&A → **v0.5.0** | 7, 10 | 1–2 | Not started |
| 8 | Cross-angle identity | 5 | 2–3 | Not started |
| 9 | Smart cut + release → **v1.0.0** | 9 | 2 | Not started |

**Why this order differs from the brief's roadmap:** identity (Phase 8) is the riskiest, most research-heavy part.
The story and disagreement features can work first with the AI's own descriptions ("person in red"), so a full
end-to-end demo exists by v0.5.0. Identity then upgrades the result. Stages are file-based, so re-running
fusion afterward is cheap. A basic director's cut (Phase 4) comes early because it needs no AI and makes the first demo stronger.

---

## How we work (keeps the repo clean)

- **One phase at a time.** Start the next phase only when the current one meets its "Done when" list.
- **One branch per phase, in a chain:** each phase branch starts from `main` right after the previous
  phase was merged, so it builds on everything before it (e.g. `phase-2-sync`, then `phase-3-viewer`).
  Commit in small, meaningful steps. **Push at the end of every session**, even mid-phase, because we switch machines.
- **Fixes to earlier phases** go on the current phase branch; old phase branches are never reopened.
- **Commit messages** follow Conventional Commits: `feat(sync): add GCC-PHAT pair measurement`, `test(ingest): …`, `docs: …`.
- **Finishing a phase:** CI green → PR into `main` → fast-forward merge (commits stay exactly as written)
  → delete the phase branch → update the Status table above. Milestone phases (3, 7, 9) also get a version tag.
- **`main` always works:** lint and tests pass on every commit to `main`.
- **Never commit** footage, generated media, model weights, API keys, or `.env`.
- **Attribution:** commits credit only `karthi-ai-engineer` (enforced; see [`CLAUDE.md`](../CLAUDE.md)).

### Target repo layout

Organized by kind of file, not by phase. Inner structure grows when a phase needs it.

```
scenefold/
├─ src/     Python code for the pipeline
├─ web/     viewer and UI
├─ tests/   tests
├─ docs/    brief, roadmap, design notes
└─ data/    videos and generated outputs, never committed
```

### Event workspace (the files each stage reads and writes)

```
data/<event_id>/
├─ originals/          input files, never modified (SHA-256 recorded in manifest)
├─ proxies/            normalized video + mono 48 kHz WAV per clip      Phase 1
├─ manifest.json       clips and their metadata                          Phase 1
├─ timeline.json       offsets, confidence, pair measurements            Phase 2
├─ cut.json, cut.mp4   shot list with a reason per shot                  Phases 4, 9
├─ observations/       one JSON per clip, clip-local time                Phase 5
├─ knowledge.sqlite    events, entities, evidence, conflicts             Phase 6
└─ story.json          cited narrative                                   Phase 7
```

---

## Phase 0: Foundation

**Goal:** a clean, tested skeleton that runs on any machine, checked automatically on every push.
**Runs on:** any laptop (CPU).

**Steps**
- [x] Git repo, remote, commit identity guard (`.githooks/`), `CLAUDE.md`, `.gitattributes`, Claude attribution off
- [x] Top-level folders: `src/`, `web/`, `tests/`, `docs/`, `data/`
- [x] `.gitignore` (keeps `data/` out of GitHub)
- [x] Brief and logo moved into `docs/`; `README.md` with install, quick start, and responsible use
- [x] License: Apache-2.0 (`LICENSE`, declared in `pyproject.toml`)
- [x] `uv` project on Python 3.13; ruff + pytest
- [x] GitHub Actions CI (`.github/workflows/ci.yml`): lint and all tests on Windows, macOS, and Ubuntu
- [x] Tests for the commit guard (`tests/test_commit_guard.py`)
- [x] CLI: `scenefold ingest` (`sync` and `view` arrive with their phases)
- [x] Data contract for `manifest` (Pydantic models in `src/scenefold/manifest.py`)

**Done when**
- A fresh clone + `uv sync` + `uv run pytest` passes locally and in CI on Windows, macOS, and Ubuntu.

**Decision: license — Apache-2.0** (decided 2026-09-17). Ultralytics YOLO and BoxMOT are AGPL-3.0 and would make
the whole repo AGPL, so Phase 8 plans a permissive vision stack (RF-DETR, supervision, torchreid; verify their
licenses then). As the sole author you can still switch to AGPL in Phase 8 if YOLO proves clearly better.

**Non-code task (any time before Phase 5): film the test event.** 3–5 consenting friends, 2–5 minutes,
people in clearly different clothes. At the start and end, one person claps visibly in view of every phone.
The claps give ground truth for sync error and clock drift.

---

## Phase 1: Ingest

**Goal:** any phone video goes in; consistent proxies, audio, and a manifest come out. Originals never change.
**Brief outcome:** 1 · **Runs on:** CPU

**Steps**
- Probe each file with ffprobe: duration, streams, variable frame rate, rotation, HDR, audio/video `start_time`,
  creation time (a rough prior for sync).
- Hash originals (SHA-256) for stable clip IDs; skip clips already processed.
- Normalize to proxies: H.264 720p, constant 30 fps, keyframe every 1 s, `+faststart`; apply rotation;
  tone-map iPhone HDR to SDR; keep only the first video and audio stream.
- Extract mono 48 kHz WAV in the **same FFmpeg pass** as the proxy, so both share one timeline
  (and the sound isn't compressed twice). Picture and sound are padded to start exactly at clip time 0.
- Write `manifest.json`. Flag problems (no audio, clipped audio, very short clip) instead of failing.

**Done when**
- Handles an iPhone HDR MOV, an Android variable-frame-rate MP4, a portrait video, a clip without audio, and an MKV.
- A test flash and click placed at a known time land at that time in the proxy and WAV, even when the
  audio or video track starts late (tests allow ±5 ms; measured about 0.1 ms).
- Re-running on the same inputs does no work.

**Result (2026-09-17)**
- `scenefold ingest <event> <files or folders>`; code in `src/scenefold/` (`ingest.py`, `media.py`, `manifest.py`, `cli.py`).
- 116 tests pass, covering every "Done when" item above plus WebM without duration, interlaced and
  non-square-pixel video, 5.1 audio, silent and clipped audio, half-downloaded and broken files,
  duplicates, moved files, changed settings, locked or corrupt workspaces, and non-video files.
- Speed on this laptop (i5-1334U, CPU only): a 10 s 4K HDR portrait clip plus a 30 s 1080p clip took 9.8 s.
- Not yet tried on real phone footage.

---

## Phase 2: Sync

**Goal:** every clip with usable audio sits on one master timeline with a confidence score; the rest are reported with a reason.
**Brief outcome:** 2 · **Runs on:** CPU

**Steps**
- Data contract for `timeline`. Rule: every time field is named `t_local` or `t_master`, never a bare `t`.
- Synthetic test generator: one source recording → N fake phone clips with known offsets, noise, echo,
  gain changes, clock drift, plus one unrelated clip. It saves the true offsets alongside the clips.
- Real test set: a small subset of the Jiku phone dataset with its sync ground truth (see research notes),
  plus a home recording from 2–3 phones with a clap at the start and end.
- Pair measurement: GCC-PHAT with soft whitening (β ≈ 0.8) for all clip pairs.
- Confidence per pair: peak strength vs. the next-best peak, plus agreement across ~10 s windows
  (the windows also estimate clock drift).
- Global solve: weighted least squares over all pairs, iterative outlier rejection, cycle-consistency
  check (A→B plus B→C must equal A→C).
- Report clips that can't be placed (no audio, no confident overlap) with the reason.
- Write `timeline.json`: offsets, confidence, every pair measurement, rejected pairs.
- Evaluation script: error vs. ground truth; compare against audalign and bbc/audio-offset-finder as baselines.

**Done when**
- Synthetic set: median error ≤ 10 ms, and the unrelated clip is rejected.
- Jiku subset: error measured against ground truth and reported (target: 95% of clips within one frame, 33 ms).
- Overlaps shorter than ~5 s come out low-confidence instead of wrongly placed.

**Progress (2026-09-17)**
- Done: `timeline.json` contract (`src/scenefold/timeline.py`); synthetic event audio (`tests/synth.py`);
  pair measurement with GCC-PHAT-β and a peak-vs-runner-up confidence (`audio_offset.py`); weighted
  least-squares solve that drops inconsistent pairs and places clips through other clips (`sync.py`);
  `scenefold sync <event>`.
- Measured on synthetic phones (10–30 dB noise, echo, different volumes): offset error about 0.02 ms;
  related pairs score 23–40+ confidence, unrelated audio 1.05–1.14 (threshold 2.0); a 5-minute pair takes 0.3 s.
- Left: windowed drift check, evaluation on the Jiku subset and the home recording, repetitive-music cases.

---

## Phase 3: Synced viewer + sync report → v0.1.0

**Goal:** all angles play together in the browser. First public demo.
**Brief outcome:** 3 · **Runs on:** CPU + browser

**Steps**
- Static web page loads `timeline.json` and proxies from a tiny local server.
- One master clock. Small drift is corrected by nudging `playbackRate`, large drift by seeking.
  Measure drift per frame with `requestVideoFrameCallback`.
- Coverage lanes: one per camera, in that camera's color, grey where it wasn't recording.
- Seeking: pause all → seek all → wait until every video reports `seeked` → play together.
- One audio track at a time, selectable.
- Sync report page: pair graph, offsets, confidence, rejected pairs.

**Done when**
- 4 clips at 720p play in sync on a laptop without a dedicated GPU, drift measured under one frame.
- Scrubbing and seeking never leave videos out of sync.
- README shows a GIF of the viewer; tag `v0.1.0`; first progress post.

---

## Phase 4: Quality cut (no AI)

**Goal:** a first automatic director's cut based on picture quality alone.
**Brief outcome:** 9 (basic) · **Runs on:** CPU

**Steps**
- Score each clip per second: sharpness, shake, exposure.
- Pick shots with dynamic programming: maximize quality, enforce a minimum shot length (~2–3 s),
  penalize each cut, avoid bouncing straight back to the same angle.
- Cut the picture only; keep continuous audio from the single best microphone.
- Write `cut.json` (shots plus a reason for each); render `cut.mp4` with FFmpeg; play it in the viewer.

**Done when**
- The cut renders end-to-end from `timeline.json`, every shot states a reason, no shot is shorter than
  the minimum, and the audio has no jumps.

---

## Phase 5: Clip understanding

**Goal:** each clip produces time-stamped observations: actions, sounds, speech, on-screen text, appearance.
**Brief outcome:** 4 · **Runs on:** cloud API (any laptop)

**Steps**
- Model-agnostic provider interface. First adapter: Gemini Flash tier (accepts video directly).
  Claude and OpenAI need sampled frames instead; local Qwen-VL needs the GPU machine.
- Observation schema via structured JSON output: clip ID, `t_local` start/end, type, description,
  subject description, confidence.
- Analyze each clip **independently**: independent witnesses make disagreements real evidence.
- Cache by (clip hash, model, prompt version); log tokens and cost per event.
- Optional face blurring before upload.
- Speech with word-level timing locally (faster-whisper or WhisperX).
- Timestamp refinement: snap the AI's second-level times to nearby audio onsets and motion peaks.

**Done when**
- Every test-event clip produces schema-valid observations; a re-run makes zero API calls.
- Event recall is measured on the hand-labeled test event; cost per event is reported.

**Decision: Gemini free vs. paid tier.** Free-tier content may be used by Google and read by human reviewers.
Recommendation: paid tier (well under 1 cent per 30 s clip), or only consenting footage on free tier.

---

## Phase 6: Event knowledge + conflicts

**Goal:** observations from all cameras merge into events on the master timeline; disagreements are found and explained.
**Brief outcomes:** 6, 8 · **Runs on:** CPU + API

**Steps**
- Convert observations to master time using `timeline.json`.
- Group observations into candidate events (time overlap, type, description similarity); an LLM confirms borderline merges.
- Knowledge store: SQLite (entities, observations, events, evidence, conflicts) behind a small repository layer.
- Conflict types: not in view, occluded, different interpretation, suspected model error, audio/visual delay.
- Resolve a conflict by which camera had the better view at that moment, not by majority vote. Otherwise mark it unresolved.

**Done when**
- Every event cites at least one observation; every conflict has a type and a resolution or "unresolved".
- Event recall and conflict precision are measured on the labeled test event.

---

## Phase 7: Story + Q&A → v0.5.0

**Goal:** a readable, cited account of the event, and questions answered from the event knowledge.
**Brief outcomes:** 7, 10 · **Runs on:** API

**Steps**
- Generate the narrative from events; every sentence cites event or observation IDs.
- Citation validator in code: the ID exists and the time falls inside that clip's coverage.
  Statements that fail are removed or flagged.
- Q&A: a small event fits in the model's context, so pass the knowledge directly; same validator on answers.
- UI: "What happened" panel, disagreement cards, ask box. Clicking a citation jumps the viewer to that clip and time.

**Done when**
- 100% of displayed statements pass the validator.
- Faithfulness is checked by hand on the test event (target: ≥ 90% supported).
- End-to-end demo works: drop in clips → synced viewer, story, disagreements. Tag `v0.5.0`.

---

## Phase 8: Cross-angle identity

**Goal:** the same person or object seen by different phones gets one shared identity.
**Brief outcome:** 5 · **Runs on:** NVIDIA GPU machine preferred; OpenVINO on CPU as fallback

**Steps**
- Detect and track per clip → tracklets (the Phase 0 license decision picks the libraries).
- Appearance embedding per tracklet (re-identification features averaged over the track).
- Cross-camera matching: appearance + time co-occurrence on the synced clock + motion correlation →
  similarity matrix → bipartite matching. "Unknown" is a valid result.
- The VLM double-checks ambiguous pairs and explains its choice.
- Re-run Phase 6 fusion with shared entities.

**Done when**
- Matching accuracy is measured on a hand-labeled set; low-confidence matches show as unknown, never forced.

**Time-box:** this is the research-heavy phase. If accuracy stalls, ship what works and document the limits.

---

## Phase 9: Smart cut + release → v1.0.0

**Goal:** the director's cut uses event knowledge, and the project is ready for other people.
**Brief outcome:** 9 · **Runs on:** CPU + API

**Steps**
- Cut scoring adds event importance, subject size and framing, and following identities through the action.
- Final UI pass: camera colors used consistently everywhere; event graph view.
- README: demo video/GIF, architecture diagram, metrics table (sync error, matching accuracy, cost per event),
  limitations, responsible use.

**Done when**
- A newcomer can do everything in brief §16 "Definition of Success". Tag `v1.0.0`.

---

## Research notes

Checked 2026-09-17. Versions, model names, and prices change quickly, so recheck at the start of each phase.

**Tooling**
- Python 3.13: Ultralytics lists support up to 3.13, and current numpy/scipy require ≥ 3.12.
- FFmpeg: developed with 9.0. The test clips use `-display_rotation` (added in FFmpeg 6.0) and `-fps_mode`
  (added in 5.1). Homebrew's `ffmpeg` has no libzimg (`zscale`), but the keg-only `ffmpeg-full` does;
  Ubuntu 24.04's apt package is 6.1. https://formulae.brew.sh/formula/ffmpeg-full
- uv 0.12.x; Windows install: `winget install --id=astral-sh.uv -e`.
  PyTorch CUDA wheels come from a separate index configured in `[tool.uv.sources]`.
  https://docs.astral.sh/uv/guides/integration/pytorch/

**Sync (Phases 1–2)**
- No existing tool combines all-pairs GCC-PHAT with a global least-squares solve; the closest published design is
  Kammerl et al., ICASSP 2014. https://research.google/pubs/temporal-synchronization-of-multiple-audio-signals/
- Baselines to compare against: audalign (MIT) https://github.com/benfmiller/audalign,
  bbc/audio-offset-finder (Apache-2.0) https://github.com/bbc/audio-offset-finder.
  Don't copy code from skellysync (AGPL).
- FFmpeg pitfalls:
  - Use `-fps_mode cfr`, which replaces `-vsync`.
  - Edit lists in MOV/MP4 are applied by default; keep that.
  - FFmpeg trims the AAC priming delay, so always decode audio through FFmpeg.
  - Rotation is stored as Display Matrix side data.
  - iPhone HDR is HEVC HLG with Dolby Vision 8.4; tone-map with `zscale` + `tonemap` on CPU, or `libplacebo` on GPU.
  - Docs: https://ffmpeg.org/ffmpeg.html
- Real test data: the Jiku Mobile Video Dataset (473 phone clips) is still downloadable
  https://traces.cs.umass.edu/docs/traces/multimedia/, with sample-accurate sync ground truth at
  https://github.com/protyposis/JikuMVD-SynchronizationGroundTruth

**Viewer (Phase 3)**
- `requestVideoFrameCallback` works in all major browsers since Oct 2024. https://caniuse.com/mdn-api_htmlvideoelement_requestvideoframecallback
- Master clock with `playbackRate` nudging: timingsrc (MIT) https://github.com/chrisguttandin/timingsrc, or ~100 lines of our own.
  Variable `playbackRate` is unreliable on iOS.

**Understanding (Phase 5)**
- Gemini video input:
  - Video goes inline under 100 MB, or through the Files API.
  - Frame rate (`fps`) and start/end offsets can be set per video.
  - Cost is ~70 tokens per frame at default resolution, plus 32 tokens per second of audio.
  - Timestamps are MM:SS only; JSON-schema output is supported.
  - Docs: https://ai.google.dev/gemini-api/docs/video-understanding
- Gemini pricing: the Flash tier (e.g. `gemini-3.8-flash`) costs under 1 cent per 30 s clip at 1 fps, but its promo price doubles on 2027-01-01.
  Free-tier data may be used by Google. https://ai.google.dev/gemini-api/docs/pricing
- Claude and OpenAI APIs accept images, not video: send sampled frames with timestamps.
- Local VLMs: Qwen3-VL and newer Qwen models (Apache-2.0). 8B-class models need ~7 GB at 4-bit, so use the GPU machine.
  https://github.com/QwenLM/Qwen3-VL
- Speech: faster-whisper 1.2.x, WhisperX 3.8.x (word timestamps, speaker diarization).
  Sound tagging: PANNs, BEATs, CLAP.

**Knowledge (Phase 6)**
- Kùzu was archived in Oct 2025; its successor LadybugDB is still maturing. DuckDB's graph extension (DuckPGQ) is incomplete.
  SQLite is enough for V1.

**Identity (Phase 8)**
- YOLO26 is the current Ultralytics model (AGPL-3.0). https://docs.ultralytics.com/models/
- BoxMOT v25 (AGPL-3.0) has many trackers and re-ID weights (OSNet, CLIP-ReID) but no cross-camera module.
  https://github.com/mikel-brostrom/boxmot
- RF-DETR base models are Apache-2.0. https://github.com/roboflow/rf-detr
- SAM 3 needs a CUDA GPU.
- Cross-camera recipe from recent papers: tracklets per camera → averaged appearance features → time constraints →
  motion/pose correlation → bipartite matching. https://arxiv.org/html/2605.09245

**Director's cut (Phases 4, 9)**
- Useful signals: sharpness, shake, exposure, occlusion, subject size and framing, event relevance, audio.
- Shot selection by dynamic programming with a minimum shot length and a cut penalty, as in
  EditIQ (IUI 2025) https://arxiv.org/html/2502.02172v1 and DIRECT (2026) https://arxiv.org/abs/2604.04875
