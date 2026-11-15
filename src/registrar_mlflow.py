"""Registra el backtest, el monitoreo y el modelo recomendado en MLflow.

Crea dos experimentos y un modelo registrado:

- `flujo-efectivo-backtest`: una corrida por modelo con sus parámetros, métricas, umbrales de aceptación, veredicto
  frente a ETS y las series de rMAE y cobertura móviles (para verlas como gráficas en MLflow).
- `flujo-efectivo-monitoreo`: una corrida por ejecución con el data drift (PSI y prueba KS por serie y su historia)
  y, si se indica `--api`, los SLO, los presupuestos de error y las alertas activas de la API.
- Modelo `flujo-efectivo-pronostico`: ETS empaquetado como pyfunc con el alias `campeon`; la corrida del mejor
  modelo evaluado queda marcada como `retador`.

Ejemplos:
    pip install mlflow
    python src/registrar_mlflow.py                                  # escribe en mlflow.db y mlartifacts/
    python src/registrar_mlflow.py --api http://127.0.0.1:8000      # agrega el estado operativo de la API
    mlflow ui --backend-store-uri sqlite:///mlflow.db               # abre la interfaz en http://127.0.0.1:5000
"""
import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

import mlflow
import numpy as np
import pandas as pd
from mlflow.entities import Metric
from mlflow.tracking import MlflowClient

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api import confianza                               # noqa: E402
from flujo_efectivo import drift, significancia         # noqa: E402
from flujo_efectivo.config import ARCHIVO_JSON, RAIZ, Config   # noqa: E402
from flujo_efectivo.datos import huella_series, preparar_datos   # noqa: E402
from flujo_efectivo.modelos import FAMILIA, Registro    # noqa: E402

EXP_BACKTEST, EXP_MONITOREO, MODELO_REGISTRADO = "flujo-efectivo-backtest", "flujo-efectivo-monitoreo", "flujo-efectivo-pronostico"
SERIE_PRINCIPAL, NAIVE, REFERENCIA = "OPERATIVO_TOTAL", confianza.NAIVE, "ETS"


class PronosticoETS(mlflow.pyfunc.PythonModel):
    """Recibe la historia (unique_id, ds, y) y devuelve P10, P50 y P90 de los siguientes días hábiles."""

    def predict(self, context, model_input, params=None):
        from flujo_efectivo.config import Config as Cfg
        from flujo_efectivo.modelos import Registro as Reg
        cfg = Cfg()
        historia = model_input.assign(ds=pd.to_datetime(model_input["ds"]))
        return Reg(cfg).funciones["ETS"](historia, int((params or {}).get("horizonte", cfg.horizonte)))


def metricas_escalonadas(cliente, run_id, nombre, puntos):
    """Sube una serie (fecha, valor) como métrica con paso y marca de tiempo, para graficarla en MLflow."""
    lote = [Metric(nombre, float(p["valor"]), int(pd.Timestamp(p["fecha"]).timestamp() * 1000), i)
            for i, p in enumerate(puntos) if p["valor"] is not None and np.isfinite(p["valor"])]
    for i in range(0, len(lote), 900):
        cliente.log_batch(run_id, metrics=lote[i:i + 900])


