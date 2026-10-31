"""Prueba de punta a punta: levanta la API con uvicorn, abre el dashboard en Chromium y comprueba que lo que se
ve en pantalla es lo que entregó la API, al cambiar de serie y de modelo, con el frontend servido desde otro
origen (CORS) y con la API caída.

Necesita Playwright y Chromium; si no están, la prueba se omite. Para guardar capturas:
    CAPTURAS=results/capturas python -m pytest tests/test_e2e.py
"""
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
import requests

playwright = pytest.importorskip("playwright.sync_api")
RAIZ = Path(__file__).resolve().parents[1]


def puerto_libre():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def esperar(url, segundos=90):
    limite = time.time() + segundos
    while time.time() < limite:
        try:
            if requests.get(url, timeout=2).status_code == 200:
                return
        except requests.RequestException:
            pass
        time.sleep(0.5)
    raise TimeoutError(url)


@pytest.fixture(scope="module")
def salidas_api(tmp_path_factory):
    return tmp_path_factory.mktemp("salidas_api")


@pytest.fixture(scope="module")
def api(salidas_api):
    puerto = puerto_libre()
    registro = open(salidas_api / "uvicorn.log", "w")
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "api.main:app", "--host", "127.0.0.1", "--port", str(puerto)],
                            cwd=RAIZ, stdout=registro, stderr=subprocess.STDOUT,
                            env={**os.environ, "FLUJO_SALIDAS": str(salidas_api)})
    try:
        esperar(f"http://127.0.0.1:{puerto}/api/v1/salud")
        yield f"http://127.0.0.1:{puerto}/"
    finally:
        proc.terminate()
        proc.wait(timeout=10)
        registro.close()


