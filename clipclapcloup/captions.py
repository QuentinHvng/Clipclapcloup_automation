"""Drafting the caption that goes with a clip.

Two levels, both editable afterwards:

  * a free, offline default built from what we actually know — the real video
    title, the genuine part number, a follow prompt and a few generic
    discovery hashtags. Nothing invented about what happens in the clip.
  * an optional AI draft, if the user has put their own Anthropic API key in
    the settings. It gets the real title, description and tags and is told
    not to invent anything beyond them. Any failure — no key, no network, a
    bad response — silently falls back to the default.
"""
from __future__ import annotations

import requests

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"

HASHTAGS = {
    "en": "#fyp #foryou #viral",
    "fr": "#fyp #pourtoi #viral",
}

FOLLOW_MORE = {
    "en": "Follow for more!",
    "fr": "Abonnez-vous !",
}

FOLLOW_FOR_REST = {
    "en": "The rest is on the profile — follow so you don't miss it!",
    "fr": "Suite sur le compte, abonnez-vous !",
}

PART_LABEL = {
    "en": "Part {index}/{total}",
    "fr": "Partie {index}/{total}",
}


def part_label(index: int, total: int, language: str = "en") -> str:
    return PART_LABEL.get(language, PART_LABEL["en"]).format(index=index, total=total)


def default_caption(
    title: str,
    part_index: int | None = None,
    parts_total: int | None = None,
    language: str = "en",
) -> str:
    """The offline default. Always something usable, never a blank field."""
    language = language if language in HASHTAGS else "en"
    title = (title or "Clip").strip() or "Clip"
    hashtags = HASHTAGS[language]

    if part_index and parts_total and parts_total > 1:
        is_last = part_index >= parts_total
        prompt = FOLLOW_MORE[language] if is_last else FOLLOW_FOR_REST[language]
        label = part_label(part_index, parts_total, language)
        return f"{label} — {title}\n{prompt} {hashtags}"

    return f"{title}\n{FOLLOW_MORE[language]} {hashtags}"


def _prompt(
    title: str,
    description: str,
    tags: list[str],
    part_index: int | None,
    parts_total: int | None,
    language: str,
) -> str:
    language_name = "French" if language == "fr" else "English"
    series_note = ""
    if part_index and parts_total and parts_total > 1:
        series_note = (
            f"\nThis clip is part {part_index} of {parts_total} of a series. "
            + (
                "It is the last part."
                if part_index >= parts_total
                else "More parts follow, so the caption should make people want to see the rest."
            )
        )

    return (
        f"Write a TikTok caption in {language_name} for a short clip taken from a longer "
        "YouTube video. Work only from the facts below — do not invent any detail about "
        "what happens in the clip that these facts do not already give you.\n\n"
        f"Source video title: {title}\n"
        f"Source video description: {description or '(none)'}\n"
        f"Source video tags: {', '.join(tags) or '(none)'}"
        f"{series_note}\n\n"
        "Reply with the finished caption and nothing else: one short punchy hook line, "
        "then 2 to 4 relevant hashtags drawn from the facts above. No quotes, no markdown, "
        "no explanation. 150 characters maximum."
    )


def ai_caption(
    title: str,
    description: str,
    tags: list[str],
    part_index: int | None = None,
    parts_total: int | None = None,
    language: str = "en",
    api_key: str = "",
    model: str = "claude-haiku-4-5",
) -> str | None:
    """Return an AI-written caption, or None if it can't be produced. Never
    raises: the caller always has default_caption() to fall back on."""
    if not api_key:
        return None
    try:
        response = requests.post(
            ANTHROPIC_URL,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": 200,
                "messages": [
                    {
                        "role": "user",
                        "content": _prompt(title, description, tags, part_index, parts_total, language),
                    }
                ],
            },
            timeout=25,
        )
        response.raise_for_status()
        blocks = response.json().get("content", [])
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        return text.strip().strip('"') or None
    except Exception:  # noqa: BLE001 - every failure mode ends the same way
        return None


def build_caption(
    title: str,
    description: str,
    tags: list[str],
    part_index: int | None,
    parts_total: int | None,
    settings: dict,
) -> tuple[str, bool]:
    """Return (caption, came_from_ai)."""
    language = settings.get("caption_language", "en")
    drafted = ai_caption(
        title,
        description,
        tags,
        part_index,
        parts_total,
        language=language,
        api_key=(settings.get("anthropic_api_key") or "").strip(),
        model=settings.get("anthropic_model") or "claude-haiku-4-5",
    )
    if drafted:
        return drafted, True
    return default_caption(title, part_index, parts_total, language), False
