"""Pruebas de la versión publicada (GitHub Pages): el generador escribe las mismas respuestas que da la API y el
dashboard funciona sin servidor, servido desde usuario.github.io/predictor-flujo-efectivo/."""
import json
import mimetypes
import os
from pathlib import Path

import pytest

from api.publicar import publicar, slug

SERIES = ["OPERATIVO_TOTAL", "OPERATIVO_BBVA", "OPERATIVO_HSBC", "NETO_TOTAL"]
MODELOS = ["ETS", "SARIMAX", "SARIMAX + temporada", "Naive (referencia)"]
URL_PAGES = "https://usuario-ejemplo.github.io/predictor-flujo-efectivo/"


@pytest.fixture(scope="module")
def sitio(tmp_path_factory):
    return publicar(tmp_path_factory.mktemp("pages") / "predictor-flujo-efectivo", avance=lambda *_: None)


def leer(sitio, relativa):
    return json.loads((sitio / "api" / "v1" / relativa).read_text(encoding="utf-8"))


def test_archivos_generados(sitio):
    assert (sitio / "index.html").exists() and (sitio / ".nojekyll").exists()
    assert '<meta name="flujo-modo" content="estatico">' in (sitio / "index.html").read_text(encoding="utf-8")
    for serie in SERIES:
        assert (sitio / "api" / "v1" / "series" / serie / "historia.json").exists()
        for m in MODELOS:
            assert (sitio / "api" / "v1" / "pronostico" / serie / f"{slug(m)}.json").exists()
            assert (sitio / "api" / "v1" / "metricas" / serie / f"{slug(m)}.json").exists()
    assert json.loads((sitio / "api" / "openapi.json").read_text(encoding="utf-8"))["info"]["version"]
    salud = leer(sitio, "salud.json")
    assert salud["modo"] == "estatico" and salud["publicado"] and salud["archivo_salida"] is None
    assert sum(1 for p in sitio.rglob("*") if p.is_file()) < 100          # cabe en una sola carga web de GitHub


def test_archivos_iguales_a_la_api(sitio, cliente):
    for serie in SERIES:
        for m in ("ETS", "SARIMAX"):
            assert leer(sitio, f"pronostico/{serie}/{slug(m)}.json")["dias"] == \
                cliente.get("/api/v1/pronostico", params={"serie": serie, "modelo": m}).json()["dias"]
    rep = leer(sitio, "reporte/ETS.json")
    api_rep = cliente.get("/api/v1/reporte", params={"modelo": "ETS"}).json()
    assert rep["series"] == api_rep["series"]            # la latencia y la hora cambian entre corridas; el pronóstico no
    assert rep["confianza"]["metricas"] == api_rep["confianza"]["metricas"]
    sin_latencia = lambda r: [u for u in r["confianza"]["umbrales"] if u["clave"] != "latencia"]   # noqa: E731
    assert sin_latencia(rep) == sin_latencia(api_rep)


def test_no_sobrescribe_una_carpeta_ajena(tmp_path):
    (tmp_path / "mio.txt").write_text("no borrar", encoding="utf-8")
    with pytest.raises(SystemExit):
        publicar(tmp_path, avance=lambda *_: None)
    assert (tmp_path / "mio.txt").exists()


def test_dashboard_publicado_funciona_sin_servidor(sitio):
    sync_api = pytest.importorskip("playwright.sync_api")

    def servir(route):
        relativa = route.request.url.split("?")[0].removeprefix(URL_PAGES) or "index.html"
        archivo = sitio / relativa
        if archivo.is_dir():
            archivo = archivo / "index.html"
        if not archivo.is_file():
            return route.fulfill(status=404, body="no existe")
        tipo = mimetypes.guess_type(str(archivo))[0] or "application/octet-stream"
        route.fulfill(status=200, body=archivo.read_bytes(), content_type=tipo)

    with sync_api.sync_playwright() as p:
        try:
            navegador = p.chromium.launch()
        except Exception as e:
            pytest.skip(f"No se pudo abrir Chromium: {e}")
        pagina = navegador.new_page(viewport={"width": 1360, "height": 900}, accept_downloads=True)
        pagina.set_default_timeout(60_000)
        pagina.route("https://usuario-ejemplo.github.io/**", servir)
        errores, peticiones = [], []
        pagina.on("pageerror", lambda e: errores.append(str(e)))
        pagina.on("console", lambda m: errores.append(m.text) if m.type == "error" else None)
        pagina.on("request", lambda r: peticiones.append(r.url))
        pagina.goto(URL_PAGES)
        pagina.wait_for_selector("main[data-estado='listo']")
        pagina.wait_for_selector("#tabla-modelos tbody tr")

        assert pagina.text_content("#conexion-texto") == "Versión publicada, sin servidor"
        assert not pagina.is_visible("#guardar-json")
        assert pagina.get_attribute("#enlace-repo", "href") == "https://github.com/usuario-ejemplo/predictor-flujo-efectivo"
        p50 = leer(sitio, "pronostico/OPERATIVO_TOTAL/ETS.json")["dias"][0]["p50"]
        assert pagina.locator("#tabla-dias tbody tr").first.locator("td").nth(2).text_content() == f"{p50 / 1e6:,.2f}"

        pagina.select_option("#sel-serie", "OPERATIVO_HSBC")
        pagina.select_option("#sel-modelo", "SARIMAX + temporada")
        pagina.wait_for_function("document.querySelector('#alerta-titulo').textContent.includes('OPERATIVO_HSBC')")
        pagina.wait_for_selector("main[data-estado='listo']")
        assert any(u.endswith("api/v1/pronostico/OPERATIVO_HSBC/SARIMAX_temporada.json") for u in peticiones)
        assert not any("?" in u for u in peticiones if "/api/v1/" in u)        # solo archivos, sin consultas
        enlace = pagina.locator("#descargar-json")
        assert enlace.get_attribute("download") == "pronostico_SARIMAX_temporada_2026-09-30.json"
        assert enlace.get_attribute("href").endswith("api/v1/reporte/SARIMAX_temporada.json")
        modelo = pagina.evaluate("async (u) => (await (await fetch(u)).json()).modelo", enlace.get_attribute("href"))
        assert modelo == "SARIMAX + temporada"
        if os.environ.get("CAPTURAS"):
            pagina.select_option("#sel-serie", "OPERATIVO_TOTAL")
            pagina.select_option("#sel-modelo", "ETS")
            pagina.wait_for_selector("main[data-estado='listo']")
            pagina.wait_for_timeout(300)
            pagina.screenshot(path=str(Path(os.environ["CAPTURAS"]) / "pagina_publicada.png"), full_page=True)
        assert errores == []
        navegador.close()
