"""Genera el reporte JSON del pronóstico sin levantar la API.

    python -m api.exportar                                   # modelo recomendado, en salidas/
    python -m api.exportar --modelo SARIMAX --salidas otra/carpeta
    python -m api.exportar --datos movimientos.json

Valida el JSON de entrada, calcula el backtest si no está en caché y escribe
salidas/pronostico_<modelo>_<último día con datos>.json.
"""
import argparse
import sys
from pathlib import Path

from .servicio import ServicioPronostico
from flujo_efectivo.validacion import DatosInvalidos


def main(argv=None):
    p = argparse.ArgumentParser(description="Escribe el pronóstico completo en un archivo JSON.")
    p.add_argument("--datos", type=Path, help="JSON de movimientos (por omisión, el de data/)")
    p.add_argument("--modelo", default="ETS")
    p.add_argument("--salidas", type=Path, help="Carpeta de salida (por omisión, salidas/)")
    a = p.parse_args(argv)
    s = ServicioPronostico(ruta_datos=a.datos, modelo_recomendado=a.modelo, dir_salidas=a.salidas,
                           precalentar=False, calcular_faltantes=True, escribir_salida=False)
    try:
        s.iniciar()
    except DatosInvalidos as e:
        print(e, file=sys.stderr)
        return 1
    if s.hilo is not None:
        print("Calculando el backtest que falta para las métricas de confianza...")
        s.hilo.join()
    ruta, rep = s.guardar_reporte(a.modelo)
    print(f"Reporte escrito en {ruta} ({ruta.stat().st_size:,} bytes)")
    for serie in rep["series"]:
        al = serie["alerta_deficit"]
        print(f"  {serie['serie']:16s} alerta {al['nivel']:10s} peor P10 {al['peor_p10'] / 1e6:8.2f} M  "
              f"colchón {al['colchon_sugerido'] / 1e6:6.2f} M")
    c = rep["confianza"]
    if c["disponible"]:
        print(f"Confianza {c['nivel']}: cumple {sum(1 for u in c['umbrales'] if u['cumple'])} de {len(c['umbrales'])} umbrales")
    return 0


if __name__ == "__main__":
    sys.exit(main())
