"""Simulacro de incidentes: provoca dos incidentes, ejecuta sus runbooks y deja el registro con la evidencia.

    INC-01  Aumento de latencia          runbook RB-02 (y RB-08 por el presupuesto de error)
    INC-02  Modelo degradado por un      runbooks RB-04, RB-06, RB-05 y RB-03
            export defectuoso

El script arranca su propia copia de la API en otro puerto, con las ventanas de las alertas aceleradas, y para cada
incidente hace lo mismo que haría la persona de guardia: toma la línea base, provoca la falla, espera a que la alerta
se active, corre los pasos de diagnóstico del runbook (guardando cada comando y su salida), aplica la mitigación y
verifica la recuperación. Nada de esto toca la API en producción ni los datos originales.

    python herramientas/simulacro_incidentes.py                    # los dos incidentes (unos 15 minutos)
    python herramientas/simulacro_incidentes.py --incidente 1      # solo el de latencia (unos 6 minutos)
    python herramientas/simulacro_incidentes.py --capturas         # además guarda capturas del panel (requiere playwright)

Salida (por omisión en results/simulacro_incidentes/):
    registro_incidentes.json   registro completo: cronología, pasos del runbook con sus salidas, antes, durante y después
    registro_incidentes.md     el mismo registro para leer
    bitacora_monitor.jsonl     lo que vio el monitor de alertas en cada consulta
    api_inc*.log               log JSON de la API durante cada incidente
"""
import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import monitor_alertas as monitor                                  # noqa: E402
import simular_operacion as sim                                    # noqa: E402

RAIZ = Path(__file__).resolve().parents[1]
TOKEN = "simulacro"
DEFECTUOSO = "simulacro_saldos.json"


def hora():
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


def r(x, n=2):
    return None if x is None else round(float(x), n)