@pytest.fixture(scope="module")
def frontend_aparte():
    """El mismo frontend servido por un servidor estático en otro puerto (otro origen)."""
    puerto = puerto_libre()
    proc = subprocess.Popen([sys.executable, "-m", "http.server", str(puerto), "--bind", "127.0.0.1",
                             "--directory", str(RAIZ / "frontend")], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        esperar(f"http://127.0.0.1:{puerto}/index.html")
        yield f"http://127.0.0.1:{puerto}/"
    finally:
        proc.terminate()
        proc.wait(timeout=10)


@pytest.fixture(scope="module")
def navegador():
    with playwright.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:                       # Chromium no instalado
            pytest.skip(f"No se pudo abrir Chromium: {e}")
        yield b
        b.close()


def capturar(pagina, nombre):
    destino = os.environ.get("CAPTURAS")
    if destino:
        Path(destino).mkdir(parents=True, exist_ok=True)
        pagina.screenshot(path=str(Path(destino) / nombre), full_page=True)


def millones(v):
    return ("−" if v < 0 else "") + f"{abs(v) / 1e6:,.2f}"


def abrir(navegador, url, **opciones):
    pagina = navegador.new_page(viewport={"width": 1360, "height": 900}, accept_downloads=True, **opciones)
    pagina.set_default_timeout(90_000)          # el primer pronóstico de SARIMAX tarda segundos con la CPU ocupada
    errores = []
    pagina.on("pageerror", lambda e: errores.append(str(e)))
    pagina.on("console", lambda m: errores.append(m.text) if m.type == "error" else None)
    pagina.goto(url)
    return pagina, errores


def filas_tabla(pagina):
    return pagina.locator("#tabla-dias tbody tr").evaluate_all(
        "filas => filas.map(f => [...f.cells].map(c => c.textContent.trim()))")


def test_dashboard_muestra_lo_que_entrega_la_api(api, navegador):
    pagina, errores = abrir(navegador, api)
    pagina.wait_for_selector("main[data-estado='listo']", timeout=60_000)
    pagina.wait_for_selector("#tabla-modelos tbody tr", timeout=60_000)

    p = requests.get(api + "api/v1/pronostico", params={"serie": "OPERATIVO_TOTAL", "modelo": "ETS"}).json()
    filas = filas_tabla(pagina)
    assert len(filas) == len(p["dias"]) == 5
    for fila, d in zip(filas, p["dias"]):
        assert fila[1:4] == [millones(d["p10"]), millones(d["p50"]), millones(d["p90"])]
        assert fila[4] == f"{round(d['prob_deficit'] * 100)} %"
    assert "OPERATIVO_TOTAL" in pagina.text_content("#alerta-titulo")
    assert pagina.get_attribute("#alerta", "data-nivel") == p["alerta_deficit"]["nivel"]
    assert pagina.text_content("#colchon").startswith(millones(p["alerta_deficit"]["colchon_sugerido"]))
    assert pagina.locator("#umbrales li").count() == 6
    assert pagina.locator("#grafica svg circle").count() == 5          # un punto P50 por día pronosticado
    assert pagina.get_attribute("#conexion", "data-estado") == "ok"
    capturar(pagina, "dashboard_escritorio.png")

    # cambiar de serie: el dashboard pide el nuevo pronóstico y lo pinta
    with pagina.expect_response(lambda r: "api/v1/pronostico?serie=NETO_TOTAL" in r.url and r.status == 200, timeout=90_000):
        pagina.select_option("#sel-serie", "NETO_TOTAL")
    pagina.wait_for_function("document.querySelector('#alerta-titulo').textContent.includes('NETO_TOTAL')")
    pagina.wait_for_selector("main[data-estado='listo']")
    p2 = requests.get(api + "api/v1/pronostico", params={"serie": "NETO_TOTAL", "modelo": "ETS"}).json()
    assert filas_tabla(pagina)[0][2] == millones(p2["dias"][0]["p50"])

    # cambiar de modelo
    with pagina.expect_response(lambda r: "modelo=SARIMAX&" in r.url and "api/v1/pronostico" in r.url and r.status == 200, timeout=90_000):
        pagina.select_option("#sel-modelo", "SARIMAX")
    pagina.wait_for_selector("main[data-estado='listo']")
    p3 = requests.get(api + "api/v1/pronostico", params={"serie": "NETO_TOTAL", "modelo": "SARIMAX"}).json()
    pagina.wait_for_function(f"document.querySelector('#tabla-dias tbody tr').cells[2].textContent.trim() === '{millones(p3['dias'][0]['p50'])}'")
    assert "modelo=SARIMAX" in pagina.url

    # el registro de comunicación muestra peticiones exitosas
    estados = pagina.locator("#tabla-registro tbody td:nth-child(4)").all_text_contents()
    assert estados and all(e == "200" for e in estados)
    assert errores == []
    pagina.close()


def test_descarga_y_guardado_del_json(api, navegador, salidas_api):
    pagina, errores = abrir(navegador, api + "?modelo=SARIMAX")
    pagina.wait_for_selector("main[data-estado='listo']", timeout=60_000)

    # descargar: el archivo que baja el navegador es el reporte de la API para el modelo elegido
    with pagina.expect_download() as descarga:
        pagina.click("#descargar-json")
    archivo = descarga.value
    assert archivo.suggested_filename == "pronostico_SARIMAX_2026-09-30.json"
    rep = json.loads(Path(archivo.path()).read_text(encoding="utf-8"))
    api_rep = requests.get(api + "api/v1/reporte", params={"modelo": "SARIMAX"}).json()
    rep.pop("generado"), api_rep.pop("generado")
    assert rep == api_rep

    # guardar en el servidor: el botón hace POST y el archivo aparece en la carpeta de salidas
    with pagina.expect_response(lambda r: "api/v1/reporte" in r.url and r.request.method == "POST" and r.status == 201):
        pagina.click("#guardar-json")
    pagina.wait_for_function("document.querySelector('#exportar-estado').textContent.startsWith('Guardado')")
    assert "pronostico_SARIMAX_2026-09-30.json" in pagina.text_content("#exportar-estado")
    guardado = json.loads((salidas_api / "pronostico_SARIMAX_2026-09-30.json").read_text(encoding="utf-8"))
    assert guardado["modelo"] == "SARIMAX" and len(guardado["series"]) == 4
    assert errores == []
    pagina.close()


def test_frontend_en_otro_origen_usa_cors(api, frontend_aparte, navegador):
    pagina, errores = abrir(navegador, f"{frontend_aparte}index.html?api={api}")
    pagina.wait_for_selector("main[data-estado='listo']", timeout=60_000)
    assert pagina.get_attribute("#conexion", "data-estado") == "ok"
    assert len(filas_tabla(pagina)) == 5
    assert errores == []
    pagina.close()


def test_api_caida_muestra_error_y_permite_reintentar(navegador, frontend_aparte):
    cerrado = f"http://127.0.0.1:{puerto_libre()}/"
    pagina, _ = abrir(navegador, f"{frontend_aparte}index.html?api={cerrado}")
    pagina.wait_for_selector("#error:not([hidden])", timeout=20_000)
    assert "No hubo respuesta" in pagina.text_content("#error-texto")
    assert pagina.get_attribute("#conexion", "data-estado") == "error"
    assert pagina.is_visible("#reintentar")
    pagina.close()


def test_vista_movil_y_modo_oscuro(api, navegador):
    for nombre, opciones in (("dashboard_movil.png", {"viewport": {"width": 390, "height": 844}}),
                             ("dashboard_oscuro.png", {"color_scheme": "dark"})):
        pagina = navegador.new_page(**opciones) if "viewport" in opciones else navegador.new_page(viewport={"width": 1360, "height": 900}, **opciones)
        pagina.set_default_timeout(90_000)
        pagina.goto(api)
        pagina.wait_for_selector("main[data-estado='listo']", timeout=60_000)
        pagina.wait_for_selector("#tabla-modelos tbody tr", timeout=60_000)
        ancho = pagina.evaluate("document.documentElement.scrollWidth")
        assert ancho <= pagina.viewport_size["width"] + 1        # sin desplazamiento horizontal
        capturar(pagina, nombre)
        pagina.close()
