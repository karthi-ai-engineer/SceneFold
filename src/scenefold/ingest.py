"""Accept videos into an event workspace.

For each input: keep an untouched copy of the original, record its details in the manifest, and
make the working copy (proxy video + mono WAV) that later stages use.

`ingest()` is the single entry point. The CLI calls it today; an upload page or API can call the
same function later.
"""

import hashlib
import os
import re
import shutil
import stat
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from scenefold import media
from scenefold.manifest import (
    MANIFEST_NAME,
    ORIGINALS_DIR,
    PROXIES_DIR,
    Clip,
    ClipStatus,
    Issue,
    Manifest,
    ManifestError,
    Proxy,
    ProxySettings,
    Source,
    load_manifest,
    normalize_event_id,
    now,
    save_manifest,
)

# Used only to decide whether an unreadable file is a broken video (recorded as failed)
# or just some other file (skipped). Readable files are accepted whatever their extension.
VIDEO_EXTENSIONS = frozenset(
    {".mp4", ".m4v", ".mov", ".qt", ".mkv", ".webm", ".avi", ".wmv", ".flv", ".3gp", ".3g2"}
    | {".mts", ".m2ts", ".ts", ".mpg", ".mpeg", ".vob", ".mxf", ".dv", ".ogv"}
)
IGNORED_FILE_NAMES = frozenset({"thumbs.db", "desktop.ini", ".ds_store"})
SHORT_CLIP_S = 5.0
SILENT_PEAK_DB = -60.0
CLIPPED_FRACTION = 0.001
LOCK_NAME = ".ingest.lock"
_SAFE_SUFFIX = re.compile(r"\.[a-z0-9]{1,8}")


class IngestError(Exception):
    """The run cannot start: bad event name, FFmpeg missing, unreadable manifest, or locked."""


class Outcome(StrEnum):
    ADDED = "added"  # new clip (its status says ok, warning, or failed)
    UPDATED = "updated"  # known clip whose working copy was rebuilt
    UNCHANGED = "unchanged"  # already ingested; nothing to do
    DUPLICATE = "duplicate"  # same content as a clip ingested under another name
    SKIPPED = "skipped"  # not a video
    FAILED = "failed"  # a video that could not be processed
    MISSING = "missing"  # path does not exist


@dataclass
class InputResult:
    path: Path
    outcome: Outcome
    message: str = ""
    clip: Clip | None = None
    save: bool = False  # write `clip` to the manifest


@dataclass
class IngestReport:
    event_id: str
    event_dir: Path
    results: list[InputResult] = field(default_factory=list)

    @property
    def manifest_path(self) -> Path:
        return self.event_dir / MANIFEST_NAME

    def count(self, outcome: Outcome) -> int:
        return sum(result.outcome is outcome for result in self.results)

    @property
    def has_failures(self) -> bool:
        return any(r.outcome in (Outcome.FAILED, Outcome.MISSING) for r in self.results)


Progress = Callable[[int, int, Path, InputResult | None], None]
"""Called with (number, total, path, None) before each input and with the result after it."""


def ingest(
    event_name: str,
    inputs: Iterable[str | Path] | str | Path,
    data_dir: str | Path = "data",
    settings: ProxySettings | None = None,
    progress: Progress | None = None,
) -> IngestReport:
    """Add video files (or folders of them) to an event. Safe to run again on the same inputs."""
    try:
        event_id = normalize_event_id(event_name)
        media.find_tools()
    except (ValueError, media.MediaToolsError) as exc:
        raise IngestError(str(exc)) from exc
    if isinstance(inputs, str | Path):
        inputs = [inputs]
    settings = settings or ProxySettings()
    event_dir = Path(data_dir) / event_id
    report = IngestReport(event_id, event_dir)

    candidates = discover(inputs)
    if not any(path.exists() for path in candidates):
        report.results = [
            InputResult(p, Outcome.MISSING, "file or folder not found") for p in candidates
        ]
        return report

    created = not event_dir.exists()
    try:
        event_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise IngestError(f"cannot create the event folder {event_dir}: {exc}") from exc
    try:
        with _locked(event_dir):
            manifest = _open_manifest(event_dir, event_id, settings)
            for number, path in enumerate(candidates, 1):
                if progress:
                    progress(number, len(candidates), path, None)
                result = _ingest_path(path, event_dir, manifest, settings)
                if result.save and result.clip is not None:
                    manifest.upsert(result.clip)
                    manifest.proxy_settings = settings
                    manifest.updated_at = now()
                    save_manifest(event_dir, manifest)
                report.results.append(result)
                if progress:
                    progress(number, len(candidates), path, result)
    finally:
        if created and event_dir.exists() and not report.manifest_path.exists():
            _remove_tree(event_dir)  # nothing was recorded; don't leave an empty event behind
    return report


