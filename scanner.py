#!/usr/bin/env python3
"""Huurscanner: scant huursites en stuurt nieuwe woningen naar Telegram."""
from __future__ import annotations

import argparse
import html as htmllib
import json
import os
import random
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin, quote

import yaml
from bs4 import BeautifulSoup

try:
    from curl_cffi import requests as http  # doet zich voor als echte Chrome (minder geblokkeerd)
    IMPERSONATE = {"impersonate": "chrome"}
except ImportError:  # pragma: no cover
    import requests as http
    IMPERSONATE = {}

ROOT = Path(__file__).resolve().parent
STATE_FILE = ROOT / "state" / "seen.json"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "nl-NL,nl;q=0.9,en;q=0.8"}
SKIP_STATUS = re.compile(r"verhuurd|onder optie|gereserveerd|onder voorbehoud|in optie", re.I)


# ───────────────────────── model ─────────────────────────
@dataclass
class Listing:
    source: str
    id: str
    url: str
    title: str
    price: int | None = None
    m2: int | None = None
    bedrooms: int | None = None
    rooms: int | None = None
    location: str = ""
    image: str | None = None
    status: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.source}:{self.id}"

    @property
    def address_key(self) -> str | None:
        t = re.sub(r"^(appartement|huis|studio|kamer|woning|penthouse|maisonnette|benedenwoning|bovenwoning)\s+",
                   "", self.title.lower().strip())
        t = re.sub(r"[^a-z0-9]", "", t)
        return t if re.search(r"\d", t) else None


# ───────────────────────── helpers ─────────────────────────
def get(url: str, **kw):
    kw.setdefault("headers", HEADERS)
    kw.setdefault("timeout", 30)
    r = http.get(url, **IMPERSONATE, **kw)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code} voor {url}")
    if "Just a moment" in r.text[:3000] or "cf-challenge" in r.text[:5000]:
        raise RuntimeError(f"Bot-check (Cloudflare) op {url}")
    return r


def post_json(url: str, body: dict):
    r = http.post(url, json=body, headers={**HEADERS, "Accept": "application/json"},
                  timeout=30, **IMPERSONATE)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code} voor {url}")
    return r.json()


def to_int(s) -> int | None:
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return int(s)
    m = re.search(r"\d[\d.]*", str(s))
    return int(m.group(0).replace(".", "")) if m else None


def parse_price(text: str) -> int | None:
    for m in re.finditer(r"€\s*([\d.]+)", text):
        v = int(m.group(1).replace(".", ""))
        if v >= 300:
            return v
    return None


def parse_m2(text: str) -> int | None:
    m = re.search(r"woonoppervlakte[^\d]{0,40}(\d{2,4})", text, re.I) or \
        re.search(r"(\d{2,4})\s*(?:m²|m2)\b", text, re.I)
    return int(m.group(1)) if m else None


def parse_bedrooms(text: str) -> int | None:
    m = re.search(r"(\d+)\s*slaapkamers?", text, re.I) or \
        re.search(r"slaapkamers?[^\d]{0,25}(\d+)", text, re.I)
    return int(m.group(1)) if m else None


def parse_rooms(text: str) -> int | None:
    m = re.search(r"(\d+)[\s-]*kamers?\b", text, re.I) or \
        re.search(r"aantal kamers[^\d]{0,25}(\d+)", text, re.I)
    return int(m.group(1)) if m else None


def soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def cards_by_link(html: str, base: str, pattern: str):
    """Generieke kaart-detectie: zoek links naar woningen en loop omhoog tot het
    blok dat precies één woning bevat. Werkt op de meeste makelaarssites."""
    s = soup(html)
    rx = re.compile(pattern)
    seen = {}
    for a in s.find_all("a", href=True):
        href = urljoin(base, a["href"].split("#")[0])
        if not rx.search(href) or href in seen:
            continue
        el = a
        while el.parent is not None and el.parent.name not in ("body", "html", "[document]"):
            links = {urljoin(base, x["href"].split("#")[0]) for x in el.parent.find_all("a", href=True)
                     if rx.search(urljoin(base, x["href"]))}
            if len(links) > 1:
                break
            el = el.parent
        img = el.find("img")
        img_url = None
        if img:
            img_url = img.get("src") or img.get("data-src")
            if img_url and img_url.startswith("data:"):
                img_url = None
            if img_url:
                img_url = urljoin(base, img_url)
        seen[href] = (el.get_text(" | ", strip=True), img_url)
    return seen


