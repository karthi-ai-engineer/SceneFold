"""What the event itself holds: `data/<event_id>/knowledge.sqlite`.

Everything before this is per clip. This is where the clips meet: moments timed inside each one are
put on the shared clock, and the ones that land together are the same thing happening, caught by
several phones. That is what makes an event more than one person's account of it.

Three ideas, kept apart on purpose:

- an **event** is something that happened at a time on the shared clock;
- **evidence** is one clip's part in it: the moment it caught, what it was showing, how good its
  picture was;
- a **conflict** is two clips' accounts of one event disagreeing, with a type and either a
  resolution or an honest "unresolved".

Nothing is asserted without evidence, and every row carries the clip it came from, so any claim
later phases make can be traced back to the footage.

SQLite because a story needs to ask questions of this ("what happened between 2:10 and 2:40",
"which clips saw the confetti"), and a pile of JSON answers those badly.
"""

import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path

SCHEMA_VERSION = 1
KNOWLEDGE_NAME = "knowledge.sqlite"

# Conflict types, from the roadmap. The first four are about what the cameras could see; the last
# is about sound reaching them at different times, which sync measures rather than guesses.
NOT_IN_VIEW = "not in view"
OCCLUDED = "occluded"
READ_DIFFERENTLY = "different interpretation"
MODEL_ERROR = "suspected model error"
SOUND_DELAY = "audio/visual delay"
UNRESOLVED = "unresolved"

SCHEMA = """
CREATE TABLE IF NOT EXISTS about (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS clips (
    clip_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    offset_s REAL,
    heard_late_s REAL,
    drift_ppm REAL,
    duration_s REAL
);
CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    t_master_s REAL NOT NULL,
    kind TEXT NOT NULL,          -- sound, picture, or both
    strength REAL NOT NULL,      -- 0 to 1, the strongest witness
    witnesses INTEGER NOT NULL,  -- how many clips caught it
    recording INTEGER NOT NULL,  -- how many were filming at the time, caught it or not
    summary TEXT                 -- what the best-placed clip was showing, when anything said so
);
CREATE TABLE IF NOT EXISTS evidence (
    event_id TEXT NOT NULL REFERENCES events(event_id) ON DELETE CASCADE,
    clip_id TEXT NOT NULL REFERENCES clips(clip_id),
    t_local_s REAL NOT NULL,     -- where it sits in that clip's own seconds
    kind TEXT NOT NULL,
    strength REAL NOT NULL,
    summary TEXT,                -- what that clip was showing around then
    picture_score REAL,          -- how much its picture was worth looking at
    PRIMARY KEY (event_id, clip_id, kind)
);
CREATE TABLE IF NOT EXISTS conflicts (
    event_id TEXT NOT NULL REFERENCES events(event_id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    explanation TEXT NOT NULL,
    resolved_by TEXT REFERENCES clips(clip_id),  -- NULL means unresolved
    reason TEXT NOT NULL,
    PRIMARY KEY (event_id, kind)
);
CREATE INDEX IF NOT EXISTS events_by_time ON events(t_master_s);
CREATE INDEX IF NOT EXISTS evidence_by_clip ON evidence(clip_id);
"""


@dataclass
class Evidence:
    """One clip's part in an event."""

    clip_id: str
    t_local_s: float
    kind: str
    strength: float
    summary: str | None = None
    picture_score: float | None = None


@dataclass
class Conflict:
    """Two accounts of one event that do not agree."""

    kind: str
    explanation: str
    resolved_by: str | None = None  # the clip whose account is taken, None when unresolved
    reason: str = UNRESOLVED


@dataclass
class Event:
    """Something that happened at one moment on the shared clock."""

    event_id: str
    t_master_s: float
    kind: str
    strength: float
    recording: int  # clips that were filming then, whether or not they caught it
    summary: str | None = None
    evidence: list[Evidence] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)

    @property
    def witnesses(self) -> int:
        return len(self.evidence)


def open_store(event_dir: Path) -> sqlite3.Connection:
    """The event's knowledge, ready to use; created on first open."""
    event_dir.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(event_dir / KNOWLEDGE_NAME)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.executescript(SCHEMA)
    db.execute(
        "INSERT INTO about (key, value) VALUES ('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(SCHEMA_VERSION),),
    )
    db.commit()
    return db


