# ¿Mejora algún modelo a ETS de forma significativa?

Comparación pareada sobre 276 orígenes del backtest. Prueba de Diebold-Mariano con varianza HAC, intervalo por bootstrap de bloques y ajuste de Holm. Mejora mínima exigida: 5 % de MAE.

- **Chronos-2 (cross-learning)**: MAE 1.98 M MXN, 10.3 % menos error que ETS (IC 95 %: +8.6 a +12.7 %; p ajustada 0.000). Mejora significativa. WQL 0.5795 (+8.6 %): mejora significativa.
- **Chronos-2**: MAE 1.99 M MXN, 10.2 % menos error que ETS (IC 95 %: +8.4 a +12.7 %; p ajustada 0.000). Mejora significativa. WQL 0.5762 (+9.2 %): mejora significativa.
- **Chronos-2 + temporada**: MAE 1.99 M MXN, 10.0 % menos error que ETS (IC 95 %: +8.2 a +12.5 %; p ajustada 0.000). Mejora significativa. WQL 0.5774 (+9.0 %): mejora significativa.
- **Chronos-Bolt Small**: MAE 2.01 M MXN, 9.0 % menos error que ETS (IC 95 %: +6.9 a +12.2 %; p ajustada 0.000). Mejora significativa. WQL 0.5960 (+6.0 %): mejora significativa.
- **SARIMAX**: MAE 2.33 M MXN, 5.1 % más error que ETS (IC 95 %: -8.2 a -2.4 %; p ajustada 0.002). Peor, significativo. WQL 0.6490 (-2.3 %): sin diferencia significativa.
- **SARIMAX + temporada**: MAE 2.36 M MXN, 6.5 % más error que ETS (IC 95 %: -10.0 a -3.4 %; p ajustada 0.001). Peor, significativo. WQL 0.6501 (-2.5 %): sin diferencia significativa.
- **Naive (referencia)**: MAE 3.05 M MXN, 37.7 % más error que ETS (IC 95 %: -43.6 a -31.9 %; p ajustada 0.000). Peor, significativo. WQL 1.0183 (-60.6 %): peor, significativo.

**Conclusión:** Chronos-2 (cross-learning) mejora de forma significativa a ETS (10.3 % menos MAE, por encima del mínimo de 5 %). 

## Umbrales de aceptación

Los cuatro modelos Chronos cumplen rMAE, WQL, cobertura y F1 de dirección, y ninguno cumple el sesgo en las 4 semanas posteriores al cambio de temporada del 01-09-2026 (límite ±0.3 M MXN por día): Chronos-2 0.64, Chronos-2 + temporada 0.49, Chronos-2 (cross-learning) 0.66 y Chronos-Bolt Small 0.54, frente a 0.38 de ETS. La latencia de Chronos no viene en estos archivos, así que ese umbral queda sin evaluar.

**Decisión:** Chronos-2 mejora a ETS de forma significativa, pero no lo reemplaza todavía. La regla del proyecto pide cumplir todos los umbrales, y falta medir su latencia y validarlo con datos reales. ETS sigue como modelo recomendado de la API y Chronos-2 queda como candidato.

## Origen de los datos

Backtest corrido en Google Colab con GPU (`tablas/backtest.csv.gz`, 8 modelos, 5,520 pronósticos cada uno). Los valores reales y los pronósticos de ETS y del ingenuo son idénticos a los de `results/tablas/backtest.csv.gz`. Los de SARIMAX difieren en promedio 0.02 M MXN por pronóstico, por el optimizador en otro equipo. La API sigue usando el backtest en CPU de `results/tablas/`.
