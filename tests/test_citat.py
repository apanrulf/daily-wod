"""Testar citatflödet mot mockade Instagram- och Claude-API:er."""
import builtins
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace as NS
from zoneinfo import ZoneInfo

from PIL import Image

# Kör: python tests/test_citat.py  (kopierar projektet till en temporär mapp)
SRC = Path(__file__).resolve().parents[1]
WORK = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp()) / "projekt"
if WORK.exists():
    shutil.rmtree(WORK)
shutil.copytree(SRC, WORK, ignore=shutil.ignore_patterns("__pycache__", "tests", ".git", ".venv", ".env"))
(WORK / ".env").write_text("ANTHROPIC_API_KEY=sk-test\nIG_ACCESS_TOKEN=OLD\n")
TODAY = datetime.now(ZoneInfo("Europe/Stockholm")).date().isoformat()
(WORK / "citat.json").write_text(json.dumps([
    {"datum": "2000-01-03", "veckodag": "Monday", "quote": "Old quote two.", "theme": "grit", "status": "publicerad"},
    {"datum": "2000-01-01", "veckodag": "Saturday", "quote": "Old quote one.", "theme": "rest", "status": "publicerad"},
]))
wods_before = (WORK / "wods.json").read_text()
sys.path.insert(0, str(WORK))
os.chdir(WORK)
os.environ.pop("BILD_BAS_URL", None)

import main as M  # noqa: E402
from citat import QUOTE_BG, highlight_span, render_quote_image  # noqa: E402

GOOD = {
    "quote": "Show up tired. Leave proud.", "highlight": "Leave proud", "theme": "showing up",
    "caption": "Show up tired. Leave proud.\n\nThe hardest rep is the walk to the gym.\n\nSend this to your training partner 💪\n#motivation #mindset #trainhard",
}


class Resp:
    def __init__(self, data, status=200):
        self._d, self.status_code, self.text = data, status, json.dumps(data)

    def json(self):
        return self._d


class FakeSession:
    def __init__(self):
        self.calls = []

    def request(self, method, url, params=None, json=None, timeout=None):
        path = url.split("graph.instagram.com/")[1]
        self.calls.append((method, path, json))
        if path == "v25.0/me":
            return Resp({"data": [{"user_id": "111", "username": "dailywod.engine"}]})
        if path.endswith("content_publishing_limit"):
            return Resp({"data": [{"quota_usage": 0, "config": {"quota_total": 100}}]})
        if path == "v25.0/111/media" and method == "POST":
            return Resp({"id": "cont1"})
        if path == "v25.0/cont1":
            return Resp({"status_code": "FINISHED"})
        if path == "v25.0/111/media_publish":
            return Resp({"id": "pub1"})
        if path == "v25.0/pub1":
            return Resp({"permalink": "https://ig/p/citat"})
        return Resp({"error": {"message": f"okänd sökväg {path}"}}, 400)


class FakeMessages:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    def create(self, **kw):
        self.calls.append(json.loads(json.dumps(kw, default=str)))
        return NS(stop_reason="end_turn", content=[NS(type="text", text=self.replies.pop(0))])


def reply(d):
    return f"<citat>{json.dumps(d, ensure_ascii=False)}</citat>"


def fresh(replies=()):
    sess = FakeSession()
    ig = M.InstagramClient("OLD", session=sess)
    ig.poll_delay = 0
    return NS(messages=FakeMessages(replies)), sess, ig


def posts(sess):
    return [c for c in sess.calls if c[0] == "POST"]


def history():
    return json.loads((WORK / "citat.json").read_text())


ok = True


def check(cond, label):
    global ok
    print(("PASS " if cond else "FAIL ") + label)
    ok &= bool(cond)


def run(argv, **kw):
    try:
        M.main(argv, **kw)
        return None
    except SystemExit as e:
        return str(e)


IMAGE = WORK / "bilder" / f"citat-{TODAY}.jpg"

