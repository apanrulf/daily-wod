"""Motiverande citat: Claude skriver ett eget citat och bilden ritas med omvänd färgskala.

WOD-bilderna är mörka med limegrön accent. Citatbilderna är limegröna med nästan svart
text, så att de syns som egna rutor i profilens rutnät men ändå hör ihop med varumärket.
"""
import json
import re
from pathlib import Path

from PIL import Image, ImageDraw

from agent import _tag, _text
from bild import ACCENT, BG, H, MARGIN, W, _font

QUOTE_BG = ACCENT
INK = BG
SOFT = (92, 112, 28)  # dämpad mörkgrön för datum och sidfot på limegrönt
INNER = W - 2 * MARGIN

QUOTE_SYSTEM = """You are the head coach behind an Instagram account that posts one original workout of the day (WOD) every day, in English. A few times a week the account also posts a short motivational quote.

<brand>
{brand}
</brand>

Write today's quote.
- It must be your own original line. Never quote, paraphrase or attribute words to real people, athletes, coaches, books or films, and never present it as anyone else's words.
- It is about training and the mindset behind it: showing up, consistency, effort, patience, recovery, community, discomfort, progress over perfection.
- Short and punchy, so it can be read in a tiny thumbnail: at most 12 words and 80 characters.
- Plain words, no hashtags, no emojis, no quotation marks around it.
- Do not repeat the theme or wording of the recent quotes.
- Never use the word "CrossFit" anywhere, including hashtags.

Reply with only a JSON object inside <citat></citat> tags, with exactly these keys:
{{
  "quote": "The quote itself",
  "highlight": "1-3 consecutive words copied exactly from the quote that carry its meaning; they get a dark box on the image",
  "theme": "One or two words for variety tracking, e.g. 'consistency'",
  "caption": "Instagram caption in English: the quote as the first line, 2-4 short sentences from the coach that tie it to training, a call to save the post or send it to a training partner who needs it, then 3-5 hashtags from the brand description. Max 1000 characters."
}}"""

FIELDS = {"quote": str, "highlight": str, "theme": str, "caption": str}


def _words(text):
    return [re.sub(r"[^\w']", "", w).lower() for w in text.split()]


def highlight_span(quote, highlight):
    """Index (start, slut) för de ord i citatet som ska markeras, eller None."""
    q, h = _words(quote), [w for w in _words(highlight) if w]
    for i in range(len(q) - len(h) + 1):
        if h and q[i:i + len(h)] == h:
            return i, i + len(h)
    return None


def _validate(c):
    for key, kind in FIELDS.items():
        if not isinstance(c.get(key), kind) or not c.get(key).strip():
            raise ValueError(f'fältet "{key}" saknas eller har fel typ')
    c = {k: c[k].strip() for k in FIELDS}
    c["quote"] = c["quote"].strip('"“”\' ')
    if len(c["quote"]) > 90 or len(c["quote"].split()) > 14:
        raise ValueError("citatet är för långt för bilden (högst 12 ord och 80 tecken)")
    if not 1 <= len(c["highlight"].split()) <= 3 or highlight_span(c["quote"], c["highlight"]) is None:
        raise ValueError('"highlight" måste vara 1–3 ord i följd som står ordagrant i citatet')
    if len(c["caption"]) > 2200:
        raise ValueError("bildtexten är längre än Instagrams gräns på 2 200 tecken")
    if "crossfit" in json.dumps(c, ensure_ascii=False).lower():
        raise ValueError('ordet "CrossFit" får inte förekomma')
    return c


def generate_quote(llm, cfg, history, day, feedback=None, previous=None, attempts=3):
    """Returnerar ett validerat citat som dict. Försöker igen om svaret inte går att använda."""
    brand = cfg.brand_path.read_text(encoding="utf-8")
    recent = [{k: q.get(k) for k in ("datum", "quote", "theme")} for q in history[:30]]
    prompt = (
        f"Today is {day.strftime('%A')}, {day.isoformat()}.\n\n"
        "Recent quotes, newest first:\n<history>\n"
        + json.dumps(recent, ensure_ascii=False, indent=1)
        + "\n</history>"
    )
    if previous and feedback:
        prompt += (
            "\n\nYour previous draft for today:\n<previous>\n"
            + json.dumps(previous, ensure_ascii=False, indent=1)
            + f"\n</previous>\nThe owner's feedback: {feedback}\nWrite a new version."
        )

    messages = [{"role": "user", "content": prompt}]
    last_error = None
    for _ in range(attempts):
        resp = llm.messages.create(
            model=cfg.model,
            max_tokens=2000,
            system=QUOTE_SYSTEM.format(brand=brand),
            messages=messages,
        )
        raw = _text(resp)
        try:
            body = _tag(raw, "citat")
            if body is None:
                raise ValueError("svaret saknar <citat>-taggar")
            return _validate(json.loads(body))
        except (ValueError, json.JSONDecodeError) as exc:
            last_error = exc
            messages += [
                {"role": "assistant", "content": resp.content},
                {"role": "user", "content": f"That reply could not be used: {exc}. "
                                            "Reply again with only valid JSON inside <citat></citat>."},
            ]
    raise RuntimeError(f"Claude gav inget användbart citat efter {attempts} försök: {last_error}")


