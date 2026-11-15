"""Simulacro de operación: genera tráfico contra la API y, si se pide, provoca incidentes para probar las alertas.

La API debe arrancar con FLUJO_TOKEN_ADMIN (y FLUJO_CAOS=1 para la inyección de fallas). Ejemplos:

    python herramientas/simular_operacion.py --fase normal --segundos 60
    python herramientas/simular_operacion.py --fase errores --segundos 40 --error 0.35
    python herramientas/simular_operacion.py --fase lentitud --segundos 40 --latencia-ms 1500
    python herramientas/simular_operacion.py --fase drift          # carga un archivo con cambio de nivel
    python herramientas/simular_operacion.py --fase saldos-acumulados  # export defectuoso que sí pasa la validación
    python herramientas/simular_operacion.py --fase carga-invalida # carga un archivo al que le faltan campos
    python herramientas/simular_operacion.py --fase restaurar      # vuelve al archivo original
"""
import argparse
import json
import random
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

RAIZ = Path(__file__).resolve().parents[1]
ORIGINAL = "movimientos_feban_sep2025_sep2026_anon.json"
SERIES = ["OPERATIVO_TOTAL", "OPERATIVO_BBVA", "OPERATIVO_HSBC", "NETO_TOTAL"]
MODELOS = ["ETS", "ETS", "ETS", "SARIMAX"]


def trafico(base, segundos, por_segundo, hilos=1, parar=None):
    """Mezcla de consultas parecida a la del dashboard. Devuelve el conteo de respuestas por estado.

    Con `hilos` > 1 reparte el ritmo entre varios clientes en paralelo, para que el volumen no caiga cuando la API responde lento.
    `parar` es un threading.Event opcional para detener el tráfico antes de tiempo.
    """
    if hilos > 1:
        total = {}
        with ThreadPoolExecutor(hilos) as grupo:
            for parcial in grupo.map(lambda _: trafico(base, segundos, por_segundo / hilos, 1, parar), range(hilos)):
                for estado, n in parcial.items():
                    total[estado] = total.get(estado, 0) + n
        return total
    conteo, fin = {}, time.time() + segundos
    while time.time() < fin and not (parar and parar.is_set()):
        ruta, params = random.choice([
            ("pronostico", {"serie": random.choice(SERIES), "modelo": random.choice(MODELOS)}),
            ("pronostico", {"serie": "OPERATIVO_TOTAL"}),
            ("alertas", {}), ("series", {}), ("modelos", {}),
            ("metricas", {"serie": random.choice(SERIES)}),
            ("series/OPERATIVO_TOTAL/historia", {"dias": 30}),
        ])
        try:
            estado = requests.get(f"{base}/api/v1/{ruta}", params=params, timeout=30).status_code
        except requests.RequestException:
            estado = "sin respuesta"
        conteo[estado] = conteo.get(estado, 0) + 1
        time.sleep(random.expovariate(por_segundo))
    return conteo


def admin(base, token, ruta, cuerpo):
    r = requests.post(f"{base}/api/v1/{ruta}", json=cuerpo, headers={"X-Token-Admin": token}, timeout=120)
    return r.status_code, r.json()


def archivo_con_drift(destino, factor=1.8, desplazamiento=1_500_000, dias=20):
    """Copia del ejemplo en la que los ingresos operativos de los últimos `dias` días hábiles suben de nivel."""
    datos = json.loads((RAIZ / "data" / ORIGINAL).read_text(encoding="utf-8"))
    movs = datos["movimientos"]
    fechas = sorted({m["fecha"] for m in movs})[-dias:]
    por_dia = {}
    for m in movs:
        if m["fecha"] in fechas and m["monto"] > 0 and "SWEEP" not in m["concepto"].upper() and "GIRO" not in m["concepto"].upper():
            m["monto"] = round(m["monto"] * factor, 2)
            por_dia.setdefault(m["fecha"], m)
    for m in por_dia.values():                                  # un ingreso adicional por día, del mismo tipo que uno real
        extra = dict(m, monto=float(desplazamiento), partida=f"{m.get('partida', '')}-SIM")
        movs.append(extra)
    destino.write_text(json.dumps(datos, ensure_ascii=False), encoding="utf-8")
    return destino


