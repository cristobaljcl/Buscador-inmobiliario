# 🏠 Radar Inmobiliario

Cada mañana a las **7:00 (hora de Madrid)**, el radar:

1. Lee los anuncios nuevos de **pisos.com** y **Fotocasa** (venta y alquiler) y de **Idealista** (alertas por email y/o API oficial). Si un mismo piso aparece en varios portales, se agrupa.
2. Calcula el alquiler real de cada barrio a partir de los anuncios de alquiler.
3. Valora cada piso con la metodología de *(Invierte en) Tu Primer Piso*: regla del 150, rentabilidad bruta y neta, cash flow con hipoteca, ROCE, tipo de piso según el libro y precio de oferta para alcanzar múltiplo 150.
4. Puntúa cada piso de 0 a 100 y pide a Claude un análisis del TOP: veredicto, riesgos y preguntas para el vendedor.
5. Te envía el informe a **cristobalcl85@gmail.com**, con el TOP 10 de anuncios activos de cada provincia ordenado por puntuación.
6. Publica un **panel web cifrado** (móvil y PC) con filtros, mapa de precios, calculadora editable, favoritos, estados y notas.

Zonas configuradas por defecto (en [config.yaml](config.yaml)):

| Zona | Estrategia | Municipios |
|---|---|---|
| Madrid | Larga duración | Madrid capital, Móstoles, Alcorcón, Getafe, Leganés, Fuenlabrada |
| Murcia | Vacacional | Cartagena, San Javier, Los Alcázares, San Pedro del Pinatar, Mazarrón, Águilas |
| Alicante | Vacacional | Torrevieja, Santa Pola, Benidorm, Alicante, Dénia |
| Pontevedra | Vacacional | Sanxenxo, Vigo, Pontevedra, O Grove, Baiona |
| A Coruña | Vacacional | A Coruña, Santiago, Ferrol |

Presupuesto máximo: 250.000 €. Todo es editable en `config.yaml`.

Además hay una búsqueda de vivienda propia, la estrategia **Jubilación gallinos**: todas las viviendas de **Boadilla del Monte** hasta 300.000 €, en cualquier estado. No entra en el informe de inversión. Cada día a las 7:00 se envía un email aparte a nati.seijas@gmail.com con todas las ofertas vigentes y las novedades destacadas. En el panel se ve eligiendo esa estrategia en el filtro. Cualquier zona puede tener su propio email añadiendo `informe: {destinatario, titulo}` en `config.yaml`.

La estrategia **Ofertas aceptables Madrid** recoge todo Madrid capital hasta 300.000 € con 2 habitaciones o más. Excluye Vallecas, Pan Bendito y Abrantes. Cada día se leen enteros pisos.com y Fotocasa. Hay miles de anuncios, así que el email diario (a cristobalcl85@gmail.com con copia a nati.seijas@gmail.com y josefinalozano@hotmail.es) solo trae las novedades y las bajadas de precio. El listado completo está en el panel, eligiendo esa estrategia en el filtro.

---

## Puesta en marcha (unos 20 minutos)

### 1. Repositorio en GitHub
1. Crea una cuenta en <https://github.com> si no la tienes.
2. Crea un repositorio **público** llamado `radar-inmobiliario`. Los datos del panel van cifrados. GitHub Pages en un repositorio privado requiere un plan de pago.
3. Sube el contenido de esta carpeta. Lo más sencillo es **Add file → Upload files** y arrastrar todo, incluida la carpeta `.github`.

### 2. Contraseña de aplicación de Gmail (para enviar el email y leer las alertas de Idealista)
1. Activa la verificación en dos pasos en tu cuenta de Google.
2. Ve a <https://myaccount.google.com/apppasswords> y crea una contraseña llamada "Radar".
3. Copia la clave de 16 caracteres.

### 3. Secretos del repositorio
En **Settings → Secrets and variables → Actions → New repository secret**, crea:

| Secreto | Valor | Obligatorio |
|---|---|---|
| `GMAIL_USER` | `cristobaljcl85@gmail.com` (cuenta que envía el informe y recibe las alertas de Idealista) | Sí |
| `GMAIL_APP_PASSWORD` | La clave de 16 caracteres del paso 2, creada **en esa misma cuenta** | Sí |
| `SITE_PASSWORD` | Una contraseña larga para el panel web | Sí |
| `ANTHROPIC_API_KEY` | Clave de <https://console.anthropic.com> para el análisis con IA | Recomendado |
| `IDEALISTA_API_KEY` / `IDEALISTA_API_SECRET` | Credenciales de <https://developers.idealista.com> | Opcional |

