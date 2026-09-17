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
git switch phase-2-sync            # current work branch (see "Where things stand")
uv sync
uv run pytest                      # expect 201 passed, 1 xfailed (the known chorus limit)
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

## Where things stand (session 3, 2026-09-17)

| Phase | Status | Where |
|---|---|---|
| 0 Foundation | Done | `main` |
| 1 Ingest | Done; ran on 12 real Jiku phone clips (all ok). Audio timing issue found, see next steps | `main` |
| 2 Sync | **In progress**: drift-aware sync measured on real clips (Jiku); a few checks left | branch `phase-2-sync` (not merged) |
| 3–9 | Not started | |

- `main` = `dcaf74e`. `phase-2-sync` is ahead with all sync work, `scenefold evaluate`,
  `tools/jiku.py`, and docs. Check with `git log --oneline origin/main..origin/phase-2-sync`.
- Results and findings are in `docs/ROADMAP.md`, Phase 2 "Progress". In short: on two real Jiku
  subsets, 5 of 6 phones agree with the published ground truth within 4.3 ms (174 s overlaps) and
  26 ms (80 s overlaps); the Nexus S differs by 70–110 ms, cause unknown.
- Jiku data is local only: `data/_downloads/jiku/` (clips, ground truth XML and JSON), workspaces
  `data/jiku-saf/` and `data/jiku-saf-long/`. On a new machine, `uv run python tools/jiku.py jiku-saf`
  fetches it again (786 MB; `jiku-saf-long` is 1.54 GB).

### Next steps, in order

1. **Home clap recording (Karthi).** ~1 minute, 2–3 phones at once, a visible, sharp clap at the start
   and the end, some walking. Then `scenefold ingest`, `sync`, and `evaluate` with the clap times
   (README "Checking sync with claps"). First check of visual (not just audio) sync.
2. **Ingest audio timing (Phase 1 fix, on this branch).** For every Jiku clip compare the WAV's sample
   count with the audio container timestamps. Galaxy S II runs ~310 ppm ahead, so
   `aresample=async=1` cuts a 100 ms jump after ~5 min, and picture and sound slip ~60 ms per 200 s.
   Try soft compensation (`aresample=async=<N>`, which stretches smoothly) while keeping the flash and
   click tests within 5 ms; decide which clock a working copy follows. This may also explain the
   Nexus S difference.
3. **Nexus S check.** If step 2 doesn't explain it, find a moment seen and heard by the Nexus S and
   another phone in `jiku-saf-long`, and see which placement is right.
4. **Baselines** (audalign, audio-offset-finder) on the same Jiku audio: `tools/baselines.py` was being
   written at the end of session 3; see the session log.
5. Then PR `phase-2-sync` → `main`, fast-forward merge once CI is green, delete the branch.
6. **Phase 3, synced viewer** → first demo `v0.1.0`. Use `1 + drift_ppm/1e6` as each clip's rate.
7. Later (not Phase 2): a solver that weighs several candidate lags per pair would fix the repeated
   chorus case (strict xfail test in `tests/test_sync.py`).

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