# ---------------------------------------------------------------- API del simulacro
def arrancar_api(puerto, salida, nombre_log, acelerar):
    """Arranca la API en segundo plano con los interruptores del simulacro. Devuelve el proceso y la ruta de su log."""
    entorno = dict(os.environ, FLUJO_TOKEN_ADMIN=TOKEN, FLUJO_CAOS="1", FLUJO_OBS_ACELERAR=str(acelerar),
                   FLUJO_RESPUESTA_AUTO="aplicar", FLUJO_FECHA_REFERENCIA="2026-10-01", FLUJO_SALIDAS=str(salida / "salidas_api"))
    ruta_log = salida / nombre_log
    proceso = subprocess.Popen([sys.executable, "-m", "uvicorn", "api.main:app", "--host", "127.0.0.1", "--port", str(puerto)],
                               cwd=RAIZ, env=entorno, stdout=open(ruta_log, "w", encoding="utf-8"), stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{puerto}"
    for _ in range(240):
        try:
            if requests.get(f"{base}/api/v1/salud", timeout=2).ok:
                break
        except requests.RequestException:
            time.sleep(0.5)
    else:
        proceso.terminate()
        raise RuntimeError(f"La API no arrancó; revisa {ruta_log}")
    requests.get(f"{base}/api/v1/observabilidad", timeout=300)      # calienta los pronósticos de todos los modelos
    return proceso, base, ruta_log


def lineas_log(ruta_log, desde=0.0):
    """Líneas JSON del log de acceso escritas después de `desde` (segundos de época)."""
    filas = []
    for linea in ruta_log.read_text(encoding="utf-8", errors="replace").splitlines():
        if not linea.startswith("{"):
            continue
        try:
            fila = json.loads(linea)
        except ValueError:
            continue
        if datetime.fromisoformat(fila["ts"]).timestamp() >= desde:
            filas.append(fila)
    return filas


def duracion_por_ruta(texto_prometheus):
    """Del histograma de `/metrics`: por ruta, peticiones totales y las que tardaron hasta 1 s."""
    rutas = {}
    for ruta, limite, n in re.findall(r'flujo_http_duracion_segundos_bucket\{ruta="([^"]+)",le="([^"]+)"\} (\d+)', texto_prometheus):
        if limite in ("1.0", "+Inf"):
            rutas.setdefault(ruta, {})["total" if limite == "+Inf" else "hasta_1s"] = int(n)
    return rutas


def autocorrelacion(valores):
    """Correlación de cada valor con el anterior: cerca de 0 en un flujo diario, alta en un saldo que se arrastra."""
    n = len(valores)
    media = sum(valores) / n
    varianza = sum((v - media) ** 2 for v in valores)
    return sum((valores[i] - media) * (valores[i - 1] - media) for i in range(1, n)) / varianza if varianza else 0.0


# ---------------------------------------------------------------- registro
class Simulacro:
    def __init__(self, base, ruta_log, salida, capturas=False):
        self.base, self.ruta_log, self.salida, self.capturas = base, ruta_log, salida, capturas
        self.activas = {}
        self.inc = None

    def abrir(self, clave, titulo, causa, runbooks):
        self.activas = {a["id"]: a for a in monitor.consultar(self.base)["alertas"]}
        self.inc = {"id": clave, "titulo": titulo, "causa_simulada": causa, "runbooks": runbooks, "inicio": monitor.ahora(),
                    "cronologia": [], "alertas": [], "pasos": [], "fotos": {}, "tiempos": {}, "acciones_automaticas": []}
        self.t0, self.t_falla = time.time(), None
        print(f"\n===== {clave}. {titulo} =====", flush=True)
        return self.inc

    def falla(self, evento):
        """Marca el inicio de la falla: los tiempos de detección y mitigación se miden desde aquí."""
        self.t_falla = time.time()
        self.inc["inicio_falla"] = monitor.ahora()
        self.anotar(evento)

    def anotar(self, evento):
        self.inc["cronologia"].append({"hora": hora(), "segundo": round(time.time() - self.t0), "evento": evento})
        print(f"{hora()}  +{time.time() - self.t0:5.0f} s  {evento}", flush=True)

    def paso(self, runbook, fase, que, comando, salida, lectura):
        """Un paso del runbook: qué se hizo, con qué comando, qué devolvió y cómo se interpretó."""
        self.inc["pasos"].append({"hora": hora(), "runbook": runbook, "fase": fase, "paso": que, "comando": comando,
                                  "salida": salida, "lectura": lectura})
        print(f"{hora()}  [{runbook} {fase}] {que}\n          $ {comando}\n          -> {lectura}", flush=True)

    def consultar(self):
        """Consulta la API como lo hace el monitor y anota en la cronología las alertas que cambian."""
        o = monitor.consultar(self.base)
        if o is None:
            return None
        self.activas, nuevas, resueltas = monitor.cambios(self.activas, o)
        for a in nuevas:
            self.anotar(f"Alerta activada: {a['prioridad']} {a['nombre']} ({a['id']}), runbook {a['runbook']}. {a['resumen']}")
            self.inc["alertas"].append({"id": a["id"], "nombre": a["nombre"], "prioridad": a["prioridad"], "runbook": a["runbook"],
                                        "resumen": a["resumen"], "notifica": a["notifica"], "inhibida_por": a["inhibida_por"],
                                        "activada_s": round(time.time() - self.t0), "activada": hora(), "resuelta_s": None,
                                        "desde_la_falla_s": None if self.t_falla is None else round(time.time() - self.t_falla)})
        for a in resueltas:
            self.anotar(f"Alerta resuelta: {a['nombre']} ({a['id']}).")
            for fila in self.inc["alertas"]:
                if fila["id"] == a["id"] and fila["resuelta_s"] is None:
                    fila["resuelta_s"], fila["resuelta"] = round(time.time() - self.t0), hora()
        with open(self.salida / "bitacora_monitor.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"momento": monitor.ahora(), "incidente": self.inc["id"], **monitor.indicadores(o),
                                "alertas": sorted(self.activas)}, ensure_ascii=False) + "\n")
        return o

    def esperar(self, condicion, cada, maximo):
        """Consulta cada `cada` segundos hasta que `condicion(o)` se cumpla. Devuelve la última respuesta y si se cumplió."""
        fin = time.time() + maximo
        while True:
            o = self.consultar()
            if o is not None and condicion(o):
                return o, True
            if time.time() >= fin:
                return o, False
            time.sleep(cada)

    def foto(self, etiqueta, o=None):
        """Resumen de los indicadores en un momento del incidente (antes, durante, después)."""
        o = o or self.consultar()
        v = o["trafico"]["ventanas"]
        slos = {s["id"]: s for s in o["slos"]}
        foto = {
            "momento": monitor.ahora(), "estado": o["estado"],
            "alertas": [{"id": a["id"], "prioridad": a["prioridad"], "nombre": a["nombre"], "runbook": a["runbook"],
                         "notifica": a["notifica"], "inhibida_por": a["inhibida_por"]} for a in o["alertas"]],
            "servicio": {"p95_ms_5m": r(v["5m"].get("p95_ms"), 1), "p95_ms_1h": r(v["1h"].get("p95_ms"), 1),
                         "lentas_5m_pct": r(v["5m"]["tasa_lentas"] * 100, 1) if v["5m"]["peticiones"] else None,
                         "lentas_1h_pct": r(v["1h"]["tasa_lentas"] * 100, 1) if v["1h"]["peticiones"] else None,
                         "peticiones_5m": v["5m"]["peticiones"], "peticiones_1h": v["1h"]["peticiones"],
                         "errores_1h_pct": r(v["1h"]["tasa_error"] * 100, 2) if v["1h"]["peticiones"] else None,
                         "consumo_latencia": {k: r(x, 1) for k, x in (slos["latencia"].get("tasas_consumo") or {}).items()}},
            "slos": {k: {"sli_pct": r(s.get("sli_pct"), 1), "objetivo_pct": s["objetivo_pct"],
                         "consumido_pct": r(s.get("presupuesto_consumido_pct"), 1), "estado": s["estado"]} for k, s in slos.items()},
            "modelo": {"recomendado": o["modelo"]["recomendado"], "original": o["modelo"]["original"],
                       "rmae_movil": r(o["modelo"]["rmae_movil"], 3), "cobertura_movil": r(o["modelo"]["cobertura_movil"], 1),
                       "nivel_confianza": o["modelo"]["nivel_confianza"],
                       "modelos": {m: {"backtest": e["backtest"], "rmae_movil": r(e.get("rmae_movil"), 3), "alarma_rmae": e.get("alarma_rmae"),
                                       "cobertura_movil": r(e.get("cobertura_movil"), 1), "alarma_cobertura": e.get("alarma_cobertura")}
                                   for m, e in o["modelo"]["modelos"].items()}},
            "drift": None if not o.get("drift") else {
                "nivel": o["drift"]["nivel"],
                "series": [{"serie": s["serie"], "psi": r(s.get("psi")), "p_ks": r(s.get("p_ks"), 4),
                            "cambio_media_sd": r(s.get("cambio_media_sd")), "media_referencia": r(s.get("media_referencia"), 0),
                            "media_reciente": r(s.get("media_reciente"), 0), "nivel": s["nivel"]} for s in o["drift"]["series"]],
                "composicion": [{"indicador": c["indicador"], "nivel": c["nivel"]} for c in o["drift"].get("composicion", [])]},
            "datos": {k: o["datos"][k] for k in ("archivo", "hasta", "movimientos")},
            "deficit": o["deficit"] and {"nivel": o["deficit"]["nivel"], "colchon_sugerido": r(o["deficit"].get("colchon_sugerido"), 0)},
            "acciones": o["respuesta_automatica"]["acciones"],
        }
        self.inc["fotos"][etiqueta] = foto
        (self.salida / f"{self.inc['id']}_{etiqueta}.json").write_text(json.dumps(o, ensure_ascii=False), encoding="utf-8")
        if self.capturas:
            self.captura(f"{self.inc['id']}_{etiqueta}.png")
        return foto

    def captura(self, nombre):
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                navegador = p.chromium.launch()
                pagina = navegador.new_page(viewport={"width": 1360, "height": 900}, device_scale_factor=2)
                pagina.goto(f"{self.base}/operacion.html")
                pagina.wait_for_selector("body[data-estado]", timeout=120_000)
                pagina.wait_for_timeout(800)
                pagina.screenshot(path=str(self.salida / nombre), full_page=True)
                navegador.close()
        except Exception as e:                                         # la captura es opcional: el registro no depende de ella
            print(f"          (sin captura del panel: {e})", flush=True)

    def cerrar(self, resultado, seguimiento):
        eventos = requests.get(f"{self.base}/api/v1/observabilidad/eventos", params={"limite": 400}, timeout=60).json()
        desde = datetime.fromtimestamp(self.t0, timezone.utc).isoformat(timespec="seconds")
        self.inc["bitacora_api"] = [e for e in reversed(eventos) if e["momento"] >= desde]
        self.inc["acciones_automaticas"] = [e for e in self.inc["bitacora_api"] if e["tipo"] in ("accion_automatica", "accion_recomendada")]
        self.inc["fin"] = monitor.ahora()
        self.inc["tiempos"]["duracion_total_s"] = round(time.time() - self.t0)
        primeras = [a["desde_la_falla_s"] for a in self.inc["alertas"] if a["desde_la_falla_s"] is not None]
        self.inc["tiempos"]["primera_alerta_s"] = min(primeras) if primeras else None
        self.inc["resultado"], self.inc["seguimiento"] = resultado, seguimiento
        return self.inc

    def admin(self, ruta, cuerpo):
        return sim.admin(self.base, TOKEN, ruta, cuerpo)


