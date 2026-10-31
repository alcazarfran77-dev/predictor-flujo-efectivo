# Salidas

Aquí escribe la API el pronóstico completo en JSON. Cada archivo se llama `pronostico_<modelo>_<último día con datos>.json` y es el mismo documento que responde `GET /api/v1/reporte`. Se genera de cuatro formas:

- Al arrancar la API, en cuanto el modelo recomendado tiene pronóstico y backtest.
- Con el botón "Guardar en el servidor" del dashboard o con `POST /api/v1/reporte?modelo=ETS`.
- Sin levantar la API: `python -m api.exportar --modelo ETS`.
- El botón "Descargar" del dashboard baja el mismo archivo a la computadora de quien lo usa.

`pronostico_ETS_2026-09-30.json` es el que se genera con los datos del repositorio.

## Contenido

| Campo | Qué trae |
|---|---|
| `formato`, `version` | `flujo-efectivo/pronostico`, versión 1.0 |
| `generado`, `pronostico_calculado` | Cuándo se escribió el archivo y cuándo se calculó el pronóstico (UTC) |
| `datos` | Archivo de entrada, periodo, días hábiles, movimientos, duplicados y avisos de la validación |
| `modelo`, `familia`, `recomendado`, `latencia_ms` | Modelo usado y cuánto tardó en pronosticar todas las series |
| `horizonte`, `cuantiles`, `unidades`, `umbral_deficit` | 5 días hábiles, P10/P50/P90, MXN y el umbral de déficit (0) |
| `niveles_alerta` | Qué significa cada nivel: ninguna, vigilancia, media y alta |
| `confianza` | Nivel de confianza del modelo, métricas del backtest (MAE, rMAE, WQL, cobertura, F1), umbrales del reporte con su valor y si se cumplen, y el estado del monitoreo |
| `series[]` | Una entrada por serie (`OPERATIVO_TOTAL`, `OPERATIVO_BBVA`, `OPERATIVO_HSBC`, `NETO_TOTAL`) |
| `series[].dias[]` | Por día: fecha, P10, P50, P90, probabilidad de déficit, nivel de alerta y error histórico de ese horizonte |
| `series[].acumulado` | P10, P50 y P90 del flujo acumulado del horizonte |
| `series[].alerta_deficit` | Nivel del horizonte, días en alerta, peor P10, colchón sugerido y cómo funcionó cada nivel en el backtest |
| `series[].confianza` | Resumen de confianza de esa serie |

Los montos van en pesos y las fechas en formato AAAA-MM-DD. El esquema formal es el modelo `Reporte` del contrato OpenAPI (`/docs`).
