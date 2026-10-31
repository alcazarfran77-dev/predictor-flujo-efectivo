"""Adaptadores de Chronos-2, Chronos-Bolt Small, ETS, SARIMAX y naive con una sola interfaz (sección 5.1).

Cada modelo recibe la historia en formato largo (unique_id, ds, y) y un horizonte h, y devuelve por serie
y fecha futura el pronóstico puntual (mediana) y los cuantiles P10/P50/P90.
"""
import time
import warnings
from functools import partial

import numpy as np
import pandas as pd
from scipy.stats import norm
from statsmodels.tsa.exponential_smoothing.ets import ETSModel
from statsmodels.tsa.statespace.sarimax import SARIMAX

from .covariables import calendario, covariables_temporada, covariables_utiles, fechas_futuras

COL_PUNTO = "pronostico"

FAMILIA = {
    "Chronos-2": "Fundacional HF", "Chronos-2 + temporada": "Fundacional HF",
    "Chronos-2 (cross-learning)": "Fundacional HF", "Chronos-Bolt Small": "Fundacional HF",
    "ETS": "Estadístico", "SARIMAX": "Estadístico", "SARIMAX + temporada": "Estadístico",
    "Naive (referencia)": "Referencia",
}
MODELO_BASE = {"Chronos-2 (cross-learning)": "Chronos-2", "Chronos-2 + temporada": "Chronos-2"}  # comparten pesos
PARES_TEMPORADA = {"Chronos-2": "Chronos-2 + temporada", "SARIMAX": "SARIMAX + temporada"}     # sin -> con


def columnas_salida(cfg):
    return ["unique_id", "ds", COL_PUNTO, *cfg.cols_q.values(), "respaldo"]


# ---------------------------------------------------------------- Hugging Face
def cargar_pipelines(cfg, dispositivo, dtype, token=None, sincronizar=lambda: None):
    """Carga Chronos-2 y Chronos-Bolt Small; devuelve (pipelines, tabla de modelos, tiempos de carga)."""
    import torch
    from chronos import BaseChronosPipeline

    pipelines, tiempos, filas = {}, {}, []
    for nombre, repo in cfg.repos_hf.items():
        memoria_antes = 0
        if dispositivo == "cuda":
            torch.cuda.empty_cache()
            memoria_antes = torch.cuda.memory_allocated()
        t0 = time.perf_counter()
        pipe = BaseChronosPipeline.from_pretrained(repo, device_map=dispositivo, dtype=dtype, token=token)
        sincronizar()
        tiempos[nombre] = time.perf_counter() - t0
        pipelines[nombre] = pipe
        primer = next(pipe.model.parameters())
        filas.append({
            "modelo": nombre, "repositorio": str(repo), "clase": type(pipe).__name__,
            "parámetros (M)": sum(p.numel() for p in pipe.model.parameters()) / 1e6,
            "contexto máx. (pasos)": pipe.model_context_length,
            "horizonte nativo (pasos)": pipe.model_prediction_length,
            "cuantiles entrenados": len(pipe.quantiles),
            "dispositivo": str(primer.device), "dtype": str(primer.dtype).replace("torch.", ""),
            "carga (s)": tiempos[nombre],
            "VRAM pesos (MB)": ((torch.cuda.memory_allocated() - memoria_antes) / 1024**2
                                if dispositivo == "cuda" else np.nan),
        })
    return pipelines, pd.DataFrame(filas).set_index("modelo"), tiempos


def pronosticar_chronos(pipe, contexto, h, cfg, con_temporada=False, **opciones):
    df = contexto[["unique_id", "ds", "y"]].sort_values(["unique_id", "ds"], kind="stable").reset_index(drop=True)
    if con_temporada:
        fechas_ctx = pd.DatetimeIndex(df["ds"].drop_duplicates().sort_values())
        utiles = covariables_utiles(fechas_ctx, fechas_futuras(fechas_ctx[-1], h, cfg), cfg)
        if utiles:
            df = pd.concat([df, covariables_temporada(df["ds"], cfg)[utiles]], axis=1)
            ultimas = df.groupby("unique_id", sort=False)["ds"].max()
            futuro = pd.concat([pd.DataFrame({"unique_id": uid, "ds": fechas_futuras(ultima, h, cfg)})
                                for uid, ultima in ultimas.items()], ignore_index=True)
            futuro = pd.concat([futuro, covariables_temporada(futuro["ds"], cfg)[utiles]], axis=1)
            opciones["future_df"] = futuro
    pred = pipe.predict_df(
        df, id_column="unique_id", timestamp_column="ds", target="y",
        prediction_length=h, quantile_levels=list(cfg.cuantiles), freq=cfg.frecuencia, **opciones,
    )
    pred = pred.rename(columns={"predictions": COL_PUNTO, **{str(q): c for q, c in cfg.cols_q.items()}})
    pred["ds"] = pd.to_datetime(pred["ds"]).astype("datetime64[ns]")
    pred["respaldo"] = False
    return pred[columnas_salida(cfg)]


