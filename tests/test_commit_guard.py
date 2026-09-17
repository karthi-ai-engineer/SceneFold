"""Tests for the commit guard (.githooks/commit-msg), run in throwaway git repositories."""

import os
import shutil
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[1] / ".githooks" / "commit-msg"
MAINTAINER_NAME = "Karthi AI Engineer"
MAINTAINER_EMAIL = "296384397+karthi-ai-engineer@users.noreply.github.com"
WORK_EMAIL = "someone@work-laptop.example.co.jp"

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


@dataclass
class Repo:
    path: Path
    env: dict[str, str]
    changes: int = 0

    def git(self, *args: str, extra_env: dict[str, str] | None = None):
        return subprocess.run(
            ["git", *args],
            cwd=self.path,
            env=self.env | (extra_env or {}),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    def head(self) -> str:
        return self.git("rev-parse", "HEAD").stdout.strip()

    def commit(self, message: str, *args: str, extra_env: dict[str, str] | None = None):
        """Change a file and commit it with `message` (passed through a file to keep newlines)."""
        self.changes += 1
        (self.path / "notes.txt").write_text(f"change {self.changes}\n", encoding="utf-8")
        assert self.git("add", "notes.txt").returncode == 0
        message_file = self.path.parent / f"message-{self.changes}.txt"
        message_file.write_text(message, encoding="utf-8")
        return self.git("commit", "-q", "-F", str(message_file), *args, extra_env=extra_env)


@pytest.fixture
def repo(tmp_path) -> Repo:
    """A new repository using a copy of the hook, isolated from this machine's git settings."""
    empty_config = tmp_path / "empty.gitconfig"
    empty_config.write_text("", encoding="utf-8")
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env |= {"GIT_CONFIG_GLOBAL": str(empty_config), "GIT_CONFIG_NOSYSTEM": "1"}

    hooks = tmp_path / "hooks"
    hooks.mkdir()
    hook = hooks / "commit-msg"
    shutil.copyfile(HOOK, hook)
    hook.chmod(hook.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    work = tmp_path / "repo"
    work.mkdir()
    repo = Repo(work, env)
    for args in (
        ["init", "-q", "-b", "main"],
        ["config", "user.name", MAINTAINER_NAME],
        ["config", "user.email", MAINTAINER_EMAIL],
        ["config", "commit.gpgsign", "false"],
        ["config", "core.hooksPath", hooks.as_posix()],
    ):
        assert repo.git(*args).returncode == 0
    # the starting commit skips the hook, so every test exercises it from a known state
    first = repo.commit("chore: start", "--no-verify")
    assert first.returncode == 0, first.stderr
    return repo


def assert_blocked(repo: Repo, reason: str, message: str, *args: str, extra_env=None) -> None:
    """Try the commit and check it was refused for `reason` and HEAD did not move."""
    before = repo.head()
    result = repo.commit(message, *args, extra_env=extra_env)
    assert result.returncode != 0
    assert "commit blocked" in result.stderr
    assert reason in result.stderr
    assert repo.head() == before


def test_hook_names_the_maintainer_account():
    assert f'ALLOWED_EMAIL="{MAINTAINER_EMAIL}"' in HOOK.read_text(encoding="utf-8")


def test_clean_commit_by_the_maintainer_is_allowed(repo):
    before = repo.head()
    result = repo.commit(
        "feat(sync): add pair measurement\n\nMeasures offsets between two clips.\n"
    )
    assert result.returncode == 0, result.stderr
    assert repo.head() != before
    author = repo.git("log", "-1", "--format=%an <%ae>").stdout.strip()
    assert author == f"{MAINTAINER_NAME} <{MAINTAINER_EMAIL}>"


@pytest.mark.parametrize(
    "message",
    [
        "docs: update CLAUDE.md\n",
        "chore: turn off attribution in .claude/settings.json\n",
        "docs: move the brief\n\nCLAUDE.md now points to docs/PROJECT_BRIEF.md.\n",
    ],
)
def test_messages_that_only_mention_claude_files_are_allowed(repo, message):
    before = repo.head()
    result = repo.commit(message)
    assert result.returncode == 0, result.stderr
    assert repo.head() != before


def test_another_author_email_is_blocked(repo):
    assert repo.git("config", "user.email", WORK_EMAIL).returncode == 0
    assert_blocked(repo, WORK_EMAIL, "feat: work laptop default identity\n")


def test_author_override_is_blocked(repo):
    author = f"--author=Someone Else <{WORK_EMAIL}>"
    assert_blocked(repo, "GIT_AUTHOR_IDENT", "feat: sneaky author\n", author)


def test_another_committer_is_blocked(repo):
    committer = {"GIT_COMMITTER_NAME": "Someone Else", "GIT_COMMITTER_EMAIL": WORK_EMAIL}
    assert_blocked(repo, "GIT_COMMITTER_IDENT", "feat: other committer\n", extra_env=committer)


@pytest.mark.parametrize(
    "message",
    [
        "feat: add sync\n\nCo-Authored-By: Claude Opus 5 <noreply@anthropic.com>\n",
        "feat: add sync\n\nco-authored-by: claude <someone@example.com>\n",
        "feat: add sync\n\nCo-authored-by: Anthropic Helper <helper@example.com>\n",
        "fix: tidy\n\nQuestions go to noreply@anthropic.com\n",
        "docs: readme\n\n🤖 Generated with [Claude Code](https://claude.com/claude-code)\n",
        "docs: readme\n\nGENERATED WITH CLAUDE\n",
    ],
)
def test_ai_credit_in_the_message_is_blocked(repo, message):
    assert_blocked(repo, "credits Claude", message)
