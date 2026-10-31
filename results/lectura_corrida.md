# Lectura de la corrida

- **Base de evaluación:** 5,520 pronósticos por modelo (276 orígenes × 4 series × 5 pasos), sobre 283 días hábiles de historia.
- **Menor error puntual:** ETS, MAE de 2.21 M MXN (rMAE 0.73).
- **Desde el 01-09-2026:** menor MAE para ETS (2.42 M MXN).
- **Mejor pronóstico probabilístico (WQL):** ETS (WQL 0.634).
- **Superan al naive (rMAE < 1):** ETS, SARIMAX, SARIMAX + temporada.
- **Dirección del flujo:** mejor F1 para ETS (0.70; accuracy 0.71, precision 0.72, recall 0.68).
- **Calibración del intervalo:** el más cercano al 80 % es SARIMAX + temporada (82 %).
- **Latencia estadísticos (CPU):** 235.7 a 5,402.4 ms por llamada; crece linealmente con el número de series.
- **Covariable temporada en SARIMAX** (con vs. sin): antes del 01-09-2026: MAE +1.4 %, sesgo -0.16 → -0.08 M MXN; desde el 01-09-2026: MAE -0.3 %, sesgo +0.24 → -0.22 M MXN.