def registrar_backtest(cliente, bt, cfg, fuente, datos, con_modelo=True):
    mlflow.set_experiment(EXP_BACKTEST)
    bn = bt[bt["modelo"] == NAIVE]
    comparacion = significancia.comparar(bt, REFERENCIA, horizonte=cfg.horizonte)
    servidos = set(Registro(cfg).nombres)
    corridas = {}
    for modelo in bt["modelo"].unique():
        bm = bt[bt["modelo"] == modelo]
        t = confianza.metricas_basicas(bm, confianza._mae(bn))
        sesgo, _ = confianza.sesgo_tras_cambio(bm, cfg)
        umbrales = confianza.evaluar_umbrales({"rmae": t["rmae"], "wql": t["wql"], "cobertura": t["cobertura"],
                                               "f1_direccion": t["f1_direccion"], "sesgo_cambio": sesgo, "latencia": None})
        evaluados = [u for u in umbrales if u["cumple"] is not None]
        sm, sn = bm[bm["unique_id"] == SERIE_PRINCIPAL], bn[bn["unique_id"] == SERIE_PRINCIPAL]
        mon = confianza.monitoreo(sm, sn, 0)
        with mlflow.start_run(run_name=modelo) as corrida:
            mlflow.set_tags({"familia": FAMILIA.get(modelo, ""), "servido_por_la_api": str(modelo in servidos).lower(),
                             "fuente_backtest": fuente, "huella_series": huella_series(datos.series),
                             "rol": "campeon" if modelo == REFERENCIA else "referencia" if modelo == NAIVE else "candidato"})
            mlflow.log_params({"modelo": modelo, "horizonte": cfg.horizonte, "origenes": bm["origen"].nunique(),
                               "series": bm["unique_id"].nunique(), "pronosticos": len(bm), "cuantiles": "0.1, 0.5, 0.9",
                               "datos_desde": f"{datos.fechas[0]:%Y-%m-%d}", "datos_hasta": f"{datos.fechas[-1]:%Y-%m-%d}",
                               "fecha_corte": cfg.fecha_corte})
            mlflow.log_metrics({"mae_mxn": t["mae"], "rmae": t["rmae"], "wql": t["wql"], "cobertura_p10_p90": t["cobertura"],
                                "f1_direccion": t["f1_direccion"], "sesgo_mxn": t["sesgo"],
                                "umbrales_cumplidos": sum(1 for u in evaluados if u["cumple"]), "umbrales_evaluados": len(evaluados),
                                "rmae_movil_actual": mon["rmae_movil"]["valor"], "cobertura_movil_actual": mon["cobertura_movil"]["valor"],
                                "alarma_rmae": float(mon["rmae_movil"]["alerta"]), "alarma_cobertura": float(mon["cobertura_movil"]["alerta"])})
            if sesgo is not None and np.isfinite(sesgo):
                mlflow.log_metric("sesgo_tras_cambio_mmxn", sesgo)
            if modelo in comparacion.index:
                r = comparacion.loc[modelo]
                mlflow.log_metrics({"mejora_mae_vs_ets_pct": r["Mejora MAE (%)"], "ic95_inferior_pct": r["IC95 inferior (%)"],
                                    "ic95_superior_pct": r["IC95 superior (%)"], "p_ajustada": r["p ajustada (Holm)"],
                                    "mejora_wql_vs_ets_pct": r["Mejora WQL (%)"], "origenes_en_que_gana_pct": r["Orígenes en que gana (%)"]})
                mlflow.set_tag("veredicto_vs_ets", r["Veredicto MAE"])
            metricas_escalonadas(cliente, corrida.info.run_id, "rmae_movil_20d", mon["rmae_movil"]["serie"])
            metricas_escalonadas(cliente, corrida.info.run_id, "cobertura_movil_40d", mon["cobertura_movil"]["serie"])
            mlflow.log_dict({"umbrales": umbrales}, "umbrales_aceptacion.json")
            por_h = pd.DataFrame(confianza.por_horizonte(bm, bn))
            mlflow.log_table(por_h, "metricas_por_horizonte.json")
            if modelo == REFERENCIA and con_modelo:
                mlflow.pyfunc.log_model(name="modelo", python_model=PronosticoETS(), code_paths=[str(RAIZ / "src" / "flujo_efectivo")],
                                        registered_model_name=MODELO_REGISTRADO,
                                        pip_requirements=["pandas", "numpy", "scipy", "statsmodels", "scikit-learn"])
            corridas[modelo] = corrida.info.run_id
    if con_modelo:
        version = max(cliente.search_model_versions(f"name='{MODELO_REGISTRADO}'"), key=lambda v: int(v.version))
        cliente.set_registered_model_alias(MODELO_REGISTRADO, "campeon", version.version)
        cliente.update_registered_model(MODELO_REGISTRADO, description=(
            "Pronóstico diario de flujo de efectivo (P10, P50 y P90). El alias campeon apunta al modelo que recomienda la API."))
        ref = confianza.metricas_basicas(bt[bt["modelo"] == REFERENCIA], confianza._mae(bn))
        cliente.set_model_version_tag(MODELO_REGISTRADO, version.version, "mae_mxn", f"{ref['mae']:.0f}")
        cliente.set_model_version_tag(MODELO_REGISTRADO, version.version, "datos_hasta", f"{datos.fechas[-1]:%Y-%m-%d}")
    mejores = comparacion[comparacion["Veredicto MAE"] == significancia.V_MEJORA]
    if len(mejores):
        retador = mejores["Mejora MAE (%)"].idxmax()
        cliente.set_tag(corridas[retador], "rol", "retador")
    return corridas, comparacion


