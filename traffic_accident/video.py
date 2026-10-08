"""Local OpenCV decoding and checked annotated-video writing."""

import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .errors import SetupError
from .timestamps import TimestampClock


def get_cv2() -> Any:
    """Import OpenCV lazily so missing libraries have a setup message."""
    try:
        import cv2
    except (ImportError, OSError) as exc:
        raise SetupError(f"OpenCV is unavailable: {exc}. Activate .venv and install requirements.txt.") from exc
    return cv2


@dataclass(frozen=True)
class SourceInfo:
    path: str
    width: int
    height: int
    metadata_fps: float | None
    effective_fps: float
    fps_is_assumed: bool
    metadata_frame_count: int | None
    estimated_duration_seconds: float | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class VideoFrame:
    index: int
    timestamp_seconds: float
    timestamp_source: str
    image: Any


class VideoReader:
    """Decode each frame once; a first-frame check makes empty/corrupt files fail early."""

    def __init__(self, source: Path, *, fallback_fps: float = 30.0) -> None:
        self.source = source
        self.warnings: list[str] = []
        self.decoded_count = 0
        self.reached_eof = False
        if not source.is_file():
            raise SetupError(f"Input video is missing or is not a file: {source}. Pass a quoted local --source path.")
        try:
            with source.open("rb") as stream:
                if not stream.read(1):
                    raise SetupError(f"Input video is empty: {source}.")
        except OSError as exc:
            raise SetupError(f"Input video is not readable: {source}: {exc}.") from exc
        self.cv2 = get_cv2()
        try:
            self.capture = self.cv2.VideoCapture(str(source))
        except Exception as exc:
            raise SetupError(f"OpenCV could not initialize the video reader for {source}: {exc}.") from exc
        try:
            if not self.capture.isOpened():
                raise SetupError(f"OpenCV cannot open {source}. The video may be corrupt or its codec unsupported; try a valid MP4.")
            ok, image = self.capture.read()
            if not ok or image is None or image.size == 0:
                raise SetupError(f"No frame could be decoded from {source}. Check that the file is an intact supported video.")
            first_ms = self.capture.get(self.cv2.CAP_PROP_POS_MSEC)
            fps = self.capture.get(self.cv2.CAP_PROP_FPS)
            fps_valid = math.isfinite(fps) and fps > 0
            effective = fps if fps_valid else fallback_fps
            if not fps_valid:
                self.warnings.append(
                    f"Source FPS metadata is zero/invalid; assuming {fallback_fps:g} FPS. "
                    "FPS-derived times and output playback duration are estimates; set --fallback-fps if known."
                )
            count = self.capture.get(self.cv2.CAP_PROP_FRAME_COUNT)
            total = int(count) if math.isfinite(count) and count > 0 else None
            if total is None:
                self.warnings.append("Frame-count metadata is unavailable; decoder EOF cannot prove the entire file is intact.")
            height, width = image.shape[:2]
            self.info = SourceInfo(str(source), width, height, fps if fps_valid else None,
                                   effective, not fps_valid, total, total / effective if total else None)
            self.clock = TimestampClock(effective)
            self._first: tuple[Any, float] | None = (image, first_ms)
        except BaseException as exc:
            self.capture.release()
            if isinstance(exc, SetupError) or not isinstance(exc, Exception):
                raise
            raise SetupError(f"OpenCV failed while reading source metadata/first frame: {exc}.") from exc

    def __enter__(self) -> "VideoReader":
        return self

    def __exit__(self, *args: Any) -> None:
        self.capture.release()

    def read_next(self) -> VideoFrame | None:
        """Return a decoded frame, or EOF with an early-stop diagnostic if detectable."""
        if self.reached_eof:
            return None
        if self._first is not None:
            image, raw_ms = self._first
            self._first = None
        else:
            try:
                ok, image = self.capture.read()
                raw_ms = self.capture.get(self.cv2.CAP_PROP_POS_MSEC)
            except Exception as exc:
                raise SetupError(f"Video decoder failed after {self.decoded_count} frames: {exc}.") from exc
            if not ok or image is None or image.size == 0:
                self.reached_eof = True
                total = self.info.metadata_frame_count
                if total is not None and self.decoded_count < total:
                    self.warnings.append(
                        f"Decoder stopped after {self.decoded_count} frames, before metadata count {total}; "
                        "the clip may be truncated/corrupt or its metadata inaccurate. Result is incomplete."
                    )
                return None
        if image.shape[:2] != (self.info.height, self.info.width):
            raise SetupError("Decoded frame dimensions changed mid-video; convert to a constant-resolution video first.")
        timestamp, provenance = self.clock.stamp(self.decoded_count, raw_ms)
        packet = VideoFrame(self.decoded_count, timestamp, provenance, image)
        self.decoded_count += 1
        return packet

    def timestamp_warnings(self) -> list[str]:
        """Summarize fallback counts without logging once per frame."""
        if not self.clock.fallback_count:
            return []
        return [f"{self.clock.fallback_count} frame timestamps used FPS fallback because decoder times were invalid/nonmonotonic; times are estimates."]


