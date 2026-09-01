# OJO-99 Omega V4 Predictivo

## Regla permanente
**CERO NÚMEROS AL AZAR.**
Si no hay datos suficientes o la evidencia es débil, muestra `SIN SEÑAL`.

## Qué añade V4
- Calendario maestro por turno.
- Zona horaria maestra: `America/Santo_Domingo`.
- Sincronización automática de resultados recientes.
- Backfill histórico incremental y respetuoso.
- Memoria PostgreSQL.
- Motor de frecuencia, recencia, atraso, día, reverso y transición.
- Motor especializado de afinidad/palés.
- Motor de tripletas observadas.
- Walk-forward/backtesting temporal.
- Métrica objetivo 2-DE-3.
- Predicciones congeladas antes del sorteo.
- Evaluación posterior.
- Panel de rendimiento real.
- Jugada Maestra solo con evidencia estricta.
- Sin scraping agresivo ni evasión de bloqueos.

## Fuente
Por defecto:
https://loterianacional.com.do/resultados/

El importador histórico intenta la misma fuente usando `?date=DD-MM-YYYY`.
Si la web cambia de formato, OJO-99 registra el error y **no inventa resultados**.

## Render
Mantén tu `DATABASE_URL` actual.
Sube este proyecto a GitHub y usa `Deploy latest commit`.

## Aviso
OJO Score es evidencia interna, no garantía ni porcentaje real de premio.
El panel de backtesting sirve para medir qué tan bien o mal funciona el sistema frente a datos reales.
