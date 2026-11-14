"""¿Un modelo mejora de forma significativa al de referencia (ETS)? Comparación pareada sobre el backtest.

Los modelos se comparan sobre los mismos orígenes, series y horizontes. Para cada origen se promedia la pérdida de
sus pronósticos (4 series x 5 días) y se analiza la diferencia `modelo − referencia`:

- Prueba de Diebold-Mariano con varianza HAC (rezago = horizonte − 1, porque los pronósticos de orígenes
  consecutivos se traslapan) y la corrección de muestra pequeña de Harvey, Leybourne y Newbold.
- Intervalo de confianza de la mejora relativa por bootstrap de bloques móviles de orígenes.
- Ajuste de Holm por comparar varios modelos contra la misma referencia.

Pérdidas: error absoluto de la mediana (MAE) y pérdida de cuantiles (la que promedia el WQL).
Un modelo "mejora de forma significativa" si reduce el MAE al menos `mejora_minima` (5 %, la misma regla con la que
la API reemplaza al modelo recomendado), con p ajustada < `alfa` y un intervalo que no incluye el cero.
"""
import numpy as np
import pandas as pd
from scipy import stats

REFERENCIA = "ETS"
MEJORA_MINIMA = 0.05
ALFA = 0.05
COL_PUNTO = "pronostico"
CUANTILES = {0.1: "P10", 0.5: "P50", 0.9: "P90"}

V_MEJORA = "Mejora significativa"
V_MENOR = "Mejora significativa, menor al mínimo"
V_IGUAL = "Sin diferencia significativa"
V_PEOR = "Peor, significativo"


def perdidas(resultados, cuantiles=None):
    """Agrega a cada pronóstico su error absoluto y su pérdida de cuantiles promedio."""
    cuantiles = cuantiles or CUANTILES
    d = resultados.copy()
    d["perdida_abs"] = (d[COL_PUNTO] - d["y"]).abs()
    d["perdida_q"] = sum(np.maximum(q * (d["y"] - d[c]), (q - 1) * (d["y"] - d[c])) for q, c in cuantiles.items()) \
        / len(cuantiles)
    return d


def por_origen(d, columna):
    """Tabla origen x modelo con la pérdida promedio de los pronósticos de cada origen."""
    t = d.pivot_table(index="origen", columns="modelo", values=columna, aggfunc="mean", sort=True)
    return t.dropna()


def diebold_mariano(dif, rezago):
    """Estadístico y valor p bilateral de H0: las dos pérdidas esperadas son iguales."""
    dif = np.asarray(dif, dtype=float)
    n = len(dif)
    if n < 8 or np.allclose(dif, dif[0]):
        return np.nan, np.nan
    rezago = int(min(max(rezago, 0), n - 2))
    c = dif - dif.mean()
    gamma = [c @ c / n] + [(c[k:] @ c[:-k]) / n for k in range(1, 2 * rezago + 1) if k < n]
    var = gamma[0] + 2 * sum(gamma[1:rezago + 1])        # núcleo rectangular, el de la prueba original
    if var <= 0:                                         # puede salir negativa: se usa Bartlett con el doble de rezago
        ancho = len(gamma)
        var = gamma[0] + 2 * sum((1 - k / ancho) * g for k, g in enumerate(gamma[1:], start=1))
    if var <= 0:
        return np.nan, np.nan
    dm = dif.mean() / np.sqrt(var / n)
    h = rezago + 1
    dm *= np.sqrt(max(n + 1 - 2 * h + h * (h - 1) / n, 1e-9) / n)     # Harvey-Leybourne-Newbold
    return dm, 2 * stats.t.sf(abs(dm), df=n - 1)


