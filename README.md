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

## Daily AI Reporter

El `daily_ai_report.py` genera un paquete AI_REVIEW ZIP diario con analisis compacto de senales, lifecycle, perdidas, no-progress, guards y readiness.

### Archivos generados en el ZIP

| Archivo | Descripcion |
|---|---|
| `00_README_FOR_AI.md` | Instrucciones para revision AI |
| `01_executive_summary.md` | Resumen ejecutivo |
| `08_strategy_hypotheses.json` | Hipotesis de estrategia |
| `09_ai_prompt.md` | Prompt para AI |
| `11_deep_diagnostics.json` | Diagnosticos profundos |
| `12_t02_no_progress_reclaim_zone_pf.json` | T02 diagnostics |
| `f5_t03b_integration_sections_slim.json` | Slim F5_T03B sections |
| `13-16` | F5_T04bcd batch2 diagnostics |
| `17-18` | F5_T04e loss contribution & AI insight |
| `19_telegram_lifecycle_reconciliation_v2.json` | F5_T09a lifecycle reconciliation |
| `20-21` | F5_T09bc no-progress & MFE capture |
| `22-26` | F5_T09dfghi guard filter segmentation |
| `27_symbol_not_allowed_shadow_alpha.json` | F5_T09e symbol alpha |
| `28_f5_t09_ai_super_digest.json/md` | F5_T10 compact AI digest |
| `29_f5_t12_strategy_change_readiness.json/md` | **F5_T12 compact strategy readiness digest** |

### F5_T12 Strategy Change Readiness Digest

El digest `29_f5_t12_strategy_change_readiness.json` y `.md` resume si los cambios F5_T12 del Bot estan justificados:

- **denominators**: senales oficiales, enviadas a Telegram, candidatos, eventos, facts
- **pf_core**: Profit Factor de senales enviadas y totales
- **loss_top**: Top 5 contribuyentes de perdida por outcome, symbol, side, zone
- **no_progress_core**: conteo, avg_r, bucket_counts, top symbols
- **risk_context_candidates**: casos que habrian sido bloqueados por reglas F5_T12
- **guard_value**: guards con net_guard_value positivo/negativo
- **data_quality**: MFE/MAE known, data gaps, confidence warnings
- **human_checklist**: checklist de 10 items para revisar antes de deploy/cambio de flags

Ambos archivos estan por debajo de 95,000 caracteres. La evidencia completa permanece server-side.

### Ejecutar reporter

```bash
# Dry-run (no escribe archivos)
python daily_ai_report.py --dry-run

# Generar reporte completo
python daily_ai_report.py

# Ventana personalizada
python daily_ai_report.py --window 24h
python daily_ai_report.py --window 7d
```
