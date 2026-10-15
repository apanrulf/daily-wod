"""Karusell med tre bilder: passet, skalning och tempo, och en uppmaning att dela."""
from pathlib import Path

from PIL import Image, ImageDraw

from bild import (ACCENT, BG, FG, H, MARGIN, MUTED, PANEL, W, _draw_words, _font, _wrap_words,
                  draw_swipe, render_wod_image)

INNER = W - 2 * MARGIN


def _header(draw, label, page, total):
    y = MARGIN - 10
    draw.text((MARGIN, y), label, font=_font("BebasNeue-Regular.ttf", 60), fill=ACCENT)
    draw.text((W - MARGIN, y + 34), f"{page}/{total}", font=_font("Barlow-SemiBold.ttf", 30),
              fill=MUTED, anchor="rs")
    return y + 92


def _footer(draw, handle, swipe_text=None):
    font = _font("Barlow-SemiBold.ttf", 28)
    fy = H - MARGIN + 4
    if handle:
        draw.text((MARGIN, fy), f"@{handle}", font=font, fill=MUTED, anchor="ls")
    if swipe_text:
        draw_swipe(draw, swipe_text, font, ACCENT, fy)


def _paragraph(draw, x, y, text, font, color, width, line_h):
    for line in _wrap_words(draw, [(w, color) for w in text.split()], font, width):
        _draw_words(draw, x, y, line, font)
        y += line_h
    return y


def _save(img, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "JPEG", quality=92, optimize=True, progressive=True)
    return path


def render_plan_slide(wod, handle, path, page=2, total=3):
    """Bild 2: hur man ska ta sig an passet, tempotips och tre nivåer. Krymper tills allt får plats."""
    limit = H - MARGIN - 70
    for scale in (1.0, 0.94, 0.88, 0.82, 0.76, 0.7):
        img, bottom = _draw_plan(wod, handle, page, total, scale)
        if bottom <= limit:
            break
    return _save(img, path)


def _draw_plan(wod, handle, page, total, k):
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)
    y = _header(draw, "THE PLAN", page, total)
    s = lambda v: int(round(v * k))  # noqa: E731

    draw.text((MARGIN - 4, y), "HOW TO ATTACK IT", font=_font("BebasNeue-Regular.ttf", s(116)), fill=FG)
    y += s(136)

    section = _font("Barlow-Bold.ttf", max(24, s(28)))
    body = _font("Barlow-Medium.ttf", s(38))
    y = _paragraph(draw, MARGIN, y, wod["stimulus"], body, FG, INNER, s(52)) + s(34)

    tips = wod.get("strategy") or []
    if tips:
        draw.text((MARGIN, y), "PACING", font=section, fill=ACCENT)
        y += s(50)
        for tip in tips[:3]:
            draw.rectangle((MARGIN, y + s(18), MARGIN + s(13), y + s(31)), fill=ACCENT)
            y = _paragraph(draw, MARGIN + s(34), y, tip, body, FG, INNER - s(34), s(50)) + s(12)
        y += s(26)

    levels = wod.get("levels") or [{"name": "Scaled", "text": x} for x in wod.get("scaling", [])]
    if levels:
        draw.text((MARGIN, y), "PICK YOUR LEVEL", font=section, fill=ACCENT)
        y += s(52)
        name_font = _font("Barlow-Bold.ttf", s(30))
        text_font = _font("Barlow-Medium.ttf", s(31))
        col = s(272)
        for lvl in levels[:3]:
            lines = _wrap_words(draw, [(w, MUTED) for w in lvl["text"].split()], text_font, INNER - col - 24)
            box_h = s(40) + max(1, len(lines)) * s(42) + s(24)
            draw.rounded_rectangle((MARGIN - 4, y, W - MARGIN + 4, y + box_h), radius=16, fill=PANEL)
            draw.text((MARGIN + 24, y + s(34)), lvl["name"].upper(), font=name_font, fill=FG)
            ly = y + s(34)
            for line in lines:
                _draw_words(draw, MARGIN + col, ly, line, text_font)
                ly += s(42)
            y += box_h + s(16)

    _footer(draw, handle, "SWIPE")
    return img, y