# ───────────────────────── bronnen ─────────────────────────
def scrape_pararius_like(base: str, path: str, source: str) -> list[Listing]:
    url = base + path
    s = soup(get(url).text)
    out = []
    for sec in s.select("section.listing-search-item"):
        a = sec.select_one("a.listing-search-item__link--title")
        if not a:
            continue
        lid = sec.get("data-listing-search-item-id") or a["href"]
        feats = {li.get("class", [""])[-1]: li.get_text(" ", strip=True)
                 for li in sec.select("li.illustrated-features__item")}
        m2 = rooms = None
        for k, v in feats.items():
            if "surface-area" in k:
                m2 = to_int(v)
            if "number-of-rooms" in k:
                rooms = to_int(v)
        img = sec.select_one("img")
        src = sec.select_one("source")
        image = (img.get("src") if img else None) or \
                (src.get("srcset", "").split(" ")[0] if src else None)
        label = sec.select_one(".listing-search-item__label")
        if image:
            image = image.replace("&auto=webp", "")
        out.append(Listing(
            source=source, id=lid, url=urljoin(base, a["href"]),
            title=a.get_text(" ", strip=True),
            price=to_int(sec.select_one(".listing-search-item__price").get_text(" ", strip=True))
            if sec.select_one(".listing-search-item__price") else None,
            m2=m2, rooms=rooms,
            location=(sec.select_one(".listing-search-item__sub-title") or soup("")).get_text(" ", strip=True),
            image=image or None,
            status=label.get_text(" ", strip=True) if label else "",
        ))
    return out


def scrape_pararius(c) -> list[Listing]:
    p = f"/huurwoningen/{c['plaats']}/0-{c['max_huur']}/{c['min_slaapkamers']}-slaapkamers/{c['min_m2']}m2"
    items = scrape_pararius_like("https://www.pararius.nl", p, "Pararius")
    for it in items:  # Pararius filtert slaapkamers al aan serverkant
        it.bedrooms = it.bedrooms or c["min_slaapkamers"]
    return items


def scrape_huurwoningen(c) -> list[Listing]:
    q = (f"/in/{c['plaats']}/?filters%5Bprice%5D%5Bmax%5D={c['max_huur']}"
         f"&filters%5Bbedrooms%5D%5Bmin%5D={c['min_slaapkamers']}"
         f"&filters%5Bliving_size%5D%5Bmin%5D={c['min_m2']}")
    return scrape_pararius_like("https://www.huurwoningen.nl", q, "Huurwoningen.nl")


def scrape_funda(c) -> list[Listing]:
    url = ("https://www.funda.nl/zoeken/huur?selected_area=" + quote(f'["{c["plaats"]}"]') +
           "&price=" + quote(f'"0-{c["max_huur"]}"') +
           "&floor_area=" + quote(f'"{c["min_m2"]}-"') +
           "&bedrooms=" + quote(f'"{c["min_slaapkamers"]}-"') +
           "&sort=" + quote('"date_down"'))
    html = get(url).text
    out = []
    for href, (text, img) in cards_by_link(html, "https://www.funda.nl", r"/detail/huur/[^/]+/[^/]+/\d+").items():
        m = re.search(r"/detail/huur/[^/]+/([^/]+)/(\d+)", href)
        slug, fid = m.group(1), m.group(2)
        parts = [p.strip() for p in text.split("|")]
        li = next((i for i, p in enumerate(parts) if re.match(r"^\d{4}\s?[A-Z]{2}\b", p)), None)
        loc = parts[li] if li is not None else ""
        title = parts[li - 1] if li else re.sub(r"^(appartement|huis)-", "", slug).replace("-", " ")
        status = " ".join(p for p in parts if SKIP_STATUS.search(p))
        bed = None
        # Funda toont na m² het aantal slaapkamers als los getal
        for i, p in enumerate(parts):
            if "m²" in p and i + 1 < len(parts) and re.fullmatch(r"\d{1,2}", parts[i + 1]):
                bed = int(parts[i + 1])
                break
        out.append(Listing(source="Funda", id=fid, url=href.split("?")[0], title=title,
                           price=parse_price(text), m2=parse_m2(text), bedrooms=bed,
                           location=loc, image=img, status=status))
    return out


