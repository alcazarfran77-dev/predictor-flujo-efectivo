"""Métricas de confianza del pronóstico, calculadas sobre el backtest walk-forward.

Los umbrales son los de la sección 7 del reporte técnico: los de aceptación califican al modelo y los de
monitoreo avisan cuándo revisarlo o recalibrar el intervalo.
"""
import numpy as np
import pandas as pd

NAIVE = "Naive (referencia)"

# (clave, nombre, mínimo, máximo, unidad)
UMBRALES_ACEPTACION = [
    ("rmae", "rMAE frente al ingenuo", None, 0.80, ""),
    ("wql", "WQL", None, 0.65, ""),
    ("cobertura", "Cobertura P10–P90", 70.0, 90.0, "%"),
    ("f1_direccion", "F1 del evento dirección", 0.65, None, ""),
    ("sesgo_cambio", "Sesgo medio en las 4 semanas tras un cambio de temporada", -0.3, 0.3, "M MXN/día"),
    ("latencia", "Latencia de la corrida diaria (todas las series)", None, 5.0, "s"),
]
VENTANA_RMAE, LIMITE_RMAE, DIAS_SEGUIDOS_RMAE = 20, 0.90, 10
VENTANA_COBERTURA, COBERTURA_MIN, COBERTURA_MAX = 40, 65.0, 95.0
DIAS_CAMBIO = 20


def _f(v):
    """float JSON-seguro (NaN e infinito pasan a None)."""
    if v is None:
        return None
    v = float(v)
    return v if np.isfinite(v) else None


def metricas_basicas(d, mae_naive=None):
    """MAE, rMAE, sesgo, WQL, cobertura y F1 de dirección de un subconjunto del backtest."""
    if d.empty:
        return None
    error = d["pronostico"] - d["y"]
    pinball = sum(np.maximum(q * (d["y"] - d[c]), (q - 1) * (d["y"] - d[c])).sum()
                  for q, c in ((0.1, "P10"), (0.5, "P50"), (0.9, "P90")))
    real, pred = d["y"] > d["ultimo_obs"], d["pronostico"] > d["ultimo_obs"]
    vp, fp, fn = int((real & pred).sum()), int((~real & pred).sum()), int((real & ~pred).sum())
    f1 = 2 * vp / (2 * vp + fp + fn) if (2 * vp + fp + fn) else None
    mae = float(error.abs().mean())
    return {
        "mae": mae,
        "rmae": _f(mae / mae_naive) if mae_naive else None,
        "sesgo": float(error.mean()),
        "wql": _f(2 * pinball / 3 / d["y"].abs().sum()),
        "cobertura": float(((d["y"] >= d["P10"]) & (d["y"] <= d["P90"])).mean() * 100),
        "f1_direccion": _f(f1),
        "n": int(len(d)),
    }


def _mae(d):
    return float((d["pronostico"] - d["y"]).abs().mean()) if len(d) else np.nan


def por_horizonte(bt_modelo, bt_naive):
    filas = []
    for h, d in bt_modelo.groupby("h"):
        m = metricas_basicas(d, _mae(bt_naive[bt_naive["h"] == h]))
        filas.append({"h": int(h), **m})
    return filas


def sesgo_tras_cambio(bt_modelo, cfg):
    """Sesgo medio (M MXN/día) en los 20 días hábiles que siguen al último cambio de temporada del backtest."""
    fechas = pd.DatetimeIndex(sorted(bt_modelo["ds"].unique()))
    if len(fechas) < 2:
        return None, None
    mes = fechas.month
    temporada = np.where(np.isin(mes, cfg.meses_ingreso_alto), 1, np.where(np.isin(mes, cfg.meses_gasto_alto), 2, 0))
    cambios = fechas[1:][temporada[1:] != temporada[:-1]]
    if not len(cambios):
        return None, None
    inicio = cambios[-1]
    ventana = fechas[(fechas >= inicio)][:DIAS_CAMBIO]
    d = bt_modelo[bt_modelo["ds"].isin(ventana)]
    return _f((d["pronostico"] - d["y"]).mean() / 1e6), inicio.strftime("%Y-%m-%d")


