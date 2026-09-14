"""Picking the liveliest stretch of a video, cheaply.

The heuristic: measure loudness (RMS) once per second across the whole audio
track, then take the window with the highest average. Louder, denser audio —
talking over each other, laughing, a crowd, music swelling — is a decent
stand-in for "something is happening", and it costs a single audio decode
rather than a transcription or a vision model.

It is a heuristic and nothing more: it has no idea what is being said or
shown. That is exactly why every clip lands in the library for review instead
of going straight out.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from .ffmpeg import run_ffmpeg

SAMPLE_RATE = 16_000
EDGE_MARGIN_S = 5  # skip intros and outros


def _decode_mono_pcm(source: Path, duration: float | None, on_progress=None) -> np.ndarray:
    raw = run_ffmpeg(
        [
            "-i", str(source),
            "-vn",
            "-ac", "1",
            "-ar", str(SAMPLE_RATE),
            "-f", "s16le",
            "-",
        ],
        duration=duration,
        on_progress=on_progress,
        capture_stdout=True,
    )
    return np.frombuffer(raw, dtype=np.int16)


def find_best_segment(
    source: Path,
    target_duration: float = 75.0,
    source_duration: float | None = None,
    on_progress=None,
) -> tuple[float, float]:
    """Return (start, end) in seconds for the loudest `target_duration`
    window, staying clear of the very beginning and end."""
    samples = _decode_mono_pcm(source, source_duration, on_progress)
    total_seconds = len(samples) / SAMPLE_RATE
    if total_seconds <= target_duration:
        return 0.0, total_seconds

    buckets_count = int(total_seconds)
    trimmed = samples[: buckets_count * SAMPLE_RATE].astype(np.float64)
    per_second = trimmed.reshape(buckets_count, SAMPLE_RATE)
    energy = np.sqrt(np.mean(per_second**2, axis=1))

    low = min(EDGE_MARGIN_S, max(0, buckets_count // 4))
    high = max(low + 1, buckets_count - EDGE_MARGIN_S)
    window = int(target_duration)

    if high - low <= window:
        start = low
    else:
        cumulative = np.cumsum(energy[low:high])
        window_sums = cumulative[window - 1:] - np.concatenate(([0], cumulative[:-window]))
        start = low + int(np.argmax(window_sums))

    end = min(start + target_duration, total_seconds)
    return float(start), float(end)
