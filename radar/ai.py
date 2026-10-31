"""Análisis cualitativo con Claude de las mejores oportunidades (opcional: requiere ANTHROPIC_API_KEY)."""
import json
import logging
import os

log = logging.getLogger(__name__)

SYSTEM = """Eres un analista de inversión inmobiliaria en España que aplica la metodología del libro
"(Invierte en) Tu Primer Piso" (Inversor Directivo): priorizar flujo de caja sobre revalorización,
regla del 150 (coste total / alquiler mensual; <150 extraordinaria, 150-180 buena, >200 evitar),
preferencia por el "piso con algún pero" (defectos que bajan el precio pero no la alquilabilidad:
sin ascensor, poca luz, reforma asumible), zonas con demanda de alquiler estable, hipoteca a tipo fijo,
colchón de liquidez y negociación basada en números. Eres directo, concreto y escéptico: señalas
lo que falta por verificar. Escribe en español de España."""

SCHEMA = {
    "type": "object",
    "properties": {
        "veredicto": {"type": "string", "enum": ["Llamar hoy", "Interesante", "Solo si negocia", "Descartar"]},
        "comentario": {"type": "string", "description": "2-3 frases con la tesis de inversión"},
        "puntos_fuertes": {"type": "array", "items": {"type": "string"}},
        "riesgos": {"type": "array", "items": {"type": "string"}},
        "preguntas_vendedor": {"type": "array", "items": {"type": "string"},
                               "description": "3 preguntas clave para la primera llamada"},
    },
    "required": ["veredicto", "comentario", "puntos_fuertes", "riesgos", "preguntas_vendedor"],
    "additionalProperties": False,
}


def analyze(listings: list[dict], model: str) -> dict[str, dict]:
    """Devuelve {listing_id: análisis}. Si no hay API key o falla, devuelve {} sin interrumpir el radar."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return {}
    import anthropic

    client = anthropic.Anthropic()
    out = {}
    for l in listings:
        v = l["valuation"]
        facts = {
            "titulo": l.get("title"), "ubicacion": l.get("location"), "precio": l["price"], "m2": l.get("m2"),
            "habitaciones": l.get("rooms"), "planta": l.get("floor"),
            "descripcion": (l.get("description") or "")[:2500], "estrategia": v["estrategia"],
            "metricas": {k: v[k] for k in ("multiplo", "calidad", "tipo_libro", "coste_total", "rent_bruta",
                                           "rent_neta", "cashflow_mes", "roce", "precio_objetivo_150",
                                           "ingresos", "reforma", "senales", "riesgos", "bajadas_pct",
                                           "bajo_mercado_pct", "aviso_normativo")},
        }
        try:
            resp = client.beta.messages.create(
                model=model,
                max_tokens=4000,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                system=SYSTEM,
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
                messages=[{"role": "user", "content":
                           "Analiza esta oportunidad para un inversor que busca su primer piso de alquiler:\n"
                           + json.dumps(facts, ensure_ascii=False)}],
            )
            if resp.stop_reason == "refusal":
                continue
            text = next(b.text for b in resp.content if b.type == "text")
            out[l["id"]] = json.loads(text)
        except Exception as e:  # noqa: BLE001 - la IA es un extra; nunca debe tumbar el informe
            log.warning("Análisis IA fallido para %s: %s", l["id"], e)
            if isinstance(e, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)) or "credit balance" in str(e):
                log.warning("Sin acceso a la API de Anthropic (clave o saldo): se omite el análisis IA.")
                break
    return out
