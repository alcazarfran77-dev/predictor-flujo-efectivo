"""Job diario y administración de los artefactos que comparte con la API (ver api/almacen.py).

En Render corre como Cron Job con la misma imagen que la API:

    python -m herramientas.artefactos publicar          # job: datos -> backtest -> pronósticos -> versión nueva

Administración (desde Codespaces o la consola del servicio):

    python -m herramientas.artefactos subir-entrada movimientos.json   # valida y deja los datos para el job
    python -m herramientas.artefactos vigente
    python -m herramientas.artefactos listar
    python -m herramientas.artefactos revertir 20261124T130012Z-05b68222
    python -m herramientas.artefactos podar --conservar 30

Variables: FLUJO_ALMACEN (obligatoria), FLUJO_MODELO, FLUJO_DATOS (datos locales si no hay entrada en el almacén),
FLUJO_AVISAR_URL y FLUJO_TOKEN_ADMIN (opcionales: avisa a la API por la red privada para que revise sin esperar).
El job sale con código 1 si falla; en ese caso no mueve el apuntador y la API sigue con la versión anterior.
"""
import argparse
import json
import logging
import os
import platform
import resource
import shutil
import sys
import tempfile
import time
from importlib import metadata
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "src"))

from api import almacen as A  # noqa: E402
from api.servicio import NAIVE, ServicioPronostico  # noqa: E402
from flujo_efectivo.config import ARCHIVO_JSON  # noqa: E402

log = logging.getLogger("flujo.job")


def rss_max_mb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


def versiones_paquetes():
    salida = {"python": platform.python_version()}
    for p in ("pandas", "numpy", "statsmodels", "scipy", "fastapi", "boto3"):
        try:
            salida[p] = metadata.version(p)
        except metadata.PackageNotFoundError:
            pass
    return salida


def obtener_datos(almacen, destino, local=None):
    """Datos de entrada: archivo local si se indica; si no, entrada/movimientos.json del almacén; si no, FLUJO_DATOS."""
    if local:
        shutil.copyfile(local, destino)
        return f"archivo local {Path(local).name}"
    if almacen.leer(A.ENTRADA) is not None:
        almacen.bajar(A.ENTRADA, destino)
        return f"{almacen.descripcion}/{A.ENTRADA}"
    respaldo = Path(os.environ.get("FLUJO_DATOS") or ARCHIVO_JSON)
    shutil.copyfile(respaldo, destino)
    return f"archivo de la imagen {respaldo.name} (no hay {A.ENTRADA} en el almacén)"


def publicar(args):
    almacen = A.abrir()
    if almacen is None:
        raise SystemExit("Define FLUJO_ALMACEN (s3://bucket/prefijo o file:///ruta).")
    fases, t_total = {}, time.perf_counter()
    trabajo = Path(tempfile.mkdtemp(prefix="job_flujo_"))
    carpeta = trabajo / "version"
    carpeta.mkdir()
    try:
        t = time.perf_counter()
        origen = obtener_datos(almacen, carpeta / "movimientos.json", args.datos)
        fases["datos_s"] = time.perf_counter() - t

        t = time.perf_counter()
        s = ServicioPronostico(ruta_datos=carpeta / "movimientos.json",
                               dir_resultados=Path(args.resultados) if args.resultados else trabajo / "sin_resultados",
                               dir_cache=trabajo / "cache", calcular_faltantes=False, precalentar=False,
                               escribir_salida=False, modelo_recomendado=os.environ.get("FLUJO_MODELO", "ETS")).iniciar()
        fases["validar_s"] = time.perf_counter() - t
        actual = A.vigente(almacen)
        if actual and actual.get("huella_series") == s.huella and not args.forzar:
            log.info("Las series no cambiaron desde la versión %s: no se publica nada.", actual["version"])
            print(json.dumps({"resultado": "sin cambios", "version": actual["version"], "origen_datos": origen}))
            return 0

        t = time.perf_counter()
        orden = sorted(s.modelos, key=lambda m: (m != NAIVE, m != s.modelo_recomendado))
        s._calcular_backtest([m for m in orden if s.estado_backtest[m] != "listo"])   # secuencial, en este proceso
        fases["backtest_s"] = time.perf_counter() - t
        errores = [m for m, e in s.estado_backtest.items() if e != "listo"]
        if NAIVE in errores or s.modelo_recomendado in errores:
            raise RuntimeError(f"Falló el backtest de modelos indispensables: {errores}")

        t = time.perf_counter()
        for m in s.modelos:
            if m not in errores:
                s.pronostico_modelo(m)
        fases["pronosticos_s"] = time.perf_counter() - t

        t = time.perf_counter()
        corrida = s.exportar_artefactos(carpeta, extra={"origen_datos": origen, "modelos_con_error": errores,
                                                        "duracion_s": fases, "versiones": versiones_paquetes()})
        meta = {"huella_series": s.huella, "datos_hasta": corrida["datos"]["hasta"], "modelo_recomendado": s.modelo_recomendado,
                "modelos": corrida["modelos"], "modelos_con_error": errores, "origen_datos": origen}
        version = A.publicar(almacen, carpeta, meta, conservar=args.conservar)
        fases["publicar_s"] = time.perf_counter() - t
        fases["total_s"] = time.perf_counter() - t_total
        resumen = {"resultado": "publicada", "version": version, "datos_hasta": meta["datos_hasta"],
                   "modelos_con_error": errores, "bytes": sum(p.stat().st_size for p in carpeta.rglob("*") if p.is_file()),
                   "rss_max_mb": round(rss_max_mb(), 1), "fases_s": {k: round(v, 2) for k, v in fases.items()}}
        print(json.dumps(resumen, ensure_ascii=False))
        avisar_api()
        return 0
    finally:
        shutil.rmtree(trabajo, ignore_errors=True)


