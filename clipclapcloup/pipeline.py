"""The whole "YouTube link in, finished clips out" sequence.

Runs as one background job and reports a single progress bar the user can
actually trust: each stage owns a slice of it, so the bar moves forward
steadily instead of restarting at every step.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from . import library
from .captions import build_caption, part_label
from .config import app_data_dir, clips_dir, load_settings
from .jobs import phase_scaler
from .media.clip import MIN_CLIP_SECONDS, cut_clip, to_timecode
from .media.download import download_source, fetch_metadata
from .media.highlight import find_best_segment

# How much of the overall progress bar each stage gets.
STAGE_BOUNDS = {
    "metadata": (0, 3),
    "download": (3, 45),
    "analyze": (45, 55),
    "cut": (55, 93),
    "caption": (93, 99),
    "cleanup": (99, 100),
}


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", text or "").strip("-").lower()
    return slug[:48] or "clip"


def _unique_folder(root: Path, slug: str) -> Path:
    folder = root / slug
    suffix = 2
    while folder.exists() and any(folder.glob("*.mp4")):
        folder = root / f"{slug}-{suffix}"
        suffix += 1
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _plan_parts(
    available: float,
    parts: int,
    part_duration: float,
    overlap: float,
) -> tuple[int, float]:
    """Fit the requested layout into what the video actually offers.

    Rather than refusing a short video, shrink the parts to what is there and
    let the caller mention it. Returns (parts, per_part_duration)."""
    parts = max(1, int(parts))
    if parts == 1:
        return 1, min(part_duration, available)

    requested_window = parts * part_duration - (parts - 1) * overlap
    window = min(requested_window, available)
    per_part = (window + (parts - 1) * overlap) / parts

    while per_part < MIN_CLIP_SECONDS and parts > 1:
        parts -= 1
        window = min(parts * part_duration - (parts - 1) * overlap, available)
        per_part = (window + (parts - 1) * overlap) / parts

    return parts, per_part


def create_clips(job, url: str, options: dict) -> dict:
    """Download, find the best stretch, cut it into clips, caption them and
    register everything in the library. Returns the new group."""
    settings = load_settings()
    parts = int(options.get("parts") or settings.get("parts") or 1)
    part_duration = float(options.get("part_duration") or settings.get("part_duration") or 70)
    overlap = float(options.get("overlap") if options.get("overlap") is not None else settings.get("overlap", 5))
    mode = options.get("mode") or settings.get("mode") or "blur-pad"
    hook_text = (options.get("hook_text") or "").strip()
    burn_label = bool(options.get("burn_part_label", settings.get("burn_part_label", True)))
    language = settings.get("caption_language", "en")

    # -- 1. what are we even working with ------------------------------
    job.update(phase="metadata", percent=0, detail="Reading video details…")
    meta = fetch_metadata(url, settings, on_note=job.say)
    job.say(f"Video: {meta['title']}")
    job.update(percent=STAGE_BOUNDS["metadata"][1])

    slug = slugify(meta["title"])
    folder = _unique_folder(clips_dir(), slug)
    work_dir = app_data_dir() / "work" / folder.name
    work_dir.mkdir(parents=True, exist_ok=True)

    try:
        # -- 2. download -----------------------------------------------
        scale = phase_scaler(*STAGE_BOUNDS["download"])
        job.update(phase="download", detail="Downloading the source video…")
        source = download_source(
            url, work_dir,
            on_progress=lambda p: job.update(percent=scale(p)),
            settings=settings,
            preferred_options=meta.get("_options"),
            on_note=job.say,
        )
        job.say(f"Downloaded {source.name} ({source.stat().st_size / 1_048_576:.0f} MB)")

        # -- 3. find the liveliest stretch -----------------------------
        scale = phase_scaler(*STAGE_BOUNDS["analyze"])
        job.update(phase="analyze", detail="Listening for the liveliest moment…")
        job.say("Analysing the audio…")
        source_duration = meta.get("duration") or 0
        usable = source_duration or part_duration * max(parts, 1)
        parts, per_part = _plan_parts(usable, parts, part_duration, overlap)
        if parts != int(options.get("parts") or 1):
            job.say(f"The video only fits {parts} part(s) at this length.")
        window = parts * per_part - (parts - 1) * overlap

        start_s, end_s = find_best_segment(
            source,
            target_duration=window,
            source_duration=source_duration or None,
            on_progress=lambda p: job.update(percent=scale(p)),
        )
        actual = end_s - start_s
        per_part = (actual + (parts - 1) * overlap) / parts if parts > 1 else actual
        if per_part < MIN_CLIP_SECONDS:
            raise RuntimeError("That video is too short to make a clip from.")
        job.say(f"Best stretch: {to_timecode(start_s)} → {to_timecode(end_s)}")

        # -- 4. cut ----------------------------------------------------
        cut_start, cut_end = STAGE_BOUNDS["cut"]
        step = per_part - overlap if parts > 1 else 0
        created: list[dict] = []

        for index in range(parts):
            part_start = start_s + index * step
            part_end = part_start + per_part
            name = f"{folder.name}-part-{index + 1}.mp4" if parts > 1 else f"{folder.name}.mp4"
            out_path = folder / name

            label = part_label(index + 1, parts, language) if (parts > 1 and burn_label) else ""
            overlay = "\n".join(t for t in (hook_text, label) if t) or None

            slice_start = cut_start + (cut_end - cut_start) * index / parts
            slice_end = cut_start + (cut_end - cut_start) * (index + 1) / parts
            scale = phase_scaler(slice_start, slice_end)

            job.update(
                phase="cut",
                detail=f"Cutting clip {index + 1} of {parts}…" if parts > 1 else "Cutting the clip…",
                percent=slice_start,
            )
            job.say(f"Cutting {name} ({to_timecode(part_start)} → {to_timecode(part_end)}, {mode})")
            cut_clip(
                source,
                part_start,
                part_end,
                out_path,
                mode=mode,
                overlay_text=overlay,
                on_progress=lambda p: job.update(percent=scale(p)),
            )
            created.append(
                {
                    "path": str(out_path),
                    "part_index": index + 1,
                    "parts_total": parts,
                    "start": to_timecode(part_start),
                    "end": to_timecode(part_end),
                    "duration": round(per_part, 1),
                }
            )

        # -- 5. captions -----------------------------------------------
        scale = phase_scaler(*STAGE_BOUNDS["caption"])
        job.update(phase="caption", detail="Drafting captions…", percent=STAGE_BOUNDS["caption"][0])
        used_ai = False
        for position, clip in enumerate(created, start=1):
            caption, from_ai = build_caption(
                meta["title"],
                meta.get("description", ""),
                meta.get("tags", []),
                clip["part_index"] if parts > 1 else None,
                parts if parts > 1 else None,
                settings,
            )
            used_ai = used_ai or from_ai
            clip["caption"] = caption
            job.update(percent=scale(position / len(created) * 100))
        job.say("Captions written by Claude." if used_ai else "Captions drafted from the video title.")

        # -- 6. register and tidy up -----------------------------------
        job.update(phase="cleanup", detail="Tidying up…", percent=STAGE_BOUNDS["cleanup"][0])
        records = []
        for clip in created:
            records.append(
                library.add(
                    {
                        **clip,
                        "group_key": folder.name,
                        "video_title": meta["title"],
                        "video_description": meta.get("description", ""),
                        "video_tags": meta.get("tags", []),
                        "youtube_url": url,
                        "mode": mode,
                    }
                )
            )
        library.write_captions_file(folder)
    finally:
        # The source video is several hundred megabytes and is of no use once
        # the clips exist, so it never outlives the job.
        shutil.rmtree(work_dir, ignore_errors=True)

    job.update(percent=100, detail="Done")
    return {
        "group_key": folder.name,
        "folder": str(folder),
        "video_title": meta["title"],
        "clips": records,
    }
