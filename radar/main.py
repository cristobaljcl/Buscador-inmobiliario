"""Orquestador diario del Radar Inmobiliario.

Uso:
    python -m radar.main                 # ejecución programada (solo actúa a partir de las 7:00 de Madrid, 1 vez/día)
    python -m radar.main --force         # ejecutar ya, ignorando hora y envío previo
    python -m radar.main --force --no-email   # sin enviar correo (guarda out/informe.html)
"""
import argparse
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from radar import ai, mailer, report, site
from radar.db import DB
from radar.sources.idealista_api import IdealistaAPI
from radar.sources.idealista_email import fetch_alerts
from radar.sources.pisos_com import PisosCom
from radar.valuation import PriceModel, valuate

ROOT = Path(__file__).resolve().parent.parent
TZ = ZoneInfo("Europe/Madrid")
log = logging.getLogger("radar")
PRIORITY = {"new": 2, "drop": 1, "seen": 0}


def gmail_credentials() -> tuple[str | None, str | None]:
    """Google muestra la contraseña de aplicación en bloques con espacios; se eliminan por si se pegó así."""
    user = (os.environ.get("GMAIL_USER") or "").strip() or None
    pw = (os.environ.get("GMAIL_APP_PASSWORD") or "").replace(" ", "").replace(" ", "").strip() or None
    return user, pw


def passes_filters(l: dict, z: dict) -> bool:
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

    def store(l: dict):
        st = db.upsert_listing(l, today)
        if PRIORITY[st] >= PRIORITY.get(statuses.get(l["id"], "seen"), 0):
            statuses[l["id"]] = st

    # ---- pisos.com ---------------------------------------------------------
    pc = cfg["pisos_com"]
    pisos = PisosCom(tuple(pc["pausa_segundos"]))
    for z in cfg["zonas"]:
        for slug in z["slugs"]:
            for r in pisos.rentals(slug, pc["paginas_alquiler"]):
                if r.get("m2") and r["price"] < 15000:
                    db.add_rent_sample({"id": f"pisos:{r['code']}", "zone": z["id"], "location": r["location"],
                                        "price": r["price"], "m2": r["m2"]}, today)
            sales = pisos.sales(slug, z["precio_max"], pc["paginas_venta"])
            log.info("pisos.com %s: %d anuncios de venta", slug, len(sales))
            for s in sales:
                if passes_filters(s, z):
                    store({**s, "id": f"pisos:{s['code']}", "source": "pisos.com", "zone": z["id"]})
    errors.extend(pisos.errors)

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


def dedupe(listings: list[dict]) -> list[dict]:
    """Agrupa el mismo inmueble publicado en varios portales (zona + precio + m² + habitaciones)."""
    groups: dict[tuple, dict] = {}
    for l in sorted(listings, key=lambda x: -len(x.get("description") or "")):
        k = (l["zone"], l["price"], l.get("m2"), l.get("rooms"))
        if not l.get("m2") or k not in groups:
            groups.setdefault(k if l.get("m2") else (l["id"],), {**l, "otros_portales": []})
        else:
            groups[k]["otros_portales"].append(l["url"])
    return list(groups.values())


def run(args):
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    now = datetime.now(TZ)
    today = now.date().isoformat()
    db = DB(ROOT / "data" / "radar.db")

    if not args.force and (now.hour < 7 or db.already_sent(today)):
        log.info("Fuera de horario o informe ya enviado hoy (%s %02d:%02d). Nada que hacer.", today, now.hour, now.minute)
        db.close()
        return

    first_run = db.is_first_run()
    errors: list[str] = []
    statuses = {} if args.sin_scraping else collect(cfg, db, today, errors)
    db.conn.commit()

    zones = {z["id"]: z for z in cfg["zonas"]}
    active = [l for l in db.active_listings(today) if l["zone"] in zones]

    # ---- valoración ----------------------------------------------------------
    rent_models, sale_models = {}, {}
    for zid in zones:
        rent_models[zid] = PriceModel(db.rent_samples(zid, today), lo=4, hi=35)  # >35 €/m²: temporada/lujo
        sale_models[zid] = PriceModel([l for l in active if l["zone"] == zid], lo=300, hi=15000)
    for l in active:
        z = zones[l["zone"]]
        l["zone_name"] = z["nombre"]
        l["valuation"] = valuate(l, z, cfg, rent_models[l["zone"]], sale_models[l["zone"]], db.price_history(l["id"]))
        l["_status"] = statuses.get(l["id"], "seen")
        db.set_valuation(l["id"], l["valuation"])

    listings = dedupe(active)
    by_score = sorted(listings, key=lambda l: -l["valuation"]["puntuacion"])
    fresh = by_score if first_run else [l for l in by_score if l["_status"] in ("new", "drop")]
    top = [l for l in fresh if l["valuation"]["puntuacion"] >= cfg["email"]["puntuacion_minima"]][:cfg["email"]["top_n"]]
    top_ids = {l["id"] for l in top}
    drops = [l for l in by_score if l["_status"] == "drop" and l["id"] not in top_ids and l["valuation"]["puntuacion"] >= 35]

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
        })

    # ---- panel web -------------------------------------------------------------
    site_pw = os.environ.get("SITE_PASSWORD")
    if site_pw:
        web_items = []
        for l in by_score[:cfg["web"]["max_anuncios"]]:
            web_items.append({k: l.get(k) for k in ("id", "source", "zone", "zone_name", "url", "title", "location",
                                                    "price", "m2", "rooms", "baths", "floor", "photo", "first_seen",
                                                    "last_seen", "otros_portales")}
                             | {"desc": (l.get("description") or "")[:1500], "v": l["valuation"],
                                "hist": db.price_history(l["id"]), "status": l["_status"]})
        meta = {"generado": now.strftime("%d/%m/%Y %H:%M"), "hoy": today, "zonas": zone_stats,
                "financiacion": cfg["financiacion"]}
        site.publish(ROOT, web_items, meta, site_pw)
    else:
        errors.append("SITE_PASSWORD no configurada: no se ha actualizado el panel web.")

    # ---- email -------------------------------------------------------------------
    subject, html = report.build_email(now.strftime("%d/%m"), top, drops, zone_stats, analyses, errors,
                                       cfg["web"]["url"], first_run)
    out = ROOT / "out"
    out.mkdir(exist_ok=True)
    (out / "informe.html").write_text(html, encoding="utf-8")
    gmail_user, gmail_pw = gmail_credentials()
    sent = False
    send_failed = False
    if not args.no_email and gmail_user and gmail_pw:
        try:
            mailer.send(gmail_user, gmail_pw, cfg["email"]["destinatario"], subject, html)
            sent = True
            log.info("Informe enviado a %s", cfg["email"]["destinatario"])
        except Exception:  # noqa: BLE001
            send_failed = True
            log.exception("No se pudo enviar el email")
    else:
        log.info("Email no enviado (sin credenciales o --no-email). Informe en out/informe.html")

    stats = {"activos": len(listings), "top": len(top), "bajadas": len(drops), "errores": errors}
    if sent:
        db.mark_sent(today, now.isoformat(), stats)
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
