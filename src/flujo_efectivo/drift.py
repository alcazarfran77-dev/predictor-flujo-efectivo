"""Detección de data drift: ¿los datos recientes se parecen a los que el modelo ya vio?

Compara una ventana reciente contra una de referencia inmediatamente anterior, en tres frentes:

- Series diarias (lo que entra al modelo): PSI, prueba de Kolmogorov-Smirnov y cambio de la media.
- Composición de los movimientos: número de movimientos por día y reparto del importe por banco y por categoría.
- Esquema y calidad: los cubre `validacion.py` (campos, duplicados, huecos, monedas).

Con ventanas de 20 días el PSI solo es ruidoso: en la historia del proyecto supera 0.25 en una de cada tres ventanas
sin que la distribución haya cambiado. Por eso el nivel "significativo" exige dos señales a la vez (PSI alto y
prueba KS significativa) y el PSI solo deja el nivel en "moderado".
"""
import numpy as np
import pandas as pd
from scipy import stats

VENTANA_RECIENTE = 20          # días hábiles (cuatro semanas)
VENTANA_REFERENCIA = 120       # días hábiles anteriores a la ventana reciente
BINS = 5
PSI_MODERADO, PSI_ALTO = 0.10, 0.25
ALFA_KS = 0.05
CAMBIO_MEDIA = 0.5             # en desviaciones estándar de la referencia

SIN_CAMBIO, MODERADO, SIGNIFICATIVO, SIN_DATOS = "sin cambio", "moderado", "significativo", "sin datos"
ORDEN = {SIN_DATOS: 0, SIN_CAMBIO: 0, MODERADO: 1, SIGNIFICATIVO: 2}


def psi(referencia, reciente, bins=BINS):
    """Population Stability Index con cortes en los cuantiles de la referencia."""
    referencia, reciente = np.asarray(referencia, float), np.asarray(reciente, float)
    cortes = np.unique(np.quantile(referencia, np.linspace(0, 1, bins + 1)[1:-1]))
    n = len(cortes) + 1
    r = np.bincount(np.searchsorted(cortes, referencia), minlength=n) / len(referencia)
    a = np.bincount(np.searchsorted(cortes, reciente), minlength=n) / len(reciente)
    r, a = np.clip(r, 1e-3, None), np.clip(a, 1e-3, None)
    return float(((a - r) * np.log(a / r)).sum())


def psi_categorias(referencia, reciente):
    """PSI entre dos repartos por categoría (Series con el peso de cada categoría)."""
    cats = sorted(set(referencia.index) | set(reciente.index))
    r = referencia.reindex(cats, fill_value=0).to_numpy(float)
    a = reciente.reindex(cats, fill_value=0).to_numpy(float)
    if r.sum() <= 0 or a.sum() <= 0:
        return None
    r, a = np.clip(r / r.sum(), 1e-3, None), np.clip(a / a.sum(), 1e-3, None)
    return float(((a - r) * np.log(a / r)).sum())


def nivel_drift(valor_psi, p_ks, cambio_media):
    if valor_psi >= PSI_ALTO and p_ks < ALFA_KS:
        return SIGNIFICATIVO
    if p_ks < ALFA_KS or abs(cambio_media) >= CAMBIO_MEDIA or (valor_psi >= PSI_ALTO and abs(cambio_media) >= CAMBIO_MEDIA / 2):
        return MODERADO
    return SIN_CAMBIO


def comparar_ventanas(referencia, reciente):
    referencia, reciente = np.asarray(referencia, float), np.asarray(reciente, float)
    sd = referencia.std(ddof=1)
    cambio = float((reciente.mean() - referencia.mean()) / sd) if sd > 0 else 0.0
    valor_psi = psi(referencia, reciente)
    ks = stats.ks_2samp(referencia, reciente)
    return {"psi": valor_psi, "ks": float(ks.statistic), "p_ks": float(ks.pvalue), "cambio_media_sd": cambio,
            "media_referencia": float(referencia.mean()), "media_reciente": float(reciente.mean()),
            "razon_dispersion": float(reciente.std(ddof=1) / sd) if sd > 0 else None,
            "nivel": nivel_drift(valor_psi, ks.pvalue, cambio)}


