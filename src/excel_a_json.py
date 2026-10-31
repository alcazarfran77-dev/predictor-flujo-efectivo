"""Convierte un export FEBAN en Excel al formato JSON que lee la API, y lo valida.

    python src/excel_a_json.py data/DEPOSITOS_CARGADOS_FEBAN_sep2025_sep2026_anon.xlsx \
        data/movimientos_feban_sep2025_sep2026_anon.json

El JSON queda con un movimiento por línea, para que los cambios se lean bien en git.
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from flujo_efectivo.config import Config                       # noqa: E402
from flujo_efectivo.validacion import CAMPOS, FORMATO, validar_archivo   # noqa: E402

ORDEN = ["fecha", "monto", "banco", "concepto", "tipo_tx", "linea_extracto", "clave_extracto", "regla_contable", "moneda"]


def registros(ruta_excel, cfg):
    col = cfg.columnas
    datos = pd.read_excel(ruta_excel, sheet_name=cfg.hoja_excel,
                          dtype={col["linea_extracto"]: str, col["clave_extracto"]: str})
    salida = []
    for fila in datos.to_dict("records"):
        mov = {}
        for interno in ORDEN:
            valor = fila.get(col[interno])
            if valor is None or (isinstance(valor, float) and pd.isna(valor)):
                continue
            nombre = CAMPOS[interno][0]
            if interno == "fecha":
                valor = pd.Timestamp(valor).strftime("%Y-%m-%d")
            elif interno == "monto":
                valor = float(valor)
            else:
                valor = str(valor)
            mov[nombre] = valor
        salida.append(mov)
    return salida


def escribir(movimientos, destino, origen, cfg):
    cabecera = {
        "formato": FORMATO, "version": "1.0", "moneda": cfg.moneda, "fuente": "Export FEBAN de SAP",
        "origen": Path(origen).name, "generado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    lineas = ["{"] + [f"  {json.dumps(k)}: {json.dumps(v, ensure_ascii=False)}," for k, v in cabecera.items()]
    lineas.append('  "movimientos": [')
    cuerpo = [f"    {json.dumps(m, ensure_ascii=False)}" for m in movimientos]
    lineas.append(",\n".join(cuerpo))
    lineas += ["  ]", "}"]
    Path(destino).write_text("\n".join(lineas) + "\n", encoding="utf-8")


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("excel", type=Path)
    p.add_argument("json", type=Path)
    a = p.parse_args()
    cfg = Config()
    movs = registros(a.excel, cfg)
    escribir(movs, a.json, a.excel, cfg)
    r = validar_archivo(a.json, cfg)
    print(f"{len(movs):,} movimientos escritos en {a.json}")
    print(r.texto())
    sys.exit(0 if r.valido else 1)


if __name__ == "__main__":
    main()
