"""Lectura de listados de Fotocasa (venta y alquiler), con peticiones pausadas y moderadas.

Fotocasa incrusta los resultados como JSON en <script id="__initial_props__">, con coordenadas y
marcas de ocupada / subasta / nuda propiedad / alquilada que se trasladan a la descripción para que
la valoración las detecte como riesgo o señal.
"""
import json
import logging
import random
import time

import requests
from bs4 import BeautifulSoup

from radar.sources.pisos_com import HEADERS

BASE = "https://www.fotocasa.es"
TYPES = {"Flat": "Piso", "Penthouse": "Ático", "Duplex": "Dúplex", "Studio": "Estudio", "Loft": "Loft",
         "Apartment": "Apartamento", "House": "Casa", "Chalet": "Chalet", "SemidetachedHouse": "Chalet pareado",
         "TerracedHouse": "Casa adosada", "DetachedHouse": "Chalet", "GroundFloor": "Bajo", "RusticHouse": "Casa rústica"}
FLAGS = {"isOccupied": "vivienda ocupada", "isAuctioned": "subasta", "isBareOwnership": "nuda propiedad",
         "isRentedWithTenants": "alquilado con inquilino"}
log = logging.getLogger(__name__)


def _location(a: dict, fallback: str | None) -> str | None:
    """Mismo formato que pisos.com: 'Numancia (Distrito Puente de Vallecas. Madrid)' o 'Centro (Getafe)'."""
    town, district, hood = a.get("municipality") or a.get("city"), a.get("district"), a.get("neighborhood")
    if not town:
        return fallback
    if hood and district and hood != district:
        return f"{hood} (Distrito {district}. {town})"
    return f"{hood or district} ({town})" if (hood or district) else town


def parse_listings(html: str) -> tuple[list[dict], int]:
    """Anuncios de una página de resultados y el total de resultados de la búsqueda."""
    el = BeautifulSoup(html, "html.parser").find(id="__initial_props__")
    if not el:
        return [], 0
    result = json.loads(el.get_text())["initialSearch"]["result"]
    out = []
    for x in result.get("realEstates") or []:
        if not x.get("rawPrice"):
            continue  # "A consultar"
        f = {q["key"]: q["value"] for q in x.get("features", [])}
        flags = [label for key, label in FLAGS.items() if x.get(key)]
        desc = (x.get("description") or "").strip()
        if flags:
            desc += f"\n\n[Datos del portal: {', '.join(flags)}]"
        c = x.get("coordinates") or {}
        images = [m["src"] for m in x.get("multimedia") or [] if m.get("type") == "image"]
        out.append({
            "code": str(x["id"]),
            "url": BASE + x["detail"]["es-ES"],
            "title": f"{TYPES.get(x.get('buildingSubtype'), 'Vivienda')} en {x.get('location') or ''}".strip(),
            "location": _location(x.get("address") or {}, x.get("location")),
            "price": x["rawPrice"],
            "m2": f.get("surface") or None,
            "rooms": f.get("rooms"),
            "baths": f.get("bathrooms") or None,
            "floor": None,  # Fotocasa da la planta codificada; no es fiable en el listado
            "description": desc or None,
            "photo": images[0] if images else None,
            # accuracy 1 = dirección exacta; 0 = el anunciante la oculta y el punto es aproximado
            "lat": c.get("latitude"), "lon": c.get("longitude"),
            "geo_prec": "calle" if c.get("accuracy") else "barrio",
        })
    return out, result.get("count") or 0


class Fotocasa:
    def __init__(self, pause: tuple[float, float] = (3.0, 6.0)):
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
            log.warning("Fotocasa error %s: %s", url, e)
            self.errors.append(f"Fotocasa {url}: {e}")
            return None

    def _crawl(self, path: str, query: str, pages: int) -> list[dict]:
        results, seen = [], set()
        for page in range(1, pages + 1):
            html = self._get(f"{BASE}{path}" + (f"/{page}" if page > 1 else "") + query)
            if not html:
                break
            try:
                items, total = parse_listings(html)
            except (ValueError, KeyError, TypeError) as e:
                self.errors.append(f"Fotocasa {path}: formato de página inesperado ({e})")
                break
            items = [i for i in items if i["code"] not in seen]
            if not items:
                break
            seen.update(i["code"] for i in items)
            results.extend(items)
            if len(seen) >= total:
                break
        return results

    def sales(self, slug: str, max_price: int, pages: int) -> list[dict]:
        # Orden por fecha de publicación para capturar las novedades del día
        return self._crawl(f"/es/comprar/viviendas/{slug}/todas-las-zonas/l",
                           f"?maxPrice={max_price}&sortType=publicationDate", pages)

    def rentals(self, slug: str, pages: int) -> list[dict]:
        if pages <= 0:
            return []
        return self._crawl(f"/es/alquiler/viviendas/{slug}/todas-las-zonas/l", "?sortType=publicationDate", pages)
