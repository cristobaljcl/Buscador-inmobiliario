"""Lee de Gmail (IMAP) los correos de alertas de Idealista y extrae los anuncios que contienen.

Es la vía legal y estable para Idealista: tú creas búsquedas guardadas con aviso por email
en idealista.com y este módulo procesa esos correos cada mañana.
"""
import email
import imaplib
import logging
import re
from datetime import date, timedelta
from email.header import decode_header, make_header
from urllib.parse import unquote

from bs4 import BeautifulSoup

log = logging.getLogger(__name__)
INMUEBLE_RE = re.compile(r"idealista\.com/(?:[a-z]{2}/)?inmueble/(\d+)")
PRICE_RE = re.compile(r"(\d{1,3}(?:\.\d{3})+|\d{4,7})\s*€")
M2_RE = re.compile(r"(\d{2,4})\s*m²")
ROOMS_RE = re.compile(r"(\d+)\s*hab")


def _html_of(msg) -> str | None:
    for part in msg.walk():
        if part.get_content_type() == "text/html":
            payload = part.get_payload(decode=True)
            return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
    return None


def parse_alert_html(html: str) -> list[dict]:
    """Extrae anuncios de un correo de alerta. Formato heurístico: busca enlaces a /inmueble/<id>/
    y lee precio, m² y habitaciones del bloque que los rodea."""
    soup = BeautifulSoup(html, "html.parser")
    found: dict[str, dict] = {}
    for a in soup.find_all("a", href=True):
        m = INMUEBLE_RE.search(unquote(a["href"]))
        if not m:
            continue
        code = m.group(1)
        # Subir por el árbol hasta un bloque que contenga el precio del anuncio
        block = a
        for _ in range(8):
            if block.parent is None:
                break
            block = block.parent
            text = block.get_text(" ", strip=True)
            if PRICE_RE.search(text) and len(text) < 1200:
                break
        text = block.get_text(" ", strip=True)
        price = PRICE_RE.search(text)
        if not price:
            continue
        item = found.setdefault(code, {
            "code": code,
            "url": f"https://www.idealista.com/inmueble/{code}/",
            "price": int(price.group(1).replace(".", "")),
            "title": None, "m2": None, "rooms": None, "photo": None, "description": None,
        })
        title = a.get_text(" ", strip=True)
        if title and len(title) > 10 and not PRICE_RE.search(title):
            item["title"] = title
        if (m2 := M2_RE.search(text)) and not item["m2"]:
            item["m2"] = int(m2.group(1))
        if (rooms := ROOMS_RE.search(text)) and not item["rooms"]:
            item["rooms"] = int(rooms.group(1))
        img = block.find("img", src=True)
        if img and not item["photo"] and "logo" not in img["src"]:
            item["photo"] = img["src"]
        item["description"] = text[:600]
    return list(found.values())


def fetch_alerts(user: str, password: str, days_back: int, already_processed) -> list[tuple[str, str, list[dict]]]:
    """Devuelve [(message_id, asunto, anuncios)] de los correos de Idealista no procesados."""
    out = []
    since = (date.today() - timedelta(days=days_back)).strftime("%d-%b-%Y")
    with imaplib.IMAP4_SSL("imap.gmail.com") as imap:
        imap.login(user, password)
        imap.select("INBOX", readonly=True)
        status, data = imap.search(None, f'(FROM "idealista" SINCE {since})')
        if status != "OK":
            return out
        for num in data[0].split():
            _, msg_data = imap.fetch(num, "(RFC822)")
            msg = email.message_from_bytes(msg_data[0][1])
            mid = msg.get("Message-ID", f"num-{num.decode()}")
            if already_processed(mid):
                continue
            subject = str(make_header(decode_header(msg.get("Subject", ""))))
            html = _html_of(msg)
            listings = parse_alert_html(html) if html else []
            log.info("Alerta Idealista '%s': %d anuncios", subject, len(listings))
            out.append((mid, subject, listings))
    return out
