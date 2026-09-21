"""Tests for the viewer's web server (no FFmpeg): a fake event workspace served on a free port."""

import json
import socket
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from email.message import Message
from pathlib import Path
from urllib.parse import quote

import pytest

from scenefold import view
from scenefold.cli import main
from scenefold.cut import FILM_NAME, ONLY_ANGLE, CutSettings, Film, Shot, save_cut
from scenefold.identity import PEOPLE_NAME
from scenefold.knowledge import (
    KNOWLEDGE_NAME,
    NOT_IN_VIEW,
    Conflict,
    Event,
    Evidence,
    open_store,
    replace_all,
)
from scenefold.manifest import (
    Clip,
    ClipStatus,
    Manifest,
    Proxy,
    ProxySettings,
    Source,
    now,
    save_manifest,
)
from scenefold.story import STORY_NAME, Line, Story, save_story
from scenefold.timeline import ClipPlacement, SyncSettings, Timeline, save_timeline
from scenefold.view import ViewError, ViewerServer, make_server, serve

EVENT = "test-event"
CLIP_ID = "0123456789ab"  # has a working video
FAILED_ID = "ba9876543210"  # ingest could not process it, so it has no working video
ESCAPING_ID = "eeeeeeeeeeee"  # a manifest entry pointing outside the event folder
VIDEO = bytes(range(256)) * 1000  # every byte tells where it sits, so ranges can be checked
SIZE = len(VIDEO)
VIDEO_URL = f"/media/{CLIP_ID}.mp4"
FILM = bytes(range(256)) * 400  # the finished cut, likewise
FILM_SIZE = len(FILM)
PAGE = "<!doctype html><title>Scenefold viewer · 視聴</title>"
SCRIPT = "export const clock = 0;"


def clip(clip_id: str, video: str | None) -> Clip:
    proxy = None
    if video is not None:
        proxy = Proxy(video=video, width=320, height=240, fps=30, duration_s=10, settings_key="k")
    return Clip(
        clip_id=clip_id,
        status=ClipStatus.OK if video else ClipStatus.FAILED,
        ingested_at=now(),
        source=Source(
            name=f"{clip_id}.mp4",
            path=f"originals/{clip_id}.mp4",
            size_bytes=SIZE,
            modified_ns=0,
            sha256=clip_id * 5 + "0000",
        ),
        proxy=proxy,
    )


@pytest.fixture
def workspace(tmp_path) -> Path:
    """data/test-event with a manifest, a timeline, and one working video of known bytes."""
    event_dir = tmp_path / "data" / EVENT
    (event_dir / "proxies").mkdir(parents=True)
    (event_dir / "proxies" / f"{CLIP_ID}.mp4").write_bytes(VIDEO)
    (tmp_path / "outside.mp4").write_bytes(b"not part of the event")
    clips = [
        clip(CLIP_ID, f"proxies/{CLIP_ID}.mp4"),
        clip(FAILED_ID, None),
        clip(ESCAPING_ID, "../../outside.mp4"),
    ]
    manifest = Manifest(
        event_id=EVENT,
        created_at=now(),
        updated_at=now(),
        proxy_settings=ProxySettings(),
        clips=clips,
    )
    save_manifest(event_dir, manifest)
    placements = [
        ClipPlacement(clip_id=CLIP_ID, name="a.mp4", placed=True, offset_s=0.0, duration_s=10.0),
        ClipPlacement(
            clip_id=FAILED_ID, name="b.mp4", placed=False, duration_s=0.0, reason="no video"
        ),
    ]
    timeline = Timeline(
        event_id=EVENT,
        created_at=now(),
        settings=SyncSettings(),
        duration_s=10.0,
        clips=placements,
        pairs=[],
    )
    save_timeline(event_dir, timeline)
    return event_dir


