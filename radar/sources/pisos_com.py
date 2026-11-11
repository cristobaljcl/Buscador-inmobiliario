"""Lectura de listados de pisos.com (venta y alquiler), con peticiones pausadas y moderadas."""
import logging
import random
import re
import time

import requests
from bs4 import BeautifulSoup

BASE = "https://www.pisos.com"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/130.0 Safari/537.36",
    "Accept-Language": "es-ES,es;q=0.9",
}
log = logging.getLogger(__name__)


def _int(text: str | None) -> int | None:
    if not text:
        return None
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else None


def parse_cards(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for card in soup.select("div.ad-preview[data-lnk-href]"):
        price_el = card.select_one(".ad-preview__price")
        price = _int(price_el.get_text()) if price_el else None
        if not price:
            continue  # "Consultar precio"
        href = card["data-lnk-href"]
        title_el = card.select_one(".ad-preview__title")
        loc_el = card.select_one(".ad-preview__subtitle")
        desc_el = card.select_one(".ad-preview__description")
        img = card.select_one(".carousel__main-photo--as-img img, .carousel img")
        m2 = rooms = baths = None
        floor = None
        for ch in card.select(".ad-preview__char"):
            t = ch.get_text(strip=True).lower()
            if "m²" in t or "m2" in t:
                m2 = _int(t)
            elif "hab" in t:
                rooms = _int(t)
            elif "baño" in t:
                baths = _int(t)
            elif "planta" in t or t in ("bajo", "entreplanta", "sótano", "semisótano", "ático"):
                floor = ch.get_text(strip=True)
        out.append({
            "code": card.get("id") or href,
            "url": BASE + href if href.startswith("/") else href,
            "title": title_el.get_text(strip=True) if title_el else None,
            "location": loc_el.get_text(strip=True) if loc_el else None,
            "price": price,
            "m2": m2,
            "rooms": rooms,
            "baths": baths,
            "floor": floor,
            "description": desc_el.get_text(" ", strip=True) if desc_el else None,
            "photo": img.get("src") or img.get("data-src") if img else None,
        })
    return out


class PisosCom:
    def __init__(self, pause: tuple[float, float] = (2.5, 5.0)):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.pause = pause
        self.errors: list[str] = []

    def _get(self, url: str) -> str | None:
        time.sleep(random.uniform(*self.pause))
        try:
            r = self.session.get(url, timeout=30)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.text
        except requests.RequestException as e:
            log.warning("pisos.com error %s: %s", url, e)
            self.errors.append(f"pisos.com {url}: {e}")
            return None

    def _crawl(self, path: str, pages: int) -> list[dict]:
        results, seen = [], set()
        for page in range(1, pages + 1):
            url = f"{BASE}{path}" + (f"{page}/" if page > 1 else "")
            html = self._get(url)
            if not html:
                break
            cards = [c for c in parse_cards(html) if c["code"] not in seen]
            if not cards:
                break
            seen.update(c["code"] for c in cards)
            results.extend(cards)
        return results

    def sales(self, slug: str, max_price: int, pages: int, min_rooms: int = 0) -> list[dict]:
        # Orden "más recientes" para capturar las novedades del día; "con-N-habitaciones" = N o más
        rooms = f"con-{min_rooms}-habitaciones/" if min_rooms >= 2 else ""
        return self._crawl(f"/venta/{slug}/hasta-{max_price}/{rooms}fecharecientedesde-desc/", pages)

    def rentals(self, slug: str, pages: int) -> list[dict]:
        return self._crawl(f"/alquiler/{slug}/fecharecientedesde-desc/", pages)
