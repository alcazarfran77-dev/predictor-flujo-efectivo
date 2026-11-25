"""Prueba de carga de la puerta G1 contra la API desplegada (Render, Cloud Run o local).

Corre desde Colab o Codespaces, sin instalar nada más que httpx (ya viene en requirements-api.txt):

    python -m herramientas.prueba_carga https://predictor-flujo.onrender.com
    python -m herramientas.prueba_carga https://... --salida g1_render.json --segundos 15

Mide lo mismo que la validación con límites emulados del reporte técnico y compara contra los umbrales del plan:
- 16 clientes con GET /pronostico: p95 de 500 ms o menos y 0 errores.
- Pico supuesto, 5 personas abren el tablero en el mismo segundo (40 peticiones): 95 % en menos de 1 s.
- 40 clientes sostenidos: 60 req/s o más.
- POST /pronostico con ETS: p95 menor a 1 s.
- Memoria del proceso (de /metrics): 80 % o menos de la RAM del plan (--ram-mb, 512 por omisión).
La latencia incluye la red entre el cliente y el servicio: córrela desde una región cercana o descuenta el tiempo de ida
y vuelta que reporta `rtt_ms`.
"""
import argparse
import asyncio
import json
import statistics
import sys
import time
from datetime import date, timedelta

import httpx

TABLERO = ["/api/v1/salud", "/api/v1/series", "/api/v1/pronostico?serie=OPERATIVO_TOTAL&horizonte=5",
           "/api/v1/series/OPERATIVO_TOTAL/historia?dias=30", "/api/v1/metricas?serie=OPERATIVO_TOTAL",
           "/api/v1/alertas", "/api/v1/modelos", "/api/v1/comparacion"]
GET_P = "/api/v1/pronostico?serie=OPERATIVO_TOTAL"


def percentil(xs, q):
    xs = sorted(xs)
    return xs[int(q * (len(xs) - 1))] if xs else None


async def carga(base, encabezados, metodo, ruta, concurrencia, segundos, **kw):
    lat, errores = [], 0
    fin = time.perf_counter() + segundos
    async with httpx.AsyncClient(base_url=base, headers=encabezados, timeout=60,
                                 limits=httpx.Limits(max_connections=concurrencia)) as c:
        async def cliente():
            nonlocal errores
            while time.perf_counter() < fin:
                t = time.perf_counter()
                try:
                    r = await c.request(metodo, ruta, **kw)
                    errores += r.status_code != 200
                except httpx.HTTPError:
                    errores += 1
                lat.append((time.perf_counter() - t) * 1000)
        t0 = time.perf_counter()
        await asyncio.gather(*[cliente() for _ in range(concurrencia)])
        dur = time.perf_counter() - t0
    return {"concurrencia": concurrencia, "peticiones": len(lat), "rps": len(lat) / dur, "p50_ms": percentil(lat, .5),
            "p95_ms": percentil(lat, .95), "max_ms": max(lat), "errores": errores,
            "pct_menor_1s": 100 * sum(x < 1000 for x in lat) / len(lat)}


async def rafagas(base, encabezados, usuarios=5, repeticiones=20):
    lats, cargas, errores = [], [], 0
    async with httpx.AsyncClient(base_url=base, headers=encabezados, timeout=60,
                                 limits=httpx.Limits(max_connections=usuarios * len(TABLERO))) as c:
        async def una(ruta):
            nonlocal errores
            t = time.perf_counter()
            try:
                errores += (await c.get(ruta)).status_code != 200
            except httpx.HTTPError:
                errores += 1
            lats.append((time.perf_counter() - t) * 1000)

        async def usuario():
            t = time.perf_counter()
            await asyncio.gather(*[una(r) for r in TABLERO])
            cargas.append((time.perf_counter() - t) * 1000)
        for _ in range(repeticiones):
            await asyncio.gather(*[usuario() for _ in range(usuarios)])
            await asyncio.sleep(1)
    return {"peticiones": len(lats), "errores": errores, "peticion_p95_ms": percentil(lats, .95),
            "carga_tablero_p95_ms": percentil(cargas, .95), "pct_menor_1s": 100 * sum(x < 1000 for x in lats) / len(lats)}