# 1. Skapa: för långt citat, markering som inte finns i citatet, CrossFit, sedan ett bra svar
too_long = dict(GOOD, quote="This is a very long quote that goes on and on and never seems to stop at all today")
bad_highlight = dict(GOOD, highlight="never quit")
llm, sess, ig = fresh([reply(too_long), reply(bad_highlight), reply(GOOD)])
run(["citat-skapa", "--auto"], ig=ig, llm=llm)
h = history()
retries = [c["messages"][-1]["content"] for c in llm.messages.calls[1:]]
check(len(llm.messages.calls) == 3, "skapa: försöker igen efter för långt citat och fel markering")
check("för långt" in retries[0] and '"highlight"' in retries[1], "skapa: felen beskrivs på svenska för Claude")
check(h[0]["datum"] == TODAY and h[0]["status"] == "redo" and h[0]["quote"] == GOOD["quote"], "skapa: dagens citat sparas som redo, nyast först")
check(len(h) == 3 and h[1]["quote"] == "Old quote two." and h[0]["bild"] == f"bilder/citat-{TODAY}.jpg", "skapa: historik och bildsökväg")
prompt = llm.messages.calls[0]["messages"][0]["content"]
system = llm.messages.calls[0]["system"]
check("Old quote two." in prompt and prompt.index("Old quote two.") < prompt.index("Old quote one."), "skapa: tidigare citat skickas med, nyast först")
check("Never quote, paraphrase or attribute" in system and "#fitnessmotivation" in system, "skapa: regler och varumärke i systemprompten")
img = Image.open(IMAGE)
check(img.size == (1080, 1350) and img.format == "JPEG", "skapa: bilden är 1080x1350 JPEG")
corner = img.convert("RGB").getpixel((20, 20))
check(all(abs(a - b) < 12 for a, b in zip(corner, QUOTE_BG)), "skapa: limegrön bakgrund, inte WOD-bildernas mörka")
check(not posts(sess) and (WORK / "wods.json").read_text() == wods_before, "skapa: inget publiceras och wods.json är orörd")

llm, sess, ig = fresh([reply(dict(GOOD, caption="Classic CrossFit motivation"))] * 3)
err = run(["citat-skapa", "--auto", "--nytt"], ig=ig, llm=llm)
check(err and "efter 3 försök" in err and "CrossFit" in err and history()[0]["quote"] == GOOD["quote"], "skapa: CrossFit stoppas och det sparade citatet behålls")

# 2. Skapa igen: hoppar över
llm, sess, ig = fresh([])
run(["citat-skapa", "--auto"], ig=ig, llm=llm)
check(not llm.messages.calls, "skapa igen: inget nytt anrop när dagens finns")

# 3. Publicera (med en gammal bild som ska städas bort)
shutil.copy(WORK / "exempel.jpg", WORK / "bilder" / "2000-01-01-1.jpg")
llm, sess, ig = fresh()
err = run(["citat-publicera", "--auto"], ig=ig, llm=llm)
check(err and "BILD_BAS_URL" in err and not posts(sess), "publicera: kräver BILD_BAS_URL")

os.environ["BILD_BAS_URL"] = "https://raw.githubusercontent.com/erik/daily-wod/abc123/"
M.url_is_live = lambda url: False
llm, sess, ig = fresh()
err = run(["citat-publicera", "--auto"], ig=ig, llm=llm)
check(err and "publika adressen" in err and not posts(sess), "publicera: stoppar om bilden inte går att nå")
check((WORK / "bilder" / "2000-01-01-1.jpg").exists(), "publicera misslyckad: inga bilder tas bort")

