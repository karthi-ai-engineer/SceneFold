# Scenefold

Many phone videos of one event → one synced timeline, one cited story, one edited film.
**Start of every session: read `docs/HANDOFF.md` first** (what happened before, how we work, next steps).
Vision and firm principles: `docs/PROJECT_BRIEF.md`. Phases and current status: `docs/ROADMAP.md`.

## Git rules (firm)

- Commits, PRs, and pushes credit **only** the GitHub account `karthi-ai-engineer`.
  Never add `Co-Authored-By: Claude…`, "Generated with Claude Code", or any Claude/Anthropic credit.
- Commit identity: `Karthi AI Engineer <296384397+karthi-ai-engineer@users.noreply.github.com>`.
- After cloning on a new machine, run once before committing:
  ```sh
  git config user.name "Karthi AI Engineer"
  git config user.email "296384397+karthi-ai-engineer@users.noreply.github.com"
  git config core.hooksPath .githooks
  ```
  `.githooks/commit-msg` blocks commits with any other identity or with Claude credits.
  Do not bypass it with `--no-verify`.
- Machines may have other git identities or `gh` accounts (work laptops). Check `git var GIT_AUTHOR_IDENT`
  and `gh auth status` before the first commit or push on a new machine.

## Commands

Needs FFmpeg (with ffprobe) and `uv` on PATH.

```sh
uv sync                                   # create .venv and install
uv run pytest                             # all tests (generates tiny test videos with FFmpeg)
uv run ruff format src tests tools && uv run ruff check src tests tools
uv run scenefold ingest <event> <videos or folders>   # writes data/<event>/
uv run scenefold sync <event>                         # writes data/<event>/timeline.json
uv run scenefold sync <event> --sound-only            # skip the picture pass (distance), faster
uv run scenefold evaluate <event> <truth.json>        # sync error against ground truth moments
uv run scenefold cut <event>                          # writes data/<event>/cut.json and cut.mp4
uv run scenefold view <event>                         # the synced viewer at http://127.0.0.1:8765
node --test web/tests/sync.test.mjs                   # the viewer's timing rules (Node 18+)
uv run --with playwright python tools/check_viewer.py <event>   # viewer sync, headless Chrome
uv run python tools/jiku.py jiku-saf                  # real phone clips + sync ground truth (786 MB)
```

## Working style

- Work one phase at a time, in the order of `docs/ROADMAP.md`. Update its status table when a step lands.
- `docs/simulation.html` animates what is built so far on imaginary data (Karthi uses it to follow the
  project). When a pipeline step lands or its behaviour changes, add or update its section there
  (instructions at the top of the file), keeping its maths in line with `src/scenefold/`.
- Branches: one per phase (`phase-N-name`), started from `main` after the previous phase merged;
  merged back by fast-forward once CI is green, then deleted.
- Never commit footage, generated media, model weights, or `.env`.
- Before ending a session (machines change often): add an entry to the session log in `docs/HANDOFF.md`,
  update its "Where things stand" section, then commit and push so the next machine can continue.
