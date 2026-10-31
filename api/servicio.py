"""Servicio de pronóstico: datos, modelos, backtest en caché, pronósticos, alertas, confianza y reporte JSON.

La API (api/main.py) crea una sola instancia al arrancar. Los movimientos llegan en un archivo JSON que se valida
antes de usarlo (src/flujo_efectivo/validacion.py). El backtest de cada modelo se lee de
results/tablas/backtest.csv.gz si corresponde a las mismas series (misma huella SHA-256); los modelos que falten
se calculan en un hilo aparte y se guardan en api_cache/ para el siguiente arranque. Cuando el modelo recomendado
tiene pronóstico y backtest, el servicio escribe el reporte completo en salidas/.
"""
import hashlib
import json
import logging
import math
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from . import VERSION, alertas, confianza
from ._rutas import RAIZ  # noqa: F401  (agrega src/ al path)
from flujo_efectivo import backtest as bt
from flujo_efectivo.config import ARCHIVO_JSON, Config
from flujo_efectivo.covariables import fechas_futuras
from flujo_efectivo.datos import huella_series, preparar_datos
from flujo_efectivo.modelos import FAMILIA, Registro
from flujo_efectivo.validacion import DatosInvalidos, leer_json

log = logging.getLogger("flujo.api")
NAIVE = confianza.NAIVE
DESCRIPCIONES = {
    "OPERATIVO_TOTAL": "Flujo operativo total (todos los bancos, sin tesorería)",
    "NETO_TOTAL": "Flujo neto con tesorería (barridos, fondos y traspasos)",
}
SERIE_PRINCIPAL = "OPERATIVO_TOTAL"
FORMATO_REPORTE = "flujo-efectivo/pronostico"
NIVELES_ALERTA = {
    "ninguna": "El P10 de todos los días queda en cero o arriba",
    "vigilancia": "El P10 de algún día es menor a 0 (regla del reporte técnico)",
    "media": f"La probabilidad de déficit de algún día es de {alertas.UMBRAL_MEDIA:.0%} o más",
    "alta": "El P50 de algún día es menor a 0: el déficit es más probable que no",
}


def limpiar_json(obj):
    """Convierte tipos de numpy y valores no finitos para escribir JSON estándar."""
    if isinstance(obj, dict):
        return {str(k): limpiar_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [limpiar_json(v) for v in obj]
    if isinstance(obj, (np.bool_, bool)):
        return bool(obj)
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (float, np.floating)):
        return float(obj) if math.isfinite(obj) else None
    return obj


def nombre_archivo(modelo, origen):
    return f"pronostico_{re.sub(r'[^A-Za-z0-9]+', '_', modelo).strip('_')}_{origen}.json"


def huella_archivo(ruta):
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


def descripcion_serie(uid):
    if uid in DESCRIPCIONES:
        return DESCRIPCIONES[uid]
    if uid.startswith("OPERATIVO_"):
        return f"Flujo operativo {uid.removeprefix('OPERATIVO_')} (sin tesorería)"
    return uid


class ErrorServicio(Exception):
    def __init__(self, codigo, mensaje):
        super().__init__(mensaje)
        self.codigo, self.mensaje = codigo, mensaje


