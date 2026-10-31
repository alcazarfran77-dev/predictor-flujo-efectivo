"""Lectura del export FEBAN (Excel o JSON), deduplicación, clasificación y construcción de las series diarias
(secciones 4.2 a 4.5)."""
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd


@dataclass
class Datos:
    crudo: pd.DataFrame
    mov: pd.DataFrame
    resumen_dedup: pd.DataFrame
    tabla_categorias: pd.DataFrame
    series: pd.DataFrame
    fechas: pd.DatetimeIndex
    ids: list
    avisos: list
    validacion: object = None          # ResultadoValidacion cuando los datos vienen de un JSON

    @property
    def pivote(self):
        return self.series.pivot(index="ds", columns="unique_id", values="y")[self.ids]


def cargar_excel(cfg, ruta=None):
    ruta = Path(ruta or cfg.archivo_excel)
    if not ruta.exists():
        raise FileNotFoundError(f"No encontré '{ruta}'. Revisa la carpeta data/ o pasa --excel.")
    col = cfg.columnas
    encabezado = pd.read_excel(ruta, sheet_name=cfg.hoja_excel, nrows=0).columns
    como_texto = {col[k]: str for k in ("linea_extracto", "clave_extracto") if col[k] in encabezado}
    crudo = pd.read_excel(ruta, sheet_name=cfg.hoja_excel, dtype=como_texto)

    faltan = [col[k] for k in ("fecha", "monto", "banco", "concepto") if col[k] not in crudo.columns]
    if faltan:
        raise KeyError(f"Faltan columnas obligatorias {faltan}. Columnas del archivo: {list(crudo.columns)}")

    crudo = crudo.rename(columns={v: k for k, v in col.items() if v in crudo.columns})
    crudo["fecha"] = pd.to_datetime(crudo["fecha"], dayfirst=True).dt.normalize().astype("datetime64[ns]")
    crudo["monto"] = pd.to_numeric(crudo["monto"], errors="coerce")
    crudo["banco"] = crudo["banco"].astype(str).str.strip().str.upper()

    avisos = []
    sin_monto = int(crudo["monto"].isna().sum())
    if sin_monto:
        avisos.append(f"{sin_monto} filas sin monto numérico se descartan.")
        crudo = crudo.dropna(subset=["monto"])
    if "moneda" in crudo.columns:
        monedas = crudo["moneda"].value_counts()
        if len(monedas) > 1 or cfg.moneda not in monedas.index:
            avisos.append(f"Monedas encontradas: {monedas.to_dict()}; se conserva solo {cfg.moneda}.")
        crudo = crudo[crudo["moneda"] == cfg.moneda].copy()
    return crudo, avisos


def cargar_json(cfg, ruta):
    """Lee y valida el JSON de movimientos. Si falta algo necesario, levanta DatosInvalidos con el detalle."""
    from .validacion import DatosInvalidos, validar_archivo

    resultado = validar_archivo(ruta, cfg)
    if not resultado.valido:
        raise DatosInvalidos(resultado)
    crudo = resultado.crudo.copy()
    crudo["tipo_tx"] = crudo["tipo_tx"].fillna("")
    return crudo, list(resultado.avisos), resultado


def huella_series(series):
    """SHA-256 de las series diarias. Es la misma si los datos llegan en Excel o en JSON."""
    texto = series[["unique_id", "ds", "y"]].to_csv(index=False, float_format="%.4f", date_format="%Y-%m-%d")
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def deduplicar(crudo, cfg):
    """Un movimiento único = banco + MR no + concepto + monto + fecha valor."""
    llave = [c for c in cfg.llave_movimiento if c in crudo.columns]
    resumen = crudo.groupby("banco").agg(filas_export=("monto", "size"))
    if "clave_extracto" in crudo.columns:
        resumen["cargas_del_extracto"] = crudo.groupby("banco")["clave_extracto"].nunique()

    mov = (crudo.drop_duplicates(subset=llave) if cfg.deduplicar else crudo).copy().reset_index(drop=True)

    resumen["movimientos_unicos"] = mov.groupby("banco").size()
    resumen["neto_export (M MXN)"] = crudo.groupby("banco")["monto"].sum() / 1e6
    resumen["neto_real (M MXN)"] = mov.groupby("banco")["monto"].sum() / 1e6
    resumen.loc["TOTAL"] = resumen.sum(numeric_only=True)
    if "cargas_del_extracto" in resumen:
        resumen.loc["TOTAL", "cargas_del_extracto"] = np.nan
    resumen.insert(resumen.columns.get_loc("movimientos_unicos") + 1, "veces_repetido",
                   resumen["filas_export"] / resumen["movimientos_unicos"])
    return mov, resumen


