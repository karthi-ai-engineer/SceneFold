# Handoff: read this first

This file carries the project's working memory between machines and chat sessions. Chat history does
not travel; this file does. **A new session should read this file, then `CLAUDE.md`, then
`docs/ROADMAP.md`, before doing anything.** At the end of every session, add an entry to the
session log at the bottom, commit, and push.

---

## How we work together

- **Roles:** Karthi (GitHub `karthi-ai-engineer`) owns the project. Claude works as a coworker:
  software developer, AI engineer, video-processing and system-design expert.
- **Decide, don't offer menus.** For technical choices and routine repo steps inside the agreed plan,
  pick the best option, do it, and explain briefly why. Karthi said: "don't give me suggestions to
  choose from, you are the coworker" and "do whatever is good; come with the optimal solution".
- **When Karthi says "don't code right now", don't.** Explain the plan first in simple, short terms.
  Karthi often asks "explain what this does" after a step: answer briefly, no jargon.
- **Keep it simple and practical.** Not over-detailed (leaves no room for research), not vague.
- **Parallel agents are welcome** for independent work (Karthi asked for two agents in parallel).
  Give each agent strict file ownership; the main session does all git steps.
- **Work until it's verified:** edge cases, tests, then report. Say plainly what is not yet tested.
- **Keep the simulator current:** `docs/simulation.html` shows the built steps as an animation on
  imaginary data, so Karthi can see how the project works. Extend it whenever a step lands; it is also
  published as a private artifact (https://claude.ai/artifact/JP6GGmKYfcA6hQo7xX7Vip).
- **Attribution rule (firm):** commits, PRs, and pushes credit only `karthi-ai-engineer`. No
  Co-Authored-By Claude, no "Generated with Claude Code". Enforced by `.claude/settings.json` and
  `.githooks/commit-msg`; details in `CLAUDE.md`. Don't enable Dependabot (its bot would appear as a
  contributor). Machines may carry other git identities (work accounts): check before committing.

## Machines

| Machine | Use | Notes |
|---|---|---|
| Windows laptop (i5-1334U, 32 GB, Intel Iris Xe, no NVIDIA) | Session 1, 2026-09-17 | Borrowed for one day; Karthi returns to it in ~5 days |
| Acer Predator PHN16-72 (i9-14900HX 24 cores / 32 threads, 16 GB RAM, RTX 4060 Laptop 8 GB, 473 GB free) | Session 2 onward, 2026-09-17 (~5 days) | Specs verified in session 2 (NVIDIA driver 595.95, CUDA 13.2). Repo at `C:\dev\SceneFold`. 8 GB VRAM fits an 8B VLM at 4-bit (~7 GB) only just |
| MacBook Air M3, 8 GB RAM, 256 GB | Backup | Memory and disk are tight for video work |

Why the Predator: nothing in Phases 2–5 needs a GPU, but it runs everything the Mac can, has more
room for footage, runs Windows like the machine the code was first tested on, and its GPU allows
starting Phase 8 (identity) early.

### Set up a new machine

```sh
# install: git, GitHub CLI, FFmpeg (7+; Windows: winget install Gyan.FFmpeg), uv (winget install astral-sh.uv)
gh auth login                      # as karthi-ai-engineer
git clone https://github.com/karthi-ai-engineer/SceneFold.git
cd SceneFold
git config user.name "Karthi AI Engineer"
git config user.email "296384397+karthi-ai-engineer@users.noreply.github.com"
git config core.hooksPath .githooks
git switch <current phase branch>  # see "Where things stand"; main when none is open
uv sync
uv run pytest                      # expect 388 passed, 1 xfailed (the known chorus limit)
node --test web/tests/sync.test.mjs  # the viewer's timing rules (needs Node 18+)
```

- **Clone outside OneDrive/Dropbox.** Windows often syncs Desktop and Documents; footage in `data/` and
  `.venv` would upload, and sync can lock files while FFmpeg writes them. Use e.g. `C:\dev\SceneFold`.
- **Another GitHub account already saved** (Git Credential Manager)? Pushes would silently use it.
  Point this repo alone at the `gh` login, then check with `git push --dry-run`:
  ```sh
  git config credential.https://github.com.helper ""
  git config --add credential.https://github.com.helper "!gh auth git-credential"
  ```

---

## Where things stand (session 7, 2026-09-21)

