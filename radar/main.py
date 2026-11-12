"""Orquestador diario del Radar Inmobiliario.

Uso:
    python -m radar.main                 # ejecución programada: busca desde la 1:45 de Madrid y envía a las 7:00, 1 vez/día
    python -m radar.main --force         # ejecutar ya, ignorando hora y envío previo
    python -m radar.main --force --no-email   # sin enviar correo (guarda out/informe.html)
"""
import argparse
import logging
import os
import re
import sys
import time
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from radar import ai, mailer, report, site
from radar.db import DB
from radar.geocode import geocode_listings
from radar.sources.fotocasa import Fotocasa
from radar.sources.idealista_api import IdealistaAPI
from radar.sources.idealista_email import fetch_alerts
from radar.sources.pisos_com import PisosCom
from radar.valuation import PriceModel, valuate

ROOT = Path(__file__).resolve().parent.parent
TZ = ZoneInfo("Europe/Madrid")
log = logging.getLogger("radar")
PRIORITY = {"new": 2, "drop": 1, "seen": 0}
START = (1, 45)    # hora de Madrid a partir de la cual actúa la ejecución programada
SEND_AT = (7, 0)   # hora de Madrid a la que salen los emails


def gmail_credentials() -> tuple[str | None, str | None]:
    """Google muestra la contraseña de aplicación en bloques con espacios; se eliminan por si se pegó así."""
    user = (os.environ.get("GMAIL_USER") or "").strip() or None
    pw = (os.environ.get("GMAIL_APP_PASSWORD") or "").replace(" ", "").replace(" ", "").strip() or None
    return user, pw


