"""Coordenadas aproximadas de los anuncios para el mapa del panel (Nominatim / OpenStreetMap).

Fotocasa da coordenadas y se usan tal cual. pisos.com no las publica, así que se geocodifica la calle del título
("Piso en calle Toledo, 40") o, si no la hay, el barrio y el municipio de la ubicación. Cada consulta
se guarda en la tabla geocache (también las fallidas) y solo se hace una vez.
"""
import logging
import math
import re
import time

import requests

URL = "https://nominatim.openstreetmap.org/search"
# La política de uso de Nominatim exige un User-Agent que identifique la aplicación y máximo 1 petición/s
HEADERS = {"User-Agent": "RadarInmobiliario/1.0 (+https://github.com/cristobaljcl/Buscador-inmobiliario)"}
STREET = re.compile(r"\ben\s+((?:calle|c/|avenida|avda\.?|plaza|paseo|camino|ronda|carretera|travesía|glorieta|"
                    r"urbanización|rúa|rua|praza|rambla)\s+[^,]+)(?:,\s*(\d+))?", re.I)
PLACE = re.compile(r"\ben\s+(.+)$")  # "Piso en Fontiñas" → "Fontiñas"
log = logging.getLogger(__name__)


def _clean_city(city: str) -> str:
    city = re.sub(r"\s+capital$", "", city.strip(), flags=re.I)
    return "Alicante" if "Alacant" in city else city  # "Alicante - Alacant"


def parse_location(location: str | None) -> tuple[str | None, str | None]:
    """'Numancia (Distrito Puente de Vallecas. Madrid Capital)' → ('Numancia', 'Madrid'); 'Vigo' → (None, 'Vigo');
    'Noalla (Santo Estevo). Municipio de Sanxenxo' → ('Noalla', 'Sanxenxo')."""
    if not location:
        return None, None
    m = re.match(r"^(.*?)(?:\s*\(.*\))?\.\s*Municipio de\s+(.+)$", location)
    if m:
        return m.group(1).strip() or None, _clean_city(m.group(2))
    m = re.match(r"^(.*?)\s*\((.*)\)\s*$", location)
    if not m:
        return None, _clean_city(location)
    inner = m.group(2).split(".")[-1]
    return m.group(1).strip() or None, _clean_city(inner)


def _km(a: tuple[float, float], b: tuple[float, float]) -> float:
    dlat, dlon = math.radians(b[0] - a[0]), math.radians(b[1] - a[1])
    h = math.sin(dlat / 2) ** 2 + math.cos(math.radians(a[0])) * math.cos(math.radians(b[0])) * math.sin(dlon / 2) ** 2
    return 12742 * math.asin(math.sqrt(h))


class Geocoder:
    def __init__(self, db, max_requests: int):
        self.db = db
        self.budget = max_requests
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.errors: list[str] = []
        self._last = 0.0

    def _lookup(self, query: str) -> tuple[float, float] | None | bool:
        """Coordenadas de la consulta, None si no existe, False si no hay cupo para preguntarlo hoy."""
        cached = self.db.geocache_get(query)
        if cached is not None:
            return cached if cached[0] is not None else None
        if self.budget <= 0:
            return False
        self.budget -= 1
        time.sleep(max(0.0, 1.1 - (time.time() - self._last)))
        self._last = time.time()
        try:
            r = self.session.get(URL, params={"q": query, "format": "jsonv2", "limit": 1, "countrycodes": "es"},
                                 timeout=20)
            r.raise_for_status()
            res = r.json()
        except Exception as e:  # noqa: BLE001 - sin mapa no se cae el radar
            if not self.errors:
                self.errors.append(f"Geocodificación (mapa): {e}")
            return False
        point = (float(res[0]["lat"]), float(res[0]["lon"])) if res else None
        self.db.geocache_put(query, point)
        return point

    def locate(self, l: dict, zone: dict, precise: bool) -> tuple[float, float, str] | None:
        """[lat, lon, precisión] con precisión 'calle', 'barrio' o 'municipio'. precise=False no consulta calles nuevas."""
        if l.get("lat") is not None and l.get("lon") is not None:  # coordenadas del propio portal (Fotocasa)
            return l["lat"], l["lon"], l.get("geo_prec") or "barrio"
        barrio, city = parse_location(l.get("location"))
        if not city:
            return None
        prov = zone.get("provincia", "")
        center = tuple(map(float, zone["idealista_api"]["center"].split(","))) if zone.get("idealista_api") else None
        candidates = []
        title = l.get("title") or ""
        m = STREET.search(title)
        if m:
            street = f"{m.group(1).strip()} {m.group(2) or ''}".strip()
            candidates.append(("calle", f"{street}, {city}, {prov}, España", precise))
        places = []
        pm = PLACE.search(title)
        if not m and pm:
            places.append(pm.group(1).strip())
        if barrio:
            places += [barrio, barrio.split("-")[0].strip()]  # "Acequión-Los Naúfragos" → "Acequión"
        for place in dict.fromkeys(places):
            if place.lower() not in (city.lower(), f"{city} capital".lower()):
                candidates.append(("barrio", f"{place}, {city}, {prov}, España", True))
        candidates.append(("municipio", f"{city}, {prov}, España", True))
        for prec, query, may_ask in candidates:
            if not may_ask and self.db.geocache_get(query) is None:
                continue
            point = self._lookup(query)
            # Se descartan resultados absurdos (otra calle homónima a cientos de km)
            if point and (not center or _km(point, center) < 100):
                return point[0], point[1], prec
        return None


def geocode_listings(db, listings: list[dict], zones: dict, max_requests: int) -> list[str]:
    """Añade l['geo'] = [lat, lon, precisión]. Primero ubica todo a nivel barrio y luego afina por calle
    los mejor puntuados mientras quede cupo (los demás se afinan en días siguientes)."""
    geo = Geocoder(db, max_requests)
    for l in listings:
        l["geo"] = geo.locate(l, zones[l["zone"]], precise=False)
    for l in listings:
        if geo.budget <= 0:
            break
        if not l["geo"] or (l["geo"][2] != "calle" and STREET.search(l.get("title") or "")):
            better = geo.locate(l, zones[l["zone"]], precise=True)
            if better:
                l["geo"] = better
    db.conn.commit()
    located = sum(1 for l in listings if l["geo"])
    log.info("Mapa: %d/%d anuncios ubicados (%d consultas nuevas)", located, len(listings), max_requests - geo.budget)
    return geo.errors