def discover(inputs: Iterable[str | Path]) -> list[Path]:
    """Expand folders (recursively) into files, in a stable order, without repeats.

    Paths that don't exist are kept so they can be reported as missing.
    """
    found: list[Path] = []
    seen: set[str] = set()
    for raw in inputs:
        path = Path(raw)
        for candidate in _walk(path) if path.is_dir() else [path]:
            key = os.path.normcase(os.path.abspath(candidate))
            if key not in seen:
                seen.add(key)
                found.append(candidate)
    return found


def _walk(folder: Path) -> Iterator[Path]:
    if _is_workspace(folder):
        return
    for root, dirs, files in os.walk(folder):
        root_path = Path(root)
        dirs[:] = sorted(
            d for d in dirs if not d.startswith(".") and not _is_workspace(root_path / d)
        )
        for name in sorted(files):
            if name.startswith(".") or name.lower() in IGNORED_FILE_NAMES:
                continue  # hidden and system files, e.g. macOS "._IMG_0001.MOV" shadows
            yield root_path / name


def _is_workspace(folder: Path) -> bool:
    return (folder / MANIFEST_NAME).is_file() and (
        (folder / ORIGINALS_DIR).is_dir() or (folder / PROXIES_DIR).is_dir()
    )


def _inside_workspace(path: Path) -> bool:
    parent = path.parent
    return _is_workspace(parent) or (
        parent.name in (ORIGINALS_DIR, PROXIES_DIR) and _is_workspace(parent.parent)
    )


@contextmanager
def _locked(event_dir: Path) -> Iterator[None]:
    lock = event_dir / LOCK_NAME
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise IngestError(
            f"another ingest is already running for this event. If none is running, "
            f"delete {lock} and try again."
        ) from None
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(f"{os.getpid()}\n")
        yield
    finally:
        lock.unlink(missing_ok=True)


def _open_manifest(event_dir: Path, event_id: str, settings: ProxySettings) -> Manifest:
    try:
        manifest = load_manifest(event_dir)
    except ManifestError as exc:
        raise IngestError(f"{exc}\nFix or move that file, then run ingest again.") from exc
    if manifest is None:
        created = now()
        return Manifest(
            event_id=event_id, created_at=created, updated_at=created, proxy_settings=settings
        )
    manifest.event_id = event_id
    return manifest


def _ingest_path(
    path: Path, event_dir: Path, manifest: Manifest, settings: ProxySettings
) -> InputResult:
    """Ingest one file; never raises, so one bad file can't stop the batch."""
    try:
        return _ingest_file(path, event_dir, manifest, settings)
    except OSError as exc:
        return InputResult(path, Outcome.FAILED, f"file access error: {exc}")
    except Exception as exc:  # a bug; report it and keep going
        return InputResult(path, Outcome.FAILED, f"unexpected error ({type(exc).__name__}: {exc})")


