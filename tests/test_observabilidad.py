"""Pruebas de observabilidad: trazas, métricas, SLO con presupuesto de error, alertas, drift y respuesta automática."""
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from api import observabilidad as O
from api.servicio import SERIE_PRINCIPAL

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))
sys.path.insert(0, str(RAIZ))

from flujo_efectivo import drift   # noqa: E402
from herramientas.simular_operacion import archivo_con_drift, archivo_invalido   # noqa: E402


# ---------------------------------------------------------------- trazas, logs y métricas
def test_traza_respeta_el_traceparent_valido_y_genera_si_falta():
    trace, span = O.nueva_traza("00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01")
    assert trace == "0af7651916cd43dd8448eb211c80319c" and len(span) == 16
    for malo in (None, "", "basura", "00-" + "0" * 32 + "-b7ad6b7169203331-01", "00-zz-xx-01"):
        trace, span = O.nueva_traza(malo)
        assert len(trace) == 32 and int(trace, 16) and len(span) == 16


def test_cada_respuesta_lleva_traza_y_tiempos(cliente):
    r = cliente.get("/api/v1/pronostico", params={"serie": "OPERATIVO_TOTAL"},
                    headers={"traceparent": "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"})
    assert r.status_code == 200
    assert r.headers["traceparent"].startswith("00-0af7651916cd43dd8448eb211c80319c-")
    assert r.headers["server-timing"].startswith("app;dur=")
    assert cliente.get("/api/v1/series").headers["traceparent"] != r.headers["traceparent"]


def test_log_json_con_traza_y_campos_de_negocio():
    datos = O.iniciar_traza("a" * 32, "b" * 16)
    O.anotar(modelo="ETS", serie="OPERATIVO_TOTAL", vacio=None)
    with O.tramo("inferencia", modelo="ETS"):
        time.sleep(0.003)
    registro = O.log.makeRecord("flujo.acceso", 20, __file__, 1, "GET /api/v1/pronostico 200", (), None,
                                extra={"datos": {"ruta": "/api/v1/pronostico", "estado": 200, **datos["campos"], "tramos": datos["tramos"]}})
    linea = json.loads(O.FormatoJSON().format(registro))
    assert linea["trace_id"] == "a" * 32 and linea["nivel"] == "INFO" and linea["modelo"] == "ETS" and "vacio" not in linea
    assert linea["tramos"][0]["nombre"] == "inferencia" and linea["tramos"][0]["ms"] >= 1


def test_metricas_prometheus(cliente):
    cliente.get("/api/v1/pronostico", params={"serie": "NO_EXISTE"})
    texto = cliente.get("/metrics").text
    assert '# TYPE flujo_http_peticiones_total counter' in texto and '# TYPE flujo_http_duracion_segundos histogram' in texto
    assert 'flujo_http_peticiones_total{ruta="/api/v1/pronostico",metodo="GET",estado="404"}' in texto
    assert 'flujo_http_duracion_segundos_bucket{ruta="/api/v1/pronostico",le="+Inf"}' in texto
    for medidor in ("flujo_modelo_rmae_movil", "flujo_modelo_cobertura_movil", "flujo_drift_psi", "flujo_slo_presupuesto_restante",
                    "flujo_slo_tasa_consumo", "flujo_datos_dias_atraso", "flujo_deficit_probabilidad_maxima", "flujo_alerta_activa"):
        assert f"\n{medidor}" in texto
    assert 'flujo_modelo_rmae_movil{modelo="ETS",serie="OPERATIVO_TOTAL"} 0.65' in texto


# ---------------------------------------------------------------- SLO y alertas
def test_panel_operativo_en_estado_normal(cliente):
    o = cliente.get("/api/v1/observabilidad").json()
    assert {s["id"] for s in o["slos"]} == {"disponibilidad", "latencia", "frescura", "calidad", "calibracion"}
    calidad = next(s for s in o["slos"] if s["id"] == "calidad")
    assert calidad["estado"] == "cumple" and calidad["sli_pct"] == 100 and calidad["eventos"] == 60
    assert o["modelo"]["recomendado"] == "ETS" and o["modelo"]["rmae_movil"] == pytest.approx(0.652, abs=0.001)
    assert o["drift"]["nivel"] == "sin cambio" and len(o["drift"]["series"]) == 4
    assert not any(a["id"].startswith(("api_", "latencia_", "modelo_", "drift_")) for a in o["alertas"])
    retador = next(a for a in o["alertas"] if a["id"] == "retador_mejor")
    assert retador["prioridad"] == "P4" and not retador["notifica"] and retador["runbook"] == "RB-04"
    assert set(o["runbooks"]) == {f"RB-0{i}" for i in range(1, 9)}