def archivo_con_saldos_acumulados(destino, dias=30):
    """Export defectuoso: en los últimos `dias` días hábiles cada día arrastra el saldo de los anteriores.

    Imita un export que entrega saldos acumulados en lugar de los movimientos del día. El archivo pasa la validación
    (todos los campos son correctos), pero la serie operativa deja de parecerse a su historia.
    """
    datos = json.loads((RAIZ / "data" / ORIGINAL).read_text(encoding="utf-8"))
    movs = datos["movimientos"]
    fechas = sorted({m["fecha"] for m in movs})[-dias:]
    operativo = lambda m: "SWEEP" not in m["concepto"].upper() and "GIRO" not in m["concepto"].upper()   # noqa: E731
    neto, plantilla = {}, {}
    for m in movs:
        if m["fecha"] in fechas and operativo(m):
            neto[m["fecha"]] = neto.get(m["fecha"], 0.0) + m["monto"]
            if m["monto"] > 0:
                plantilla.setdefault(m["fecha"], m)
    arrastre = 0.0
    for fecha in fechas:
        if arrastre and fecha in plantilla:
            movs.append(dict(plantilla[fecha], monto=round(arrastre, 2), partida=f"{plantilla[fecha].get('partida', '')}-SALDO"))
        arrastre += neto.get(fecha, 0.0)
    destino.write_text(json.dumps(datos, ensure_ascii=False), encoding="utf-8")
    return destino


def archivo_invalido(destino):
    datos = json.loads((RAIZ / "data" / ORIGINAL).read_text(encoding="utf-8"))
    for m in datos["movimientos"][:3]:
        m.pop("concepto", None)
    datos["movimientos"][3]["fecha"] = "30/09/2026"
    destino.write_text(json.dumps(datos, ensure_ascii=False), encoding="utf-8")
    return destino


def main():
    p = argparse.ArgumentParser(description="Simulacro de operación del predictor.")
    p.add_argument("--base", default="http://127.0.0.1:8000")
    p.add_argument("--token", default="simulacro")
    p.add_argument("--fase", required=True, choices=["normal", "errores", "lentitud", "drift", "saldos-acumulados", "carga-invalida", "restaurar"])
    p.add_argument("--segundos", type=float, default=30)
    p.add_argument("--por-segundo", type=float, default=6)
    p.add_argument("--hilos", type=int, default=1, help="clientes en paralelo")
    p.add_argument("--error", type=float, default=0.35)
    p.add_argument("--latencia-ms", type=float, default=1500)
    a = p.parse_args()
    datos = RAIZ / "data"
    if a.fase == "normal":
        print(trafico(a.base, a.segundos, a.por_segundo, a.hilos))
    elif a.fase in ("errores", "lentitud"):
        cuerpo = {"error": a.error} if a.fase == "errores" else {"latencia_ms": a.latencia_ms}
        print(admin(a.base, a.token, "observabilidad/simulacro", cuerpo))
        print(trafico(a.base, a.segundos, a.por_segundo, a.hilos))
        print(admin(a.base, a.token, "observabilidad/simulacro", {"error": 0, "latencia_ms": 0}))
    elif a.fase == "drift":
        archivo_con_drift(datos / "simulacro_drift.json")
        print(admin(a.base, a.token, "datos/recargar", {"archivo": "simulacro_drift.json"}))
    elif a.fase == "saldos-acumulados":
        archivo_con_saldos_acumulados(datos / "simulacro_saldos.json")
        print(admin(a.base, a.token, "datos/recargar", {"archivo": "simulacro_saldos.json"}))
    elif a.fase == "carga-invalida":
        archivo_invalido(datos / "simulacro_invalido.json")
        print(admin(a.base, a.token, "datos/recargar", {"archivo": "simulacro_invalido.json"}))
    else:
        print(admin(a.base, a.token, "datos/recargar", {"archivo": ORIGINAL}))


if __name__ == "__main__":
    main()
