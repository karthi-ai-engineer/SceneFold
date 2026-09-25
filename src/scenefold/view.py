"""Watch an event's clips together: a small web server for the browser viewer in `web/`.

`scenefold view <event>` serves one event workspace on this computer only (127.0.0.1):

    GET /                       the viewer page, web/index.html
    GET /<path>                 the viewer's other files in web/ (.html .js .mjs .css .svg .json
                                .png .ico); nothing outside that folder
    GET /api/timeline           data/<event>/timeline.json, as `scenefold sync` wrote it
    GET /api/manifest           data/<event>/manifest.json, as `scenefold ingest` wrote it
    GET /api/cut                data/<event>/cut.json, the shot list `scenefold cut` wrote
    GET /api/story              data/<event>/story.json, the cited account `scenefold story` wrote
    GET /api/people             data/<event>/people.json, who `scenefold identify` matched up
    GET /api/positions          data/<event>/positions.json, where `scenefold map` put the phones
    GET /api/events?from=&to=   what `scenefold fuse` knows happened in that stretch of the shared
                                clock (seconds; the whole event when they are left out), each
                                moment with its evidence and any conflict
    GET /media/<clip_id>.mp4    that clip's working video, with Range support so the browser can
                                seek
    GET /film.mp4               data/<event>/cut.mp4, the finished film, with the same Range support

Files are read again on every request, so running sync again shows up when the page reloads.
Anything that isn't there gets a 404 whose body explains why: {"error": "<plain explanation>"}.
Requests addressed to any host name other than 127.0.0.1 or localhost get a 403, so other web
sites can't reach the viewer through their own domain names.
"""

import errno
import json
import math
import os
import re
import sqlite3
import webbrowser
from contextlib import closing, suppress
from dataclasses import asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import BinaryIO
from urllib.parse import parse_qs, unquote, urlsplit

from scenefold.cut import CUT_NAME, FILM_NAME
from scenefold.identity import PEOPLE_NAME
from scenefold.knowledge import KNOWLEDGE_NAME, Event, events_between
from scenefold.manifest import MANIFEST_NAME, ManifestError, load_manifest, normalize_event_id
from scenefold.positions import POSITIONS_NAME
from scenefold.story import STORY_NAME
from scenefold.timeline import TIMELINE_NAME, TimelineError, load_timeline

HOST = "127.0.0.1"  # never reachable from other computers
DEFAULT_PORT = 8765
EXTRA_PORTS = 20  # when the port is taken, how many of the next ports are tried
CHUNK_BYTES = 64 * 1024
WEB_DIR = Path(__file__).resolve().parents[2] / "web"  # Scenefold runs from a source checkout

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".json": "application/json",
    ".png": "image/png",
    ".ico": "image/x-icon",
}
API_FILES = {  # route -> (file in the event folder, the command that writes it)
    "/api/timeline": (TIMELINE_NAME, "scenefold sync"),
    "/api/manifest": (MANIFEST_NAME, "scenefold ingest"),
    "/api/cut": (CUT_NAME, "scenefold cut"),
    "/api/story": (STORY_NAME, "scenefold story"),
    "/api/people": (PEOPLE_NAME, "scenefold identify"),
    "/api/positions": (POSITIONS_NAME, "scenefold map"),
}
# The film is one video of the whole event, not one clip, so it gets a name of its own.
FILM_PATH = "/film.mp4"
# The event store is a database, not a file to hand over: this route answers questions of it.
EVENTS_PATH = "/api/events"
NO_STORE = f"this event has no {KNOWLEDGE_NAME} yet; run `scenefold fuse`"

_MEDIA_PATH = re.compile(r"/media/([0-9a-f]{12})\.mp4")
_BYTE_RANGE = re.compile(r"\s*bytes\s*=\s*(\d*)\s*-\s*(\d*)\s*", re.IGNORECASE)
# a browser that no longer wants the rest of a video just closes the connection
_DISCONNECTED = (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)


class ViewError(Exception):
    """The viewer cannot start: bad event name, no synced event, no viewer page, or no free port."""


class _NotFound(Exception):
    """Answer 404; the message says why."""


class _BadQuery(Exception):
    """The request asked for something that makes no sense, like `from=soon`; answer 400."""


class _RangeNotSatisfiable(Exception):
    """The Range asked for starts past the end of the file."""


