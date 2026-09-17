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
uv run pytest                      # expect 167 passed
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

## Where things stand (session 2, 2026-09-17)

| Phase | Status | Where |
|---|---|---|
| 0 Foundation | Done | `main` |
| 1 Ingest | Done on generated clips; **never run on real phone footage** | `main` |
| 2 Sync | **In progress**: core works on synthetic audio | branch `phase-2-sync` (not merged) |
| 3–9 | Not started | |

- `main` = `dcaf74e`. `phase-2-sync` is ahead of `main` with the sync work (timeline contract,
  synthetic audio, sync) and docs (this handoff, `docs/RELATED_WORK.md`). Check with
  `git log --oneline origin/main..origin/phase-2-sync`.
- PR #1 (Phases 0+1) merged by fast-forward; the old phase branches were deleted.
- CI: 167 tests pass on Ubuntu, Windows, macOS for `phase-2-sync`.

### Next steps, in order

1. ~~Set up the Predator~~ **Done (session 2):** specs verified (table above); 167 tests pass locally
   in 31 s with FFmpeg 9.0.1 (full build, has `zscale`) and uv 0.12.15; lint clean.
2. **Real footage.** Karthi records ~1 minute with 2–3 phones at once, with a visible clap at the start
   and at the end. Run `scenefold ingest` then `scenefold sync` on it. First real check of both phases.
3. **Finish Phase 2** on `phase-2-sync`:
   - Evaluation script: sync error in ms against ground truth (the claps; a small subset of the Jiku
     mobile video dataset, which has sample-accurate sync ground truth; see ROADMAP research notes).
   - Windowed check: measure each pair's offset in ~10 s windows (confidence cross-check and clock
     drift estimate).
   - Repetitive music cases (a wrong match one beat off should be rejected, not used).
   - Then PR `phase-2-sync` → `main`, fast-forward merge once CI is green, delete the branch.
4. **Phase 3, synced viewer** → first demo `v0.1.0`.

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
  peak ÷ best peak outside ±100 ms (threshold 2.0; unrelated audio scores ~1.05–1.14, real matches
  23+ on synthetic data). Weighted least squares over all pairs, iteratively dropping pairs off by more
  than 20 ms; clips placed through other clips; unplaceable clips get a plain reason, never forced.
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
