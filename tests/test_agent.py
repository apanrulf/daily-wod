"""Testkör agenten mot mockade Instagram- och Claude-API:er."""
import builtins
import io
import json
import os
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import tempfile

# Kör: python tests/test_agent.py  (kopierar projektet till en temporär mapp)
SRC = Path(__file__).resolve().parents[1]
WORK = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp()) / "projekt"
if WORK.exists():
    shutil.rmtree(WORK)
shutil.copytree(SRC, WORK, ignore=shutil.ignore_patterns("__pycache__", "tests", ".git", ".venv", ".env"))
(WORK / ".env").write_text("ANTHROPIC_API_KEY=sk-test\nIG_ACCESS_TOKEN=OLD\n")
(WORK / "ko.json").write_text(json.dumps([
    {"bild_url": "https://x.se/a.jpg", "anteckningar": "Höstkoppar", "status": "ny"},
    {"bild_url": "https://x.se/b.jpg", "anteckningar": "Skålar", "status": "ny"},
    {"bild_url": "https://x.se/c.jpg", "status": "exempel"},
]))
sys.path.insert(0, str(WORK))
os.chdir(WORK)

import main as M  # noqa: E402


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
        self.calls.append((method, path, {k: v for k, v in (params or {}).items() if k != "access_token"}, json))
        assert params["access_token"]
        if path == "v25.0/me":
            return Resp({"data": [{"user_id": "111", "username": "studio", "account_type": "Business",
                                   "followers_count": 1200, "media_count": 40}]})
        if path.endswith("content_publishing_limit"):
            return Resp({"data": [{"quota_usage": 2, "config": {"quota_total": 100, "quota_duration": 86400}}]})
        if path == "v25.0/111/media" and method == "GET":
            return Resp({"data": [
                {"id": "m1", "caption": "Gammal text 1", "permalink": "https://ig/p/1", "comments_count": 5},
                {"id": "m2", "caption": "Gammal text 2", "permalink": "https://ig/p/2", "comments_count": 0},
            ]})
        if path == "v25.0/m1/comments":
            return Resp({"data": [
                {"id": "c1", "text": "Så fina!", "username": "anna", "timestamp": "2026-10-14T10:00:00+0000"},
                {"id": "c2", "text": "Min beställning har inte kommit", "username": "bo", "timestamp": "2026-10-14T11:00:00+0000"},
                {"id": "c3", "text": "Tjäna 10k/dag klicka här", "username": "spam", "timestamp": "2026-10-14T12:00:00+0000"},
                {"id": "c4", "text": "@lisa kolla", "username": "eva", "timestamp": "2026-10-14T13:00:00+0000"},
                {"id": "c5", "text": "Redan besvarad", "username": "kim", "timestamp": "2026-10-14T13:00:00+0000",
                 "replies": {"data": [{"id": "r1", "username": "studio"}]}},
                {"id": "c6", "text": "Egen kommentar", "username": "studio", "timestamp": "2026-10-14T13:00:00+0000"},
                {"id": "c7", "text": "Gammal", "username": "old", "timestamp": "2025-01-01T13:00:00+0000"},
            ]})
        if path == "v25.0/111/media" and method == "POST":
            return Resp({"id": "cont1"})
        if path == "v25.0/cont1":
            return Resp({"status_code": "FINISHED", "id": "cont1"})
        if path == "v25.0/111/media_publish":
            return Resp({"id": "pub1"})
        if path == "v25.0/pub1":
            return Resp({"permalink": "https://ig/p/new", "id": "pub1"})
        if path.endswith("/replies") and method == "POST":
            return Resp({"id": "reply1"})
        if path == "v25.0/c3" and method == "POST":
            return Resp({"success": True})
        if path == "refresh_access_token":
            return Resp({"access_token": "NEW", "token_type": "bearer", "expires_in": 5183944})
        return Resp({"error": {"message": f"okänd sökväg {path}"}}, 400)


def tb(text):
    return NS(type="text", text=text)