def _byte_range(header: str | None, size: int) -> tuple[int, int] | None:
    """The first and last byte a Range header asks for, or None to send the whole file.

    Understands one range in the forms `bytes=a-b`, `bytes=a-` and `bytes=-n` (the last n bytes).
    Anything else, such as several ranges, is ignored, as HTTP allows. Raises _RangeNotSatisfiable
    when the range holds no byte of the file.
    """
    match = _BYTE_RANGE.fullmatch(header) if header else None
    if match is None or match.groups() == ("", ""):
        return None
    first, last = match.groups()
    if not first:  # the last n bytes
        wanted = int(last)
        if wanted == 0 or size == 0:
            raise _RangeNotSatisfiable
        return max(0, size - wanted), size - 1
    start = int(first)
    if last and int(last) < start:  # a malformed range is ignored, not refused
        return None
    if start >= size:
        raise _RangeNotSatisfiable
    end = min(int(last), size - 1) if last else size - 1
    return start, end


def _static_file(web_dir: Path, url_path: str) -> Path:
    """The file in the web folder that a URL path names, or _NotFound.

    `web_dir` must already be resolved. Anything that could climb out of it (`..`, backslashes,
    drive letters, also when percent-encoded) is refused before the path is even looked up.
    """
    not_found = _NotFound(f"the viewer has no file at {url_path}")
    relative = unquote(url_path).removeprefix("/") or "index.html"
    parts = relative.split("/")
    if any(char in relative for char in "\\:\0") or any(p in ("", ".", "..") for p in parts):
        raise not_found
    try:
        file = (web_dir / relative).resolve()
    except (OSError, ValueError) as exc:
        raise not_found from exc
    if not file.is_relative_to(web_dir) or not file.is_file():
        raise not_found
    if file.suffix.lower() not in CONTENT_TYPES:
        raise not_found
    return file


def _clip_video(event_dir: Path, clip_id: str) -> Path:
    """A clip's working video, as the manifest names it, or _NotFound."""
    try:
        manifest = load_manifest(event_dir)
    except ManifestError as exc:
        raise _NotFound(str(exc)) from exc
    clip = manifest.find(clip_id) if manifest else None
    if clip is None:
        raise _NotFound(f"this event has no clip {clip_id}")
    if clip.proxy is None:
        raise _NotFound(f"clip {clip_id} has no working video; ingest could not process it")
    video = (event_dir / clip.proxy.video).resolve()
    if not video.is_relative_to(event_dir.resolve()) or not video.is_file():
        raise _NotFound(
            f"the working video of clip {clip_id} is missing; run `scenefold ingest` again"
        )
    return video


def _asked_span(query: str) -> tuple[float, float]:
    """The stretch of the shared clock a request asks about, in seconds.

    No `from` means from the start of the event, no `to` means to the end of it, so a request
    without either gets everything that is known.
    """
    asked = parse_qs(query)
    span = []
    for name, whole_event in (("from", float("-inf")), ("to", float("inf"))):
        given = (asked.get(name) or [""])[-1].strip()
        if not given:
            span.append(whole_event)
            continue
        try:
            span.append(float(given))
        except ValueError as exc:
            raise _BadQuery(f"{name}={given} is not a number of seconds") from exc
    return span[0], span[1]


def _asked_for_conflicts(query: str) -> bool:
    """Whether a request asked only for the moments the clips disagree about."""
    given = (parse_qs(query).get("conflicts") or [""])[-1].strip().lower()
    return given in ("1", "true", "yes")


def _known_events(event_dir: Path, t_start_s: float, t_end_s: float) -> list[Event]:
    """What the event store knows happened in that stretch, or _NotFound.

    The store is opened read-only (`mode=ro`): the viewer only ever shows what `scenefold fuse`
    found, and a page in a browser must not be able to change it.
    """
    store = (event_dir / KNOWLEDGE_NAME).resolve()  # a URI needs the whole path
    if not store.is_file():
        raise _NotFound(NO_STORE)
    try:
        with closing(sqlite3.connect(f"{store.as_uri()}?mode=ro", uri=True)) as db:
            db.row_factory = sqlite3.Row
            return events_between(db, t_start_s, t_end_s)
    except sqlite3.Error as exc:
        raise _NotFound(
            f"{KNOWLEDGE_NAME} cannot be read ({exc}); run `scenefold fuse` again"
        ) from exc


def _names_this_computer(host: str | None) -> bool:
    """False when a request's Host header names another site.

    A web page can point its own domain name at 127.0.0.1 ("DNS rebinding") to read the viewer's
    videos, but the browser still sends that domain as the Host, so such requests are refused.
    """
    if host is None:
        return True  # only browsers matter here, and they always send it
    try:
        return urlsplit(f"//{host}").hostname in (HOST, "localhost")
    except ValueError:
        return False


