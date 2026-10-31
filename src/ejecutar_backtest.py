"""Corre el experimento completo fuera de Colab y guarda tablas, figuras y Excel en results/.

Ejemplos:
    python src/ejecutar_backtest.py                       # los 8 modelos (descarga Chronos de Hugging Face)
    python src/ejecutar_backtest.py --sin-chronos         # solo ETS, SARIMAX y naive
    python src/ejecutar_backtest.py --max-origenes 40     # prueba rápida
"""
import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from flujo_efectivo import backtest as bt          # noqa: E402
from flujo_efectivo import entorno, graficas, latencia, metricas, reporte   # noqa: E402
from flujo_efectivo.config import RAIZ, Config     # noqa: E402
from flujo_efectivo.datos import cuentas_saldo_cero, huella_series, preparar_datos   # noqa: E402
from flujo_efectivo.modelos import FAMILIA, Registro, cargar_pipelines   # noqa: E402


def argumentos():
    p = argparse.ArgumentParser(description="Backtest del predictor de flujo de efectivo.")
    p.add_argument("--excel", type=Path, help="Export FEBAN en Excel o JSON (por omisión, el Excel de data/).")
    p.add_argument("--salida", type=Path, default=RAIZ / "results", help="Carpeta de resultados.")
    p.add_argument("--sin-chronos", action="store_true", help="Omite Chronos-2 y Chronos-Bolt Small.")
    p.add_argument("--chronos2", help="Repositorio o carpeta local de Chronos-2.")
    p.add_argument("--chronos-bolt", help="Repositorio o carpeta local de Chronos-Bolt Small.")
    p.add_argument("--horizonte", type=int, help="Días hábiles por pronóstico (por omisión 5).")
    p.add_argument("--max-origenes", type=int, help="Orígenes del backtest (por omisión 280).")
    p.add_argument("--fecha-corte", help="Fecha que separa las métricas por periodo (AAAA-MM-DD).")
    p.add_argument("--sin-latencia", action="store_true", help="Omite la medición de latencia y escalabilidad.")
    p.add_argument("--cpu", action="store_true", help="Corre Chronos en CPU aunque haya GPU.")
    return p.parse_args()


