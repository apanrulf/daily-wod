"""Liten klient för Instagram API med Instagram Login (graph.instagram.com).

Bara de anrop agenten behöver: kontoinfo, publiceringsgräns, inlägg,
kommentarer, svar, dölja kommentarer, publicera bild eller karusell och förnya token.
I torrkörningsläge (dry_run) görs alla läsningar på riktigt men inga
skrivningar skickas till Instagram.
"""
import time

import requests

BASE_URL = "https://graph.instagram.com"


class InstagramError(Exception):
    pass


class InstagramClient:
    def __init__(self, access_token, user_id=None, api_version="v25.0", dry_run=False, session=None):
        self.token = access_token
        self._user_id = user_id
        self._username = None
        self.version = api_version
        self.dry_run = dry_run
        self.session = session or requests.Session()
        self.poll_delay = 3  # sekunder mellan statuskontroller vid publicering

    # ---------- grund ----------

    def _request(self, method, path, params=None, body=None, versioned=True):
        url = f"{BASE_URL}/{self.version}/{path}" if versioned else f"{BASE_URL}/{path}"
        query = dict(params or {})
        query["access_token"] = self.token
        try:
            resp = self.session.request(method, url, params=query, json=body, timeout=30)
        except requests.RequestException as exc:
            raise InstagramError(f"Kunde inte nå Instagram: {exc}") from exc
        try:
            data = resp.json()
        except ValueError:
            data = {}
        if resp.status_code >= 400 or (isinstance(data, dict) and "error" in data):
            err = data.get("error", {}) if isinstance(data, dict) else {}
            msg = err.get("message") or resp.text[:300]
            raise InstagramError(f"Instagram svarade {resp.status_code}: {msg}")
        return data

    @staticmethod
    def _first(data):
        # Vissa endpoints svarar {"data": [{...}]} i stället för ett platt objekt.
        if isinstance(data, dict) and isinstance(data.get("data"), list):
            return data["data"][0] if data["data"] else {}
        return data

    # ---------- konto ----------

    def me(self):
        data = self._first(self._request(
            "GET", "me",
            {"fields": "user_id,username,account_type,followers_count,media_count"},
        ))
        if not data.get("user_id"):
            raise InstagramError("Fick inget konto-ID från Instagram. Kontrollera token.")
        self._user_id = self._user_id or str(data["user_id"])
        self._username = data.get("username")
        return data

    @property
    def user_id(self):
        if not self._user_id:
            self.me()
        return self._user_id

    @property
    def username(self):
        if self._username is None:
            self.me()
        return self._username

    def publishing_limit(self):
        """Returnerar (använda, max) publiceringar under rullande 24 timmar."""
        data = self._first(self._request(
            "GET", f"{self.user_id}/content_publishing_limit",
            {"fields": "quota_usage,config"},
        ))
        used = int(data.get("quota_usage") or 0)
        total = int((data.get("config") or {}).get("quota_total") or 100)
        return used, total

    # ---------- läsa ----------

    def recent_media(self, limit=10):
        data = self._request(
            "GET", f"{self.user_id}/media",
            {"fields": "id,caption,media_type,permalink,timestamp,comments_count", "limit": limit},
        )
        return data.get("data", [])

    def comments(self, media_id, limit=50):
        data = self._request(
            "GET", f"{media_id}/comments",
            {"fields": "id,text,username,timestamp,replies{username}", "limit": limit},
        )
        return data.get("data", [])

    # ---------- skriva ----------

    def reply_to_comment(self, comment_id, message):
        if self.dry_run:
            return {"id": "torrkorning"}
        return self._request("POST", f"{comment_id}/replies", body={"message": message})

    def hide_comment(self, comment_id):
        if self.dry_run:
            return {"success": True}
        return self._request("POST", str(comment_id), body={"hide": True})

    def publish_image(self, image_url, caption, alt_text=None):
        """Publicerar en bild i tre steg: skapa container, vänta tills den är klar, publicera."""
        if self.dry_run:
            return {"id": "torrkorning", "permalink": None}
        body = {"image_url": image_url, "caption": caption}
        if alt_text:
            body["alt_text"] = alt_text
        container_id = self._request("POST", f"{self.user_id}/media", body=body)["id"]
        self._wait_until_ready(container_id)
        media_id = self._request(
            "POST", f"{self.user_id}/media_publish", body={"creation_id": container_id}
        )["id"]
        try:
            permalink = self._request("GET", str(media_id), {"fields": "permalink"}).get("permalink")
        except InstagramError:
            permalink = None
        return {"id": media_id, "permalink": permalink}

    def publish_carousel(self, image_urls, caption, alt_texts=None):
        """Publicerar 2-10 bilder som en karusell: en container per bild, en karusellcontainer, publicera.

        Räknas som ett inlägg i dygnsgränsen.
        """
        if not 2 <= len(image_urls) <= 10:
            raise InstagramError(f"En karusell måste ha 2-10 bilder, inte {len(image_urls)}.")
        if self.dry_run:
            return {"id": "torrkorning", "permalink": None}
        alt_texts = list(alt_texts or [])
        children = []
        for i, url in enumerate(image_urls):
            body = {"image_url": url, "is_carousel_item": True}
            alt = alt_texts[i] if i < len(alt_texts) else None
            if alt:
                body["alt_text"] = alt
            try:
                container_id = self._request("POST", f"{self.user_id}/media", body=body)["id"]
            except InstagramError as exc:
                # alt_text är dokumenterat för bildcontainrar, men om Instagram skulle avvisa det
                # på karusellbilder vill vi hellre publicera utan alt-text än inte alls.
                if "alt_text" not in body or "alt_text" not in str(exc):
                    raise
                body.pop("alt_text")
                container_id = self._request("POST", f"{self.user_id}/media", body=body)["id"]
            self._wait_until_ready(container_id)
            children.append(str(container_id))

        carousel_id = self._request("POST", f"{self.user_id}/media", body={
            "media_type": "CAROUSEL", "children": ",".join(children), "caption": caption,
        })["id"]
        self._wait_until_ready(carousel_id)
        media_id = self._request(
            "POST", f"{self.user_id}/media_publish", body={"creation_id": carousel_id}
        )["id"]
        try:
            permalink = self._request("GET", str(media_id), {"fields": "permalink"}).get("permalink")
        except InstagramError:
            permalink = None
        return {"id": media_id, "permalink": permalink}

    def _wait_until_ready(self, container_id, attempts=20):
        for _ in range(attempts):
            status = self._request("GET", str(container_id), {"fields": "status_code"}).get("status_code")
            if status in ("FINISHED", None):
                return
            if status in ("ERROR", "EXPIRED"):
                raise InstagramError(
                    f"Instagram kunde inte bearbeta bilden (status {status}). "
                    "Kontrollera att det är en JPEG på en publik adress."
                )
            time.sleep(self.poll_delay)
        raise InstagramError("Bilden blev aldrig klar för publicering. Försök igen om en stund.")

    # ---------- token ----------

    def refresh_token(self):
        """Förnyar en långlivad token (måste vara minst 24 timmar gammal). Returnerar (token, sekunder)."""
        data = self._request(
            "GET", "refresh_access_token", {"grant_type": "ig_refresh_token"}, versioned=False
        )
        return data["access_token"], int(data.get("expires_in") or 0)