class ServicioPronostico:
    def __init__(self, ruta_datos=None, modelo_recomendado="ETS", usar_chronos=False, repos_hf=None,
                 dir_resultados=None, dir_cache=None, calcular_faltantes=True, umbral_deficit=0.0,
                 precalentar=True, dir_salidas=None, escribir_salida=True):
        self.cfg = Config()
        self.ruta_datos = Path(ruta_datos or ARCHIVO_JSON)
        self.dir_salidas = Path(dir_salidas or RAIZ / "salidas")
        self.escribir_salida = escribir_salida
        self.ultimo_archivo = None
        self._salida_inicial = threading.Lock()
        self._salida_escrita = False
        if repos_hf:
            self.cfg.repos_hf.update(repos_hf)
        self.modelo_recomendado = modelo_recomendado
        self.usar_chronos = usar_chronos
        self.dir_resultados = Path(dir_resultados or RAIZ / "results")
        self.dir_cache = Path(dir_cache or RAIZ / "api_cache")
        self.calcular_faltantes = calcular_faltantes
        self.precalentar = precalentar
        self.umbral_deficit = float(umbral_deficit)

        self._lock_bt = threading.Lock()
        self._locks = {}
        self._pronosticos, self._latencias, self._confianza = {}, {}, {}
        self._lock_externo = threading.Lock()
        self.estado_backtest = {}
        self.hilo = None
        self.listo = False

    # ------------------------------------------------------------------ arranque
    def iniciar(self):
        t0 = time.perf_counter()
        if self.ruta_datos.suffix.lower() != ".json":                 # la API solo lee JSON
            raise DatosInvalidos(leer_json(self.ruta_datos)[1])
        self.datos = preparar_datos(self.cfg, self.ruta_datos)        # valida el JSON; si falta algo, se detiene aquí
        self.huella = huella_series(self.datos.series)
        self.huella_archivo = huella_archivo(self.ruta_datos)
        self.duplicados = len(self.datos.crudo) - len(self.datos.mov)

        self.pipelines = None
        if self.usar_chronos:
            import os

            from flujo_efectivo import entorno
            from flujo_efectivo.modelos import cargar_pipelines
            dispositivo, dtype = entorno.detectar_dispositivo()
            self.pipelines, _, _ = cargar_pipelines(self.cfg, dispositivo, dtype, os.environ.get("HF_TOKEN"))
        self.registro = Registro(self.cfg, self.pipelines)
        self.modelos = self.registro.nombres
        if self.modelo_recomendado not in self.modelos:
            self.modelo_recomendado = "ETS"
        for m in self.modelos:
            self._locks[m] = threading.Lock()

        self.backtest = self._leer_backtest()
        presentes = set(self.backtest["modelo"].unique()) if len(self.backtest) else set()
        for m in self.modelos:
            self.estado_backtest[m] = "listo" if m in presentes else "pendiente"
        faltantes = [m for m in self.modelos if m not in presentes]
        if faltantes and self.calcular_faltantes:
            faltantes.sort(key=lambda m: (m != NAIVE, m != self.modelo_recomendado))
            self.hilo = threading.Thread(target=self._calcular_backtest, args=(faltantes,), daemon=True)
            self.hilo.start()
        if self.precalentar:
            threading.Thread(target=self._precalentar, daemon=True).start()
        elif self.escribir_salida and not faltantes:
            threading.Thread(target=self._escribir_salida_inicial, daemon=True).start()
        self.listo = True
        log.info("Servicio listo en %.1f s; backtest en caché: %s; por calcular: %s",
                 time.perf_counter() - t0, sorted(presentes), faltantes)
        return self

    def _precalentar(self):
        """Calcula en segundo plano el pronóstico de cada modelo (el recomendado primero)."""
        for m in sorted(self.modelos, key=lambda m: m != self.modelo_recomendado):
            try:
                self.pronostico_modelo(m)
            except Exception:
                log.exception("Falló el pronóstico de %s", m)
            if m == self.modelo_recomendado:
                self._escribir_salida_inicial()

    def _escribir_salida_inicial(self):
        """Escribe una vez el reporte del modelo recomendado, cuando ya tiene pronóstico y backtest."""
        if not self.escribir_salida:
            return
        with self._salida_inicial:
            if self._salida_escrita or self.estado_backtest.get(self.modelo_recomendado) != "listo" \
                    or self.estado_backtest.get(NAIVE) != "listo":
                return
            try:
                ruta, _ = self.guardar_reporte(self.modelo_recomendado)
                self._salida_escrita = True
                log.info("Reporte JSON escrito en %s", ruta)
            except Exception:
                log.exception("No se pudo escribir el reporte JSON")

    def _leer_backtest(self):
        partes = []
        corrida = self.dir_resultados / "corrida.json"
        archivo = self.dir_resultados / "tablas" / "backtest.csv.gz"
        if corrida.exists() and archivo.exists():
            meta = json.loads(corrida.read_text(encoding="utf-8"))
            if (meta.get("huella_series") == self.huella
                    and meta.get("parametros", {}).get("horizonte") == self.cfg.horizonte):
                partes.append(pd.read_csv(archivo, parse_dates=["ds", "origen"]))
        cache = self._ruta_cache()
        if cache.exists():
            partes.append(pd.read_csv(cache, parse_dates=["ds", "origen"]))
        if not partes:
            return pd.DataFrame(columns=["modelo"])
        b = pd.concat(partes, ignore_index=True)
        b = b.drop_duplicates(subset=["modelo", "unique_id", "origen", "h"], keep="last")
        return b[b["modelo"].isin(self.modelos)].reset_index(drop=True)

    def _ruta_cache(self):
        return self.dir_cache / f"backtest_{self.huella[:16]}_h{self.cfg.horizonte}.csv.gz"

    def _calcular_backtest(self, faltantes):
        """Corre en un hilo: usa su propio registro para no compartir la caché de SARIMAX con las peticiones."""
        registro = Registro(self.cfg, self.pipelines)
        todas = dict(registro.funciones)          # ligadas a la caché de SARIMAX de este registro
        for m in faltantes:
            self.estado_backtest[m] = "calculando"
            registro.funciones = {m: todas[m]}
            try:
                res, _, _, _ = bt.backtest(self.datos.series, registro, self.cfg, avance=log.info)
                columnas = [c for c in res.columns if c != "mes"]
                with self._lock_bt:
                    self.backtest = pd.concat([self.backtest, res[columnas]], ignore_index=True)
                    self._confianza.clear()
                    try:                                  # la caché solo ahorra tiempo en el siguiente arranque
                        self.dir_cache.mkdir(parents=True, exist_ok=True)
                        self.backtest.to_csv(self._ruta_cache(), index=False, compression="gzip")
                    except OSError as e:
                        log.warning("No se pudo guardar la caché del backtest en %s: %s", self.dir_cache, e)
                self.estado_backtest[m] = "listo"
                self._escribir_salida_inicial()
            except Exception:
                log.exception("Falló el backtest de %s", m)
                self.estado_backtest[m] = "error"

    # ------------------------------------------------------------------ consultas
    def validar(self, modelo=None, serie=None):
        if modelo is not None and modelo not in self.modelos:
            raise ErrorServicio(404, f"Modelo '{modelo}' no disponible. Opciones: {', '.join(self.modelos)}")
        if serie is not None and serie not in self.datos.ids:
            raise ErrorServicio(404, f"Serie '{serie}' no existe. Opciones: {', '.join(self.datos.ids)}")

    def info_datos(self):
        f = self.datos.fechas
        v = self.datos.validacion
        return {
            "archivo": self.ruta_datos.name, "formato": "json", "huella": self.huella[:16],
            "desde": f[0].strftime("%Y-%m-%d"), "hasta": f[-1].strftime("%Y-%m-%d"),
            "dias_habiles": len(f), "movimientos": len(self.datos.mov), "duplicados": self.duplicados,
            "avisos": list(v.avisos) if v else [],
        }

    def resultado_validacion(self):
        v = self.datos.validacion
        return v.a_dict() if v else {"valido": True, "total_errores": 0, "errores": [], "avisos": [], "resumen": {}}

    def series(self):
        s = self.datos.series
        filas = []
        for uid in self.datos.ids:
            d = s[s["unique_id"] == uid]
            filas.append({"id": uid, "descripcion": descripcion_serie(uid), "principal": uid == SERIE_PRINCIPAL,
                          "ultima_fecha": d["ds"].iloc[-1].strftime("%Y-%m-%d"), "ultimo_valor": float(d["y"].iloc[-1])})
        return filas

    def historia(self, serie, dias):
        d = self.datos.series[self.datos.series["unique_id"] == serie].tail(dias)
        return [{"fecha": f.strftime("%Y-%m-%d"), "valor": float(v)} for f, v in zip(d["ds"], d["y"])]

    def _backtest_de(self, modelo):
        with self._lock_bt:
            b = self.backtest
        return b[b["modelo"] == modelo]

    def pronostico_modelo(self, modelo):
        """Pronóstico de todas las series con toda la historia (se calcula una vez por modelo)."""
        with self._locks[modelo]:
            if modelo not in self._pronosticos:
                t0 = time.perf_counter()
                pred = self.registro.funciones[modelo](self.datos.series, self.cfg.horizonte_final)
                self._latencias[modelo] = time.perf_counter() - t0
                self._pronosticos[modelo] = (pred, datetime.now(timezone.utc).isoformat(timespec="seconds"))
                return pred, self._pronosticos[modelo][1], False
        pred, generado = self._pronosticos[modelo]
        return pred, generado, True

    def pronostico(self, serie, modelo, horizonte):
        self.validar(modelo, serie)
        pred, generado, en_cache = self.pronostico_modelo(modelo)
        p = pred[pred["unique_id"] == serie].sort_values("ds").head(horizonte)
        dias = alertas.tabla_dias(p, self.umbral_deficit)
        dias["h"] = np.arange(1, len(dias) + 1)

        conf = self.metricas_confianza(modelo, serie)
        por_h = {f["h"]: f for f in conf["por_horizonte"]} if conf else {}
        salida_dias = []
        for fila in dias.to_dict("records"):
            c = por_h.get(fila["h"])
            salida_dias.append({
                "fecha": fila["fecha"], "h": int(fila["h"]),
                "p10": fila["p10"], "p50": fila["p50"], "p90": fila["p90"],
                "prob_deficit": float(fila["prob_deficit"]),
                "alerta": {"nivel": fila["nivel"], "activa": fila["nivel"] != "ninguna"},
                "confianza": ({k: c[k] for k in ("mae", "rmae", "cobertura", "sesgo", "n")} if c else None),
            })
        resumen = alertas.resumen_alerta(dias, self.umbral_deficit)
        resumen["regla"] = "Alerta a tesorería cuando el P10 de algún día del horizonte es menor a 0 (reporte, sección 7)"
        resumen["desempeno"] = conf["reglas_deficit"] if conf else []
        ultima = self.datos.series.loc[self.datos.series["unique_id"] == serie, "ds"].max()
        return {
            "serie": serie, "descripcion": descripcion_serie(serie), "modelo": modelo,
            "familia": FAMILIA.get(modelo, ""), "origen": ultima.strftime("%Y-%m-%d"),
            "generado": generado, "latencia_ms": self._latencias[modelo] * 1000, "en_cache": en_cache,
            "unidades": "MXN", "cuantiles": list(self.cfg.cuantiles), "umbral_deficit": self.umbral_deficit,
            "dias": salida_dias, "acumulado": alertas.acumulado(dias), "alerta_deficit": resumen,
            "confianza": self._resumen_confianza(conf, modelo),
        }

    def _resumen_confianza(self, conf, modelo):
        if not conf:
            return {"disponible": False, "estado_backtest": self.estado_backtest.get(modelo, "pendiente")}
        return {
            "disponible": True, "estado_backtest": "listo", "nivel": conf["nivel_confianza"],
            "umbrales_cumplidos": sum(1 for u in conf["umbrales"] if u["cumple"]),
            "umbrales_total": len(conf["umbrales"]), "alarmas_monitoreo": conf["alarmas"],
            "mae_serie": conf["serie_metricas"]["mae"], "rmae_serie": conf["serie_metricas"]["rmae"],
            "cobertura_serie": conf["serie_metricas"]["cobertura"],
        }

    def metricas_confianza(self, modelo, serie=None):
        """Métricas de confianza del modelo (y de la serie, si se indica). None si el backtest no está listo."""
        self.validar(modelo, serie)
        if self.estado_backtest.get(modelo) != "listo" or self.estado_backtest.get(NAIVE) != "listo":
            return None
        clave = (modelo, serie)
        if clave in self._confianza:
            return self._confianza[clave]
        bm, bn = self._backtest_de(modelo), self._backtest_de(NAIVE)
        todas = confianza.metricas_basicas(bm, confianza._mae(bn))
        if serie:
            sm, sn = bm[bm["unique_id"] == serie], bn[bn["unique_id"] == serie]
        else:
            sm, sn = bm, bn
        de_serie = confianza.metricas_basicas(sm, confianza._mae(sn))
        sesgo, fecha_cambio = confianza.sesgo_tras_cambio(bm, self.cfg)
        if modelo not in self._latencias:
            self.pronostico_modelo(modelo)
        valores = {"rmae": todas["rmae"], "wql": todas["wql"], "cobertura": todas["cobertura"],
                   "f1_direccion": todas["f1_direccion"], "sesgo_cambio": sesgo,
                   "latencia": self._latencias.get(modelo)}
        umbrales = confianza.evaluar_umbrales(valores)
        mon = confianza.monitoreo(sm, sn, self.duplicados)
        nivel, fallas, alarmas = confianza.nivel_confianza(umbrales, mon)
        corte = pd.Timestamp(self.cfg.fecha_corte)
        resultado = {
            "modelo": modelo, "serie": serie, "disponible": True,
            "origenes": int(bm["origen"].nunique()),
            "periodo_backtest": [bm["ds"].min().strftime("%Y-%m-%d"), bm["ds"].max().strftime("%Y-%m-%d")],
            "todas_las_series": todas, "serie_metricas": de_serie,
            "desde_corte": confianza.metricas_basicas(sm[sm["ds"] >= corte], confianza._mae(sn[sn["ds"] >= corte])),
            "fecha_corte": self.cfg.fecha_corte,
            "por_horizonte": confianza.por_horizonte(sm, sn),
            "umbrales": umbrales, "fecha_cambio_temporada": fecha_cambio,
            "monitoreo": mon, "reglas_deficit": alertas.desempeno_reglas(sm, self.umbral_deficit),
            "nivel_confianza": nivel, "fallas": fallas, "alarmas": alarmas,
        }
        self._confianza[clave] = resultado
        return resultado

    def alertas_series(self, modelo):
        """Resumen de alerta de déficit de todas las series con un modelo."""
        self.validar(modelo)
        filas = []
        for uid in self.datos.ids:
            p = self.pronostico(uid, modelo, self.cfg.horizonte_final)
            a = p["alerta_deficit"]
            filas.append({"serie": uid, "descripcion": p["descripcion"], "principal": uid == SERIE_PRINCIPAL,
                          **{k: a[k] for k in ("activa", "nivel", "dias_en_alerta", "primera_fecha",
                                               "probabilidad_maxima", "peor_p10", "colchon_sugerido")},
                          "p50_acumulado": p["acumulado"]["p50"]})
        return filas

    def comparacion(self):
        """Modelos lado a lado con los criterios de aceptación y de cambio de modelo del reporte."""
        filas = []
        ref = self.metricas_confianza(self.modelo_recomendado) if self.estado_backtest.get(self.modelo_recomendado) == "listo" else None
        for m in self.modelos:
            fila = {"modelo": m, "familia": FAMILIA.get(m, ""), "recomendado": m == self.modelo_recomendado,
                    "estado_backtest": self.estado_backtest.get(m, "pendiente")}
            c = self.metricas_confianza(m) if fila["estado_backtest"] == "listo" else None
            fila["latencia_ms"] = self._latencias[m] * 1000 if m in self._latencias else None
            if c:
                t, dc = c["todas_las_series"], c["desde_corte"]
                fila.update({"mae": t["mae"], "rmae": t["rmae"], "wql": t["wql"], "cobertura": t["cobertura"],
                             "f1_direccion": t["f1_direccion"], "mae_desde_corte": dc["mae"] if dc else None,
                             "umbrales_cumplidos": sum(1 for u in c["umbrales"] if u["cumple"]),
                             "umbrales_total": len(c["umbrales"]), "nivel_confianza": c["nivel_confianza"]})
                if ref and m != self.modelo_recomendado and m != NAIVE:
                    r_global = t["mae"] / ref["todas_las_series"]["mae"]
                    r_corte = (dc["mae"] / ref["desde_corte"]["mae"]) if dc and ref["desde_corte"] else None
                    fila["mae_vs_recomendado"] = r_global
                    fila["reemplaza_al_recomendado"] = bool(
                        r_global <= 0.95 and r_corte is not None and r_corte <= 0.95
                        and fila["umbrales_cumplidos"] == fila["umbrales_total"])
            filas.append(fila)
        return filas

    def pronostico_externo(self, historia, modelo, horizonte, serie_id="externa"):
        """Pronostica una serie enviada por otro sistema (por ejemplo, el ERP) sin tocar los datos cargados."""
        self.validar(modelo)
        if len(historia) < 10:
            raise ErrorServicio(422, "La historia necesita al menos 10 días.")
        d = pd.DataFrame(historia)
        d["fecha"] = pd.to_datetime(d["fecha"]).map(pd.offsets.BDay().rollforward)
        diario = d.groupby("fecha")["valor"].sum()
        fechas = pd.date_range(diario.index.min(), diario.index.max(), freq=self.cfg.frecuencia)
        diario = diario.reindex(fechas, fill_value=0.0)
        ctx = pd.DataFrame({"unique_id": serie_id, "ds": fechas.astype("datetime64[ns]"), "y": diario.to_numpy(float)})
        t0 = time.perf_counter()
        with self._lock_externo:
            pred = self.registro.funciones[modelo](ctx, horizonte)
        latencia = time.perf_counter() - t0
        dias = alertas.tabla_dias(pred, self.umbral_deficit)
        dias["h"] = np.arange(1, len(dias) + 1)
        resumen = alertas.resumen_alerta(dias, self.umbral_deficit)
        resumen["regla"] = "Alerta cuando el P10 de algún día del horizonte es menor a 0"
        resumen["desempeno"] = []
        return {
            "serie": serie_id, "descripcion": "Serie enviada en la petición", "modelo": modelo,
            "familia": FAMILIA.get(modelo, ""), "origen": fechas[-1].strftime("%Y-%m-%d"),
            "generado": datetime.now(timezone.utc).isoformat(timespec="seconds"), "latencia_ms": latencia * 1000,
            "en_cache": False, "unidades": "MXN", "cuantiles": list(self.cfg.cuantiles),
            "umbral_deficit": self.umbral_deficit,
            "dias": [{"fecha": r["fecha"], "h": int(r["h"]), "p10": r["p10"], "p50": r["p50"], "p90": r["p90"],
                      "prob_deficit": float(r["prob_deficit"]),
                      "alerta": {"nivel": r["nivel"], "activa": r["nivel"] != "ninguna"}, "confianza": None}
                     for r in dias.to_dict("records")],
            "acumulado": alertas.acumulado(dias), "alerta_deficit": resumen,
            "confianza": {"disponible": False, "estado_backtest": "sin backtest para series externas"},
        }

    def reporte(self, modelo=None):
        """Pronóstico completo de un modelo, listo para escribirse como archivo JSON."""
        modelo = modelo or self.modelo_recomendado
        self.validar(modelo)
        conf = self.metricas_confianza(modelo)
        series = []
        for uid in self.datos.ids:
            p = self.pronostico(uid, modelo, self.cfg.horizonte_final)
            series.append({
                "serie": uid, "descripcion": p["descripcion"], "principal": uid == SERIE_PRINCIPAL,
                "origen": p["origen"], "dias": p["dias"], "acumulado": p["acumulado"],
                "alerta_deficit": p["alerta_deficit"], "confianza": p["confianza"],
            })
        if conf:
            mon = {k: {kk: vv for kk, vv in v.items() if kk != "serie"} for k, v in conf["monitoreo"].items()}
            confianza_modelo = {
                "disponible": True, "estado_backtest": "listo", "nivel": conf["nivel_confianza"],
                "origenes": conf["origenes"], "periodo_backtest": conf["periodo_backtest"],
                "metricas": conf["todas_las_series"], "desde_corte": conf["desde_corte"],
                "fecha_corte": conf["fecha_corte"], "umbrales": conf["umbrales"],
                "fecha_cambio_temporada": conf["fecha_cambio_temporada"], "monitoreo": mon,
            }
        else:
            confianza_modelo = {"disponible": False, "estado_backtest": self.estado_backtest.get(modelo, "pendiente")}
        _, generado, _ = self.pronostico_modelo(modelo)
        return limpiar_json({
            "formato": FORMATO_REPORTE, "version": "1.0", "version_api": VERSION,
            "generado": datetime.now(timezone.utc).isoformat(timespec="seconds"), "pronostico_calculado": generado,
            "datos": self.info_datos(), "modelo": modelo, "familia": FAMILIA.get(modelo, ""),
            "recomendado": modelo == self.modelo_recomendado, "latencia_ms": self._latencias[modelo] * 1000,
            "horizonte": self.cfg.horizonte_final, "cuantiles": list(self.cfg.cuantiles), "unidades": "MXN",
            "umbral_deficit": self.umbral_deficit, "niveles_alerta": NIVELES_ALERTA,
            "confianza": confianza_modelo, "series": series,
        })

    def guardar_reporte(self, modelo=None, directorio=None):
        """Escribe el reporte en salidas/ (de forma atómica) y devuelve (ruta, reporte)."""
        rep = self.reporte(modelo)
        destino_dir = Path(directorio or self.dir_salidas)
        destino_dir.mkdir(parents=True, exist_ok=True)
        destino = destino_dir / nombre_archivo(rep["modelo"], rep["series"][0]["origen"])
        temporal = destino.with_suffix(".json.tmp")
        temporal.write_text(json.dumps(rep, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        os.replace(temporal, destino)
        self.ultimo_archivo = destino
        return destino, rep

    def ruta_relativa(self, ruta):
        ruta = Path(ruta).resolve()
        try:
            return ruta.relative_to(RAIZ).as_posix()
        except ValueError:
            return str(ruta)

    def fechas_siguientes(self):
        return [f.strftime("%Y-%m-%d") for f in fechas_futuras(self.datos.fechas[-1], self.cfg.horizonte_final, self.cfg)]