def clasificar(concepto, tipo, monto, cfg):
    texto, tipo = str(concepto).upper(), str(tipo).upper()
    for categoria, patron, tipos, es_tesoreria in cfg.reglas_categoria:
        if re.search(patron, texto) or tipo in tipos:
            return categoria, es_tesoreria
    return (cfg.categoria_ingreso_default if monto >= 0 else cfg.categoria_egreso_default), False


def clasificar_movimientos(mov, cfg):
    tipos_tx = mov["tipo_tx"] if "tipo_tx" in mov.columns else pd.Series("", index=mov.index)
    clasificacion = [clasificar(c, t, m, cfg) for c, t, m in zip(mov["concepto"], tipos_tx, mov["monto"])]
    mov["categoria"] = [c for c, _ in clasificacion]
    mov["es_tesoreria"] = np.array([t for _, t in clasificacion], dtype=bool)
    mov["flujo"] = np.where(mov["es_tesoreria"], "Tesorería", "Operativo")

    tabla = (
        mov.groupby(["flujo", "categoria"])
        .agg(movimientos=("monto", "size"),
             ingresos=("monto", lambda s: s[s > 0].sum() / 1e6),
             egresos=("monto", lambda s: s[s < 0].sum() / 1e6),
             neto=("monto", lambda s: s.sum() / 1e6))
        .rename(columns={"ingresos": "ingresos (M MXN)", "egresos": "egresos (M MXN)", "neto": "neto (M MXN)"})
    )
    return mov, tabla


def cuentas_saldo_cero(mov):
    """Bancos cuyo neto diario es 0 todos los días (cuenta concentradora)."""
    neto = mov.groupby(["fecha", "banco"])["monto"].sum().unstack("banco")
    return [b for b in neto.columns if (neto[b].abs() < 1).all()]


def construir_series(mov, cfg):
    """OPERATIVO_TOTAL, OPERATIVO_<banco> y NETO_TOTAL en días hábiles; fines de semana al siguiente día hábil."""
    if cfg.frecuencia.upper() == "B":
        mov["fecha_serie"] = mov["fecha"].map(pd.offsets.BDay().rollforward).astype("datetime64[ns]")
    else:
        mov["fecha_serie"] = mov["fecha"]
    fechas = pd.date_range(mov["fecha_serie"].min(), mov["fecha_serie"].max(),
                           freq=cfg.frecuencia).astype("datetime64[ns]")

    definiciones = {"OPERATIVO_TOTAL": mov[~mov["es_tesoreria"]]}
    if cfg.series_por_banco:
        for banco in sorted(mov["banco"].unique()):
            definiciones[f"OPERATIVO_{banco}"] = mov[~mov["es_tesoreria"] & (mov["banco"] == banco)]
    definiciones["NETO_TOTAL"] = mov
    partes = []
    for nombre, sub in definiciones.items():
        diario = sub.groupby("fecha_serie")["monto"].sum().reindex(fechas, fill_value=0.0)
        partes.append(pd.DataFrame({"unique_id": nombre, "ds": fechas, "y": diario.to_numpy(dtype=float)}))
    return pd.concat(partes, ignore_index=True), pd.DatetimeIndex(fechas)


def preparar_datos(cfg, ruta=None):
    """Excel (.xlsx) para el cuaderno y el script; JSON (.json, validado) para la API."""
    ruta = Path(ruta or cfg.archivo_excel)
    validacion = None
    if ruta.suffix.lower() == ".json":
        crudo, avisos, validacion = cargar_json(cfg, ruta)
    else:
        crudo, avisos = cargar_excel(cfg, ruta)
    mov, resumen_dedup = deduplicar(crudo, cfg)
    mov, tabla_categorias = clasificar_movimientos(mov, cfg)
    series, fechas = construir_series(mov, cfg)
    ids = list(series["unique_id"].unique())
    return Datos(crudo, mov, resumen_dedup, tabla_categorias, series, fechas, ids, avisos, validacion)
