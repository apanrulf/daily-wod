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


def quote_images():
    return {p.name for p in (WORK / "bilder").glob("citat-*.jpg")}


def publish_ok():
    """Publicerar det citat som väntar. Returnerar body för containern."""
    llm, sess, ig = fresh()
    run(["citat-publicera", "--auto"], ig=ig, llm=llm)
    media = [c[2] for c in posts(sess) if c[1] == "v25.0/111/media"]
    return media, sess


# 1. Skapa: för långt citat, markering som inte finns i citatet, sedan ett bra svar
too_long = dict(GOOD, quote="This is a very long quote that goes on and on and never seems to stop at all today")
bad_highlight = dict(GOOD, highlight="never quit")
llm, sess, ig = fresh([reply(too_long), reply(bad_highlight), reply(GOOD)])
run(["citat-skapa", "--auto"], ig=ig, llm=llm)
h = history()
first = h[0]
retries = [c["messages"][-1]["content"] for c in llm.messages.calls[1:]]
check(len(llm.messages.calls) == 3, "skapa: försöker igen efter för långt citat och fel markering")
check("för långt" in retries[0] and '"highlight"' in retries[1], "skapa: felen beskrivs på svenska för Claude")
check(first["status"] == "redo" and first["quote"] == GOOD["quote"] and first["id"].startswith(TODAY), "skapa: citatet sparas som redo, nyast först, med unikt id")
check(len(h) == 3 and h[1]["quote"] == "Old quote two." and first["bild"] == f"bilder/citat-{first['id']}.jpg", "skapa: historik och bildsökväg")
prompt = llm.messages.calls[0]["messages"][0]["content"]
system = llm.messages.calls[0]["system"]
check("Old quote two." in prompt and prompt.index("Old quote two.") < prompt.index("Old quote one."), "skapa: tidigare citat skickas med, nyast först")
check("Never quote, paraphrase or attribute" in system and "#fitnessmotivation" in system, "skapa: regler och varumärke i systemprompten")
img = Image.open(WORK / first["bild"])
check(img.size == (1080, 1350) and img.format == "JPEG", "skapa: bilden är 1080x1350 JPEG")
corner = img.convert("RGB").getpixel((20, 20))
check(all(abs(a - b) < 12 for a, b in zip(corner, QUOTE_BG)), "skapa: limegrön bakgrund, inte WOD-bildernas mörka")
check(not posts(sess) and (WORK / "wods.json").read_text() == wods_before, "skapa: inget publiceras och wods.json är orörd")
weekday = datetime.now(ZoneInfo("Europe/Stockholm")).strftime("%A")
check(TODAY not in prompt and weekday not in prompt and "2000-01" not in prompt, "skapa: Claude får inget datum eller veckodag")
gray = img.convert("L")
top_right = [gray.getpixel((x, y)) for y in range(60, 130, 3) for x in range(500, 1000, 3)]
check(min(top_right) > 150, "skapa: inget datum på bilden")

# 2. Allmänna citat: veckodagar och dagens pass stoppas. Ett misslyckat försök rör inte det som väntar.
llm, sess, ig = fresh([reply(dict(GOOD, quote="Monday is for showing up.", highlight="showing up")),
                       reply(dict(GOOD, caption="Crush today's workout! #motivation")),
                       reply(dict(GOOD, caption="Classic CrossFit motivation"))])
err = run(["citat-skapa", "--auto"], ig=ig, llm=llm)
retries = [c["messages"][-1]["content"] for c in llm.messages.calls[1:]]
check(all("allmänna" in r for r in retries), "skapa: veckodagar och dagens pass stoppas")
check(err and "efter 3 försök" in err and "CrossFit" in err, "skapa: CrossFit stoppas")
check(history() == h, "skapa misslyckad: citatet som väntar ligger kvar orört")

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
media, sess = publish_ok()
expected = f"https://raw.githubusercontent.com/erik/daily-wod/abc123/{first['bild']}"
check(seen == [expected] and len(media) == 1 and media[0]["image_url"] == expected, "publicera: en bild med rätt URL")
check(media[0]["caption"] == GOOD["caption"] and GOOD["quote"] in media[0]["alt_text"], "publicera: bildtext och alt-text med citatet")
check("is_carousel_item" not in media[0] and posts(sess)[-1][1] == "v25.0/111/media_publish", "publicera: vanlig bild, inte karusell")
h = history()
check(h[0]["status"] == "publicerad" and h[0]["permalink"] == "https://ig/p/citat", "publicera: status och länk sparas")
check((WORK / "wods.json").read_text() == wods_before, "publicera: wods.json är orörd")
waiting = {Path(rel).name for w in json.loads(wods_before) if w.get("status") == "redo"
           for rel in (w.get("bilder") or ([w["bild"]] if w.get("bild") else []))}
