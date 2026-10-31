"""Motor de valoración basado en la metodología de "(Invierte en) Tu Primer Piso" (Inversor Directivo).

Métricas clave:
- Regla del 150: múltiplo = coste total de compra / alquiler mensual.
  <150 extraordinaria · 150-180 buena · 180-210 correcta · 210-240 regular · >240 mala.
- Rentabilidad bruta y neta sobre el coste total (precio + impuestos + gastos + reforma).
- Cash flow mensual y rentabilidad sobre el capital aportado (ROCE) con hipoteca a tipo fijo.
- Clasificación del libro: Tipo 1 "piso como el mío" (3-4% bruto), Tipo 2 "piso con algún pero"
  (5-6%) y Tipo 3 "piso de menos de 80K" (7-10%).
"""
import re
from statistics import median

MIN_SAMPLES = 5


def location_keys(location: str | None) -> list[str]:
    """De 'Acacias (Distrito Arganzuela. Madrid Capital)' → ['acacias (distrito ...)', 'acacias', 'distrito arganzuela. madrid capital']."""
    if not location:
        return []
    loc = location.lower().strip()
    keys = [loc]
    m = re.match(r"^(.*?)\s*\((.*)\)\s*$", loc)
    if m:
        keys += [m.group(1).strip(), m.group(2).strip()]
    return keys


class PriceModel:
    """Mediana de €/m² por barrio/distrito con alternativa a nivel de zona."""

    def __init__(self, samples: list[dict], lo: float, hi: float, min_m2: int = 30):
        self.by_key: dict[str, list[float]] = {}
        self.all: list[float] = []
        seen = set()
        for s in samples:
            if not s.get("m2") or s["m2"] < min_m2:  # descarta estudios/habitaciones con €/m² inflado
                continue
            # Una promoción con muchas unidades idénticas no debe dominar la mediana del barrio
            sig = (s["price"], s["m2"], s.get("location"))
            if sig in seen:
                continue
            seen.add(sig)
            v = s["price"] / s["m2"]
            if not lo <= v <= hi:
                continue
            self.all.append(v)
            for k in location_keys(s.get("location")):
                self.by_key.setdefault(k, []).append(v)

    def estimate(self, location: str | None) -> tuple[float | None, str, int]:
        """Devuelve (€/m², nivel usado, nº de muestras)."""
        zone_med = median(self.all) if len(self.all) >= MIN_SAMPLES else None
        for k in location_keys(location):
            vals = self.by_key.get(k, [])
            if len(vals) >= MIN_SAMPLES:
                m = median(vals)
                # Un barrio muy alejado de la mediana de la zona suele ser ruido (pocas muestras atípicas)
                if zone_med is None or 0.6 * zone_med <= m <= 1.6 * zone_med:
                    return m, k, len(vals)
        if zone_med is not None:
            return zone_med, "zona", len(self.all)
        return None, "sin datos", 0


def annuity(principal: float, rate: float, years: int) -> float:
    r, n = rate / 12, years * 12
    return principal * r / (1 - (1 + r) ** -n) if r else principal / n


def _contains_any(text: str, words: list[str]) -> list[str]:
    return [w for w in words if w in text]


def multiplier_label(mult: float) -> str:
    if mult < 150:
        return "Extraordinaria"
    if mult < 180:
        return "Buena"
    if mult < 210:
        return "Correcta"
    if mult < 240:
        return "Regular"
    return "Mala"