def obs_con_trafico(errores=0.0, lentas=0.0, n=400):
    obs = O.Observabilidad(fecha_referencia="2026-10-01", modo_respuesta="recomendar")
    for i in range(n):
        obs.registrar("/api/v1/pronostico", "GET", 500 if i < n * errores else 200, 2.0 if i < n * lentas else 0.02)
    obs.registrar("/api/v1/salud", "GET", 500, 0.01)             # las sondas no cuentan para el SLO
    obs.registrar("/api/v1/metricas", "GET", 503, 0.01)          # 503 con Retry-After no es un error del servicio
    return obs


def alertas_de(obs, servicio):
    modelos = O.estado_modelo(servicio, SERIE_PRINCIPAL)
    slos, ventanas = O.calcular_slos(obs, servicio, modelos)
    return slos, O.evaluar_alertas(obs, servicio, slos, ventanas, modelos, O.drift_de(obs, servicio), None)


def test_presupuesto_de_error_y_consumo_rapido(servicio):
    obs = obs_con_trafico(errores=0.30)
    slos, alertas = alertas_de(obs, servicio)
    disp = next(s for s in slos if s["id"] == "disponibilidad")
    assert disp["eventos"] == 401 and disp["malos"] == 120 and disp["sli_pct"] == pytest.approx(70.07, abs=0.01)
    assert disp["tasas_consumo"]["5m"] == pytest.approx(0.2993 / 0.005, rel=0.01) and disp["estado"] == "agotado"
    ids = {a["id"]: a for a in alertas}
    assert ids["api_consumo_rapido"]["prioridad"] == "P1" and ids["api_consumo_rapido"]["notifica"]
    assert "api_consumo_lento" not in ids                        # la rápida ya cubre el mismo problema
    assert ids["presupuesto_agotado_disponibilidad"]["prioridad"] == "P2" and ids["presupuesto_agotado_disponibilidad"]["runbook"] == "RB-08"
    assert alertas[0]["prioridad"] == "P1"                       # ordenadas por prioridad
    assert any(e["tipo"] == "alerta_activada" and e["prioridad"] == "P1" for e in obs.eventos)


def test_un_error_aislado_no_dispara_y_la_latencia_se_inhibe(servicio):
    _, alertas = alertas_de(obs_con_trafico(errores=0.0025), servicio)       # 1 de 400: 0.25 %, la mitad del presupuesto
    assert not any(a["id"].startswith("api_") for a in alertas)
    _, alertas = alertas_de(obs_con_trafico(errores=0.9, lentas=0.9), servicio)
    ids = {a["id"]: a for a in alertas}
    assert ids["latencia_consumo_rapido"]["inhibida_por"] == "api_consumo_rapido" and not ids["latencia_consumo_rapido"]["notifica"]
    obs = obs_con_trafico(errores=0.30, n=10)                                 # muy poco tráfico: no hay base para alertar
    assert not any(a["id"] == "api_consumo_rapido" for a in alertas_de(obs, servicio)[1])


def test_alerta_resuelta_queda_en_la_bitacora(servicio):
    obs = obs_con_trafico(errores=0.30)
    alertas_de(obs, servicio)
    obs.peticiones.clear()
    _, alertas = alertas_de(obs, servicio)
    assert not any(a["id"] == "api_consumo_rapido" for a in alertas)
    assert any(e["tipo"] == "alerta_resuelta" and e["alerta"] == "api_consumo_rapido" for e in obs.eventos)


def test_dias_de_atraso_y_alerta_de_datos_atrasados(servicio):
    assert O.dias_habiles_de_atraso("2026-09-30", "2026-10-01") == 0          # miércoles -> jueves: al día
    assert O.dias_habiles_de_atraso("2026-09-30", "2026-10-02") == 1
    assert O.dias_habiles_de_atraso("2026-10-02", "2026-10-05") == 0          # viernes -> lunes
    assert O.dias_habiles_de_atraso("2026-09-30", "2026-09-30") == 0
    obs = O.Observabilidad(fecha_referencia="2026-10-09")
    _, alertas = alertas_de(obs, servicio)
    atraso = next(a for a in alertas if a["id"] == "datos_atrasados")
    assert atraso["prioridad"] == "P2" and atraso["valor"] == 6 and atraso["runbook"] == "RB-03"


# ---------------------------------------------------------------- drift
def test_psi_y_niveles_de_drift():
    rng = np.random.default_rng(3)
    ref = rng.normal(0, 1, 2000)
    assert drift.psi(ref, ref) == pytest.approx(0, abs=1e-9)
    assert drift.psi(ref, rng.normal(0, 1, 2000)) < 0.02
    assert drift.psi(ref, rng.normal(1.5, 1, 2000)) > 0.25
    assert drift.nivel_drift(0.40, 0.60, 0.1) == drift.SIN_CAMBIO              # PSI alto solo: ruido de una ventana corta
    assert drift.nivel_drift(0.40, 0.60, 0.3) == drift.MODERADO
    assert drift.nivel_drift(0.05, 0.01, 0.0) == drift.MODERADO
    assert drift.nivel_drift(0.40, 0.01, 0.2) == drift.SIGNIFICATIVO


