"""Compara cada modelo del backtest contra ETS y dice si la mejora es significativa.

Lee `results/tablas/backtest.csv.gz` (lo escribe `ejecutar_backtest.py`) y guarda la comparación en `results/`.

Ejemplos:
    python src/comparar_modelos.py
    python src/comparar_modelos.py --backtest otra/backtest.csv.gz --referencia ETS --mejora-minima 5
"""
import argparse
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from flujo_efectivo import significancia as sig     # noqa: E402
from flujo_efectivo.config import RAIZ              # noqa: E402
from flujo_efectivo.modelos import FAMILIA          # noqa: E402


def escribir(resultados, salida, referencia=sig.REFERENCIA, mejora_minima=sig.MEJORA_MINIMA, avance=print):
    """Calcula la comparación y escribe las tablas y la lectura. Devuelve (tabla, por_serie, por_periodo, lineas)."""
    salida = Path(salida)
    (salida / "tablas").mkdir(parents=True, exist_ok=True)
    opciones = {"mejora_minima": mejora_minima}
    tabla = sig.comparar(resultados, referencia, **opciones)
    por_serie = sig.comparar_por_grupo(resultados, "unique_id", referencia, **opciones)
    por_periodo = sig.comparar_por_grupo(resultados, "periodo", referencia, **opciones)
    familia = {m: FAMILIA.get(m, "") for m in resultados["modelo"].unique()}
    lineas = sig.lectura(tabla, referencia, familia, mejora_minima)

    tabla.to_csv(salida / "tablas" / "comparacion_vs_ets.csv", float_format="%.6f")
    por_serie.to_csv(salida / "tablas" / "comparacion_vs_ets_por_serie.csv", float_format="%.6f")
    por_periodo.to_csv(salida / "tablas" / "comparacion_vs_ets_por_periodo.csv", float_format="%.6f")
    n = resultados["origen"].nunique()
    texto = [f"# ¿Mejora algún modelo a {referencia} de forma significativa?", "",
             f"Comparación pareada sobre {n} orígenes del backtest. Prueba de Diebold-Mariano con varianza HAC, "
             f"intervalo por bootstrap de bloques y ajuste de Holm. Mejora mínima exigida: "
             f"{mejora_minima * 100:.0f} % de MAE.", ""] + lineas
    (salida / "comparacion_vs_ets.md").write_text("\n".join(texto) + "\n", encoding="utf-8")
    if avance:
        columnas = ["MAE (M MXN)", "Mejora MAE (%)", "IC95 inferior (%)", "IC95 superior (%)", "p ajustada (Holm)",
                    "Veredicto MAE", "Mejora WQL (%)", "Veredicto WQL"]
        avance(tabla[columnas].round(3).to_string() if not tabla.empty else "Sin modelos que comparar.")
        avance("\n" + "\n".join(lineas))
    return tabla, por_serie, por_periodo, lineas


def main():
    p = argparse.ArgumentParser(description="Comparación pareada de los modelos del backtest contra ETS.")
    p.add_argument("--backtest", type=Path, default=RAIZ / "results" / "tablas" / "backtest.csv.gz")
    p.add_argument("--salida", type=Path, default=RAIZ / "results")
    p.add_argument("--referencia", default=sig.REFERENCIA)
    p.add_argument("--mejora-minima", type=float, default=sig.MEJORA_MINIMA * 100, help="En porcentaje de MAE.")
    a = p.parse_args()
    resultados = pd.read_csv(a.backtest, parse_dates=["ds", "origen"])
    print(f"{a.backtest}: {resultados['modelo'].nunique()} modelos, {resultados['origen'].nunique()} orígenes")
    tabla, *_ = escribir(resultados, a.salida, a.referencia, a.mejora_minima / 100)
    print(json.dumps({"archivo": str(a.salida / 'comparacion_vs_ets.md'), "modelos": len(tabla)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