| Phase | Status | Where |
|---|---|---|
| 0 Foundation | Done | `main` |
| 1 Ingest | Done; checked on 12 real Jiku phone clips; sound now follows file timestamps | `main` |
| 2 Sync | Done: drift-aware sync, measured on real Jiku clips, ahead of two baselines | `main` (merged from `phase-2-sync`) |
| 3 Synced viewer | Done, **v0.1.0**: `scenefold view`, every picture within half a frame, the placement checked on the pictures alone, demo event and README GIF | `main` (merged from `phase-3-viewer`) |
| 4 Quality cut | Done: `scenefold cut` scores each second, plans the shots, renders `cut.mp4`, and the viewer plays it | `main` (merged from `phase-4-cut`) |
| 5 Clip understanding | **Next** | not started |
| 6–9 | Not started | |

- Results and findings: `docs/ROADMAP.md`, Phase 2 "Progress". In short: on two real Jiku subsets,
  5 of 6 phones agree with the published ground truth within 6.4 ms (174 s overlaps) and 26 ms
  (80 s overlaps); the Nexus S differs by 70–110 ms, cause unknown (not the ingest timing).
- Jiku data is local only: `data/_downloads/jiku/` (clips, ground truth XML and JSON), workspaces
  `data/jiku-saf/` and `data/jiku-saf-long/`. On a new machine, `uv run python tools/jiku.py jiku-saf`
  fetches it again (786 MB; `jiku-saf-long` is 1.54 GB), then ingest, sync, evaluate.
- **YouTube test set (personal testing only, never committed):** Karthi asked to use YouTube clips
  instead of a home recording for now. Coldplay "Fix You", Narendra Modi Stadium, Ahmedabad:
  - `coldplay-jan25` (6 clips): 27fb9PAtqWA TOmDG24_lA8 VSqpJ9M3viA iFxmnXwaSls mGAFbbACfxg pzqNetFpSMY
  - `coldplay-jan26` (5 clips): 5yzlgOigdTE UY_LoABsuR0 gMdI4i8aOlk uBLMlXD8Dzg xa_Anul1-7A
  - Download (portrait clips need the width limit):
    `uv run --no-project --with yt-dlp yt-dlp -f "bv*[height<=720][width<=1280]+ba[ext=m4a]/bv*[width<=720]+ba" --merge-output-format mp4 -o "data/_downloads/youtube/coldplay-fix-you/%(channel).24B %(id)s.%(ext)s" <urls>`
  - Then `scenefold ingest coldplay-jan25 <its 6 files>`, `sync`, `view`. Both pass `check_viewer.py`.

### Next steps, in order

1. ~~Finish Phase 3~~ **Done (session 6):** tagged `v0.1.0` and merged into `main`. The README GIF
   comes from a drawn event with nobody in it (`tools/demo_event.py`, recorded by
   `tools/record_viewer.py`); `uv run python tools/demo_event.py` rebuilds it anywhere in about two
   minutes. The laptop-without-GPU item was dropped (that laptop is gone, and the viewer decodes in
   software anyway), and the picture check replaced the home clap recording. The Nexus S check
   failed for want of a signal: Jiku's lighting is steady, so no pair's brightness matched clearly.
   It stays open; it would need a visible, audible moment (a hit or a light cue) found by hand.
2. **Then: place the pictures, not the sound arrival** (ROADMAP Phase 2, "Next in sync"). The
   picture check found phones up to 423 ms apart in when they heard the same stadium show, which is
   how far they stood from the speakers (2.9 ms per metre). `tools/check_pictures.py` already
   measures it; the work is to fold it into sync, `timeline.json`, and the viewer, and to keep
   sound alignment available for listening.
3. ~~Reject pairs whose windows disagree~~ **Done (session 5):** `partial_match` (ROADMAP Phase 2).
   Watch it on new footage: a true pair with a moving phone or an edit can fall under 0.9 too.
4. Later (not Phase 3): place several groups (one per night or moment) instead of only the main
   one; detect edits (cuts) inside uploaded clips; a solver that weighs several candidate lags per
   pair would fix the repeated chorus case (strict xfail test in `tests/test_sync.py`). With more than 6 clips, Chrome's limit
   of 6 connections per host may queue video loading; check on a bigger event.

### Open questions / decisions still pending

- Gemini free vs paid tier (Phase 5; free tier may use uploaded footage).
- The brief mentions `scenefold-ui-preview.html`; it was never added to the repo.
- Filming the real test event (3–5 friends, consent, distinct clothes, claps) before Phase 5.

---

## Design decisions so far (and why)

- **Folders by kind, not by phase:** `src/`, `web/`, `tests/`, `docs/`, `data/` (data is never committed).
- **Branches:** one per phase, chained (each starts from `main` after the previous phase merged);
  merge by fast-forward once CI is green; delete merged branches; fixes to older phases go on the
  current branch.
- **Phase order changed from the brief:** identity (hardest) moved to Phase 8, so a cited story demo
  exists by v0.5.0; a no-AI quality cut comes early (Phase 4).
