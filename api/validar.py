"""Valida un archivo JSON de movimientos sin levantar la API.

    python -m api.validar data/movimientos_feban_sep2025_sep2026_anon.json
    python -m api.validar movimientos.json --json      # resultado completo en JSON

Termina con código 0 si el archivo tiene lo necesario para pronosticar y 1 si no.
"""
import argparse
import json
import sys
from pathlib import Path

from ._rutas import RAIZ  # noqa: F401  (agrega src/ al path)
from flujo_efectivo.config import ARCHIVO_JSON, Config
from flujo_efectivo.validacion import validar_archivo


def main(argv=None):
    p = argparse.ArgumentParser(description="Valida un JSON de movimientos para el predictor de flujo de efectivo.")
    p.add_argument("archivo", type=Path, nargs="?", default=ARCHIVO_JSON)
    p.add_argument("--json", action="store_true", help="Imprime el resultado completo en JSON")
    a = p.parse_args(argv)
    r = validar_archivo(a.archivo, Config())
    if a.json:
        print(json.dumps(r.a_dict(), ensure_ascii=False, indent=2))
    else:
        print(r.texto())
        if r.valido:
            res = r.resumen
            print(f"{res['movimientos_validos']:,} movimientos del {res['desde']} al {res['hasta']} "
                  f"({res['dias_habiles']} días hábiles); bancos: {', '.join(f'{b} {n:,}' for b, n in res['bancos'].items())}")
    return 0 if r.valido else 1


if __name__ == "__main__":
    sys.exit(main())
