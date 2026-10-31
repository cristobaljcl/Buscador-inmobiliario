# Prompt maestro: Radar de Oportunidades Inmobiliarias

## Misión
Construir un **radar personal de inversión inmobiliaria** que cada mañana detecte, puntúe y priorice
las mejores oportunidades de compra para alquilar en España. Debe aplicar la metodología de
*"(Invierte en) Tu Primer Piso"* (Inversor Directivo) y avisarme solo de lo que merece mi tiempo.
El objetivo no es listar anuncios, sino **decirme qué 3-5 pisos debo llamar hoy y por qué**.

## 1. Fuentes de datos (por orden de preferencia)
1. **API oficial de Idealista** (developers.idealista.com), si concede acceso.
2. **Alertas por email de Idealista y pisos.com**: búsquedas guardadas en cada portal cuyos correos
   se leen y procesan automáticamente. Es la vía legal y estable.
3. **pisos.com** web, con peticiones moderadas que respeten robots.txt y los términos de uso.
4. Fuentes de contexto: precios medios de alquiler por zona (Idealista o Fotocasa informes),
   INE (población y renta), Catastro (año de construcción y m² reales), y datos de viviendas
   turísticas (registro autonómico de VUT, AirDNA o Inside Airbnb).
- Deduplicar el mismo inmueble publicado en varios portales (dirección aproximada, m², precio, fotos).
- Guardar el **histórico de cada anuncio**: bajadas de precio, días publicado y reapariciones.

## 2. Configuración de estrategias (archivo `config.yaml`, editable sin tocar código)
Cada provincia o zona tiene una estrategia:
- **LARGA DURACIÓN**: provincias o municipios, precio máximo, m² mínimos, habitaciones, planta,
  ascensor (opcional, porque sin ascensor suele rendir más), estado (incluido "a reformar"),
  alquiler de referencia por m².
- **VACACIONAL**: lo mismo, más ADR (precio medio por noche), ocupación estimada por temporada,
  si la normativa local permite licencia VUT, cercanía a playa o centro, y gestión (comisión
  15-25%).
- **HABITACIONES** (opcional): pisos grandes cerca de universidades u hospitales.

## 3. Motor de valoración ("Tu Primer Piso")
Para cada anuncio calcular:
- **Coste total** = precio + ITP de la comunidad + notaría, registro y gestoría (~2%) + reforma
  estimada según el estado.
- **Alquiler estimado**: comparables de la zona (€/m²) o, en vacacional, ADR × ocupación.
- **Regla del 150**: múltiplo = coste total ÷ alquiler mensual.
  <150 extraordinaria · 150-180 buena · 180-210 correcta · 210-240 regular · >240 descartar.
- **Rentabilidad bruta y neta**: descontar comunidad, IBI, seguros, seguro de impagos, vacío (5-8%),
  mantenimiento (5-10%) e impuestos.
- **Cash flow mensual y rentabilidad sobre capital aportado (ROCE)**, con una hipoteca configurable:
  80% LTV, tipo fijo y plazo de 25-30 años.
- **Clasificación según el libro**: Tipo 1 "piso como el mío" (3-4% bruto), Tipo 2 "piso con algún
  pero" (5-6%, el preferido) y Tipo 3 "<80K" (7-10%, con más gestión).
- **Señales de oportunidad**: bajada de precio reciente, más de 60 días publicado, palabras como
  "urge", "herencia", "a reformar", "ocasión", "negociable", o precio/m² inferior en más de un 15%
  a la media de la zona.
- **Señales de riesgo**: "ocupado", "nuda propiedad", "subasta", "sin cédula", bajos interiores,
  zonas sin demanda o zonas tensionadas con topes de alquiler (Ley de Vivienda).
- **Puntuación final de 0 a 100** con una explicación en lenguaje natural generada por IA
  (Claude). Incluye una **oferta de negociación sugerida**: el precio máximo para alcanzar
  múltiplo 150.

## 4. Informe diario (07:00, hora de Madrid)
Se envía por email a cristobalcl85@gmail.com:
- Asunto: `🏠 Radar 31/10 — 3 oportunidades TOP, 27 nuevas, 5 bajadas de precio`.
- **TOP del día**: tarjetas con foto, precio, múltiplo, rentabilidad neta, cash flow, puntuación,
  motivo y enlace.
- Bajadas de precio en pisos que ya estoy siguiendo.
- Resumen por provincia: nuevos anuncios, mediana de €/m² y tendencia semanal.
- Los anuncios descartados no aparecen en el correo; quedan en la web por si quiero revisarlos.

## 5. Aplicación web (móvil y PC)
- Web responsive o PWA instalable en el móvil, protegida con contraseña.
- Listado filtrable, mapa, ficha con calculadora editable (cambiar alquiler, reforma o hipoteca y
  recalcular), favoritos, estados (Nuevo → Visto → Llamado → Visitado → Oferta → Descartado) y notas.
- Panel de evolución de precios por zona.
- Cuando marco un piso como "bueno" o "malo", el sistema aprende mis preferencias y ajusta la
  puntuación.

## 6. Requisitos técnicos
- Python, SQLite o Postgres, ejecución programada en la nube (GitHub Actions o un VPS), así que
  no depende de que mi PC esté encendido.
- Envío de correo con Gmail SMTP y una contraseña de aplicación. Los secretos van en variables de
  entorno, nunca en el código.
- Logs y aviso por email si una fuente falla (el portal cambia el HTML, bloquea, etc.).
- Todo en español. Cifras en € con formato español.

## 7. Fases
1. **MVP (semana 1)**: config + pisos.com y alertas email de Idealista + motor de valoración +
   email diario.
2. **Web (semana 2)**: panel móvil con fichas, calculadora y estados.
3. **Inteligencia (semana 3+)**: puntuación con IA, histórico de precios, aprendizaje de
   preferencias, datos de mercado vacacional.

## Métrica de éxito
Recibir cada día menos de 5 pisos que de verdad llamaría, y cerrar mi primera compra con
múltiplo ≤ 170 en menos de 6 meses.