def evaluar_umbrales(valores):
    filas = []
    for clave, nombre, minimo, maximo, unidad in UMBRALES_ACEPTACION:
        v = valores.get(clave)
        cumple = None if v is None else ((minimo is None or v >= minimo) and (maximo is None or v <= maximo))
        if minimo is not None and maximo is not None:
            condicion = f"entre {minimo:g} y {maximo:g}"
        elif maximo is not None:
            condicion = f"<= {maximo:g}"
        else:
            condicion = f">= {minimo:g}"
        filas.append({"clave": clave, "nombre": nombre, "valor": _f(v), "condicion": condicion,
                      "unidad": unidad, "cumple": cumple})
    return filas


def monitoreo(bt_modelo, bt_naive, duplicados):
    """rMAE móvil de 20 días y cobertura móvil de 40 días por fecha pronosticada, con sus disparadores."""
    err = bt_modelo.assign(e=(bt_modelo["pronostico"] - bt_modelo["y"]).abs(),
                           dentro=((bt_modelo["y"] >= bt_modelo["P10"]) & (bt_modelo["y"] <= bt_modelo["P90"])))
    por_dia = err.groupby("ds").agg(e=("e", "sum"), dentro=("dentro", "sum"), n=("e", "size"))
    naive = bt_naive.assign(e=(bt_naive["pronostico"] - bt_naive["y"]).abs()).groupby("ds")["e"].sum()
    por_dia["e_naive"] = naive.reindex(por_dia.index)
    rmae = (por_dia["e"].rolling(VENTANA_RMAE).sum() / por_dia["e_naive"].rolling(VENTANA_RMAE).sum())
    cobertura = por_dia["dentro"].rolling(VENTANA_COBERTURA).sum() / por_dia["n"].rolling(VENTANA_COBERTURA).sum() * 100

    ultimos = rmae.dropna().tail(DIAS_SEGUIDOS_RMAE)
    alerta_rmae = bool(len(ultimos) == DIAS_SEGUIDOS_RMAE and (ultimos > LIMITE_RMAE).all())
    cob_actual = cobertura.dropna().iloc[-1] if cobertura.notna().any() else np.nan
    alerta_cob = bool(np.isfinite(cob_actual) and not (COBERTURA_MIN <= cob_actual <= COBERTURA_MAX))

    def serie(s, dias=120):
        s = s.dropna().tail(dias)
        return [{"fecha": f.strftime("%Y-%m-%d"), "valor": float(v)} for f, v in s.items()]

    return {
        "rmae_movil": {
            "valor": _f(rmae.dropna().iloc[-1]) if rmae.notna().any() else None,
            "ventana_dias": VENTANA_RMAE, "limite": LIMITE_RMAE, "dias_seguidos": DIAS_SEGUIDOS_RMAE,
            "alerta": alerta_rmae, "accion": "Revisar el modelo y las reglas de clasificación",
            "serie": serie(rmae),
        },
        "cobertura_movil": {
            "valor": _f(cob_actual), "ventana_dias": VENTANA_COBERTURA,
            "rango": [COBERTURA_MIN, COBERTURA_MAX], "alerta": alerta_cob,
            "accion": "Recalibrar el intervalo", "serie": serie(cobertura),
        },
        "duplicados": {
            "valor": int(duplicados), "alerta": duplicados > 0,
            "accion": "Revisar el export antes de pronosticar",
        },
    }


def nivel_confianza(umbrales, mon):
    """alta: cumple todo y sin alarmas; media: una falla o una alarma; baja: más de una."""
    fallas = sum(1 for u in umbrales if u["cumple"] is False)
    alarmas = sum(1 for k in ("rmae_movil", "cobertura_movil", "duplicados") if mon[k]["alerta"])
    total = fallas + alarmas
    nivel = "alta" if total == 0 else "media" if total == 1 else "baja"
    return nivel, fallas, alarmas
