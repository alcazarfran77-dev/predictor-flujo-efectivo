"""Monitor de alertas: consulta la API cada cierto tiempo y avisa cuando una alerta se activa o se resuelve.

La API calcula las alertas cada vez que alguien consulta `/api/v1/observabilidad`. Este script es ese "alguien":
déjalo corriendo junto a la API (o prográmalo con cron) y tendrás supervisión continua sin instalar nada más.

    python herramientas/monitor_alertas.py                             # consulta cada 30 s y escribe en pantalla
    python herramientas/monitor_alertas.py --cada 10 --bitacora salidas/alertas.jsonl
    python herramientas/monitor_alertas.py --webhook https://...       # envía las alertas P1 y P2 a un canal
    python herramientas/monitor_alertas.py --una-vez                   # una sola consulta; el código de salida dice el estado

Códigos de salida con --una-vez: 0 operando normal o con avisos, 1 degradado (P2), 2 incidente (P1), 3 la API no responde.
"""
import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

SALIDA_POR_ESTADO = {"operando normal": 0, "con avisos": 0, "degradado": 1, "incidente": 2}


def ahora():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def consultar(base, espera=120):
    """Lee el resumen de observabilidad. Devuelve None si la API no responde."""
    try:
        r = requests.get(f"{base}/api/v1/observabilidad", timeout=espera)
        r.raise_for_status()
        return r.json()
    except (requests.RequestException, ValueError):
        return None


def indicadores(o):
    """Los números que conviene tener a la vista en cada consulta."""
    slos = {s["id"]: s for s in o["slos"]}
    corta = o["trafico"]["ventanas"]["5m"]
    return {
        "estado": o["estado"],
        "p95_ms_5m": round(corta["p95_ms"], 1) if corta.get("p95_ms") is not None else None,
        "lentas_5m_pct": round(corta["tasa_lentas"] * 100, 1) if corta.get("peticiones") else None,
        "errores_5m_pct": round(corta["tasa_error"] * 100, 1) if corta.get("peticiones") else None,
        "modelo": o["modelo"]["recomendado"],
        "rmae_movil": o["modelo"]["rmae_movil"],
        "cobertura_movil": o["modelo"]["cobertura_movil"],
        "drift": o["drift"]["nivel"] if o.get("drift") else None,
        "datos": o["datos"]["archivo"],
        "presupuesto_consumido_pct": {k: round(s["presupuesto_consumido_pct"], 1) for k, s in slos.items()
                                      if s.get("presupuesto_consumido_pct") is not None},
    }


def cambios(antes, o):
    """Compara las alertas de esta consulta con las de la anterior."""
    actuales = {a["id"]: a for a in o["alertas"]}
    nuevas = [a for clave, a in actuales.items() if clave not in antes]
    resueltas = [a for clave, a in antes.items() if clave not in actuales]
    return actuales, nuevas, resueltas


def texto_alerta(a):
    aviso = "" if a["notifica"] else (f" (silenciada por {a['inhibida_por']})" if a["inhibida_por"] else " (solo en el panel)")
    return f"{a['prioridad']} {a['nombre']}: {a['resumen']} Runbook {a['runbook']}, {a['runbook_nombre']}.{aviso}"


def notificar(webhook, a, tipo):
    """Envía la alerta a un webhook (Slack, Teams o cualquier receptor que acepte JSON con el campo `text`)."""
    cuerpo = {"text": f"[{tipo}] {texto_alerta(a)}", "alerta": a["id"], "prioridad": a["prioridad"], "runbook": a["runbook"],
              "valor": a.get("valor"), "umbral": a.get("umbral"), "canal": a.get("canal")}
    try:
        requests.post(webhook, json=cuerpo, timeout=10)
    except requests.RequestException as e:
        print(f"{ahora()}  no se pudo notificar: {e}", file=sys.stderr)


def vigilar(base, cada=30, bitacora=None, webhook=None, una_vez=False, hasta=None, al_cambiar=None):
    """Ciclo de supervisión. `hasta(o)` permite detenerlo desde otro script; `al_cambiar(tipo, alerta)` recibe cada cambio."""
    activas, sin_respuesta = {}, 0
    while True:
        o = consultar(base)
        if o is None:
            sin_respuesta += 1
            print(f"{ahora()}  la API no responde ({sin_respuesta} consultas seguidas)", flush=True)
            if una_vez:
                return 3
        else:
            sin_respuesta = 0
            activas, nuevas, resueltas = cambios(activas, o)
            for tipo, lista in (("ACTIVADA", nuevas), ("RESUELTA", resueltas)):
                for a in lista:
                    print(f"{ahora()}  {tipo}  {texto_alerta(a)}", flush=True)
                    if webhook and a["notifica"]:
                        notificar(webhook, a, tipo)
                    if al_cambiar:
                        al_cambiar(tipo, a)
            fila = {"momento": ahora(), **indicadores(o), "alertas": sorted(activas),
                    "activadas": [a["id"] for a in nuevas], "resueltas": [a["id"] for a in resueltas]}
            if bitacora:
                Path(bitacora).parent.mkdir(parents=True, exist_ok=True)
                with open(bitacora, "a", encoding="utf-8") as f:
                    f.write(json.dumps(fila, ensure_ascii=False) + "\n")
            if una_vez:
                print(json.dumps(fila, ensure_ascii=False, indent=2))
                return SALIDA_POR_ESTADO.get(o["estado"], 0)
            if hasta and hasta(o):
                return 0
        time.sleep(cada)


def main():
    p = argparse.ArgumentParser(description="Supervisa las alertas del predictor de flujo de efectivo.")
    p.add_argument("--base", default="http://127.0.0.1:8000")
    p.add_argument("--cada", type=float, default=30, help="segundos entre consultas")
    p.add_argument("--bitacora", help="archivo .jsonl donde se guarda cada consulta")
    p.add_argument("--webhook", help="URL que recibe las alertas que notifican (P1 y P2)")
    p.add_argument("--una-vez", action="store_true")
    a = p.parse_args()
    try:
        sys.exit(vigilar(a.base, a.cada, a.bitacora, a.webhook, a.una_vez))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