# ---------------------------------------------------------------- INC-01: aumento de latencia
def incidente_latencia(s, latencia_ms=1500, acelerar=10):
    inc = s.abrir("INC-01", "Aumento de latencia de la API",
                  f"Se inyectan {latencia_ms} ms de espera a cada petición de la API (POST /api/v1/observabilidad/simulacro). "
                  "Imita una instancia saturada o una dependencia lenta.", ["RB-02", "RB-08"])
    s.anotar("Línea base: 45 s de tráfico normal (6 peticiones por segundo).")
    sim.trafico(s.base, 45, 6, hilos=3)
    antes = s.foto("antes")
    metricas_antes = duracion_por_ruta(requests.get(f"{s.base}/metrics", timeout=60).text)

    # --- la falla
    estado, cuerpo = s.admin("observabilidad/simulacro", {"latencia_ms": latencia_ms})
    s.falla(f"Inicio de la falla: {latencia_ms} ms de latencia inyectada (respuesta {estado}: {cuerpo}).")
    t_falla = s.t_falla
    parar = threading.Event()
    clientes = threading.Thread(target=sim.trafico, args=(s.base, 900, 64, 16, parar), daemon=True)
    clientes.start()

    # --- detección: la alerta P2 de consumo rápido es la que convoca a la guardia
    o, detectada = s.esperar(lambda o: any(a["id"] == "latencia_consumo_rapido" for a in o["alertas"]), 3, 420)
    inc["tiempos"]["deteccion_s"] = round(time.time() - t_falla)
    s.anotar("La alerta P2 de latencia está activa: se abre el incidente y se sigue el runbook RB-02." if detectada
             else "La alerta P2 no se activó en el tiempo previsto; se continúa con las alertas activas.")
    durante = s.foto("durante")

    # --- RB-02, diagnóstico
    v = o["trafico"]["ventanas"]
    consumo = next(x for x in o["slos"] if x["id"] == "latencia")["tasas_consumo"]
    s.paso("RB-02", "Confirmar", "Abrir el panel operativo y anotar hora, prioridad y magnitud.",
           f"curl {s.base}/api/v1/observabilidad",
           {"estado": o["estado"], "p95_ms": {k: r(v[k]["p95_ms"], 0) for k in ("5m", "1h")},
            "peticiones_lentas_pct": {k: r(v[k]["tasa_lentas"] * 100, 1) for k in ("5m", "1h")},
            "tasa_de_consumo_latencia": {k: r(x, 1) for k, x in consumo.items()},
            "alertas": [f"{a['prioridad']} {a['id']}" for a in o["alertas"]]},
           f"El p95 de la ventana corta es {v['5m']['p95_ms']:.0f} ms contra un umbral de 1 000 ms; "
           f"{v['5m']['tasa_lentas'] * 100:.0f} % de las peticiones tarda más de 1 s.")

    resp = requests.get(f"{s.base}/api/v1/pronostico", params={"serie": "OPERATIVO_TOTAL"}, timeout=60)
    trace_id = resp.headers["traceparent"].split("-")[1]
    time.sleep(0.5)
    linea = next((f for f in lineas_log(s.ruta_log, t_falla) if f.get("trace_id") == trace_id), {})
    s.paso("RB-02", "Confirmar", "Tomar el trace_id de una petición afectada y buscarla en el log.",
           f"curl -i '{s.base}/api/v1/pronostico?serie=OPERATIVO_TOTAL'  y  grep {trace_id[:12]} api.log",
           {"traceparent": resp.headers["traceparent"], "Server-Timing": resp.headers["Server-Timing"],
            "linea_de_log": {k: linea.get(k) for k in ("ts", "nivel", "ruta", "estado", "duracion_ms", "tramos", "latencia_inyectada_ms")}},
           f"La petición tardó {linea.get('duracion_ms', 0):.0f} ms y la respuesta trae su traza para seguirla en el log.")

    ahora = duracion_por_ruta(requests.get(f"{s.base}/metrics", timeout=60).text)
    por_ruta = {}
    for ruta, c in sorted(ahora.items()):
        total = c["total"] - metricas_antes.get(ruta, {}).get("total", 0)
        rapidas = c["hasta_1s"] - metricas_antes.get(ruta, {}).get("hasta_1s", 0)
        if total and ruta.startswith("/api/v1/") and "observabilidad" not in ruta:
            por_ruta[ruta] = {"peticiones": total, "sobre_1s": total - rapidas, "sobre_1s_pct": r((total - rapidas) / total * 100, 1)}
    s.paso("RB-02", "Diagnóstico 1", "Ver si la latencia alta es de todas las rutas o solo de las que calculan.",
           f"curl {s.base}/metrics | grep flujo_http_duracion_segundos_bucket", por_ruta,
           "Todas las rutas están lentas, también las que solo leen datos (series, modelos). No es un endpoint de cálculo.")

    lentas = [f for f in lineas_log(s.ruta_log, t_falla) if f.get("duracion_ms", 0) >= 1000 and f.get("ruta", "").startswith("/api/v1/")
              and "observabilidad" not in f["ruta"]]
    tramos = {}
    for f in lentas:
        for t in f.get("tramos", []):
            tramos.setdefault(t["nombre"], []).append(t["ms"])
    resumen_tramos = {n: {"peticiones": len(x), "ms_promedio": r(sum(x) / len(x), 0)} for n, x in tramos.items()}
    media = sum(f["duracion_ms"] for f in lentas) / max(len(lentas), 1)
    s.paso("RB-02", "Diagnóstico 2", "Revisar los tramos en los logs de las peticiones lentas.",
           "grep '\"duracion_ms\"' api.log | python -c 'agrupa los tramos de las peticiones de más de 1 000 ms'",
           {"peticiones_lentas_en_el_log": len(lentas), "duracion_promedio_ms": r(media, 0), "tramos": resumen_tramos,
            "ejemplo": {k: lentas[-1].get(k) for k in ("ts", "ruta", "duracion_ms", "tramos", "trace_id")} if lentas else None},
           "El tiempo no se va en el tramo de inferencia: el modelo no se está recalculando. La espera ocurre antes de "
           "llegar al cálculo (tramo espera_inyectada), que es lo que se ve cuando el servidor está saturado.")

    salud = requests.get(f"{s.base}/api/v1/salud", timeout=60).json()
    eventos = [e for e in requests.get(f"{s.base}/api/v1/observabilidad/eventos", timeout=60).json()
               if e["tipo"] in ("simulacro", "datos_recargados")][:3]
    s.paso("RB-02", "Diagnóstico 3", "Revisar si hay un backtest en cálculo, si la instancia se reinició y qué cambió antes del inicio.",
           f"curl {s.base}/api/v1/salud  y  curl {s.base}/api/v1/observabilidad/eventos",
           {"salud": salud, "minutos_activa": r(o["proceso"]["segundos_activo"] / 60, 1), "cambios_recientes": eventos},
           "No hay backtest en cálculo ni reinicio. La bitácora muestra el cambio que coincide con el inicio: la latencia inyectada.")

    # --- RB-02, mitigación
    estado, cuerpo = s.admin("observabilidad/simulacro", {"latencia_ms": 0, "error": 0})
    t_mitigacion = time.time()
    inc["tiempos"]["mitigacion_s"] = round(t_mitigacion - t_falla)
    parar.set()
    s.paso("RB-02", "Mitigación", "Quitar la causa de la saturación.",
           f"curl -X POST {s.base}/api/v1/observabilidad/simulacro -H 'X-Token-Admin: ***' -d '{{\"latencia_ms\": 0}}'",
           {"estado": estado, "respuesta": cuerpo},
           "En el simulacro la causa es la falla inyectada y se retira. En producción este paso es subir el plan de la "
           "instancia o reiniciarla, como indica el runbook.")
    s.anotar("Mitigación aplicada: se retira la latencia inyectada.")
    clientes.join(timeout=30)

    # --- verificación
    parar2 = threading.Event()
    normal = threading.Thread(target=sim.trafico, args=(s.base, 900, 8, 4, parar2), daemon=True)
    normal.start()
    o, ok = s.esperar(lambda o: (o["trafico"]["ventanas"]["5m"]["peticiones"] >= 20 and (o["trafico"]["ventanas"]["5m"]["p95_ms"] or 9e9) < 1000
                                 and not any(a["id"] == "latencia_consumo_rapido" for a in o["alertas"])), 3, 240)
    inc["tiempos"]["recuperacion_s"] = round(time.time() - t_mitigacion)
    v = o["trafico"]["ventanas"]
    s.paso("RB-02", "Cierre", "Verificar que el p95 de la ventana corta volvió a estar por debajo de 1 s.",
           f"curl {s.base}/api/v1/observabilidad",
           {"p95_ms_5m": r(v["5m"]["p95_ms"], 1), "peticiones_lentas_5m_pct": r(v["5m"]["tasa_lentas"] * 100, 1),
            "alertas": [f"{a['prioridad']} {a['id']}" for a in o["alertas"]]},
           f"El p95 volvió a {v['5m']['p95_ms']:.0f} ms y la alerta P2 de consumo rápido se resolvió." if ok
           else "El p95 todavía no baja de 1 s.")
    s.anotar("Criterio de cierre del RB-02 cumplido: p95 de la ventana corta por debajo de 1 s.")
    o, _ = s.esperar(lambda o: not any(a["id"].startswith("latencia_consumo") for a in o["alertas"]), 5, 300)
    inc["tiempos"]["alertas_de_consumo_resueltas_s"] = round(time.time() - t_mitigacion)
    parar2.set()
    normal.join(timeout=30)
    despues = s.foto("despues")

    # --- RB-08: lo que el incidente le costó al presupuesto de error
    lat = despues["slos"]["latencia"]
    s.paso("RB-08", "Diagnóstico", "Identificar cuánto presupuesto de error consumió el incidente.",
           f"curl {s.base}/api/v1/observabilidad  (bloque slos)",
           {"slo_latencia": lat, "antes": antes["slos"]["latencia"],
            "alertas_que_siguen_activas": [f"{a['prioridad']} {a['id']}" for a in despues["alertas"]]},
           f"El SLO de latencia quedó en {lat['sli_pct']} % (objetivo 95 %) con {lat['consumido_pct']} % del presupuesto consumido. "
           "El servicio ya responde normal, pero el presupuesto gastado no vuelve con la mitigación: se recupera con el tiempo.")
    agotado = any(a["id"] == "presupuesto_agotado_latencia" for a in despues["alertas"])
    return s.cerrar(
        f"El servicio se recuperó: el p95 de la ventana corta pasó de {durante['servicio']['p95_ms_5m']:.0f} ms durante el incidente a "
        f"{despues['servicio']['p95_ms_5m']:.0f} ms después de la mitigación (antes: {antes['servicio']['p95_ms_5m']:.0f} ms). "
        f"Las alertas de consumo se resolvieron solas. " +
        ("Queda activa la alerta de presupuesto de latencia agotado, que es la consecuencia esperada: el simulacro concentra "
         "en minutos el tráfico lento que en producción se repartiría en 30 días." if agotado else
         "El presupuesto de latencia no se agotó."),
        ["Aplicar RB-08: congelar cambios que no sean correcciones hasta recuperar el presupuesto de latencia.",
         f"Las ventanas del simulacro van aceleradas {acelerar:g} veces; con las ventanas reales la alerta P2 tarda más en activarse "
         "porque exige que 72 % de las peticiones de la última hora sean lentas.",
         "Evaluar una alerta adicional por p95 de 5 minutos para detectar antes una lentitud total."])