def book_type(gross: float, price: int) -> str:
    if gross >= 0.07 and price < 90000:
        return "Tipo 3 · Piso <80K (alta rentabilidad, más gestión)"
    if gross >= 0.05:
        return "Tipo 2 · Piso con algún pero (equilibrio preferido)"
    return "Tipo 1 · Piso como el mío (baja rentabilidad)"


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def valuate(l: dict, zone: dict, cfg: dict, rent_model: PriceModel, sale_model: PriceModel,
            history: list[tuple[str, int]]) -> dict:
    price = l["price"]
    m2 = l.get("m2")
    rooms = l.get("rooms") or 0
    text = f"{l.get('title') or ''} {l.get('description') or ''}".lower()
    fin = cfg["financiacion"]
    vacational = zone["estrategia"] == "vacacional"
    notes: list[str] = []

    # ---- coste total de adquisición --------------------------------------
    taxes = price * (zone["itp"] + fin["gastos_compra_pct"])
    reform = 0
    reform_kind = None
    for kind in ("integral", "parcial"):
        rk = cfg["reforma"][kind]
        if m2 and _contains_any(text, rk["palabras"]):
            reform, reform_kind = rk["eur_m2"] * m2, kind
            break
    furniture = cfg["gastos_vacacional"]["mobiliario"] if vacational else 0
    total_cost = price + taxes + reform + furniture

    # ---- alquiler de larga duración estimado ------------------------------
    rent_m2, rent_level, rent_n = rent_model.estimate(l.get("location"))
    if rent_m2 is None:
        rent_m2, rent_level = zone["alquiler_m2_defecto"], "valor por defecto (config)"
        notes.append("Alquiler estimado con valor por defecto: faltan comparables en la zona.")
    if m2:
        eff_m2 = m2 if m2 <= 90 else 90 + 0.6 * (m2 - 90)  # el €/m² baja en pisos grandes
        long_rent = rent_m2 * eff_m2
    else:
        long_rent = rent_m2 * (45 + 20 * max(rooms, 1))
        notes.append("Sin m² en el anuncio: superficie estimada por nº de habitaciones.")

    # ---- ingresos y gastos según estrategia -------------------------------
    if vacational:
        gv = cfg["gastos_vacacional"]
        mult_hab = gv["multiplicador_hab"].get(min(rooms, 5), 1.0)
        adr = zone["adr_base"] * mult_hab
        gross_income = adr * 365 * zone["ocupacion"]
        expenses = (gross_income * (gv["gestion_pct"] + gv["plataforma_pct"] + gv["mantenimiento_pct"])
                    + gv["suministros_mes"] * 12 + gv["comunidad_mes"] * 12
                    + price * gv["ibi_pct_precio"] + gv["seguro_anual"])
        # Para la regla del 150 en vacacional usamos el ingreso tras comisiones y suministros
        monthly_for_rule = (gross_income * (1 - gv["gestion_pct"] - gv["plataforma_pct"]) - gv["suministros_mes"] * 12) / 12
        # El ADR es único por zona: en ubicaciones poco turísticas sobreestima. Se limita frente al alquiler largo.
        cap = long_rent * gv["tope_vs_larga"]
        if monthly_for_rule > cap:
            scale = cap / monthly_for_rule
            notes.append(f"Ingreso vacacional limitado a {gv['tope_vs_larga']}× el alquiler de larga duración "
                         f"(ADR de zona probablemente alto para esta ubicación).")
            gross_income *= scale
            adr *= scale
            expenses = (gross_income * (gv["gestion_pct"] + gv["plataforma_pct"] + gv["mantenimiento_pct"])
                        + gv["suministros_mes"] * 12 + gv["comunidad_mes"] * 12
                        + price * gv["ibi_pct_precio"] + gv["seguro_anual"])
            monthly_for_rule = (gross_income * (1 - gv["gestion_pct"] - gv["plataforma_pct"]) - gv["suministros_mes"] * 12) / 12
        # Gastos no descontados ya en monthly_for_rule (para la calculadora del panel)
        other_expenses = expenses - gross_income * (gv["gestion_pct"] + gv["plataforma_pct"]) - gv["suministros_mes"] * 12
        income_detail = {"adr": round(adr), "ocupacion": zone["ocupacion"], "ingresos_brutos_anuales": round(gross_income),
                         "alquiler_larga_alternativo": round(long_rent)}
    else:
        g = cfg["gastos_larga"]
        gross_income = long_rent * 12
        expenses = (gross_income * (g["seguro_impago_pct"] + g["vacio_pct"] + g["mantenimiento_pct"])
                    + g["comunidad_mes"] * 12 + price * g["ibi_pct_precio"] + g["seguro_hogar_anual"])
        monthly_for_rule = long_rent
        other_expenses = expenses
        income_detail = {"alquiler_mensual": round(long_rent)}

    net_income = gross_income - expenses
    gross_yield = gross_income / total_cost
    net_yield = net_income / total_cost
    mult = total_cost / monthly_for_rule if monthly_for_rule > 0 else 999

    # ---- financiación -----------------------------------------------------
    loan = price * fin["ltv"]
    equity = total_cost - loan
    payment = annuity(loan, fin["interes"], fin["anios"])
    cashflow = net_income / 12 - payment
    roce = cashflow * 12 / equity if equity > 0 else 0

    # ---- precio objetivo de negociación (múltiplo 150) --------------------
    target_price = (150 * monthly_for_rule - reform - furniture) / (1 + zone["itp"] + fin["gastos_compra_pct"])
    target_price = max(0, round(target_price / 1000) * 1000)
    discount_needed = 1 - target_price / price if price else 0

    # ---- señales ----------------------------------------------------------
    s = cfg["senales"]
    opp = _contains_any(text, s["oportunidad"])
    rented = _contains_any(text, s["alquilado"])
    risks = _contains_any(text, s["riesgo"])
    floor = (l.get("floor") or "").lower()
    if zone.get("excluir_bajos") and floor.startswith("bajo"):
        risks.append("bajo (excluido en config)")

    drops = []
    if len(history) >= 2:
        first_price = history[0][1]
        if price < first_price:
            drops.append(round((first_price - price) / first_price * 100, 1))
    days_listed = len({d for d, _ in history})

    sale_m2, _, _ = sale_model.estimate(l.get("location"))
    below_market = None
    if sale_m2 and m2:
        below_market = 1 - (price / m2) / sale_m2

    # ---- puntuación 0-100 -------------------------------------------------
    # El vacacional exige más gestión y tiene más riesgo regulatorio: se le exige un múltiplo un 25% mejor
    mult_eff = mult * (cfg["gastos_vacacional"]["exigencia_multiplo"] if vacational else 1)
    score = 50 * _clamp((240 - mult_eff) / 110)          # múltiplo 130 → 50 pts, 240 → 0
    score += 15 * _clamp(roce / 0.15)                    # ROCE 15% → 15 pts
    score += 10 * _clamp(cashflow / 300)                 # cash flow 300 €/mes → 10 pts
    score += min(10, 4 * len(opp))
    if below_market is not None and below_market > 0:
        score += min(10, below_market * 50)              # 20% bajo mercado → 10 pts
    if drops:
        score += 5
    if rented and not vacational:
        score += 3
    if vacational and any(w in zone.get("aviso_normativo", "").lower() for w in ("moratoria", "restringido")):
        score -= 5
    if risks:
        score = min(score, 25)  # ocupados, subastas, nuda propiedad…: no aptos para un primer piso
    if mult < 100:
        # >12% bruto casi nunca es real: ocupado, nuda propiedad, error de m², anuncio mal ubicado…
        score = min(score, 50)
        notes.insert(0, "⚠ Rentabilidad anormalmente alta: revisa si está ocupado, es nuda propiedad, "
                        "los m² son correctos o la ubicación del anuncio es real.")
    score = round(_clamp(score, 0, 100))

    if reform_kind:
        notes.append(f"Reforma {reform_kind} estimada: {round(reform):,} €".replace(",", "."))

    return {
        "estrategia": zone["estrategia"],
        "puntuacion": score,
        "multiplo": round(mult),
        "calidad": multiplier_label(mult),
        "tipo_libro": book_type(gross_yield, price),
        "coste_total": round(total_cost),
        "impuestos_gastos": round(taxes),
        "reforma": round(reform),
        "mobiliario": furniture,
        "rent_bruta": round(gross_yield * 100, 2),
        "rent_neta": round(net_yield * 100, 2),
        "ingresos": income_detail,
        "gastos_anuales": round(expenses),
        "ingreso_mensual_regla": round(monthly_for_rule),
        "gastos_otros_calc": round(other_expenses),
        "cuota_hipoteca": round(payment),
        "capital_aportado": round(equity),
        "cashflow_mes": round(cashflow),
        "roce": round(roce * 100, 1),
        "alquiler_m2": round(rent_m2, 2),
        "alquiler_ref": f"{rent_level} ({rent_n} muestras)" if rent_n else rent_level,
        "venta_m2_zona": round(sale_m2) if sale_m2 else None,
        "bajo_mercado_pct": round(below_market * 100, 1) if below_market is not None else None,
        "precio_objetivo_150": target_price,
        "descuento_necesario_pct": round(discount_needed * 100, 1),
        "senales": opp,
        "alquilado": bool(rented),
        "riesgos": risks,
        "bajadas_pct": drops,
        "dias_visto": days_listed,
        "aviso_normativo": zone.get("aviso_normativo"),
        "notas": notes,
    }