def avisar_api():
    """Aviso opcional por la red privada de Render; si falla, la API toma la versión en su siguiente revisión."""
    url, token = os.environ.get("FLUJO_AVISAR_URL"), os.environ.get("FLUJO_TOKEN_ADMIN")
    if not url or not token:
        return
    import urllib.request
    try:
        peticion = urllib.request.Request(url.rstrip("/") + "/api/v1/artefactos/revisar", method="POST",
                                          headers={"X-Token-Admin": token})
        urllib.request.urlopen(peticion, timeout=10).read()
    except Exception as e:                                # el aviso es una cortesía, no una condición
        log.warning("No se pudo avisar a la API (%s); la tomará en su siguiente revisión.", e)


def subir_entrada(args):
    from flujo_efectivo.config import Config
    from flujo_efectivo.validacion import validar_movimientos
    contenido = json.loads(Path(args.archivo).read_text(encoding="utf-8"))
    r = validar_movimientos(contenido, Config())
    if not r.valido:
        print(json.dumps(r.a_dict(), ensure_ascii=False, indent=1))
        raise SystemExit("El archivo no pasó la validación; no se subió.")
    almacen = A.abrir()
    almacen.subir(args.archivo, A.ENTRADA)
    print(f"Subido a {almacen.descripcion}/{A.ENTRADA}. El job lo usará en su siguiente corrida.")
    return 0


def main(argv=None):
    logging.basicConfig(level=os.environ.get("FLUJO_LOG", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="accion", required=True)
    pub = sub.add_parser("publicar", help="Job diario: calcula y publica una versión nueva")
    pub.add_argument("--datos", help="Usar este archivo JSON en lugar de entrada/movimientos.json")
    pub.add_argument("--forzar", action="store_true", help="Publicar aunque las series no hayan cambiado")
    pub.add_argument("--conservar", type=int, default=30, help="Versiones que se conservan (las demás se borran)")
    pub.add_argument("--resultados", help="Carpeta results/ con un backtest de las mismas series para reutilizarlo "
                                          "(sirve para publicar la primera versión sin recalcular)")
    ent = sub.add_parser("subir-entrada", help="Valida un JSON de movimientos y lo deja para el job")
    ent.add_argument("archivo")
    sub.add_parser("vigente", help="Muestra la versión vigente")
    sub.add_parser("listar", help="Lista las versiones publicadas")
    rev = sub.add_parser("revertir", help="Apunta a una versión anterior")
    rev.add_argument("version")
    pod = sub.add_parser("podar", help="Borra versiones viejas (nunca la vigente)")
    pod.add_argument("--conservar", type=int, default=30)
    args = p.parse_args(argv)

    if args.accion == "publicar":
        return publicar(args)
    if args.accion == "subir-entrada":
        return subir_entrada(args)
    almacen = A.abrir()
    if almacen is None:
        raise SystemExit("Define FLUJO_ALMACEN.")
    if args.accion == "vigente":
        print(json.dumps(A.vigente(almacen), ensure_ascii=False, indent=1))
    elif args.accion == "listar":
        actual = (A.vigente(almacen) or {}).get("version")
        for v in A.versiones(almacen):
            print(("* " if v == actual else "  ") + v)
    elif args.accion == "revertir":
        print(json.dumps(A.revertir(almacen, args.version), ensure_ascii=False, indent=1))
    elif args.accion == "podar":
        print(json.dumps({"borradas": A.podar(almacen, args.conservar)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