def registrar_monitoreo(cliente, datos, api=None):
    mlflow.set_experiment(EXP_MONITOREO)
    res = drift.resumen(datos.series, datos.mov)
    with mlflow.start_run(run_name=f"monitoreo datos al {datos.fechas[-1]:%Y-%m-%d}") as corrida:
        mlflow.set_tags({"nivel_drift": res["nivel"], "datos_hasta": f"{datos.fechas[-1]:%Y-%m-%d}"})
        mlflow.log_params({f"criterio_{k}": v for k, v in res["criterio"].items()})
        for f in res["series"]:
            if "psi" in f:
                mlflow.log_metrics({f"drift_psi.{f['serie']}": f["psi"], f"drift_ks_p.{f['serie']}": f["p_ks"],
                                    f"drift_cambio_media.{f['serie']}": f["cambio_media_sd"]})
            historia = drift.historia_drift(datos.series, f["serie"], paso=1)
            metricas_escalonadas(cliente, corrida.info.run_id, f"historia_psi.{f['serie']}", [{"fecha": h["fecha"], "valor": h["psi"]} for h in historia])
            metricas_escalonadas(cliente, corrida.info.run_id, f"historia_ks_p.{f['serie']}", [{"fecha": h["fecha"], "valor": h["p_ks"]} for h in historia])
        for f in res["composicion"]:
            mlflow.log_metric("drift_psi." + f["indicador"].replace(" ", "_").replace("í", "i"), f["psi"])
        mlflow.log_dict(res, "drift.json")
        mlflow.log_table(pd.DataFrame(res["series"]), "drift_series.json")
        if api:
            import requests
            o = requests.get(f"{api.rstrip('/')}/api/v1/observabilidad", timeout=120).json()
            mlflow.set_tags({"estado_operativo": o["estado"], "modelo_recomendado": o["modelo"]["recomendado"],
                             "respuesta_automatica": o["respuesta_automatica"]["modo"]})
            for s in o["slos"]:
                if s.get("sli_pct") is not None:
                    mlflow.log_metric(f"slo_indicador.{s['id']}", s["sli_pct"])
                if s.get("presupuesto_restante_pct") is not None:
                    mlflow.log_metric(f"slo_presupuesto_restante.{s['id']}", s["presupuesto_restante_pct"])
            for prioridad in ("P1", "P2", "P3", "P4"):
                mlflow.log_metric(f"alertas_activas.{prioridad}", sum(1 for a in o["alertas"] if a["prioridad"] == prioridad))
            t = o["trafico"]["total"]
            mlflow.log_metrics({"api_peticiones": t["peticiones"], "api_errores_5xx": t["errores_5xx"],
                                "api_p95_ms": t["p95_ms"] or 0, "datos_dias_atraso": next(s["valor"] for s in o["slos"] if s["id"] == "frescura")})
            if o["alertas"]:
                mlflow.log_table(pd.DataFrame(o["alertas"])[["prioridad", "id", "nombre", "resumen", "runbook", "desde", "notifica"]], "alertas_activas.json")
            mlflow.log_dict(o, "observabilidad.json")
            with tempfile.TemporaryDirectory() as tmp:
                ruta = Path(tmp) / "metricas_prometheus.txt"
                ruta.write_text(requests.get(f"{api.rstrip('/')}/metrics", timeout=120).text, encoding="utf-8")
                mlflow.log_artifact(str(ruta))
        return corrida.info.run_id, res


def main():
    p = argparse.ArgumentParser(description="Registra backtest, monitoreo y modelo en MLflow.")
    p.add_argument("--uri", default=f"sqlite:///{RAIZ / 'mlflow.db'}", help="URI de seguimiento de MLflow.")
    p.add_argument("--datos", type=Path, default=ARCHIVO_JSON)
    p.add_argument("--backtest", type=Path, help="backtest.csv.gz (por omisión, el de Colab si existe; si no, el de CPU).")
    p.add_argument("--api", help="URL de la API para registrar SLO, alertas y métricas operativas.")
    p.add_argument("--sin-modelo", action="store_true", help="No empaqueta ni registra el modelo.")
    p.add_argument("--solo-monitoreo", action="store_true", help="Registra solo la corrida de monitoreo.")
    a = p.parse_args()
    mlflow.set_tracking_uri(a.uri)
    cliente, cfg, t0 = MlflowClient(a.uri), Config(), time.time()
    datos = preparar_datos(cfg, a.datos)
    if not a.solo_monitoreo:
        colab = RAIZ / "results" / "colab_gpu" / "tablas" / "backtest.csv.gz"
        archivo = a.backtest or (colab if colab.exists() else RAIZ / "results" / "tablas" / "backtest.csv.gz")
        bt = pd.read_csv(archivo, parse_dates=["ds", "origen"])
        corridas, comparacion = registrar_backtest(cliente, bt, cfg, str(archivo.relative_to(RAIZ)) if archivo.is_relative_to(RAIZ) else archivo.name,
                                                   datos, con_modelo=not a.sin_modelo)
        print(f"Backtest: {len(corridas)} corridas en '{EXP_BACKTEST}'.")
    run_id, res = registrar_monitoreo(cliente, datos, a.api)
    print(f"Monitoreo: corrida {run_id} en '{EXP_MONITOREO}' (drift {res['nivel']}).")
    print(json.dumps({"uri": a.uri, "segundos": round(time.time() - t0, 1)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