def scrape_kamernet(c) -> list[Listing]:
    url = f"https://kamernet.nl/huren/huurwoningen-{c['plaats']}"
    html = get(url).text
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        raise RuntimeError("Kamernet: geen data gevonden in pagina")
    data = json.loads(m.group(1))
    lst = data["props"]["pageProps"]["targetPageProps"]["findListingsResponse"]["listings"]
    slugs = {1: "kamer", 2: "appartement", 4: "studio", 8: "anti-kraak"}
    out = []
    for l in lst:
        t = l.get("listingType")
        if t in (1, 4):  # kamers en studio's overslaan
            continue
        slug = slugs.get(t, "appartement")
        out.append(Listing(
            source="Kamernet", id=str(l["listingId"]),
            url=f"https://kamernet.nl/huren/{slug}-{l['citySlug']}/{l['streetSlug']}/{slug}-{l['listingId']}",
            title=f"{slug.capitalize()} {l.get('street', '')}",
            price=l.get("totalRentalPrice"), m2=l.get("surfaceArea"),
            location=l.get("city", ""), image=l.get("resizedFullPreviewImageUrl") or l.get("thumbnailUrl"),
            warnings=[] if l.get("utilitiesIncluded") else ["prijs mogelijk excl. G/W/E"],
        ))
    return out


VESTEDA_COORDS = {"utrecht": (52.090736, 5.12142)}


def scrape_vesteda(c) -> list[Listing]:
    lat, lng = VESTEDA_COORDS.get(c["plaats"], VESTEDA_COORDS["utrecht"])
    body = {"filters": [0], "language": "nl", "latitude": lat, "longitude": lng,
            "place": c["plaats"].capitalize(), "placeType": 1, "priceFrom": 500,
            "priceTo": c["max_huur"], "radius": 5, "sorting": 0}
    res = post_json("https://www.vesteda.com/api/units/search/facet", body)["results"]
    objs = res.get("objects") or []
    if isinstance(objs, dict):
        objs = [o for v in objs.values() for o in v]
    out = []
    for o in objs:
        status = "" if o.get("status") == 1 else "niet beschikbaar"
        nr = f"{o.get('houseNumber') or ''}{(' ' + o['houseNumberAddition']) if o.get('houseNumberAddition') else ''}"
        out.append(Listing(
            source="Vesteda", id=str(o["id"]), url=urljoin("https://www.vesteda.com", o["url"]),
            title=f"{o.get('entitysubtypelabel') or 'Woning'} {o.get('street', '')} {nr}".strip(),
            price=to_int(o.get("priceUnformatted")), m2=o.get("size"),
            bedrooms=o.get("numberOfBedRooms") or None,
            location=f"{o.get('postalCode') or ''} {o.get('city', '')}".strip(),
            image=o.get("imageBig"), status=status,
            warnings=["prijs is kale huur, servicekosten komen erbij"],
        ))
    return out


