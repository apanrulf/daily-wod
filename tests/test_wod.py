"""Testar WOD-flödet mot mockade Instagram- och Claude-API:er."""
import builtins
import contextlib
import io
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace as NS
from zoneinfo import ZoneInfo

from PIL import Image

import tempfile

# Kör: python tests/test_wod.py  (kopierar projektet till en temporär mapp)
SRC = Path(__file__).resolve().parents[1]
WORK = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp()) / "projekt"
if WORK.exists():
    shutil.rmtree(WORK)
shutil.copytree(SRC, WORK, ignore=shutil.ignore_patterns("__pycache__", "tests", ".git", ".venv", ".env"))
(WORK / ".env").write_text("ANTHROPIC_API_KEY=sk-test\nIG_ACCESS_TOKEN=OLD\n")
TODAY = datetime.now(ZoneInfo("Europe/Stockholm")).date().isoformat()
(WORK / "wods.json").write_text(json.dumps([
    {"datum": "2000-01-02", "veckodag": "Sunday", "title": "Old Two", "format": "AMRAP 12",
     "workout": ["10 Burpees"], "focus": ["engine"], "status": "publicerad"},
    {"datum": "2000-01-01", "veckodag": "Saturday", "title": "Old One", "format": "For Time",
     "workout": ["Row 2k"], "focus": ["engine"], "status": "publicerad"},
]))
sys.path.insert(0, str(WORK))
os.chdir(WORK)
os.environ.pop("BILD_BAS_URL", None)
os.environ.pop("WOD_FORMAT", None)

import main as M  # noqa: E402

GOOD = {
    "title": "Grip & Rip", "format": "For Time · 12 min cap",
    "workout": ["21-15-9", "Thrusters (43/30 kg · 95/65 lb)", "Chest-to-bar pull-ups"],
    "scaling": ["Thrusters 30/20 kg", "Jumping pull-ups"],
    "stimulus": "Fast and spicy.", "focus": ["legs", "pull"],
    "strategy": ["Break thrusters early: 11-10, 8-7, 9.", "Quick singles on pull-ups."],
    "levels": [{"name": "Rx", "text": "As written."},
               {"name": "Intermediate", "text": "Thrusters 35/25 kg, jumping pull-ups."},
               {"name": "Beginner", "text": "Thrusters 20/15 kg, ring rows."}],
    "caption": "Grip it and rip it 🔥\n\n21-15-9 ...\n\nDrop your time below 👇\n#wod #metcon #fortime",
}


class Resp:
    def __init__(self, data, status=200):
        self._d, self.status_code, self.text = data, status, json.dumps(data)

    def json(self):
        return self._d


class FakeSession:
    def __init__(self):
        self.calls = []
        self.containers = 0

    def request(self, method, url, params=None, json=None, timeout=None):
        path = url.split("graph.instagram.com/")[1]
        self.calls.append((method, path, json))
        if path == "v25.0/me":
            return Resp({"data": [{"user_id": "111", "username": "dailywod.engine"}]})
        if path.endswith("content_publishing_limit"):
            return Resp({"data": [{"quota_usage": 0, "config": {"quota_total": 100}}]})
        if path == "v25.0/111/media" and method == "POST":
            self.containers += 1  # unikt id per container så att ordningen i children går att kontrollera
            return Resp({"id": f"cont{self.containers}"})
        if path.startswith("v25.0/cont"):
            return Resp({"status_code": "FINISHED"})
        if path == "v25.0/111/media_publish":
            return Resp({"id": "pub1"})
        if path == "v25.0/pub1":
            return Resp({"permalink": "https://ig/p/wod"})
        if path == "refresh_access_token":
            return Resp({"access_token": "NEW-TOKEN", "expires_in": 5184000})
        return Resp({"error": {"message": f"okänd sökväg {path}"}}, 400)


class FakeMessages:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    def create(self, **kw):
        self.calls.append(json.loads(json.dumps(kw, default=str)))
        text = self.replies.pop(0)
        return NS(stop_reason="end_turn", content=[NS(type="text", text=text)])


def wod_reply(d):
    return f"<wod>{json.dumps(d, ensure_ascii=False)}</wod>"


