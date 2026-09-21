# Scenefold Roadmap

We build Scenefold one phase at a time. Each phase ends with something that runs, is tested, and is measured.
The vision and firm principles live in [`PROJECT_BRIEF.md`](PROJECT_BRIEF.md). This file covers **what to build next** and **when a phase counts as done**.

Last updated: 2026-09-21

## Status

| Phase | Name | Brief outcomes | Rough size (3 h sessions) | Status |
|---|---|---|---|---|
| 0 | Foundation | — | 1 | Done |
| 1 | Ingest | 1 | 1 | Done; checked on 12 real phone clips (sound now follows the file timestamps) |
| 2 | Sync | 2 | 1–2 | Done: drift-aware sync, measured on real Jiku clips, ahead of two baselines |
| 2b | Place the pictures, not the sound arrival | 2 | ~1 | Done: measured in sync, and the viewer can hold the pictures together |
| 3 | Synced viewer → **v0.1.0** | 3 | 1–2 | Done: viewer within half a frame, checked on the pictures too, demo event and README GIF |
| 4 | Quality cut (no AI) | 9 (basic) | 1 | Done: `scenefold cut` scores, chooses shots, renders the film, and the viewer plays it |
| 5 | Clip understanding | 4 | 2 | Done but for recall: watches, listens, and times the moments, all on this computer |
| 6 | Event knowledge + conflicts | 6, 8 | 2 | Done: clips merged into events with evidence, and disagreements found twice over |
| 7 | Story + Q&A → **v0.5.0** | 7, 10 | 1–2 | Built: cited story and questions, every sentence checked in code. Tag held back until faithfulness is checked by hand |
| 8 | Cross-angle identity | 5 | 2–3 | Built and merged: matching from what the clips say people are wearing, no new dependency. Accuracy on real footage waits on a hand-labelled event |
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
- Real phone footage (12 Jiku clips, 2026-09-17): all ok; working copies of 1080p clips take ~7–12 s
  each on the Predator. Fixed 2026-09-19: sound now follows the file's timestamps like the picture
  (some phones' audio sample counts disagree with their timestamps; see Phase 2 findings).

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
- Confidence per pair: peak strength vs. the next-best peak. ~10 s windows measure clock drift
  (as a confidence check they fail on music, see Progress).
- Global solve: weighted least squares over all pairs, iterative outlier rejection, cycle-consistency
  check (A→B plus B→C must equal A→C).
- Report clips that can't be placed (no audio, no confident overlap) with the reason.
- Write `timeline.json`: offsets, confidence, every pair measurement, rejected pairs.
- Evaluation script: error vs. ground truth; compare against audalign and bbc/audio-offset-finder as baselines.

**Done when**
- Synthetic set: median error ≤ 10 ms, and the unrelated clip is rejected.
- Jiku subset: error measured against ground truth and reported (target: 95% of clip pairs within one
  frame, 33 ms; pairs, because a clip's error only exists relative to another clip).
- Overlaps shorter than ~5 s come out low-confidence instead of wrongly placed.

**Progress (2026-09-17)**
- Done:
  - `timeline.json` contract with offset and clock drift per clip (`timeline.py`).
  - Synthetic event audio with noise, echo, volume, clock drift, and looped music (`tests/synth.py`).
  - Pair measurement (`audio_offset.py`): GCC-PHAT-β with a peak-vs-runner-up confidence. Drift comes
    from the lag in 10 s windows (a Theil–Sen line); B is stretched to cancel it and matched again.
    When strong drift hides the whole-clip match, one-minute pieces find it.
  - Solver (`sync.py`): weighted least squares for offsets and per-clip drift (against the average
    clock). Each pair is judged against the solve without it, so a confident wrong pair can't win.
  - `scenefold sync`, `scenefold evaluate` with ground-truth moments (`evaluate.py`), `tools/jiku.py`.
- Synthetic: offsets within 0.2 ms with drift up to 460 ppm and overlaps up to 20 min; unrelated audio
  scores 1.0–1.1 (threshold 2.0). Without drift handling, 10 min at 100 ppm had fallen to 1.9.
- Jiku, event SAF_290512: two subsets of 6 clips from 3 phone models (Galaxy S II, Galaxy Nexus,
  Nexus S). Pair errors at moments every 10 s against the published ground truth, with working copies
  whose sound follows the file timestamps (2026-09-19; the ground truth's sample-counted times are
  moved onto each file's timestamps by `tools/jiku.py`):

  | Subset | Placed | Sync time | All pairs: median / p95 / worst | Within 33 ms | Without the Nexus S |
  |---|---|---|---|---|---|
  | `jiku-saf`, ~80 s overlap | 6 of 6 | ~4 s | 12.0 / 99.9 / 110.7 ms | 68% | 8.5 / 21.7 / 25.5 ms, 100% |
  | `jiku-saf-long`, ~174 s overlap | 6 of 6 | ~7 s | 3.9 / 78.1 / 85.3 ms | 68% | 2.1 / 5.7 / 6.4 ms, 100% |

  (Before the ingest fix, with sample-counted sound: 8.7 / 20.7 / 25.8 and 2.5 / 4.1 / 4.3 ms.)
- Baselines on the same working WAVs, scored like `scenefold evaluate` (`tools/baselines.py`, Python
  3.12 via uv): each method predicts one clip's time of a ground-truth moment from the other's.
  Without the Nexus S, median / worst in ms, and moments within one frame:

  | Method | `jiku-saf` (98 errors) | `jiku-saf-long` (187 errors) |
  |---|---|---|
  | Scenefold, solved timeline | 8.5 / 25.5, all | 2.1 / 6.4, all |
  | Scenefold, raw pair measurement | 8.8 / 25.9, all | 2.2 / 15.3, all |
  | audio-offset-finder 0.5.5 (MFCC, 16 ms steps, no drift) | 10.3 / 61.7, 90 | 6.4 / 30.4, all |
  | audalign 1.3.1 (correlation, no drift; `fine_align` adds nothing) | 10.5 / 24.0, all | 14.6 / 47.9, 174 |

  No method made a wrong match (> 1 s). Over 80 s the methods are close; over ~3 minutes the lack of
  drift handling shows. On the Nexus S, both baselines land 68–92 ms (median) from the ground truth,
  like Scenefold: that proves the matching on our WAVs, not their time base, so it stays open.
- Findings from real clips:
  - **Nexus S disagrees with the ground truth by 70–110 ms** (16 kHz AAC, edit lists), in both
    subsets, while its pairs agree with each other. Every other phone agrees within a frame. Its audio
    timestamps match its samples exactly, so it is not the ingest timing issue below; the cause is
    in one side's decoding; unresolved. A moment both seen and heard by it and another phone settles it.
  - **Ingest audio timing (Phase 1), fixed 2026-09-19:** measured frame by frame, Galaxy S II audio
    holds 314.5 ppm more samples than its timestamps say (smoothly, 0.4 ms jitter), and Galaxy Nexus
    timestamps jump 14–19 ms after the first audio frame. With `aresample=async=1` the WAV followed
    the samples, so sound drifted up to 63 ms from its own picture (and would jump 100 ms after ~5
    min). Now `async=1000` (`ProxySettings.audio_max_stretch`) stretches the sound to follow its
    timestamps, as the picture does; tested with generated files that mimic both phones. Measured
    drift now describes each file's timestamp clock (Galaxy S II ≈ −20 ppm against the average,
    was +136). The ground truth counts samples, so `tools/jiku.py` maps it onto each file's timestamps.
  - **Moving phones:** the lag between two Galaxy Nexus clips stepped by ~15 ms within a minute (a
    phone moving ~5 m, or lost audio). A straight drift line can't follow that (errors up to 26 ms).
    Sound-based sync is limited by path changes: about 3 ms per metre.
  - Checking whether windows agree does not work as a confidence test for music (beats repeat inside
    the search range), so windows only measure drift.
  - 11 of 12 Jiku clips were flagged for clipped audio (0.5–5.4% of samples): the flag may be too eager.
- Known limit: identical repeated sound (a recorded chorus played twice) shared by only two clips
  matches the wrong place, and a clip that heard both repeats makes the true pairs ambiguous
  (strict xfail test). Fixing it needs a solver that weighs several candidate lags per pair.
- **Same song, different nights (YouTube test, 2026-09-19):** 14 fan clips of Coldplay's "Fix You"
  from both Ahmedabad shows (25 and 26 Jan 2025) all landed on one clock. Stadium bands play to
  backing tracks that are identical every night, so clips from different nights match on the music
  (confidence 1.0–2.4) about as well as same-night clips through a stadium crowd (1.7–7.5), and the
  placements are self-consistent (same song structure). What does separate them: searching each
  10 s window ±1 s, same-night pairs agree with the pair's lag in 85–100% of windows, different
  nights in at most 62%. Grouping by that gave two clean sets (6 clips on the 25th, 5 on the 26th)
  that each sync completely. YouTube uploads may also be edited (cuts), which breaks "one clip,
  one offset".
- **Partial matches set aside (2026-09-20):** every pair now also gets `windows` and `agreement`
  (each 10 s window searched ±1 s, agreeing within 30 ms; up to 24 windows). A pair with at least
  2 windows and agreement under 0.9 is set aside as `partial_match`, however confident: across
  ~100 labelled real pairs (TOL 30 ms), true pairs scored 1.00 almost always (lowest 0.75), nights
  apart 0.50–0.87, and a synthetic other-night pair reached confidence 4.9. Short clips are judged
  too: a 32 s clip (1 of 3 windows agreeing) had bridged the two nights. Result: the mixed 14 clips
  now split (the 26th placed, the 25th reported as a separate group), both single-night events and
  both Jiku subsets place fully with unchanged errors. Unplaced clips that matched each other say
  so ("matches other clips, but not the main group").
- Done 2026-09-19: every "Done when" item is met. The home clap recording and the Nexus S check test
  picture sync, which the Phase 3 viewer makes easy to see, so they moved there.

### Phase 2b: place the pictures, not the sound arrival (2026-09-20, after v0.1.0)

The Phase 3 picture check found phones at one stadium show sitting up to 423 ms apart in when they
heard the event, so sound-based offsets put their pictures that far out (below, Phase 3). That
measuring is now part of sync itself, in `src/scenefold/picture_offset.py`:

- Every placed pair is matched again on its brightness curve, searching ±2 s around where the sound
  put it, with the pair's measured clock drift cancelled exactly as the sound does it.
- A match counts only when it stands 4 standard deviations above lags more than 5 s away **and**
  leads the best lag anywhere else by 0.6 of those standard deviations. Scores divide by the square
  root of the overlap, so every lag is judged on the same scale however long the two clips share.
- The second bar was added on 2026-09-21 after CI failed on Windows: a steadily lit room, whose
  clips hold no signal at all, produced a "clear" match. The cause is that anything repeating
  matches at every repeat — video encoding marks every keyframe, one second apart — and a repeat
  can win the local search by luck. Measured: a repeating pattern alone leads by at most 0.50
  standard deviations, while real matches lead by 0.84-2.71 (drawn clips) and 0.60-1.97 (two
  stadium concerts), so the bar sits at 0.6. It drops the weakest pairs and leaves the answers
  unchanged: 0/47/54/97/268 ms on the 26th (was 0/44/50/96/270).
- The clear differences are solved into one delay per clip, dropping any that disagrees with the
  rest by more than 60 ms, exactly as the sound solver does. `timeline.json` gains `heard_late_s`
  per clip and the picture measurement on every pair (`schema_version` 2).
- `scenefold sync` prints it as a distance; `--sound-only` skips the pass, which has to read every
  picture. The viewer's "Line up" control holds the pictures together (the default when any clip
  knows its distance) or the sound, and the sync report shows the distances and every picture
  match.

**Measured.** On drawn clips whose true distances are known, the delays come back within a frame,
and where the truth is zero it reads within 17 ms (half a frame). On the two Coldplay nights it
reproduces what the standalone check had found: 0/46/52/97/273 ms on the 26th and up to 420 ms on
the 25th. On both Jiku subsets it says it cannot tell, because their lighting is steady — the
honest answer, and the reason it can never be the only way clips are placed.

`tools/check_pictures.py`, which found the effect, was deleted: sync now measures it, so keeping a
second implementation would only let the two drift apart.

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
- Visual sync checks (carried from Phase 2): match the clips by their pictures alone and compare
  with where sound put them, including `jiku-saf-long`, to settle the Nexus S's 70–110 ms
  disagreement with the published ground truth.

**Done when**
- 4 clips at 720p play in sync, drift measured under one frame.
  (Dropped 2026-09-20: "on a laptop without a dedicated GPU". The borrowed i5 laptop is gone and
  Karthi has only the Predator; the viewer decodes 720p in software, so this was never a GPU test.)
- Scrubbing and seeking never leave videos out of sync.
- The pictures are checked against the sound, not only the sound against itself.
- README shows a GIF of the viewer; tag `v0.1.0`; first progress post.

**Progress (2026-09-19)**
- Done: `scenefold view` (local server with Range support, `view.py`), the viewer in `web/` (tiles,
  coverage lanes, frame stepping, slow motion, sound picker, sync health, sync report), its timing
  rules in `web/sync.js` with Node tests in CI, and `tools/check_viewer.py` (headless Chrome).
- Measured with `tools/check_viewer.py` in headless Chrome on the Predator (6 real clips at 720p per
  Jiku subset, ~15 s from 3 points each, then 8 random jumps):

  | Check | `jiku-saf` | `jiku-saf-long` |
  |---|---|---|
  | Playback, every clip: mean / p95 / worst error | 3–4 / 5–12 / ≤ 19 ms | 3–4 / 5–10 / ≤ 13 ms |
  | Jumps while paused: furthest video | 0.00 ms | 0.00 ms |
  | Jumps while playing, 1–3 s after: worst p95 | ≤ 17 ms | ≤ 19 ms |

- What it took (measured in Chrome, each worth knowing for any multi-video player):
  - Chrome plays any rate within 0.1% of 1.0 as normal speed, and stalls a video for about a frame
    each time its rate enters that band: flipping 1.001 ↔ 1.06 every frame played at 0.87×, while
    1.002 ↔ 1.06 played at 1.03×. So silent videos never sit at their own rate: on time they trim
    0.3% toward the clock, and corrections are at least 1%. The clip being heard keeps its rate.
  - A jump while playing stalls the picture while Chrome decodes from the previous keyframe; aiming
    ahead by each clip's own measured jump time lands it within a frame.
  - Sound starts later than pictures (up to 0.7 s in headless Chrome), and `currentTime` of the
    heard clip jumps once its output settles. The clock follows the heard clip's shown frames
    instead, and the pictures start only once its sound is moving.
  - A clip that starts recording mid-playback waits on its first frame and is started a learned
    moment early (a paused video needs ~60 ms to get going).
**Picture check (2026-09-20): sync is right, and the sound arrives late from far away**

The picture check (then `tools/check_pictures.py`, now part of sync) never listens. It reads how
bright each working copy is frame by frame
(stage lighting, flashes), removes slow changes, and matches those curves for every placed pair,
searching ±2 s around where sound put them. A match counts when its peak stands 4 standard
deviations above lags more than 5 s away, where nothing should line up.

On the Coldplay sets every pair matched clearly (4.6–7.4σ), but the pictures did not sit where the
sound put them: up to 269 ms out on Jan 26 and 423 ms on Jan 25, always in one direction per clip.
Giving each clip a single delay explains every pair to within 15 ms (Jan 26, median 9 ms) and 50 ms
(Jan 25, median 11 ms). So the picture matching itself is accurate to about a third of a frame —
the timeline, drift, and viewer chain is right — and the spread is one number per phone:

| Jan 26 | delay | Jan 25 | delay |
|---|---|---|---|
| Gareth Sequeira | 0 ms | Figments of Imagination | 0 ms |
| Jyoti Malik | +38 ms | Sayan Santra | +195 ms |
| Dharm Bharodiya | +49 ms | Sachin Jacob | +204 ms |
| Adrit Girish | +94 ms | sam | +309 ms |
| Somnath Das | +264 ms | Promit Dey | +338 ms |
| | | nishant parekh | +422 ms |

That is the speed of sound: 2.9 ms per metre, so these phones stood 0–91 m and 0–145 m apart in
their distance from the speakers — ordinary for a stadium holding 130,000 people. **Sound-based sync
lines up when each phone *heard* the event, not when it happened.** Any phone further from the
speakers has its picture placed ahead of the others by its distance, up to 12 frames here. (Delay towers and an
uploader's own audio shift would look the same; the per-clip fit only shows it is one number per
clip.) Aligning pictures instead is the first item after Phase 3.

Jiku cannot be checked this way: its lighting is steady, so no pair reached 3σ and the best lags
scattered by ±2 s. The Nexus S question stays open.

**Demo event and the README picture (2026-09-20).** Real footage shows real people, so the public
picture could not come from Jiku or YouTube. `tools/demo_event.py` draws a show with FFmpeg — lit
stage, three figures, a crowd, a clock in the picture, and restless lighting — and films it with
four imaginary phones, each with its own window of the show, framing, brightness, microphone noise,
echo, and clock drift. `tools/record_viewer.py` then photographs the viewer playing it in headless
Chrome and turns that into `docs/viewer.gif` (2 MB). On this event:

- sync places all four within 1 ms of their true offsets, and measures drift within ~25 ppm;
- `tools/check_viewer.py` passes: every picture within a frame, jumps included (the Done-when);
- the picture check gets all six pairs clearly (4.7–5.3σ) and puts the pictures within
  12 ms of the sound, with fitted distances of 0–3 m. Nothing travels in a drawn show, so zero is
  the right answer: the picture check is right where the truth is known.

Two things the demo taught about what brightness matching needs: lighting on a regular beat matches
at every beat, and single-frame flicker does not survive re-encoding. Its lighting is therefore
fourteen pulses at unrelated speeds (0.4–5 Hz), which is also what makes real stage lighting work.

**Done 2026-09-20.** Every "Done when" item is met: four 720p clips play within a frame (measured by
`tools/check_viewer.py` on the demo event and both Jiku subsets), jumps land in place, the pictures
were checked against the sound, and the README shows the viewer. Tagged `v0.1.0`.

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

**Progress (2026-09-21)**
- `scenefold cut <event>` (`--plan-only` to skip rendering), code in `src/scenefold/quality.py`
  (what each second of each clip looks like) and `src/scenefold/cut.py` (the shots, `cut.json`,
  `cut.mp4`). 30 tests.
- **Scoring**, on small grey frames ten a second: *sharpness* (how much detail a frame holds),
  *steadiness* (how far the whole picture shifts between frames, found by lining each frame up with
  the one before it, so a subject crossing a steady frame costs nothing and only the camera moving
  does) and *exposure* (crushed black, blown white, and distance from mid-grey). Each is stretched
  over the range the event itself shows, so "good" means better than the other angles here.
- **Shots** come from one pass of dynamic programming over the whole film: pieces of 3-12 seconds,
  each angle available only where it was really recording, a cut costing 0.35 and a cut straight
  back to the angle before last costing 0.25 more. A piece may also hold the angle it already had,
  costing nothing and merged back into one shot; without that, a stretch filmed by one phone alone
  could not be covered at all, since there is no second angle to cut to.
- **Sound** comes from one clip, unbroken: the longest, and of equally long ones the one nearest to
  the sound (`heard_late_s`), because its sound fits pictures of the event most closely. The film
  runs exactly as long as that clip, on that clip's own clock; every shot carries the `speed` that
  puts it on that clock, which keeps picture and sound together over a long film (300 ppm would
  otherwise drift 90 ms in five minutes).
- On `coldplay-jan26` (5 clips, 5:08 of film) it chose 13 shots from 3 to 63 seconds with varied
  reasons, taking 51 s to look at 20 minutes of footage. On the drawn demo event the clock burnt
  into the picture reads 30.467 s at 30 s into the film: the shots land on the right frames.
- **Watching it:** the viewer has a third tab, Film, which plays `cut.mp4` with the shot list
  beside it, highlighting the shot on screen and why it was chosen; clicking a shot jumps there.
  `view.py` serves `/api/cut` and `/film.mp4` (the same Range support as the clips). The shot list
  is fetched at start-up and the film itself only when the tab is opened: a browser allows about
  six connections to one site and the clips hold them all while they load, which had left the tab
  waiting 15 s on a six-clip event.
- Left for this phase: nothing in the "Done when" list. Remaining ideas, not required: a cut that
  can use footage outside the microphone clip's span, and shot lengths that follow the music.

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

**Progress (2026-09-21)**
- `scenefold observe <event>` writes `observations/<clip_id>.json` per clip, in clip-local time:
  `observations.py` is the contract, `observe.py` does the watching, 12 tests run without a model.
- Measured on `coldplay-jan26` (5 clips, ~20 minutes of footage, 12 s windows): 91 observations in
  338 s of model time, about one second of model time per 3.5 seconds of video. A second run asks
  nothing. The descriptions follow change across a window ("the stage lighting shifts from blue to
  green while the view zooms in"), which is why a window is four frames rather than one.
- **Speech (2026-09-21)**: `speech.py` runs Whisper through faster-whisper, an optional install
  (`uv sync --extra speech`), and keeps every word's own start and end. It tries the graphics card,
  proves it on a moment of silence, and falls back to the processor — this machine has no cuBLAS,
  so it uses the processor and says so. Voice detection is on by default because Whisper writes
  invented lyrics over music: on twenty minutes of concert audio it kept one two-word line (45 s of
  listening), and on a spoken test clip made with the computer's own voice it caught every word
  with times. Watching and listening cache apart, so adding one never re-runs the other.
- **Moments (2026-09-21)**: `moments.py` finds what can be timed exactly — growth in the sound
  (spectral flux peaks standing 4 robust deviations above the usual) and sudden change in the
  picture (the brightness curve sync already uses). Strength is relative to the clip, so a loud
  event does not raise the bar for a quiet recording. Each observation is then pulled onto the
  strongest moment inside its window, and each spoken line onto the sound that starts it, within
  0.25 s. Measured: claps found within 50 ms of where they were placed, drawn flashes within
  150 ms; on `coldplay-jan26`, 77 of 91 windows gained an exact time (243-444 moments a clip).
  Where nothing stands out, the coarse time stays: the one spoken line on that event was left
  where Whisper put it, because no onset was near it.
- Still to do here: recall against a hand-labelled event, which needs the test event filmed with
  friends. Worth knowing: the moment inside a window is the most prominent thing that happened
  while the model was looking, which is not always the thing it chose to describe.

**Decision (2026-09-21): a model on this computer, not a cloud one.** Karthi already had Ollama
with `qwen3.5:4b` — 4.7B parameters, vision, Apache-2.0, quantised to about 3.4 GB. It answers in
about 3 seconds for a window of four frames on the Predator. That settles the Gemini free-vs-paid
question by not asking it: no footage leaves the machine, nothing costs anything, and the licence
fits this repo. Most of the test footage is of people who agreed to be filmed by a friend, not to
be uploaded to anyone's API. A cloud adapter can come later for consenting footage; the provider
interface is one small class (`observe.Watcher`).

**What a 4B model is good and bad at, measured on real concert frames.** Good: saying what is in
front of it ("a wide shot of a crowd holding up glowing lights", "a hand waving in the
foreground"), and it reads on-screen text (it read the drawn demo's burnt-in clock exactly).
Bad: counting (it answered "5,000 people" to anything crowded) and judging itself (it answered
confidence 1.0 every single time). So it is never asked to count, and how far to trust a window
comes from the picture score (`quality.py`), not from the model's opinion of itself.

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

**Progress (2026-09-21)**
- `scenefold fuse <event>` (`fuse.py`, `knowledge.py`). Every clip's timed moments go onto the
  shared clock — picture-aligned, so a phone at the back is not placed 400 ms early — and moments
  landing within 0.15 s of each other become one event with several witnesses. A clip only ever
  contributes its strongest moment to an event, so one phone shaking twice is not two witnesses.
- Each event carries evidence per clip (its own second, what it was showing, how good its picture
  was) and the account of the best-placed clip as its summary. Everything is written to
  `knowledge.sqlite` in one transaction, replacing the last picture rather than adding to it.
- **Corroboration measured.** On the drawn demo, where every clip films the same show, 219 of 298
  moments were caught by more than one clip (73%); on `coldplay-jan26`, where phones point
  different ways, 118 of 311 (38%). The difference is what it should be.
- **Conflicts.** A conflict is raised only when two clips agree something happened and a third,
  whose picture was at least as good as the weakest witness's, caught nothing. That rule matters:
  without it, 307 of 311 concert events were "conflicts" — noise. With it, 69, of which 7 are
  resolved by the better view, 44 are left unresolved because no camera was clearly better, and 18
  because the camera with the best view is the one that missed it, which is the case where the
  event may not have happened at all. Resolution is never by majority.
- **Reading the accounts (2026-09-21)**: `judge.py` shows the model on this computer two
  descriptions of one moment and asks whether both could be true at that instant. Wording, detail
  and focus differ innocently; what cannot both hold (one performer against four, an empty dark
  stage against a lit one with a band) is a conflict, typed as a different interpretation, an
  occlusion, a camera pointed elsewhere, or a suspected model error. An unclear answer is read as
  agreement, because calling a disagreement puts a moment in front of a person.
- The first prompt was far too forgiving: told that wording differences do not matter, the model
  accepted "a performer stands alone at a piano" alongside "four band members play together". With
  a rubric naming what cannot both be true, it reads 6 of 6 calibration pairs the way a person
  would; that calibration is a test, skipped when no model is running.
- On `coldplay-jan26` it read 54 distinct pairs (deduplicated from 311 events — the same
  twelve-second description covers many moments) and found no contradictions, which is the honest
  answer for five phones pointed at one stage. The 91 conflicts there are all the structural kind.
- Still to do here: recall and precision against the hand-labelled event.

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

**Progress (2026-09-21)**
- `scenefold story <event>` (`story.py`) writes the account from `knowledge.sqlite` and nothing
  else. The model gets a numbered list of moments — when, what the clearest camera showed, how many
  clips caught it, how they disagreed — and writes one sentence each, ending with the number.
- **The citations are checked in code**: the moment must exist and a clip that saw it must have
  been filming then. A sentence that fails is dropped and the reason kept in `story.json`, so what
  was thrown away stays visible. On `coldplay-jan26`: 15 sentences kept, none dropped, 4 marked as
  moments the clips disagreed about, every line naming the clips behind it.
- The moments told are spread across the event rather than taken strongest-first, which would
  bunch the account around the noisiest minute.
- Two things learned: a citation written after the full stop ("the lights drop. [1]") splits off
  and would credit the next sentence with the previous one's footage, so a mark at the start of a
  fragment is carried back; and two moments sharing one twelve-second description make the model
  write the same sentence twice, so identical neighbours are merged into one line with both
  citations.
- **Questions (2026-09-21)**: `scenefold ask <event> "<question>"` answers from the same store
  with the same checking. The moments handed over are the ones whose words the question shares;
  where nothing matches, the account's own spread goes instead, so "what happened?" still reaches
  the whole event. An answer the footage cannot support is dropped exactly as a story sentence is,
  and "The footage does not show this." is passed on unchanged rather than treated as a failure.
  On the concert it answered a question about the crowd's lights with a cited, disputed moment,
  and refused "was anybody hurt in the crowd?" outright.
- Left in this phase: the panels in the viewer, faithfulness checked by hand, and the tag.

---

## Phase 8: Cross-angle identity

**Goal:** the same person or object seen by different phones gets one shared identity.
**Brief outcome:** 5 · **Runs on:** NVIDIA GPU machine preferred; OpenVINO on CPU as fallback

**Steps**
- ~~Detect and track per clip~~ → the model already on this machine is asked who it can see, once
  every few seconds (`scenefold people`). Sightings are clothing and position only: never faces,
  never names, never anything measured off a body.
- Tracklets per clip, by matching each window's sightings to the people already being followed.
- Cross-camera matching: how alike the descriptions are + time co-occurrence on the synced clock →
  similarity matrix → bipartite matching (`scipy.linear_sum_assignment`). "Unknown" is a valid result.
- *(tried, dropped)* The VLM double-checks ambiguous pairs and explains its choice. Across three
  framings of the question the model swung from never rejecting a pair to rejecting nearly all of
  them. It names the clashing garment reliably — "purple vs brown", "red top vs white shirt" — but
  cannot turn that into a verdict, and a referee that unreliable makes the decisions worse. If it
  is tried again: ask only for the clash, decide in code, and measure on far more than ten pairs.
- *(held back on purpose)* Re-run Phase 6 fusion with shared entities. The machinery would work
  today, but it would carry identities of unmeasured accuracy into the event store and from there
  into the story, where every sentence is supposed to be checkable. "Person-03 was there" is a
  claim the footage cannot yet back. This waits on the hand-labelled event, and that is the whole
  reason for the wait — not effort.

**Why words before embeddings.** Before building anything, the local model was asked to describe
people in the footage we have. On the Jiku stage clips it gave genuinely separating descriptions —
"black sleeveless top and dark pants", "red sleeveless top" — and two phones filming the same
second independently produced the same two, which is the whole signal this phase needs. On a wide
Coldplay crowd shot it managed "dark clothing", which separates nobody. So the first version reads
clothing out of words: no detector weights, no licence question, nothing to download, and it fails
visibly rather than quietly where people are too small to describe. A detector plus a
re-identification embedding is the better instrument and stays the plan; this is the version that
could be measured this week.

**Measured on six angles of the Jiku stage footage** (2026-09-21, `qwen3.5:4b`, 20 minutes of
footage, about 13 minutes of asking):

| | |
| --- | --- |
| Sightings the model gave | 465 over 6 clips |
| Of those, describing somebody | 425 (the rest were "a person", "dark clothing") |
| Tracklets after joining within each clip | 80 |
| People after matching across clips | 26 |
| Caught by more than one phone | 16 |
| Of those, beyond doubt | 7 |
| People split inside one clip | 0 — the one outfit seen twice was two people on screen together |

Raising the bar from 0.30 to 0.80 moves the count smoothly (21 → 81 people), so it does not sit on
a knife edge. Above about 0.6 a clip's own view of one person starts fragmenting, which inflates
"caught by more than one phone" with pieces of the same person; 0.45 is where the count of people
best matches what is in the footage.

**Done when**
- Matching accuracy is measured on a hand-labeled set; low-confidence matches show as unknown, never forced.
  *Half met: the matching maths is measured on hand-written pairs (`tests/test_identity.py`), and
  unmatched and unsure are both reported. What is **not** measured is whether each match is the
  right person. A spot check of four angles at one moment confirmed the sync and the machinery, and
  also showed the weak point plainly: the model described people on stage well ("black sleeveless
  top and dark jeans", "brown hat"), and described others in ways no frame supported. A match
  between two consistent mistakes still matches. Only an event where who is who is known can
  separate the two, which is the same hand-labelled event Phases 5 and 6 are waiting on.*

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
  - Files one by one: `https://skulddata.cs.umass.edu/traces/mmsys/2013/jiku/dataset/<name>.mp4`
    (no archive). Five events with ground truth, 50–143 clips each, 6 s to 40 min, ~130 GB in total.
    CC BY 4.0 (the repository's default); cite Saini et al., MMSys 2013. Real people: keep it local.
  - Ground truth: one XML per event. Each recording has `offset` (a .NET TimeSpan from the event's
    earliest recording) and `speed`; a moment at clip time t sits at `offset + speed * t`, so
    `drift_ppm = (1/speed - 1) * 1e6`. Only clips linked by hand-set syncpoints are reliable.
    The repository has no license: download it at run time, never commit it.
- Clock drift is large on some phones (Guggenberger et al., MMM 2015, 16 devices, 90-minute recordings):
  - Audio: Galaxy S II 274 ppm (16 ms/min), iPod touch 4G 417 ppm; most others −15 to +17 ppm.
    Same model varies by ~1.5 ppm; temperature adds up to ~10 ppm. Newer phones (Pixel 6a/7,
    LibriWASN 2023) measured about 14–17 ppm.
  - Drift settles into a straight line after ~10 min of warm-up.
  - Video can drift differently from audio (Acer Iconia: audio +13, video −554 ppm).
  - https://protyposis.net/files/mmm2015-timedrift-cameraready.pdf
- Baselines on Python 3.13 (checked 2026-09-17): both pin old numpy and fail a plain install.
  - audalign 1.3.1 (MIT): install with `--no-deps`, then numpy, scipy, pydub, `audioop-lts`; Python API
    `align_files(...)`; turn off multiprocessing on Windows. Its confidence is relative only.
  - audio-offset-finder 0.5.5 (Apache-2.0): `--no-deps` works with numpy 2. CLI
    `audio-offset-finder --find-offset-of B --within A --json`. MFCC correlation, 16 ms resolution,
    compares only the first ~32 s of one file, no drift handling.
  - Simplest: run both outside the project on Python 3.12, where their pins install cleanly:
    `uv run --no-project --python 3.12 --with audalign --with audio-offset-finder python tools/baselines.py <events>`.

**Viewer (Phase 3)**
- `requestVideoFrameCallback` works in all major browsers since Oct 2024. https://caniuse.com/mdn-api_htmlvideoelement_requestvideoframecallback
- Playwright's bundled Chromium has no H.264; `channel="chrome"` drives the installed Google Chrome.
- Chrome allows 6 connections per host: with more than 6 clips, `preload="auto"` may queue loads.
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
