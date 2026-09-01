# OJO-99 Omega V7 — Comando Supremo

V7 eleva la robustez del análisis, no promete premios.

## Nuevo en V7
- Anti-sobreajuste.
- Ensemble multi-ventana: largo/medio/corto.
- Detección de cambio de régimen (drift).
- Validación walk-forward por motor.
- Motores sin ventaja fuera de muestra reciben peso mínimo o cero.
- Calidad de datos antes de permitir señales fuertes.
- Puertas de confianza.
- Señal "COMANDO SUPREMO" solo con:
  - muestra amplia,
  - datos de alta calidad,
  - varios motores validados,
  - palé de score extremo.
- DB-first: la app sigue funcionando aunque falle el recolector.
- Circuit breaker y backoff.
- Importación CSV propia.
- Cero números al azar.

## Importante
Una lotería legítima está diseñada para ser aleatoria.
V7 puede medir patrones históricos y descartar motores débiles, pero no puede
garantizar aciertos ni crear una ventaja que los datos no demuestren.


## Contador de aciertos V7.1
La app muestra por lotería cuántos aciertos reales ha tenido y la fecha de cada uno.
Solo cuenta predicciones que fueron congeladas antes del sorteo y evaluadas después.
Esto evita contar aciertos retrospectivos.


## Radar Total V7.2
V7.2 añade explícitamente afinidad, compañeros, jaladores, inversos, secuencias
de uno y dos sorteos, posición, día de semana, día del mes, mes y familias de
números. Estas señales no se asumen útiles: cada motor se valida walk-forward
y puede recibir peso cero si no supera su baseline.


## Alertas Máximas V7.3
La etiqueta 100/100 representa el máximo score interno del sistema.
No representa 100% de probabilidad real.

La alerta de número exige muestra, calidad, varios motores validados y score extremo.
La alerta de palé exige además lift y coocurrencia fuerte.
La alerta de tripleta exige muestra todavía mayor y repetición histórica.

Super Kino TV utiliza un radar separado con ventanas largo/medio/corto y afinidades
propias, porque no tiene la misma estructura de una quiniela de tres números.


## V8 Arena Total
V8 agrega Top 5 + Más Fuego para números, palés y tripletas; ADN de candidatos,
régimen semanal, presión por decenas, fechas especiales, Consejo de Generales
y dos jugadas Kino A/B. Todas las recomendaciones siguen la regla absoluta:
cero números al azar y ninguna señal fuerte sin validación.
