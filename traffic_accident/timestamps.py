"""Monotonic clip-relative timestamps, retaining explicit fallback provenance."""

import math


class TimestampClock:
    """Prefer decoder presentation times; estimate with FPS when unavailable."""

    def __init__(self, fps: float) -> None:
        self.fps = fps
        self.previous: float | None = None
        self.origin_ms: float | None = None
        self.fallback_count = 0
        self.decoder_count = 0

    def stamp(self, frame_index: int, decoder_ms: float) -> tuple[float, str]:
        """Normalize the first frame to zero; reject repeated/backward/invalid times."""
        valid = math.isfinite(decoder_ms) and decoder_ms >= 0
        if frame_index == 0:
            self.origin_ms = decoder_ms if valid else None
            timestamp = 0.0
            source = "decoder" if valid else "fps_fallback"
        else:
            candidate = (decoder_ms - self.origin_ms) / 1000 if valid and self.origin_ms is not None else None
            if candidate is not None and self.previous is not None and candidate > self.previous + 1e-9:
                timestamp, source = candidate, "decoder"
            else:
                timestamp = max(frame_index / self.fps, (self.previous or 0.0) + 1 / self.fps)
                source = "fps_fallback"
        self.previous = timestamp
        if source == "decoder":
            self.decoder_count += 1
        else:
            self.fallback_count += 1
        return timestamp, source