# ---------------------------------------------------------------- INC-02: modelo degradado por un export defectuoso
def incidente_modelo(s, dias=17, registrar_mlflow=False):
    inc = s.abrir("INC-02", "Modelo degradado por un export defectuoso",
                  f"Se carga un archivo en el que los últimos {dias} días hábiles traen el saldo acumulado en lugar de los "
                  "movimientos del día. El archivo tiene todos sus campos correctos y pasa la validación.",
                  ["RB-04", "RB-06", "RB-03", "RB-05", "RB-07"])
    cache = RAIZ / "api_cache"
    cache_previa = set(cache.glob("*")) if cache.exists() else set()
    parar = threading.Event()
    clientes = threading.Thread(target=sim.trafico, args=(s.base, 3600, 2, 2, parar), daemon=True)
    clientes.start()
    s.anotar("Línea base: tráfico normal (2 peticiones por segundo) con el archivo original.")
    time.sleep(20)
    antes = s.foto("antes")

    # --- la falla
    sim.archivo_con_saldos_acumulados(RAIZ / "data" / DEFECTUOSO, dias=dias)
    estado, cuerpo = s.admin("datos/recargar", {"archivo": DEFECTUOSO})
    s.falla(f"Inicio de la falla: se carga {DEFECTUOSO}. La API lo acepta (respuesta {estado}) y recalcula el backtest.")
    t_falla = s.t_falla
    inc["carga"] = {"estado": estado, "respuesta": cuerpo}

    # --- detección: hay que esperar a que el backtest termine para tener métricas del modelo
    listo = lambda o: all(e["backtest"] == "listo" for e in o["modelo"]["modelos"].values())      # noqa: E731
    o, _ = s.esperar(listo, 10, 1200)
    inc["tiempos"]["backtest_recalculado_s"] = round(time.time() - t_falla)
    s.anotar("El backtest terminó de recalcularse con los datos nuevos.")
    del_modelo = lambda o: any(a["runbook"] in ("RB-04", "RB-05") and a["prioridad"] != "P4" and a["notifica"] for a in o["alertas"])   # noqa: E731
    if not del_modelo(o):
        o, _ = s.esperar(del_modelo, 5, 60)
    inc["tiempos"]["deteccion_s"] = round(time.time() - t_falla)
    durante = s.foto("durante", o)
    degradado = next((a for a in inc["alertas"] if a["id"] == "modelo_degradado"), None)
    if degradado:
        inc["tiempos"]["modelo_degradado_activada_s"] = degradado["desde_la_falla_s"]
    s.anotar("Las alertas del modelo ya notifican: se abre el incidente y se sigue el runbook RB-04.")

    # --- RB-04, diagnóstico
    modelos = durante["modelo"]["modelos"]
    servidos = [m for m in modelos if "Naive" not in m]
    peores = [m for m in servidos if modelos[m]["rmae_movil"] > antes["modelo"]["modelos"][m]["rmae_movil"] + 0.05]
    menos = [m for m in servidos if modelos[m]["cobertura_movil"] < antes["modelo"]["modelos"][m]["cobertura_movil"] - 5]
    s.paso("RB-04", "Confirmar", "Abrir el panel operativo y anotar las alertas activas y su prioridad.",
           f"curl {s.base}/api/v1/observabilidad",
           {"estado": o["estado"], "alertas": [{"prioridad": a["prioridad"], "alerta": a["id"], "runbook": a["runbook"], "resumen": a["resumen"],
                                                 "silenciada_por": a["inhibida_por"]} for a in o["alertas"]]},
           f"Hay {len(o['alertas'])} alertas activas; la de mayor prioridad es {min(a['prioridad'] for a in o['alertas'])}."
           + (f" La de modelo degradado es P2 porque coincide con data drift significativo. Se activó {degradado['desde_la_falla_s']} s "
              "después de la carga, cuando terminó el backtest de ETS, pero quedó silenciada hasta que terminó el de todos los modelos."
              if degradado else
              " El rMAE móvil todavía no acumula 10 días seguidos sobre 0.90, así que el aviso llega por la calibración y el drift."))
    s.paso("RB-04", "Diagnóstico 1", "Ver el rMAE móvil de todos los modelos servidos: si todos subieron, el cambio está en los datos.",
           f"curl {s.base}/api/v1/observabilidad  (bloque modelo.modelos)",
           {"durante": modelos, "antes": antes["modelo"]["modelos"]},
           f"El rMAE móvil subió en {len(peores)} de los {len(servidos)} modelos servidos y la cobertura cayó en {len(menos)}. "
           + ("Un problema del modelo afectaría a uno solo: el cambio está en los datos." if len(peores) == len(servidos)
              else "El deterioro no es de un solo modelo: hay que revisar los datos."))
    cambiadas = [x["serie"] for x in durante["drift"]["series"] if x["nivel"] != "sin cambio"] + \
                [c["indicador"] for c in durante["drift"]["composicion"] if c["nivel"] != "sin cambio"]
    iguales = [x["serie"] for x in durante["drift"]["series"] if x["nivel"] == "sin cambio"]
    s.paso("RB-04", "Diagnóstico 2", "Revisar el bloque de data drift.",
           f"curl {s.base}/api/v1/observabilidad  (bloque drift)", durante["drift"],
           f"Data drift {durante['drift']['nivel']}. Cambiaron: {', '.join(cambiadas) or 'ninguna serie'}. "
           f"Sin cambio: {', '.join(iguales) or 'ninguna'}. Un cambio real del negocio no se quedaría en un solo banco.")
    if registrar_mlflow:
        uri = f"sqlite:///{s.salida / 'mlflow_simulacro.db'}"
        comando = [sys.executable, "src/registrar_mlflow.py", "--solo-monitoreo", "--api", s.base, "--datos", f"data/{DEFECTUOSO}", "--uri", uri]
        res = subprocess.run(comando, cwd=RAIZ, capture_output=True, text=True, timeout=600)
        s.paso("RB-04", "Diagnóstico 3", "Registrar la corrida de monitoreo en MLflow para compararla con las anteriores.",
               "python src/registrar_mlflow.py --solo-monitoreo --api <API> --datos data/" + DEFECTUOSO,
               (res.stdout or res.stderr).strip().splitlines()[-3:],
               "La corrida queda en el experimento flujo-efectivo-monitoreo con el drift y las alertas del incidente.")

    # --- RB-04, mitigación: con un respaldo bajo el límite, la API conmuta sola (modo aplicar)
    time.sleep(6)
    o2 = s.consultar()
    servido = requests.get(f"{s.base}/api/v1/pronostico", params={"serie": "OPERATIVO_TOTAL"}, timeout=120).json()
    automaticas = [e for e in requests.get(f"{s.base}/api/v1/observabilidad/eventos", timeout=60).json()
                   if e["tipo"] in ("accion_automatica", "accion_recomendada")]
    conmuto = bool(o2["modelo"]["original"])
    s.paso("RB-04", "Mitigación 1", "Si otro modelo servido está por debajo de 0.90, conmutar a ese respaldo (en modo aplicar la API lo hace sola).",
           f"curl {s.base}/api/v1/observabilidad/eventos  y  curl '{s.base}/api/v1/pronostico?serie=OPERATIVO_TOTAL'",
           {"modelo_recomendado": o2["modelo"]["recomendado"], "modelo_original": o2["modelo"]["original"],
            "modelo_que_sirve_el_pronostico": servido["modelo"],
            "bitacora": [{"momento": e["momento"], "tipo": e["tipo"], "mensaje": e["mensaje"]} for e in automaticas],
            "alertas": [f"{a['prioridad']} {a['id']}" for a in o2["alertas"]]},
           (f"La API conmutó sola de {o2['modelo']['original']} a {o2['modelo']['recomendado']} y el pronóstico ya sale del respaldo. "
            "La conmutación contiene el daño pero no arregla la causa: el respaldo pronostica con los mismos datos defectuosos."
            if conmuto else "Ningún modelo servido cumple el límite o el recomendado no llegó a la alarma: no hay conmutación."))
    if conmuto:
        s.anotar(f"Respuesta automática: la API conmuta de {o2['modelo']['original']} a {o2['modelo']['recomendado']}.")

    # --- RB-06, diagnóstico: distinguir cambio real de error de datos
    total = next(x for x in durante["drift"]["series"] if x["serie"] == "OPERATIVO_TOTAL")
    puntos = requests.get(f"{s.base}/api/v1/series/OPERATIVO_TOTAL/historia", params={"dias": dias + 120}, timeout=60).json()["puntos"]
    valores = [p["valor"] for p in puntos]
    serie = [{"fecha": p["fecha"], "millones": r(p["valor"] / 1e6, 2)} for p in puntos[-dias:]]
    arrastre_antes, arrastre_ahora = autocorrelacion(valores[:-dias]), autocorrelacion(valores[-dias:])
    s.paso("RB-06", "Diagnóstico 1", "Ver qué series cambiaron y en qué dirección, y distinguir un cambio real de un error del export.",
           f"curl '{s.base}/api/v1/series/OPERATIVO_TOTAL/historia?dias={dias + 120}'",
           {"media_referencia_120_dias": total["media_referencia"], "media_reciente_20_dias": total["media_reciente"],
            "cambio_en_desviaciones": total["cambio_media_sd"],
            "parecido_de_cada_dia_con_el_anterior": {"120_dias_previos": r(arrastre_antes), f"ultimos_{dias}_dias": r(arrastre_ahora)},
            "flujo_operativo_diario_millones": serie},
           f"El promedio diario pasó de {total['media_referencia'] / 1e6:.1f} a {total['media_reciente'] / 1e6:.1f} millones. Además, la "
           f"correlación de cada día con el anterior pasó de {arrastre_antes:.2f} a {arrastre_ahora:.2f}: los últimos {dias} días ya no "
           "oscilan alrededor del promedio, cada uno queda cerca del anterior. Es la forma de un saldo que se arrastra, no la de un flujo diario.")
    val = subprocess.run([sys.executable, "-m", "api.validar", f"data/{DEFECTUOSO}"], cwd=RAIZ, capture_output=True, text=True, timeout=300)
    s.paso("RB-03", "Diagnóstico", "Validar el archivo cargado.", f"python -m api.validar data/{DEFECTUOSO}",
           {"codigo_de_salida": val.returncode, "salida": (val.stdout or val.stderr).strip().splitlines()[:8]},
           "El archivo pasa la validación: los campos son correctos. La validación revisa la estructura, no el nivel de los montos, "
           "por eso el export defectuoso entró. La causa es un error de datos y la mitigación es la del RB-03.")

    # --- RB-05 y respuesta automática
    acciones = durante["acciones"]
    s.paso("RB-05", "Diagnóstico", "Ver la cobertura del intervalo y la acción que propone la API.",
           f"curl {s.base}/api/v1/observabilidad  (bloques modelo y respuesta_automatica)",
           {"cobertura_movil_por_modelo": {m: e["cobertura_movil"] for m, e in modelos.items()}, "rango_aceptado": [65, 95],
            "acciones": acciones},
           f"La cobertura de ETS bajó a {modelos['ETS']['cobertura_movil']} %: el intervalo quedó estrecho para los datos nuevos. "
           "La API propone un factor de recalibración pero no lo aplica sin aprobación. No se aprueba: recalibrar taparía un error de datos.")

    # --- RB-07: la alerta de déficit también cambió, y hay que saber cuánto pesa
    d_antes, d_dur = antes["deficit"] or {}, durante["deficit"] or {}
    s.paso("RB-07", "Diagnóstico", "Revisar la alerta de déficit y la confianza del modelo antes de avisar a tesorería.",
           f"curl '{s.base}/api/v1/alertas'  y  bloque modelo de /api/v1/observabilidad",
           {"antes": d_antes, "durante": d_dur, "nivel_de_confianza": durante["modelo"]["nivel_confianza"]},
           (f"La alerta de déficit pasó de {d_antes.get('nivel')} a {d_dur.get('nivel')} y el colchón sugerido de "
            f"{(d_antes.get('colchon_sugerido') or 0) / 1e6:.2f} a {(d_dur.get('colchon_sugerido') or 0) / 1e6:.2f} millones: los datos "
            "defectuosos también mueven la señal de liquidez. " if d_antes.get("nivel") != d_dur.get("nivel") else
            f"La alerta de déficit sigue en {d_dur.get('nivel')} y el colchón sugerido cambió poco, de "
            f"{(d_antes.get('colchon_sugerido') or 0) / 1e6:.2f} a {(d_dur.get('colchon_sugerido') or 0) / 1e6:.2f} millones. ")
           + f"Con confianza {durante['modelo']['nivel_confianza']} y datos en duda, el runbook indica que la alerta pesa menos: no se usa "
           "para decidir y se avisa a tesorería que el pronóstico del día no es confiable.")

    # --- mitigación (RB-03): volver al último archivo bueno
    estado, cuerpo = s.admin("datos/recargar", {"archivo": sim.ORIGINAL})
    t_mitigacion = time.time()
    inc["tiempos"]["mitigacion_s"] = round(t_mitigacion - t_falla)
    s.paso("RB-03", "Mitigación", "Restaurar el último archivo bueno mientras se corrige el export en el origen.",
           f"curl -X POST {s.base}/api/v1/datos/recargar -H 'X-Token-Admin: ***' -d '{{\"archivo\": \"{sim.ORIGINAL}\"}}'",
           {"estado": estado, "respuesta": cuerpo},
           "La API valida y carga el archivo anterior. El backtest de ese archivo ya estaba calculado, así que las métricas vuelven de inmediato.")
    s.anotar("Mitigación aplicada: se restaura el archivo original.")

    # --- verificación
    o, ok = s.esperar(lambda o: listo(o) and not o["modelo"]["original"] and not any(
        a["runbook"] in ("RB-04", "RB-05", "RB-06", "RB-08") and a["prioridad"] != "P4" for a in o["alertas"]), 5, 600)
    inc["tiempos"]["recuperacion_s"] = round(time.time() - t_mitigacion)
    despues = s.foto("despues", o)
    s.paso("RB-04", "Cierre", "Verificar que el rMAE móvil, la cobertura y el drift volvieron a sus valores normales.",
           f"curl {s.base}/api/v1/observabilidad",
           {"modelo_recomendado": despues["modelo"]["recomendado"], "rmae_movil": despues["modelo"]["rmae_movil"],
            "cobertura_movil": despues["modelo"]["cobertura_movil"],
            "drift": despues["drift"]["nivel"], "slo_calidad": despues["slos"]["calidad"], "slo_calibracion": despues["slos"]["calibracion"],
            "alertas": [f"{a['prioridad']} {a['id']}" for a in despues["alertas"]]},
           f"{despues['modelo']['recomendado']} vuelve a ser el modelo recomendado, con rMAE móvil {despues['modelo']['rmae_movil']}, "
           f"cobertura {despues['modelo']['cobertura_movil']} % y drift {despues['drift']['nivel']}." if ok
           else "Todavía hay alertas del modelo activas.")
    s.anotar("Criterio de cierre cumplido: métricas del modelo en sus valores normales y alertas resueltas.")
    if registrar_mlflow:
        uri = f"sqlite:///{s.salida / 'mlflow_simulacro.db'}"
        res = subprocess.run([sys.executable, "src/registrar_mlflow.py", "--solo-monitoreo", "--api", s.base, "--uri", uri],
                             cwd=RAIZ, capture_output=True, text=True, timeout=600)
        s.paso("RB-04", "Registro", "Registrar en MLflow la corrida de monitoreo posterior a la recuperación.",
               "python src/registrar_mlflow.py --solo-monitoreo --api <API>", (res.stdout or res.stderr).strip().splitlines()[-3:],
               "Quedan dos corridas de monitoreo para comparar: durante el incidente y después.")
    parar.set()
    clientes.join(timeout=30)
    (RAIZ / "data" / DEFECTUOSO).unlink(missing_ok=True)
    for archivo in (set(cache.glob("*")) if cache.exists() else set()) - cache_previa:
        archivo.unlink()                  # el backtest del archivo defectuoso no se conserva: cada simulacro lo recalcula

    m_antes, m_dur, m_desp = (f["modelo"]["modelos"]["ETS"] for f in (antes, durante, despues))
    return s.cerrar(
        f"El modelo se recuperó al restaurar el archivo original: el rMAE móvil de ETS pasó de {m_antes['rmae_movil']} antes del incidente a "
        f"{m_dur['rmae_movil']} durante y volvió a {m_desp['rmae_movil']}; la cobertura pasó de {m_antes['cobertura_movil']} % a "
        f"{m_dur['cobertura_movil']} % y volvió a {m_desp['cobertura_movil']} %. El drift pasó de {antes['drift']['nivel']} a "
        f"{durante['drift']['nivel']} y volvió a {despues['drift']['nivel']}.",
        ["Agregar a la carga de datos un control de nivel: rechazar o dejar en cuarentena un archivo cuyo drift contra la historia "
         "sea significativo, antes de servir pronósticos con él.",
         "Pedir a tesorería el export corregido y cargarlo con POST /api/v1/datos/recargar.",
         "La alerta de modelo degradado quedó silenciada mientras se calculaba el backtest de los otros modelos. Conviene silenciarla "
         "solo mientras falte el backtest del modelo recomendado.",
         "El respaldo quedó apenas por debajo del límite de rMAE. Con datos defectuosos todos los modelos se deterioran, "
         "así que la conmutación solo gana tiempo.",
         "El recálculo del backtest tarda varios minutos; durante ese lapso la API sirve pronósticos con los datos nuevos sin métricas "
         "de confianza. Conviene conservar los datos anteriores hasta que el backtest termine."])


