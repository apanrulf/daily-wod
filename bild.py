"""Gör dagens WOD-bild: 1080 x 1350 JPEG (4:5, Instagrams högsta stående format)."""
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONTS = Path(__file__).resolve().parent / "fonts"

W, H = 1080, 1350
MARGIN = 88

BG = (13, 13, 13)
PANEL = (24, 24, 24)
FG = (244, 244, 238)
MUTED = (140, 140, 134)
ACCENT = (214, 255, 64)


def _font(name, size):
    return ImageFont.truetype(str(FONTS / name), size)


def _segments(line):
    """Delar en rad i (text, färg): vikter inom parentes blir gråa."""
    parts = re.split(r"(\([^)]*\))", line)
    return [(p, MUTED if p.startswith("(") else FG) for p in parts if p]


def _is_heading(line):
    """Rader som '21-15-9', '3 rounds:', 'Then:' eller 'Rest 2 min' visas i fetstil."""
    low = line.lower()
    return bool(
        re.fullmatch(r"[\d\s\-–x×/]+", line)
        or line.rstrip().endswith(":")
        or low.startswith(("then", "rest", "every", "part ", "a)", "b)", "c)"))
        or re.match(r"^\d+\s+rounds?\b", low)
        or re.match(r"^(amrap|emom|e\d+mom|for time|tabata)\b", low)
    )


def _wrap_words(draw, words, font, width):
    """words: lista av (ord, färg). Returnerar rader som listor av (ord, färg)."""
    lines, current, cur_w = [], [], 0
    space = draw.textlength(" ", font=font)
    for word, color in words:
        w = draw.textlength(word, font=font)
        extra = w if not current else w + space
        if current and cur_w + extra > width:
            lines.append(current)
            current, cur_w = [(word, color)], w
        else:
            current.append((word, color))
            cur_w += extra
    if current:
        lines.append(current)
    return lines


def _layout_block(draw, rows, size, width):
    """Bryter alla rader i passet för en viss teckenstorlek. Returnerar (rader, höjd)."""
    regular, bold = _font("Barlow-Medium.ttf", size), _font("Barlow-Bold.ttf", size)
    line_h, gap = int(size * 1.22), int(size * 0.32)
    out, height = [], 0
    for i, row in enumerate(rows):
        heading = _is_heading(row)
        font = bold if heading else regular
        words = []
        for text, color in _segments(row):
            words += [(w, ACCENT if heading else color) for w in text.split()]
        wrapped = _wrap_words(draw, words, font, width)
        if heading and i > 0:
            height += gap
        for j, words_line in enumerate(wrapped):
            out.append((height, font, words_line, j > 0))
            height += line_h
    return out, height


def _draw_words(draw, x, y, words, font, indent=0):
    space = draw.textlength(" ", font=font)
    x += indent
    for word, color in words:
        draw.text((x, y), word, font=font, fill=color)
        x += draw.textlength(word, font=font) + space


def _fit_title(draw, title, width):
    title = title.upper()
    for size in range(150, 88, -4):
        font = _font("BebasNeue-Regular.ttf", size)
        if draw.textlength(title, font=font) <= width:
            return [title], font
    font = _font("BebasNeue-Regular.ttf", 96)
    words = [(w, FG) for w in title.split()]
    lines = _wrap_words(draw, words, font, width)
    return [" ".join(w for w, _ in line) for line in lines[:2]], font


def draw_swipe(draw, text, font, color, y):
    """Högerställd text följd av en ritad pil (typsnittet saknar tecknet →)."""
    arrow_w, gap = 34, 14
    x_end = W - MARGIN
    mid = y - font.size * 0.36
    draw.line([(x_end - arrow_w, mid), (x_end, mid)], fill=color, width=4)
    draw.line([(x_end - 13, mid - 11), (x_end, mid), (x_end - 13, mid + 11)], fill=color, width=4, joint="curve")
    draw.text((x_end - arrow_w - gap, y), text, font=font, fill=color, anchor="rs")


