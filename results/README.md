# Resultados

Estos archivos salen de `python src/ejecutar_backtest.py --sin-chronos` sobre `data/DEPOSITOS_CARGADOS_FEBAN_sep2025_sep2026_anon.xlsx`, con los parámetros por omisión: horizonte de 5 días hábiles, 276 orígenes del 03-09-2025 al 23-09-2026 y corte de periodos el 01-09-2026. Corrió en CPU con 2 vCPU en 7 minutos. `corrida.json` guarda los parámetros, la duración de cada modelo y las versiones de los paquetes.

Los números de ETS, SARIMAX, SARIMAX + temporada y el naive son idénticos a los del cuaderno, porque el script usa el mismo código. Las latencias sí cambian entre corridas según la carga de la máquina; en el reporte técnico usé las de una corrida anterior del cuaderno, que salieron cerca del doble para los modelos estadísticos, aunque la proporción entre ellos se mantiene.

## Chronos-2 y Chronos-Bolt Small

Sus resultados están pendientes. En el entorno donde armé el repositorio no pude descargar los pesos de Hugging Face, así que solo comprobé que el código de los dos modelos corre de punta a punta con versiones diminutas de pesos aleatorios, y esos números no se guardan aquí porque no dicen nada de los modelos reales. Para completarlos hay que correr el cuaderno en Colab con GPU T4, o en una máquina con acceso a Hugging Face:

```bash
python src/ejecutar_backtest.py
```

Esa corrida sobrescribe esta carpeta con los ocho modelos.

## Archivos

| Archivo | Contenido |
|---|---|
| `lectura_corrida.md` | Hallazgos principales, generados a partir de las tablas |
| `resultados_predictor_flujo.xlsx` | Todas las tablas en un solo Excel, una hoja por tabla (igual que la sección 8.5 del cuaderno) |
| `corrida.json` | Parámetros, tamaño de los datos, duración y versiones |
| `tablas/resumen.csv` | Tabla comparativa ordenada por MAE (sección 8.1) |
| `tablas/metricas_regresion.csv` | MAE, sesgo, RMSE, sMAPE, WQL, cobertura P10–P90 y ancho del intervalo, en pesos |
| `tablas/metricas_clasificacion.csv` | Accuracy, precision, recall, F1 y matriz de confusión para dirección, flujo alto y déficit |
| `tablas/metricas_por_periodo.csv` | Las mismas métricas antes y desde el 01-09-2026 |
| `tablas/efecto_temporada.csv` | SARIMAX con covariable contra SARIMAX sin ella, por periodo |
| `tablas/mae_mensual.csv`, `tablas/sesgo_mensual.csv` | Error y sesgo por mes de la fecha pronosticada, en millones de MXN |
| `tablas/mae_por_serie.csv` | MAE por serie y modelo, en millones de MXN |
| `tablas/latencia.csv`, `tablas/escalabilidad.csv` | Latencia por llamada y con 1, 16 y 64 series |
| `tablas/pronostico_siguientes_dias.csv` | Pronóstico del 01 al 07-10-2026 con P10, P50 y P90 |
| `tablas/backtest.csv.gz` | Los 22,080 pronósticos del backtest con su valor real |
| `tablas/series_diarias.csv` | Las cuatro series diarias que se pronostican |
| `tablas/categorias.csv`, `tablas/deduplicacion.csv` | Clasificación de movimientos y control de duplicados |
| `figuras/01_series_diarias.png` … `07_pronostico_siguiente_dia.png` | Las gráficas de las secciones 4.5, 7.1 y 8 del cuaderno, más el MAE mensual |
| `capturas/` | El dashboard conectado a la API en escritorio, móvil y modo oscuro; las genera `tests/test_e2e.py` |

En `escalabilidad.csv` el valor vacío de SARIMAX + temporada con 64 series significa que no se midió: la proyección desde 16 series pasaba de 60 segundos.
