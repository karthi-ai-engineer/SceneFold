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
| Acer Predator (NVIDIA, reportedly RTX 3060; **specs not yet verified**) | Next ~5 days (chosen) | Check `nvidia-smi`, RAM, disk first |
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

---

## Where things stand (end of session 1, 2026-09-17)

| Phase | Status | Where |
|---|---|---|
| 0 Foundation | Done | `main` |
| 1 Ingest | Done on generated clips; **never run on real phone footage** | `main` |
| 2 Sync | **In progress**: core works on synthetic audio | branch `phase-2-sync` (not merged) |
| 3–9 | Not started | |

- `main` = `dcaf74e`. `phase-2-sync` = 3 commits ahead (timeline contract, synthetic audio, sync).
- PR #1 (Phases 0+1) merged by fast-forward; the old phase branches were deleted.
- CI: 167 tests pass on Ubuntu, Windows, macOS for `phase-2-sync`.

### Next steps, in order

1. **Set up the Predator** (commands above). Verify its specs and that 167 tests pass.
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

- Predator's real specs.
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
- **Windows:** `uv` from winget isn't on PATH until a new terminal; Python `write_text` writes CRLF
  (`.gitattributes` normalizes to LF).
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