def intervalo_mejora(modelo, referencia, bloque=10, repeticiones=5000, confianza=0.95, semilla=42):
    """IC de la mejora relativa 1 − pérdida(modelo)/pérdida(referencia) por bootstrap de bloques móviles."""
    modelo, referencia = np.asarray(modelo, float), np.asarray(referencia, float)
    n = len(modelo)
    bloque = int(min(max(bloque, 1), n))
    rng = np.random.default_rng(semilla)
    n_bloques = int(np.ceil(n / bloque))
    inicios = rng.integers(0, n - bloque + 1, size=(repeticiones, n_bloques))
    idx = (inicios[:, :, None] + np.arange(bloque)[None, None, :]).reshape(repeticiones, -1)[:, :n]
    mejoras = 1 - modelo[idx].sum(axis=1) / referencia[idx].sum(axis=1)
    cola = (1 - confianza) / 2
    return tuple(np.quantile(mejoras, [cola, 1 - cola]))


def holm(p):
    """Valores p ajustados por Holm (los NaN se quedan como NaN)."""
    p = pd.Series(p, dtype=float)
    validos = p.dropna().sort_values()
    m, ajustados, maximo = len(validos), {}, 0.0
    for i, (k, v) in enumerate(validos.items()):
        maximo = max(maximo, min(1.0, (m - i) * v))
        ajustados[k] = maximo
    return p.index.map(lambda k: ajustados.get(k, np.nan)).to_numpy()


def veredicto(mejora, p, ic_lo, ic_hi, mejora_minima=MEJORA_MINIMA, alfa=ALFA):
    if not np.isfinite(p) or p >= alfa:
        return V_IGUAL
    if mejora < 0 and ic_hi < 0:
        return V_PEOR
    if mejora > 0 and ic_lo > 0:
        return V_MEJORA if mejora >= mejora_minima else V_MENOR
    return V_IGUAL


def comparar(resultados, referencia=REFERENCIA, horizonte=None, cuantiles=None, mejora_minima=MEJORA_MINIMA,
             alfa=ALFA, bloque=10, repeticiones=5000, semilla=42):
    """Tabla con una fila por modelo: MAE, WQL, mejora frente a la referencia, IC, valor p y veredicto."""
    if referencia not in set(resultados["modelo"]):
        raise ValueError(f"El backtest no tiene el modelo de referencia '{referencia}'.")
    d = perdidas(resultados, cuantiles)
    horizonte = int(horizonte or d["h"].max())
    abs_o, q_o = por_origen(d, "perdida_abs"), por_origen(d, "perdida_q")
    suma_y = d[d["modelo"] == referencia]["y"].abs().sum()
    n_filas = (d["modelo"] == referencia).sum()
    filas = {}
    for m in [x for x in d["modelo"].unique() if x != referencia]:
        dm_a, p_a = diebold_mariano(abs_o[m] - abs_o[referencia], horizonte - 1)
        dm_q, p_q = diebold_mariano(q_o[m] - q_o[referencia], horizonte - 1)
        lo_a, hi_a = intervalo_mejora(abs_o[m], abs_o[referencia], bloque, repeticiones, semilla=semilla)
        lo_q, hi_q = intervalo_mejora(q_o[m], q_o[referencia], bloque, repeticiones, semilla=semilla)
        filas[m] = {
            "MAE (M MXN)": abs_o[m].mean() / 1e6,
            "MAE referencia (M MXN)": abs_o[referencia].mean() / 1e6,
            "Mejora MAE (%)": (1 - abs_o[m].sum() / abs_o[referencia].sum()) * 100,
            "IC95 inferior (%)": lo_a * 100, "IC95 superior (%)": hi_a * 100,
            "DM": dm_a, "p": p_a,
            "WQL": 2 * q_o[m].mean() * n_filas / suma_y,
            "Mejora WQL (%)": (1 - q_o[m].sum() / q_o[referencia].sum()) * 100,
            "IC95 WQL inferior (%)": lo_q * 100, "IC95 WQL superior (%)": hi_q * 100,
            "p WQL": p_q,
            "Orígenes en que gana (%)": (abs_o[m] < abs_o[referencia]).mean() * 100,
            "Orígenes": len(abs_o),
        }
    t = pd.DataFrame(filas).T
    t.index.name = "modelo"
    if t.empty:
        return t
    t["p ajustada (Holm)"] = holm(t["p"])
    t["p WQL ajustada (Holm)"] = holm(t["p WQL"])
    t["Veredicto MAE"] = [veredicto(r["Mejora MAE (%)"] / 100, r["p ajustada (Holm)"], r["IC95 inferior (%)"],
                                    r["IC95 superior (%)"], mejora_minima, alfa) for _, r in t.iterrows()]
    t["Veredicto WQL"] = [veredicto(r["Mejora WQL (%)"] / 100, r["p WQL ajustada (Holm)"], r["IC95 WQL inferior (%)"],
                                    r["IC95 WQL superior (%)"], mejora_minima, alfa) for _, r in t.iterrows()]
    orden = ["MAE (M MXN)", "MAE referencia (M MXN)", "Mejora MAE (%)", "IC95 inferior (%)", "IC95 superior (%)",
             "DM", "p", "p ajustada (Holm)", "Veredicto MAE", "WQL", "Mejora WQL (%)", "IC95 WQL inferior (%)",
             "IC95 WQL superior (%)", "p WQL", "p WQL ajustada (Holm)", "Veredicto WQL", "Orígenes en que gana (%)",
             "Orígenes"]
    return t[orden].sort_values("Mejora MAE (%)", ascending=False)