def render_wod_image(wod, day, handle, path, carousel=False):
    """Bild 1. Med carousel=True flyttas skalningen till bild 2 och sidfoten säger "swipe"."""
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)
    inner = W - 2 * MARGIN

    # Överkant: WOD-etikett och datum
    y = MARGIN - 10
    label = _font("BebasNeue-Regular.ttf", 60)
    draw.text((MARGIN, y), "WOD", font=label, fill=ACCENT)
    date_font = _font("Barlow-SemiBold.ttf", 30)
    date_txt = day.strftime("%A · %d %b %Y").upper()
    draw.text((W - MARGIN, y + 34), date_txt, font=date_font, fill=MUTED, anchor="rs")

    # Titel
    y += 92
    lines, tfont = _fit_title(draw, wod["title"], inner)
    for line in lines:
        draw.text((MARGIN - 4, y), line, font=tfont, fill=FG)
        y += int(tfont.size * 0.92)

    # Formatrad
    y += 18
    fmt_font = _font("Barlow-Bold.ttf", 40)
    draw.text((MARGIN, y), wod["format"].upper(), font=fmt_font, fill=ACCENT)
    y += 66
    draw.rectangle((MARGIN, y, MARGIN + 110, y + 6), fill=ACCENT)
    y += 46

    # Nederkant: skalning och sidfot (reserveras först så passet får resten)
    small = _font("Barlow-Medium.ttf", 28)
    small_bold = _font("Barlow-Bold.ttf", 24)
    scale_lines = []
    for s in ([] if carousel else wod.get("scaling", [])[:3]):
        scale_lines += _wrap_words(draw, [(w, MUTED) for w in s.split()], small, inner - 48)
    scale_lines = scale_lines[:5]
    footer_h = 60
    panel_h = (40 + 30 + len(scale_lines) * 38 + 30) if scale_lines else 0
    bottom = H - MARGIN - footer_h - panel_h - (30 if panel_h else 0)

    # Passet: största teckenstorlek där allt får plats, helst utan radbrytningar
    fitting = []
    for size in range(72 if carousel else 60, 25, -2):
        layout, block_h = _layout_block(draw, wod["workout"], size, inner)
        if y + block_h <= bottom:
            fitting.append(layout)
    unwrapped = [lay for lay in fitting if not any(continued for *_, continued in lay)]
    if unwrapped and unwrapped[0][0][1].size >= 40:  # utan radbrytning om texten ändå blir stor nog
        block = unwrapped[0]
    elif fitting:
        block = fitting[0]
    else:
        block = _layout_block(draw, wod["workout"], 26, inner)[0]
    for dy, font, words_line, continued in block:
        _draw_words(draw, MARGIN, y + dy, words_line, font, indent=36 if continued else 0)

    # Skalningspanel
    if scale_lines:
        top = H - MARGIN - footer_h - panel_h
        draw.rounded_rectangle((MARGIN - 4, top, W - MARGIN + 4, top + panel_h), radius=18, fill=PANEL)
        draw.text((MARGIN + 24, top + 30), "SCALED", font=small_bold, fill=ACCENT)
        sy = top + 70
        for words_line in scale_lines:
            _draw_words(draw, MARGIN + 24, sy, words_line, small)
            sy += 38

    # Sidfot
    foot = _font("Barlow-SemiBold.ttf", 28)
    fy = H - MARGIN + 4
    if handle:
        draw.text((MARGIN, fy), f"@{handle}", font=foot, fill=MUTED, anchor="ls")
    if carousel:
        draw_swipe(draw, "SWIPE FOR THE PLAN", foot, ACCENT, fy)
    else:
        draw.text((W - MARGIN, fy), "POST YOUR SCORE BELOW", font=foot, fill=MUTED, anchor="rs")

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "JPEG", quality=92, optimize=True, progressive=True)
    return path
