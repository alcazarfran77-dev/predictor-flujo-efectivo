"""Pruebas de la comparación pareada contra ETS: la prueba distingue una mejora real del ruido."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))

from flujo_efectivo import significancia as sig   # noqa: E402


def backtest_sintetico(escalas, n_origenes=200, semilla=7):
    """Backtest con ruido gaussiano: `escalas` da la desviación del error de cada modelo."""
    rng = np.random.default_rng(semilla)
    origenes = pd.bdate_range("2025-09-01", periods=n_origenes)
    filas = []
    for o in origenes:
        for serie in ("A", "B"):
            for h in range(1, 6):
                y = rng.normal(1e6, 5e5)
                for modelo, escala in escalas.items():
                    p50 = y + rng.normal(0, escala)
                    filas.append({"origen": o, "unique_id": serie, "h": h, "modelo": modelo, "y": y,
                                  "pronostico": p50, "P10": p50 - 1.28 * escala, "P50": p50, "P90": p50 + 1.28 * escala,
                                  "periodo": "antes" if o < origenes[len(origenes) * 3 // 4] else "desde"})
    return pd.DataFrame(filas)


def test_detecta_una_mejora_real_y_descarta_el_ruido():
    d = backtest_sintetico({"ETS": 1e6, "Mejor": 0.8e6, "Igual": 1e6, "Peor": 1.3e6})
    t = sig.comparar(d, repeticiones=1000)
    assert t.loc["Mejor", "Veredicto MAE"] == sig.V_MEJORA and t.loc["Mejor", "Mejora MAE (%)"] > 10
    assert t.loc["Igual", "Veredicto MAE"] == sig.V_IGUAL
    assert t.loc["Peor", "Veredicto MAE"] == sig.V_PEOR
    assert (t["IC95 inferior (%)"] <= t["Mejora MAE (%)"]).all() and (t["Mejora MAE (%)"] <= t["IC95 superior (%)"]).all()
    assert (t["p ajustada (Holm)"] >= t["p"] - 1e-12).all()
    assert list(t.index) == ["Mejor", "Igual", "Peor"]                       # ordenada por mejora


def test_mejora_real_pero_menor_al_minimo():
    d = backtest_sintetico({"ETS": 1e6, "Poco mejor": 0.97e6}, n_origenes=1500)
    t = sig.comparar(d, repeticiones=500)
    assert 0 < t.loc["Poco mejor", "Mejora MAE (%)"] < 5
    assert t.loc["Poco mejor", "Veredicto MAE"] == sig.V_MENOR
    assert sig.comparar(d, repeticiones=500, mejora_minima=0.01).loc["Poco mejor", "Veredicto MAE"] == sig.V_MEJORA


def test_diebold_mariano_y_holm():
    rng = np.random.default_rng(1)
    falsos = np.mean([sig.diebold_mariano(rng.normal(0, 1, 276), 4)[1] < 0.05 for _ in range(400)])
    assert falsos < 0.09                                                     # cerca del 5 % nominal
    dm, p = sig.diebold_mariano(rng.normal(0.5, 1, 300), 4)
    assert dm > 0 and p < 0.001
    assert np.isnan(sig.diebold_mariano(np.zeros(50), 4)[0])                 # modelos idénticos
    assert np.isnan(sig.diebold_mariano([1.0, 2.0, 3.0], 4)[0])              # muy pocos orígenes
    assert sig.holm([0.01, 0.04, 0.03]) == pytest.approx([0.03, 0.06, 0.06])
    assert np.isnan(sig.holm([0.01, np.nan])[1])


def test_lectura_dice_si_chronos_reemplaza_a_ets():
    d = backtest_sintetico({"ETS": 1e6, "Chronos-2": 0.8e6, "Chronos-Bolt Small": 1e6})
    familia = {"ETS": "Estadístico", "Chronos-2": "Fundacional HF", "Chronos-Bolt Small": "Fundacional HF"}
    texto = "\n".join(sig.lectura(sig.comparar(d, repeticiones=500), "ETS", familia))
    assert "Chronos-2 mejora de forma significativa a ETS" in texto
    sin = backtest_sintetico({"ETS": 1e6, "Chronos-2": 1e6})
    assert "se mantiene como modelo recomendado" in "\n".join(sig.lectura(sig.comparar(sin, repeticiones=500), "ETS", familia))
    solo = backtest_sintetico({"ETS": 1e6, "SARIMAX": 1e6}, n_origenes=40)
    assert "no incluye Chronos" in "\n".join(sig.lectura(sig.comparar(solo, repeticiones=200), "ETS", {"SARIMAX": "Estadístico"}))
    with pytest.raises(ValueError, match="referencia"):
        sig.comparar(solo[solo["modelo"] != "ETS"])


def test_backtest_guardado_reproduce_las_metricas():
    d = pd.read_csv(RAIZ / "results" / "tablas" / "backtest.csv.gz", parse_dates=["ds", "origen"])
    t = sig.comparar(d, repeticiones=500)
    reg = pd.read_csv(RAIZ / "results" / "tablas" / "metricas_regresion.csv", index_col=0)
    for m in t.index:
        assert t.loc[m, "MAE (M MXN)"] * 1e6 == pytest.approx(reg.loc[m, "MAE"], rel=1e-6)
        assert t.loc[m, "WQL"] == pytest.approx(reg.loc[m, "WQL"], rel=1e-6)
    assert t.loc["SARIMAX", "Veredicto MAE"] == sig.V_PEOR                   # 5.2 % más error que ETS
    por_serie = sig.comparar_por_grupo(d, "unique_id", repeticiones=200)
    assert set(por_serie.index.get_level_values(0)) == set(d["unique_id"])
