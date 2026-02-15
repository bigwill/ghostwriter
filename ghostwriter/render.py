"""Ghosted PDF renderer.

Draws poem text with overlapping, slightly jittered layers to produce a
"ghosting" effect that plays well with e-ink persistence on the Sony DPT-RP1.

Words that were morphed get extra ghost layers so they visually shimmer
compared to the surrounding text.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen.canvas import Canvas

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PAGE_W, PAGE_H = A4  # 595.27 × 841.89 pt  ≈  210 × 297 mm


@dataclass
class GhostStyle:
    """Tuneable parameters for the ghosting effect."""

    font_name: str = "Times-Roman"
    font_size: float = 14.0
    title_font_size: float = 20.0
    line_spacing: float = 1.6          # multiplier of font_size
    margin_top: float = 72.0           # pt
    margin_bottom: float = 72.0
    margin_left: float = 72.0
    margin_right: float = 72.0

    # Ghost layers for normal words
    layers: int = 3
    jitter_xy: float = 1.2             # max px offset per layer
    jitter_angle: float = 0.6          # max degrees rotation per layer
    fade_step: float = 0.18            # gray increment per layer (0 = black)

    # Extra ghost for morphed words
    morph_extra_layers: int = 2
    morph_jitter_xy: float = 2.4
    morph_jitter_angle: float = 1.5
    morph_fade_step: float = 0.12

    # Stanza separator: blank lines become this much vertical space
    stanza_gap_lines: float = 1.0


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _draw_ghosted_word(
    c: Canvas,
    text: str,
    x: float,
    y: float,
    layers: int,
    jitter_xy: float,
    jitter_angle: float,
    fade_step: float,
) -> None:
    """Draw *text* at (x, y) with *layers* overlapping jittered copies."""
    for i in range(layers):
        off_x = random.uniform(-jitter_xy, jitter_xy)
        off_y = random.uniform(-jitter_xy, jitter_xy)
        angle = random.uniform(-jitter_angle, jitter_angle)
        gray = i * fade_step

        c.saveState()
        c.translate(x + off_x, y + off_y)
        c.rotate(angle)
        c.setFillColorRGB(gray, gray, gray)
        c.drawString(0, 0, text)
        c.restoreState()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def render_poem(
    lines: list[str],
    output: str | Path = "ghost.pdf",
    morphed_words: Optional[set[str]] = None,
    style: Optional[GhostStyle] = None,
    title: Optional[str] = None,
    seed: Optional[int] = None,
) -> Path:
    """Render *lines* of a poem to a ghosted PDF.

    Parameters
    ----------
    lines:
        Each element is one line of the poem.  Empty strings are treated as
        stanza breaks.
    output:
        Destination file path.
    morphed_words:
        Set of (lowercased) words that were morphed — they get extra ghosting.
    style:
        Visual parameters.  Uses defaults when *None*.
    title:
        Optional title rendered at the top of the page.
    seed:
        Fix the random seed for reproducible jitter.

    Returns
    -------
    Path to the written PDF.
    """
    if seed is not None:
        random.seed(seed)

    style = style or GhostStyle()
    morphed_words = morphed_words or set()
    output = Path(output)

    c = Canvas(str(output), pagesize=A4)

    usable_width = PAGE_W - style.margin_left - style.margin_right
    leading = style.font_size * style.line_spacing
    cursor_y = PAGE_H - style.margin_top

    # -- optional title ---------------------------------------------------
    if title:
        c.setFont(style.font_name, style.title_font_size)
        _draw_ghosted_word(
            c,
            title,
            style.margin_left,
            cursor_y,
            layers=style.layers,
            jitter_xy=style.jitter_xy * 0.5,
            jitter_angle=style.jitter_angle * 0.3,
            fade_step=style.fade_step,
        )
        cursor_y -= style.title_font_size * 2.2

    c.setFont(style.font_name, style.font_size)

    for line in lines:
        # Stanza break
        if line.strip() == "":
            cursor_y -= leading * style.stanza_gap_lines
            continue

        # Page break if we've run out of room
        if cursor_y < style.margin_bottom:
            c.showPage()
            c.setFont(style.font_name, style.font_size)
            cursor_y = PAGE_H - style.margin_top

        # Render word-by-word so morphed words get their own effect
        x = style.margin_left
        tokens = line.split(" ")
        space_w = c.stringWidth(" ", style.font_name, style.font_size)

        for token in tokens:
            word_w = c.stringWidth(token, style.font_name, style.font_size)

            # Soft-wrap if the word would exceed the right margin
            if x + word_w > PAGE_W - style.margin_right and x > style.margin_left:
                cursor_y -= leading
                x = style.margin_left
                if cursor_y < style.margin_bottom:
                    c.showPage()
                    c.setFont(style.font_name, style.font_size)
                    cursor_y = PAGE_H - style.margin_top

            # Is this a morphed word?
            stripped = token.strip(".,;:!?\"'()[]—–-").lower()
            is_morphed = stripped in morphed_words

            if is_morphed:
                # Extra ghost layers for morphed words
                total_layers = style.layers + style.morph_extra_layers
                _draw_ghosted_word(
                    c, token, x, cursor_y,
                    layers=total_layers,
                    jitter_xy=style.morph_jitter_xy,
                    jitter_angle=style.morph_jitter_angle,
                    fade_step=style.morph_fade_step,
                )
            else:
                _draw_ghosted_word(
                    c, token, x, cursor_y,
                    layers=style.layers,
                    jitter_xy=style.jitter_xy,
                    jitter_angle=style.jitter_angle,
                    fade_step=style.fade_step,
                )

            x += word_w + space_w

        cursor_y -= leading

    c.save()
    return output.resolve()
