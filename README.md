# BotVIP / AlphaScalp Audit Dashboard

Dashboard independiente en Streamlit para auditoria profesional de lifecycle, F4_T11a y calibracion observacional.

## Principios de seguridad

- No modifica el bot principal.
- No ejecuta migraciones.
- No hace INSERT/UPDATE/DELETE.
- Usa conexion SQLite en modo `read-only` cuando es posible.
- Activa `PRAGMA query_only=ON` en la conexion.
- Recomendado: trabajar sobre una copia local de `trading_bot.db` para auditorias pesadas.

## Estructura

```text
botvip_audit_dashboard/
  app.py
  requirements.txt
  .env.example
  README.md
  src/
    db.py
    schema.py
    metrics.py
    parsers.py
    charts.py
    reports.py
  pages/
    1_Overview.py
    2_F4_T11a_Lifecycle_Audit.py
    3_Events_Explorer.py
    4_Signals_Explorer.py
    5_OFA_Funnel.py
    6_Rejection_Analysis.py
    7_Strategy_Calibration_Lab.py
    8_Symbol_Performance.py
    9_Export_Reports.py
```

La primera version funcional esta concentrada en `app.py` y modulos `src/`. Las paginas en `pages/` quedan como stubs seguros para evolucionar a multipage sin romper la primera entrega.

## Instalacion local

```bash
cd botvip_audit_dashboard
python -m venv .venv
# Linux/macOS
source .venv/bin/activate
# Windows PowerShell
# .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env
streamlit run app.py
```

## Configurar DB

Edita `.env`:

```text
DB_PATH=./data/trading_bot.db
```

Recomendado: copiar la DB desde produccion a `./data/trading_bot.db` antes de auditar.

## Copia segura desde Vultr (opcional)

Ejemplo conceptual, ajusta usuario/host/ruta local:

```bash
ssh usuario@TU_SERVIDOR "sudo docker compose -f /opt/botvip/docker-compose.yml exec -T bot sh -lc 'ls -lh /app/data/trading_bot.db'"
scp usuario@TU_SERVIDOR:/opt/botvip/data/trading_bot.db ./data/trading_bot.db
```

Si la DB real solo existe dentro del contenedor, usa `docker cp` en el servidor para sacarla a una ruta temporal de solo auditoria y luego `scp`.

## Uso

```bash
streamlit run app.py
```

En la barra lateral puedes seleccionar ventana: 12h, 24h, 7d o custom. Usa `Refresh cache` para limpiar cache de Streamlit.

## Limitaciones de esta version

- No asume schema exacto: descubre tablas y columnas.
- Si faltan columnas esperadas, muestra warnings y continua.
- Las metricas F4_T11a se calculan con el mejor dato disponible entre `signal_events`, `signals` y `metrics_json`.
- Las paginas avanzadas son stubs. El dashboard funcional inicial esta en `app.py`.