def _ingest_file(
    path: Path, event_dir: Path, manifest: Manifest, settings: ProxySettings
) -> InputResult:
    if not path.exists():
        return InputResult(path, Outcome.MISSING, "file or folder not found")
    if not path.is_file():
        return InputResult(path, Outcome.SKIPPED, "not a regular file")
    if _inside_workspace(path):
        return InputResult(path, Outcome.SKIPPED, "a Scenefold working file, not an input")
    file_stat = path.stat()
    if file_stat.st_size == 0:
        return InputResult(path, Outcome.SKIPPED, "empty file")

    source_path = str(path.resolve())
    known = next(
        (
            clip
            for clip in manifest.clips
            if clip.source.path == source_path
            and clip.source.size_bytes == file_stat.st_size
            and clip.source.modified_ns == file_stat.st_mtime_ns
        ),
        None,
    )
    if known and _is_complete(known, event_dir, settings):
        return InputResult(path, Outcome.UNCHANGED, "already ingested", known)

    try:
        info = media.probe(path.resolve())
    except media.ProbeError as exc:
        if path.suffix.lower() not in VIDEO_EXTENSIONS:
            return InputResult(path, Outcome.SKIPPED, "not a video file")
        issue = Issue(
            code="unreadable",
            message=f"could not read the video; it may be incomplete or damaged ({exc})",
        )
        source = _source(path, file_stat, _hash_file(path), None)
        return _failed(path, source, None, issue)
    if info.video is None:
        reason = "audio only, no video track" if info.audio else "no video track"
        return InputResult(path, Outcome.SKIPPED, reason)
    if info.is_image:
        return InputResult(path, Outcome.SKIPPED, "a still image, not a video")

    source = _source(path, file_stat, _hash_file(path), info)
    clip_id = source.sha256[:12]
    existing = manifest.find(clip_id)
    if existing and _is_complete(existing, event_dir, settings):
        if existing.source.name != path.name:
            message = f"same video as {existing.source.name} (clip {clip_id})"
            return InputResult(path, Outcome.DUPLICATE, message, existing)
        # Same video under the same name: it was moved or touched. Remember where it is now.
        existing.source.path = source.path
        existing.source.modified_ns = source.modified_ns
        return InputResult(path, Outcome.UNCHANGED, "already ingested", existing, save=True)

    original_rel = f"{ORIGINALS_DIR}/{clip_id}{_safe_suffix(path)}"
    original = event_dir / original_rel
    if problem := _disk_space_problem(event_dir, original, file_stat.st_size, info):
        return InputResult(path, Outcome.FAILED, problem)
    if not _store_original(path, original, file_stat.st_size):
        return InputResult(path, Outcome.FAILED, "the file changed while it was being copied")

    proxies = event_dir / PROXIES_DIR
    proxies.mkdir(exist_ok=True)
    video_rel, audio_rel = f"{PROXIES_DIR}/{clip_id}.mp4", f"{PROXIES_DIR}/{clip_id}.wav"
    video_tmp, audio_tmp = proxies / f"{clip_id}.partial.mp4", proxies / f"{clip_id}.partial.wav"
    try:
        made = media.make_proxy(original, info, settings, video_tmp, audio_tmp)
        os.replace(video_tmp, event_dir / video_rel)
        if made.has_audio:
            os.replace(audio_tmp, event_dir / audio_rel)
        else:
            (event_dir / audio_rel).unlink(missing_ok=True)
    except media.ProxyError as exc:
        for stale in (event_dir / video_rel, event_dir / audio_rel):
            stale.unlink(missing_ok=True)
        issue = Issue(code="convert_failed", message=f"could not make the working copy: {exc}")
        return _failed(path, source, original_rel, issue)
    finally:
        video_tmp.unlink(missing_ok=True)
        audio_tmp.unlink(missing_ok=True)

    issues = made.issues + _quality_issues(info, made)
    clip = Clip(
        clip_id=clip_id,
        status=ClipStatus.WARNING if issues else ClipStatus.OK,
        issues=issues,
        ingested_at=now(),
        original=original_rel,
        source=source,
        proxy=Proxy(
            video=video_rel,
            audio=audio_rel if made.has_audio else None,
            width=made.width,
            height=made.height,
            fps=settings.fps,
            duration_s=made.duration_s,
            settings_key=settings.key(),
            audio_mean_db=made.audio_mean_db,
            audio_peak_db=made.audio_peak_db,
        ),
    )
    outcome = Outcome.UPDATED if existing else Outcome.ADDED
    return InputResult(path, outcome, "", clip, save=True)


def _source(
    path: Path, file_stat: os.stat_result, sha256: str, info: media.MediaInfo | None
) -> Source:
    return Source(
        name=path.name,
        path=str(path.resolve()),
        size_bytes=file_stat.st_size,
        modified_ns=file_stat.st_mtime_ns,
        sha256=sha256,
        container=info.container if info else None,
        duration_s=info.duration_s if info else None,
        recorded_at=info.recorded_at if info else None,
        video=info.video if info else None,
        audio=info.audio if info else None,
    )


