"""Probabilidad y alerta de déficit a partir de los cuantiles P10, P50 y P90.

La regla del reporte técnico es alertar a tesorería cuando el P10 de un día es menor a 0, es decir, cuando
el déficit cae dentro del intervalo del 80 %. Aquí se conserva esa regla como nivel "vigilancia" y se agregan
dos niveles más severos para ordenar las alertas:

    alta        P50 < 0                      el déficit es más probable que no
    media       probabilidad >= UMBRAL_MEDIA el doble, aprox., de la frecuencia histórica de déficit
    vigilancia  P10 < 0                      regla del reporte
    ninguna     P10 >= 0
"""
import numpy as np
import pandas as pd
from scipy.stats import norm

Z90 = float(norm.ppf(0.9))
UMBRAL_MEDIA = 0.25
NIVELES = ["ninguna", "vigilancia", "media", "alta"]


def probabilidad_deficit(p10, p50, p90, umbral=0.0):
    """P(flujo < umbral) con una normal partida que pasa por P10, P50 y P90.

    Debajo de la mediana usa la dispersión (P50 - P10) / z0.9 y arriba (P90 - P50) / z0.9. Para los modelos
    estadísticos, cuyo intervalo es gaussiano y simétrico, el resultado es exacto; para Chronos, que entrega
    cuantiles asimétricos, respeta la asimetría de cada lado.
    """
    p10, p50, p90 = (np.asarray(v, dtype=float) for v in (p10, p50, p90))
    sigma = np.where(p50 > umbral, (p50 - p10) / Z90, (p90 - p50) / Z90)
    sigma = np.maximum(sigma, 1e-9)
    return norm.cdf((umbral - p50) / sigma)


def nivel_alerta(p10, p50, prob, umbral=0.0, umbral_media=UMBRAL_MEDIA):
    """Nivel de alerta de un día (vectorizado)."""
    p10, p50, prob = (np.asarray(v, dtype=float) for v in (p10, p50, prob))
    return np.select([p50 < umbral, prob >= umbral_media, p10 < umbral],
                     ["alta", "media", "vigilancia"], default="ninguna")


def resumen_alerta(dias, umbral=0.0):
    """Alerta del horizonte completo: el día más severo, cuántos días la activan y el peor escenario."""
    niveles = list(dias["nivel"])
    peor = max(niveles, key=NIVELES.index)
    en_alerta = dias[dias["nivel"] != "ninguna"]
    return {
        "activa": peor != "ninguna",
        "nivel": peor,
        "dias_en_alerta": int(len(en_alerta)),
        "primera_fecha": en_alerta["fecha"].min() if len(en_alerta) else None,
        "probabilidad_maxima": float(dias["prob_deficit"].max()),
        "peor_p10": float(dias["p10"].min()),
        "colchon_sugerido": float(max(0.0, umbral - dias["p10"].min())),
    }


def desempeno_reglas(backtest, umbral=0.0, umbral_media=UMBRAL_MEDIA):
    """Qué tan bien anticipó cada nivel los días con déficit en el backtest (precision, recall, F1)."""
    if backtest is None or backtest.empty:
        return []
    prob = probabilidad_deficit(backtest["P10"], backtest["P50"], backtest["P90"], umbral)
    real = (backtest["y"] < umbral).to_numpy()
    reglas = {
        "vigilancia (P10 < 0)": (backtest["P10"] < umbral).to_numpy(),
        f"media (probabilidad >= {umbral_media:.0%})": prob >= umbral_media,
        "alta (P50 < 0)": (backtest["P50"] < umbral).to_numpy(),
    }
    filas = []
    for nombre, alerta in reglas.items():
        vp = int((alerta & real).sum())
        fp = int((alerta & ~real).sum())
        fn = int((~alerta & real).sum())
        precision = vp / (vp + fp) if vp + fp else None
        recall = vp / (vp + fn) if vp + fn else None
        f1 = (2 * precision * recall / (precision + recall)) if precision and recall else None
        filas.append({
            "regla": nombre,
            "dias_alertados_pct": float(alerta.mean() * 100),
            "precision": precision, "recall": recall, "f1": f1,
            "eventos_reales": int(real.sum()), "pronosticos": int(len(real)),
            "tasa_base_pct": float(real.mean() * 100),
        })
    return filas


def acumulado(dias):
    """Cuantiles del flujo acumulado del horizonte, suponiendo días independientes.

    La autocorrelación a un día del flujo operativo es cercana a 0.03, así que la suma de varianzas es una
    aproximación razonable. La mediana del acumulado es la suma de las medianas.
    """
    p50 = float(dias["p50"].sum())
    sigma = float(np.sqrt((((dias["p90"] - dias["p10"]) / (2 * Z90)) ** 2).sum()))
    return {"p10": p50 - Z90 * sigma, "p50": p50, "p90": p50 + Z90 * sigma,
            "prob_deficit": float(norm.cdf(-p50 / max(sigma, 1e-9)))}


def tabla_dias(pred, umbral=0.0, umbral_media=UMBRAL_MEDIA):
    """Agrega probabilidad y nivel de alerta a un pronóstico con columnas P10, P50 y P90."""
    d = pd.DataFrame({
        "fecha": pd.to_datetime(pred["ds"]).dt.strftime("%Y-%m-%d").to_numpy(),
        "p10": pred["P10"].to_numpy(float), "p50": pred["P50"].to_numpy(float),
        "p90": pred["P90"].to_numpy(float),
    })
    d["prob_deficit"] = probabilidad_deficit(d["p10"], d["p50"], d["p90"], umbral)
    d["nivel"] = nivel_alerta(d["p10"], d["p50"], d["prob_deficit"], umbral, umbral_media)
    return d