def test_drift_del_ejemplo_y_de_un_cambio_de_nivel(servicio, tmp_path):
    from flujo_efectivo.datos import preparar_datos
    r = drift.resumen(servicio.datos.series, servicio.datos.mov)
    assert r["nivel"] == drift.SIN_CAMBIO and [f["indicador"] for f in r["composicion"]][0] == "Movimientos por día"
    niveles = [h["nivel"] for uid in servicio.datos.ids for h in drift.historia_drift(servicio.datos.series, uid, paso=1)]
    assert niveles.count(drift.SIGNIFICATIVO) / len(niveles) < 0.02            # casi sin falsas alarmas en la historia
    con_drift = preparar_datos(servicio.cfg, archivo_con_drift(tmp_path / "drift_anon.json"))
    r = drift.resumen(con_drift.series, con_drift.mov)
    total = next(f for f in r["series"] if f["serie"] == "OPERATIVO_TOTAL")
    assert r["nivel"] == drift.SIGNIFICATIVO and total["nivel"] == drift.SIGNIFICATIVO and total["cambio_media_sd"] > 1


# ---------------------------------------------------------------- respuesta automática
def servicio_falso(recomendado="ETS"):
    return SimpleNamespace(modelo_recomendado=recomendado, modelo_original=None, _comparacion="x")


def estado(rmae, alarma):
    return {"backtest": "listo", "rmae_movil": rmae, "alarma_rmae": alarma, "cobertura_movil": 80.0, "alarma_cobertura": False}


def test_conmuta_al_respaldo_y_restaura_con_histeresis():
    degradado = [{"id": "modelo_degradado", "inhibida_por": None}]
    modelos = {"ETS": estado(0.97, True), "SARIMAX": estado(0.78, False), "SARIMAX + temporada": estado(0.84, False),
               "Naive (referencia)": estado(1.0, False)}
    s, obs = servicio_falso(), O.Observabilidad(modo_respuesta="recomendar")
    accion = O.respuesta_automatica(obs, s, degradado, modelos)[0]
    assert accion["id"] == "conmutar_modelo" and accion["a"] == "SARIMAX" and not accion["ejecutada"] and s.modelo_recomendado == "ETS"

    s, obs = servicio_falso(), O.Observabilidad(modo_respuesta="aplicar")
    accion = O.respuesta_automatica(obs, s, degradado, modelos)[0]
    assert accion["ejecutada"] and s.modelo_recomendado == "SARIMAX" and s.modelo_original == "ETS" and s._comparacion is None
    assert obs.eventos[0]["tipo"] == "accion_automatica"

    modelos["ETS"] = estado(0.88, False)                                       # mejoró, pero no lo suficiente: se queda el respaldo
    assert O.respuesta_automatica(obs, s, [], modelos) == [] and s.modelo_recomendado == "SARIMAX"
    modelos["ETS"] = estado(0.80, False)
    accion = O.respuesta_automatica(obs, s, [], modelos)[0]
    assert accion["id"] == "restaurar_modelo" and s.modelo_recomendado == "ETS" and s.modelo_original is None


def test_sin_respaldo_escala_y_alerta_inhibida_no_actua():
    modelos = {"ETS": estado(0.97, True), "SARIMAX": estado(0.95, True), "Naive (referencia)": estado(1.0, False)}
    s, obs = servicio_falso(), O.Observabilidad(modo_respuesta="aplicar")
    accion = O.respuesta_automatica(obs, s, [{"id": "modelo_degradado", "inhibida_por": None}], modelos)[0]
    assert accion["id"] == "sin_respaldo" and not accion["ejecutada"] and s.modelo_recomendado == "ETS"
    assert O.respuesta_automatica(obs, s, [{"id": "modelo_degradado", "inhibida_por": "datos_atrasados"}], modelos) == []
    assert O.respuesta_automatica(O.Observabilidad(modo_respuesta="off"), s, [{"id": "modelo_degradado", "inhibida_por": None}], modelos) == []


def test_factor_de_recalibracion(servicio):
    factor = O.factor_recalibracion(servicio, "ETS")
    assert 0.7 < factor < 1.3                                                   # el intervalo de ETS ya está cerca de 80 %


