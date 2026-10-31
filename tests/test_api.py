"""Pruebas de contrato e integración de la API: cada endpoint responde con el esquema acordado y con los
mismos números que el paquete de modelos (src/flujo_efectivo) y que el backtest guardado en results/."""
import json
import time
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.servicio import ServicioPronostico
from flujo_efectivo import backtest as bt
from flujo_efectivo.config import RAIZ
from flujo_efectivo.modelos import Registro
from flujo_efectivo.validacion import DatosInvalidos

SERIES = ["OPERATIVO_TOTAL", "OPERATIVO_BBVA", "OPERATIVO_HSBC", "NETO_TOTAL"]


def test_salud(cliente):
    r = cliente.get("/api/v1/salud")
    assert r.status_code == 200
    s = r.json()
    assert s["estado"] == "ok" and s["modelo_recomendado"] == "ETS" and s["serie_principal"] == "OPERATIVO_TOTAL"
    assert s["datos"]["movimientos"] == 4804 and s["datos"]["duplicados"] == 0
    assert {m["nombre"] for m in s["modelos"]} >= {"ETS", "SARIMAX", "SARIMAX + temporada", "Naive (referencia)"}
    assert all(m["backtest"] == "listo" for m in s["modelos"])
    assert s["fechas_pronostico"] == ["2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07"]
    assert r.headers["X-Version-API"] == s["version"] and "app;dur=" in r.headers["Server-Timing"]
    assert s["datos"]["archivo"].endswith(".json") and s["datos"]["formato"] == "json" and s["datos"]["avisos"] == []


def test_series_e_historia(cliente):
    series = cliente.get("/api/v1/series").json()
    assert [s["id"] for s in series] == SERIES
    assert sum(s["principal"] for s in series) == 1
    h = cliente.get("/api/v1/series/OPERATIVO_TOTAL/historia", params={"dias": 20}).json()
    assert len(h["puntos"]) == 20 and h["puntos"][-1]["fecha"] == "2026-09-30"


@pytest.mark.parametrize("serie", SERIES)
def test_pronostico_contrato(cliente, serie):
    r = cliente.get("/api/v1/pronostico", params={"serie": serie, "modelo": "ETS", "horizonte": 5})
    assert r.status_code == 200
    p = r.json()
    assert p["serie"] == serie and p["origen"] == "2026-09-30" and p["cuantiles"] == [0.1, 0.5, 0.9]
    assert len(p["dias"]) == 5 and [d["h"] for d in p["dias"]] == [1, 2, 3, 4, 5]
    for d in p["dias"]:
        assert d["p10"] <= d["p50"] <= d["p90"]
        assert 0 <= d["prob_deficit"] <= 1
        # regla del reporte: hay alerta cuando el P10 es negativo; "alta" cuando también el P50 lo es
        assert d["alerta"]["activa"] == (d["p10"] < 0)
        assert (d["alerta"]["nivel"] == "alta") == (d["p50"] < 0)
        assert d["confianza"]["n"] > 0 and 0 <= d["confianza"]["cobertura"] <= 100
    a = p["alerta_deficit"]
    assert a["activa"] == any(d["p10"] < 0 for d in p["dias"])
    assert a["peor_p10"] == min(d["p10"] for d in p["dias"])
    assert a["colchon_sugerido"] == max(0.0, -a["peor_p10"])
    assert len(a["desempeno"]) == 3
    assert p["acumulado"]["p10"] <= p["acumulado"]["p50"] <= p["acumulado"]["p90"]
    assert p["confianza"]["disponible"] and p["confianza"]["nivel"] in ("alta", "media", "baja")