def scrape_bouwinvest(c) -> list[Listing]:
    out = []
    for page in (1, 2, 3):  # standaard gesorteerd op nieuwste eerst
        url = (f"https://www.wonenbijbouwinvest.nl/api/search?query={c['plaats'].capitalize()}"
               f"&range=5&page={page}")
        j = get(url, headers={**HEADERS, "Accept": "application/json"}).json()
        for d in j.get("data", []):
            if d.get("class") != "ProjectProperty":
                continue
            pr = d.get("price") or {}
            price = (to_int(pr.get("price")) or 0) + (to_int(pr.get("service_cost")) or 0) or None
            props = d.get("properties") or {}
            imgs = d.get("images") or d.get("media") or []
            image = None
            if isinstance(imgs, list) and imgs:
                f = imgs[0]
                image = f if isinstance(f, str) else (f.get("url") or f.get("src"))
            out.append(Listing(
                source="Wonen bij Bouwinvest", id=str(d["id"]), url=d["url"], title=d.get("name", ""),
                price=price, m2=(d.get("sizes") or {}).get("surface"),
                bedrooms=props.get("total_sleepingrooms") or None,
                rooms=props.get("total_rooms") or None,
                location=f"{(d.get('address') or {}).get('zipcode', '')} {(d.get('address') or {}).get('city', '')}",
                image=image, status=(d.get("labels") or {}).get("stickerbar", "") or "",
            ))
    return out


def enrich_from_detail(l: Listing):
    try:
        sp = soup(get(l.url).text)
    except Exception:
        return
    if not l.image:
        og = sp.find("meta", property="og:image")
        l.image = og.get("content") if og else None
    h1 = sp.find("h1")
    if h1 and (len(l.title) < 8 or "," in l.title or len(l.title) > 60):
        l.title = h1.get_text(" ", strip=True)[:80] or l.title
    t = sp.get_text(" ", strip=True)
    l.m2 = l.m2 or parse_m2(t)
    l.bedrooms = l.bedrooms or parse_bedrooms(t)
    l.rooms = l.rooms or parse_rooms(t)
    l.price = l.price or parse_price(t)


def scrape_generic(cfg: dict) -> list[Listing]:
    out = {}
    for url in cfg["urls"]:
        html = get(url).text
        for href, (text, img) in cards_by_link(html, url, cfg["link_patroon"]).items():
            if href in out:
                continue
            parts = [p.strip() for p in text.split("|") if p.strip()]
            cands = [p for p in parts
                     if re.search(r"[A-Za-z]{3}", p) and "€" not in p and not SKIP_STATUS.search(p)
                     and not re.search(r"p/?mnd|per maand|^(te huur|nieuw|details|bekijk|favoriet|direct)", p, re.I)]
            addr = [p for p in cands if re.search(r"[A-Za-z]{3,}.*\s\d+\s?[A-Za-z]?$", p) or "," in p]
            title = (addr or cands or [href.rstrip("/").split("/")[-1]])[0]
            out[href] = Listing(
                source=cfg["naam"], id=href, url=href, title=title.strip(),
                price=parse_price(text), m2=parse_m2(text), bedrooms=parse_bedrooms(text),
                rooms=parse_rooms(text), image=img,
                status=" ".join(p for p in parts if SKIP_STATUS.search(p)),
            )
        time.sleep(1)
    return list(out.values())


# ───────────────────────── filter ─────────────────────────
def matches(l: Listing, c: dict) -> bool:
    if l.status and SKIP_STATUS.search(l.status) or l.status == "niet beschikbaar":
        return False
    strict = c.get("strikt", False)
    if l.price is None:
        if strict:
            return False
        l.warnings.append("prijs onbekend")
    elif l.price > c["max_huur"]:
        return False
    if l.m2 is None:
        if strict:
            return False
        l.warnings.append("m² onbekend")
    elif l.m2 < c["min_m2"]:
        return False
    if l.bedrooms is not None:
        if l.bedrooms < c["min_slaapkamers"]:
            return False
    elif l.rooms is not None:
        if l.rooms < c["min_slaapkamers"] + 1:
            return False
        l.warnings.append(f"{l.rooms} kamers, aantal slaapkamers niet vermeld")
    else:
        if strict:
            return False
        l.warnings.append("aantal slaapkamers onbekend")
    return True