def drift_series(series, reciente=VENTANA_RECIENTE, referencia=VENTANA_REFERENCIA):
    """Drift de cada serie diaria. `series` trae unique_id, ds, y."""
    filas = []
    for uid, g in series.groupby("unique_id", sort=False):
        g = g.sort_values("ds")
        y = g["y"].to_numpy(float)
        fila = {"serie": uid, "dias_reciente": reciente, "dias_referencia": referencia}
        if len(y) < reciente + max(40, referencia // 2):
            filas.append({**fila, "nivel": SIN_DATOS})
            continue
        ref = y[-(reciente + referencia):-reciente]
        fila.update(comparar_ventanas(ref, y[-reciente:]))
        fila.update(dias_referencia=len(ref), desde=g["ds"].iloc[-reciente].strftime("%Y-%m-%d"),
                    hasta=g["ds"].iloc[-1].strftime("%Y-%m-%d"))
        filas.append(fila)
    return filas


def historia_drift(series, uid, paso=5, reciente=VENTANA_RECIENTE, referencia=VENTANA_REFERENCIA):
    """El mismo cálculo en ventanas pasadas de una serie. Sirve para graficar y para calibrar los umbrales."""
    g = series[series["unique_id"] == uid].sort_values("ds")
    y, fechas = g["y"].to_numpy(float), g["ds"].to_numpy()
    filas = []
    for fin in range(reciente + referencia, len(y) + 1, paso):
        r = comparar_ventanas(y[fin - reciente - referencia:fin - reciente], y[fin - reciente:fin])
        filas.append({"fecha": pd.Timestamp(fechas[fin - 1]).strftime("%Y-%m-%d"), **r})
    return filas


def drift_composicion(mov, reciente=VENTANA_RECIENTE, referencia=VENTANA_REFERENCIA):
    """Drift de la composición de los movimientos: volumen diario y reparto del importe por banco y categoría."""
    if mov is None or len(mov) == 0:
        return []
    m = mov.copy()
    m["dia"] = pd.to_datetime(m["fecha_serie"] if "fecha_serie" in m else m["fecha"]).dt.normalize()
    dias = np.sort(m["dia"].unique())
    if len(dias) < reciente + 40:
        return []
    corte, inicio = dias[-reciente], dias[max(0, len(dias) - reciente - referencia)]
    act, ref = m[m["dia"] >= corte], m[(m["dia"] >= inicio) & (m["dia"] < corte)]
    filas = []
    por_dia_ref = ref.groupby("dia").size().reindex(dias[(dias >= inicio) & (dias < corte)], fill_value=0)
    por_dia_act = act.groupby("dia").size().reindex(dias[dias >= corte], fill_value=0)
    c = comparar_ventanas(por_dia_ref.to_numpy(), por_dia_act.to_numpy())
    filas.append({"indicador": "Movimientos por día", "tipo": "volumen", "psi": c["psi"], "p_ks": c["p_ks"],
                  "referencia": c["media_referencia"], "reciente": c["media_reciente"], "nivel": c["nivel"]})
    for columna, nombre in (("banco", "Reparto del importe por banco"), ("categoria", "Reparto del importe por categoría")):
        if columna not in m:
            continue
        r = ref.assign(a=ref["monto"].abs()).groupby(columna)["a"].sum()
        a = act.assign(a=act["monto"].abs()).groupby(columna)["a"].sum()
        valor = psi_categorias(r, a)
        if valor is None:
            continue
        nuevas = sorted(set(a.index) - set(r.index))
        nivel = SIGNIFICATIVO if valor >= PSI_ALTO or nuevas else MODERADO if valor >= PSI_MODERADO else SIN_CAMBIO
        filas.append({"indicador": nombre, "tipo": "composición", "psi": valor, "p_ks": None, "referencia": None,
                      "reciente": None, "nivel": nivel, "categorias_nuevas": nuevas})
    return filas


def resumen(series, mov=None):
    """Drift de series y de composición con el nivel más alto de todos."""
    s, c = drift_series(series), drift_composicion(mov)
    nivel = max([f["nivel"] for f in s + c] or [SIN_DATOS], key=lambda n: ORDEN[n])
    return {"nivel": nivel, "series": s, "composicion": c,
            "criterio": {"psi_moderado": PSI_MODERADO, "psi_alto": PSI_ALTO, "alfa_ks": ALFA_KS,
                         "cambio_media_sd": CAMBIO_MEDIA, "dias_reciente": VENTANA_RECIENTE,
                         "dias_referencia": VENTANA_REFERENCIA}}
