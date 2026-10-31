"""Covariable `temporada` y regresores de calendario."""
import numpy as np
import pandas as pd

COVARIABLES_TEMPORADA = ["temp_ing_alto", "temp_gasto_alto"]


def fechas_futuras(ultima_fecha, h, cfg):
    return pd.date_range(ultima_fecha, periods=h + 1, freq=cfg.frecuencia)[1:].astype("datetime64[ns]")


def covariables_temporada(fechas_idx, cfg):
    """Indicadores 0/1 de temporada (float: Chronos-2 trata los booleanos como categorías)."""
    f = pd.DatetimeIndex(fechas_idx)
    return pd.DataFrame({
        "temp_ing_alto": f.month.isin(cfg.meses_ingreso_alto).astype(float),
        "temp_gasto_alto": f.month.isin(cfg.meses_gasto_alto).astype(float),
    })


def etiquetas_temporada(fechas_idx, cfg):
    f = pd.DatetimeIndex(fechas_idx)
    return np.where(f.month.isin(cfg.meses_ingreso_alto), "ingreso alto",
                    np.where(f.month.isin(cfg.meses_gasto_alto), "gasto alto", "normal"))


def temporada_de(mes, cfg):
    return ("Mayor ingreso" if mes.month in cfg.meses_ingreso_alto else
            "Mayor gasto" if mes.month in cfg.meses_gasto_alto else "Normal")


def covariables_utiles(fechas_hist, fechas_sig, cfg):
    """Indicadores de temporada que se pueden usar en este origen (lista vacía = modelo sin covariable).

    - La historia debe tener al menos dos temporadas con `min_dias_temporada` días hábiles o más cada una.
    - Todas las temporadas del horizonte deben estar ya en la historia: no se extrapola a una temporada nunca vista.
    - Se omite el indicador de la temporada base ("normal" si ya se observó) para no duplicar la constante.
    """
    conteo = pd.Series(etiquetas_temporada(fechas_hist, cfg)).value_counts()
    vistas = set(conteo[conteo >= cfg.min_dias_temporada].index)
    if len(vistas) < 2 or not set(etiquetas_temporada(fechas_sig, cfg)) <= vistas:
        return []
    base = "normal" if "normal" in vistas else "gasto alto"
    return [col for etiqueta, col in (("ingreso alto", "temp_ing_alto"), ("gasto alto", "temp_gasto_alto"))
            if etiqueta in vistas and etiqueta != base]


def calendario(fechas_idx):
    """Regresores exógenos de calendario para SARIMAX (viernes = categoría base)."""
    f = pd.DatetimeIndex(fechas_idx)
    X = pd.DataFrame(index=range(len(f)))
    for d, nombre in enumerate(["lun", "mar", "mie", "jue"]):
        X[nombre] = (f.dayofweek == d).astype(float)
    X["quincena"] = ((f.day >= 13) & (f.day <= 16)).astype(float)
    X["fin_mes"] = ((f + pd.offsets.BDay(2)).month != f.month).astype(float)
    return X