def comparar_por_grupo(resultados, columna, referencia=REFERENCIA, **opciones):
    """La misma comparación dentro de cada serie o de cada periodo. Sirve para ver si la mejora es pareja."""
    partes = {}
    for valor, grupo in resultados.groupby(columna, sort=False):
        if grupo["origen"].nunique() >= 8:
            t = comparar(grupo, referencia, **opciones)
            partes[valor] = t[["Mejora MAE (%)", "IC95 inferior (%)", "IC95 superior (%)", "p", "Orígenes"]]
    return pd.concat(partes, names=[columna]) if partes else pd.DataFrame()


def lectura(tabla, referencia=REFERENCIA, familia=None, mejora_minima=MEJORA_MINIMA):
    """Conclusión en texto: qué modelos mejoran a la referencia y si alguno la reemplaza."""
    if tabla.empty:
        return [f"El backtest solo tiene el modelo {referencia}; no hay con qué compararlo."]
    lineas = []
    for m, r in tabla.iterrows():
        signo = "menos" if r["Mejora MAE (%)"] >= 0 else "más"
        lineas.append(
            f"- **{m}**: MAE {r['MAE (M MXN)']:.2f} M MXN, {abs(r['Mejora MAE (%)']):.1f} % {signo} error que "
            f"{referencia} (IC 95 %: {r['IC95 inferior (%)']:+.1f} a {r['IC95 superior (%)']:+.1f} %; "
            f"p ajustada {r['p ajustada (Holm)']:.3f}). {r['Veredicto MAE']}. "
            f"WQL {r['WQL']:.4f} ({r['Mejora WQL (%)']:+.1f} %): {r['Veredicto WQL'].lower()}.")
    candidatos = [m for m in tabla.index if familia is None or familia.get(m) == "Fundacional HF"]
    ganadores = [m for m in candidatos if tabla.loc[m, "Veredicto MAE"] == V_MEJORA]
    if familia is not None and not candidatos:
        lineas.append(f"\nEsta corrida no incluye Chronos-2 ni Chronos-Bolt; la comparación solo cubre los modelos "
                      f"estadísticos.")
    elif ganadores:
        mejor = max(ganadores, key=lambda m: tabla.loc[m, "Mejora MAE (%)"])
        lineas.append(f"\n**Conclusión:** {mejor} mejora de forma significativa a {referencia} "
                      f"({tabla.loc[mejor, 'Mejora MAE (%)']:.1f} % menos MAE, por encima del mínimo de "
                      f"{mejora_minima * 100:.0f} %). Antes de reemplazarlo hay que revisar sus umbrales de aceptación "
                      f"y su latencia.")
    else:
        lineas.append(f"\n**Conclusión:** ningún modelo Chronos mejora de forma significativa a {referencia} con el "
                      f"mínimo de {mejora_minima * 100:.0f} %. {referencia} se mantiene como modelo recomendado.")
    return lineas
