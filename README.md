# OJO-99 Omega V5 — Red Propia

La aplicación móvil ya no consulta directamente las webs externas.
Los recolectores del servidor obtienen datos con límites, los guardan como
observaciones y OJO-99 publica una API propia desde PostgreSQL.

## Flujo
Fuentes externas → RawObservation → verificador → CanonicalDraw → API propia → app

## API propia
- GET /api/results
- GET /api/history?lottery=...
- GET /api/network/status
- POST /api/network/sync-now
- POST /api/network/history-step
- GET /api/analyze?lottery=...
- GET /api/performance?lottery=...
- GET /api/backtest?lottery=...

## Verificación
- 2 fuentes con el mismo resultado → VERIFIED
- 1 fuente → PROVISIONAL
- fuentes discrepantes → CONFLICT
- CONFLICT no entra al análisis

## Seguridad del recolector
- peticiones espaciadas
- pausa en 403/429
- backoff
- caché histórica
- un solo recolector a la vez
- no evasión de bloqueos

## Importante
SOURCE_SECONDARY_URL queda vacío por defecto.
Solo añade una segunda fuente real/autorizada cuando tengas una válida.
No inventes una URL.

## Regla
CERO NÚMEROS AL AZAR.