@pytest.fixture
def film(workspace) -> Film:
    """The same event once it has been cut: cut.json and a film of known bytes."""
    cut = Film(
        event_id=EVENT,
        created_at=now(),
        settings=CutSettings(),
        duration_s=10.0,
        start_s=0.0,
        audio_clip_id=CLIP_ID,
        audio_reason="the only clip on the clock",
        audio_end_s=10.0,
        shots=[
            Shot(
                clip_id=CLIP_ID,
                name="a.mp4",
                start_s=0.0,
                end_s=10.0,
                local_start_s=0.0,
                local_end_s=10.0,
                score=0.5,
                reason=ONLY_ANGLE,
            )
        ],
    )
    save_cut(workspace, cut)
    (workspace / FILM_NAME).write_bytes(FILM)
    return cut


@pytest.fixture
def knowledge(workspace) -> list[Event]:
    """The same event once `scenefold fuse` has been over it: two moments, the second disputed."""
    events = [
        Event(
            event_id=f"{EVENT}-00001",
            t_master_s=1.5,
            kind="sound",
            strength=0.8,
            recording=1,
            summary="the lights drop",
            evidence=[
                Evidence(
                    clip_id=CLIP_ID,
                    t_local_s=1.5,
                    kind="sound",
                    strength=0.8,
                    summary="the lights drop",
                    picture_score=0.61,
                )
            ],
        ),
        Event(
            event_id=f"{EVENT}-00002",
            t_master_s=6.25,
            kind="picture",
            strength=0.4,
            recording=2,
            summary="confetti over the crowd",
            evidence=[
                Evidence(clip_id=CLIP_ID, t_local_s=6.25, kind="picture", strength=0.4),
                Evidence(clip_id=FAILED_ID, t_local_s=6.2, kind="picture", strength=0.3),
            ],
            conflicts=[
                Conflict(
                    kind=NOT_IN_VIEW,
                    explanation="2 of 3 clips filming at this moment caught it",
                    reason="no camera had a clearly better view",
                )
            ],
        ),
    ]
    clips = [
        {
            "clip_id": clip_id,
            "name": name,
            "offset_s": 0.0,
            "heard_late_s": 0.0,
            "drift_ppm": None,
            "duration_s": 10.0,
        }
        for clip_id, name in ((CLIP_ID, "a.mp4"), (FAILED_ID, "b.mp4"))
    ]
    db = open_store(workspace)
    replace_all(db, clips, events)
    db.close()
    return events


@pytest.fixture
def story(workspace) -> Story:
    """The account `scenefold story` writes: two sentences, one of them disputed, one left out."""
    written = Story(
        event_id=EVENT,
        created_at=now(),
        model="test-model",
        lines=[
            Line(
                t_master_s=1.5,
                text="The lights drop.",
                cites=[f"{EVENT}-00001"],
                clips=[CLIP_ID],
            ),
            Line(
                t_master_s=6.25,
                text="Confetti falls over the crowd.",
                cites=[f"{EVENT}-00002"],
                clips=[CLIP_ID, FAILED_ID],
                disputed=True,
            ),
        ],
        dropped=["The crowd sang along — points at no moment"],
    )
    save_story(workspace, written)
    return written


@pytest.fixture
def web(tmp_path) -> Path:
    """A stand-in for the repo's web/ folder, with a secret page right next to it."""
    folder = tmp_path / "web"
    (folder / "lib").mkdir(parents=True)
    (folder / "index.html").write_text(PAGE, encoding="utf-8")
    (folder / "app.js").write_text(SCRIPT, encoding="utf-8")
    (folder / "lib" / "clock.mjs").write_text(SCRIPT, encoding="utf-8")
    (folder / "lib" / "style.css").write_text("body { margin: 0 }", encoding="utf-8")
    (folder / "notes.txt").write_text("not a type the viewer serves", encoding="utf-8")
    (tmp_path / "secret.html").write_text("outside the web folder", encoding="utf-8")
    return folder