# ───────────────────────── telegram ─────────────────────────
class Telegram:
    def __init__(self, token: str | None, chat_id: str | None, state: dict, dry: bool):
        self.token, self.dry, self.state = token, dry, state
        self.chat_id = chat_id or state.get("telegram_chat_id")
        if not self.dry and self.token and not self.chat_id:
            self.chat_id = self._discover_chat()

    def _api(self, method: str, **data):
        r = http.post(f"https://api.telegram.org/bot{self.token}/{method}", data=data, timeout=30)
        j = r.json()
        if not j.get("ok"):
            raise RuntimeError(f"Telegram {method}: {j}")
        return j["result"]

    def _discover_chat(self):
        try:
            for u in reversed(self._api("getUpdates")):
                chat = (u.get("message") or u.get("my_chat_member") or {}).get("chat")
                if chat:
                    self.state["telegram_chat_id"] = str(chat["id"])
                    print(f"Telegram chat gevonden: {chat['id']}")
                    return str(chat["id"])
        except Exception as e:
            print(f"Kon Telegram-chat niet vinden: {e}")
        print("⚠️  Stuur eerst een bericht (bijv. /start) naar je bot in Telegram.")
        return None

    def send(self, text: str, image: str | None = None):
        if self.dry or not self.token or not self.chat_id:
            print("\n--- melding ---\n" + re.sub(r"<[^>]+>", "", text) + (f"\n[foto] {image}" if image else ""))
            return
        if image:
            try:
                self._api("sendPhoto", chat_id=self.chat_id, photo=image, caption=text, parse_mode="HTML")
                return
            except Exception:
                pass
        self._api("sendMessage", chat_id=self.chat_id, text=text, parse_mode="HTML",
                  disable_web_page_preview="false")
        time.sleep(0.5)


def fmt(l: Listing) -> str:
    e = htmllib.escape
    bits = []
    if l.price:
        bits.append(f"💶 € {l.price:,}".replace(",", ".") + " p/m")
    if l.m2:
        bits.append(f"📐 {l.m2} m²")
    if l.bedrooms:
        bits.append(f"🛏 {l.bedrooms} slaapk.")
    elif l.rooms:
        bits.append(f"🚪 {l.rooms} kamers")
    msg = f"🏠 <b>Nieuw op {e(l.source)}</b>\n<b>{e(l.title)}</b>\n"
    if l.location:
        msg += f"{e(l.location)}\n"
    msg += " · ".join(bits) + "\n"
    if l.warnings:
        msg += "⚠️ " + e(", ".join(dict.fromkeys(l.warnings))) + "\n"
    msg += f'\n👉 <a href="{e(l.url)}">Bekijk en reageer direct</a>'
    return msg


# ───────────────────────── main ─────────────────────────
BUILTIN = {
    "pararius": scrape_pararius,
    "funda": scrape_funda,
    "huurwoningen": scrape_huurwoningen,
    "kamernet": scrape_kamernet,
    "vesteda": scrape_vesteda,
    "bouwinvest": scrape_bouwinvest,
}
FAIL_ALERT_AFTER = 6


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"seen": {}, "addresses": {}, "fails": {}}


