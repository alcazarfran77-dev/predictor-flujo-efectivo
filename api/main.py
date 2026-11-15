"""API del predictor de flujo de efectivo y servidor del dashboard.

Arranque (desde la raíz del repositorio):
    uvicorn api.main:app --port 8000

Variables de entorno opcionales:
    FLUJO_DATOS          archivo JSON de movimientos (por omisión, data/movimientos_feban_sep2025_sep2026_anon.json)
    FLUJO_SALIDAS        carpeta donde se escribe el reporte JSON (por omisión, salidas/)
    FLUJO_MODELO         modelo recomendado (ETS)
    FLUJO_CHRONOS        1 para cargar Chronos-2 y Chronos-Bolt Small (requiere acceso a Hugging Face o pesos locales)
    FLUJO_CHRONOS2       repositorio o carpeta de Chronos-2
    FLUJO_CHRONOS_BOLT   repositorio o carpeta de Chronos-Bolt Small
    FLUJO_CORS           orígenes permitidos separados por coma (por omisión, *)
    FLUJO_UMBRAL_DEFICIT umbral de déficit en MXN (0)

Las variables de observabilidad (logs, alertas, respuesta automática) están en api/observabilidad.py.
"""
import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Optional

from typing import Any

from fastapi import APIRouter, Body, FastAPI, Header, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from . import VERSION
from . import esquemas as E
from . import observabilidad as O
from ._rutas import RAIZ
from .servicio import SERIE_PRINCIPAL, ErrorServicio, ServicioPronostico, nombre_archivo
from flujo_efectivo.validacion import DatosInvalidos, validar_movimientos

