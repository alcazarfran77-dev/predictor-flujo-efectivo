"""Backtest walk-forward y pronóstico hacia adelante (secciones 5.2 y 5.3)."""
import time

import numpy as np
import pandas as pd

from .covariables import fechas_futuras
from .modelos import COL_PUNTO


def etiquetas_periodo(cfg):
    corte = pd.Timestamp(cfg.fecha_corte)
    return corte, f"Antes del {corte:%d-%m-%Y}", f"Desde el {corte:%d-%m-%Y}"


def origenes(series, cfg):
    fechas_serie = pd.DatetimeIndex(np.sort(series["ds"].unique()))
    cortes = list(range(cfg.min_contexto, len(fechas_serie) - cfg.horizonte + 1))[-cfg.max_origenes:]
    if not cortes:
        raise ValueError(f"Con {len(fechas_serie)} días no hay orígenes con min_contexto={cfg.min_contexto} "
                         f"y horizonte={cfg.horizonte}.")
    return fechas_serie, cortes


def backtest(series, registro, cfg, sincronizar=lambda: None, avance=print):
    """Para cada origen usa solo la historia hasta ese día y pronostica los siguientes `horizonte` días hábiles.

    La primera llamada de cada modelo se mide aparte (latencia en frío) y no entra en las estadísticas.
    """
    fechas_serie, cortes = origenes(series, cfg)
    corte, etq_antes, etq_desde = etiquetas_periodo(cfg)
    h = cfg.horizonte

    registros, latencias, latencia_fria, duracion = [], [], {}, {}
    registro.estado_sarimax.cache = {}
    try:
        for nombre, pronosticar in registro.items():
            t_modelo = time.perf_counter()
            sincronizar(); t0 = time.perf_counter()
            pronosticar(series[series["ds"] < fechas_serie[cortes[0]]], h)
            sincronizar(); latencia_fria[nombre] = time.perf_counter() - t0

            for i, k in enumerate(cortes, start=1):
                if i % 50 == 0:
                    avance(f"  {nombre:28s} origen {i}/{len(cortes)}")
                contexto = series[series["ds"] < fechas_serie[k]]
                objetivo = series[series["ds"].isin(fechas_serie[k:k + h])]

                sincronizar(); t0 = time.perf_counter()
                pred = pronosticar(contexto, h)
                sincronizar(); dt = time.perf_counter() - t0

                referencia = (contexto.sort_values("ds", kind="stable").groupby("unique_id")
                              .agg(ultimo_obs=("y", "last"), mediana_ctx=("y", "median")))
                pred = (pred.merge(objetivo, on=["unique_id", "ds"], how="left")
                            .merge(referencia, left_on="unique_id", right_index=True))
                pred["h"] = pred.groupby("unique_id").cumcount() + 1
                pred["origen"] = fechas_serie[k - 1]
                pred["modelo"] = nombre
                registros.append(pred)
                latencias.append({"modelo": nombre, "origen": fechas_serie[k - 1], "segundos": dt})
            duracion[nombre] = time.perf_counter() - t_modelo
            avance(f"  {nombre:28s} {duracion[nombre]:7.1f} s   (1.ª llamada en frío: "
                   f"{latencia_fria[nombre] * 1000:,.0f} ms)")
    finally:
        registro.estado_sarimax.cache = None

    resultados = pd.concat(registros, ignore_index=True)
    if resultados["y"].isna().any():
        raise AssertionError("Hay pronósticos sin valor real: revisa la frecuencia y las fechas.")
    resultados["periodo"] = np.where(resultados["ds"] < corte, etq_antes, etq_desde)
    resultados["mes"] = resultados["ds"].dt.to_period("M")
    return resultados, pd.DataFrame(latencias), latencia_fria, duracion


def pronostico_adelante(series, registro, cfg):
    """Con toda la historia, cada modelo pronostica `horizonte_final` días hábiles hacia adelante."""
    return pd.concat({n: fn(series, cfg.horizonte_final) for n, fn in registro.items()},
                     names=["modelo"]).reset_index(level=0)


def tabla_pronostico(pronostico_final, registro, cfg, ids):
    """Mediana [P10 – P90] en millones de MXN por serie, fecha y modelo."""
    q_lo, q_hi = cfg.q_lo, cfg.q_hi
    vista = pronostico_final.assign(
        fecha=lambda d: d["ds"].dt.strftime("%d-%m-%Y"),
        valor=lambda d: (d[COL_PUNTO] / 1e6).map("{:,.2f}".format) + "  [" + (d[q_lo] / 1e6).map("{:,.2f}".format)
                        + " – " + (d[q_hi] / 1e6).map("{:,.2f}".format) + "]",
    )
    return (vista.pivot(index=["unique_id", "fecha"], columns="modelo", values="valor")
            .reindex(columns=registro.nombres).reindex(ids, level="unique_id").rename_axis(columns=None))


def siguientes_fechas(fechas, cfg):
    return fechas_futuras(fechas[-1], cfg.horizonte_final, cfg)
