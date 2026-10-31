"""Pruebas unitarias de la probabilidad y los niveles de alerta de déficit."""
import numpy as np
import pandas as pd
from scipy.stats import norm

from api import alertas


def test_probabilidad_exacta_para_intervalo_gaussiano():
    mu, sigma = 2.0e6, 3.0e6
    p10, p50, p90 = norm.ppf([0.1, 0.5, 0.9], loc=mu, scale=sigma)
    p = alertas.probabilidad_deficit(p10, p50, p90)
    assert abs(float(p) - norm.cdf(0, loc=mu, scale=sigma)) < 1e-9


def test_probabilidad_respeta_cuantiles():
    # si el P10 es exactamente 0, la probabilidad de déficit es 10 %; si el P50 es 0, 50 %; si el P90 es 0, 90 %
    assert abs(float(alertas.probabilidad_deficit(0.0, 5.0, 12.0)) - 0.1) < 1e-9
    assert abs(float(alertas.probabilidad_deficit(-3.0, 0.0, 9.0)) - 0.5) < 1e-9
    assert abs(float(alertas.probabilidad_deficit(-8.0, -4.0, 0.0)) - 0.9) < 1e-9


def test_probabilidad_monotona_en_la_mediana():
    p50 = np.linspace(-5, 5, 41)
    p = alertas.probabilidad_deficit(p50 - 4, p50, p50 + 4)
    assert np.all(np.diff(p) < 0)


def test_niveles_de_alerta():
    p10 = np.array([1.0, -1.0, -1.0, -6.0])
    p50 = np.array([5.0, 4.0, 1.0, -1.0])
    prob = np.array([0.02, 0.15, 0.30, 0.60])
    assert list(alertas.nivel_alerta(p10, p50, prob)) == ["ninguna", "vigilancia", "media", "alta"]


def test_resumen_toma_el_dia_mas_severo_y_el_colchon():
    dias = pd.DataFrame({"fecha": ["2026-10-01", "2026-10-02"], "p10": [-1.5e6, 0.5e6], "p50": [2e6, 3e6],
                         "p90": [5e6, 6e6], "prob_deficit": [0.2, 0.05], "nivel": ["vigilancia", "ninguna"]})
    r = alertas.resumen_alerta(dias)
    assert r["activa"] and r["nivel"] == "vigilancia" and r["dias_en_alerta"] == 1
    assert r["primera_fecha"] == "2026-10-01"
    assert r["colchon_sugerido"] == 1.5e6


def test_acumulado_suma_medianas_y_varianzas():
    dias = pd.DataFrame({"p10": [-1.0, -1.0], "p50": [1.0, 1.0], "p90": [3.0, 3.0]})
    a = alertas.acumulado(dias)
    sigma = 2.0 / alertas.Z90                     # desviación de cada día
    assert abs(a["p50"] - 2.0) < 1e-12
    assert abs(a["p90"] - (2.0 + alertas.Z90 * np.sqrt(2) * sigma)) < 1e-9


def test_desempeno_reglas_cuenta_bien():
    bt = pd.DataFrame({"P10": [-1, -1, 1, 1], "P50": [-0.5, 1, 2, 2], "P90": [2, 3, 4, 4], "y": [-2, 1, -1, 3]})
    filas = {f["regla"].split(" ")[0]: f for f in alertas.desempeno_reglas(bt)}
    v = filas["vigilancia"]
    assert v["eventos_reales"] == 2 and v["pronosticos"] == 4
    assert v["precision"] == 0.5 and v["recall"] == 0.5
    assert filas["alta"]["precision"] == 1.0 and filas["alta"]["recall"] == 0.5