# ---------------------------------------------------------------- statsmodels
def _escala(y):
    e = float(np.mean(np.abs(y)))
    return e if e > 0 else 1.0


def naive_una(y, fechas_hist, h, cfg, uid=None):
    dif = np.diff(y)
    sd1 = float(np.std(dif, ddof=1)) if len(dif) >= 2 else (float(np.abs(dif).mean()) if len(dif) else 0.0)
    return np.full(h, y[-1], dtype=float), sd1 * np.sqrt(np.arange(1, h + 1))


def ets_una(y, fechas_hist, h, cfg, uid=None):
    """Error aditivo; tendencia amortiguada con >= 10 días y estacionalidad semanal con >= 12."""
    n, escala = len(y), _escala(y)
    ys = pd.Series(y / escala)
    if np.ptp(ys) == 0:
        return np.full(h, y[-1], dtype=float), np.zeros(h)
    m = cfg.estacionalidad
    estacional, tendencia = n >= 2 * m + 2, n >= 10
    res = ETSModel(ys, error="add", trend="add" if tendencia else None, damped_trend=tendencia,
                   seasonal="add" if estacional else None,
                   seasonal_periods=m if estacional else None).fit(disp=False, maxiter=2000)
    pred = res.get_prediction(start=n, end=n + h - 1)
    return (np.asarray(pred.predicted_mean, float) * escala,
            np.sqrt(np.asarray(pred.var_pred_mean, float)) * escala)


def especificacion_sarimax(n, cfg):
    m = cfg.estacionalidad
    if n >= 3 * m:
        return (1, 0, 1), (1, 0, 0, m)
    if n >= 8:
        return (1, 0, 0), (0, 0, 0, 0)
    return (0, 0, 0), (0, 0, 0, 0)          # historia mínima: media + ruido


class EstadoSarimax:
    """Caché de parámetros para la re-estimación periódica. Solo se activa dentro del backtest."""

    def __init__(self):
        self.cache = None


def sarimax_una(y, fechas_hist, h, cfg, estado, uid=None, con_temporada=False):
    n, escala = len(y), _escala(y)
    ys = pd.Series(y / escala)
    if np.ptp(ys) == 0:
        return np.full(h, y[-1], dtype=float), np.zeros(h)
    orden, orden_est = especificacion_sarimax(n, cfg)
    fechas_sig = fechas_futuras(fechas_hist[-1], h, cfg)
    partes_hist, partes_sig = [], []
    if cfg.usar_exogenas_sarimax and n >= 30:
        partes_hist.append(calendario(fechas_hist))
        partes_sig.append(calendario(fechas_sig))
    if con_temporada:
        utiles = covariables_utiles(fechas_hist, fechas_sig, cfg)
        partes_hist.append(covariables_temporada(fechas_hist, cfg)[utiles])
        partes_sig.append(covariables_temporada(fechas_sig, cfg)[utiles])
    X = X_fut = None
    columnas = ()
    if partes_hist:
        X_hist, X_sig = pd.concat(partes_hist, axis=1), pd.concat(partes_sig, axis=1)
        columnas = tuple(X_hist.columns[X_hist.std() > 0])
        if columnas:
            X, X_fut = X_hist[list(columnas)].to_numpy(), X_sig[list(columnas)].to_numpy()
    modelo = SARIMAX(ys, exog=X, order=orden, seasonal_order=orden_est, trend="c")

    # Backtest: re-estima cada `sarimax_reestimar_cada` orígenes partiendo de los parámetros anteriores;
    # entre re-estimaciones solo filtra con esos parámetros (sin datos posteriores al origen).
    cache = estado.cache if estado is not None else None
    clave = (uid, con_temporada, orden, orden_est, columnas)
    previo = cache.get(clave) if (cache is not None and uid is not None) else None
    if previo is not None and len(previo["params"]) != len(modelo.start_params):
        previo = None
    if previo is not None and previo["usos"] < cfg.sarimax_reestimar_cada - 1:
        res = modelo.filter(previo["params"])
        previo["usos"] += 1
    else:
        res = modelo.fit(disp=False, maxiter=500, start_params=None if previo is None else previo["params"])
        if cache is not None and uid is not None:
            cache[clave] = {"params": np.asarray(res.params), "usos": 0}
    fc = res.get_forecast(h, exog=X_fut)
    return np.asarray(fc.predicted_mean, float) * escala, np.asarray(fc.se_mean, float) * escala