@pytest.fixture
def viewer(workspace, web) -> Iterator[ViewerServer]:
    """The server answering in a background thread, shut down after the test."""
    server = make_server(workspace, port=0, web_dir=web)
    # a short poll makes shutdown quick; the default waits up to half a second per test
    thread = threading.Thread(target=server.serve_forever, args=(0.01,), daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join()


# no proxy settings from the environment or the Windows registry: always talk to the server itself
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def fetch(
    server: ViewerServer, path: str, method: str = "GET", headers: dict | None = None
) -> tuple[int, Message, bytes]:
    """(status, headers, body); the path is sent exactly as written, not tidied like a browser."""
    request = urllib.request.Request(server.url[:-1] + path, method=method, headers=headers or {})
    try:
        with _opener.open(request, timeout=10) as response:
            return response.status, response.headers, response.read()
    except urllib.error.HTTPError as error:
        with error:
            return error.code, error.headers, error.read()


def error_of(body: bytes) -> str:
    return json.loads(body)["error"]


def test_the_viewer_listens_on_this_computer_only(viewer):
    assert viewer.server_address[0] == "127.0.0.1"
    assert viewer.url == f"http://127.0.0.1:{viewer.server_port}/"


def test_other_sites_cannot_reach_it_through_their_own_domain_names(viewer):
    for host in ("evil.example", "evil.example:8765", "127.0.0.1.evil.example"):
        status, _, body = fetch(viewer, "/api/manifest", headers={"Host": host})
        assert status == 403, host
        assert viewer.url in error_of(body)
    for host in (f"localhost:{viewer.server_port}", "LOCALHOST", "127.0.0.1:5173"):
        status, _, _ = fetch(viewer, "/api/manifest", headers={"Host": host})
        assert status == 200, host


def test_the_page_and_its_files_are_served_with_their_types(viewer):
    status, headers, body = fetch(viewer, "/")
    assert status == 200
    assert headers["Content-Type"] == "text/html; charset=utf-8"
    assert headers["Cache-Control"] == "no-store"
    assert body.decode("utf-8") == PAGE
    expected = {
        "/index.html": "text/html",
        "/app.js": "text/javascript",
        "/lib/clock.mjs": "text/javascript",
        "/lib/style.css": "text/css",
    }
    for path, content_type in expected.items():
        status, headers, body = fetch(viewer, path)
        assert status == 200, path
        assert headers["Content-Type"].split(";")[0] == content_type
        assert int(headers["Content-Length"]) == len(body) > 0


def test_a_query_string_does_not_change_the_file(viewer):
    status, _, body = fetch(viewer, "/app.js?v=2")
    assert status == 200 and body.decode() == SCRIPT


def test_other_files_are_not_found(viewer):
    for path in ("/notes.txt", "/missing.js", "/lib/", "/lib", "/api/other"):
        status, headers, body = fetch(viewer, path)
        assert status == 404, path
        assert headers["Content-Type"] == "application/json"
        assert error_of(body)


@pytest.mark.parametrize(
    "path",
    [
        "/../x",
        "/%2e%2e/%2e%2e/pyproject.toml",
        "/..%5c..%5cpyproject.toml",
        "/../secret.html",
        "/%2e%2e/secret.html",
        "/%2E%2E%2Fsecret.html",
        "/..%5csecret.html",
        "/..\\secret.html",
        "/lib/../../secret.html",
        "/lib/%2e%2e/%2e%2e/secret.html",
        "/%2e%2e/data/test-event/manifest.json",
        "//secret.html",
        "/C:/Windows/win.ini",
        "/app.js%00.html",
    ],
)
def test_nothing_outside_the_web_folder_is_served(viewer, path):
    status, _, body = fetch(viewer, path)
    assert status == 404
    assert b"outside the web folder" not in body


def test_an_absolute_path_is_not_served(viewer, tmp_path):
    secret = tmp_path / "secret.html"
    for path in ("/" + quote(secret.as_posix()), "/" + quote(str(secret))):
        status, _, _ = fetch(viewer, path)
        assert status == 404, path


def test_the_timeline_and_manifest_are_served_as_written(viewer, workspace):
    for path, name in (("/api/timeline", "timeline.json"), ("/api/manifest", "manifest.json")):
        status, headers, body = fetch(viewer, path)
        assert status == 200
        assert headers["Content-Type"] == "application/json"
        assert headers["Cache-Control"] == "no-store"
        assert body == (workspace / name).read_bytes()


def test_a_missing_timeline_is_a_404_that_says_what_to_run(viewer, workspace):
    (workspace / "timeline.json").unlink()
    status, headers, body = fetch(viewer, "/api/timeline")
    assert status == 404
    assert headers["Content-Type"] == "application/json"
    assert "scenefold sync" in error_of(body)


def test_a_whole_video_is_sent_when_no_range_is_asked(viewer):
    status, headers, body = fetch(viewer, VIDEO_URL)
    assert status == 200
    assert headers["Accept-Ranges"] == "bytes"
    assert headers["Content-Type"] == "video/mp4"
    assert int(headers["Content-Length"]) == SIZE
    assert "Content-Range" not in headers
    assert body == VIDEO


@pytest.mark.parametrize(
    ("asked", "first", "last"),
    [
        ("bytes=0-99", 0, 99),
        ("bytes=1000-1999", 1000, 1999),
        ("bytes=250000-", 250000, SIZE - 1),
        ("bytes=-500", SIZE - 500, SIZE - 1),
        ("bytes=255900-999999", 255900, SIZE - 1),  # an end past the file stops at its last byte
        ("bytes=-999999", 0, SIZE - 1),  # more than the whole file is the whole file
        ("bytes=7-7", 7, 7),
    ],
)
def test_each_range_form_returns_exactly_those_bytes(viewer, asked, first, last):
    status, headers, body = fetch(viewer, VIDEO_URL, headers={"Range": asked})
    assert status == 206
    assert headers["Content-Range"] == f"bytes {first}-{last}/{SIZE}"
    assert int(headers["Content-Length"]) == last - first + 1
    assert headers["Accept-Ranges"] == "bytes"
    assert headers["Content-Type"] == "video/mp4"
    assert body == VIDEO[first : last + 1]


@pytest.mark.parametrize("asked", [f"bytes={SIZE}-", "bytes=300000-300099", "bytes=-0"])
def test_a_range_past_the_end_is_refused(viewer, asked):
    status, headers, body = fetch(viewer, VIDEO_URL, headers={"Range": asked})
    assert status == 416
    assert headers["Content-Range"] == f"bytes */{SIZE}"
    assert headers["Accept-Ranges"] == "bytes"
    assert body == b""


@pytest.mark.parametrize("asked", ["bytes=500-100", "bytes=0-9,20-29", "items=0-9", "bytes=x-"])
def test_a_range_it_does_not_understand_gets_the_whole_video(viewer, asked):
    status, _, body = fetch(viewer, VIDEO_URL, headers={"Range": asked})
    assert status == 200
    assert body == VIDEO


def test_head_sends_the_same_headers_without_a_body(viewer, story, knowledge):
    paths = ("/", "/app.js", "/api/timeline", "/api/manifest", "/api/story", "/api/events")
    for path in (*paths, VIDEO_URL, "/missing.js"):
        got_status, got_headers, _ = fetch(viewer, path)
        status, headers, body = fetch(viewer, path, method="HEAD")
        assert (status, body) == (got_status, b""), path
        assert headers["Content-Type"] == got_headers["Content-Type"]
        assert headers["Content-Length"] == got_headers["Content-Length"]

    status, headers, body = fetch(viewer, VIDEO_URL, "HEAD", {"Range": "bytes=100-199"})
    assert (status, body) == (206, b"")
    assert headers["Content-Range"] == f"bytes 100-199/{SIZE}"
    assert headers["Content-Length"] == "100"


@pytest.mark.parametrize(
    "path",
    [
        "/media/ffffffffffff.mp4",  # no such clip
        "/media/0123456789AB.mp4",  # clip IDs are lowercase
        "/media/0123456789a.mp4",
        "/media/0123456789abc.mp4",
        f"/media/{CLIP_ID}.mov",
        f"/media/{CLIP_ID}",
        f"/media/proxies/{CLIP_ID}.mp4",
        "/media/..%2fmanifest.json",
        f"/media/{FAILED_ID}.mp4",  # no working video
        f"/media/{ESCAPING_ID}.mp4",  # the manifest may not point outside the event folder
    ],
)
def test_only_known_clips_with_a_working_video_are_served(viewer, path):
    status, headers, body = fetch(viewer, path)
    assert status == 404
    assert headers["Content-Type"] == "application/json"
    assert error_of(body)


def test_a_working_video_missing_on_disk_says_to_ingest_again(viewer, workspace):
    (workspace / "proxies" / f"{CLIP_ID}.mp4").unlink()
    status, _, body = fetch(viewer, VIDEO_URL)
    assert status == 404
    assert "scenefold ingest" in error_of(body)


def test_the_cut_and_the_film_are_served_as_written(viewer, film, workspace):
    status, headers, body = fetch(viewer, "/api/cut")
    assert status == 200
    assert headers["Content-Type"] == "application/json"
    assert body == (workspace / "cut.json").read_bytes()
    assert json.loads(body)["shots"][0]["reason"] == ONLY_ANGLE

    status, headers, body = fetch(viewer, "/film.mp4")
    assert status == 200
    assert headers["Content-Type"] == "video/mp4"
    assert headers["Accept-Ranges"] == "bytes"
    assert int(headers["Content-Length"]) == FILM_SIZE
    assert body == FILM


@pytest.mark.parametrize(
    ("asked", "first", "last"),
    [
        ("bytes=0-99", 0, 99),
        ("bytes=1000-", 1000, FILM_SIZE - 1),
        ("bytes=-400", FILM_SIZE - 400, FILM_SIZE - 1),
    ],
)
def test_the_film_can_be_seeked_like_a_clip(viewer, film, asked, first, last):
    status, headers, body = fetch(viewer, "/film.mp4", headers={"Range": asked})
    assert status == 206
    assert headers["Content-Range"] == f"bytes {first}-{last}/{FILM_SIZE}"
    assert body == FILM[first : last + 1]

    status, headers, body = fetch(viewer, "/film.mp4", "HEAD", {"Range": asked})
    assert (status, body) == (206, b"")
    assert headers["Content-Length"] == str(last - first + 1)


def test_an_event_that_was_never_cut_says_so(viewer):
    for path in ("/api/cut", "/film.mp4"):
        status, headers, body = fetch(viewer, path)
        assert status == 404, path
        assert headers["Content-Type"] == "application/json"
        assert "scenefold cut" in error_of(body), path


def test_a_cut_without_its_film_still_serves_the_shot_list(viewer, film, workspace):
    (workspace / FILM_NAME).unlink()
    assert fetch(viewer, "/api/cut")[0] == 200
    status, _, body = fetch(viewer, "/film.mp4")
    assert status == 404
    assert "no film yet" in error_of(body)


def test_the_story_is_served_as_written(viewer, story, workspace):
    status, headers, body = fetch(viewer, "/api/story")
    assert status == 200
    assert headers["Content-Type"] == "application/json"
    assert headers["Cache-Control"] == "no-store"
    assert body == (workspace / STORY_NAME).read_bytes()
    told = json.loads(body)
    assert [line["text"] for line in told["lines"]] == [line.text for line in story.lines]
    assert told["lines"][1]["disputed"] is True
    assert told["dropped"] == story.dropped


def test_who_was_matched_is_served_as_written(viewer, workspace):
    (workspace / PEOPLE_NAME).write_text(
        json.dumps({
            "event_id": EVENT,
            "people": [{"person_id": "person-01", "wearing": "a red top", "clips": [CLIP_ID]}],
            "found": {"people": 1, "across_angles": 0, "sure": 0},
        }),
        encoding="utf-8",
    )  # fmt: skip
    status, headers, body = fetch(viewer, "/api/people")
    assert status == 200
    assert headers["Content-Type"] == "application/json"
    assert json.loads(body)["people"][0]["wearing"] == "a red top"


def test_an_event_nobody_has_been_matched_in_says_what_to_run(viewer):
    status, _, body = fetch(viewer, "/api/people")
    assert status == 404
    assert "scenefold identify" in error_of(body)


def test_an_event_without_an_account_says_what_to_run(viewer):
    status, headers, body = fetch(viewer, "/api/story")
    assert status == 404
    assert headers["Content-Type"] == "application/json"
    assert "scenefold story" in error_of(body)


def test_the_moments_of_a_stretch_come_with_their_evidence_and_conflicts(viewer, knowledge):
    status, headers, body = fetch(viewer, "/api/events?from=5&to=7")
    assert status == 200
    assert headers["Content-Type"] == "application/json"
    answer = json.loads(body)
    assert (answer["from_s"], answer["to_s"], answer["count"]) == (5.0, 7.0, 1)
    (moment,) = answer["events"]
    assert moment["event_id"] == f"{EVENT}-00002"
    assert moment["t_master_s"] == 6.25
    assert moment["witnesses"] == 2
    assert moment["recording"] == 2
    assert [e["clip_id"] for e in moment["evidence"]] == [CLIP_ID, FAILED_ID]
    assert moment["conflicts"][0]["kind"] == NOT_IN_VIEW
    assert moment["conflicts"][0]["resolved_by"] is None
    assert "clearly better view" in moment["conflicts"][0]["reason"]


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("", [1.5, 6.25]),  # no span at all: the whole event
        ("?from=0&to=10", [1.5, 6.25]),
        ("?from=1.5&to=6.25", [1.5]),  # the start is included, the end is not
        ("?from=2", [6.25]),  # from here to the end of the event
        ("?to=2", [1.5]),  # from the beginning of the event to here
        ("?from=&to=", [1.5, 6.25]),  # empty is the same as leaving them out
        ("?from=7&to=9", []),
        ("?from=9&to=1", []),  # nothing at all, rather than everything
    ],
)
def test_the_moments_asked_for_are_the_ones_in_that_stretch(viewer, knowledge, query, expected):
    status, _, body = fetch(viewer, f"/api/events{query}")
    assert status == 200
    answer = json.loads(body)
    assert [moment["t_master_s"] for moment in answer["events"]] == expected
    assert answer["count"] == len(expected)