def test_administracion_deshabilitada_sin_token_y_carga_invalida_conserva_los_datos(cliente, monkeypatch):
    assert cliente.post("/api/v1/datos/recargar", json={"archivo": "x.json"}).status_code == 403
    assert cliente.post("/api/v1/observabilidad/simulacro", json={"error": 1}).status_code == 403
    monkeypatch.setenv("FLUJO_TOKEN_ADMIN", "secreto")
    assert cliente.post("/api/v1/datos/recargar", json={"archivo": "x.json"}, headers={"X-Token-Admin": "otro"}).status_code == 401
    cabeceras = {"X-Token-Admin": "secreto"}
    assert cliente.post("/api/v1/observabilidad/simulacro", json={"error": 1}, headers=cabeceras).status_code == 403   # falta FLUJO_CAOS
    assert cliente.post("/api/v1/datos/recargar", json={"archivo": "../README.md"}, headers=cabeceras).status_code == 422
    invalido = archivo_invalido(RAIZ / "data" / "simulacro_invalido.json")
    try:
        r = cliente.post("/api/v1/datos/recargar", json={"archivo": invalido.name}, headers=cabeceras)
        assert r.status_code == 422 and not r.json()["aceptado"] and "concepto" in " ".join(r.json()["errores"])
        assert r.json()["datos_en_uso"] == "movimientos_feban_sep2025_sep2026_anon.json"
        o = cliente.get("/api/v1/observabilidad").json()
        rechazo = next(a for a in o["alertas"] if a["id"] == "carga_rechazada")
        assert rechazo["prioridad"] == "P2" and o["datos"]["movimientos"] == 4804
        assert cliente.get("/api/v1/salud").json()["datos"]["movimientos"] == 4804
    finally:
        invalido.unlink(missing_ok=True)
        cliente.app.state.obs.carga_rechazada = None


# ---------------------------------------------------------------- configuración de alertas, monitor y simulacro
def test_configuracion_de_alertas_es_coherente():
    from api import config_alertas as C
    assert C.validar()
    assert all(a["runbook"] in C.RUNBOOKS and a["prioridad"] in C.PRIORIDADES for a in C.ALERTAS.values())
    assert all(causa in C.ALERTAS and set(efectos) <= set(C.ALERTAS) for causa, efectos in C.INHIBICIONES.items())
    assert C.CONSUMO_RAPIDO["limite"] > C.CONSUMO_LENTO["limite"] and len(C.como_tabla()) == len(C.ALERTAS)


def test_monitor_detecta_alertas_activadas_y_resueltas(cliente):
    from herramientas import monitor_alertas as M
    o = cliente.get("/api/v1/observabilidad").json()
    fila = M.indicadores(o)
    assert fila["estado"] == o["estado"] and fila["modelo"] == "ETS" and "calidad" in fila["presupuesto_consumido_pct"]
    falsa = {"id": "latencia_consumo_rapido", "prioridad": "P2", "nombre": "Latencia", "resumen": "Lenta.", "runbook": "RB-02",
             "runbook_nombre": "Latencia alta", "notifica": True, "inhibida_por": None}
    activas, nuevas, resueltas = M.cambios({}, dict(o, alertas=o["alertas"] + [falsa]))
    assert falsa in nuevas and not resueltas and "RB-02" in M.texto_alerta(falsa)
    activas, nuevas, resueltas = M.cambios(activas, o)
    assert resueltas == [falsa] and not nuevas


def test_export_con_saldos_acumulados_pasa_la_validacion_y_cambia_el_nivel(tmp_path):
    from flujo_efectivo.config import Config
    from flujo_efectivo.validacion import validar_archivo
    from herramientas.simular_operacion import ORIGINAL, archivo_con_saldos_acumulados
    ruta = archivo_con_saldos_acumulados(tmp_path / "saldos.json", dias=15)
    original = json.loads((RAIZ / "data" / ORIGINAL).read_text(encoding="utf-8"))["movimientos"]
    nuevo = json.loads(ruta.read_text(encoding="utf-8"))["movimientos"]
    assert 0 < len(nuevo) - len(original) <= 15
    assert validar_archivo(ruta, Config()).valido
    assert abs(sum(m["monto"] for m in nuevo) - sum(m["monto"] for m in original)) > 5_000_000      # el arrastre mueve el nivel


def test_la_latencia_inyectada_queda_en_la_traza(cliente, monkeypatch):
    monkeypatch.setenv("FLUJO_TOKEN_ADMIN", "t")
    monkeypatch.setenv("FLUJO_CAOS", "1")
    try:
        assert cliente.post("/api/v1/observabilidad/simulacro", json={"latencia_ms": 30}, headers={"X-Token-Admin": "t"}).status_code == 200
        r = cliente.get("/api/v1/series")
        assert "espera_inyectada" in r.headers["server-timing"]
    finally:
        cliente.post("/api/v1/observabilidad/simulacro", json={"latencia_ms": 0}, headers={"X-Token-Admin": "t"})
    assert "espera_inyectada" not in cliente.get("/api/v1/series").headers["server-timing"]