seen = []
M.url_is_live = lambda url: seen.append(url) or True
llm, sess, ig = fresh()
run(["citat-publicera", "--auto"], ig=ig, llm=llm)
expected = f"https://raw.githubusercontent.com/erik/daily-wod/abc123/bilder/citat-{TODAY}.jpg"
media = [c[2] for c in posts(sess) if c[1] == "v25.0/111/media"]
check(seen == [expected] and len(media) == 1 and media[0]["image_url"] == expected, "publicera: en bild med rätt URL")
check(media[0]["caption"] == GOOD["caption"] and GOOD["quote"] in media[0]["alt_text"], "publicera: bildtext och alt-text med citatet")
check("is_carousel_item" not in media[0] and posts(sess)[-1][1] == "v25.0/111/media_publish", "publicera: vanlig bild, inte karusell")
h = history()
check(h[0]["status"] == "publicerad" and h[0]["permalink"] == "https://ig/p/citat", "publicera: status och länk sparas")
check((WORK / "wods.json").read_text() == wods_before, "publicera: wods.json är orörd")
waiting = {Path(rel).name for w in json.loads(wods_before) if w.get("status") == "redo"
           for rel in (w.get("bilder") or ([w["bild"]] if w.get("bild") else []))}
left = {p.name for p in (WORK / "bilder").glob("*.jpg")}
check(left == {f"citat-{TODAY}.jpg"} | waiting and (WORK / "bilder" / ".gitkeep").exists(), "publicera: gamla bilder tas bort, bara dagens citat (och väntande WOD) kvar")

llm, sess, ig = fresh()
run(["citat-publicera", "--auto"], ig=ig, llm=llm)
check(not posts(sess), "publicera igen: ingen dubbelpost")
llm, sess, ig = fresh([])
run(["citat-skapa", "--auto", "--nytt"], ig=ig, llm=llm)
check(not llm.messages.calls, "skapa --nytt: skriver inte över ett publicerat citat")

# 4. Torrkörning sparar inget
(WORK / "citat.json").write_text(json.dumps(h[1:]))
IMAGE.unlink()
llm, sess, ig = fresh([reply(GOOD)])
run(["citat-skapa", "--auto", "--torrkorning"], ig=ig, llm=llm)
check(IMAGE.exists() and len(history()) == 2, "torrkörning: bilden görs men inget sparas")

# 5. Interaktivt med feedback
answers = iter(["f", "Shorter please", "j"])
orig_input, orig_tty = builtins.input, sys.stdin.isatty
builtins.input = lambda prompt="": next(answers)
sys.stdin.isatty = lambda: True
second = dict(GOOD, quote="Consistency beats intensity.", highlight="Consistency")
llm, sess, ig = fresh([reply(GOOD), reply(second)])
run(["citat-skapa"], ig=ig, llm=llm)
builtins.input, sys.stdin.isatty = orig_input, orig_tty
fb = llm.messages.calls[1]["messages"][0]["content"]
check("Shorter please" in fb and GOOD["quote"] in fb, "interaktivt: feedback och förra förslaget skickas")
check(history()[0]["quote"] == "Consistency beats intensity.", "interaktivt: den nya versionen sparas")

# 6. Markeringen hittar orden oavsett skiljetecken och versaler
check(highlight_span("Show up tired. Leave proud.", "leave PROUD") == (3, 5), "markering: skiljetecken och versaler ignoreras")
check(highlight_span("Show up tired.", "show tired") is None, "markering: orden måste stå i följd")

# 7. Långt citat ryms: inga mörka pixlar i marginalerna
long_q = {"quote": "Uncomfortable reps today become the strength everyone calls natural talent tomorrow.",
          "highlight": "natural talent"}
im = Image.open(render_quote_image(long_q, datetime(2026, 10, 28), "dailywod.engine", WORK / "bilder" / "lang.jpg")).convert("L")
w, hgt = im.size
edges = [im.getpixel((x, y)) for y in range(0, hgt, 4) for x in list(range(0, 50)) + list(range(w - 50, w))]
check(min(edges) > 120, "långt citat: ingen text i sidomarginalerna")

print("\nALLA TESTER OK" if ok else "\nNÅGOT TEST MISSLYCKADES")
sys.exit(0 if ok else 1)