def alt_text(c):
    return f"Motivational quote on a lime green background: {c['quote']}"[:1000]


# ---------- bild ----------

def _wrap(draw, words, font, width):
    """Bryter ord (text, markerad) till rader. Returnerar listor av (text, markerad)."""
    lines, current, cur_w = [], [], 0
    space = draw.textlength(" ", font=font)
    for word in words:
        w = draw.textlength(word[0], font=font)
        extra = w if not current else w + space
        if current and cur_w + extra > width:
            lines.append(current)
            current, cur_w = [word], w
        else:
            current.append(word)
            cur_w += extra
    if current:
        lines.append(current)
    return lines


def _layout_quote(draw, words, width, max_h):
    """Största Bebas-storlek där citatet ryms i rutan, med högst fem rader."""
    for size in range(210, 79, -6):
        font = _font("BebasNeue-Regular.ttf", size)
        lines = _wrap(draw, words, font, width)
        line_h = int(size * 0.96)
        if len(lines) <= 5 and len(lines) * line_h <= max_h:
            return font, lines, line_h
    font = _font("BebasNeue-Regular.ttf", 80)
    return font, _wrap(draw, words, font, width), int(80 * 0.96)


def render_quote_image(c, day, handle, path):
    img = Image.new("RGB", (W, H), QUOTE_BG)
    draw = ImageDraw.Draw(img)

    # Överkant: etikett och datum
    y = MARGIN - 10
    draw.text((MARGIN, y), "MINDSET", font=_font("BebasNeue-Regular.ttf", 60), fill=INK)
    draw.text((W - MARGIN, y + 34), day.strftime("%A · %d %b %Y").upper(),
              font=_font("Barlow-SemiBold.ttf", 30), fill=SOFT, anchor="rs")

    # Stort citattecken
    draw.text((MARGIN - 14, 150), "“", font=_font("BebasNeue-Regular.ttf", 340), fill=INK)

    # Citatet, med de markerade orden i en mörk ruta
    span = highlight_span(c["quote"], c["highlight"]) or (0, 0)
    words = [(w, span[0] <= i < span[1]) for i, w in enumerate(c["quote"].split())]
    top, bottom = 430, H - MARGIN - 190
    font, lines, line_h = _layout_quote(draw, words, INNER - 20, bottom - top)
    y = top + max(0, (bottom - top - len(lines) * line_h) // 2)
    space = draw.textlength(" ", font=font)
    pad_x, pad_top, pad_bottom = int(font.size * 0.08), int(font.size * 0.02), int(font.size * 0.05)
    for line in lines:
        x = MARGIN - 4
        boxes, cursor = [], x
        for text, marked in line:
            w = draw.textlength(text, font=font)
            if marked:
                if boxes and boxes[-1][1] >= cursor - space - 1:
                    boxes[-1][1] = cursor + w
                else:
                    boxes.append([cursor, cursor + w])
            cursor += w + space
        for x0, x1 in boxes:
            draw.rectangle((x0 - pad_x, y + pad_top, x1 + pad_x, y + int(font.size * 0.92) + pad_bottom), fill=INK)
        for text, marked in line:
            draw.text((x, y), text, font=font, fill=QUOTE_BG if marked else INK)
            x += draw.textlength(text, font=font) + space
        y += line_h

    # Nederkant: streck, avsändare och sidfot
    by = H - MARGIN - 130
    draw.rectangle((MARGIN, by, MARGIN + 110, by + 6), fill=INK)
    draw.text((MARGIN, by + 62), "— YOUR COACH", font=_font("Barlow-Bold.ttf", 34), fill=INK, anchor="ls")
    foot = _font("Barlow-SemiBold.ttf", 28)
    fy = H - MARGIN + 4
    if handle:
        draw.text((MARGIN, fy), f"@{handle}", font=foot, fill=SOFT, anchor="ls")
    draw.text((W - MARGIN, fy), "SAVE FOR A HARD DAY", font=foot, fill=SOFT, anchor="rs")

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "JPEG", quality=92, optimize=True, progressive=True)
    return path
