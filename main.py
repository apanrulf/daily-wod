"""Instagram-agenten. Kör `python main.py --help` för en översikt."""
import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import anthropic
import requests
from dotenv import set_key

from agent import Approver, CommentAgent, write_caption
from bild import render_wod_image
from config import ENV_PATH, load_config
from instagram_api import InstagramClient, InstagramError
from karusell import render_carousel
from wod import alt_text, carousel_alt_texts, generate_wod


# ---------- hjälpare ----------

def load_json(path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def parse_ts(ts):
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S%z")
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)


def today(cfg):
    return datetime.now(ZoneInfo(cfg.timezone)).date()


def url_is_live(url, attempts=24, delay=5):
    """Väntar tills bilden går att hämta publikt. GitHub kan behöva en stund efter push."""
    for i in range(attempts):
        try:
            r = requests.head(url, timeout=15, allow_redirects=True)
            if r.status_code == 200 and r.headers.get("content-type", "").startswith("image/jpeg"):
                return True
        except requests.RequestException:
            pass
        if i < attempts - 1:
            time.sleep(delay)
    return False


def image_paths(entry):
    """Relativa bildsökvägar för ett WOD. Äldre poster har en sträng i "bild", nyare en lista i "bilder"."""
    if entry.get("bilder"):
        return list(entry["bilder"])
    return [entry["bild"]] if entry.get("bild") else []


def require_terminal(args):
    if not args.auto and not sys.stdin.isatty():
        sys.exit("Ingen terminal att fråga i. Kör interaktivt, eller lägg till --auto.")


# ---------- kommandon ----------

def cmd_status(cfg, ig, llm, args):
    me = ig.me()
    used, total = ig.publishing_limit()
    queue = load_json(cfg.queue_path, [])
    waiting = sum(1 for p in queue if p.get("status") == "ny")
    print(f"Inloggad som @{me.get('username')} ({me.get('account_type')})")
    print(f"Följare: {me.get('followers_count')} · Inlägg: {me.get('media_count')}")
    print(f"Publiceringar senaste dygnet: {used} av {total}")
    print(f"Inlägg som väntar i kön: {waiting}")
    day = today(cfg).isoformat()
    wod_today = next((w for w in load_json(cfg.wod_path, []) if w.get("datum") == day), None)
    print(f"Dagens WOD: {wod_today['title']} ({wod_today['status']})" if wod_today else "Dagens WOD: inte skapat än")
    print("Kopplingen till Instagram fungerar.")


def cmd_post(cfg, ig, llm, args):
    queue = load_json(cfg.queue_path, [])
    pending = [p for p in queue if p.get("status") == "ny"]
    if not pending:
        print('Inga inlägg med status "ny" i ko.json.')
        return

    used, total = ig.publishing_limit()
    room = total - used
    recent = [m.get("caption") for m in ig.recent_media(5) if m.get("caption")]
    approver = None if args.auto else Approver(auto=False)
    published = 0

    for item in pending[: args.antal]:
        if published >= room:
            print("Dygnsgränsen för publiceringar är nådd. Resten väntar till nästa körning.")
            break
        url = (item.get("bild_url") or "").strip()
        if not url.startswith("https://"):
            print(f"Hoppar över ett inlägg utan giltig bild_url: {url!r}")
            continue
        if not url.lower().split("?")[0].endswith((".jpg", ".jpeg")):
            print(f"Obs: Instagram tar bara emot JPEG. Kontrollera {url}")

        print(f"\nSkriver bildtext till {url} ...")
        caption, alt = write_caption(llm, cfg, url, item.get("anteckningar"), recent)

        if approver:
            while True:
                print(f"\n{'=' * 60}\nBILD: {url}\n\n{caption}\n\nAlt-text: {alt}\n{'=' * 60}")
                choice = input("[j] publicera  [f] ge feedback  [r] redigera själv  [n] hoppa över: ").strip().lower()
                if choice in ("j", "ja", "y"):
                    break
                if choice in ("n", "nej"):
                    caption = None
                    break
                if choice == "f":
                    feedback = input("Vad ska ändras? ").strip()
                    if feedback:
                        print("Skriver om ...")
                        caption, alt = write_caption(
                            llm, cfg, url, item.get("anteckningar"), recent,
                            previous=caption, feedback=feedback,
                        )
                if choice == "r":
                    edited = input("Klistra in din bildtext (en rad, använd \\n för radbrytning): ").strip()
                    if edited:
                        caption = edited.replace("\\n", "\n")
            if caption is None:
                print("Hoppade över. Inlägget ligger kvar i kön.")
                continue

        result = ig.publish_image(url, caption, alt)
        published += 1
        recent.insert(0, caption)
        if ig.dry_run:
            print(f"[torrkörning] Skulle ha publicerat:\n{caption}")
            continue
        item.update(
            status="publicerad",
            bildtext=caption,
            alt_text=alt,
            media_id=result["id"],
            permalink=result.get("permalink"),
            publicerad=datetime.now().isoformat(timespec="minutes"),
        )
        save_json(cfg.queue_path, queue)
        print(f"✓ Publicerat: {result.get('permalink') or result['id']}")

    print(f"\nKlart. {published} inlägg {'skulle ha publicerats' if ig.dry_run else 'publicerade'}.")