def replace_all(db: sqlite3.Connection, clips: list[dict], events: list[Event]) -> None:
    """Write a fresh picture of the event, in one go: either all of it lands or none of it does."""
    with db:  # one transaction: a half-written event is worse than an old one
        db.execute("DELETE FROM conflicts")
        db.execute("DELETE FROM evidence")
        db.execute("DELETE FROM events")
        db.execute("DELETE FROM clips")
        db.executemany(
            "INSERT INTO clips (clip_id, name, offset_s, heard_late_s, drift_ppm, duration_s) "
            "VALUES (:clip_id, :name, :offset_s, :heard_late_s, :drift_ppm, :duration_s)",
            clips,
        )
        for event in events:
            db.execute(
                "INSERT INTO events (event_id, t_master_s, kind, strength, witnesses, recording, "
                "summary) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    event.event_id,
                    event.t_master_s,
                    event.kind,
                    event.strength,
                    event.witnesses,
                    event.recording,
                    event.summary,
                ),
            )
            db.executemany(
                "INSERT INTO evidence (event_id, clip_id, t_local_s, kind, strength, summary, "
                "picture_score) VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        event.event_id,
                        one.clip_id,
                        one.t_local_s,
                        one.kind,
                        one.strength,
                        one.summary,
                        one.picture_score,
                    )
                    for one in event.evidence
                ],
            )
            db.executemany(
                "INSERT INTO conflicts (event_id, kind, explanation, resolved_by, reason) "
                "VALUES (?, ?, ?, ?, ?)",
                [
                    (event.event_id, c.kind, c.explanation, c.resolved_by, c.reason)
                    for c in event.conflicts
                ],
            )


def events_between(db: sqlite3.Connection, t_start_s: float, t_end_s: float) -> list[Event]:
    """Everything known to have happened in a stretch of the shared clock, in order."""
    rows = db.execute(
        "SELECT * FROM events WHERE t_master_s >= ? AND t_master_s < ? ORDER BY t_master_s",
        (t_start_s, t_end_s),
    ).fetchall()
    return [_event(db, row) for row in rows]


def all_events(db: sqlite3.Connection) -> list[Event]:
    rows = db.execute("SELECT * FROM events ORDER BY t_master_s").fetchall()
    return [_event(db, row) for row in rows]


def events_with_conflicts(db: sqlite3.Connection) -> list[Event]:
    """Only the events whose witnesses disagree: what a person should look at first."""
    rows = db.execute(
        "SELECT e.* FROM events e JOIN conflicts c ON c.event_id = e.event_id "
        "GROUP BY e.event_id ORDER BY e.t_master_s"
    ).fetchall()
    return [_event(db, row) for row in rows]


def counts(db: sqlite3.Connection) -> dict[str, int]:
    """How much is known, in round numbers."""
    one = lambda sql: db.execute(sql).fetchone()[0]  # noqa: E731
    return {
        "clips": one("SELECT COUNT(*) FROM clips"),
        "events": one("SELECT COUNT(*) FROM events"),
        "corroborated": one("SELECT COUNT(*) FROM events WHERE witnesses > 1"),
        "evidence": one("SELECT COUNT(*) FROM evidence"),
        "conflicts": one("SELECT COUNT(*) FROM conflicts"),
        "unresolved": one("SELECT COUNT(*) FROM conflicts WHERE resolved_by IS NULL"),
    }


def load_store(event_dir: Path) -> sqlite3.Connection | None:
    """The event's knowledge if it has any yet, otherwise None."""
    if not (event_dir / KNOWLEDGE_NAME).exists():
        return None
    return open_store(event_dir)


def _event(db: sqlite3.Connection, row: sqlite3.Row) -> Event:
    with closing(db.execute("SELECT * FROM evidence WHERE event_id = ?", (row["event_id"],))) as e:
        evidence = [
            Evidence(
                clip_id=r["clip_id"],
                t_local_s=r["t_local_s"],
                kind=r["kind"],
                strength=r["strength"],
                summary=r["summary"],
                picture_score=r["picture_score"],
            )
            for r in e.fetchall()
        ]
    with closing(db.execute("SELECT * FROM conflicts WHERE event_id = ?", (row["event_id"],))) as c:
        conflicts = [
            Conflict(
                kind=r["kind"],
                explanation=r["explanation"],
                resolved_by=r["resolved_by"],
                reason=r["reason"],
            )
            for r in c.fetchall()
        ]
    return Event(
        event_id=row["event_id"],
        t_master_s=row["t_master_s"],
        kind=row["kind"],
        strength=row["strength"],
        recording=row["recording"],
        summary=row["summary"],
        evidence=evidence,
        conflicts=conflicts,
    )