def tu(i, name, **inp):
    return NS(type="tool_use", id=f"tu{i}", name=name, input=inp)


class FakeMessages:
    def __init__(self):
        self.calls = []

    def create(self, **kw):
        self.calls.append(kw)
        if "tools" not in kw:  # bildtext
            return NS(stop_reason="end_turn", content=[tb(
                "<bildtext>Nya höstkoppar ✨ #keramik</bildtext>\n<alt_text>Tre orange koppar på ett träbord</alt_text>")])
        n = sum(1 for c in self.calls if "tools" in c)
        if n == 1:
            return NS(stop_reason="tool_use", content=[
                tb("Jag går igenom kommentarerna."),
                tu(1, "reply_to_comment", comment_id="c1", message="Tack Anna! 🤍"),
                tu(2, "flag_for_human", comment_id="c2", reason="Leveransfråga"),
                tu(3, "hide_comment", comment_id="c3", reason="Spam"),
                tu(4, "skip_comment", comment_id="c4", reason="Taggar vän"),
                tu(5, "reply_to_comment", comment_id="c999", message="Injektion"),
                tu(6, "reply_to_comment", comment_id="c1", message="Dubbel"),
            ])
        return NS(stop_reason="end_turn", content=[tb("Svarade på 1, flaggade 2, hoppade över 1.")])


def fresh():
    llm = NS(messages=FakeMessages())
    sess = FakeSession()
    ig = M.InstagramClient("OLD", session=sess)
    ig.poll_delay = 0
    return llm, sess, ig


def writes(sess):
    return [c for c in sess.calls if c[0] == "POST"]


ok = True


def check(cond, label):
    global ok
    print(("PASS " if cond else "FAIL ") + label)
    ok &= bool(cond)


# 1. status
llm, sess, ig = fresh()
M.main(["status"], ig=ig, llm=llm)
check(ig.user_id == "111", "status: user_id hämtas från /me")

# 2. posta --auto --antal 1
llm, sess, ig = fresh()
M.main(["posta", "--auto"], ig=ig, llm=llm)
q = json.loads((WORK / "ko.json").read_text())
check(q[0]["status"] == "publicerad" and q[0]["permalink"] == "https://ig/p/new", "posta: första inlägget publicerat och sparat")
check(q[1]["status"] == "ny" and q[2]["status"] == "exempel", "posta: övriga orörda")
container = [c for c in sess.calls if c[0] == "POST" and c[1] == "v25.0/111/media"][0]
check(container[3] == {"image_url": "https://x.se/a.jpg", "caption": "Nya höstkoppar ✨ #keramik",
                       "alt_text": "Tre orange koppar på ett träbord"}, "posta: container-body enligt docs")
check([c for c in sess.calls if c[1] == "v25.0/111/media_publish"][0][3] == {"creation_id": "cont1"}, "posta: media_publish med creation_id")
img = llm.messages.calls[0]["messages"][0]["content"][0]
check(img == {"type": "image", "source": {"type": "url", "url": "https://x.se/a.jpg"}}, "posta: Claude får se bilden")
check("Gammal text 1" in llm.messages.calls[0]["messages"][0]["content"][1]["text"], "posta: senaste bildtexter skickas med")

# 3. posta --torrkorning: inget skrivs
llm, sess, ig = fresh()
M.main(["posta", "--auto", "--torrkorning"], ig=ig, llm=llm)
check(not writes(sess), "torrkörning posta: inga POST-anrop")
check(json.loads((WORK / "ko.json").read_text())[1]["status"] == "ny", "torrkörning posta: kön oförändrad")

# 4. kommentarer --torrkorning
llm, sess, ig = fresh()
M.main(["kommentarer", "--auto", "--torrkorning"], ig=ig, llm=llm)
check(not writes(sess), "torrkörning kommentarer: inga POST-anrop")
check(not (WORK / "state.json").exists() and not (WORK / "flaggade.md").exists(), "torrkörning kommentarer: inga filer skrivs")