def cmd_comments(cfg, ig, llm, args):
    state = load_json(cfg.state_path, {"hanterade": []})
    handled = set(state.get("hanterade", []))
    me = ig.username
    cutoff = datetime.now(timezone.utc) - timedelta(days=args.dagar)

    todo = []
    for media in ig.recent_media(args.inlagg):
        if not media.get("comments_count"):
            continue
        for c in ig.comments(media["id"]):
            if c["id"] in handled or c.get("username") == me:
                continue
            replies = (c.get("replies") or {}).get("data", [])
            if any(r.get("username") == me for r in replies):
                continue
            if parse_ts(c.get("timestamp")) < cutoff:
                continue
            todo.append({
                "id": c["id"],
                "username": c.get("username") or "okänd",
                "text": c.get("text") or "",
                "timestamp": c.get("timestamp"),
                "inlagg": (media.get("caption") or "")[:200],
                "permalink": media.get("permalink"),
            })
        if len(todo) >= args.max:
            todo = todo[: args.max]
            break

    if not todo:
        print("Inga nya kommentarer att hantera.")
        return

    print(f"{len(todo)} nya kommentarer. Agenten går igenom dem ...")
    agent = CommentAgent(llm, cfg, ig, Approver(auto=args.auto))
    done, summary = agent.run(todo)

    if not ig.dry_run:
        state["hanterade"] = (state.get("hanterade", []) + sorted(done))[-5000:]
        save_json(cfg.state_path, state)

    c = agent.counts
    print(
        f"\nKlart: {c['svar']} svar, {c['flaggade']} flaggade, {c['dolda']} dolda, "
        f"{c['hoppade']} utan åtgärd, {c['avböjda']} avböjda av dig."
    )
    if summary:
        print(summary)
    if c["flaggade"] and not ig.dry_run:
        print(f"Flaggade kommentarer finns i {cfg.flagged_path.name}.")


def cmd_wod_create(cfg, ig, llm, args):
    day = today(cfg)
    history = load_json(cfg.wod_path, [])
    existing = next((w for w in history if w.get("datum") == day.isoformat()), None)
    if existing and existing.get("status") == "publicerad":
        print(f"Dagens WOD är redan publicerat: {existing.get('permalink') or existing.get('media_id')}")
        return
    if existing and not args.nytt:
        print(f'Dagens WOD finns redan ("{existing["title"]}"). Lägg till --nytt för att skriva ett nytt.')
        return
    require_terminal(args)

    past = [w for w in history if w.get("datum") != day.isoformat()]
    handle = cfg.ig_handle or ig.username

    def render(wod):
        if cfg.wod_format == "bild":
            paths = [render_wod_image(wod, day, handle, cfg.images_dir / f"{day.isoformat()}.jpg")]
        else:
            paths = render_carousel(wod, day, handle, cfg.images_dir, day.isoformat())
        return [f"{cfg.images_dir.name}/{p.name}" for p in paths]

    print(f"Coachen programmerar dagens WOD ({day.strftime('%A %Y-%m-%d')}) ...")
    wod = generate_wod(llm, cfg, past, day)
    while True:
        images = render(wod)
        print(f"\n{'=' * 60}\n{wod['title']} · {wod['format']}\n")
        print("\n".join(wod["workout"]))
        label = "Bild" if len(images) == 1 else "Bilder"
        print(f"\n{label}: {', '.join(images)}\n\nBildtext:\n{wod['caption']}\n{'=' * 60}")
        if args.auto:
            break
        choice = input("[j] spara  [f] ge feedback  [n] avbryt: ").strip().lower()
        if choice in ("j", "ja", "y"):
            break
        if choice in ("n", "nej"):
            print("Avbrutet. Inget sparat.")
            return
        if choice == "f":
            feedback = input("Vad ska ändras? ").strip()
            if feedback:
                print("Coachen skriver om ...")
                wod = generate_wod(llm, cfg, past, day, feedback=feedback, previous=wod)

    if ig.dry_run:
        print(f"[torrkörning] {label}: {', '.join(images)}. Inget sparas i {cfg.wod_path.name}.")
        return
    entry = {"datum": day.isoformat(), "veckodag": day.strftime("%A"), **wod,
             "bilder": images, "status": "redo"}
    history = sorted([entry] + past, key=lambda w: w.get("datum", ""), reverse=True)
    save_json(cfg.wod_path, history)
    print(f"✓ Sparat. {', '.join(images)} väntar på publicering.")