def normalize(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn")


def excluded_area(l: dict, z: dict) -> bool:
    """Fuera de la zona: barrio en la lista negra, o no menciona ningún término de 'exigir_ubicacion'."""
    text = normalize(f"{l.get('location') or ''} {l.get('title') or ''}")
    if any(normalize(b) in text for b in z.get("excluir_barrios", [])):
        return True
    required = z.get("exigir_ubicacion")
    return bool(required) and not any(normalize(w) in text for w in required)


def zone_center(z: dict) -> list[float] | None:
    """[lat, lon] de la zona: 'centro' o, si no hay, el centro de búsqueda de la API de Idealista."""
    center = z.get("centro") or (z.get("idealista_api") or {}).get("center")
    return [float(c) for c in center.split(",")] if center else None


def excluded_text(l: dict, z: dict) -> bool:
    """El anuncio no es una vivienda entera (participación, nuda propiedad, crédito…): expresiones regulares
    de 'excluir_texto' buscadas en título y descripción, sin tildes ni mayúsculas."""
    patterns = z.get("excluir_texto")
    if not patterns:
        return False
    text = normalize(f"{l.get('title') or ''} {l.get('description') or ''}")
    return any(re.search(p, text) for p in patterns)


def excluded(l: dict, z: dict) -> bool:
    return excluded_area(l, z) or excluded_text(l, z)


def passes_filters(l: dict, z: dict) -> bool:
    if excluded(l, z):
        return False
    if l["price"] > z["precio_max"]:
        return False
    if l.get("m2") and l["m2"] < z["m2_min"]:
        return False
    if l.get("rooms") is not None and l["rooms"] < z["habitaciones_min"]:
        return False
    return True


def match_zone(text: str, zones: list[dict]) -> dict | None:
    text = text.lower()
    for z in zones:
        if any(w.lower() in text for w in z.get("idealista_match", [])):
            return z
    return None


def collect(cfg: dict, db: DB, today: str, errors: list[str]) -> dict[str, str]:
    """Recoge anuncios de todas las fuentes. Devuelve {listing_id: 'new'|'drop'|'seen'}."""
    statuses: dict[str, str] = {}
    own_ids = {z["id"] for z in cfg["zonas"] if z.get("ids_propios")}

    def store(l: dict):
        # Zonas que comparten anuncios con otra (p. ej. Ofertas Madrid y Madrid) los guardan con su propio id
        if l["zone"] in own_ids:
            l = {**l, "id": f"{l['zone']}/{l['id']}"}
        st = db.upsert_listing(l, today)
        if PRIORITY[st] >= PRIORITY.get(statuses.get(l["id"], "seen"), 0):
            statuses[l["id"]] = st

    # ---- pisos.com ---------------------------------------------------------
    pc = cfg["pisos_com"]
    pisos = PisosCom(tuple(pc["pausa_segundos"]))
    for z in cfg["zonas"]:
        for slug in z["slugs"]:
            for r in pisos.rentals(slug, z.get("paginas_alquiler", pc["paginas_alquiler"])):
                if r.get("m2") and r["price"] < 15000:
                    db.add_rent_sample({"id": f"pisos:{r['code']}", "zone": z["id"], "location": r["location"],
                                        "price": r["price"], "m2": r["m2"]}, today)
            sales = pisos.sales(slug, z["precio_max"], z.get("paginas_venta", pc["paginas_venta"]),
                                z["habitaciones_min"])
            log.info("pisos.com %s: %d anuncios de venta", slug, len(sales))
            for s in sales:
                if passes_filters(s, z):
                    store({**s, "id": f"pisos:{s['code']}", "source": "pisos.com", "zone": z["id"]})
    errors.extend(pisos.errors)

    # ---- Fotocasa ----------------------------------------------------------
    fc = cfg.get("fotocasa", {})
    if fc.get("activada", True):
        foto = Fotocasa(tuple(fc.get("pausa_segundos", (3, 6))))
        for z in cfg["zonas"]:
            for slug in z.get("fotocasa", []):
                for r in foto.rentals(slug, z.get("paginas_alquiler", fc.get("paginas_alquiler", 1))):
                    if r.get("m2") and r["price"] < 15000:
                        db.add_rent_sample({"id": f"fotocasa:{r['code']}", "zone": z["id"], "location": r["location"],
                                            "price": r["price"], "m2": r["m2"]}, today)
                sales = foto.sales(slug, z["precio_max"], z.get("paginas_venta", fc.get("paginas_venta", 2)),
                                   z["habitaciones_min"])
                log.info("Fotocasa %s: %d anuncios de venta", slug, len(sales))
                for s in sales:
                    if passes_filters(s, z):
                        store({**s, "id": f"fotocasa:{s['code']}", "source": "fotocasa", "zone": z["id"]})
        errors.extend(foto.errors)

    # ---- Idealista: alertas por email ---------------------------------------
    ide = cfg["idealista"]
    gmail_user, gmail_pw = gmail_credentials()
    if ide["alertas_email"] and gmail_user and gmail_pw:
        try:
            unmatched = 0
            for mid, subject, items in fetch_alerts(gmail_user, gmail_pw, ide["dias_atras"], db.email_processed):
                for it in items:
                    z = match_zone(f"{subject} {it.get('title') or ''} {it.get('description') or ''}", cfg["zonas"])
                    if not z:
                        unmatched += 1
                        continue
                    if passes_filters(it, z):
                        store({**it, "id": f"idealista:{it['code']}", "source": "idealista", "zone": z["id"],
                               "location": it.get("location") or subject})
                db.mark_email_processed(mid)
            if unmatched:
                errors.append(f"Idealista email: {unmatched} anuncios sin zona asignada (revisa 'idealista_match').")
        except Exception as e:  # noqa: BLE001 - una fuente caída no debe tumbar el radar
            errors.append(f"Idealista email (IMAP): {e}")

    # ---- Idealista: API oficial (cuota limitada, zonas en rotación) --------------
    key, secret = os.environ.get("IDEALISTA_API_KEY"), os.environ.get("IDEALISTA_API_SECRET")
    if ide["api"]["activada"] and key and secret:
        month = today[:7]
        try:
            api = IdealistaAPI(key, secret)
            zones = [z for z in cfg["zonas"] if z.get("idealista_api")]
            start = datetime.fromisoformat(today).toordinal()
            for i in range(min(ide["api"]["peticiones_por_ejecucion"], len(zones))):
                if db.api_requests(month) >= 95:
                    errors.append("Idealista API: cuota mensual agotada.")
                    break
                z = zones[(start * ide["api"]["peticiones_por_ejecucion"] + i) % len(zones)]
                items = api.search(z["idealista_api"]["center"], z["idealista_api"]["distance"],
                                   z["precio_max"], z["m2_min"])
                db.add_api_request(month)
                for it in items:
                    if passes_filters(it, z):
                        store({**it, "id": f"idealista:{it['code']}", "source": "idealista", "zone": z["id"]})
        except Exception as e:  # noqa: BLE001
            errors.append(f"Idealista API: {e}")

    return statuses


def area_key(location: str | None) -> str:
    """Distrito (grandes ciudades) o municipio de la ubicación, normalizado: 'Zofío (Distrito Usera. Madrid
    Capital)' y 'Zofio (Distrito Usera. Madrid)' dan 'usera'."""
    loc = location or ""
    m = re.search(r"Distrito ([^.)]+)", loc)
    if m:
        return normalize(m.group(1).strip())
    m = re.search(r"\(([^()]*)\)\s*$", loc)
    return normalize(re.sub(r"\s+capital$", "", (m.group(1) if m else loc).strip(), flags=re.I))


def dedupe(listings: list[dict]) -> list[dict]:
    """Agrupa el mismo inmueble publicado en varios portales: misma zona, precio, m², habitaciones y
    distrito/municipio, y como mucho un anuncio por portal (dos del mismo portal son pisos distintos)."""
    groups: dict[tuple, list[dict]] = {}
    out = []
    for l in sorted(listings, key=lambda x: -len(x.get("description") or "")):
        if not l.get("m2"):
            out.append({**l, "otros_portales": []})
            continue
        k = (l["zone"], l["price"], l["m2"], l.get("rooms"), area_key(l.get("location")))
        group = next((g for g in groups.get(k, []) if l["source"] not in g["_sources"]), None)
        if group:
            group["otros_portales"].append(l["url"])
            group["_sources"].add(l["source"])
        else:
            group = {**l, "otros_portales": [], "_sources": {l["source"]}}
            groups.setdefault(k, []).append(group)
            out.append(group)
    for g in out:
        g.pop("_sources", None)
    return out


def run(args):
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    now = datetime.now(TZ)
    today = now.date().isoformat()
    db = DB(ROOT / "data" / "radar.db")

    # Se arranca a las 2:07 para tener la búsqueda hecha y el email se retiene hasta la hora de envío
    if not args.force and ((now.hour, now.minute) < START or db.already_sent(today)):
        log.info("Fuera de horario o informe ya enviado hoy (%s %02d:%02d). Nada que hacer.", today, now.hour, now.minute)
        db.close()
        return

    first_run = db.is_first_run()
    errors: list[str] = []
    statuses = {} if args.sin_scraping else collect(cfg, db, today, errors)
    db.conn.commit()

    zones = {z["id"]: z for z in cfg["zonas"]}
    # Los filtros de barrios y de texto también se aplican a lo ya guardado, por si las listas cambian
    active = [l for l in db.active_listings(today) if l["zone"] in zones and not excluded(l, zones[l["zone"]])]

    # ---- valoración ----------------------------------------------------------
    rent_models, sale_models = {}, {}
    for zid, z in zones.items():
        # 'alquiler_de': la zona usa los alquileres de otra (Ofertas Madrid no lee alquileres propios)
        rent_models[zid] = PriceModel(db.rent_samples(z.get("alquiler_de", zid), today), lo=4, hi=35)  # >35 €/m²: temporada/lujo
        sale_models[zid] = PriceModel([l for l in active if l["zone"] == zid], lo=300, hi=15000)
    for l in active:
        z = zones[l["zone"]]
        l["zone_name"] = z["nombre"]
        l["valuation"] = valuate(l, z, cfg, rent_models[l["zone"]], sale_models[l["zone"]], db.price_history(l["id"]))
        l["_status"] = statuses.get(l["id"], "seen")
        db.set_valuation(l["id"], l["valuation"])

    listings = dedupe(active)
    by_score = sorted(listings, key=lambda l: -l["valuation"]["puntuacion"])
    # Las zonas con informe propio (p. ej. Jubilación gallinos) no entran en el informe de inversión
    own_report = {zid for zid, z in zones.items() if z.get("informe")}
    investing = [l for l in by_score if l["zone"] not in own_report]
    fresh = investing if first_run else [l for l in investing if l["_status"] in ("new", "drop")]
    top = [l for l in fresh if l["valuation"]["puntuacion"] >= cfg["email"]["puntuacion_minima"]][:cfg["email"]["top_n"]]
    top_ids = {l["id"] for l in top}
    drops = [l for l in investing if l["_status"] == "drop" and l["valuation"]["bajadas_pct"] and l["id"] not in top_ids and l["valuation"]["puntuacion"] >= 35]
    province_tops: dict[str, list[dict]] = {}
    for zid, z in zones.items():
        if zid not in own_report:
            province_tops.setdefault(z.get("provincia") or z["nombre"], [])
    for l in investing:
        province = zones[l["zone"]].get("provincia") or zones[l["zone"]]["nombre"]
        province_tops[province].append(l)
    province_tops = {province: listings[:10] for province, listings in province_tops.items()}

    # ---- análisis IA -----------------------------------------------------------
    analyses = {}
    if cfg["ia"]["activada"]:
        analyses = ai.analyze(top[:cfg["ia"]["analizar_top"]], cfg["ia"]["modelo"])
        for l in top:
            if l["id"] in analyses:
                l["valuation"]["ia"] = analyses[l["id"]]
                db.set_valuation(l["id"], l["valuation"])

    # ---- estadísticas por zona -----------------------------------------------
    zone_stats = []
    for zid, z in zones.items():
        zl = [l for l in listings if l["zone"] == zid]
        rent_m2, _, _ = rent_models[zid].estimate(None)
        sale_m2, _, _ = sale_models[zid].estimate(None)
        zone_stats.append({
            "id": zid, "nombre": z["nombre"], "estrategia": z["estrategia"],
            "nuevos": sum(1 for l in zl if l["_status"] == "new"), "activos": len(zl),
            "mejor_multiplo": min((l["valuation"]["multiplo"] for l in zl), default=None),
            "alquiler_m2": round(rent_m2, 1) if rent_m2 else None, "venta_m2": round(sale_m2) if sale_m2 else None,
            "centro": zone_center(z),
        })

    # ---- panel web -------------------------------------------------------------
    site_pw = os.environ.get("SITE_PASSWORD")
    if site_pw:
        # Las zonas con informe propio se publican enteras; las de inversión, solo las mejor puntuadas
        published = ([l for l in by_score if l["zone"] in own_report]
                     + investing[:cfg["web"]["max_anuncios"]])
        errors.extend(geocode_listings(db, published, zones, cfg["web"].get("mapa_consultas_max", 400)))
        web_items = []
        for l in published:
            # Descripción más corta en zonas grandes para que el panel no pese demasiado en el móvil
            desc_len = zones[l["zone"]].get("informe", {}).get("descripcion_panel", 1500)
            web_items.append({k: l.get(k) for k in ("id", "source", "zone", "zone_name", "url", "title", "location",
                                                    "price", "m2", "rooms", "baths", "floor", "photo", "first_seen",
                                                    "last_seen", "otros_portales", "geo")}
                             | {"desc": (l.get("description") or "")[:desc_len], "v": l["valuation"],
                                "hist": db.price_history(l["id"]), "status": l["_status"]})
        meta = {"generado": now.strftime("%d/%m/%Y %H:%M"), "hoy": today, "zonas": zone_stats,
                "financiacion": cfg["financiacion"], "estrategias": cfg.get("estrategias", {})}
        site.publish(ROOT, web_items, meta, site_pw)
    else:
        errors.append("SITE_PASSWORD no configurada: no se ha actualizado el panel web.")

    # ---- email -------------------------------------------------------------------
    send_at = now.replace(hour=SEND_AT[0], minute=SEND_AT[1], second=0, microsecond=0)
    if not args.force and not args.no_email and datetime.now(TZ) < send_at:
        wait = (send_at - datetime.now(TZ)).total_seconds()
        log.info("Búsqueda terminada; se espera %d min para enviar a las %02d:%02d", wait // 60, *SEND_AT)
        time.sleep(wait)
        now = datetime.now(TZ)
    emails = [("informe.html", cfg["email"]["destinatario"], cfg["email"].get("copia", []),
               *report.build_email(now.strftime("%d/%m"), top, drops,
                                   [s for s in zone_stats if s["id"] not in own_report], analyses, errors,
                                   province_tops, cfg["web"]["url"], first_run))]
    recent = (now.date() - timedelta(days=3)).isoformat()
    for zid in own_report:
        z = zones[zid]
        # Acumulado: lo que sigue publicado. pisos.com y Fotocasa se leen enteros a diario, así que lo que lleva
        # 3 días sin aparecer se da por vendido; las alertas de Idealista no se repiten y se mantienen 21 días.
        current = sorted((l for l in listings if l["zone"] == zid
                          and (l["source"] == "idealista" or l["last_seen"] >= recent)), key=lambda l: l["price"])
        emails.append((f"informe_{zid}.html", z["informe"]["destinatario"], z["informe"].get("copia", []),
                       *report.build_family_email(now.strftime("%d/%m"), z["informe"]["titulo"], z["nombre"],
                                                  z["precio_max"], current, today,
                                                  subtitle=z["informe"].get("descripcion"),
                                                  list_all=z["informe"].get("listar_todo", True),
                                                  icon=z["informe"].get("icono", "🐔"))))
    out = ROOT / "out"
    out.mkdir(exist_ok=True)
    gmail_user, gmail_pw = gmail_credentials()
    sent = False
    send_failed = False
    for filename, to, cc, subject, html in emails:
        (out / filename).write_text(html, encoding="utf-8")
        if args.no_email or not (gmail_user and gmail_pw):
            log.info("Email no enviado (sin credenciales o --no-email). Informe en out/%s", filename)
            continue
        try:
            mailer.send(gmail_user, gmail_pw, to, subject, html, cc)
            sent = sent or filename == "informe.html"  # el informe principal marca el día como enviado
            log.info("Informe enviado a %s%s", to, f" (copia: {', '.join(cc)})" if cc else "")
        except Exception:  # noqa: BLE001
            send_failed = True
            log.exception("No se pudo enviar el email a %s", to)

    stats = {"activos": len(listings), "top": len(top), "bajadas": len(drops), "errores": errors}
    if sent:
        db.mark_sent(today, now.isoformat(), stats)
    db.conn.commit()
    pruned = db.prune(today)
    if pruned:
        log.info("Base de datos: %d anuncios retirados hace más de 60 días eliminados", pruned)
    db.close()
    log.info("Hecho: %s", stats)
    if send_failed:
        sys.exit(1)  # GitHub te avisará por email del fallo; la BD se guarda igualmente


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    p = argparse.ArgumentParser()
    p.add_argument("--force", action="store_true", help="ignorar la hora y el envío previo")
    p.add_argument("--no-email", action="store_true", help="no enviar el correo")
    p.add_argument("--sin-scraping", action="store_true", help="recalcular con los datos ya guardados")
    run(p.parse_args())


if __name__ == "__main__":
    main()