def test_the_whole_event_is_the_span_when_none_is_asked_for(viewer, knowledge):
    status, _, body = fetch(viewer, "/api/events")
    answer = json.loads(body)
    assert status == 200
    assert (answer["from_s"], answer["to_s"]) == (None, None)  # JSON cannot say "for ever"


def test_a_span_without_an_end_is_still_readable_json(viewer, knowledge):
    # "inf" is a number to Python but not to JSON, and a browser refuses a body holding Infinity
    status, _, body = fetch(viewer, "/api/events?from=-inf&to=inf")
    assert status == 200
    assert b"Infinity" not in body
    answer = json.loads(body)
    assert (answer["from_s"], answer["to_s"], answer["count"]) == (None, None, 2)


def test_a_span_that_is_not_a_number_of_seconds_is_refused(viewer, knowledge):
    for query, said in (("?from=soon", "from=soon"), ("?to=later", "to=later")):
        status, headers, body = fetch(viewer, f"/api/events{query}")
        assert status == 400, query
        assert headers["Content-Type"] == "application/json"
        assert said in error_of(body)


def test_asking_the_store_never_changes_it(viewer, knowledge, workspace):
    store = workspace / KNOWLEDGE_NAME
    before = store.read_bytes()
    for path in ("/api/events", "/api/events?from=0&to=3"):
        assert fetch(viewer, path)[0] == 200
    assert store.read_bytes() == before
    assert not list(workspace.glob(f"{KNOWLEDGE_NAME}-*"))  # no journal left beside it either


