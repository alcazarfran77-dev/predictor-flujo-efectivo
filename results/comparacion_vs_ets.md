# ¿Mejora algún modelo a ETS de forma significativa?

Comparación pareada sobre 276 orígenes del backtest. Prueba de Diebold-Mariano con varianza HAC, intervalo por bootstrap de bloques y ajuste de Holm. Mejora mínima exigida: 5 % de MAE.

- **SARIMAX**: MAE 2.33 M MXN, 5.2 % más error que ETS (IC 95 %: -8.3 a -2.5 %; p ajustada 0.002). Peor, significativo. WQL 0.6495 (-2.4 %): sin diferencia significativa.
- **SARIMAX + temporada**: MAE 2.36 M MXN, 6.5 % más error que ETS (IC 95 %: -10.1 a -3.4 %; p ajustada 0.001). Peor, significativo. WQL 0.6503 (-2.5 %): sin diferencia significativa.
- **Naive (referencia)**: MAE 3.05 M MXN, 37.7 % más error que ETS (IC 95 %: -43.6 a -31.9 %; p ajustada 0.000). Peor, significativo. WQL 1.0183 (-60.6 %): peor, significativo.

Esta corrida no incluye Chronos-2 ni Chronos-Bolt; la comparación solo cubre los modelos estadísticos.