def cmd_wod_publish(cfg, ig, llm, args):
    day = today(cfg)
    history = load_json(cfg.wod_path, [])
    entry = next((w for w in history if w.get("datum") == day.isoformat()), None)
    if entry is None:
        sys.exit("Det finns inget WOD för i dag. Kör wod-skapa först.")
    if entry.get("status") == "publicerad":
        print(f"Dagens WOD är redan publicerat: {entry.get('permalink') or entry.get('media_id')}")
        return
    if not cfg.image_base_url:
        sys.exit("BILD_BAS_URL saknas. Den sätts automatiskt i GitHub Actions, se README.")
    require_terminal(args)

    images = image_paths(entry)
    if not images:
        sys.exit("Dagens WOD saknar bilder. Kör wod-skapa --nytt.")
    urls = [f"{cfg.image_base_url.rstrip('/')}/{rel}" for rel in images]
    for url in urls:
        print(f"Kontrollerar att Instagram kan hämta bilden: {url}")
        if not url_is_live(url):
            if not ig.dry_run:
                sys.exit("Bilden går inte att nå på den publika adressen. Är repot publikt och bilden pushad?")
            print("[torrkörning] Bilden går inte att nå än, men fortsätter.")

    used, total = ig.publishing_limit()
    if used >= total:
        sys.exit(f"Dygnsgränsen är nådd ({used} av {total}). Försök igen senare.")

    if not args.auto:
        print(f"\n{'=' * 60}\n{entry['caption']}\n{'=' * 60}")
        if input("Publicera nu? [j/n]: ").strip().lower() not in ("j", "ja", "y"):
            print("Inte publicerat.")
            return

    if len(urls) > 1:
        result = ig.publish_carousel(urls, entry["caption"], carousel_alt_texts(entry))
    else:
        result = ig.publish_image(urls[0], entry["caption"], alt_text(entry))
    if ig.dry_run:
        print("[torrkörning] Skulle ha publicerat dagens WOD.")
        return
    entry.update(
        status="publicerad",
        media_id=result["id"],
        permalink=result.get("permalink"),
        publicerad=datetime.now(ZoneInfo(cfg.timezone)).isoformat(timespec="minutes"),
    )
    save_json(cfg.wod_path, history)
    print(f"✓ Publicerat: {result.get('permalink') or result['id']}")


def cmd_refresh(cfg, ig, llm, args):
    token, expires = ig.refresh_token()
    if args.till_stdout:
        print(token)  # Används av GitHub Actions; inget annat får skrivas ut här.
        return
    set_key(str(ENV_PATH), "IG_ACCESS_TOKEN", token, quote_mode="never")
    print(f"Ny token sparad i .env. Den gäller i {expires // 86400} dagar.")


# ---------- CLI ----------

def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--auto", action="store_true",
                        help="Godkänn allt automatiskt (för schemalagda körningar).")
    common.add_argument("--torrkorning", action="store_true",
                        help="Läs från Instagram men publicera ingenting.")

    p = argparse.ArgumentParser(description="AI-agent som sköter ett Instagramkonto.")
    sub = p.add_subparsers(dest="kommando", required=True)

    sub.add_parser("status", parents=[common], help="Kontrollera att allt är kopplat.")

    sw = sub.add_parser("wod-skapa", parents=[common], help="Skriv dagens WOD och gör bilden.")
    sw.add_argument("--nytt", action="store_true", help="Skriv ett nytt även om dagens redan finns.")

    sub.add_parser("wod-publicera", parents=[common], help="Publicera dagens WOD på Instagram.")

    sp = sub.add_parser("posta", parents=[common], help="Skriv bildtext och publicera nästa inlägg i kön.")
    sp.add_argument("--antal", type=int, default=1, help="Hur många inlägg från kön (standard 1).")

    sc = sub.add_parser("kommentarer", parents=[common], help="Gå igenom och svara på nya kommentarer.")
    sc.add_argument("--inlagg", type=int, default=10, help="Hur många av de senaste inläggen (standard 10).")
    sc.add_argument("--dagar", type=int, default=14, help="Ignorera kommentarer äldre än så (standard 14).")
    sc.add_argument("--max", type=int, default=30, help="Max antal kommentarer per körning (standard 30).")

    sr = sub.add_parser("fornya-token", parents=[common], help="Förläng Instagram-token med 60 dagar.")
    sr.add_argument("--till-stdout", action="store_true",
                    help="Skriv bara ut den nya token (för GitHub Actions) i stället för att spara i .env.")
    return p


COMMANDS = {
    "status": cmd_status,
    "wod-skapa": cmd_wod_create,
    "wod-publicera": cmd_wod_publish,
    "posta": cmd_post,
    "kommentarer": cmd_comments,
    "fornya-token": cmd_refresh,
}


def main(argv=None, ig=None, llm=None):
    args = build_parser().parse_args(argv)
    cfg = load_config()
    ig = ig or InstagramClient(cfg.ig_access_token, cfg.ig_user_id, cfg.api_version)
    ig.dry_run = args.torrkorning
    llm = llm or anthropic.Anthropic(api_key=cfg.anthropic_api_key)
    if args.torrkorning:
        print("[torrkörning] Inget publiceras på Instagram.")
    try:
        COMMANDS[args.kommando](cfg, ig, llm, args)
    except InstagramError as exc:
        sys.exit(f"Instagram-fel: {exc}")
    except anthropic.APIError as exc:
        sys.exit(f"Claude-fel: {exc}")
    except RuntimeError as exc:
        sys.exit(f"Fel: {exc}")
    except KeyboardInterrupt:
        sys.exit("\nAvbrutet.")


if __name__ == "__main__":
    main()