def test_pronostico_igual_al_paquete_de_modelos(cliente, servicio):
    """La API no transforma los cuantiles: son los mismos que entrega flujo_efectivo para el mismo modelo."""
    directo = bt.pronostico_adelante(servicio.datos.series, Registro(servicio.cfg), servicio.cfg)
    for modelo in ("ETS", "SARIMAX"):
        p = cliente.get("/api/v1/pronostico", params={"serie": "OPERATIVO_TOTAL", "modelo": modelo}).json()
        ref = directo[(directo["modelo"] == modelo) & (directo["unique_id"] == "OPERATIVO_TOTAL")].sort_values("ds")
        for d, (_, f) in zip(p["dias"], ref.iterrows()):
            assert d["fecha"] == f["ds"].strftime("%Y-%m-%d")
            assert d["p10"] == pytest.approx(f["P10"]) and d["p50"] == pytest.approx(f["P50"])
            assert d["p90"] == pytest.approx(f["P90"])


def test_metricas_iguales_al_backtest_guardado(cliente):
    """Las métricas de confianza reproducen las tablas de results/ que generó el script del backtest."""
    m = cliente.get("/api/v1/metricas", params={"modelo": "ETS", "serie": "OPERATIVO_TOTAL"}).json()
    ref = pd.read_csv(RAIZ / "results" / "tablas" / "metricas_regresion.csv", index_col=0).loc["ETS"]
    t = m["todas_las_series"]
    assert t["mae"] == pytest.approx(ref["MAE"], rel=1e-6)
    assert t["rmae"] == pytest.approx(ref["rMAE vs naive"], rel=1e-6)
    assert t["wql"] == pytest.approx(ref["WQL"], rel=1e-6)
    assert t["cobertura"] == pytest.approx(ref["Cobertura P10–P90 (%)"], rel=1e-6)
    assert t["n"] == 5520 and m["origenes"] == 276
    assert [h["h"] for h in m["por_horizonte"]] == [1, 2, 3, 4, 5]
    umbrales = {u["clave"]: u for u in m["umbrales"]}
    assert set(umbrales) == {"rmae", "wql", "cobertura", "f1_direccion", "sesgo_cambio", "latencia"}
    # el reporte concluye que ETS cumple todo excepto el sesgo tras el cambio de temporada
    assert [k for k, u in umbrales.items() if u["cumple"] is False] == ["sesgo_cambio"]
    assert m["nivel_confianza"] == "media"
    assert {"rmae_movil", "cobertura_movil", "duplicados"} <= set(m["monitoreo"])


def test_alertas_de_todas_las_series(cliente):
    filas = cliente.get("/api/v1/alertas", params={"modelo": "ETS"}).json()
    assert [f["serie"] for f in filas] == SERIES
    for f in filas:
        p = cliente.get("/api/v1/pronostico", params={"serie": f["serie"], "modelo": "ETS"}).json()
        assert f["nivel"] == p["alerta_deficit"]["nivel"] and f["peor_p10"] == p["alerta_deficit"]["peor_p10"]


def test_comparacion_de_modelos(cliente):
    filas = {f["modelo"]: f for f in cliente.get("/api/v1/modelos").json()}
    assert filas["ETS"]["recomendado"] and filas["ETS"]["umbrales_cumplidos"] == 5
    assert filas["SARIMAX"]["reemplaza_al_recomendado"] is False
    assert all(f["latencia_ms"] is not None for f in filas.values())


def test_pronostico_de_serie_externa(cliente):
    historia = [{"fecha": f, "valor": 1.0e6 + 2.0e4 * i}
                for i, f in enumerate(pd.bdate_range("2026-08-03", periods=40).strftime("%Y-%m-%d"))]
    r = cliente.post("/api/v1/pronostico", json={"serie_id": "erp-caja", "modelo": "ETS", "horizonte": 3,
                                                "historia": historia})
    assert r.status_code == 200
    p = r.json()
    assert p["serie"] == "erp-caja" and len(p["dias"]) == 3
    assert p["dias"][0]["fecha"] == "2026-09-28"           # siguiente día hábil después del viernes 25-09
    assert all(d["p10"] <= d["p50"] <= d["p90"] for d in p["dias"])
    assert p["confianza"]["disponible"] is False


