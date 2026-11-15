"""Observabilidad del predictor: métricas, logs, trazas, SLO con presupuesto de error, alertas y respuesta automática.

- Métricas: contadores e histogramas de las peticiones y medidores del modelo y de los datos, en formato Prometheus
  (`GET /metrics`) y resumidos en `GET /api/v1/observabilidad`.
- Logs: una línea JSON por petición, con el identificador de traza, la ruta, el estado, la duración y los tramos.
- Trazas: identificador W3C `traceparent` (se respeta el que llega o se genera uno) y tramos medidos dentro de la API.
- SLO: disponibilidad, latencia, frescura de datos, calidad y calibración del modelo, cada uno con su presupuesto.
- Alertas: tasas de consumo del presupuesto en dos ventanas, prioridad por impacto operativo, inhibición y runbook.
- Respuesta automática: conmutar al modelo de respaldo y restaurarlo; el rechazo de cargas inválidas vive en main.py.

Variables de entorno:
    FLUJO_LOG_FORMATO       json (por omisión) o texto
    FLUJO_FECHA_REFERENCIA  fecha contra la que se mide el atraso de los datos (por omisión, hoy)
    FLUJO_RESPUESTA_AUTO    off, recomendar (por omisión) o aplicar
    FLUJO_OBS_ACELERAR      divide las ventanas de las alertas (solo demostraciones; por omisión 1)
    FLUJO_TOKEN_ADMIN       token de los endpoints de administración; sin él quedan deshabilitados
    FLUJO_CAOS              1 para permitir la inyección de fallas en simulacros
"""
import contextvars
import json
import logging
import os
import random
import secrets
import threading
import time
from collections import Counter, defaultdict, deque
from contextlib import contextmanager
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from . import VERSION, confianza
from .config_alertas import (ALERTAS, CONSUMO_LENTO, CONSUMO_RAPIDO, DIAS_CALIDAD, ESTADOS_SIN_ERROR, HISTERESIS_RMAE, INHIBICIONES,
                             MAX_ATRASO_DATOS, MIN_PETICIONES, OBJ_CALIDAD, OBJ_DISPONIBILIDAD, OBJ_LATENCIA, P1, P2, P3, P4,
                             PRESUPUESTO_AGOTADO, PRESUPUESTO_EN_RIESGO, PRIORIDADES, PRIORIDADES_QUE_NOTIFICAN, RUNBOOKS,
                             RUTAS_SIN_SLI, SLOS, UMBRAL_LATENCIA, VENTANA_SLO_DIAS, VENTANAS)

