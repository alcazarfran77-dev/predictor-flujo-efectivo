"""Latencia por llamada y escalabilidad con el número de series (sección 7)."""
import time

import numpy as np
import pandas as pd

from .modelos import FAMILIA, MODELO_BASE


def _memoria_gpu(dispositivo, reiniciar=False):
    if dispositivo != "cuda":
        return 0
    import torch
    if reiniciar:
        torch.cuda.reset_peak_memory_stats()
        return torch.cuda.memory_allocated()
    return torch.cuda.max_memory_allocated()


def medir_latencia(series, registro, cfg, dispositivo="cpu", sincronizar=lambda: None,
                   tiempos_carga=None, latencia_fria=None, latencias_backtest=None):
    """Pronostica todas las series con toda la historia hasta `n_repeticiones_latencia` veces.

    Un modelo que acumula más de 30 s se detiene a las 5 repeticiones. Se reportan mediana y p95.
    """
    tiempos_carga = tiempos_carga or {}
    latencia_fria = latencia_fria or {}
    n_series = series["unique_id"].nunique()
    filas = []
    for nombre, pronosticar in registro.items():
        es_hf = FAMILIA[nombre] == "Fundacional HF"
        memoria_base = _memoria_gpu(dispositivo, reiniciar=True)
        tiempos = []
        for _ in range(cfg.n_repeticiones_latencia):
            sincronizar(); t0 = time.perf_counter()
            pronosticar(series, cfg.horizonte)
            sincronizar(); tiempos.append((time.perf_counter() - t0) * 1000)
            if len(tiempos) >= 5 and sum(tiempos) > 30_000:
                break
        t = np.array(tiempos)
        fila = {
            "modelo": nombre,
            "dispositivo": dispositivo if es_hf else "cpu",
            "repeticiones": len(t),
            "carga (s)": tiempos_carga.get(MODELO_BASE.get(nombre, nombre), 0.0),
            "1.ª llamada en frío (ms)": latencia_fria.get(nombre, np.nan) * 1000,
            "mediana (ms)": np.median(t),
            "p95 (ms)": np.percentile(t, 95),
            "desv. est. (ms)": t.std(ddof=1),
            "ms por serie": np.median(t) / n_series,
            "series por segundo": n_series / (np.median(t) / 1000),
            "mediana en backtest (ms)*": np.nan,
            "VRAM pico inferencia (MB)": ((_memoria_gpu(dispositivo) - memoria_base) / 1024**2
                                          if dispositivo == "cuda" and es_hf else np.nan),
        }
        if latencias_backtest is not None and len(latencias_backtest):
            sel = latencias_backtest["modelo"] == nombre
            fila["mediana en backtest (ms)*"] = latencias_backtest.loc[sel, "segundos"].median() * 1000
        filas.append(fila)
    return pd.DataFrame(filas).set_index("modelo")


def replicar_series(df, n):
    base = list(df["unique_id"].unique())
    return pd.concat([df[df["unique_id"] == base[i % len(base)]].assign(unique_id=f"{base[i % len(base)]}#{i}")
                      for i in range(n)], ignore_index=True)


def escalabilidad(series, registro, cfg, sincronizar=lambda: None):
    """Latencia mediana por llamada al replicar las series. No mide un tamaño cuya proyección lineal
    desde el anterior supere `limite_escala_s` segundos."""
    filas, ultimo = [], {}
    for n in cfg.tamanos_lote:
        lote = replicar_series(series, n)
        for nombre, pronosticar in registro.items():
            if nombre in ultimo:
                n_prev, ms_prev = ultimo[nombre]
                if ms_prev / 1000 * n / n_prev > cfg.limite_escala_s:
                    filas.append({"series en el lote": n, "modelo": nombre, "mediana (ms)": np.nan})
                    continue
            tiempos = []
            for _ in range(3):
                sincronizar(); t0 = time.perf_counter()
                pronosticar(lote, cfg.horizonte)
                sincronizar(); tiempos.append((time.perf_counter() - t0) * 1000)
                if tiempos[-1] > 5_000:
                    break
            filas.append({"series en el lote": n, "modelo": nombre, "mediana (ms)": np.median(tiempos)})
            ultimo[nombre] = (n, np.median(tiempos))
    return (pd.DataFrame(filas)
            .pivot(index="series en el lote", columns="modelo", values="mediana (ms)")[registro.nombres])