@pytest.mark.parametrize("metodo, url, cuerpo, codigo", [
    ("get", "/api/v1/pronostico?serie=NO_EXISTE", None, 404),
    ("get", "/api/v1/pronostico?modelo=NO_EXISTE", None, 404),
    ("get", "/api/v1/pronostico?horizonte=9", None, 422),
    ("get", "/api/v1/series/NO_EXISTE/historia", None, 404),
    ("get", "/api/v1/metricas?serie=NO_EXISTE", None, 404),
    ("post", "/api/v1/pronostico", {"historia": [{"fecha": "2026-09-01", "valor": 1.0}] * 5}, 422),
    ("post", "/api/v1/pronostico", {"modelo": "XX", "historia": [{"fecha": f"2026-09-{d:02d}", "valor": 1.0} for d in range(1, 15)]}, 404),
])
def test_errores_con_mensaje_claro(cliente, metodo, url, cuerpo, codigo):
    r = getattr(cliente, metodo)(url, json=cuerpo) if cuerpo is not None else getattr(cliente, metodo)(url)
    assert r.status_code == codigo
    assert "detail" in r.json()


def test_cors_para_un_frontend_en_otro_dominio(cliente):
    r = cliente.get("/api/v1/salud", headers={"Origin": "https://tablero.ejemplo.mx"})
    assert r.headers["access-control-allow-origin"] in ("*", "https://tablero.ejemplo.mx")
    pre = cliente.options("/api/v1/pronostico", headers={"Origin": "https://tablero.ejemplo.mx",
                                                         "Access-Control-Request-Method": "POST",
                                                         "Access-Control-Request-Headers": "content-type"})
    assert pre.status_code == 200


def test_sirve_el_dashboard_y_el_contrato(cliente):
    html = cliente.get("/")
    assert html.status_code == 200 and "app.js" in html.text
    assert cliente.get("/app.js").status_code == 200 and cliente.get("/styles.css").status_code == 200
    esquema = cliente.get("/openapi.json").json()
    assert {"/api/v1/pronostico", "/api/v1/metricas", "/api/v1/alertas", "/api/v1/modelos"} <= set(esquema["paths"])


def test_metricas_mientras_el_backtest_no_esta_listo(tmp_path):
    """Sin backtest en caché, la API sigue pronosticando y avisa con 503 que las métricas aún no existen."""
    pendiente = ServicioPronostico(dir_resultados=tmp_path, dir_cache=tmp_path, calcular_faltantes=False,
                                   precalentar=False, dir_salidas=tmp_path)
    anterior = getattr(app.state, "servicio", None)
    app.state.servicio = pendiente
    try:
        with TestClient(app) as c:
            r = c.get("/api/v1/metricas")
            assert r.status_code == 503 and r.headers["Retry-After"] == "30"
            p = c.get("/api/v1/pronostico").json()
            assert len(p["dias"]) == 5 and p["confianza"]["disponible"] is False
            assert all(d["confianza"] is None for d in p["dias"])
    finally:
        app.state.servicio = anterior


# ------------------------------------------------------------------ entrada JSON validada
def test_validacion_del_archivo_cargado(cliente):
    v = cliente.get("/api/v1/datos/validacion").json()
    assert v["valido"] and v["total_errores"] == 0
    assert v["resumen"]["movimientos_validos"] == 4804 and v["resumen"]["dias_habiles"] == 283
    assert v["resumen"]["bancos"] == {"BBVA": 2319, "HSBC": 2485}


def test_validar_un_json_correcto(cliente):
    contenido = json.loads((RAIZ / "data" / "movimientos_feban_sep2025_sep2026_anon.json").read_text(encoding="utf-8"))
    v = cliente.post("/api/v1/datos/validar", json=contenido).json()
    assert v["valido"] and v["resumen"]["movimientos_validos"] == 4804