def fresh(replies=()):
    llm = NS(messages=FakeMessages(replies))
    sess = FakeSession()
    ig = M.InstagramClient("OLD", session=sess)
    ig.poll_delay = 0
    return llm, sess, ig


def posts(sess):
    return [c for c in sess.calls if c[0] == "POST"]


def media_posts(sess):
    return [c[2] for c in posts(sess) if c[1] == "v25.0/111/media"]


def history():
    return json.loads((WORK / "wods.json").read_text())


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


# 1. Skapa: första svaret ogiltigt (CrossFit), andra trasig JSON, tredje bra
bad = dict(GOOD, caption="Classic CrossFit style #crossfit")
llm, sess, ig = fresh([wod_reply(bad), "<wod>{inte json</wod>", wod_reply(GOOD)])
run(["wod-skapa", "--auto"], ig=ig, llm=llm)
h = history()
check(len(llm.messages.calls) == 3, "skapa: försöker igen efter CrossFit-ord och trasig JSON")
check("CrossFit" in llm.messages.calls[1]["messages"][-1]["content"], "skapa: felet skickas tillbaka till Claude")
check(h[0]["datum"] == TODAY and h[0]["status"] == "redo" and h[0]["title"] == "Grip & Rip", "skapa: dagens WOD sparas som redo, nyast först")
check(len(h) == 3 and h[1]["title"] == "Old Two", "skapa: historiken behålls")
prompt = llm.messages.calls[0]["messages"][0]["content"]
check("Old Two" in prompt and "Old One" in prompt and prompt.index("Old Two") < prompt.index("Old One"), "skapa: historiken skickas till coachen, nyast först")
check("Never use the word \"CrossFit\"" in llm.messages.calls[0]["system"] and "Veckoschema" in llm.messages.calls[0]["system"], "skapa: regler och varumärke i systemprompten")
CAROUSEL = [f"bilder/{TODAY}-{i}.jpg" for i in (1, 2, 3)]
imgs = [Image.open(WORK / rel) for rel in CAROUSEL]
check(all(i.size == (1080, 1350) and i.format == "JPEG" for i in imgs), "skapa: tre bilder i 1080x1350 JPEG")
check(h[0]["bilder"] == CAROUSEL and "bild" not in h[0], "skapa: bilderna sparas som en lista i wods.json")
check(h[0]["strategy"] == GOOD["strategy"] and [lv["name"] for lv in h[0]["levels"]] == ["Rx", "Intermediate", "Beginner"], "skapa: tempotips och nivåer sparas")
check(not posts(sess), "skapa: inget publiceras")
check('"strategy"' in llm.messages.calls[0]["system"] and '"levels"' in llm.messages.calls[0]["system"], "skapa: coachen ombeds skriva strategy och levels")

# 1b. Ogiltiga strategy och levels skickas tillbaka, sedan ett giltigt svar
no_strategy = {k: v for k, v in GOOD.items() if k != "strategy"}
two_levels = dict(GOOD, levels=GOOD["levels"][:2])
llm, sess, ig = fresh([wod_reply(no_strategy), wod_reply(two_levels), wod_reply(GOOD)])
run(["wod-skapa", "--auto", "--nytt"], ig=ig, llm=llm)
retries = [c["messages"][-1]["content"] for c in llm.messages.calls[1:]]
check(len(llm.messages.calls) == 3, "validering: försöker igen efter fel i strategy och levels")
check('"strategy"' in retries[0] and '"levels"' in retries[1] and "exakt 3" in retries[1], "validering: felen beskrivs på svenska för Claude")
check(history()[0]["bilder"] == CAROUSEL, "validering: det giltiga svaret sparas")

# 2. Skapa igen: hoppar över
llm, sess, ig = fresh([])
run(["wod-skapa", "--auto"], ig=ig, llm=llm)
check(not llm.messages.calls, "skapa igen: inget nytt anrop när dagens finns")

# 3. Publicera utan BILD_BAS_URL
llm, sess, ig = fresh()
err = run(["wod-publicera", "--auto"], ig=ig, llm=llm)
check(err and "BILD_BAS_URL" in err and not posts(sess), "publicera: kräver BILD_BAS_URL")

