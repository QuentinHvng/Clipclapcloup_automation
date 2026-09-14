"""Cutting one 9:16 clip out of a source video."""
from __future__ import annotations

from pathlib import Path

from .ffmpeg import run_ffmpeg
from .overlay import render_overlay_png

MIN_CLIP_SECONDS = 5

# Keep the whole 16:9 frame and fill the empty space with a blurred copy of
# the same footage. The blur is computed at a tiny resolution and scaled back
# up — visually identical to blurring at full size, and dramatically faster.
BLUR_PAD_FILTER = (
    "split=2[bg][fg];"
    "[bg]scale=192:341,boxblur=8:4,scale=1080:1920[bg];"
    "[fg]scale=1080:1920:force_original_aspect_ratio=decrease[fg];"
    "[bg][fg]overlay=(W-w)/2:(H-h)/2,format=yuv420p,setsar=1"
)

# Fill the screen by cropping to a centered 9:16 window. Nothing is letter-
# boxed, but the edges of the frame are lost.
CROP_FILTER = (
    "crop=ih*9/16:ih,"
    "scale=1080:1920:force_original_aspect_ratio=increase,"
    "crop=1080:1920,setsar=1"
)


def to_seconds(value) -> float:
    """Accept seconds, 'MM:SS' or 'HH:MM:SS'."""
    if isinstance(value, (int, float)):
        return float(value)
    parts = [float(p) for p in str(value).split(":")]
    while len(parts) < 3:
        parts.insert(0, 0.0)
    hours, minutes, seconds = parts
    return hours * 3600 + minutes * 60 + seconds


def to_timecode(seconds: float) -> str:
    total = int(round(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def cut_clip(
    source: Path,
    start,
    end,
    out_path: Path,
    mode: str = "blur-pad",
    overlay_text: str | None = None,
    on_progress=None,
) -> Path:
    """Export [start, end] of `source` as a 1080x1920 TikTok-ready mp4."""
    start_s = to_seconds(start)
    duration = to_seconds(end) - start_s
    if duration < MIN_CLIP_SECONDS:
        raise ValueError(f"A clip of {duration:.0f}s is too short to export.")

    video_filter = CROP_FILTER if mode == "crop" else BLUR_PAD_FILTER
    out_path.parent.mkdir(parents=True, exist_ok=True)

    encode_flags = [
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "20",
        "-c:a", "aac",
        "-b:a", "192k",
        "-movflags", "+faststart",
    ]

    if overlay_text:
        overlay_png = out_path.with_suffix(".overlay.png")
        render_overlay_png(overlay_text, overlay_png)
        args = [
            "-ss", str(start_s),
            "-i", str(source),
            "-loop", "1",
            "-i", str(overlay_png),
            "-filter_complex",
            f"[0:v]{video_filter}[base];[base][1:v]overlay=0:0:format=auto[outv]",
            "-map", "[outv]",
            "-map", "0:a",
            "-t", str(duration),
            *encode_flags,
            str(out_path),
        ]
    else:
        overlay_png = None
        args = [
            "-ss", str(start_s),
            "-i", str(source),
            "-t", str(duration),
            "-vf", video_filter,
            *encode_flags,
            str(out_path),
        ]

    run_ffmpeg(args, duration=duration, on_progress=on_progress)

    if overlay_png is not None:
        overlay_png.unlink(missing_ok=True)
    return out_path
