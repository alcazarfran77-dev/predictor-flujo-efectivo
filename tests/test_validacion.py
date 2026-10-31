"""Pruebas de la entrada JSON: qué acepta, qué rechaza y con qué mensaje, y que da las mismas series que el Excel."""
import json
import subprocess
import sys

import pandas as pd
import pytest

from flujo_efectivo.config import ARCHIVO_DATOS, ARCHIVO_JSON, RAIZ, Config
from flujo_efectivo.datos import huella_series, preparar_datos
from flujo_efectivo.validacion import (MAX_ERRORES_DETALLE, DatosInvalidos, validar_archivo,
                                       validar_movimientos)

CFG = Config()


def movimientos(dias=30, inicio="2026-08-03", **extra):
    """Historia mínima válida: un cobro y una comisión por día hábil."""
    filas = []
    for i, f in enumerate(pd.bdate_range(inicio, periods=dias).strftime("%Y-%m-%d")):
        filas.append({"fecha": f, "monto": 1.0e6 + i, "banco": "BBVA", "concepto": "SPEI RECIBIDO", "partida": f"{2 * i:05d}", **extra})
        filas.append({"fecha": f, "monto": -500.0, "banco": "BBVA", "concepto": "COMISION SPEI", "partida": f"{2 * i + 1:05d}", **extra})
    return filas


def errores(r):
    return {(e["fila"], e["campo"]) for e in r.errores}


def test_el_json_del_repositorio_es_valido_y_da_las_mismas_series_que_el_excel():
    r = validar_archivo(ARCHIVO_JSON, CFG)
    assert r.valido and r.total_errores == 0 and r.resumen["movimientos_validos"] == 4804
    desde_json = preparar_datos(CFG, ARCHIVO_JSON)
    desde_excel = preparar_datos(CFG, ARCHIVO_DATOS)
    assert desde_json.series.equals(desde_excel.series)
    assert huella_series(desde_json.series) == huella_series(desde_excel.series)
    meta = json.loads((RAIZ / "results" / "corrida.json").read_text(encoding="utf-8"))
    assert meta["huella_series"] == huella_series(desde_json.series)       # por eso la API reutiliza el backtest


def test_acepta_objeto_lista_y_encabezados_feban():
    assert validar_movimientos({"movimientos": movimientos()}, CFG).valido
    assert validar_movimientos(movimientos(), CFG).valido
    feban = [{"Value date": m["fecha"], "Amount": m["monto"], "House Bk": m["banco"], "Payment Notes": m["concepto"],
              "MR no": m["partida"], "External transaction": "NTRF", "AC": "MXN"} for m in movimientos()]
    r = validar_movimientos(feban, CFG)
    assert r.valido and r.resumen["campos_opcionales"]["tipo_transaccion"] == 60


@pytest.mark.parametrize("cambio, esperado", [
    (lambda m: m.pop("fecha"), (1, "fecha")),
    (lambda m: m.update(fecha="03/08/2026"), (1, "fecha")),
    (lambda m: m.update(fecha="2026-02-30"), (1, "fecha")),
    (lambda m: m.pop("monto"), (1, "monto")),
    (lambda m: m.update(monto="1000"), (1, "monto")),
    (lambda m: m.update(monto=True), (1, "monto")),
    (lambda m: m.update(banco="  "), (1, "banco")),
    (lambda m: m.pop("concepto"), (1, "concepto")),
    (lambda m: m.update(concepto=None), (1, "concepto")),
    (lambda m: m.update(partida=["00001"]), (1, "partida")),
])
def test_cada_dato_obligatorio_se_revisa(cambio, esperado):
    movs = movimientos()
    cambio(movs[0])
    r = validar_movimientos({"movimientos": movs}, CFG)
    assert not r.valido and errores(r) == {esperado}


def test_reune_todos_los_errores_con_un_limite_de_detalle():
    movs = [{"fecha": "x", "monto": "y", "banco": "", "concepto": 1.5} for _ in range(40)]
    r = validar_movimientos(movs, CFG)
    assert r.total_errores == 120 and len(r.errores) == MAX_ERRORES_DETALLE
    assert "y 70 más" in r.texto()