def test_validar_un_json_incompleto_dice_que_falta(cliente):
    v = cliente.post("/api/v1/datos/validar", json={"movimientos": [
        {"fecha": "2026-09-01", "monto": 10.0, "banco": "BBVA"},
        {"fecha": "01/09/2026", "monto": "10", "banco": "BBVA", "concepto": "SPEI"},
    ]}).json()
    assert not v["valido"] and v["total_errores"] == 3
    assert {(e["fila"], e["campo"]) for e in v["errores"]} == {(1, "concepto"), (2, "fecha"), (2, "monto")}


# ------------------------------------------------------------------ salida en archivo JSON
def test_reporte_contrato_y_consistencia(cliente):
    r = cliente.get("/api/v1/reporte", params={"modelo": "ETS"})
    assert r.status_code == 200 and "content-disposition" not in r.headers
    rep = r.json()
    assert rep["formato"] == "flujo-efectivo/pronostico" and rep["modelo"] == "ETS" and rep["recomendado"]
    assert [s["serie"] for s in rep["series"]] == SERIES
    assert rep["confianza"]["disponible"] and rep["confianza"]["nivel"] == "media"
    assert set(rep["niveles_alerta"]) == {"ninguna", "vigilancia", "media", "alta"}
    for s in rep["series"]:
        p = cliente.get("/api/v1/pronostico", params={"serie": s["serie"], "modelo": "ETS"}).json()
        assert s["dias"] == p["dias"] and s["alerta_deficit"] == p["alerta_deficit"] and s["acumulado"] == p["acumulado"]


def test_reporte_para_descargar(cliente):
    r = cliente.get("/api/v1/reporte", params={"modelo": "SARIMAX", "descargar": True})
    assert r.status_code == 200
    assert r.headers["content-disposition"] == 'attachment; filename="pronostico_SARIMAX_2026-09-30.json"'
    assert r.json()["modelo"] == "SARIMAX"


def test_reporte_guardado_en_archivo(cliente, dir_salidas):
    r = cliente.post("/api/v1/reporte", params={"modelo": "SARIMAX + temporada"})
    assert r.status_code == 201
    info = r.json()
    ruta = Path(dir_salidas) / "pronostico_SARIMAX_temporada_2026-09-30.json"
    assert info["nombre"] == ruta.name and ruta.exists() and info["bytes"] == ruta.stat().st_size
    en_archivo = json.loads(ruta.read_text(encoding="utf-8"))
    por_api = cliente.get("/api/v1/reporte", params={"modelo": "SARIMAX + temporada"}).json()
    en_archivo.pop("generado"), por_api.pop("generado")
    assert en_archivo == por_api                      # el archivo y la respuesta de la API son el mismo documento


def test_el_servicio_escribe_el_reporte_al_arrancar(cliente, dir_salidas):
    ruta = Path(dir_salidas) / "pronostico_ETS_2026-09-30.json"
    limite = time.time() + 60
    while not ruta.exists() and time.time() < limite:
        time.sleep(0.5)
    assert ruta.exists()
    assert cliente.get("/api/v1/salud").json()["archivo_salida"] is not None
    assert json.loads(ruta.read_text(encoding="utf-8"))["modelo"] == "ETS"


def test_no_arranca_con_un_json_invalido(tmp_path):
    malo = tmp_path / "movimientos.json"
    malo.write_text(json.dumps({"movimientos": [{"fecha": "2026-09-01", "banco": "BBVA", "concepto": "x"}]}), encoding="utf-8")
    with pytest.raises(DatosInvalidos) as e:
        ServicioPronostico(ruta_datos=malo, dir_salidas=tmp_path, precalentar=False).iniciar()
    assert "monto" in str(e.value)
    with pytest.raises(DatosInvalidos):
        ServicioPronostico(ruta_datos=RAIZ / "data" / "DEPOSITOS_CARGADOS_FEBAN_sep2025_sep2026_anon.xlsx",
                           dir_salidas=tmp_path, precalentar=False).iniciar()