left = {p.name for p in (WORK / "bilder").glob("*.jpg")}
check(left == {Path(first["bild"]).name} | waiting and (WORK / "bilder" / ".gitkeep").exists(), "publicera: gamla bilder tas bort, bara senaste citatet (och väntande WOD) kvar")

llm, sess, ig = fresh()
err = run(["citat-publicera", "--auto"], ig=ig, llm=llm)
check(err and "väntar" in err and not posts(sess), "publicera igen: ingen dubbelpost när inget väntar")

# 4. Flera citat samma dag
second_q = dict(GOOD, quote="Consistency beats intensity.", highlight="Consistency")
llm, sess, ig = fresh([reply(second_q)])
run(["citat-skapa", "--auto"], ig=ig, llm=llm)
h = history()
check(len(llm.messages.calls) == 1 and h[0]["quote"] == second_q["quote"] and h[0]["id"] != first["id"], "flera om dagen: ett nytt citat skapas samma dag")
check(first["quote"] in llm.messages.calls[0]["messages"][0]["content"], "flera om dagen: det förra citatet finns i historiken")
media, sess = publish_ok()
check(len(media) == 1 and media[0]["image_url"].endswith(h[0]["bild"]), "flera om dagen: det nya citatet publiceras")
h = history()
check([q["status"] for q in h[:2]] == ["publicerad", "publicerad"] and quote_images() == {Path(h[0]["bild"]).name}, "flera om dagen: båda publicerade, bara den senaste bilden kvar")

# 5. Ett citat som aldrig publicerades ersätts av nästa körning
llm, sess, ig = fresh([reply(GOOD)])
run(["citat-skapa", "--auto"], ig=ig, llm=llm)
stuck = history()[0]
llm, sess, ig = fresh([reply(second_q)])
run(["citat-skapa", "--auto"], ig=ig, llm=llm)
h = history()
check(h[0]["status"] == "redo" and h[1]["id"] == stuck["id"] and h[1]["status"] == "avbruten", "avbrutet: det gamla väntande citatet markeras som avbrutet")
media, sess = publish_ok()
check(len(media) == 1 and media[0]["image_url"].endswith(h[0]["bild"]), "avbrutet: bara det nya citatet publiceras")
check(not (WORK / stuck["bild"]).exists(), "avbrutet: bilden till det avbrutna citatet städas bort")

# 6. Torrkörning sparar inget
before, images_before = history(), quote_images()
llm, sess, ig = fresh([reply(GOOD)])
run(["citat-skapa", "--auto", "--torrkorning"], ig=ig, llm=llm)
check(len(quote_images()) == len(images_before) + 1 and history() == before, "torrkörning: bilden görs men inget sparas")

# 7. Interaktivt med feedback
answers = iter(["f", "Shorter please", "j"])
orig_input, orig_tty = builtins.input, sys.stdin.isatty
builtins.input = lambda prompt="": next(answers)
sys.stdin.isatty = lambda: True
second = dict(GOOD, quote="Progress over perfection.", highlight="Progress")
llm, sess, ig = fresh([reply(GOOD), reply(second)])
run(["citat-skapa"], ig=ig, llm=llm)
builtins.input, sys.stdin.isatty = orig_input, orig_tty
fb = llm.messages.calls[1]["messages"][0]["content"]
check("Shorter please" in fb and GOOD["quote"] in fb, "interaktivt: feedback och förra förslaget skickas")
check(history()[0]["quote"] == "Progress over perfection.", "interaktivt: den nya versionen sparas")

# 8. Markeringen hittar orden oavsett skiljetecken och versaler
check(highlight_span("Show up tired. Leave proud.", "leave PROUD") == (3, 5), "markering: skiljetecken och versaler ignoreras")
check(highlight_span("Show up tired.", "show tired") is None, "markering: orden måste stå i följd")

# 9. Långt citat ryms: inga mörka pixlar i marginalerna
long_q = {"quote": "Uncomfortable reps today become the strength everyone calls natural talent tomorrow.",
          "highlight": "natural talent"}
im = Image.open(render_quote_image(long_q, "dailywod.engine", WORK / "bilder" / "lang.jpg")).convert("L")
w, hgt = im.size
edges = [im.getpixel((x, y)) for y in range(0, hgt, 4) for x in list(range(0, 50)) + list(range(w - 50, w))]
check(min(edges) > 120, "långt citat: ingen text i sidomarginalerna")

print("\nALLA TESTER OK" if ok else "\nNÅGOT TEST MISSLYCKADES")
sys.exit(0 if ok else 1)