@pytest.mark.parametrize("contenido, campo", [
    ({"datos": []}, "movimientos"),
    ({"movimientos": []}, "movimientos"),
    ({"movimientos": {"a": 1}}, "movimientos"),
    ({"formato": "otro", "movimientos": movimientos()}, "formato"),
    ({"version": "2.0", "movimientos": movimientos()}, "version"),
    ("texto", None),
])
def test_estructura_del_archivo(contenido, campo):
    r = validar_movimientos(contenido, CFG)
    assert not r.valido and r.errores[0]["campo"] == campo


def test_historia_demasiado_corta():
    r = validar_movimientos(movimientos(dias=6), CFG)
    assert not r.valido and "al menos 10" in r.errores[0]["mensaje"]
    corta = validar_movimientos(movimientos(dias=20), CFG)
    assert corta.valido and any("no son concluyentes" in a for a in corta.avisos)


def test_sin_flujo_operativo_no_hay_que_pronosticar():
    movs = [dict(m, concepto="SWEEP TO MX HBMX") for m in movimientos()]
    r = validar_movimientos(movs, CFG)
    assert not r.valido and r.errores[0]["campo"] == "concepto"


def test_moneda_otros_movimientos_se_excluyen_y_sin_mxn_es_error():
    movs = movimientos() + [{"fecha": "2026-08-03", "monto": 10.0, "banco": "BBVA", "concepto": "X", "moneda": "USD"}]
    r = validar_movimientos(movs, CFG)
    assert r.valido and r.resumen["movimientos_validos"] == 60 and any("USD" in a for a in r.avisos)
    r = validar_movimientos({"moneda": "USD", "movimientos": movimientos()}, CFG)
    assert not r.valido and r.errores[0]["campo"] == "moneda"


def test_avisos_de_calidad():
    movs = movimientos(dias=70)
    movs.append(dict(movs[0]))                                      # extracto cargado dos veces
    movs = [m for m in movs if not ("2026-09-01" <= m["fecha"] <= "2026-09-10")]   # hueco de 8 días hábiles
    for m in movs:
        m.pop("partida", None)
    r = validar_movimientos(movs, CFG)
    assert r.valido
    texto = " ".join(r.avisos)
    assert "1 movimientos repetidos" in texto
    assert "del 2026-09-01 al 2026-09-10" in texto
    assert "Sin 'partida'" in texto and "sin 'tipo_transaccion'" in texto


def test_archivo_que_no_es_json(tmp_path):
    roto = tmp_path / "movimientos.json"
    roto.write_text('{"movimientos": [', encoding="utf-8")
    r = validar_archivo(roto, CFG)
    assert not r.valido and "No es un JSON válido" in r.errores[0]["mensaje"]
    assert "No encontré" in validar_archivo(tmp_path / "no_existe.json", CFG).errores[0]["mensaje"]
    assert "Se esperaba un archivo .json" in validar_archivo(ARCHIVO_DATOS, CFG).errores[0]["mensaje"]
    with pytest.raises(DatosInvalidos):
        preparar_datos(CFG, roto)


def test_linea_de_comandos(tmp_path):
    ok = subprocess.run([sys.executable, "-m", "api.validar", str(ARCHIVO_JSON)], cwd=RAIZ, capture_output=True, text=True)
    assert ok.returncode == 0 and "Datos válidos" in ok.stdout
    malo = tmp_path / "m.json"
    malo.write_text(json.dumps([{"fecha": "2026-09-01"}]), encoding="utf-8")
    falla = subprocess.run([sys.executable, "-m", "api.validar", str(malo), "--json"], cwd=RAIZ, capture_output=True, text=True)
    assert falla.returncode == 1 and json.loads(falla.stdout)["total_errores"] == 3


def test_exportar_escribe_el_archivo(tmp_path):
    r = subprocess.run([sys.executable, "-m", "api.exportar", "--salidas", str(tmp_path)], cwd=RAIZ,
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr
    rep = json.loads((tmp_path / "pronostico_ETS_2026-09-30.json").read_text(encoding="utf-8"))
    assert rep["formato"] == "flujo-efectivo/pronostico" and len(rep["series"]) == 4
    assert all(len(s["dias"]) == 5 for s in rep["series"])
