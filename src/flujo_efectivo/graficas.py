"""Gráficas del cuaderno, guardadas como PNG (secciones 4.5, 7.1 y 8)."""
import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates          # noqa: E402
import matplotlib.pyplot as plt            # noqa: E402
import matplotlib.ticker as mticker        # noqa: E402
import numpy as np                         # noqa: E402
import pandas as pd                        # noqa: E402
from matplotlib.patches import Patch       # noqa: E402

from .modelos import COL_PUNTO             # noqa: E402

plt.rcParams.update({
    "figure.dpi": 110, "savefig.dpi": 130, "font.size": 9.5,
    "axes.spines.top": False, "axes.spines.right": False, "axes.edgecolor": "#b5b4ae",
    "axes.grid": True, "axes.grid.axis": "y", "grid.color": "#e5e4e0", "grid.linewidth": 0.8,
    "axes.axisbelow": True, "axes.titlesize": 10.5, "axes.titleweight": "bold",
    "axes.labelcolor": "#52514e", "xtick.color": "#52514e", "ytick.color": "#52514e",
    "legend.frameon": False,
})
COLOR_REAL, COLOR_NEUTRO = "#0b0b0b", "#b5b4ae"
COLOR_POSITIVO, COLOR_NEGATIVO = "#2a78d6", "#e34948"
COLORES_MODELO = {
    "Chronos-2": "#2a78d6", "Chronos-2 + temporada": "#4a3aa7", "Chronos-2 (cross-learning)": "#eb6834",
    "Chronos-Bolt Small": "#1baf7a", "ETS": "#eda100", "SARIMAX": "#e87ba4", "SARIMAX + temporada": "#e34948",
    "Naive (referencia)": "#008300",
}
MARCADORES = {"Chronos-2": "o", "Chronos-2 + temporada": "P", "Chronos-2 (cross-learning)": "s",
              "Chronos-Bolt Small": "D", "ETS": "^", "SARIMAX": "v", "SARIMAX + temporada": "*",
              "Naive (referencia)": "X"}
EN_MILLONES = mticker.FuncFormatter(lambda v, _: f"{v / 1e6:,.1f}")