class _Handler(BaseHTTPRequestHandler):
    server: "ViewerServer"
    protocol_version = "HTTP/1.1"  # keep connections open across the many range requests

    def do_GET(self) -> None:
        self._respond()

    def do_HEAD(self) -> None:
        self._respond()

    def handle(self) -> None:
        with suppress(*_DISCONNECTED):  # browsers cancel range requests all the time
            super().handle()

    def log_message(self, *args: object) -> None:
        pass  # no line per request: a playing video makes hundreds

    def _respond(self) -> None:
        if not _names_this_computer(self.headers.get("Host")):
            self._send_error(HTTPStatus.FORBIDDEN, f"this viewer only answers at {self.server.url}")
            return
        path, _, query = self.path.partition("?")
        path = path.split("#", 1)[0]
        try:
            if path in API_FILES:
                name, command = API_FILES[path]
                missing = f"this event's {name} is missing or unreadable; run `{command}`"
                self._send_file(self.server.event_dir / name, "application/json", missing)
            elif path == EVENTS_PATH:
                self._send_events(query.split("#", 1)[0])
            elif path.startswith("/media/"):
                match = _MEDIA_PATH.fullmatch(path)
                if match is None:
                    raise _NotFound(f"{path} is not a clip video; use /media/<clip_id>.mp4")
                self._send_video(_clip_video(self.server.event_dir, match.group(1)))
            elif path == FILM_PATH:
                film = self.server.event_dir / FILM_NAME
                self._send_video(film, "this event has no film yet; run `scenefold cut`")
            elif path.startswith("/api/"):
                known = ", ".join([*API_FILES, EVENTS_PATH])
                raise _NotFound(f"there is no {path}; try {known}")
            else:
                file = _static_file(self.server.web_dir, path)
                missing = f"the viewer has no file at {path}"
                self._send_file(file, CONTENT_TYPES[file.suffix.lower()], missing)
        except _NotFound as exc:
            self._send_error(HTTPStatus.NOT_FOUND, str(exc))
        except _BadQuery as exc:
            self._send_error(HTTPStatus.BAD_REQUEST, str(exc))

    def _send_error(self, status: HTTPStatus, message: str) -> None:
        body = json.dumps({"error": message}).encode()
        self._start(status, "application/json", len(body))
        self._write(body)

    def _send_events(self, query: str) -> None:
        """The moments in the asked-for stretch, each with its evidence and any conflict.

        `conflicts=1` narrows it to the ones whose witnesses disagree, which is what a person
        looking for where the footage argues with itself actually wants.
        """
        t_start_s, t_end_s = _asked_span(query)
        events = _known_events(self.server.event_dir, t_start_s, t_end_s)
        if _asked_for_conflicts(query):
            events = [event for event in events if event.conflicts]
        answer = {
            # The bounds as asked, with null for "as far as the event goes": JSON has no word for
            # forever, and a browser refuses to read the one Python would write (Infinity).
            "from_s": t_start_s if math.isfinite(t_start_s) else None,
            "to_s": t_end_s if math.isfinite(t_end_s) else None,
            "count": len(events),
            "conflicts_only": _asked_for_conflicts(query),
            "events": [asdict(event) | {"witnesses": event.witnesses} for event in events],
        }
        body = json.dumps(answer).encode()
        self._start(HTTPStatus.OK, "application/json", len(body))
        self._write(body)

    def _send_file(self, file: Path, content_type: str, missing: str) -> None:
        try:
            body = file.read_bytes()
        except OSError as exc:
            raise _NotFound(missing) from exc
        self._start(HTTPStatus.OK, content_type, len(body))
        self._write(body)

    def _send_video(self, video: Path, missing: str | None = None) -> None:
        try:
            file = video.open("rb")
        except OSError as exc:
            raise _NotFound(missing or f"cannot open {video.name}: {exc.strerror}") from exc
        with file:
            size = os.fstat(file.fileno()).st_size
            headers = {"Accept-Ranges": "bytes"}
            try:
                span = _byte_range(self.headers.get("Range"), size)
            except _RangeNotSatisfiable:
                headers["Content-Range"] = f"bytes */{size}"
                self._start(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE, "video/mp4", 0, headers)
                return
            status, (first, last) = HTTPStatus.OK, (0, size - 1)
            if span is not None:
                status, (first, last) = HTTPStatus.PARTIAL_CONTENT, span
                headers["Content-Range"] = f"bytes {first}-{last}/{size}"
            self._start(status, "video/mp4", last - first + 1, headers)
            if self.command != "HEAD":
                file.seek(first)
                self._stream(file, last - first + 1)

    def _stream(self, file: BinaryIO, length: int) -> None:
        while length > 0:
            chunk = file.read(min(CHUNK_BYTES, length))
            if not chunk:  # the file shrank while we sent it: the client can't trust this answer
                self.close_connection = True
                return
            self.wfile.write(chunk)
            length -= len(chunk)

    def _start(
        self, status: HTTPStatus, content_type: str, length: int, headers: dict | None = None
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")  # a new sync or ingest shows on reload
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()

    def _write(self, body: bytes) -> None:
        if self.command != "HEAD":
            self.wfile.write(body)


class ViewerServer(ThreadingHTTPServer):
    """Serves one event workspace and the viewer page, on this computer only."""

    daemon_threads = True  # an open browser tab never keeps Scenefold from exiting
    # On Windows, two servers that both set SO_REUSEADDR can listen on the same port, so a second
    # viewer would share the first one's port instead of moving on. Elsewhere it only lets a
    # restarted viewer take its port back at once.
    allow_reuse_address = os.name != "nt"

    def __init__(self, port: int, event_dir: Path, web_dir: Path) -> None:
        self.event_dir = event_dir
        self.web_dir = web_dir.resolve()
        super().__init__((HOST, port), _Handler)

    @property
    def url(self) -> str:
        return f"http://{HOST}:{self.server_port}/"


def make_server(
    event_dir: Path, port: int = DEFAULT_PORT, web_dir: Path | None = None
) -> ViewerServer:
    """A server for one synced event workspace, listening but not yet answering.

    Port 0 picks any free port; a taken port moves on to the next free one of the following
    EXTRA_PORTS. Call `serve_forever()` to answer requests.
    """
    event_dir = Path(event_dir)
    web_dir = Path(web_dir) if web_dir is not None else WEB_DIR
    _check_workspace(event_dir)
    page = web_dir / "index.html"
    if not page.is_file():
        raise ViewError(
            f"the viewer page {page} is missing; run Scenefold from its source checkout "
            "(`uv run scenefold view`)"
        )
    if not 0 <= port <= 65535:
        raise ViewError(f"{port} is not a port number; use 1 to 65535, or 0 for any free port")

    last = port if port == 0 else min(port + EXTRA_PORTS, 65535)
    for candidate in range(port, last + 1):
        try:
            return ViewerServer(candidate, event_dir, web_dir)
        except OSError as exc:
            if not _port_taken(exc):
                raise ViewError(f"cannot listen on {HOST}:{candidate}: {exc}") from exc
    tried = f"port {port} is" if last == port else f"ports {port} to {last} are all"
    raise ViewError(f"{tried} in use; choose another port")


def _check_workspace(event_dir: Path) -> None:
    try:
        manifest = load_manifest(event_dir)
        timeline = load_timeline(event_dir)
    except (ManifestError, TimelineError) as exc:
        raise ViewError(str(exc)) from exc
    if manifest is None:
        raise ViewError(
            f"no ingested event at {event_dir}; run `scenefold ingest`, then `scenefold sync` first"
        )
    if timeline is None:
        raise ViewError(f"no timeline at {event_dir}; run `scenefold sync` first")


def _port_taken(exc: OSError) -> bool:
    # Windows answers "access denied" for ports it reserves (e.g. for Hyper-V) and for ports
    # another program holds in some ways
    return exc.errno == errno.EADDRINUSE or isinstance(exc, PermissionError)


def serve(
    event_name: str,
    data_dir: str | Path = "data",
    port: int = DEFAULT_PORT,
    open_browser: bool = True,
) -> None:
    """Serve an event's viewer and open it in the browser; returns after Ctrl+C."""
    try:
        event_id = normalize_event_id(event_name)
    except ValueError as exc:
        raise ViewError(str(exc)) from exc
    with make_server(Path(data_dir) / event_id, port) as server:
        print(f"Viewer for {event_id}: {server.url}")
        print("Press Ctrl+C to stop.", flush=True)
        if open_browser:
            webbrowser.open(server.url)
        with suppress(KeyboardInterrupt):  # Ctrl+C is the way to stop; `with` closes the server
            server.serve_forever()
