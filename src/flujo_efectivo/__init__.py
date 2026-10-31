"""Predictor de flujo de efectivo: Chronos-2, Chronos-Bolt Small, ETS y SARIMAX sobre movimientos FEBAN.

El código es el mismo del cuaderno `notebooks/predictor_flujo_efectivo_chronos.ipynb`, separado en módulos:

- config       parámetros del experimento (celda 4.1)
- datos        lectura, deduplicación, clasificación y series diarias (4.2 a 4.5)
- covariables  covariable `temporada` y calendario
- modelos      adaptadores de cada modelo y registro (5.1)
- backtest     walk-forward y pronóstico hacia adelante (5.2 y 5.3)
- metricas     regresión, clasificación y periodos (6)
- latencia     latencia y escalabilidad (7)
- graficas     figuras en PNG (8)
- reporte      lectura de la corrida y exportación (8.5 y 9.1)
"""
from .config import Config

__all__ = ["Config"]
