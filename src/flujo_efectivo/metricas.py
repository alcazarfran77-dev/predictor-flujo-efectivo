"""Métricas de regresión, de clasificación y por periodo (sección 6) y tabla resumen (sección 8.1).

Accuracy, precision, recall y F1 se calculan sobre la decisión binaria que tesorería tomaría con cada pronóstico:
- Dirección: ¿entra más efectivo que en el último día conocido?
- Flujo alto: ¿el flujo supera la mediana del historial?
- Déficit: ¿habrá salida neta de efectivo?
"""
import warnings

import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, confusion_matrix, f1_score, precision_score, recall_score

from .backtest import etiquetas_periodo
from .covariables import temporada_de
from .modelos import COL_PUNTO, FAMILIA

NAIVE = "Naive (referencia)"

EVENTOS = {
    "Dirección (sube vs. último dato)": lambda d, col: d[col] > d["ultimo_obs"],
    "Flujo alto (> mediana histórica)": lambda d, col: d[col] > d["mediana_ctx"],
    "Déficit (flujo neto < 0)": lambda d, col: d[col] < 0,
}
EVENTO_PRINCIPAL = next(iter(EVENTOS))


def metricas_regresion(d, cfg):
    error = d[COL_PUNTO] - d["y"]
    denom = d["y"].abs() + d[COL_PUNTO].abs()
    cols_q = cfg.cols_q
    pinball = sum(np.maximum(q * (d["y"] - d[c]), (q - 1) * (d["y"] - d[c])).sum() for q, c in cols_q.items())
    return pd.Series({
        "MAE": error.abs().mean(),
        "Sesgo": error.mean(),                       # pronóstico − real: > 0 sobreestima
        "RMSE": np.sqrt((error ** 2).mean()),
        "sMAPE (%)": (2 * error.abs() / denom).where(denom > 0).mean() * 100,
        "WQL": 2 * pinball / len(cols_q) / d["y"].abs().sum(),
        cfg.etq_cobertura: ((d["y"] >= d[cfg.q_lo]) & (d["y"] <= d[cfg.q_hi])).mean() * 100,
        "Ancho intervalo": (d[cfg.q_hi] - d[cfg.q_lo]).mean(),
        "Respaldos (%)": d["respaldo"].mean() * 100,
    })


def tabla_regresion(resultados, cfg):
    t = resultados.groupby("modelo", sort=False).apply(metricas_regresion, cfg=cfg, include_groups=False)
    t.insert(1, "rMAE vs naive", t["MAE"] / t.loc[NAIVE, "MAE"])
    return t


def mae_por_serie(resultados, nombres, ids):
    return (resultados.groupby(["unique_id", "modelo"], sort=False)
            .apply(lambda d: (d[COL_PUNTO] - d["y"]).abs().mean(), include_groups=False)
            .unstack("modelo")[nombres].loc[ids] / 1e6)


def metricas_clasificacion(d, regla):
    real = regla(d, "y").astype(int).to_numpy()
    pred = regla(d, COL_PUNTO).astype(int).to_numpy()
    vn, fp, fn, vp = confusion_matrix(real, pred, labels=[0, 1]).ravel()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return {
            "accuracy": accuracy_score(real, pred),
            "precision": precision_score(real, pred, zero_division=np.nan),
            "recall": recall_score(real, pred, zero_division=np.nan),
            "F1": f1_score(real, pred, zero_division=np.nan),
            "VP": vp, "FP": fp, "FN": fn, "VN": vn, "n": len(real),
        }


def tabla_clasificacion(resultados):
    return pd.DataFrame([
        {"evento": evento, "modelo": modelo, **metricas_clasificacion(d, regla)}
        for evento, regla in EVENTOS.items()
        for modelo, d in resultados.groupby("modelo", sort=False)
    ]).set_index(["evento", "modelo"])