def test_an_event_nobody_has_fused_says_so(viewer):
    status, headers, body = fetch(viewer, "/api/events")
    assert status == 404
    assert headers["Content-Type"] == "application/json"
    assert "scenefold fuse" in error_of(body)


def test_a_store_that_cannot_be_read_says_so(viewer, workspace):
    (workspace / KNOWLEDGE_NAME).write_bytes(b"not a database")
    status, _, body = fetch(viewer, "/api/events")
    assert status == 404
    assert "scenefold fuse" in error_of(body)


def test_the_viewer_needs_a_synced_event(workspace, web):
    (workspace / "timeline.json").unlink()
    with pytest.raises(ViewError, match="run `scenefold sync` first"):
        make_server(workspace, port=0, web_dir=web)
    (workspace / "manifest.json").unlink()
    with pytest.raises(ViewError, match="scenefold ingest"):
        make_server(workspace, port=0, web_dir=web)


def test_an_unreadable_timeline_is_reported(workspace, web):
    (workspace / "timeline.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(ViewError, match="unreadable"):
        make_server(workspace, port=0, web_dir=web)


def test_the_viewer_needs_its_page(workspace, tmp_path):
    with pytest.raises(ViewError, match="index.html"):
        make_server(workspace, port=0, web_dir=tmp_path / "no-web")


def blocked_port() -> socket.socket:
    """A socket listening on a port, low enough that the next 20 ports exist."""
    while True:
        blocker = socket.socket()
        blocker.bind(("127.0.0.1", 0))
        if blocker.getsockname()[1] <= 65535 - view.EXTRA_PORTS:
            blocker.listen()
            return blocker
        blocker.close()


def test_a_taken_port_moves_on_to_the_next_free_one(workspace, web):
    with blocked_port() as blocker:
        taken = blocker.getsockname()[1]
        with make_server(workspace, port=taken, web_dir=web) as server:
            assert taken < server.server_port <= taken + view.EXTRA_PORTS
            assert server.url == f"http://127.0.0.1:{server.server_port}/"


def test_it_gives_up_when_no_port_is_free(workspace, web, monkeypatch):
    monkeypatch.setattr(view, "EXTRA_PORTS", 0)
    with blocked_port() as blocker, pytest.raises(ViewError, match="in use"):
        make_server(workspace, port=blocker.getsockname()[1], web_dir=web)


def test_a_bad_event_name_is_refused():
    with pytest.raises(ViewError, match="invalid event name"):
        serve("../evil", open_browser=False)


@pytest.fixture
def stopped_by_ctrl_c(web, monkeypatch) -> list[str]:
    """Serve returns as if Ctrl+C was pressed at once; returns the URLs opened in a browser."""
    opened: list[str] = []
    monkeypatch.setattr(view, "WEB_DIR", web)
    monkeypatch.setattr(view.webbrowser, "open", opened.append)

    def press_ctrl_c(self, poll_interval: float = 0.5) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(ViewerServer, "serve_forever", press_ctrl_c)
    return opened


def test_cli_view_serves_until_ctrl_c(workspace, stopped_by_ctrl_c, capsys):
    data_dir = str(workspace.parent)
    assert main(["view", EVENT, "--data-dir", data_dir, "--port", "0"]) == 0
    out = capsys.readouterr().out
    url = out.split(f"Viewer for {EVENT}: ", 1)[1].split()[0]
    assert url.startswith("http://127.0.0.1:") and url.endswith("/")
    assert "Press Ctrl+C to stop." in out
    assert out.rstrip().endswith("stopped")
    assert stopped_by_ctrl_c == [url]


def test_cli_view_can_leave_the_browser_closed(workspace, stopped_by_ctrl_c):
    data_dir = str(workspace.parent)
    assert main(["view", EVENT, "--data-dir", data_dir, "--port", "0", "--no-browser"]) == 0
    assert stopped_by_ctrl_c == []


def test_cli_view_of_an_event_that_was_never_synced(tmp_path, capsys):
    assert main(["view", "match-01", "--data-dir", str(tmp_path), "--no-browser"]) == 2
    err = capsys.readouterr().err
    assert err.startswith("error: ") and "scenefold sync" in err


def test_cli_view_with_a_bad_event_name(tmp_path, capsys):
    assert main(["view", "no/such", "--data-dir", str(tmp_path), "--no-browser"]) == 2
    assert "invalid event name" in capsys.readouterr().err
