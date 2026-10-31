"""Lectura automática de la corrida (sección 9.1) y exportación de tablas."""
import pandas as pd

from .metricas import NAIVE


def _f(v, patron="{:,.2f}"):
    return "n/d" if pd.isna(v) else patron.format(v)


def lectura_corrida(resumen, t_reg, efecto, periodos, n_eval, n_origenes, n_series, n_dias, cfg, dispositivo="cpu"):
    """Lista de líneas en Markdown con los hallazgos principales; se recalcula con cada corrida."""
    cob = cfg.etq_cobertura
    ideal = (max(cfg.cuantiles) - min(cfg.cuantiles)) * 100
    mejor_mae = resumen["MAE (M MXN)"].idxmin()
    mejor_wql = resumen["WQL"].idxmin()
    mejor_cob = (resumen[cob] - ideal).abs().idxmin()
    mejor_f1 = resumen["F1"].idxmax() if resumen["F1"].notna().any() else None
    superan = [m for m in resumen.index[resumen["rMAE vs naive"] < 1] if m != NAIVE]

    lineas = [
        f"- **Base de evaluación:** {n_eval:,} pronósticos por modelo ({n_origenes} orígenes × {n_series} series × "
        f"{cfg.horizonte} pasos), sobre {n_dias} días hábiles de historia.",
        f"- **Menor error puntual:** {mejor_mae}, MAE de {_f(resumen.loc[mejor_mae, 'MAE (M MXN)'])} M MXN "
        f"(rMAE {_f(resumen.loc[mejor_mae, 'rMAE vs naive'])}).",
        f"- **Mejor pronóstico probabilístico (WQL):** {mejor_wql} (WQL {_f(resumen.loc[mejor_wql, 'WQL'], '{:.3f}')}).",
        f"- **Superan al naive (rMAE < 1):** {', '.join(superan) if superan else 'ningún modelo'}.",
        (f"- **Dirección del flujo:** mejor F1 para {mejor_f1} ({_f(resumen.loc[mejor_f1, 'F1'])}; accuracy "
         f"{_f(resumen.loc[mejor_f1, 'Accuracy'])}, precision {_f(resumen.loc[mejor_f1, 'Precision'])}, "
         f"recall {_f(resumen.loc[mejor_f1, 'Recall'])})." if mejor_f1 else
         "- **Dirección del flujo:** F1 indefinido para todos los modelos."),
        f"- **Calibración del intervalo:** el más cercano al {ideal:.0f} % es {mejor_cob} "
        f"({_f(resumen.loc[mejor_cob, cob], '{:.0f}')} %).",
    ]
    if len(periodos) > 1:
        col = f"MAE {periodos[-1].lower()} (M MXN)"
        mejor_desde = resumen[col].idxmin()
        lineas.insert(2, f"- **{periodos[-1]}:** menor MAE para {mejor_desde} ({_f(resumen.loc[mejor_desde, col])} M MXN).")
    if "Latencia mediana (ms)" in resumen:
        hf = resumen[resumen["Familia"] == "Fundacional HF"]
        est = resumen[resumen["Familia"] == "Estadístico"]
        if len(hf):
            r = hf["Latencia mediana (ms)"].idxmin()
            lineas.append(f"- **Latencia:** el fundacional más rápido es {r} "
                          f"({_f(hf.loc[r, 'Latencia mediana (ms)'], '{:,.1f}')} ms por llamada en {dispositivo}).")
        if len(est):
            lineas.append(f"- **Latencia estadísticos (CPU):** {_f(est['Latencia mediana (ms)'].min(), '{:,.1f}')} a "
                          f"{_f(est['Latencia mediana (ms)'].max(), '{:,.1f}')} ms por llamada; crece linealmente con "
                          "el número de series.")
    for base in efecto.index.get_level_values("modelo").unique():
        partes = [f"{p.lower()}: MAE {efecto.loc[(base, p), 'Cambio de MAE (%)']:+.1f} %, sesgo "
                  f"{efecto.loc[(base, p), 'Sesgo sin (M MXN)']:+.2f} → {efecto.loc[(base, p), 'Sesgo con (M MXN)']:+.2f} M MXN"
                  for p in periodos]
        lineas.append(f"- **Covariable temporada en {base}** (con vs. sin): " + "; ".join(partes) + ".")
    con_respaldo = t_reg.loc[t_reg["Respaldos (%)"] > 0, "Respaldos (%)"]
    if len(con_respaldo):
        lineas.append("- **Respaldos naive:** " + ", ".join(f"{m} {v:.0f} %" for m, v in con_respaldo.items())
                      + " de los pronósticos.")
    return lineas


def exportar_excel(ruta, tablas):
    """Una hoja por tabla. Los índices de periodos mensuales se guardan como texto."""
    with pd.ExcelWriter(ruta, engine="openpyxl") as xls:
        for hoja, (tabla, con_indice) in tablas.items():
            if tabla is None:
                continue
            t = tabla.copy()
            if isinstance(t.index, pd.PeriodIndex):
                t.index = t.index.astype(str)
            for c in t.columns:
                if isinstance(t[c].dtype, pd.PeriodDtype):
                    t[c] = t[c].astype(str)
            t.to_excel(xls, sheet_name=hoja[:31], index=con_indice)
    return ruta