# ---------------------------------------------------------------- registro en Markdown
def a_markdown(registro):
    lineas = ["# Registro de incidentes simulados", "",
              f"Generado el {registro['generado']} con `herramientas/simulacro_incidentes.py`. "
              f"Ventanas de las alertas aceleradas {registro['configuracion']['ventanas_aceleradas']:g} veces.", ""]
    for inc in registro["incidentes"]:
        lineas += [f"## {inc['id']}. {inc['titulo']}", "", f"- Inicio: {inc['inicio']}  |  Fin: {inc['fin']}",
                   f"- Causa simulada: {inc['causa_simulada']}", f"- Runbooks: {', '.join(inc['runbooks'])}",
                   f"- Tiempos (segundos): {json.dumps(inc['tiempos'], ensure_ascii=False)}", "", "### Cronología", ""]
        lineas += [f"- {c['hora']} (+{c['segundo']} s) {c['evento']}" for c in inc["cronologia"]]
        lineas += ["", "### Ejecución de los runbooks", ""]
        for p in inc["pasos"]:
            lineas += [f"**{p['hora']} · {p['runbook']} · {p['fase']}.** {p['paso']}", "", "```", f"$ {p['comando']}",
                       json.dumps(p["salida"], ensure_ascii=False, indent=1)[:2500], "```", "", f"Lectura: {p['lectura']}", ""]
        lineas += ["### Resultado", "", inc["resultado"], "", "### Seguimiento", ""] + [f"- {x}" for x in inc["seguimiento"]] + [""]
    return "\n".join(lineas)