def _failed(path: Path, source: Source, original_rel: str | None, issue: Issue) -> InputResult:
    clip = Clip(
        clip_id=source.sha256[:12],
        status=ClipStatus.FAILED,
        issues=[issue],
        ingested_at=now(),
        original=original_rel,
        source=source,
    )
    return InputResult(path, Outcome.FAILED, issue.message, clip, save=True)


def _is_complete(clip: Clip, event_dir: Path, settings: ProxySettings) -> bool:
    """True when the clip needs no work: processed with the current settings, files in place."""
    if clip.status is ClipStatus.FAILED or clip.proxy is None or clip.original is None:
        return False
    if clip.proxy.settings_key != settings.key():
        return False
    original = event_dir / clip.original
    if not original.is_file() or original.stat().st_size != clip.source.size_bytes:
        return False
    outputs = [clip.proxy.video] + ([clip.proxy.audio] if clip.proxy.audio else [])
    return all((event_dir / output).is_file() for output in outputs)


def _quality_issues(info: media.MediaInfo, made: media.ProxyResult) -> list[Issue]:
    issues = []
    if info.audio is None:
        issues.append(
            Issue(code="no_audio", message="no audio track; this clip can't be synced by sound")
        )
    elif made.has_audio and made.audio_peak_db is not None and made.audio_peak_db <= SILENT_PEAK_DB:
        issues.append(
            Issue(
                code="silent_audio",
                message="the audio is silent; this clip can't be synced by sound",
            )
        )
    elif made.has_audio and (made.audio_clipped_fraction or 0) > CLIPPED_FRACTION:
        issues.append(
            Issue(
                code="audio_clipping",
                message=f"the audio is distorted ({made.audio_clipped_fraction:.1%} of samples at "
                "full volume); sync may be less accurate",
            )
        )
    if made.duration_s < SHORT_CLIP_S:
        issues.append(
            Issue(
                code="short_clip",
                message=f"only {made.duration_s:.1f} s long; clips under {SHORT_CLIP_S:.0f} s "
                "may not sync reliably",
            )
        )
    expected = info.duration_s
    if info.incomplete or (expected and expected - made.duration_s > max(1.0, 0.1 * expected)):
        of_total = f" of {expected:.1f} s" if expected else ""
        issues.append(
            Issue(
                code="incomplete_file",
                message=f"the file seems cut short or damaged; the working copy has "
                f"{made.duration_s:.1f} s{of_total}",
            )
        )
    return issues


def _hash_file(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _safe_suffix(path: Path) -> str:
    suffix = path.suffix.lower()
    return suffix if _SAFE_SUFFIX.fullmatch(suffix) else ""


def _disk_space_problem(
    event_dir: Path, original: Path, size: int, info: media.MediaInfo
) -> str | None:
    wav_bytes = int((info.duration_s or 0) * 48_000 * 2)
    needed = (0 if original.is_file() else size) + size // 2 + wav_bytes + 50 * 2**20
    free = shutil.disk_usage(event_dir).free
    if free < needed:
        gib = 2**30
        return f"not enough disk space: needs about {needed / gib:.1f} GB, {free / gib:.1f} GB free"
    return None


def _store_original(src: Path, dest: Path, size: int) -> bool:
    """Copy the original into the workspace once, read-only. False if the source changed."""
    if dest.is_file() and dest.stat().st_size == size:
        return True
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():  # an incomplete earlier copy
        os.chmod(dest, stat.S_IWRITE | stat.S_IREAD)
        dest.unlink()
    partial = dest.with_name(dest.name + ".partial")
    try:
        shutil.copyfile(src, partial)
        if partial.stat().st_size != size:
            return False
        src_stat = src.stat()
        os.utime(partial, ns=(src_stat.st_atime_ns, src_stat.st_mtime_ns))
        os.replace(partial, dest)
    finally:
        partial.unlink(missing_ok=True)
    os.chmod(dest, stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)
    return True


def _remove_tree(folder: Path) -> None:
    def make_writable_and_retry(func, target, _exc):
        try:
            os.chmod(target, stat.S_IWRITE | stat.S_IREAD)
            func(target)
        except OSError:
            pass

    shutil.rmtree(folder, onexc=make_writable_and_retry)