def _es_razonable(mu, sd, y):
    limite = 10 * max(float(np.abs(y).max()), 1.0)
    if not (np.all(np.isfinite(mu)) and np.all(np.isfinite(sd)) and np.all(np.abs(mu) <= limite)):
        return False
    return np.ptp(y) == 0 or bool(np.all(sd >= 1e-3 * np.std(y)))   # descarta intervalos colapsados


def pronostico_estadistico(contexto, h, ajustar_una, cfg, es_naive=False):
    """Ajusta una serie a la vez; si el ajuste falla o diverge usa el naive y lo marca como respaldo."""
    salidas = []
    for uid, g in contexto.sort_values("ds", kind="stable").groupby("unique_id", sort=False):
        y = g["y"].to_numpy(dtype=float)
        fechas_hist = pd.DatetimeIndex(g["ds"])
        respaldo = False
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                mu, sd = ajustar_una(y, fechas_hist, h, uid=uid)
            if not _es_razonable(mu, sd, y):
                raise ValueError("ajuste degenerado")
        except Exception:
            mu, sd = naive_una(y, fechas_hist, h, cfg)
            respaldo = not es_naive
        bloque = pd.DataFrame({"unique_id": uid, "ds": fechas_futuras(fechas_hist[-1], h, cfg), COL_PUNTO: mu})
        for q, col in cfg.cols_q.items():
            bloque[col] = mu + norm.ppf(q) * sd
        bloque["respaldo"] = respaldo
        salidas.append(bloque)
    return pd.concat(salidas, ignore_index=True)[columnas_salida(cfg)]


# ---------------------------------------------------------------- registro
class Registro:
    """Modelos con la firma (contexto, h) -> DataFrame, en el orden del cuaderno."""

    def __init__(self, cfg, pipelines=None):
        self.cfg = cfg
        self.estado_sarimax = EstadoSarimax()
        self.funciones = {}
        if pipelines:
            c2 = pipelines.get("Chronos-2")
            bolt = pipelines.get("Chronos-Bolt Small")
            if c2 is not None:
                self.funciones["Chronos-2"] = partial(pronosticar_chronos, c2, cfg=cfg)
                self.funciones["Chronos-2 + temporada"] = partial(pronosticar_chronos, c2, cfg=cfg, con_temporada=True)
                self.funciones["Chronos-2 (cross-learning)"] = partial(pronosticar_chronos, c2, cfg=cfg,
                                                                       cross_learning=True)
            if bolt is not None:
                self.funciones["Chronos-Bolt Small"] = partial(pronosticar_chronos, bolt, cfg=cfg)
        est = self.estado_sarimax
        self.funciones["ETS"] = partial(pronostico_estadistico, ajustar_una=partial(ets_una, cfg=cfg), cfg=cfg)
        self.funciones["SARIMAX"] = partial(
            pronostico_estadistico, ajustar_una=partial(sarimax_una, cfg=cfg, estado=est), cfg=cfg)
        self.funciones["SARIMAX + temporada"] = partial(
            pronostico_estadistico, ajustar_una=partial(sarimax_una, cfg=cfg, estado=est, con_temporada=True), cfg=cfg)
        self.funciones["Naive (referencia)"] = partial(
            pronostico_estadistico, ajustar_una=partial(naive_una, cfg=cfg), cfg=cfg, es_naive=True)

    def __iter__(self):
        return iter(self.funciones)

    def __len__(self):
        return len(self.funciones)

    def items(self):
        return self.funciones.items()

    @property
    def nombres(self):
        return list(self.funciones)

    @property
    def pares_temporada(self):
        return {a: b for a, b in PARES_TEMPORADA.items() if a in self.funciones and b in self.funciones}