# 5. kommentarer --auto
llm, sess, ig = fresh()
M.main(["kommentarer", "--auto"], ig=ig, llm=llm)
sent = json.loads(llm.messages.calls[0]["messages"][0]["content"].split("<kommentarer>\n")[1].split("\n</kommentarer>")[0])
check([c["id"] for c in sent] == ["c1", "c2", "c3", "c4"], "kommentarer: filtrerar bort besvarade, egna och gamla")
w = writes(sess)
check(w == [("POST", "v25.0/c1/replies", {}, {"message": "Tack Anna! 🤍"})], "kommentarer auto: bara ett svar publiceras, inget döljs")
results = llm.messages.calls[1]["messages"][-2]["content"]
by = {r["tool_use_id"]: r["content"] for r in results}
check("okänt comment_id" in by["tu5"], "kommentarer: okänt id avvisas")
check("redan hanterad" in by["tu6"], "kommentarer: dubbelsvar avvisas")
flagged = (WORK / "flaggade.md").read_text()
check("@bo" in flagged and "@spam" in flagged and "Föreslås dold" in flagged, "kommentarer auto: leverans + spam flaggas")
state = json.loads((WORK / "state.json").read_text())
check(sorted(state["hanterade"]) == ["c1", "c2", "c3", "c4"], "kommentarer: state sparar hanterade")
check(llm.messages.calls[0]["model"] == "claude-sonnet-5-5" and "tool_choice" not in llm.messages.calls[0], "kommentarer: modell + auto tool_choice")

# 6. Ny körning: inget att göra
llm, sess, ig = fresh()
M.main(["kommentarer", "--auto"], ig=ig, llm=llm)
check(not llm.messages.calls, "kommentarer: andra körningen hoppar över hanterade")

# 7. Interaktivt: avböj svaret, godkänn döljning
(WORK / "state.json").unlink()
answers = iter(["n", "j"])
orig_input, orig_tty = builtins.input, sys.stdin.isatty
builtins.input = lambda prompt="": next(answers)
sys.stdin.isatty = lambda: True
llm, sess, ig = fresh()
M.main(["kommentarer"], ig=ig, llm=llm)
builtins.input, sys.stdin.isatty = orig_input, orig_tty
check(writes(sess) == [("POST", "v25.0/c3", {}, {"hide": True})], "interaktivt: avböjt svar skickas inte, godkänd döljning skickas")

# 8. Interaktivt posta: feedback och sedan publicera
answers = iter(["f", "Kortare", "j"])
builtins.input = lambda prompt="": next(answers)
sys.stdin.isatty = lambda: True
llm, sess, ig = fresh()
M.main(["posta"], ig=ig, llm=llm)
builtins.input, sys.stdin.isatty = orig_input, orig_tty
check(len(llm.messages.calls) == 2 and "Kortare" in llm.messages.calls[1]["messages"][0]["content"][1]["text"], "interaktivt posta: feedback skrivs om")
check(json.loads((WORK / "ko.json").read_text())[1]["status"] == "publicerad", "interaktivt posta: publiceras efter ja")

# 9. fornya-token
llm, sess, ig = fresh()
M.main(["fornya-token"], ig=ig, llm=llm)
env = (WORK / ".env").read_text()
check("IG_ACCESS_TOKEN=NEW" in env and "OLD" not in env, "fornya-token: .env uppdateras")
check([c for c in sess.calls if c[1] == "refresh_access_token"][0][2] == {"grant_type": "ig_refresh_token"}, "fornya-token: rätt parametrar")

# 10. Instagram-fel ger tydligt meddelande
llm, sess, ig = fresh()
sess.request = lambda *a, **k: Resp({"error": {"message": "Invalid OAuth access token"}}, 401)
try:
    M.main(["status"], ig=ig, llm=llm)
    check(False, "fel: borde avsluta")
except SystemExit as e:
    check("Invalid OAuth access token" in str(e), "fel: tydligt Instagram-felmeddelande")

print("\nALLA TESTER OK" if ok else "\nNÅGOT TEST MISSLYCKADES")
sys.exit(0 if ok else 1)