log = logging.getLogger("flujo.acceso")
NAIVE = confianza.NAIVE
BUCKETS = (0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
# ---------------------------------------------------------------- trazas y logs
_traza = contextvars.ContextVar("traza", default=None)


def nueva_traza(traceparent=None):
    """Devuelve (trace_id, span_id). Respeta un `traceparent` W3C válido; si no llega, genera uno."""
    trace_id = None
    if traceparent:
        partes = traceparent.strip().split("-")
        if len(partes) == 4 and len(partes[1]) == 32 and len(partes[2]) == 16 and partes[1] != "0" * 32:
            try:
                int(partes[1], 16)
                trace_id = partes[1].lower()
            except ValueError:
                trace_id = None
    return trace_id or secrets.token_hex(16), secrets.token_hex(8)


def iniciar_traza(trace_id, span_id):
    datos = {"trace_id": trace_id, "span_id": span_id, "tramos": [], "campos": {}}
    _traza.set(datos)
    return datos


def traza_actual():
    return _traza.get()


@contextmanager
def tramo(nombre, **campos):
    """Mide un tramo de trabajo dentro de la petición en curso. Fuera de una petición no hace nada."""
    datos = _traza.get()
    t0 = time.perf_counter()
    try:
        yield
    finally:
        ms = (time.perf_counter() - t0) * 1000
        if datos is not None and ms >= 1:                     # los tramos de menos de 1 ms solo hacen ruido
            datos["tramos"].append({"nombre": nombre, "ms": round(ms, 2), **campos})


def anotar(**campos):
    """Agrega campos de negocio (modelo, serie, horizonte) al log de la petición en curso."""
    datos = _traza.get()
    if datos is not None:
        datos["campos"].update({k: v for k, v in campos.items() if v is not None})


class FormatoJSON(logging.Formatter):
    """Una línea JSON por registro. Los campos extra de `log.info(..., extra={"datos": {...}})` van al mismo nivel."""

    def format(self, record):
        linea = {"ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
                 "nivel": record.levelname, "logger": record.name, "mensaje": record.getMessage()}
        datos = _traza.get()
        if datos is not None:
            linea["trace_id"], linea["span_id"] = datos["trace_id"], datos["span_id"]
        linea.update(getattr(record, "datos", None) or {})
        if record.exc_info:
            linea["excepcion"] = self.formatException(record.exc_info)
        return json.dumps(linea, ensure_ascii=False, default=str)


def configurar_logs():
    nivel = os.environ.get("FLUJO_LOG", "INFO")
    manejador = logging.StreamHandler()
    if os.environ.get("FLUJO_LOG_FORMATO", "json").lower() == "json":
        manejador.setFormatter(FormatoJSON())
    else:
        manejador.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    raiz = logging.getLogger("flujo")
    raiz.handlers[:] = [manejador]
    raiz.setLevel(nivel)
    raiz.propagate = False


def _ahora_iso(t=None):
    return datetime.fromtimestamp(t or time.time(), timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------- registro de peticiones y eventos
class Observabilidad:
    def __init__(self, acelerar=None, fecha_referencia=None, modo_respuesta=None):
        self.inicio = time.time()
        self.acelerar = max(1.0, float(acelerar or os.environ.get("FLUJO_OBS_ACELERAR", "1") or 1))
        self.fecha_referencia = fecha_referencia or os.environ.get("FLUJO_FECHA_REFERENCIA") or None
        self.modo_respuesta = (modo_respuesta or os.environ.get("FLUJO_RESPUESTA_AUTO", "recomendar")).lower()
        self.peticiones = deque(maxlen=500_000)            # (t, ruta, estado, duración, cuenta para el SLI)
        self.contadores = Counter()                        # (ruta, método, estado)
        self.histograma = defaultdict(lambda: [0] * (len(BUCKETS) + 1))
        self.suma_duracion = Counter()
        self.eventos = deque(maxlen=400)
        self.alertas_desde = {}                            # id de alerta -> momento en que se activó
        self.caos = {"error": 0.0, "latencia_ms": 0.0}
        self.carga_rechazada = None                        # última carga de datos rechazada
        self.acciones = {}                                 # id de acción -> detalle de la última evaluación
        self._candado = threading.Lock()
        self._drift = None                                 # (huella, resultado)

    # ---- peticiones
    def registrar(self, ruta, metodo, estado, duracion):
        cuenta = ruta.startswith("/api/") and not ruta.startswith(RUTAS_SIN_SLI)
        with self._candado:
            self.peticiones.append((time.time(), ruta, estado, duracion, cuenta))
            self.contadores[(ruta, metodo, estado)] += 1
            cubetas = self.histograma[ruta]
            for i, limite in enumerate(BUCKETS):
                if duracion <= limite:
                    cubetas[i] += 1
            cubetas[-1] += 1
            self.suma_duracion[ruta] += duracion

    def evento(self, tipo, mensaje, prioridad=None, **datos):
        e = {"momento": _ahora_iso(), "tipo": tipo, "mensaje": mensaje, "prioridad": prioridad, **datos}
        self.eventos.appendleft(e)
        logging.getLogger("flujo.eventos").info(mensaje, extra={"datos": {"evento": tipo, "prioridad": prioridad, **datos}})
        return e

    def falla_inyectada(self):
        """Simulacro: decide si esta petición falla y cuánta latencia se le agrega."""
        c = self.caos
        return (c["error"] > 0 and random.random() < c["error"]), c["latencia_ms"] / 1000

    # ---- indicadores de tráfico
    def _sli(self, desde=None):
        with self._candado:
            datos = [p for p in self.peticiones if p[4] and (desde is None or p[0] >= desde)]
        return datos

    def ventana(self, segundos=None):
        ahora = time.time()
        datos = self._sli(None if segundos is None else ahora - segundos / self.acelerar)
        n = len(datos)
        errores = sum(1 for p in datos if p[2] >= 500 and p[2] not in ESTADOS_SIN_ERROR)
        lentas = sum(1 for p in datos if p[3] > UMBRAL_LATENCIA)
        duraciones = np.array([p[3] for p in datos]) if n else np.array([])
        lapso = (segundos / self.acelerar) if segundos else max(ahora - self.inicio, 1)
        return {"peticiones": n, "errores_5xx": errores, "lentas": lentas,
                "tasa_error": errores / n if n else 0.0, "tasa_lentas": lentas / n if n else 0.0,
                "p50_ms": float(np.percentile(duraciones, 50) * 1000) if n else None,
                "p95_ms": float(np.percentile(duraciones, 95) * 1000) if n else None,
                "por_segundo": n / lapso}

    def serie_trafico(self, cubetas=40):
        """Peticiones, errores y p95 por intervalo de la última hora (acelerada), para las gráficas."""
        ahora = time.time()
        ancho = VENTANAS["1h"] / self.acelerar / cubetas
        datos = self._sli(ahora - ancho * cubetas)
        filas = [{"momento": _ahora_iso(ahora - ancho * (cubetas - i - 1)), "peticiones": 0, "errores": 0, "d": []}
                 for i in range(cubetas)]
        for t, _, estado, dur, _ in datos:
            i = min(cubetas - 1, max(0, int((t - (ahora - ancho * cubetas)) / ancho)))
            filas[i]["peticiones"] += 1
            filas[i]["errores"] += estado >= 500 and estado not in ESTADOS_SIN_ERROR
            filas[i]["d"].append(dur)
        for f in filas:
            d = f.pop("d")
            f["p95_ms"] = float(np.percentile(d, 95) * 1000) if d else None
        return {"segundos_por_intervalo": ancho, "intervalos": filas}

    # ---- Prometheus
    def texto_prometheus(self, medidores):
        def etiquetas(d):
            return "{" + ",".join(f'{k}="{str(v).replace(chr(92), "").replace(chr(34), "")}"' for k, v in d.items()) + "}" if d else ""

        lineas = ["# HELP flujo_http_peticiones_total Peticiones atendidas por ruta, método y estado.",
                  "# TYPE flujo_http_peticiones_total counter"]
        with self._candado:
            contadores, histograma, suma = dict(self.contadores), {k: list(v) for k, v in self.histograma.items()}, dict(self.suma_duracion)
        for (ruta, metodo, estado), n in sorted(contadores.items()):
            lineas.append(f"flujo_http_peticiones_total{etiquetas({'ruta': ruta, 'metodo': metodo, 'estado': estado})} {n}")
        lineas += ["# HELP flujo_http_duracion_segundos Duración de las peticiones.", "# TYPE flujo_http_duracion_segundos histogram"]
        for ruta, cubetas in sorted(histograma.items()):
            for limite, n in zip(BUCKETS, cubetas):
                lineas.append(f"flujo_http_duracion_segundos_bucket{etiquetas({'ruta': ruta, 'le': limite})} {n}")
            lineas.append(f"flujo_http_duracion_segundos_bucket{etiquetas({'ruta': ruta, 'le': '+Inf'})} {cubetas[-1]}")
            lineas.append(f"flujo_http_duracion_segundos_sum{etiquetas({'ruta': ruta})} {suma.get(ruta, 0):.6f}")
            lineas.append(f"flujo_http_duracion_segundos_count{etiquetas({'ruta': ruta})} {cubetas[-1]}")
        vistos = set()
        for nombre, ayuda, etq, valor in medidores:
            if valor is None:
                continue
            if nombre not in vistos:
                lineas += [f"# HELP {nombre} {ayuda}", f"# TYPE {nombre} gauge"]
                vistos.add(nombre)
            lineas.append(f"{nombre}{etiquetas(etq)} {float(valor):.6g}")
        return "\n".join(lineas) + "\n"


# ---------------------------------------------------------------- estado del modelo y de los datos
def dias_habiles_de_atraso(hasta, referencia=None):
    """Días hábiles completos entre el último dato y la fecha de referencia, sin contar la propia referencia."""
    ref = pd.Timestamp(referencia).normalize() if referencia else pd.Timestamp.now().normalize()
    ultimo = pd.Timestamp(hasta).normalize()
    if ref <= ultimo:
        return 0
    return len(pd.bdate_range(ultimo + pd.Timedelta(days=1), ref - pd.Timedelta(days=1)))


def cumplimiento(serie, dentro, dias=DIAS_CALIDAD):
    """Fracción de los últimos `dias` puntos de una serie de monitoreo que cumplen `dentro(valor)`."""
    valores = [p["valor"] for p in serie][-dias:]
    if not valores:
        return None, 0
    return sum(1 for v in valores if dentro(v)) / len(valores), len(valores)


def estado_modelo(servicio, serie_principal):
    """Monitoreo del modelo recomendado y de los demás modelos servidos, sobre la serie principal."""
    modelos = {}
    for m in servicio.modelos:
        if servicio.estado_backtest.get(m) != "listo" or servicio.estado_backtest.get(NAIVE) != "listo":
            modelos[m] = {"backtest": servicio.estado_backtest.get(m, "pendiente")}
            continue
        c = servicio.metricas_confianza(m, serie_principal)
        mon = c["monitoreo"]
        modelos[m] = {"backtest": "listo", "rmae_movil": mon["rmae_movil"]["valor"], "alarma_rmae": mon["rmae_movil"]["alerta"],
                      "cobertura_movil": mon["cobertura_movil"]["valor"], "alarma_cobertura": mon["cobertura_movil"]["alerta"],
                      "nivel_confianza": c["nivel_confianza"], "serie_rmae": mon["rmae_movil"]["serie"],
                      "serie_cobertura": mon["cobertura_movil"]["serie"],
                      "umbrales_cumplidos": sum(1 for u in c["umbrales"] if u["cumple"]), "umbrales_total": len(c["umbrales"])}
    return modelos


def drift_de(obs, servicio):
    from flujo_efectivo import drift
    if obs._drift is None or obs._drift[0] != servicio.huella_archivo:
        obs._drift = (servicio.huella_archivo, drift.resumen(servicio.datos.series, servicio.datos.mov))
    return obs._drift[1]


# ---------------------------------------------------------------- SLO y presupuestos de error
def calcular_slos(obs, servicio, modelos):
    total = obs.ventana(None)
    ventanas = {k: obs.ventana(s) for k, s in VENTANAS.items()}
    slos = []

    def de_trafico(clave, nombre, objetivo, malas, tasa, descripcion):
        permitido = 1 - objetivo
        n = total["peticiones"]
        consumido = (total[malas] / (n * permitido)) if n else 0.0
        slos.append({
            "id": clave, "nombre": nombre, "objetivo_pct": objetivo * 100, "ventana": f"{VENTANA_SLO_DIAS} días",
            "descripcion": descripcion, "sli_pct": (1 - total[tasa]) * 100 if n else None, "eventos": n, "malos": total[malas],
            "presupuesto_pct": permitido * 100, "presupuesto_eventos": n * permitido,
            "presupuesto_consumido_pct": consumido * 100, "presupuesto_restante_pct": max(0.0, 1 - consumido) * 100,
            "tasas_consumo": {k: (v[tasa] / permitido if v["peticiones"] else 0.0) for k, v in ventanas.items()},
            "peticiones_por_ventana": {k: v["peticiones"] for k, v in ventanas.items()},
            "estado": "sin tráfico" if not n else "agotado" if consumido >= PRESUPUESTO_AGOTADO else "en riesgo" if consumido >= PRESUPUESTO_EN_RIESGO else "cumple",
        })

    de_trafico("disponibilidad", SLOS["disponibilidad"]["nombre"], OBJ_DISPONIBILIDAD, "errores_5xx", "tasa_error",
               SLOS["disponibilidad"]["indicador"])
    de_trafico("latencia", SLOS["latencia"]["nombre"], OBJ_LATENCIA, "lentas", "tasa_lentas", SLOS["latencia"]["indicador"])

    atraso = dias_habiles_de_atraso(servicio.datos.fechas[-1], obs.fecha_referencia)
    slos.append({"id": "frescura", "nombre": SLOS["frescura"]["nombre"], "objetivo_pct": SLOS["frescura"]["objetivo"] * 100,
                 "ventana": SLOS["frescura"]["ventana"], "descripcion": SLOS["frescura"]["indicador"],
                 "valor": atraso, "unidad": "días hábiles de atraso", "limite": MAX_ATRASO_DATOS,
                 "estado": "cumple" if atraso <= MAX_ATRASO_DATOS else "incumple"})

    rec = modelos.get(servicio.modelo_recomendado, {})
    for clave, nombre, serie, dentro, descripcion in (
        ("calidad", SLOS["calidad"]["nombre"], rec.get("serie_rmae"), lambda v: v <= confianza.LIMITE_RMAE,
         SLOS["calidad"]["indicador"]),
        ("calibracion", SLOS["calibracion"]["nombre"], rec.get("serie_cobertura"),
         lambda v: confianza.COBERTURA_MIN <= v <= confianza.COBERTURA_MAX, SLOS["calibracion"]["indicador"]),
    ):
        if not serie:
            slos.append({"id": clave, "nombre": nombre, "objetivo_pct": OBJ_CALIDAD * 100, "ventana": f"{DIAS_CALIDAD} días hábiles",
                         "descripcion": descripcion, "estado": "sin datos"})
            continue
        fraccion, n = cumplimiento(serie, dentro)
        permitido = 1 - OBJ_CALIDAD
        consumido = (1 - fraccion) / permitido
        slos.append({"id": clave, "nombre": nombre, "objetivo_pct": OBJ_CALIDAD * 100, "ventana": f"{DIAS_CALIDAD} días hábiles",
                     "descripcion": descripcion, "sli_pct": fraccion * 100, "eventos": n, "malos": round((1 - fraccion) * n),
                     "presupuesto_pct": permitido * 100, "presupuesto_eventos": n * permitido,
                     "presupuesto_consumido_pct": consumido * 100, "presupuesto_restante_pct": max(0.0, 1 - consumido) * 100,
                     "estado": "agotado" if consumido >= PRESUPUESTO_AGOTADO else "en riesgo" if consumido >= PRESUPUESTO_EN_RIESGO else "cumple"})
    return slos, ventanas


# ---------------------------------------------------------------- alertas
def evaluar_alertas(obs, servicio, slos, ventanas, modelos, drift, deficit, comparacion=None):
    """Lista de alertas activas, ordenadas por prioridad. Las inhibidas quedan visibles pero no notifican.

    El nombre, la prioridad, el impacto y el runbook de cada alerta salen del catálogo de `config_alertas.py`;
    aquí solo se decide si la condición se cumple y se arma el resumen con los valores del momento.
    """
    por_id = {s["id"]: s for s in slos}
    activas = []

    def alerta(clave, resumen, valor=None, umbral=None, prioridad=None):
        c = ALERTAS[clave]
        prioridad = prioridad or c["prioridad"]
        activas.append({"id": clave, "nombre": c["nombre"], "prioridad": prioridad, "resumen": resumen, "impacto": c["impacto"],
                        "runbook": c["runbook"], "runbook_nombre": RUNBOOKS[c["runbook"]], "valor": valor, "umbral": umbral,
                        "condicion": c["condicion"], "capa": c["capa"],
                        "atencion": PRIORIDADES[prioridad]["atencion"], "canal": PRIORIDADES[prioridad]["canal"]})

    def consumo(slo, regla):
        t, n = por_id[slo]["tasas_consumo"], por_id[slo]["peticiones_por_ventana"]
        return n[regla["corta"]] >= MIN_PETICIONES and t[regla["corta"]] >= regla["limite"] and t[regla["larga"]] >= regla["limite"]

    disp, lat = por_id["disponibilidad"], por_id["latencia"]
    corta, larga, lenta = CONSUMO_RAPIDO["corta"], CONSUMO_RAPIDO["larga"], CONSUMO_LENTO["larga"]
    if consumo("disponibilidad", CONSUMO_RAPIDO):
        alerta("api_consumo_rapido", f"{ventanas[corta]['tasa_error'] * 100:.1f} % de errores 5xx en la ventana corta; el presupuesto "
               f"se consume {disp['tasas_consumo'][larga]:.0f} veces más rápido de lo permitido.",
               disp["tasas_consumo"][larga], CONSUMO_RAPIDO["limite"])
    elif consumo("disponibilidad", CONSUMO_LENTO):
        alerta("api_consumo_lento", f"El presupuesto de disponibilidad se consume {disp['tasas_consumo'][lenta]:.1f} veces más "
               f"rápido de lo permitido.", disp["tasas_consumo"][lenta], CONSUMO_LENTO["limite"])
    if consumo("latencia", CONSUMO_RAPIDO):
        alerta("latencia_consumo_rapido", f"{ventanas[corta]['tasa_lentas'] * 100:.1f} % de las peticiones tarda más de "
               f"{UMBRAL_LATENCIA:g} s (p95 de {ventanas[corta]['p95_ms']:.0f} ms).", lat["tasas_consumo"][larga], CONSUMO_RAPIDO["limite"])
    elif consumo("latencia", CONSUMO_LENTO):
        alerta("latencia_consumo_lento", f"El presupuesto de latencia se consume {lat['tasas_consumo'][lenta]:.1f} veces más rápido "
               f"de lo permitido.", lat["tasas_consumo"][lenta], CONSUMO_LENTO["limite"])
    for s in (disp, lat):
        if s.get("presupuesto_consumido_pct", 0) >= PRESUPUESTO_AGOTADO * 100:
            alerta(f"presupuesto_agotado_{s['id']}", f"Se consumió {s['presupuesto_consumido_pct']:.0f} % del presupuesto de la ventana.",
                   s["presupuesto_consumido_pct"], 100)

    fres = por_id["frescura"]
    if fres["valor"] > MAX_ATRASO_DATOS:
        alerta("datos_atrasados", f"Los movimientos llegan hasta el {servicio.datos.fechas[-1]:%d-%m-%Y}: {fres['valor']} días hábiles "
               f"de atraso.", fres["valor"], MAX_ATRASO_DATOS)
    if obs.carga_rechazada:
        alerta("carga_rechazada", f"El archivo {obs.carga_rechazada['archivo']} no pasó la validación; la API sigue con los datos "
               f"anteriores.")
    if servicio.duplicados > 0:
        alerta("datos_duplicados", f"{servicio.duplicados} movimientos repetidos se descartaron al cargar.", servicio.duplicados, 0)

    if deficit and deficit["nivel"] in ("alta", "media"):
        clave = f"deficit_{deficit['nivel']}"
        alerta(clave, f"{deficit['serie']}: probabilidad máxima de déficit de {deficit['probabilidad_maxima'] * 100:.0f} %, primera "
               f"fecha {deficit['primera_fecha']}, colchón sugerido de {deficit['colchon_sugerido'] / 1e6:.2f} M MXN.",
               deficit["probabilidad_maxima"], ALERTAS[clave]["umbral"])

    rec = modelos.get(servicio.modelo_recomendado, {})
    hay_drift = drift["nivel"] == "significativo"
    if rec.get("alarma_rmae"):
        alerta("modelo_degradado", f"El rMAE móvil de {servicio.modelo_recomendado} lleva {confianza.DIAS_SEGUIDOS_RMAE} días sobre "
               f"{confianza.LIMITE_RMAE:.2f} (hoy {rec['rmae_movil']:.2f})" + (" y hay data drift significativo." if hay_drift else "."),
               rec["rmae_movil"], confianza.LIMITE_RMAE,
               prioridad=ALERTAS["modelo_degradado"]["prioridad_con_drift"] if hay_drift else None)
    if rec.get("alarma_cobertura"):
        alerta("cobertura_fuera_de_rango", f"La cobertura móvil del rango P10 a P90 es de {rec['cobertura_movil']:.0f} %, fuera de "
               f"{confianza.COBERTURA_MIN:.0f} a {confianza.COBERTURA_MAX:.0f} %.", rec["cobertura_movil"])
    for clave in ("calidad", "calibracion"):
        s = por_id[clave]
        if s.get("presupuesto_consumido_pct", 0) >= PRESUPUESTO_AGOTADO * 100:
            alerta(f"presupuesto_agotado_{clave}", f"Solo {s['sli_pct']:.0f} % de los últimos {s['eventos']} días cumple; el objetivo "
                   f"es {s['objetivo_pct']:.0f} %.", s["presupuesto_consumido_pct"], 100)
    for nivel in ("significativo", "moderado"):
        if drift["nivel"] == nivel:
            afectadas = [f["serie"] for f in drift["series"] if f["nivel"] == nivel] + \
                        [f["indicador"] for f in drift["composicion"] if f["nivel"] == nivel]
            alerta(f"drift_{nivel}", f"Los datos de las últimas 4 semanas cambiaron frente a su referencia en: {', '.join(afectadas)}."
                   if nivel == "significativo" else f"Cambio moderado en: {', '.join(afectadas)}.")

    estados = servicio.estado_backtest
    en_error = [m for m in servicio.modelos if estados.get(m) == "error"]
    calculando = [m for m in servicio.modelos if estados.get(m) in ("calculando", "pendiente")]
    if en_error:
        alerta("backtest_error", f"No se pudo calcular el backtest de: {', '.join(en_error)}.")
    if calculando:
        alerta("backtest_calculando", f"Se calcula el backtest de: {', '.join(calculando)}.")
    original = getattr(servicio, "modelo_original", None)
    if original and original != servicio.modelo_recomendado:
        alerta("modelo_conmutado", f"La API recomienda {servicio.modelo_recomendado} en lugar de {original}.")
    if comparacion and comparacion.get("disponible"):
        mejores = [f for f in comparacion["modelos"] if f["mejora_significativa"]]
        if mejores:
            mejor = max(mejores, key=lambda f: f["mejora_mae_pct"])
            alerta("retador_mejor", f"{mejor['modelo']} tiene {mejor['mejora_mae_pct']:.1f} % menos error que {comparacion['referencia']}.",
                   mejor["mejora_mae_pct"], comparacion["mejora_minima_pct"])

    # inhibición: una causa raíz activa silencia sus consecuencias
    ids = {a["id"] for a in activas}
    ahora = time.time()
    for a in activas:
        a["inhibida_por"] = next((causa for causa, efectos in INHIBICIONES.items() if causa in ids and a["id"] in efectos), None)
        a["notifica"] = a["inhibida_por"] is None and a["prioridad"] in PRIORIDADES_QUE_NOTIFICAN
        if a["id"] not in obs.alertas_desde:
            obs.alertas_desde[a["id"]] = ahora
            obs.evento("alerta_activada", f"{a['prioridad']} {a['nombre']}: {a['resumen']}", a["prioridad"], alerta=a["id"],
                       runbook=a["runbook"], notifica=a["notifica"])
        a["desde"] = _ahora_iso(obs.alertas_desde[a["id"]])
        a["minutos_activa"] = (ahora - obs.alertas_desde[a["id"]]) / 60
    for clave in [k for k in obs.alertas_desde if k not in ids]:
        duracion = ahora - obs.alertas_desde.pop(clave)
        obs.evento("alerta_resuelta", f"Se resolvió la alerta {clave} tras {duracion / 60:.1f} min.", alerta=clave)
    return sorted(activas, key=lambda a: (a["prioridad"], a["inhibida_por"] is not None, a["id"]))


# ---------------------------------------------------------------- respuesta automática
def respuesta_automatica(obs, servicio, alertas, modelos):
    """Decide las acciones automáticas. En modo `aplicar` las ejecuta; en `recomendar` solo las propone."""
    acciones = []
    if obs.modo_respuesta == "off":
        return acciones
    aplicar = obs.modo_respuesta == "aplicar"
    activas = {a["id"]: a for a in alertas if not a["inhibida_por"]}
    original = getattr(servicio, "modelo_original", None)
    recomendado = servicio.modelo_recomendado

    def registrar(clave, descripcion, ejecutada, **datos):
        accion = {"id": clave, "descripcion": descripcion, "ejecutada": ejecutada, "modo": obs.modo_respuesta, **datos}
        if obs.acciones.get(clave, {}).get("descripcion") != descripcion or obs.acciones.get(clave, {}).get("ejecutada") != ejecutada:
            obs.evento("accion_automatica" if ejecutada else "accion_recomendada", descripcion, accion=clave, **datos)
        obs.acciones[clave] = accion
        acciones.append(accion)

    if "modelo_degradado" in activas and not original:
        candidatos = [(v["rmae_movil"], m) for m, v in modelos.items()
                      if m not in (recomendado, NAIVE) and v.get("backtest") == "listo" and not v["alarma_rmae"]
                      and v["rmae_movil"] is not None and v["rmae_movil"] <= confianza.LIMITE_RMAE]
        if candidatos:
            _, respaldo = min(candidatos)
            if aplicar:
                servicio.modelo_original = recomendado
                servicio.modelo_recomendado = respaldo
                servicio._comparacion = None
            registrar("conmutar_modelo", f"Conmutar el modelo recomendado de {recomendado} a {respaldo} (rMAE móvil "
                      f"{modelos[respaldo]['rmae_movil']:.2f}) mientras {recomendado} está degradado.", aplicar,
                      de=recomendado, a=respaldo)
        else:
            registrar("sin_respaldo", f"{recomendado} está degradado y ningún modelo servido cumple el límite de rMAE: "
                      f"se escala a revisión manual (RB-04).", False, de=recomendado)
    elif original and original != recomendado:
        estado = modelos.get(original, {})
        if estado.get("backtest") == "listo" and not estado["alarma_rmae"] and (estado["rmae_movil"] or 9) <= HISTERESIS_RMAE:
            if aplicar:
                servicio.modelo_recomendado, servicio.modelo_original = original, None
                servicio._comparacion = None
            registrar("restaurar_modelo", f"Restaurar {original} como modelo recomendado: su rMAE móvil volvió a "
                      f"{estado['rmae_movil']:.2f}.", aplicar, de=recomendado, a=original)
    if "cobertura_fuera_de_rango" in activas:
        factor = factor_recalibracion(servicio, recomendado)
        if factor:
            registrar("recalibrar_intervalo", f"Multiplicar el ancho del rango P10 a P90 de {recomendado} por {factor:.2f} "
                      f"para volver a 80 % de cobertura. Requiere aprobación.", False, factor=factor)
    return acciones


def factor_recalibracion(servicio, modelo, dias=confianza.VENTANA_COBERTURA):
    """Factor que, aplicado al ancho del intervalo, habría cubierto 80 % de los últimos `dias` días."""
    bt = servicio._backtest_de(modelo)
    if bt is None or bt.empty:
        return None
    ultimas = sorted(bt["ds"].unique())[-dias:]
    b = bt[bt["ds"].isin(ultimas)]
    medio = np.where(b["y"] >= b["P50"], b["P90"] - b["P50"], b["P50"] - b["P10"])
    razon = ((b["y"] - b["P50"]).abs() / np.where(medio > 0, medio, np.nan)).dropna()
    return float(np.quantile(razon, 0.80)) if len(razon) >= 20 else None


# ---------------------------------------------------------------- resumen para el panel operativo
def resumen(obs, servicio, serie_principal):
    modelos = estado_modelo(servicio, serie_principal)
    drift = drift_de(obs, servicio)
    deficit = None
    try:
        deficit = next((a for a in servicio.alertas_series(servicio.modelo_recomendado) if a["serie"] == serie_principal), None)
    except Exception:
        logging.getLogger("flujo.api").exception("No se pudo leer la alerta de déficit")
    comparacion = None
    try:
        comparacion = servicio.comparacion_estadistica()
    except Exception:
        logging.getLogger("flujo.api").exception("No se pudo calcular la comparación")
    slos, ventanas = calcular_slos(obs, servicio, modelos)
    alertas = evaluar_alertas(obs, servicio, slos, ventanas, modelos, drift, deficit, comparacion)
    acciones = respuesta_automatica(obs, servicio, alertas, modelos)
    notifican = [a for a in alertas if a["notifica"]]
    peor = min((a["prioridad"] for a in notifican), default=None)
    estado = {P1: "incidente", P2: "degradado", P3: "con avisos"}.get(peor, "operando normal")
    rec = modelos.get(servicio.modelo_recomendado, {})
    return {
        "generado": _ahora_iso(), "estado": estado, "prioridad_maxima": peor,
        "proceso": {"version": VERSION, "inicio": _ahora_iso(obs.inicio), "segundos_activo": time.time() - obs.inicio,
                    "ventanas_aceleradas": obs.acelerar, "simulacro": dict(obs.caos)},
        "trafico": {"total": obs.ventana(None), "ventanas": ventanas, "serie": obs.serie_trafico()},
        "slos": slos, "alertas": alertas,
        "prioridades": PRIORIDADES, "runbooks": RUNBOOKS,
        "modelo": {"recomendado": servicio.modelo_recomendado, "original": getattr(servicio, "modelo_original", None),
                   "serie": serie_principal, "rmae_movil": rec.get("rmae_movil"), "cobertura_movil": rec.get("cobertura_movil"),
                   "limite_rmae": confianza.LIMITE_RMAE, "rango_cobertura": [confianza.COBERTURA_MIN, confianza.COBERTURA_MAX],
                   "nivel_confianza": rec.get("nivel_confianza"), "serie_rmae": rec.get("serie_rmae", []),
                   "serie_cobertura": rec.get("serie_cobertura", []),
                   "modelos": {m: {k: v for k, v in e.items() if not k.startswith("serie_")} for m, e in modelos.items()}},
        "datos": {"archivo": servicio.ruta_datos.name, "hasta": f"{servicio.datos.fechas[-1]:%Y-%m-%d}",
                  "movimientos": len(servicio.datos.mov), "duplicados": servicio.duplicados,
                  "fecha_referencia": obs.fecha_referencia or f"{pd.Timestamp.now():%Y-%m-%d}",
                  "carga_rechazada": obs.carga_rechazada},
        "deficit": deficit, "drift": drift,
        "respuesta_automatica": {"modo": obs.modo_respuesta, "acciones": acciones},
        "eventos": list(obs.eventos)[:60],
    }


def medidores(res, servicio):
    """Medidores del modelo, los datos, los SLO y las alertas para `/metrics`."""
    m = [("flujo_info", "Versión de la API y modelo recomendado.", {"version": VERSION, "modelo_recomendado": res["modelo"]["recomendado"]}, 1),
         ("flujo_datos_dias_atraso", "Días hábiles de atraso de los movimientos.", {}, next(s["valor"] for s in res["slos"] if s["id"] == "frescura")),
         ("flujo_datos_movimientos", "Movimientos cargados.", {}, res["datos"]["movimientos"]),
         ("flujo_datos_duplicados", "Movimientos repetidos descartados al cargar.", {}, res["datos"]["duplicados"])]
    for nombre, e in res["modelo"]["modelos"].items():
        etq = {"modelo": nombre, "serie": res["modelo"]["serie"]}
        m.append(("flujo_backtest_listo", "1 si el backtest del modelo está listo.", {"modelo": nombre}, 1 if e.get("backtest") == "listo" else 0))
        m.append(("flujo_modelo_rmae_movil", "rMAE móvil de 20 días frente al ingenuo.", etq, e.get("rmae_movil")))
        m.append(("flujo_modelo_cobertura_movil", "Cobertura móvil de 40 días del rango P10 a P90, en porcentaje.", etq, e.get("cobertura_movil")))
        m.append(("flujo_modelo_umbrales_cumplidos", "Umbrales de aceptación que cumple el modelo.", {"modelo": nombre}, e.get("umbrales_cumplidos")))
        if nombre in servicio._latencias:
            m.append(("flujo_pronostico_latencia_segundos", "Latencia del pronóstico de todas las series.", {"modelo": nombre}, servicio._latencias[nombre]))
    for f in res["drift"]["series"]:
        if "psi" in f:
            m.append(("flujo_drift_psi", "PSI de la ventana reciente frente a la referencia.", {"serie": f["serie"]}, f["psi"]))
            m.append(("flujo_drift_ks_p", "Valor p de la prueba de Kolmogorov-Smirnov.", {"serie": f["serie"]}, f["p_ks"]))
            m.append(("flujo_drift_nivel", "Nivel de drift: 0 sin cambio, 1 moderado, 2 significativo.", {"serie": f["serie"]},
                      {"sin cambio": 0, "moderado": 1, "significativo": 2}.get(f["nivel"], 0)))
    if res["deficit"]:
        m.append(("flujo_deficit_probabilidad_maxima", "Probabilidad máxima de déficit en el horizonte.", {"serie": res["deficit"]["serie"]}, res["deficit"]["probabilidad_maxima"]))
        m.append(("flujo_deficit_colchon_sugerido", "Colchón sugerido en MXN.", {"serie": res["deficit"]["serie"]}, res["deficit"]["colchon_sugerido"]))
    for s in res["slos"]:
        if "presupuesto_restante_pct" in s:
            m.append(("flujo_slo_presupuesto_restante", "Porcentaje del presupuesto de error que queda.", {"slo": s["id"]}, s["presupuesto_restante_pct"]))
            m.append(("flujo_slo_indicador", "Valor actual del indicador del SLO, en porcentaje.", {"slo": s["id"]}, s.get("sli_pct")))
        for ventana, tasa in (s.get("tasas_consumo") or {}).items():
            m.append(("flujo_slo_tasa_consumo", "Tasa de consumo del presupuesto por ventana.", {"slo": s["id"], "ventana": ventana}, tasa))
    for a in res["alertas"]:
        m.append(("flujo_alerta_activa", "1 por cada alerta activa.", {"alerta": a["id"], "prioridad": a["prioridad"],
                                                                      "inhibida": "si" if a["inhibida_por"] else "no"}, 1))
    return m