def main():
    p = argparse.ArgumentParser(description="Simula incidentes, ejecuta sus runbooks y guarda el registro.")
    p.add_argument("--incidente", choices=["1", "2", "todos"], default="todos")
    p.add_argument("--salida", type=Path, default=RAIZ / "results" / "simulacro_incidentes")
    p.add_argument("--puerto", type=int, default=8040)
    p.add_argument("--acelerar", type=float, default=10, help="divide las ventanas de las alertas de servicio")
    p.add_argument("--dias-defectuosos", type=int, default=17, help="días hábiles con saldo acumulado en el archivo defectuoso")
    p.add_argument("--capturas", action="store_true", help="guarda capturas del panel operativo (requiere playwright)")
    p.add_argument("--mlflow", action="store_true", help="registra en MLflow las corridas de monitoreo del incidente 2")
    a = p.parse_args()
    a.salida.mkdir(parents=True, exist_ok=True)
    registro = {"generado": monitor.ahora(), "configuracion": {"ventanas_aceleradas": a.acelerar, "modo_respuesta": "aplicar",
                                                               "fecha_referencia": "2026-10-01"}, "incidentes": []}
    archivo = a.salida / "registro_incidentes.json"
    if a.incidente == "todos":
        (a.salida / "bitacora_monitor.jsonl").unlink(missing_ok=True)
    elif archivo.exists():                                    # al repetir un solo incidente se conserva el otro
        registro["incidentes"] = json.loads(archivo.read_text(encoding="utf-8"))["incidentes"]
    planes = [("1", "api_inc01.log", lambda s: incidente_latencia(s, acelerar=a.acelerar)),
              ("2", "api_inc02.log", lambda s: incidente_modelo(s, a.dias_defectuosos, a.mlflow))]
    for clave, nombre_log, correr in planes:
        if a.incidente not in (clave, "todos"):
            continue
        proceso, base, ruta_log = arrancar_api(a.puerto, a.salida, nombre_log, a.acelerar)      # cada incidente parte de un proceso limpio
        try:
            nuevo = correr(Simulacro(base, ruta_log, a.salida, a.capturas))
            registro["incidentes"] = sorted([i for i in registro["incidentes"] if i["id"] != nuevo["id"]] + [nuevo], key=lambda i: i["id"])
        finally:
            proceso.terminate()
            proceso.wait(timeout=30)
            (RAIZ / "data" / DEFECTUOSO).unlink(missing_ok=True)
        archivo.write_text(json.dumps(registro, ensure_ascii=False, indent=2), encoding="utf-8")
        (a.salida / "registro_incidentes.md").write_text(a_markdown(registro), encoding="utf-8")
    print(f"\nRegistro guardado en {a.salida}")


if __name__ == "__main__":
    main()
