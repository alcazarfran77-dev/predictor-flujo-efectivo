"""Genera la versión publicable del dashboard para GitHub Pages.

    python -m api.publicar                 # escribe docs/
    python -m api.publicar --salida sitio  # en otra carpeta

GitHub Pages solo sirve archivos, así que aquí se levanta la API en memoria, se le pide cada respuesta que usa
el dashboard (todas las series y todos los modelos) y cada una se guarda como archivo JSON en
docs/api/v1/. El dashboard detecta que es la versión publicada (meta flujo-modo=estatico) y lee esos archivos
en lugar de llamar al servidor. Los mismos archivos quedan disponibles para cualquiera en la página, por ejemplo
https://<usuario>.github.io/predictor-flujo-efectivo/api/v1/pronostico/OPERATIVO_TOTAL/ETS.json
"""
import argparse
import json
import re
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import warnings

warnings.filterwarnings("ignore", message=".*starlette.testclient.*")   # aviso de versiones nuevas de Starlette
from fastapi.testclient import TestClient  # noqa: E402

from ._rutas import RAIZ  # noqa: E402
from .main import app  # noqa: E402
from .servicio import ServicioPronostico  # noqa: E402
from flujo_efectivo.validacion import DatosInvalidos  # noqa: E402

MARCA = ".nojekyll"          # GitHub Pages no pasa el sitio por Jekyll; también marca la carpeta como generada


def slug(modelo):
    return re.sub(r"[^A-Za-z0-9]+", "_", modelo).strip("_")


def _escribir(destino, relativa, datos):
    ruta = destino / "api" / "v1" / relativa
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps(datos, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return ruta


def _preparar_carpeta(destino):
    """Vacía la carpeta sin borrarla (puede ser un volumen montado en Docker)."""
    destino.mkdir(parents=True, exist_ok=True)
    contenido = list(destino.iterdir())
    if contenido and not (destino / MARCA).exists():
        raise SystemExit(f"{destino} ya tiene archivos que no generó este script; elige otra carpeta.")
    for p in contenido:
        shutil.rmtree(p) if p.is_dir() and not p.is_symlink() else p.unlink()


def _copiar_frontend(destino):
    origen = RAIZ / "frontend"
    for f in ("app.js", "styles.css"):
        shutil.copy2(origen / f, destino / f)
    shutil.copytree(origen / "fuentes", destino / "fuentes")
    html = (origen / "index.html").read_text(encoding="utf-8")
    meta = '<meta name="flujo-modo" content="estatico">'
    html = html.replace('<meta name="viewport" content="width=device-width, initial-scale=1">',
                        '<meta name="viewport" content="width=device-width, initial-scale=1">\n  ' + meta, 1)
    if meta not in html:
        raise RuntimeError("No encontré dónde marcar index.html como versión publicada.")
    (destino / "index.html").write_text(html, encoding="utf-8")
    (destino / MARCA).write_text("", encoding="utf-8")


def publicar(destino, ruta_datos=None, avance=print):
    destino = Path(destino)
    with tempfile.TemporaryDirectory() as tmp:
        servicio = ServicioPronostico(ruta_datos=ruta_datos, precalentar=False, escribir_salida=False, dir_salidas=tmp)
        anterior = getattr(app.state, "servicio", None)
        app.state.servicio = servicio
        try:
            with TestClient(app) as c:
                if servicio.hilo is not None:
                    avance("Calculando el backtest que falta para las métricas de confianza...")
                    servicio.hilo.join()

                def pedir(url, **params):
                    r = c.get(url, params=params)
                    if r.status_code != 200:
                        raise RuntimeError(f"GET {url} {params} respondió {r.status_code}: {r.text[:300]}")
                    return r.json()

                _preparar_carpeta(destino)
                salud = pedir("/api/v1/salud")
                salud.update(modo="estatico", archivo_salida=None,
                             publicado=datetime.now(timezone.utc).isoformat(timespec="seconds"))
                archivos = [_escribir(destino, "salud.json", salud),
                            _escribir(destino, "series.json", pedir("/api/v1/series")),
                            _escribir(destino, "modelos.json", pedir("/api/v1/modelos")),
                            _escribir(destino, "datos/validacion.json", pedir("/api/v1/datos/validacion"))]
                for serie in servicio.datos.ids:
                    archivos.append(_escribir(destino, f"series/{serie}/historia.json",
                                              pedir(f"/api/v1/series/{serie}/historia", dias=30)))
                    for modelo in servicio.modelos:
                        archivos.append(_escribir(destino, f"pronostico/{serie}/{slug(modelo)}.json",
                                                  pedir("/api/v1/pronostico", serie=serie, modelo=modelo, horizonte=5)))
                        archivos.append(_escribir(destino, f"metricas/{serie}/{slug(modelo)}.json",
                                                  pedir("/api/v1/metricas", serie=serie, modelo=modelo)))
                for modelo in servicio.modelos:
                    archivos.append(_escribir(destino, f"alertas/{slug(modelo)}.json", pedir("/api/v1/alertas", modelo=modelo)))
                    archivos.append(_escribir(destino, f"reporte/{slug(modelo)}.json", pedir("/api/v1/reporte", modelo=modelo)))
                openapi = destino / "api" / "openapi.json"
                openapi.write_text(json.dumps(c.get("/openapi.json").json(), ensure_ascii=False, indent=1), encoding="utf-8")
        finally:
            app.state.servicio = anterior
    _copiar_frontend(destino)
    total = sum(1 for p in destino.rglob("*") if p.is_file())
    avance(f"Página generada en {destino}: {total} archivos ({len(archivos)} respuestas de la API en JSON), "
           f"datos hasta {salud['datos']['hasta']}.")
    return destino


def main(argv=None):
    p = argparse.ArgumentParser(description="Genera la versión del dashboard para GitHub Pages.")
    p.add_argument("--salida", type=Path, default=RAIZ / "docs")
    p.add_argument("--datos", type=Path, help="JSON de movimientos (por omisión, el de data/)")
    a = p.parse_args(argv)
    try:
        publicar(a.salida, a.datos)
    except DatosInvalidos as e:
        print(e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