def check_source(source: Path, *, fallback_fps: float) -> dict[str, Any]:
    """Open/decode the first frame; this is not a full-file integrity check."""
    with VideoReader(source, fallback_fps=fallback_fps) as reader:
        first = reader.read_next()
        return {**reader.info.as_dict(), "first_frame_decoded": first is not None,
                "check_scope": "metadata_and_first_frame_only", "warnings": reader.warnings + reader.timestamp_warnings()}


class VideoOutput:
    """Preserve source frame count/size/FPS, refuse overwrites, and validate final output."""

    def __init__(self, path: Path, source: Path, info: SourceInfo) -> None:
        self.path = path
        self.info = info
        self.count = 0
        self.cv2 = get_cv2()
        self.writer: Any = None
        self._owns_path = False
        suffix = path.suffix.lower()
        if suffix not in (".mp4", ".avi"):
            raise SetupError("Annotated --output must end in .mp4 (mp4v) or .avi (MJPG).")
        if path.resolve() == source.resolve():
            raise SetupError("Output must be different from the source video; source files are never overwritten.")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb"):
                pass
            self._owns_path = True
            codec = "mp4v" if suffix == ".mp4" else "MJPG"
            self.writer = self.cv2.VideoWriter(str(path), self.cv2.VideoWriter_fourcc(*codec),
                                               info.effective_fps, (info.width, info.height))
            if not self.writer.isOpened():
                raise SetupError(f"OpenCV cannot create output {path}. Check codec support, directory permissions, and free space.")
        except FileExistsError as exc:
            raise SetupError(f"Output already exists: {path}. Choose a new --output path; existing files are preserved.") from exc
        except BaseException as exc:
            self.abort()
            if isinstance(exc, SetupError) or not isinstance(exc, Exception):
                raise
            raise SetupError(f"Cannot create annotated output {path}: {exc}.") from exc

    def __enter__(self) -> "VideoOutput":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if exc_type is not None:
            self.abort()
        else:
            self.finish()

    def write(self, image: Any) -> None:
        """Write every decoded source frame, including frames not sampled for inference."""
        if image.shape[:2] != (self.info.height, self.info.width):
            raise SetupError("Output frame size differs from the source dimensions.")
        try:
            result = self.writer.write(image)
            if result is False:
                raise RuntimeError("VideoWriter reported failure")
            self.count += 1
        except Exception as exc:
            raise SetupError(f"Annotated video write failed at output frame {self.count}: {exc}.") from exc

    def finish(self) -> None:
        """Check a nonempty output, decodable first frame, dimensions, and frame count."""
        try:
            self.writer.release()
            self.writer = None
            if self.count == 0 or self.path.stat().st_size == 0:
                raise RuntimeError("no frames were saved")
            capture = self.cv2.VideoCapture(str(self.path))
            try:
                ok, image = capture.read()
                saved_count = capture.get(self.cv2.CAP_PROP_FRAME_COUNT)
                if not capture.isOpened() or not ok or image is None:
                    raise RuntimeError("saved output cannot be decoded")
                if image.shape[:2] != (self.info.height, self.info.width):
                    raise RuntimeError("saved output dimensions do not match")
                if math.isfinite(saved_count) and saved_count > 0 and int(saved_count) != self.count:
                    raise RuntimeError(f"saved frame count {saved_count:g} differs from {self.count} frames written")
            finally:
                capture.release()
        except BaseException as exc:
            self.abort()
            if not isinstance(exc, Exception):
                raise
            raise SetupError(f"Annotated output validation failed: {exc}. Incomplete output was removed; check disk space/codecs.") from exc

    def abort(self) -> None:
        """Remove only a failed output created by this writer, never an existing file."""
        try:
            if self.writer is not None:
                self.writer.release()
                self.writer = None
            if self._owns_path:
                self.path.unlink(missing_ok=True)
        except Exception as exc:
            raise SetupError(f"Could not clean up failed output {self.path}: {exc}. Inspect/remove the partial file manually.") from exc