def save_state(st: dict):
    now = time.time()
    keep = 90 * 86400
    for src in st["seen"]:
        st["seen"][src] = {k: v for k, v in st["seen"][src].items() if now - v < keep}
    st["addresses"] = {k: v for k, v in st["addresses"].items() if now - v < 21 * 86400}
    STATE_FILE.parent.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(st, indent=1, sort_keys=True))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="niets versturen, alleen tonen")
    ap.add_argument("--test-telegram", action="store_true", help="stuur een testbericht")
    ap.add_argument("--only", help="alleen deze bron (sleutel uit config)")
    ap.add_argument("--show-all", action="store_true", help="toon alle gevonden woningen (debug)")
    args = ap.parse_args()

    cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
    c = cfg["criteria"]
    st = load_state()
    st.setdefault("fails", {})
    tg = Telegram(os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID"), st, args.dry_run)

    if args.test_telegram:
        tg.send("✅ <b>Huurscanner is gekoppeld.</b>\nJe krijgt hier een melding zodra er een woning "
                f"in {c['plaats'].capitalize()} online komt die past: max € {c['max_huur']}, "
                f"min {c['min_m2']} m², min {c['min_slaapkamers']} slaapkamers.")
        save_state(st)
        return

    jobs = []
    for key, fn in BUILTIN.items():
        if cfg["bronnen"].get(key, False):
            jobs.append((key, lambda fn=fn: fn(c)))
    for g in cfg.get("extra_makelaars") or []:
        key = g.get("sleutel") or g["naam"]
        if cfg["bronnen"].get(key, True):
            jobs.append((key, lambda g=g: scrape_generic(g)))
    if args.only:
        jobs = [j for j in jobs if j[0] == args.only]

    random.shuffle(jobs)
    now = time.time()
    first_run_summary = []
    total_new = 0
    for key, job in jobs:
        try:
            items = job()
        except Exception as e:
            n = st["fails"].get(key, 0) + 1
            st["fails"][key] = n
            print(f"[{key}] FOUT ({n}x): {e}")
            if n == FAIL_ALERT_AFTER or (n > FAIL_ALERT_AFTER and n % 288 == 0):
                tg.send(f"⚠️ Bron <b>{htmllib.escape(key)}</b> faalt al {n} scans op rij "
                        f"({htmllib.escape(str(e)[:150])}). De andere bronnen draaien gewoon door.")
            continue
        if st["fails"].get(key, 0) >= FAIL_ALERT_AFTER:
            tg.send(f"✅ Bron <b>{htmllib.escape(key)}</b> werkt weer.")
        st["fails"][key] = 0

        if args.show_all:
            for it in items:
                print(f"  {it.source} | {it.title} | €{it.price} | {it.m2}m² | slk {it.bedrooms} | k {it.rooms} | {it.status} | {it.url}")

        first = key not in st["seen"]
        seen = st["seen"].setdefault(key, {})
        new = [it for it in items if it.key not in seen]
        for it in new:
            seen[it.key] = now
        hits = []
        for it in new:
            if key not in BUILTIN:  # makelaarssites: details + foto van de woningpagina halen
                enrich_from_detail(it)
                time.sleep(0.5)
            if matches(it, c):
                hits.append(it)
        print(f"[{key}] {len(items)} gevonden, {len(new)} nieuw, {len(hits)} passend{' (eerste run)' if first else ''}")

        if first:
            first_run_summary += hits
            continue
        for it in hits:
            ak = it.address_key
            if ak and ak in st["addresses"]:
                print(f"   (dubbel, al gemeld via andere site) {it.title}")
                continue
            if ak:
                st["addresses"][ak] = now
            tg.send(fmt(it), it.image)
            total_new += 1
        time.sleep(random.uniform(1, 3))

    if first_run_summary:
        lines, done = [], set()
        for i in first_run_summary:
            ak = i.address_key
            if ak in done:
                continue
            if ak:
                done.add(ak)
                st["addresses"][ak] = now
            lines.append(f"• <a href=\"{htmllib.escape(i.url)}\">{htmllib.escape(i.title)}</a> "
                         f"({htmllib.escape(i.source)}{', € ' + str(i.price) if i.price else ''})")
        chunks, cur = [], "🔎 <b>Huurscanner gestart.</b> Dit staat nu al online en past bij je criteria:\n\n"
        for ln in lines:
            if len(cur) + len(ln) > 3800:
                chunks.append(cur)
                cur = ""
            cur += ln + "\n"
        chunks.append(cur + "\nVanaf nu krijg je alleen nog nieuwe woningen, meteen als ze online komen.")
        for ch in chunks:
            tg.send(ch)

    save_state(st)
    print(f"Klaar. {total_new} nieuwe meldingen verstuurd.")


if __name__ == "__main__":
    sys.exit(main())