# Gamla bilder och ett citat som väntar på publicering, för att testa städningen
for name in ("2000-01-01-1.jpg", "citat-2000-01-01.jpg", "citat-vantar.jpg"):
    shutil.copy(WORK / "exempel.jpg", WORK / "bilder" / name)
(WORK / "citat.json").write_text(json.dumps([
    {"datum": "2000-01-05", "quote": "Waiting.", "bild": "bilder/citat-vantar.jpg", "status": "redo"}]))

# 4. Publicera när bilden inte går att nå
os.environ["BILD_BAS_URL"] = "https://raw.githubusercontent.com/erik/daily-wod/abc123/"
M.url_is_live = lambda url: False
llm, sess, ig = fresh()
err = run(["wod-publicera", "--auto"], ig=ig, llm=llm)
check(err and "publika adressen" in err and not posts(sess), "publicera: stoppar om bilden inte går att nå")
check((WORK / "bilder" / "2000-01-01-1.jpg").exists(), "publicera misslyckad: inga bilder tas bort")

# 5. Publicera på riktigt
seen = []
M.url_is_live = lambda url: seen.append(url) or True
llm, sess, ig = fresh()
run(["wod-publicera", "--auto"], ig=ig, llm=llm)
expected_urls = [f"https://raw.githubusercontent.com/erik/daily-wod/abc123/{rel}" for rel in CAROUSEL]
media = media_posts(sess)
items = [b for b in media if b.get("is_carousel_item")]
carousel = [b for b in media if b.get("media_type") == "CAROUSEL"]
check(seen == expected_urls, "publicera: alla tre bilderna kontrolleras")
check([b["image_url"] for b in items] == expected_urls and len(items) == 3, "publicera: tre is_carousel_item-anrop i rätt ordning")
check(all("caption" not in b for b in items), "publicera: ingen bildtext på de enskilda bilderna")
check(items[0]["alt_text"].startswith("Workout of the day: Grip & Rip") and "Levels:" in items[1]["alt_text"], "publicera: alt-text per bild")
check(len(carousel) == 1 and carousel[0]["children"] == "cont1,cont2,cont3" and carousel[0]["caption"] == GOOD["caption"], "publicera: CAROUSEL-anrop med children i rätt ordning")
order = [c[1] for c in posts(sess)]
check(order[-1] == "v25.0/111/media_publish" and posts(sess)[-1][2] == {"creation_id": "cont4"} and order.count("v25.0/111/media_publish") == 1, "publicera: media_publish sist, med karusellens id")
h = history()
check(h[0]["status"] == "publicerad" and h[0]["permalink"] == "https://ig/p/wod", "publicera: status och länk sparas")
left = sorted(p.name for p in (WORK / "bilder").glob("*.jpg"))
check(left == sorted([Path(rel).name for rel in CAROUSEL] + ["citat-vantar.jpg"]), "publicera: gamla bilder tas bort, dagens och väntande citat behålls")
check((WORK / "bilder" / ".gitkeep").exists(), "publicera: .gitkeep ligger kvar")

# 6. Publicera igen: inget händer
llm, sess, ig = fresh()
run(["wod-publicera", "--auto"], ig=ig, llm=llm)
check(not posts(sess), "publicera igen: ingen dubbelpost")
llm, sess, ig = fresh([])
run(["wod-skapa", "--auto", "--nytt"], ig=ig, llm=llm)
check(not llm.messages.calls, "skapa --nytt: skriver inte över ett publicerat WOD")

# 7. Torrkörning av skapa på en ny dag (ta bort dagens först)
(WORK / "wods.json").write_text(json.dumps(h[1:]))
for rel in CAROUSEL:
    (WORK / rel).unlink()
llm, sess, ig = fresh([wod_reply(GOOD)])
run(["wod-skapa", "--auto", "--torrkorning"], ig=ig, llm=llm)
check(all((WORK / rel).exists() for rel in CAROUSEL) and len(history()) == 2, "torrkörning: bilderna görs men inget sparas")

