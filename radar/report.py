"""Genera el email diario (HTML con estilos en línea, compatible con Gmail móvil)."""
from html import escape

GREEN, AMBER, RED, INK, MUTED, LINE = "#1a7f4b", "#b7791f", "#c53030", "#1f2933", "#616e7c", "#e4e7eb"


def eur(x) -> str:
    return f"{x:,.0f} €".replace(",", ".") if x is not None else "—"


def _score_color(s: int) -> str:
    return GREEN if s >= 70 else AMBER if s >= 50 else RED


def _card(l: dict, ai: dict | None) -> str:
    v = l["valuation"]
    tags = []
    if l.get("_status") == "new":
        tags.append("NUEVO")
    if v["bajadas_pct"]:
        tags.append(f"BAJADA −{v['bajadas_pct'][0]}%")
    if v["alquilado"]:
        tags.append("ALQUILADO")
    tag_html = " ".join(f'<span style="background:#eef2ff;color:#3949ab;font-size:11px;padding:2px 6px;'
                        f'border-radius:4px;margin-right:4px">{t}</span>' for t in tags)
    photo = (f'<img src="{escape(l["photo"])}" width="100%" style="display:block;border-radius:8px 8px 0 0;'
             f'max-height:220px;object-fit:cover">' if l.get("photo") else "")
    if v["estrategia"] == "vacacional":
        inc = v["ingresos"]
        income = f'ADR {eur(inc["adr"])} · ocup. {round(inc["ocupacion"] * 100)}% · {eur(inc["ingresos_brutos_anuales"])}/año'
    else:
        income = f'Alquiler est. {eur(v["ingresos"]["alquiler_mensual"])}/mes'
    signals = ", ".join(v["senales"][:4])
    risks = ", ".join(v["riesgos"])
    ai_html = ""
    if ai:
        qs = "".join(f"<li>{escape(q)}</li>" for q in ai.get("preguntas_vendedor", [])[:3])
        questions = f'<br><b>Preguntar:</b><ul style="margin:4px 0;padding-left:18px">{qs}</ul>' if qs else ""
        ai_html = (f'<div style="background:#f7f9fc;border-left:3px solid #3949ab;padding:8px 10px;margin-top:10px;'
                   f'font-size:13px"><b>IA · {escape(ai["veredicto"])}:</b> {escape(ai["comentario"])}{questions}</div>')
    return f"""
<div style="border:1px solid {LINE};border-radius:8px;margin:0 0 16px;overflow:hidden">
  {photo}
  <div style="padding:12px 14px">
    <div style="float:right;background:{_score_color(v['puntuacion'])};color:#fff;font-weight:bold;border-radius:20px;
                padding:4px 10px;font-size:15px">{v['puntuacion']}</div>
    <div style="font-size:12px;color:{MUTED};text-transform:uppercase">{escape(l['zone_name'])} · {v['estrategia']} · {escape(l['source'])}</div>
    <div style="font-size:17px;font-weight:bold;color:{INK};margin:2px 0">{eur(l['price'])}
      <span style="font-weight:normal;font-size:13px;color:{MUTED}">· {l.get('m2') or '?'} m² · {l.get('rooms') or '?'} hab · {escape(l.get('floor') or '')}</span></div>
    <div style="font-size:14px;color:{INK}">{escape(l.get('title') or '')}</div>
    <div style="font-size:13px;color:{MUTED};margin-bottom:6px">{escape(l.get('location') or '')}</div>
    {tag_html}
    <table style="width:100%;font-size:13px;margin-top:8px;border-collapse:collapse">
      <tr><td style="color:{MUTED}">Regla del 150</td><td style="text-align:right"><b>{v['multiplo']}</b> · {v['calidad']}</td></tr>
      <tr><td style="color:{MUTED}">Rentab. bruta / neta</td><td style="text-align:right"><b>{v['rent_bruta']}% / {v['rent_neta']}%</b></td></tr>
      <tr><td style="color:{MUTED}">Cash flow (hipoteca)</td><td style="text-align:right"><b>{eur(v['cashflow_mes'])}/mes</b> · ROCE {v['roce']}%</td></tr>
      <tr><td style="color:{MUTED}">Ingresos</td><td style="text-align:right">{income}</td></tr>
      <tr><td style="color:{MUTED}">Coste total</td><td style="text-align:right">{eur(v['coste_total'])} (aportas {eur(v['capital_aportado'])})</td></tr>
      <tr><td style="color:{MUTED}">Oferta para múltiplo 150</td><td style="text-align:right"><b>{eur(v['precio_objetivo_150'])}</b></td></tr>
    </table>
    <div style="font-size:12px;color:{MUTED};margin-top:6px">{escape(v['tipo_libro'])}</div>
    {f'<div style="font-size:12px;color:{GREEN}">✔ {escape(signals)}</div>' if signals else ''}
    {f'<div style="font-size:12px;color:{RED}">⚠ {escape(risks)}</div>' if risks else ''}
    {ai_html}
    <a href="{escape(l['url'])}" style="display:inline-block;margin-top:10px;background:{INK};color:#fff;
       text-decoration:none;padding:8px 14px;border-radius:6px;font-size:13px">Ver anuncio →</a>
  </div>
</div>"""