def memoria_mb(base, encabezados):
    try:
        for linea in httpx.get(base + "/metrics", headers=encabezados, timeout=30).text.splitlines():
            if linea.startswith("flujo_proceso_memoria_bytes"):
                return float(linea.split()[-1]) / 2**20
    except httpx.HTTPError:
        pass
    return None


def main(argv=None):
    p = argparse.ArgumentParser(description="Prueba de carga de la puerta G1")
    p.add_argument("url")
    p.add_argument("--token", help="Token de acceso, si la API lo pide (encabezado Authorization: Bearer)")
    p.add_argument("--segundos", type=int, default=10)
    p.add_argument("--ram-mb", type=float, default=512)
    p.add_argument("--salida", help="Archivo JSON con todas las mediciones")
    a = p.parse_args(argv)
    base = a.url.rstrip("/")
    enc = {"Authorization": f"Bearer {a.token}"} if a.token else {}

    with httpx.Client(base_url=base, headers=enc, timeout=120) as c:
        salud = c.get("/api/v1/salud").json()                       # despierta el servicio y confirma la versión
        rtt = [c.get("/api/v1/salud").elapsed.total_seconds() * 1000 for _ in range(10)]
    dias, d = [], date(2026, 4, 1)
    while len(dias) < 120:                                          # 120 días hábiles, como en la validación
        if d.weekday() < 5:
            dias.append(d.isoformat())
        d += timedelta(days=1)
    historia = [{"fecha": f, "valor": 2.7e6 + 1e5 * ((i * 7) % 11 - 5)} for i, f in enumerate(dias)]
    post = {"json": {"serie_id": "erp", "modelo": "ETS", "horizonte": 5, "historia": historia}}
    r = {"url": base, "momento": time.strftime("%Y-%m-%d %H:%M:%S"), "version_api": salud.get("version"),
         "artefacto": (salud.get("artefacto") or {}).get("version"), "rtt_ms": statistics.median(rtt),
         "memoria_inicio_mb": memoria_mb(base, enc)}
    r["get"] = [asyncio.run(carga(base, enc, "GET", GET_P, n, a.segundos)) for n in (1, 4, 16, 40)]
    r["post_ets"] = [asyncio.run(carga(base, enc, "POST", "/api/v1/pronostico", n, a.segundos, **post)) for n in (1, 4)]
    r["rafagas_tablero"] = asyncio.run(rafagas(base, enc))
    r["memoria_fin_mb"] = memoria_mb(base, enc)
    g16, g40 = r["get"][2], r["get"][3]
    mem = max(x for x in (r["memoria_inicio_mb"], r["memoria_fin_mb"], 0) if x is not None)
    r["criterios"] = {
        "p95 con 16 clientes <= 500 ms y 0 errores": g16["p95_ms"] <= 500 and g16["errores"] == 0,
        "pico de 40 peticiones: 95 % en menos de 1 s": r["rafagas_tablero"]["pct_menor_1s"] >= 95,
        "capacidad con 40 clientes >= 60 req/s": g40["rps"] >= 60,
        "POST con ETS: p95 < 1 s": r["post_ets"][-1]["p95_ms"] < 1000,
        f"memoria <= 80 % de {a.ram_mb:g} MB": (mem or 0) <= 0.8 * a.ram_mb if mem else None,
    }
    r["aprueba_G1"] = all(v for v in r["criterios"].values() if v is not None)
    if a.salida:
        with open(a.salida, "w", encoding="utf-8") as f:
            json.dump(r, f, ensure_ascii=False, indent=1)
    for k, v in r["criterios"].items():
        print(("✓ " if v else "✗ " if v is False else "· ") + k)
    print(f"GET 16 clientes: {g16['rps']:.0f} req/s, p95 {g16['p95_ms']:.0f} ms · 40 clientes: {g40['rps']:.0f} req/s · "
          f"RTT {r['rtt_ms']:.0f} ms · memoria {mem or float('nan'):.0f} MB")
    print("Aprueba G1" if r["aprueba_G1"] else "No aprueba G1")
    return 0 if r["aprueba_G1"] else 1


if __name__ == "__main__":
    sys.exit(main())