def _paper_plane(draw, cx, cy, size, color):
    """Enkel pappersflygplan-ikon (samma symbol som Instagrams dela-knapp)."""
    s = size
    outline = [(cx - s, cy - 0.15 * s), (cx + s, cy - 0.85 * s), (cx + 0.25 * s, cy + 0.85 * s),
               (cx - 0.1 * s, cy + 0.1 * s)]
    draw.polygon(outline, outline=color, width=10)
    draw.line([(cx - 0.1 * s, cy + 0.1 * s), (cx + s, cy - 0.85 * s)], fill=color, width=10)


# Bild 3 växlar mellan uppmaningar. Varje variant: stora rader, hur många av de sista
# raderna som är accentfärgade, och en kort brödtext.
VARIANTER = [
    (("SEND THIS", "TO YOUR", "TRAINING", "PARTNER"), 2,
     "Doing it together? Tag them in the comments and post both your times."),
    (("WHO'S", "SUFFERING", "WITH YOU", "TODAY?"), 1,
     "Misery loves company. Send this to the friend who never says no."),
    (("TAG THE", "FRIEND WHO", "OWES YOU", "A WORKOUT"), 2,
     "Drop their name in the comments and settle it on the clock."),
    (("SAVE THIS", "FOR YOUR", "NEXT", "SESSION"), 1,
     "Tap save so it's ready when you walk into the gym."),
    (("DONE IT?", "DROP YOUR", "TIME", "BELOW"), 2,
     "Post your score and level in the comments. Every result counts."),
]


def _fit_big(draw, lines, width, start=150, smallest=96):
    """Största Bebas-storlek där alla rader ryms i bredd (samma storlek på alla rader)."""
    for size in range(start, smallest - 1, -4):
        font = _font("BebasNeue-Regular.ttf", size)
        if all(draw.textlength(line, font=font) <= width for line in lines):
            return font
    return _font("BebasNeue-Regular.ttf", smallest)


def render_share_slide(day, handle, path, page=3, total=3):
    """Bild 3: en uppmaning att dela, spara eller lägga sin tid. Samma datum ger alltid samma variant."""
    lines, accent_count, blurb = VARIANTER[day.toordinal() % len(VARIANTER)]
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)
    _header(draw, "WOD", page, total)

    _paper_plane(draw, MARGIN + 90, 300, 82, ACCENT)

    big = _fit_big(draw, lines, INNER)
    y = 420
    for i, text in enumerate(lines):
        color = ACCENT if i >= len(lines) - accent_count else FG
        draw.text((MARGIN - 4, y), text, font=big, fill=color)
        y += int(big.size * 0.88)

    body = _font("Barlow-Medium.ttf", 38)
    y += 44
    _paragraph(draw, MARGIN, y, blurb, body, MUTED, INNER, 52)

    pill_font = _font("Barlow-Bold.ttf", 28)
    label = "NEW WOD EVERY MORNING"
    tw = draw.textlength(label, font=pill_font)
    py = H - MARGIN - 110
    draw.rounded_rectangle((MARGIN - 4, py, MARGIN + tw + 52, py + 62), radius=31, fill=ACCENT)
    draw.text((MARGIN + 24, py + 31), label, font=pill_font, fill=BG, anchor="lm")

    _footer(draw, handle)
    return _save(img, path)


def render_carousel(wod, day, handle, out_dir, stem):
    """Gör alla tre bilderna. Returnerar sökvägarna i rätt ordning."""
    out_dir = Path(out_dir)
    return [
        render_wod_image(wod, day, handle, out_dir / f"{stem}-1.jpg", carousel=True),
        render_plan_slide(wod, handle, out_dir / f"{stem}-2.jpg"),
        render_share_slide(day, handle, out_dir / f"{stem}-3.jpg"),
    ]