def build_email(day_label: str, top: list[dict], drops: list[dict], zone_stats: list[dict], ai: dict,
                errors: list[str], site_url: str, first_run: bool) -> tuple[str, str]:
    n_new = sum(z["nuevos"] for z in zone_stats)
    subject = f"🏠 Radar {day_label} — {len(top)} oportunidades TOP, {n_new} nuevas, {len(drops)} bajadas"
    intro = ("Primera ejecución: se ha creado la base de datos. El TOP incluye los mejores pisos actualmente "
             "publicados; a partir de mañana solo verás novedades y bajadas de precio.") if first_run else \
            "Novedades y bajadas de precio de las últimas 24 h, valoradas con la metodología <i>Tu Primer Piso</i>."
    cards = "".join(_card(l, ai.get(l["id"])) for l in top) or \
        f'<p style="color:{MUTED}">Hoy no hay novedades que superen el umbral de puntuación.</p>'
    drop_rows = "".join(
        f'<tr><td style="padding:4px 0"><a href="{escape(l["url"])}">{escape((l.get("title") or "")[:45])}</a>'
        f'<br><span style="color:{MUTED};font-size:12px">{escape(l["zone_name"])}</span></td>'
        f'<td style="text-align:right">{eur(l["price"])}<br><span style="color:{GREEN};font-size:12px">'
        f'−{l["valuation"]["bajadas_pct"][0]}% · punt. {l["valuation"]["puntuacion"]}</span></td></tr>'
        for l in drops[:10])
    zone_rows = "".join(
        f'<tr style="border-top:1px solid {LINE}"><td style="padding:4px 0">{escape(z["nombre"])}</td>'
        f'<td style="text-align:center">{z["nuevos"]}</td><td style="text-align:center">{z["activos"]}</td>'
        f'<td style="text-align:center">{z["mejor_multiplo"] or "—"}</td>'
        f'<td style="text-align:right">{z["alquiler_m2"] or "—"} / {z["venta_m2"] or "—"}</td></tr>'
        for z in zone_stats)
    err_html = ("<h3 style='color:%s'>Incidencias</h3><ul style='font-size:12px;color:%s'>%s</ul>"
                % (RED, MUTED, "".join(f"<li>{escape(e)}</li>" for e in errors[:15]))) if errors else ""
    panel = (f'<p style="text-align:center"><a href="{escape(site_url)}" style="background:#3949ab;color:#fff;'
             f'padding:10px 18px;border-radius:6px;text-decoration:none">Abrir panel completo</a></p>') if site_url else ""
    html = f"""<!doctype html><html><body style="margin:0;background:#f5f7fa">
<div style="max-width:620px;margin:0 auto;background:#fff;padding:18px;font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;color:{INK}">
  <h2 style="margin:0">Radar inmobiliario · {day_label}</h2>
  <p style="color:{MUTED};font-size:14px">{intro}</p>
  {panel}
  <h3>🏆 TOP del día</h3>
  {cards}
  {f'<h3>📉 Bajadas de precio</h3><table style="width:100%;font-size:13px">{drop_rows}</table>' if drop_rows else ''}
  <h3>📊 Resumen por zona</h3>
  <table style="width:100%;font-size:13px;border-collapse:collapse">
    <tr style="color:{MUTED}"><th align="left">Zona</th><th>Nuevos</th><th>Activos</th><th>Mejor múltiplo</th><th align="right">€/m² alquiler / venta</th></tr>
    {zone_rows}
  </table>
  {err_html}
  <p style="font-size:11px;color:{MUTED};margin-top:24px">Estimaciones automáticas orientativas, no asesoramiento financiero.
  Verifica alquiler, estado, cargas (nota simple), normativa VUT y gastos reales antes de ofertar.</p>
</div></body></html>"""
    return subject, html
