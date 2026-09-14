"""Burn a short phrase onto a clip: white text, black outline, centered.

Rendered to a transparent PNG with Pillow and composited by ffmpeg, rather
than using ffmpeg's own drawtext — that way we get proper line wrapping and,
more importantly, real color emoji, which no common desktop font provides
and drawtext cannot render at all.

Emoji glyphs come from the Twemoji asset set and are cached after first use.
If there's no network, the text still renders and the emoji are simply left
out rather than failing the whole export.
"""
from __future__ import annotations

import re
import textwrap
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont

from ..config import EMOJI_CACHE_DIR, resource_path

TWEMOJI_BASE = "https://raw.githubusercontent.com/twitter/twemoji/master/assets/72x72/"

EMOJI_PATTERN = re.compile(
    "["
    "\U0001F1E6-\U0001F1FF"
    "\U0001F300-\U0001FAFF"
    "\U00002600-\U000027BF"
    "\U00002B00-\U00002BFF"
    "\U0001F3FB-\U0001F3FF"
    "\U0000200D\U0000FE0F"
    "]"
)

VARIATION_SELECTOR = "️"
ZERO_WIDTH_JOINER = "‍"


def font_path() -> Path:
    return resource_path("assets", "fonts", "Poppins-Bold.ttf")


def _tokenize(text: str) -> list[tuple[bool, str]]:
    """Split into (is_emoji, chunk) runs so emoji sequences stay together."""
    tokens: list[tuple[bool, str]] = []
    buffer = ""
    buffer_is_emoji = False
    for char in text:
        is_emoji = bool(EMOJI_PATTERN.match(char))
        if buffer and is_emoji != buffer_is_emoji:
            tokens.append((buffer_is_emoji, buffer))
            buffer = ""
        buffer += char
        buffer_is_emoji = is_emoji
    if buffer:
        tokens.append((buffer_is_emoji, buffer))
    return tokens


def _emoji_image(emoji_chars: str, size: int) -> Image.Image | None:
    codepoints = [f"{ord(c):x}" for c in emoji_chars if c != VARIATION_SELECTOR]
    if not codepoints:
        return None
    name = "-".join(codepoints)
    cache_dir = EMOJI_CACHE_DIR()
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached = cache_dir / f"{name}.png"
    if not cached.exists():
        try:
            response = requests.get(f"{TWEMOJI_BASE}{name}.png", timeout=10)
        except requests.RequestException:
            return None
        if response.status_code != 200:
            return None
        cached.write_bytes(response.content)
    try:
        image = Image.open(cached).convert("RGBA")
    except OSError:
        return None
    return image.resize((size, size), Image.LANCZOS)


def render_overlay_png(
    text: str,
    out_path: Path,
    canvas_size: tuple[int, int] = (1080, 1920),
    font_size: int = 78,
    stroke_width: int = 7,
    max_chars_per_line: int = 20,
    y_top: int = 140,
    line_spacing: int = 18,
) -> Path:
    width, height = canvas_size
    font = ImageFont.truetype(str(font_path()), font_size)
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    lines: list[str] = []
    for raw_line in text.split("\n"):
        lines.extend(textwrap.wrap(raw_line, width=max_chars_per_line) or [raw_line])

    def measure(line: str) -> tuple[int, list[tuple[str, str, int]]]:
        parts: list[tuple[str, str, int]] = []
        total = 0
        for is_emoji, chunk in _tokenize(line):
            if is_emoji:
                glyphs = chunk.replace(VARIATION_SELECTOR, "").replace(ZERO_WIDTH_JOINER, "")
                chunk_width = font_size * (len(glyphs) or 1)
                parts.append(("emoji", chunk, chunk_width))
            else:
                box = draw.textbbox((0, 0), chunk, font=font, stroke_width=stroke_width)
                chunk_width = box[2] - box[0]
                parts.append(("text", chunk, chunk_width))
            total += chunk_width
        return total, parts

    line_height = font_size + line_spacing
    y = y_top

    for total_width, parts in (measure(line) for line in lines):
        x = int((width - total_width) / 2)
        for kind, chunk, chunk_width in parts:
            if kind == "text":
                draw.text(
                    (x, y), chunk, font=font, fill="white",
                    stroke_width=stroke_width, stroke_fill="black",
                )
            else:
                glyph = _emoji_image(chunk, font_size)
                if glyph is not None:
                    canvas.alpha_composite(glyph, (x, y + (font_size - glyph.height) // 2 + 6))
            x += chunk_width
        y += line_height

    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path)
    return out_path
