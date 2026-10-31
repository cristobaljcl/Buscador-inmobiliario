"""Cliente de la API oficial de Idealista (https://developers.idealista.com).

Requiere solicitar credenciales (IDEALISTA_API_KEY / IDEALISTA_API_SECRET). La cuota gratuita
es pequeña, así que el orquestador limita las peticiones por ejecución y rota las zonas.
"""
import base64
import logging

import requests

log = logging.getLogger(__name__)
TOKEN_URL = "https://api.idealista.com/oauth/token"
SEARCH_URL = "https://api.idealista.com/3.5/es/search"


class IdealistaAPI:
    def __init__(self, key: str, secret: str):
        creds = base64.b64encode(f"{key}:{secret}".encode()).decode()
        r = requests.post(TOKEN_URL, headers={"Authorization": f"Basic {creds}"},
                          data={"grant_type": "client_credentials", "scope": "read"}, timeout=30)
        r.raise_for_status()
        self.token = r.json()["access_token"]

    def search(self, center: str, distance: int, max_price: int, min_size: int | None) -> list[dict]:
        params = {
            "operation": "sale", "propertyType": "homes", "center": center, "distance": distance,
            "maxPrice": max_price, "order": "publicationDate", "sort": "desc", "maxItems": 50,
            "numPage": 1, "language": "es",
        }
        if min_size:
            params["minSize"] = min_size
        r = requests.post(SEARCH_URL, headers={"Authorization": f"Bearer {self.token}"}, data=params, timeout=30)
        r.raise_for_status()
        out = []
        for e in r.json().get("elementList", []):
            out.append({
                "code": str(e["propertyCode"]),
                "url": e.get("url") or f"https://www.idealista.com/inmueble/{e['propertyCode']}/",
                "title": e.get("suggestedTexts", {}).get("title") or e.get("address"),
                "location": ", ".join(x for x in (e.get("neighborhood"), e.get("district"), e.get("municipality")) if x),
                "price": int(e["price"]),
                "m2": int(e["size"]) if e.get("size") else None,
                "rooms": e.get("rooms"),
                "baths": e.get("bathrooms"),
                "floor": e.get("floor"),
                "description": e.get("description"),
                "photo": e.get("thumbnail"),
            })
        return out