def main():
    a = argumentos()
    cfg = Config()
    if a.excel:
        cfg.archivo_excel = a.excel
    if a.horizonte:
        cfg.horizonte = cfg.horizonte_final = a.horizonte
    if a.max_origenes:
        cfg.max_origenes = a.max_origenes
    if a.fecha_corte:
        cfg.fecha_corte = a.fecha_corte
    if a.chronos2:
        cfg.repos_hf["Chronos-2"] = a.chronos2
    if a.chronos_bolt:
        cfg.repos_hf["Chronos-Bolt Small"] = a.chronos_bolt

    salida = a.salida
    (salida / "tablas").mkdir(parents=True, exist_ok=True)
    (salida / "figuras").mkdir(parents=True, exist_ok=True)
    inicio = time.perf_counter()

    dispositivo, dtype = entorno.detectar_dispositivo(forzar_cpu=a.cpu)
    entorno.fijar_semillas(cfg.semilla, dispositivo)
    sincronizar = entorno.crear_sincronizador(dispositivo)
    print(f"Dispositivo: {dispositivo} ({entorno.descripcion_gpu()})")

    # ---- datos
    datos = preparar_datos(cfg)
    for aviso in datos.avisos:
        print("Aviso:", aviso)
    print(f"{len(datos.crudo):,} filas -> {len(datos.mov):,} movimientos únicos; "
          f"{len(datos.fechas)} días hábiles ({datos.fechas[0]:%d-%m-%Y} a {datos.fechas[-1]:%d-%m-%Y}) "
          f"x {len(datos.ids)} series")
    for banco in cuentas_saldo_cero(datos.mov):
        print(f"{banco}: neto diario 0 todos los días (cuenta de saldo cero)")

    # ---- modelos
    pipelines, tabla_modelos, tiempos_carga = None, None, {}
    if not a.sin_chronos:
        token = os.environ.get("HF_TOKEN")
        pipelines, tabla_modelos, tiempos_carga = cargar_pipelines(cfg, dispositivo, dtype, token, sincronizar)
        print(tabla_modelos[["repositorio", "parámetros (M)", "carga (s)"]].to_string())
    registro = Registro(cfg, pipelines)
    print("Modelos:", ", ".join(registro.nombres))

    # ---- backtest y pronóstico
    resultados, lat_bt, lat_fria, duracion = bt.backtest(datos.series, registro, cfg, sincronizar)
    _, cortes = bt.origenes(datos.series, cfg)
    pronostico_final = bt.pronostico_adelante(datos.series, registro, cfg)
    tabla_final = bt.tabla_pronostico(pronostico_final, registro, cfg, datos.ids)

    # ---- métricas
    nombres = registro.nombres
    t_reg = metricas.tabla_regresion(resultados, cfg)
    t_clf = metricas.tabla_clasificacion(resultados)
    t_per = metricas.tabla_periodo(resultados, cfg)
    periodos = metricas.periodos_presentes(resultados, cfg)
    efecto = metricas.efecto_temporada(t_per, registro.pares_temporada, periodos)
    mae_mes = metricas.mensual(resultados, nombres, absoluto=True)
    sesgo_mes = metricas.mensual(resultados, nombres, absoluto=False)
    mae_serie = metricas.mae_por_serie(resultados, nombres, datos.ids)

    # ---- latencia
    t_lat = t_esc = None
    if not a.sin_latencia:
        print("Midiendo latencia...")
        t_lat = latencia.medir_latencia(datos.series, registro, cfg, dispositivo, sincronizar,
                                        tiempos_carga, lat_fria, lat_bt)
        print("Midiendo escalabilidad...")
        t_esc = latencia.escalabilidad(datos.series, registro, cfg, sincronizar)

    resumen = metricas.resumen(t_reg, t_clf, t_per, t_lat, periodos, nombres, cfg)
    print("\nResumen (ordenado por MAE):")
    print(resumen.drop(columns="Familia").round(3).to_string())

    # ---- figuras
    fig = salida / "figuras"
    con_par = [m for par in registro.pares_temporada.items() for m in par]
    corte = pd.Timestamp(cfg.fecha_corte)
    graficas.series_diarias(datos.pivote, fig / "01_series_diarias.png")
    graficas.backtest_corte(datos.series, resultados, con_par or nombres[:2], corte, fig / "02_backtest_corte.png")
    graficas.sesgo_mensual(sesgo_mes, con_par or nombres, cfg, fig / "03_sesgo_mensual.png")
    graficas.mae_mensual(mae_mes, nombres, cfg, fig / "04_mae_mensual.png")
    graficas.exactitud_costo(resumen, len(datos.ids), fig / "05_exactitud_vs_costo.png")
    if t_esc is not None:
        graficas.escalabilidad(t_esc, dispositivo, fig / "06_escalabilidad.png")
    graficas.siguiente_dia(pronostico_final, datos.series, nombres, cfg, fig / "07_pronostico_siguiente_dia.png")

    # ---- tablas
    tab = salida / "tablas"
    resumen.to_csv(tab / "resumen.csv", float_format="%.6f")
    t_reg.to_csv(tab / "metricas_regresion.csv", float_format="%.6f")
    t_clf.to_csv(tab / "metricas_clasificacion.csv", float_format="%.6f")
    t_per.to_csv(tab / "metricas_por_periodo.csv", float_format="%.6f")
    efecto.to_csv(tab / "efecto_temporada.csv", float_format="%.6f")
    metricas.vista_mensual(mae_mes, cfg).to_csv(tab / "mae_mensual.csv", float_format="%.6f")
    sesgo_mes.rename(index=lambda m: m.strftime("%b-%y")).to_csv(tab / "sesgo_mensual.csv", float_format="%.6f")
    mae_serie.to_csv(tab / "mae_por_serie.csv", float_format="%.6f")
    pronostico_final.to_csv(tab / "pronostico_siguientes_dias.csv", index=False, float_format="%.2f")
    tabla_final.to_csv(tab / "pronostico_siguientes_dias_vista.csv")
    datos.tabla_categorias.to_csv(tab / "categorias.csv", float_format="%.6f")
    datos.resumen_dedup.to_csv(tab / "deduplicacion.csv", float_format="%.6f")
    datos.series.to_csv(tab / "series_diarias.csv", index=False, float_format="%.2f")
    resultados.assign(mes=resultados["mes"].astype(str)).to_csv(
        tab / "backtest.csv.gz", index=False, float_format="%.2f", compression="gzip")
    if t_lat is not None:
        t_lat.to_csv(tab / "latencia.csv", float_format="%.3f")
        t_esc.to_csv(tab / "escalabilidad.csv", float_format="%.3f")
    if tabla_modelos is not None:
        tabla_modelos.to_csv(tab / "modelos_hf.csv", float_format="%.3f")

    reporte.exportar_excel(salida / "resultados_predictor_flujo.xlsx", {
        "resumen": (resumen, True), "metricas_regresion": (t_reg, True), "metricas_clasificacion": (t_clf, True),
        "metricas_por_periodo": (t_per, True), "efecto_temporada": (efecto, True),
        "mae_mensual": (mae_mes, True), "sesgo_mensual": (sesgo_mes, True), "mae_por_serie": (mae_serie, True),
        "latencia": (t_lat, True), "escalabilidad": (t_esc, True), "pronostico": (pronostico_final, False),
        "backtest": (resultados, False), "series_diarias": (datos.series, False),
        "categorias": (datos.tabla_categorias, True),
    })

    n_eval = len(resultados) // len(registro)
    lineas = reporte.lectura_corrida(resumen, t_reg, efecto, periodos, n_eval, len(cortes), len(datos.ids),
                                     len(datos.fechas), cfg, dispositivo)
    (salida / "lectura_corrida.md").write_text("# Lectura de la corrida\n\n" + "\n".join(lineas) + "\n",
                                               encoding="utf-8")

    corrida = {
        "fecha": datetime.now().isoformat(timespec="seconds"),
        "archivo": Path(cfg.archivo_excel).name,
        "huella": hashlib.sha256(Path(cfg.archivo_excel).read_bytes()).hexdigest(),
        "huella_series": huella_series(datos.series),
        "dispositivo": dispositivo,
        "modelos": nombres,
        "familias": {m: FAMILIA[m] for m in nombres},
        "parametros": {"horizonte": cfg.horizonte, "max_origenes": cfg.max_origenes, "origenes": len(cortes),
                       "min_contexto": cfg.min_contexto, "cuantiles": list(cfg.cuantiles),
                       "fecha_corte": cfg.fecha_corte, "sarimax_reestimar_cada": cfg.sarimax_reestimar_cada,
                       "min_dias_temporada": cfg.min_dias_temporada,
                       "meses_ingreso_alto": list(cfg.meses_ingreso_alto),
                       "meses_gasto_alto": list(cfg.meses_gasto_alto)},
        "datos": {"filas": len(datos.crudo), "movimientos_unicos": len(datos.mov), "dias_habiles": len(datos.fechas),
                  "desde": f"{datos.fechas[0]:%Y-%m-%d}", "hasta": f"{datos.fechas[-1]:%Y-%m-%d}",
                  "series": datos.ids},
        "pronosticos_por_modelo": n_eval,
        "duracion_backtest_s": {m: round(s, 1) for m, s in duracion.items()},
        "duracion_total_s": round(time.perf_counter() - inicio, 1),
        "versiones": entorno.versiones(),
    }
    (salida / "corrida.json").write_text(json.dumps(corrida, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nResultados en {salida} ({corrida['duracion_total_s']:,.0f} s)")


if __name__ == "__main__":
    main()