### 4. Panel web (GitHub Pages)
**Settings → Pages → Source: Deploy from a branch → Branch `datos`, carpeta `/docs`**.
La base de datos y el panel se guardan en la rama `datos`. Cada ejecución la sustituye por un único commit sin historial, así el repositorio no crece cada día. El código está en `main`. Los anuncios que llevan 60 días sin aparecer se borran automáticamente.
La URL será `https://TU_USUARIO.github.io/radar-inmobiliario/`. Ponla en `web.url` de `config.yaml`.
En el móvil, abre la URL y elige "Añadir a pantalla de inicio" para usarla como app.

### 5. Alertas de Idealista (recomendado)
Idealista bloquea la lectura automática de su web, así que usamos sus **alertas por email**:
1. En idealista.com, haz una búsqueda por zona: venta, viviendas, hasta 250.000 € y los filtros que quieras.
2. Pulsa **"Guardar búsqueda"** y activa el **aviso por email inmediato o diario**. Las alertas deben llegar al buzón de `GMAIL_USER` (cristobaljcl85@gmail.com): usa esa cuenta en Idealista o reenvía allí los avisos.
3. Repite con cada zona. El radar lee esos correos cada mañana y asigna cada anuncio a su zona con las palabras de `idealista_match`.

Además, solicita acceso a la API oficial en <https://developers.idealista.com>. Es gratis con cuota limitada. Cuando te lo concedan, añade los dos secretos y el radar la usará automáticamente.

### 6. Primera ejecución
**Actions → Radar inmobiliario diario → Run workflow**. En 5-10 minutos recibirás el primer email.
La primera vez crea la base de datos, y el TOP muestra lo mejor publicado actualmente.
Desde el día siguiente, solo verás **novedades y bajadas de precio**.

---

## Cómo se valora un piso

| Métrica | Cálculo |
|---|---|
| Coste total | Precio + ITP de la comunidad + 2% de gastos + reforma estimada por palabras clave + mobiliario (vacacional) |
| Alquiler estimado | Mediana de €/m² de los anuncios de alquiler del mismo barrio (o distrito, o zona) |
| Ingreso vacacional | ADR × 365 × ocupación, menos gestión (20%), plataforma (15%) y suministros |
| **Regla del 150** | Coste total / alquiler mensual: <150 extraordinaria, 150-180 buena, 180-210 correcta, 210-240 regular, >240 mala |
| Rentabilidad neta | (Ingresos − comunidad, IBI, seguros, impago, vacío, mantenimiento) / coste total |
| Cash flow y ROCE | Con hipoteca fija del 80% a 30 años (configurable) |
| Puntuación 0-100 | Múltiplo (50) + ROCE (15) + cash flow (10) + señales de oportunidad (10) + precio bajo mercado (10) + bajada de precio (5), con penalización de 15 por cada riesgo |

**Ajusta las hipótesis de mercado** en `config.yaml` (ADR, ocupación, ITP, gastos). Son estimaciones iniciales y conviene validarlas con datos de tu zona, por ejemplo con AirDNA o preguntando a gestoras locales.

## Ejecutar en local (opcional)
```bash
pip install -r requirements.txt
python -m radar.main --force --no-email    # genera out/informe.html y docs/ (si SITE_PASSWORD está definida)
```

## Limitaciones
- pisos.com y Fotocasa se leen con pocas peticiones y pausas. Si un portal cambia su página, el email mostrará una incidencia y habrá que ajustar `radar/sources/pisos_com.py` o `radar/sources/fotocasa.py`. Fotocasa se puede desactivar con `fotocasa.activada: false`.
- Fotocasa marca los pisos ocupados, en subasta, en nuda propiedad o alquilados. El radar los trata como riesgo (o como "alquilado") igual que si lo dijera la descripción.
- El formato de los emails de Idealista puede cambiar. El lector es heurístico: busca enlaces `/inmueble/<id>`.
- Favoritos, estados y notas del panel se guardan en cada dispositivo, no se sincronizan entre móvil y PC.
- El mapa usa ubicaciones **aproximadas**: los portales no publican coordenadas, así que se geocodifica con OpenStreetMap la calle del título o, si no la hay, el barrio o el municipio. Cada dirección se consulta una sola vez y se guarda en la base de datos. Como máximo se hacen `web.mapa_consultas_max` consultas por ejecución; si hay más, se completan en los días siguientes.
- Las cifras son orientativas y no constituyen asesoramiento financiero. Antes de ofertar, verifica la nota simple, las derramas, la cédula de habitabilidad y la normativa VUT del municipio y de la comunidad de propietarios.