def _guardar(fig, ruta):
    fig.savefig(ruta, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return ruta


def _rejilla(n, alto):
    nrows = int(np.ceil(n / 2))
    return plt.subplots(nrows, 2, figsize=(12, alto * nrows), squeeze=False)


def series_diarias(pivote, ruta):
    ids, fechas = list(pivote.columns), pivote.index
    fig, axes = _rejilla(len(ids), 2.9)
    pos = np.arange(len(fechas))
    paso = max(1, len(fechas) // 8)
    for ax, uid in zip(axes.flat, ids):
        v = pivote[uid].to_numpy()
        ax.bar(pos, v, width=0.7, color=np.where(v >= 0, COLOR_POSITIVO, COLOR_NEGATIVO))
        ax.axhline(0, color=COLOR_NEUTRO, lw=1)
        ax.set_title(uid, loc="left")
        ax.yaxis.set_major_formatter(EN_MILLONES)
        ax.set_xticks(pos[::paso], [f"{f:%d-%b-%y}" for f in fechas[::paso]])
    for ax in axes.flat[len(ids):]:
        ax.set_visible(False)
    fig.supylabel("Flujo neto diario (millones MXN)", color="#52514e")
    fig.legend(handles=[Patch(color=COLOR_POSITIVO, label="Entrada neta"),
                        Patch(color=COLOR_NEGATIVO, label="Salida neta")], loc="upper right", ncol=2)
    fig.suptitle("Series diarias a pronosticar", x=0.01, ha="left", fontweight="bold")
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    return _guardar(fig, ruta)


def backtest_corte(series, resultados, modelos, corte, ruta):
    """Real contra pronóstico a 1 día desde seis semanas antes del corte."""
    ids = list(series["unique_id"].unique())
    fechas_serie = pd.DatetimeIndex(np.sort(series["ds"].unique()))
    hay_corte = fechas_serie[0] < corte <= fechas_serie[-1]
    desde = corte - pd.offsets.BDay(30) if hay_corte else fechas_serie[-60]
    r1 = resultados[resultados["h"] == 1]
    fig, axes = _rejilla(len(ids), 3.4)
    for ax, uid in zip(axes.flat, ids):
        hist = series[(series["unique_id"] == uid) & (series["ds"] >= desde)]
        ax.axhline(0, color=COLOR_NEUTRO, lw=0.8)
        if hay_corte:
            ax.axvline(corte, color=COLOR_REAL, lw=1, ls="--", zorder=1)
        ax.plot(hist["ds"], hist["y"], color=COLOR_REAL, lw=2.2, marker="o", ms=3.5, label="Real", zorder=5)
        for nombre in modelos:
            d = r1[(r1["unique_id"] == uid) & (r1["modelo"] == nombre) & (r1["ds"] >= desde)]
            ax.plot(d["ds"], d[COL_PUNTO], color=COLORES_MODELO[nombre], marker=MARCADORES[nombre],
                    lw=1.6, ms=5.5, markeredgecolor="white", markeredgewidth=0.6, label=nombre, zorder=4)
        ax.set_title(uid, loc="left")
        ax.yaxis.set_major_formatter(EN_MILLONES)
        ax.xaxis.set_major_locator(mdates.AutoDateLocator(maxticks=8))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d-%b"))
    for ax in axes.flat[len(ids):]:
        ax.set_visible(False)
    fig.supylabel("Flujo neto diario (millones MXN)", color="#52514e")
    h, l = axes.flat[0].get_legend_handles_labels()
    fig.legend(h, l, loc="lower center", ncol=5, bbox_to_anchor=(0.5, 0.0))
    fig.suptitle(f"Backtest walk-forward: real vs. pronóstico a 1 día (línea punteada = {corte:%d-%m-%Y})",
                 x=0.01, ha="left", fontweight="bold")
    fig.tight_layout(rect=(0, 0.06, 1, 0.96))
    return _guardar(fig, ruta)


def sesgo_mensual(sesgo, modelos, cfg, ruta):
    inicio = sesgo.index.to_timestamp()
    x = inicio + pd.Timedelta(days=14)
    fig, ax = plt.subplots(figsize=(11, 4.4))
    for m in inicio:
        if m.month in cfg.meses_ingreso_alto or m.month in cfg.meses_gasto_alto:
            ax.axvspan(m, m + pd.offsets.MonthBegin(1), zorder=0, lw=0,
                       color="#e6f0fb" if m.month in cfg.meses_ingreso_alto else "#fbe9e7")
    ax.axhline(0, color=COLOR_REAL, lw=1)
    for nombre in modelos:
        ax.plot(x, sesgo[nombre], color=COLORES_MODELO[nombre], marker=MARCADORES[nombre],
                lw=2, ms=7, markeredgecolor="white", markeredgewidth=0.8, label=nombre)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:+,.1f}"))
    ax.xaxis.set_major_locator(mdates.MonthLocator(bymonthday=15))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b-%y"))
    ax.set_xlim(inicio[0], inicio[-1] + pd.offsets.MonthBegin(1))
    ax.set_ylabel("Pronóstico − real (millones MXN)")
    ax.set_title("Sesgo mensual del pronóstico (azul claro = mayor ingreso · rojo claro = mayor gasto)", loc="left")
    ax.legend(loc="lower right", ncol=2, fontsize=8.5)
    fig.tight_layout()
    return _guardar(fig, ruta)


def mae_mensual(mae, modelos, cfg, ruta):
    inicio = mae.index.to_timestamp()
    x = inicio + pd.Timedelta(days=14)
    fig, ax = plt.subplots(figsize=(11, 4.2))
    for m in inicio:
        if m.month in cfg.meses_ingreso_alto or m.month in cfg.meses_gasto_alto:
            ax.axvspan(m, m + pd.offsets.MonthBegin(1), zorder=0, lw=0,
                       color="#e6f0fb" if m.month in cfg.meses_ingreso_alto else "#fbe9e7")
    for nombre in modelos:
        ax.plot(x, mae[nombre], color=COLORES_MODELO[nombre], marker=MARCADORES[nombre],
                lw=2, ms=6.5, markeredgecolor="white", markeredgewidth=0.8, label=nombre)
    ax.xaxis.set_major_locator(mdates.MonthLocator(bymonthday=15))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b-%y"))
    ax.set_xlim(inicio[0], inicio[-1] + pd.offsets.MonthBegin(1))
    ax.set_ylim(bottom=0)
    ax.set_ylabel("MAE (millones MXN)")
    ax.set_title("MAE mensual por mes de la fecha pronosticada (azul claro = mayor ingreso · rojo claro = mayor gasto)",
                 loc="left")
    ax.legend(loc="upper right", ncol=2, fontsize=8.5)
    fig.tight_layout()
    return _guardar(fig, ruta)


def exactitud_costo(resumen, n_series, ruta):
    orden = resumen.index.tolist()[::-1]
    con_latencia = "Latencia mediana (ms)" in resumen
    fig, axes = plt.subplots(1, 2 if con_latencia else 1, figsize=(12 if con_latencia else 7, 0.55 * len(orden) + 1.6),
                             squeeze=False)
    ax1 = axes[0, 0]
    v = resumen.loc[orden, "rMAE vs naive"]
    ax1.barh(orden, v, color=COLOR_POSITIVO, height=0.6)
    ax1.axvline(1, color=COLOR_REAL, lw=1, ls="--")
    ax1.text(1, len(orden) - 0.4, " naive = 1", color="#52514e", va="bottom", fontsize=8.5)
    for i, x in enumerate(v):
        ax1.text(x, i, f" {x:.2f}", va="center", color="#52514e", fontsize=8.5)
    ax1.set_title("MAE relativo al naive (menor es mejor)", loc="left")
    ax1.grid(True, axis="x"); ax1.grid(False, axis="y")
    ax1.set_xlim(0, max(v.max(), 1) * 1.18)
    if con_latencia:
        ax2 = axes[0, 1]
        lat = resumen.loc[orden, "Latencia mediana (ms)"]
        ax2.barh(orden, lat, color=COLOR_POSITIVO, height=0.6)
        for i, x in enumerate(lat):
            ax2.text(x, i, f" {x:,.1f}", va="center", color="#52514e", fontsize=8.5)
        ax2.set_xscale("log")
        ax2.set_title(f"Latencia mediana por llamada, ms ({n_series} series, escala log)", loc="left")
        ax2.grid(True, axis="x"); ax2.grid(False, axis="y")
        ax2.set_xlim(lat.min() / 2, lat.max() * 6)
        ax2.tick_params(axis="y", labelleft=False)
    fig.tight_layout()
    return _guardar(fig, ruta)


def escalabilidad(tabla, dispositivo, ruta):
    fig, ax = plt.subplots(figsize=(8, 4.2))
    for nombre in tabla.columns:
        medidos = tabla[nombre].dropna()
        ax.plot(medidos.index, medidos, color=COLORES_MODELO[nombre], marker=MARCADORES[nombre],
                lw=2, ms=7, label=nombre)
    ax.set_xscale("log"); ax.set_yscale("log")
    ax.set_xticks(list(tabla.index), [str(n) for n in tabla.index])
    ax.grid(True, which="major", axis="both")
    ax.set_xlabel("Series pronosticadas en una llamada")
    ax.set_ylabel(f"Latencia mediana (ms, {dispositivo} para Chronos)")
    ax.set_title("Escalabilidad de la inferencia", loc="left")
    ax.legend(loc="upper left", fontsize=8.5)
    fig.tight_layout()
    return _guardar(fig, ruta)


def siguiente_dia(pronostico_final, series, nombres, cfg, ruta):
    ids = list(series["unique_id"].unique())
    dia = pronostico_final["ds"].min()
    sig = pronostico_final[pronostico_final["ds"] == dia]
    q_lo, q_hi = cfg.q_lo, cfg.q_hi
    fig, axes = plt.subplots(int(np.ceil(len(ids) / 2)), 2, figsize=(12, 2.6 * np.ceil(len(ids) / 2) + 0.6),
                             squeeze=False)
    orden = list(nombres)[::-1]
    for ax, uid in zip(axes.flat, ids):
        d = sig[sig["unique_id"] == uid].set_index("modelo").loc[orden]
        for i, nombre in enumerate(orden):
            ax.plot([d.loc[nombre, q_lo], d.loc[nombre, q_hi]], [i, i], color=COLORES_MODELO[nombre], lw=2.5,
                    solid_capstyle="round")
            ax.plot(d.loc[nombre, COL_PUNTO], i, marker=MARCADORES[nombre], color=COLORES_MODELO[nombre], ms=8,
                    markeredgecolor="white", markeredgewidth=1)
        ultimo = series.loc[series["unique_id"] == uid, "y"].iloc[-1]
        ax.axvline(ultimo, color=COLOR_REAL, lw=1, ls="--")
        ax.axvline(0, color=COLOR_NEUTRO, lw=0.8)
        ax.set_yticks(range(len(orden)), orden)
        ax.set_title(uid, loc="left")
        ax.xaxis.set_major_formatter(EN_MILLONES)
        ax.grid(True, axis="x"); ax.grid(False, axis="y")
    for ax in axes.flat[len(ids):]:
        ax.set_visible(False)
    fig.supxlabel("Millones MXN  ·  línea punteada = último día observado", color="#52514e")
    fig.suptitle(f"Pronóstico para el {dia:%d-%m-%Y}: mediana e intervalo {q_lo}–{q_hi}", x=0.01, ha="left",
                 fontweight="bold")
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    return _guardar(fig, ruta)