- **Ingest:** originals copied read-only into `data/<event>/originals/`; clip ID = first 12 hex of
  SHA-256; working copy H.264 720p CFR 30 fps, keyframe every 1 s, faststart, metadata stripped;
  mono 48 kHz WAV made **in the same FFmpeg pass**. Picture and sound are padded to start at clip time 0
  with `fps=30:start_time=0` and `aresample=48000:async=1:first_pts=0` (verified: flash and click land
  within 0.1 ms even when a track starts late).
- **Sync:** GCC-PHAT with β = 0.8 at 8 kHz; search only lags with ≥ 5 s overlap; confidence = best
  peak ÷ best peak outside ±100 ms (threshold 2.0; unrelated audio ~1.0–1.1; synthetic matches 20–45,
  real Jiku pairs 2–11, so never tune thresholds on synthetic numbers). Weighted least squares over
  all pairs; clips placed through other clips; unplaceable clips get a plain reason, never forced.
  β was swept 0.6–1.0 on music, noise, echo, unrelated audio: 0.8 stays the best balance.
- **Clock drift (session 3):** the model is `t_local = (t_master − offset_s)(1 + drift_ppm/1e6)`, with
  the master clock = the average of the clips whose drift was measured. Per pair: first whole-clip
  match → lag in 10 s windows searched ±(50 ms + 1000 ppm × overlap) → Theil–Sen line (more than half
  the windows, ≥ 3, spanning ≥ 20 s) → stretch B, match again (kept only if confidence doesn't drop).
  No line → match up to 4 one-minute pieces (both clips ≥ 2 min). Per-clip drift = least squares over
  pair drifts weighted by overlap³. Measured from sound; video may differ on some devices.
- **Solver outliers (session 3):** a pair is judged by its left-out residual (residual ÷ (1 − leverage)),
  not its plain residual, because a confident wrong pair pulls the fit toward itself. Around a single
  loop of 3 pairs all tie; then the least confident pair goes. Threshold still 20 ms.
- **Evaluation:** ground truth = moments with a clip time per clip; error per pair of placed clips
  (independent of where master time 0 is); report median, p95, worst, pairs within 33 ms.
- **`tools/`:** developer scripts that aren't part of the pipeline (dataset fetch, baselines); linted in CI.
- **Viewer (Phase 3):** stdlib `ThreadingHTTPServer` on 127.0.0.1 with Range support and a Host check;
  plain ES modules in `web/`, no build step, timing rules in `web/sync.js` (Node tests). One master
  clock follows the heard clip's shown frames (rVFC); other videos are steered per frame (trim ±0.3%,
  corrections 1–10%, jump > 0.35 s aimed ahead by the clip's measured jump time).
- **Later phases (planned):** analyze each clip independently with the VLM (independent witnesses make
  disagreements meaningful); SQLite instead of Neo4j; citations validated in code; director's cut
  switches picture but keeps one continuous audio track; license Apache-2.0 (YOLO/BoxMOT are AGPL, so
  plan permissive vision libraries for Phase 8).

## Lessons and gotchas

- **CI** installs FFmpeg 9.0 via `AnimMouse/setup-ffmpeg` (Homebrew's `ffmpeg` lacks zimg, Ubuntu apt
  is 6.1). CI sets `SCENEFOLD_REQUIRE_FULL_FFMPEG=1` so missing features fail instead of skipping.
  Pull-request runs skip on purpose; look at the push run for results.
- **FFmpeg 9** reports a missing MP4 index as "error reading header; End of file" (not "moov atom not
  found"). A half-downloaded file still converts but comes out shorter (flagged `incomplete_file`).
- **Windows:** `uv` and FFmpeg from winget aren't on PATH until a new terminal (winget adds their
  package folders to the user PATH, not to `WinGet\Links`); Python `write_text` writes CRLF
  (`.gitattributes` normalizes to LF). A pip package named `gh` (unrelated to GitHub CLI) can shadow the
  real `gh`: check `gh --version` and `pip uninstall gh` if it prints `v0.0.x`.
- **Style:** ruff's formatter explodes long FFmpeg argument lists, so commands are written as
  `"-a b -c d".split()` (rule SIM905 is ignored on purpose).
- **Auto mode** once refused a merge to `main` without review; Karthi then delegated merges explicitly.
- **Claude Code sessions** started before uv/FFmpeg were installed keep a stale PATH. In PowerShell,
  prefix commands with `$env:Path = [Environment]::GetEnvironmentVariable('Path','User') + ';' +
  [Environment]::GetEnvironmentVariable('Path','Machine');`. PowerShell 5.1 mangles here-strings piped
  to `git commit -F -`; write the message to a file and use `git commit -F <file>`.
- **Timing:** the test suite takes ~41 s on the Predator, but ~150 s while an agent runs FFmpeg ingest.
- **Chrome video playback:** rates within ±0.1% of 1.0 play as normal speed, and entering that band
  stalls a video for about a frame; a jump while playing stalls while it decodes from the keyframe;
  sound starts up to 0.7 s after pictures in headless Chrome. Playwright's own Chromium has no
  H.264: use `channel="chrome"`. Test page ideas in a scratch HTML under `web/` (served by the
  viewer), then delete it.
- **Real phone audio is messier than FFmpeg's view of it:** sample counts that disagree with timestamps
  (Galaxy S II), a first-packet timestamp jump (Galaxy Nexus), edit lists (Nexus S), clipped concert
  sound, phones that move. Check ingest against real clips, not only generated ones.

---

## Session log

### Session 1: 2026-09-17, Windows laptop (~5 hours)

1. Read the brief; gave a critical analysis (sync pitfalls on real phones, identity risk, simpler
   knowledge store, independent witnesses, honest conflict types).
2. Set up git, remote, and the attribution guard; researched the stack with two agents; wrote
   `docs/ROADMAP.md` (Phases 0–9).
3. Created the folder layout. Built **Phase 1 ingest** with 116 tests (then pushed `main` +
   `phase-1-ingest`).
4. Finished **Phase 0** with two parallel agents: CI on 3 OSes, Apache-2.0 license, README, docs moved
   into `docs/`, commit-guard tests. Merged PR #1 into `main`.
5. Started **Phase 2 sync** on `phase-2-sync` with two parallel agents: pair measurement and the
   solver + `scenefold sync`. 167 tests green on CI.
6. Wrote this handoff for the move to the Predator.
7. Karthi asked whether this already exists. Researched products, research, and investigative
   practice: `docs/RELATED_WORK.md`. Pieces exist (editor audio sync, podcast auto-switching,
   research on cited multi-video stories); the end-to-end chain for crowd phone footage does not.
8. Suggested test footage: first a home recording (3 phones, claps at start/middle/end, staggered
   starts, portrait + landscape); then 3+ original (not WhatsApp/Instagram) clips of the same moment
   of a Tamil concert; a cricket game later (far-apart phones, sound delay). Footage stays in `data/`.

### Session 2: 2026-09-17, Acer Predator

1. Logged in to GitHub as `karthi-ai-engineer` (this machine also has `karthigpt04` saved in Git
   Credential Manager, so the repo's credential helper points at `gh` only for this clone).
2. Verified the Predator: RTX 4060 Laptop 8 GB and 16 GB RAM (not the reported RTX 3060).
3. Cloned to `C:\dev\SceneFold` instead of the OneDrive-synced Desktop; installed FFmpeg 9.0.1 and uv;
   set identity and hooks; `uv sync`; 167 tests pass, lint clean.

### Session 3: 2026-09-17, Acer Predator

1. Measured before building: clock drift smears the whole-clip match (10 min at 100 ppm fell to
   confidence 1.9 with a 27 ms error). Found a solver bug: with 4 clips, one wrong pair at confidence
   20+ pushed two correct pairs out.
2. Prototyped the windowed check in the scratchpad on drift, unrelated audio, looped music, a
   repeated chorus, and a muffled phone. Windows as a confidence gate failed on music, so they only
   measure drift. Built drift measurement + cancelling, drift per clip, and left-out residuals.
3. A research agent found real drift up to 274 ppm (Galaxy S II) and 417 ppm (iPod touch), the Jiku
   ground truth format, and why both baselines fail to install on Python 3.13. Raised the drift
   search to 1000 ppm; added one-minute pieces for strong drift over long overlaps.
4. Built `scenefold evaluate` (ground-truth moments). An agent built `tools/jiku.py` and ran two
   Jiku subsets: all 12 clips placed; results and real-audio findings in ROADMAP Phase 2 "Progress".
   Its claim that the ground truth is wrong for the Nexus S was not proven (its check used our own
   decoded audio), so it is recorded as unresolved.
5. Chorus heard by a bridging clip stays a known limit (strict xfail). README documents sync,
   evaluate, measured accuracy, and limits.
6. An agent compared audio-offset-finder and audalign on the same WAVs (`tools/baselines.py`, Python 3.12
   via uv, no install hacks): Scenefold was most accurate on both subsets. The baselines also put the
   Nexus S ~80 ms from the ground truth; since every method read our WAVs, that still leaves the WAV
   time base (next step 2) as the open question.
7. Built `docs/simulation.html`, an animated walk-through of the built steps on imaginary data, and
   published it as a private artifact. Rule added: extend it whenever a step lands.

### Session 4: 2026-09-19, Acer Predator

1. No new footage yet, so worked on ingest audio timing. Measured every Jiku clip frame by frame:
   Galaxy S II audio holds 314.5 ppm more samples than its timestamps; Galaxy Nexus timestamps jump
   14–19 ms after the first frame; Nexus S timestamps are exact (so its 70–110 ms mystery is elsewhere).
2. Wrote a failing test first (generated files that mimic both phones), then made the WAV follow the
   timestamps like the picture: `aresample=async=1000` via `ProxySettings.audio_max_stretch` (a new
   setting, so old working copies rebuild). 203 tests pass.
3. The Jiku ground truth counts samples, so `tools/jiku.py` now maps it onto each file's timestamps;
   `tools/baselines.py` now scores at the same moments as `scenefold evaluate` (its old per-pair
   start lag was thrown off by the 16 ms first-frame jump). Re-ran everything: accuracy held; over ~3
   minutes Scenefold beats both baselines, over 80 s they are close.
4. Closed Phase 2 (Done-when met); the clap and Nexus S checks moved to Phase 3. PR #2, fast-forward
   merge into `main`, branch deleted.
5. Phase 3 on `phase-3-viewer`. An agent built the server and `scenefold view` (59 tests, Host-header
   check against DNS rebinding); I wrote the viewer (`web/`) and `tools/check_viewer.py`, which plays
   an event in headless Chrome (Playwright, `channel="chrome"`) and reads the viewer's own per-frame
   errors. First runs were 16–45 ms off with repeated jumps; experiments found why (ROADMAP Phase 3
   "Progress"): Chrome stalls a video each time its rate enters the ±0.1% band around 1.0, jumps land
   late unless aimed ahead by the clip's own jump time, the heard clip's `currentTime` shifts once its
   sound settles, and a joining clip needs a moment to start. After the fixes both Jiku subsets pass:
   every picture within half a frame during playback, paused jumps exact.
6. A screenshot caught a CSS bug the checks could not (`display: grid` beat the `hidden` attribute).
   Lesson: look at the page once, not only its numbers. Added section 8 to `docs/simulation.html`.
7. Karthi asked to use YouTube clips for now. Downloaded 14 fan clips of Coldplay "Fix You" from
   both Ahmedabad nights: all 14 landed on one clock (backing tracks match across nights). Window
   agreement separated the nights; made `coldplay-jan25` (6) and `coldplay-jan26` (5), both pass.

### Session 5: 2026-09-20, Acer Predator

1. Measured window agreement on every labelled pair (Jiku, both Coldplay nights, cross-night,
   synthetic loops) and built the `partial_match` rule into sync (`windows`, `agreement` per pair;
   `min_agreement` 0.9, `agreement_windows` 2 in the settings). Dropped a "trust very confident
   pairs" exception after a synthetic other-night pair reached confidence 4.9; judged short clips
   after a 32 s clip bridged the nights. The mixed Coldplay set now splits; nothing else changed.
2. The viewer's sync report shows each pair's agreement; unplaced clips that matched each other get
   an honest reason. Agreement samples at most 24 windows (cost: ~1 s for a 10-minute pair).

### Session 6: 2026-09-20, Acer Predator

1. **Checked sync on the pictures** (`tools/check_pictures.py`, new). It never listens: it reads each
   working copy's brightness frame by frame, removes slow changes, and matches the curves of every
   placed pair ±2 s around their sound lag. Two measures had to be fixed before it said anything —
   brightness changes slowly, so a match must be judged against lags more than 5 s away (4σ), not
   against its own broad peak, and the search runs as one FFT correlation instead of a loop.
2. **Result: the placement is right, and the sound arrives late from far away.** On both Coldplay
   nights every pair matched clearly (4.6–7.4σ), but pictures sat up to 269 ms (Jan 26) and 423 ms
   (Jan 25) from where sound put them, consistently per clip. One delay per clip explains all pairs
   to within 15 ms and 50 ms — so the matching scatters by a third of a frame and the rest is
   distance: 2.9 ms per metre, i.e. phones 0–91 m and 0–145 m apart from the speakers. Sound-based
   sync lines up when each phone *heard* the event. Numbers and the plan: ROADMAP Phase 3 "Picture
   check" and Phase 2 "Next in sync".
3. **Nexus S: still open.** Jiku's lighting is steady, so no pair reached 3σ and best lags scattered
   by ±2 s. Brightness can't settle it; it needs a moment both seen and heard, found by hand.
4. Dropped the Phase 3 "laptop without a dedicated GPU" item: that borrowed laptop is gone, Karthi
   has only the Predator, and the viewer decodes 720p in software either way.
5. **A demo event nobody appears in** (`tools/demo_event.py`): FFmpeg draws a two-minute show (lit
   stage, three figures, crowd, a clock in the picture, restless lighting), and four imaginary
   phones film it with their own framing, brightness, noise, echo, and clock drift. Sync places all
   four within 1 ms of the truth; the viewer check and the picture check both pass on it. This is
   the safe source for anything public, since Jiku and YouTube footage shows real people.
6. **README GIF** (`docs/viewer.gif`, 2 MB): `tools/record_viewer.py` photographs the viewer playing
   in headless Chrome and builds the GIF with FFmpeg. Playwright's own recorder needs a binary it
   downloads separately, so it takes pictures instead and plays them back at the rate they came.
   Decision: generated media stays out of the repo, but this one GIF is documentation (like
   `docs/scenefold.png`) and the Phase 3 "Done when" asks for it.
7. **Phase 3 closed:** CI green, `phase-3-viewer` fast-forwarded into `main`, tagged `v0.1.0`, branch
   deleted. `docs/simulation.html` gained section 9 ("Sound takes time to arrive") and the published
   artifact was updated to match.
8. Not done, and worth knowing: no progress post has been written for v0.1.0 (the roadmap's Phase 3
   asks for one), and nothing was published outside the repo.
9. **Started Phase 2b: place the pictures, not the sound arrival** (branch `phase-2b-picture-sync`).
   `src/scenefold/picture_offset.py` matches placed pairs on their brightness curves around where
   the sound put them, cancelling the pair's measured drift, and solves the clear differences into
   one `heard_late_s` per clip (`timeline.json` schema 2, plus the picture measurement on every
   pair). `scenefold sync` prints it as a distance and takes `--sound-only` to skip the pass.
   Two measures matter and were both learned the hard way: a match is judged against lags more
   than 5 s away, and scores divide by the square root of the overlap so short and long overlaps
   are judged on the same scale (the first attempt refused pairs that barely overlapped).
10. Verified: on drawn clips with known distances the delays come back within a frame, and within
    17 ms where the truth is zero; on the Coldplay nights it reproduces the standalone check
    (0/46/52/97/273 ms on the 26th, up to 420 ms on the 25th); on both Jiku subsets it says it
    cannot tell, because their lighting is steady. `tools/check_pictures.py` was deleted — sync
    measures this now, and two implementations would drift apart.
11. Left for this branch: the viewer's side (line up by pictures or by sound, and the new columns
    in the sync report). One open question: `scenefold evaluate` compares clips at ground-truth
    moments using the sound alignment. Moments found by stepping through pictures (a flash) would
    want the picture alignment instead, so evaluate may need to say which it is using.
12. **The viewer can line up pictures or sound** (`web/sync.js` takes an alignment; `app.js` binds
    it in one place so no conversion can be left on the other one). The transport bar has
    "Line up · Pictures / Sound heard", defaulting to pictures when any clip knows its distance and
    disabled with an explanation when none does. Switching re-seeks every video through the normal
    path. The sync report gained "Heard late" and "Further away" per clip, a "Furthest from the
    sound" tile, and the picture measurement columns per pair. 15 timing tests (was 11).
13. **A bug worth remembering, found by reviewing the agent's report, not by a test:** the picture
    measurements were written into each pair row in the loop's (a, b) order, while the row itself
    may hold (b, a) — the placed clips are ordered longest first, the rows in manifest order. Four
    of ten `coldplay-jan26` rows stored the reversed answer (sound lag −81.132 s next to picture
    lag +81.093 s). Per-clip distances were never affected. Fixed by measuring each pair the way
    its own row reads, and `tests/test_picture_sync.py` now checks every row's two answers agree.
14. **`tools/check_viewer.py` needs a quiet machine.** It failed repeatedly today on the 5- and
    6-clip real events (p95 45 ms during playback, a 140 ms jump) with ~22 of Karthi's own Chrome
    processes running, and passed on the 4-clip demo. It fails the same way in sound alignment and
    on `jiku-saf-long`, which has no distances at all, so it is decoding starved of CPU, not a
    regression. Re-run it on an idle machine before trusting a red result.
15. Phase 2b merged into `main` (CI green) and the branch deleted. 291 tests pass, plus 15 viewer
    timing tests. No version tag: v0.1.0 stands until the next milestone phase.

### Session 7: 2026-09-21, Acer Predator

1. **Phase 4, the quality cut** (branch `phase-4-cut`). `src/scenefold/quality.py` measures each
   second of each clip — sharpness (detail in the frame), steadiness (how far the whole picture
   shifts, found by phase correlation, so a subject crossing a steady frame costs nothing) and
   exposure — then stretches each measure over the range the event itself shows.
2. `src/scenefold/cut.py` plans the film in one pass of dynamic programming over pieces of 3-12 s,
   with a cut costing 0.35, a cut back to the angle before last costing 0.25 more, and a free
   "hold" that merges into one shot. The hold matters: without it a stretch filmed by one phone
   alone cannot be covered, because there is no second angle to cut to — the first version failed
   exactly there and fell back to nonsense.
3. Sound comes from one clip unbroken (longest, then nearest to the sound); each shot carries the
   `speed` that puts it on that clip's clock, so picture and sound hold together over a long film.
4. Measured: 13 shots over 5:08 on `coldplay-jan26` (51 s to look at 20 minutes of footage, 216 s
   including the render); on the drawn demo the clock burnt into the picture reads 30.467 s at 30 s
   into the film, so the shots land on the right frames. 310 tests pass.
5. Two bugs worth remembering, both found by tests: `int()` truncates towards zero, so a clip
   looked available half a second before its first frame (now a clip must cover the whole second);
   and a generator that reads FFmpeg's output must not report "broken pipe" as a bad file when the
   caller simply stopped early.
6. `docs/simulation.html` gained section 10, which runs the same shot-planning rules on invented
   scores with dials for the cut cost and the shortest shot.
7. The viewer gained a **Film** tab (plays `cut.mp4`, shot list with reasons, click to jump);
   `view.py` serves `/api/cut` and `/film.mp4`. Watch the connection limit: Chrome allows about six
   per site and the clip videos hold them all, so the film's own data is fetched only when someone
   opens the tab. 316 tests pass, plus 15 viewer timing tests.
8. **CI caught a false alarm the local machine never showed.** On Windows, `test_steady_light_says
   _it_cannot_tell` failed: a steadily lit room, which holds no signal at all, produced a "clear"
   brightness match. Cause: anything that repeats matches at every repeat, and video encoding marks
   every keyframe a second apart, so a repeat can win the local search by luck. A match must now
   also lead the best lag anywhere else by 0.6 standard deviations (measured: repeats alone lead by
   at most 0.50, real matches by 0.84-2.71 on drawn clips and 0.60-1.97 on two concerts). The
   distances are unchanged; only the weakest pairs drop out. Lesson: the three OSes in CI are worth
   more than they look — the same code, same seeds, different FFmpeg build.
9. Phase 4 merged into `main` (CI green on all three systems) and the branch deleted. No tag:
   v0.1.0 stands until Phase 7. Note for the record: commit 60bf280 on the way through the branch
   failed on Windows CI (the false alarm in item 8); 367b4c5 fixes it, so only that one commit in
   the history is red.

### Next steps, in order

1. **Phase 5: what each clip shows.** The roadmap has the plan: a model-agnostic provider, one
   observation file per clip in clip-local time, each clip read on its own so that disagreements
   between angles stay real evidence. Decide the provider first (the roadmap's open question about
   Gemini's free tier using uploaded footage is still open, and the footage here is other people's).
2. Loose ends carried forward: no progress post for v0.1.0; the Nexus S still disagrees with Jiku's
   published ground truth by 70-110 ms; other nights are reported but not placed; edited uploads
   with cuts cannot be placed; `tools/check_viewer.py` needs a quiet machine to trust a red result.

### Session 8: 2026-09-21, Acer Predator

1. **Phase 5 started** (branch `phase-5-understanding`): `scenefold observe <event>` asks a model
   what each clip shows, a few seconds at a time, into `observations/<clip_id>.json` in clip-local
   time. `src/scenefold/observations.py` is the contract, `observe.py` does the watching.
2. **The provider question is settled by the machine**: Karthi already had Ollama with
   `qwen3.5:4b` (4.7B, vision, Apache-2.0, ~3.4 GB). It answers a four-frame window in about three
   seconds, costs nothing, and no footage leaves the computer — which matters because the test
   footage is of people who agreed to be filmed by a friend, not uploaded. The Gemini decision in
   the roadmap is replaced; a cloud adapter is one small class (`observe.Watcher`) when wanted.
3. **What the 4B model can and cannot do, measured before designing around it:** it describes what
   is in front of it well and reads on-screen text (it read the demo's burnt-in clock exactly), but
   it invents counts ("5,000 people") and always rates itself 1.0. So it is never asked to count,
   and each observation carries the picture score from `quality.py` instead of the model's opinion
   of itself.
4. Ollama takes a JSON schema (`format`), which makes the answers well-formed every time; the
   parser still falls back to keeping plain words if a model ever answers in prose.
5. Measured on `coldplay-jan26` (5 clips, ~20 minutes of footage, 12 s windows): 91 observations
   in 338 s of model time on an idle machine — about one second of model time per 3.5 seconds of
   video. Watching it again asked nothing. Beware measuring while the machine is busy: one clip
   took 1220 s while the test suite ran alongside it, and 64 s when re-watched on its own.
6. Two things to watch in the model's answers: it says "a Coldplay concert" although it is told
   never to guess names (it may be reading the stage, or it may be guessing), and long summaries
   repeat the same scene-setting phrases across neighbouring windows. Both are prompt work.
7. Left in Phase 5: speech with word-level timing (faster-whisper), snapping the model's
   second-level times to audio onsets and motion peaks, and measuring recall — which needs the
   hand-labelled test event Karthi has yet to film. The simulator has no section for this step yet.
8. **Speech landed** (`src/scenefold/speech.py`, optional install `uv sync --extra speech`). Whisper
   through faster-whisper, word-level times, voice detection on by default. Measured: 45 s to
   listen to 20 minutes of concert audio, which kept one two-word line — music does not become
   invented lyrics. Verified the other way with a clip spoken by Windows' own voice: every word
   came back, timed ("Please@3.56, find@4.24, your@4.56, seats@4.78"), with one homophone slip on
   the `base` model ("Right" heard as "Write"). That check is a test, skipped unless the extra is
   installed.
9. **Two bugs of mine worth remembering.** The Whisper engine was being built once per clip, so
   every clip loaded the model onto the graphics card, failed on the missing cuBLAS, and loaded it
   again on the processor — minutes wasted per event, and it looked like a hang. One engine per
   event now. And a card can load a model and only fail when asked to do arithmetic with it, so the
   device is now tried on a moment of silence before being trusted.
10. A `scenefold view` server left running from an earlier check held the installed command open
    and broke `uv run`; stop stray viewers before syncing dependencies.
11. **Moments** (`src/scenefold/moments.py`): what can be timed without understanding anything —
    growth in the sound, sudden change in the picture. Each observation is pulled onto the
    strongest moment inside its window, each spoken line onto the sound that starts it (within
    0.25 s). Measured: claps within 50 ms, drawn flashes within 150 ms, and 77 of 91 concert
    windows gained an exact time. Where nothing stands out the coarse time is kept — the one
    spoken line on that event stayed where Whisper put it.
12. A wiring bug worth remembering: the timing was only applied when a clip was *re-watched*, so
    the first run over cached observations reported 0 of 91 windows timed. Anything computed
    outside the cached step has to be applied to what is already on disk as well.

### Session 9: 2026-09-21, Acer Predator

1. **Phase 6 started** (branch `phase-5-moments` merged; now `phase-6-knowledge`): `scenefold fuse`
   puts every clip's moments on the shared clock, merges the ones landing together into events with
   several witnesses, and writes `knowledge.sqlite` (clips, events, evidence, conflicts).
2. Corroboration measured: 219 of 298 moments on the drawn demo (all clips film the same show), 118
   of 311 on the concert (phones point different ways). That gap is the sanity check.
3. **The conflict rule took two goes.** Flagging "a clip that was filming missed this" made 307 of
   311 concert events conflicts — useless. Now a conflict needs two clips agreeing plus a third
   whose picture was at least as good as the weakest witness's: 69 conflicts, 7 resolved by the
   better view, 44 unresolved because nobody was clearly better, 18 unresolved because the best
   view is the one that missed it. Measured the real gap between best and next picture score
   (median 0.154) before trusting the 0.12 margin.
4. Floating point: `12.7 - 12.5 - 0.2` is slightly negative, so a moment on a clip's first frame
   read as "not recording". `recording_at` now allows a millisecond either side.
5. **Reading the accounts** (`judge.py`): the local model compares two descriptions of one moment.
   Watch the prompt: the first version, warned off wording differences, called "one performer at a
   piano" and "four band members playing" compatible. A rubric naming what cannot both be true
   fixed it (6 of 6 calibration pairs right; that is a test that skips without a model). On the
   concert it read 54 distinct pairs and found no contradictions — five phones pointed at one
   stage genuinely agree, and the feature will earn its keep on footage where people differ.
   Caching by sentence pair cut 311 events to 54 readings.
6. **Phase 7 started**: `scenefold story` writes a cited account from the event store. Validation
   is in code, not in the prompt — the cited moment must exist and a clip that saw it must have
   been filming then; failures are dropped with their reason into `story.json`. 15 lines on the
   concert, none dropped, 4 disputed. Left: Q&A over the store, the viewer panels, and `v0.5.0`.
7. Two parsing traps worth remembering: a citation after the full stop ("drop. [1]") gets split
   onto the next sentence and must be carried back, and neighbouring moments that share one
   description make the model repeat itself, so identical adjacent lines are merged.