log = logging.getLogger("flujo.api")
logging.basicConfig(level=os.environ.get("FLUJO_LOG", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
O.configurar_logs()


def crear_servicio():
    repos = {}
    if os.environ.get("FLUJO_CHRONOS2"):
        repos["Chronos-2"] = os.environ["FLUJO_CHRONOS2"]
    if os.environ.get("FLUJO_CHRONOS_BOLT"):
        repos["Chronos-Bolt Small"] = os.environ["FLUJO_CHRONOS_BOLT"]
    return ServicioPronostico(
        ruta_datos=os.environ.get("FLUJO_DATOS"),
        dir_salidas=os.environ.get("FLUJO_SALIDAS"),
        modelo_recomendado=os.environ.get("FLUJO_MODELO", "ETS"),
        usar_chronos=os.environ.get("FLUJO_CHRONOS", "0") == "1",
        repos_hf=repos or None,
        umbral_deficit=float(os.environ.get("FLUJO_UMBRAL_DEFICIT", "0")),
    )


@asynccontextmanager
async def ciclo_de_vida(app: FastAPI):
    if getattr(app.state, "servicio", None) is None:
        app.state.servicio = crear_servicio()
    if getattr(app.state, "obs", None) is None:
        app.state.obs = O.Observabilidad()
    if not app.state.servicio.listo:
        try:
            app.state.servicio.iniciar()
        except DatosInvalidos as e:
            log.error("No se puede arrancar: %s\n%s", app.state.servicio.ruta_datos, e)
            raise
    yield


app = FastAPI(
    title="API del predictor de flujo de efectivo",
    version=VERSION,
    description=("Pronóstico diario de flujo de efectivo con cuantiles P10, P50 y P90, alerta de déficit y "
                 "métricas de confianza del backtest walk-forward. Montos en pesos mexicanos (MXN)."),
    lifespan=ciclo_de_vida,
)
origenes = [o.strip() for o in os.environ.get("FLUJO_CORS", "*").split(",") if o.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origenes, allow_methods=["GET", "POST"], allow_headers=["*"],
                   expose_headers=["Server-Timing", "X-Version-API", "traceparent"])


@app.middleware("http")
async def medir_tiempo(request: Request, call_next):
    """Mide la petición, le pone traza, la registra en las métricas y escribe su línea de log."""
    obs = getattr(request.app.state, "obs", None)
    trace_id, span_id = O.nueva_traza(request.headers.get("traceparent"))
    traza = O.iniciar_traza(trace_id, span_id)
    t0 = time.perf_counter()
    falla, espera = obs.falla_inyectada() if obs and request.url.path.startswith("/api/v1/") \
        and not request.url.path.startswith(O.RUTAS_SIN_SLI) else (False, 0)
    try:
        if espera:
            with O.tramo("espera_inyectada"):                  # el simulacro queda visible en la traza
                await asyncio.sleep(espera)
        if falla:
            respuesta = JSONResponse(status_code=500, content={"detail": "Falla inyectada por el simulacro."})
        else:
            respuesta = await call_next(request)
    except Exception:
        duracion = time.perf_counter() - t0
        ruta = getattr(request.scope.get("route"), "path", None) or "sin ruta"
        if obs:
            obs.registrar(ruta, request.method, 500, duracion)
        O.log.exception("Error no controlado", extra={"datos": {"metodo": request.method, "ruta": ruta, "estado": 500,
                                                               "duracion_ms": round(duracion * 1000, 2)}})
        raise
    duracion = time.perf_counter() - t0
    plantilla = getattr(request.scope.get("route"), "path", None)
    ruta = plantilla if plantilla and plantilla != "/" else ("estático" if respuesta.status_code < 400 and not plantilla
                                                             or plantilla == "/" else "no encontrada")
    if falla:
        ruta = request.url.path
    if obs:
        obs.registrar(ruta, request.method, respuesta.status_code, duracion)
    tramos = "".join(f', {t["nombre"]};dur={t["ms"]}' for t in traza["tramos"])
    respuesta.headers["Server-Timing"] = f"app;dur={duracion * 1000:.1f}{tramos}"
    respuesta.headers["X-Version-API"] = VERSION
    respuesta.headers["traceparent"] = f"00-{trace_id}-{span_id}-01"
    if ruta != "estático":
        nivel = logging.ERROR if respuesta.status_code >= 500 else logging.WARNING if respuesta.status_code >= 400 else logging.INFO
        O.log.log(nivel, "%s %s %s", request.method, request.url.path, respuesta.status_code, extra={"datos": {
            "metodo": request.method, "ruta": ruta, "estado": respuesta.status_code, "duracion_ms": round(duracion * 1000, 2),
            **traza["campos"], "tramos": traza["tramos"], "falla_inyectada": falla or None,
            "latencia_inyectada_ms": round(espera * 1000) or None}})
    return respuesta


@app.exception_handler(ErrorServicio)
async def error_servicio(request: Request, exc: ErrorServicio):
    return JSONResponse(status_code=exc.codigo, content={"detail": exc.mensaje})


def servicio(request: Request) -> ServicioPronostico:
    return request.app.state.servicio


api = APIRouter(prefix="/api/v1")


@api.get("/salud", response_model=E.Salud, summary="Estado del servicio, datos cargados y modelos")
def salud(request: Request):
    s = servicio(request)
    from flujo_efectivo.modelos import FAMILIA
    return {
        "estado": "ok" if s.listo else "iniciando", "version": VERSION, "datos": s.info_datos(),
        "modelos": [{"nombre": m, "familia": FAMILIA.get(m, ""), "backtest": s.estado_backtest.get(m, "pendiente")}
                    for m in s.modelos],
        "modelo_recomendado": s.modelo_recomendado, "serie_principal": SERIE_PRINCIPAL,
        "horizonte_maximo": s.cfg.horizonte_final, "umbral_deficit": s.umbral_deficit,
        "fechas_pronostico": s.fechas_siguientes(),
        "archivo_salida": s.ruta_relativa(s.ultimo_archivo) if s.ultimo_archivo else None,
    }


@api.get("/series", response_model=list[E.Serie], summary="Series diarias disponibles")
def series(request: Request):
    return servicio(request).series()


@api.get("/series/{serie}/historia", response_model=E.Historia, summary="Valores diarios observados")
def historia(request: Request, serie: str, dias: int = Query(60, ge=5, le=400)):
    s = servicio(request)
    s.validar(serie=serie)
    return {"serie": serie, "puntos": s.historia(serie, dias)}


@api.get("/pronostico", response_model=E.Pronostico,
         summary="P10, P50 y P90 por día, alerta de déficit y confianza de una serie")
def pronostico(request: Request, serie: str = SERIE_PRINCIPAL, modelo: Optional[str] = None,
               horizonte: int = Query(5, ge=1, le=5)):
    s = servicio(request)
    O.anotar(modelo=modelo or s.modelo_recomendado, serie=serie, horizonte=horizonte)
    return s.pronostico(serie, modelo or s.modelo_recomendado, horizonte)


@api.post("/pronostico", response_model=E.Pronostico,
          summary="Pronostica una serie enviada por otro sistema (sin métricas de backtest)")
def pronostico_externo(request: Request, peticion: E.PeticionPronostico):
    s = servicio(request)
    O.anotar(modelo=peticion.modelo, serie=peticion.serie_id, horizonte=peticion.horizonte, puntos_historia=len(peticion.historia))
    return s.pronostico_externo([p.model_dump() for p in peticion.historia], peticion.modelo,
                                peticion.horizonte, peticion.serie_id)


@api.get("/alertas", response_model=list[E.AlertaSerie], summary="Alerta de déficit de todas las series")
def alertas(request: Request, modelo: Optional[str] = None):
    s = servicio(request)
    return s.alertas_series(modelo or s.modelo_recomendado)


@api.get("/metricas", response_model=E.Metricas, summary="Métricas de confianza, umbrales y monitoreo",
         responses={503: {"description": "El backtest del modelo todavía se está calculando"}})
def metricas(request: Request, modelo: Optional[str] = None, serie: Optional[str] = SERIE_PRINCIPAL):
    s = servicio(request)
    modelo = modelo or s.modelo_recomendado
    m = s.metricas_confianza(modelo, serie)
    if m is None:
        return JSONResponse(status_code=503, headers={"Retry-After": "30"},
                            content={"detail": f"El backtest de {modelo} está en estado "
                                               f"'{s.estado_backtest.get(modelo)}'. Intenta de nuevo en unos segundos."})
    return m


@api.get("/modelos", response_model=list[E.ComparacionModelo],
         summary="Modelos lado a lado con los criterios de aceptación y de cambio de modelo")
def modelos(request: Request):
    return servicio(request).comparacion()


@api.get("/comparacion", response_model=E.ComparacionEstadistica,
         summary="¿Algún modelo mejora de forma significativa al recomendado? Incluye Chronos si se evaluó")
def comparacion(request: Request):
    return servicio(request).comparacion_estadistica()


@api.get("/reporte", response_model=E.Reporte, response_model_exclude_unset=True,
         summary="Pronóstico completo de un modelo (todas las series, alertas y confianza) en un solo JSON")
def reporte(request: Request, modelo: Optional[str] = None,
            descargar: bool = Query(False, description="true = responde como archivo para guardar")):
    s = servicio(request)
    rep = s.reporte(modelo)
    if not descargar:
        return rep
    nombre = nombre_archivo(rep["modelo"], rep["series"][0]["origen"])
    return JSONResponse(rep, headers={"Content-Disposition": f'attachment; filename="{nombre}"'})


@api.post("/reporte", response_model=E.ArchivoGenerado, status_code=201,
          summary="Escribe el reporte JSON en la carpeta de salidas del servidor")
def guardar_reporte(request: Request, modelo: Optional[str] = None):
    s = servicio(request)
    ruta, rep = s.guardar_reporte(modelo)
    return {"archivo": s.ruta_relativa(ruta), "nombre": ruta.name, "bytes": ruta.stat().st_size,
            "modelo": rep["modelo"], "generado": rep["generado"], "series": len(rep["series"])}


@api.post("/datos/validar", response_model=E.Validacion,
          summary="Revisa un JSON de movimientos sin cargarlo: errores por movimiento y campo, y avisos")
def validar_datos(request: Request, contenido: Any = Body(..., description="Objeto con 'movimientos' o lista de movimientos")):
    r = validar_movimientos(contenido, servicio(request).cfg)
    return r.a_dict()


@api.get("/datos/validacion", response_model=E.Validacion, summary="Resultado de la validación del archivo cargado")
def validacion_cargada(request: Request):
    return servicio(request).resultado_validacion()


# ---------------------------------------------------------------- observabilidad y administración
def observador(request: Request) -> O.Observabilidad:
    return request.app.state.obs


def exigir_admin(token):
    esperado = os.environ.get("FLUJO_TOKEN_ADMIN")
    if not esperado:
        raise ErrorServicio(403, "Los endpoints de administración están deshabilitados: define FLUJO_TOKEN_ADMIN.")
    if token != esperado:
        raise ErrorServicio(401, "Token de administración inválido.")


@api.get("/observabilidad", summary="SLO, presupuestos de error, alertas, drift, tráfico y respuesta automática")
def observabilidad(request: Request):
    return O.resumen(observador(request), servicio(request), SERIE_PRINCIPAL)


@api.get("/observabilidad/eventos", summary="Bitácora de alertas activadas y resueltas y de acciones automáticas")
def eventos(request: Request, limite: int = Query(100, ge=1, le=400)):
    return list(observador(request).eventos)[:limite]


@api.post("/datos/recargar", summary="Carga otro JSON de movimientos; si no pasa la validación, conserva los datos actuales")
def recargar_datos(request: Request, peticion: dict = Body(..., examples=[{"archivo": "movimientos.json"}]),
                   x_token_admin: Optional[str] = Header(None)):
    exigir_admin(x_token_admin)
    obs, actual = observador(request), servicio(request)
    nombre = str(peticion.get("archivo", ""))
    ruta = (actual.ruta_datos.parent / nombre).resolve()
    if not nombre or ruta.parent != actual.ruta_datos.parent.resolve() or ruta.suffix.lower() != ".json":
        raise ErrorServicio(422, "Indica el nombre de un archivo .json de la misma carpeta que los datos actuales.")
    nuevo = ServicioPronostico(ruta_datos=ruta, modelo_recomendado=getattr(actual, "modelo_original", None) or actual.modelo_recomendado,
                               dir_resultados=actual.dir_resultados, dir_cache=actual.dir_cache, dir_salidas=actual.dir_salidas,
                               umbral_deficit=actual.umbral_deficit, escribir_salida=actual.escribir_salida,
                               calcular_faltantes=actual.calcular_faltantes, precalentar=actual.precalentar)
    try:
        with O.tramo("validar_y_cargar"):
            nuevo.iniciar()
    except (DatosInvalidos, FileNotFoundError) as e:
        detalle = str(e).splitlines()
        obs.carga_rechazada = {"archivo": nombre, "momento": O._ahora_iso(), "errores": detalle[:6]}
        obs.evento("carga_rechazada", f"Se rechazó {nombre}; la API conserva {actual.ruta_datos.name}.", "P2",
                   accion="conservar_datos_anteriores", archivo=nombre)
        return JSONResponse(status_code=422, content={"detail": "El archivo no pasó la validación; se conservan los datos anteriores.",
                                                      "aceptado": False, "errores": detalle[:20], "datos_en_uso": actual.ruta_datos.name})
    request.app.state.servicio = nuevo
    obs.carga_rechazada = None
    obs.evento("datos_recargados", f"Se cargó {nombre}: {len(nuevo.datos.mov):,} movimientos hasta el {nuevo.datos.fechas[-1]:%d-%m-%Y}. "
               "El backtest se recalcula en segundo plano si los datos cambiaron.", accion="recalcular_backtest", archivo=nombre)
    return {"aceptado": True, "datos_en_uso": nombre, "movimientos": len(nuevo.datos.mov), "hasta": f"{nuevo.datos.fechas[-1]:%Y-%m-%d}",
            "backtest": dict(nuevo.estado_backtest)}


@api.post("/observabilidad/simulacro", summary="Simulacro: inyecta errores y latencia para probar las alertas (requiere FLUJO_CAOS=1)")
def simulacro(request: Request, peticion: dict = Body(..., examples=[{"error": 0.3, "latencia_ms": 0}]),
              x_token_admin: Optional[str] = Header(None)):
    exigir_admin(x_token_admin)
    if os.environ.get("FLUJO_CAOS") != "1":
        raise ErrorServicio(403, "La inyección de fallas está deshabilitada: define FLUJO_CAOS=1.")
    obs = observador(request)
    obs.caos = {"error": min(1.0, max(0.0, float(peticion.get("error", 0)))),
                "latencia_ms": min(10_000.0, max(0.0, float(peticion.get("latencia_ms", 0))))}
    obs.evento("simulacro", f"Simulacro: {obs.caos['error'] * 100:.0f} % de errores y {obs.caos['latencia_ms']:.0f} ms de latencia extra.")
    return obs.caos


@app.get("/metrics", response_class=PlainTextResponse, include_in_schema=False)
def metricas_prometheus(request: Request):
    obs, s = observador(request), servicio(request)
    res = O.resumen(obs, s, SERIE_PRINCIPAL)
    return PlainTextResponse(obs.texto_prometheus(O.medidores(res, s)), media_type="text/plain; version=0.0.4; charset=utf-8")


app.include_router(api)
app.mount("/", StaticFiles(directory=RAIZ / "frontend", html=True), name="frontend")