# 8. Interaktivt med feedback
answers = iter(["f", "More running please", "j"])
orig_input, orig_tty = builtins.input, sys.stdin.isatty
builtins.input = lambda prompt="": next(answers)
sys.stdin.isatty = lambda: True
second = dict(GOOD, title="Run & Gun", workout=["5 rounds:", "400 m run", "15 Thrusters (43/30 kg · 95/65 lb)"])
llm, sess, ig = fresh([wod_reply(GOOD), wod_reply(second)])
run(["wod-skapa"], ig=ig, llm=llm)
builtins.input, sys.stdin.isatty = orig_input, orig_tty
fb = llm.messages.calls[1]["messages"][0]["content"]
check("More running please" in fb and "Grip & Rip" in fb, "interaktivt: feedback och förra förslaget skickas")
check(history()[0]["title"] == "Run & Gun", "interaktivt: den nya versionen sparas")

# 9. Tre misslyckade svar ger tydligt fel
(WORK / "wods.json").write_text(json.dumps(history()[1:]))
llm, sess, ig = fresh(["nej", "nej", "nej"])
err = run(["wod-skapa", "--auto", "--nytt"], ig=ig, llm=llm)
check(err and "efter 3 försök" in err, "fel: tydligt meddelande efter tre misslyckade försök")

# 10. fornya-token --till-stdout skriver bara token
llm, sess, ig = fresh()
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    run(["fornya-token", "--till-stdout"], ig=ig, llm=llm)
check(buf.getvalue() == "NEW-TOKEN\n", "token: bara token på stdout")
check("NEW-TOKEN" not in (WORK / ".env").read_text(), "token: .env lämnas orörd")

# 11. status visar dagens WOD
llm, sess, ig = fresh()
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    run(["status"], ig=ig, llm=llm)
check("Dagens WOD:" in buf.getvalue(), "status: visar dagens WOD")

# 12. WOD_FORMAT=bild ger fortfarande en enda bild
(WORK / "wods.json").write_text(json.dumps([w for w in history() if w["datum"] != TODAY]))
os.environ["WOD_FORMAT"] = "bild"
llm, sess, ig = fresh([wod_reply(GOOD)])
run(["wod-skapa", "--auto"], ig=ig, llm=llm)
h = history()
single = Image.open(WORK / "bilder" / f"{TODAY}.jpg")
check(h[0]["bilder"] == [f"bilder/{TODAY}.jpg"] and single.size == (1080, 1350), "bildläge: en bild i 1080x1350")
llm, sess, ig = fresh()
run(["wod-publicera", "--auto"], ig=ig, llm=llm)
media = media_posts(sess)
check(len(media) == 1 and media[0]["caption"] == GOOD["caption"] and "is_carousel_item" not in media[0], "bildläge: publiceras som en vanlig bild")
os.environ.pop("WOD_FORMAT")

# 13. Äldre poster med "bild" som sträng publiceras fortfarande
old = dict(GOOD, datum=TODAY, veckodag="Thursday", bild=f"bilder/{TODAY}.jpg", status="redo")
(WORK / "wods.json").write_text(json.dumps([old] + [w for w in history() if w["datum"] != TODAY]))
llm, sess, ig = fresh()
run(["wod-publicera", "--auto"], ig=ig, llm=llm)
media = media_posts(sess)
check(len(media) == 1 and media[0]["image_url"].endswith(f"bilder/{TODAY}.jpg") and history()[0]["status"] == "publicerad", "äldre post: fältet bild fungerar")

# 14. Bild 3 är samma för samma datum och olika för olika datum
from datetime import date  # noqa: E402

from karusell import render_share_slide  # noqa: E402

out = WORK / "bilder" / "dela"
def slide3(d, name):
    return Image.open(render_share_slide(d, "dailywod.engine", out / name)).tobytes()
a1, a2 = slide3(date(2026, 3, 1), "a1.jpg"), slide3(date(2026, 3, 1), "a2.jpg")
others = [slide3(date(2026, 3, d), f"b{d}.jpg") for d in range(2, 6)]
check(a1 == a2, "bild 3: samma datum ger samma bild")
check(all(o != a1 for o in others) and len(set(others)) == len(others), "bild 3: olika datum ger olika bilder")

print("\nALLA TESTER OK" if ok else "\nNÅGOT TEST MISSLYCKADES")
sys.exit(0 if ok else 1)
