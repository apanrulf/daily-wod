"""Dagens WOD: Claude programmerar ett eget pass och skriver bildtexten."""
import json

from agent import _tag, _text

WOD_SYSTEM = """You are the head coach behind an Instagram account that posts one original workout of the day (WOD) every day, in English.

<brand>
{brand}
</brand>

Program today's workout.
- It must be your own original workout. Never copy, rename or lightly modify workouts published by others, including named benchmark or hero workouts.
- Classic formats are fine: AMRAP, EMOM, For Time, Chipper, Intervals, Strength + Metcon.
- Follow the weekly structure and equipment list in the brand description.
- Balance against the recent history: vary movements, modalities (gymnastics, weightlifting, monostructural) and time domains, and avoid loading the same muscle groups hard on consecutive days.
- Give loads for men/women in both units, like "Thrusters (43/30 kg · 95/65 lb)".
- Keep it safe and realistic for a typical gym: sensible volume, a time cap when relevant, and a scaled option for every demanding element.
- Never use the word "CrossFit" anywhere, including hashtags.

Reply with only a JSON object inside <wod></wod> tags, with exactly these keys:
{{
  "title": "Short catchy name for the workout, max 24 characters",
  "format": "Format line, e.g. 'For Time · 15 min cap' or 'AMRAP 20'",
  "workout": ["One line per row exactly as it should appear on the image, max 12 lines (keep it readable on a phone), e.g. '21-15-9', 'Thrusters (43/30 kg · 95/65 lb)', 'Pull-ups'"],
  "scaling": ["1-3 short lines with scaled options"],
  "stimulus": "One or two sentences of coach notes on intended stimulus and pacing",
  "focus": ["2-4 short tags for balance tracking, e.g. 'legs', 'pull', 'engine', 'olympic lifting'"],
  "caption": "Instagram caption in English: a hook line, the full workout, the scaled options, the coach note, a call to post scores in the comments, then 3-5 hashtags from the brand description. Max 2000 characters."
}}"""

FIELDS = {
    "title": str, "format": str, "workout": list, "scaling": list,
    "stimulus": str, "focus": list, "caption": str,
}


def _validate(wod):
    for key, kind in FIELDS.items():
        if not isinstance(wod.get(key), kind) or not wod.get(key):
            raise ValueError(f'fältet "{key}" saknas eller har fel typ')
    if len(wod["workout"]) > 14:
        raise ValueError("passet har för många rader för bilden (max 12)")
    if len(wod["caption"]) > 2200:
        raise ValueError("bildtexten är längre än Instagrams gräns på 2 200 tecken")
    if "crossfit" in json.dumps(wod, ensure_ascii=False).lower():
        raise ValueError('ordet "CrossFit" får inte förekomma')
    wod["workout"] = [str(x).strip() for x in wod["workout"] if str(x).strip()]
    wod["scaling"] = [str(x).strip() for x in wod["scaling"] if str(x).strip()][:3]
    wod["title"] = wod["title"].strip()[:40]
    return wod


def history_summary(history, limit=21):
    """De senaste passen, kortade till det coachen behöver för att variera."""
    return [
        {k: w.get(k) for k in ("datum", "veckodag", "title", "format", "workout", "focus")}
        for w in history[:limit]
    ]


def generate_wod(llm, cfg, history, day, feedback=None, previous=None, attempts=3):
    """Returnerar ett validerat WOD som dict. Försöker igen om svaret inte går att läsa."""
    brand = cfg.brand_path.read_text(encoding="utf-8")
    prompt = (
        f"Today is {day.strftime('%A')}, {day.isoformat()}.\n\n"
        "Recent history, newest first:\n<history>\n"
        + json.dumps(history_summary(history), ensure_ascii=False, indent=1)
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
            max_tokens=8000,
            system=WOD_SYSTEM.format(brand=brand),
            messages=messages,
        )
        raw = _text(resp)
        try:
            body = _tag(raw, "wod")
            if body is None:
                raise ValueError("svaret saknar <wod>-taggar")
            return _validate(json.loads(body))
        except (ValueError, json.JSONDecodeError) as exc:
            last_error = exc
            messages += [
                {"role": "assistant", "content": resp.content},
                {"role": "user", "content": f"That reply could not be used: {exc}. "
                                            "Reply again with only valid JSON inside <wod></wod>."},
            ]
    raise RuntimeError(f"Claude gav inget användbart WOD efter {attempts} försök: {last_error}")


def alt_text(wod):
    text = f"Workout of the day: {wod['title']}. {wod['format']}. " + "; ".join(wod["workout"])
    return text[:1000]