def metricas_periodo(d, cfg):
    m = metricas_regresion(d, cfg)
    real, pred = d["y"] > d["ultimo_obs"], d[COL_PUNTO] > d["ultimo_obs"]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        m["F1 dirección"] = f1_score(real, pred, zero_division=np.nan)
    m["n"] = len(d)
    return m


def tabla_periodo(resultados, cfg):
    """Métricas antes y desde `fecha_corte`, con rMAE contra el naive del mismo periodo."""
    t = resultados.groupby(["periodo", "modelo"], sort=False).apply(metricas_periodo, cfg=cfg, include_groups=False)
    mae_naive = t.xs(NAIVE, level="modelo")["MAE"]
    t["rMAE vs naive"] = t["MAE"].to_numpy() / mae_naive.reindex(t.index.get_level_values("periodo")).to_numpy()
    return t


def periodos_presentes(resultados, cfg):
    _, antes, desde = etiquetas_periodo(cfg)
    return [p for p in (antes, desde) if p in set(resultados["periodo"])]


def efecto_temporada(t_periodo, pares, periodos):
    """Cada modelo con covariable contra su versión sin ella. Cambio de MAE negativo = la covariable ayuda."""
    filas = []
    for base, con in pares.items():
        for p in periodos:
            a, b = t_periodo.loc[(p, base)], t_periodo.loc[(p, con)]
            filas.append({
                "modelo": base, "periodo": p,
                "MAE sin (M MXN)": a["MAE"] / 1e6, "MAE con (M MXN)": b["MAE"] / 1e6,
                "Cambio de MAE (%)": (b["MAE"] / a["MAE"] - 1) * 100,
                "Sesgo sin (M MXN)": a["Sesgo"] / 1e6, "Sesgo con (M MXN)": b["Sesgo"] / 1e6,
                "WQL sin": a["WQL"], "WQL con": b["WQL"],
            })
    return pd.DataFrame(filas).set_index(["modelo", "periodo"])


def mensual(resultados, nombres, absoluto=True):
    """MAE (absoluto=True) o sesgo mensual en millones de MXN por mes de la fecha pronosticada."""
    err = resultados[COL_PUNTO] - resultados["y"]
    return (resultados.assign(err=err.abs() if absoluto else err)
            .pivot_table(index="mes", columns="modelo", values="err", aggfunc="mean")[nombres] / 1e6)


def vista_mensual(mae_mensual, cfg):
    v = mae_mensual.copy()
    v.insert(0, "Temporada", [temporada_de(m, cfg) for m in v.index])
    v.index = [m.strftime("%b-%y") for m in v.index]
    return v


def resumen(t_reg, t_clf, t_periodo, t_latencia, periodos, nombres, cfg):
    """Tabla resumen ordenada por MAE (sección 8.1)."""
    direccion = t_clf.loc[EVENTO_PRINCIPAL]
    flujo_alto = t_clf.loc["Flujo alto (> mediana histórica)"]
    columnas = {
        "Familia": pd.Series(FAMILIA),
        "MAE (M MXN)": t_reg["MAE"] / 1e6,
        "rMAE vs naive": t_reg["rMAE vs naive"],
        **{f"MAE {p.lower()} (M MXN)": t_periodo.xs(p, level="periodo")["MAE"] / 1e6 for p in periodos},
        "WQL": t_reg["WQL"],
        cfg.etq_cobertura: t_reg[cfg.etq_cobertura],
        "Accuracy": direccion["accuracy"],
        "Precision": direccion["precision"],
        "Recall": direccion["recall"],
        "F1": direccion["F1"],
        "F1 flujo alto": flujo_alto["F1"],
    }
    if t_latencia is not None:
        columnas["Latencia mediana (ms)"] = t_latencia["mediana (ms)"]
        columnas["Carga (s)"] = t_latencia["carga (s)"]
    return pd.DataFrame(columnas).loc[nombres].sort_values("MAE (M MXN)")
