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
uv run pytest                      # expect 267 passed, 1 xfailed (the known chorus limit)
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

## Where things stand (session 6, 2026-09-20)

| Phase | Status | Where |
|---|---|---|
| 0 Foundation | Done | `main` |
| 1 Ingest | Done; checked on 12 real Jiku phone clips; sound now follows file timestamps | `main` |
| 2 Sync | Done: drift-aware sync, measured on real Jiku clips, ahead of two baselines | `main` (merged from `phase-2-sync`) |
| 3 Synced viewer | **In progress**: `scenefold view` works; every picture within half a frame, and the placement checked on the pictures alone | branch `phase-3-viewer` (not merged) |
| 4–9 | Not started | |

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

1. **Finish Phase 3** on `phase-3-viewer` (ROADMAP Phase 3 "Progress" lists what is done):
   - README GIF from consenting footage (not Jiku or YouTube: real people). The generated test clips
     from `tests/synth.py` are the safe source; record the viewer playing them.
   - Tag `v0.1.0`, then PR `phase-3-viewer` → `main`, fast-forward merge once CI is green.
   - Done in session 6: the laptop-without-GPU item was dropped (that laptop is gone, and the viewer
     decodes in software anyway), and the picture check replaced the home clap recording.
     The Nexus S check failed for want of a signal: Jiku's lighting is steady, so no pair's
     brightness matched clearly. It stays open; it would need a visible, audible moment (a hit or a
     light cue) found by hand in `jiku-saf-long`.
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
