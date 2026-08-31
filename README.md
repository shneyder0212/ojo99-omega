# OJO-99 Omega AUTO

Versión automática para Render/iPhone/Android.

## Principio no negociable
**Nunca genera números al azar como recomendación.**
Si no hay datos reales o evidencia suficiente, muestra `SIN SEÑAL`.

## Automatización
- Consulta periódica y prudente de una fuente pública de resultados.
- Intervalo predeterminado: 15 minutos.
- Deduplicación por lotería + fecha/hora.
- Guarda la fuente de cada resultado.
- Botón "Sincronizar ahora".
- Mantiene la carga manual y CSV solo como respaldo.
- Soporta sorteos de 3 números y sorteos de longitud variable como Super Kino TV.
- No usa técnicas para evadir bloqueos.
- User-Agent identificable, timeout corto y frecuencia limitada.
- Caché implícita por deduplicación: no reescribe resultados ya guardados.

## Fuentes
El recolector automático incluido usa una página pública de resultados dominicanos:
https://loterianacional.com.do/resultados/

Para históricos oficiales de Lotería Nacional existe además el portal de Datos Abiertos:
https://datos.gov.do/dataset/resultados-de-sorteos-de-banca-loterianacional

Para Kino TV puede verificarse contra LEIDSA:
https://www.leidsa.com/

**Importante:** una web externa puede cambiar su HTML. Si eso ocurre, el panel mostrará error de fuente sin inventar datos.

## Render
1. Sube este proyecto a GitHub o Render.
2. Crea PostgreSQL en Render.
3. Pon su `DATABASE_URL` en el servicio web.
4. Deploy.
5. Abre la URL HTTPS en iPhone.
6. Safari > Compartir > Añadir a pantalla de inicio.

## Variables
- `AUTO_SYNC_ENABLED=true`
- `AUTO_SYNC_MINUTES=15`
- `RESULTS_SOURCE_URL=https://loterianacional.com.do/resultados/`

## Ejecutar local
```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```

## Advertencia estadística
OJO Score es una puntuación interna de evidencia; no es una probabilidad garantizada de premio.
