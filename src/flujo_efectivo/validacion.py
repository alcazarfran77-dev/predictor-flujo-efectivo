"""Lectura y validación del archivo JSON de movimientos bancarios.

El archivo es un objeto con una lista `movimientos` (o directamente la lista). Cada movimiento necesita fecha
valor, monto, banco y concepto; el resto de los campos mejora la limpieza y la clasificación, y si falta se
avisa. Los campos se pueden escribir con los nombres del formato (`fecha`, `monto`, ...) o con los encabezados
del export FEBAN (`Value date`, `Amount`, ...), así que un export convertido tal cual también sirve.

La validación junta todos los problemas en una sola pasada y dice en qué movimiento y en qué campo está cada
uno, en lugar de detenerse en el primero. El esquema formal está en data/esquema_movimientos.json.
"""
import json
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from .datos import clasificar

FORMATO = "flujo-efectivo/movimientos"
VERSION_SOPORTADA = "1"
MAX_ERRORES_DETALLE = 50
DIAS_RECOMENDADOS = 60
HUECO_AVISO = 5

# campo interno -> (nombre en el JSON, otros nombres aceptados, obligatorio, descripción)
CAMPOS = {
    "fecha": ("fecha", ("Value date", "fecha_valor"), True, "fecha valor AAAA-MM-DD"),
    "monto": ("monto", ("Amount", "importe"), True, "importe en la moneda del archivo; negativo = salida"),
    "banco": ("banco", ("House Bk",), True, "banco de la cuenta"),
    "concepto": ("concepto", ("Payment Notes",), True, "concepto del banco; con él se separa la tesorería"),
    "tipo_tx": ("tipo_transaccion", ("External transaction", "tipo_tx"), False, "tipo de transacción del banco"),
    "linea_extracto": ("partida", ("MR no", "linea_extracto"), False, "número de partida dentro del extracto"),
    "clave_extracto": ("clave_extracto", ("Shrt key",), False, "clave de la carga del extracto"),
    "regla_contable": ("regla_contable", ("Post. rule",), False, "regla contable de SAP"),
    "moneda": ("moneda", ("AC",), False, "moneda del movimiento"),
}
OBLIGATORIOS = [k for k, v in CAMPOS.items() if v[2]]
_FECHA = re.compile(r"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?)?$")


class DatosInvalidos(ValueError):
    """El archivo no tiene lo necesario para pronosticar. `resultado` trae el detalle."""

    def __init__(self, resultado):
        self.resultado = resultado
        super().__init__(resultado.texto())


@dataclass
class ResultadoValidacion:
    valido: bool
    errores: list = field(default_factory=list)
    total_errores: int = 0
    avisos: list = field(default_factory=list)
    resumen: dict = field(default_factory=dict)
    crudo: pd.DataFrame = None

    def error(self, mensaje, fila=None, campo=None):
        self.total_errores += 1
        if len(self.errores) < MAX_ERRORES_DETALLE:
            self.errores.append({"fila": fila, "campo": campo, "mensaje": mensaje})
        self.valido = False

    def a_dict(self):
        return {"valido": self.valido, "total_errores": self.total_errores, "errores": self.errores,
                "avisos": self.avisos, "resumen": self.resumen}

    def texto(self):
        if self.valido:
            return "Datos válidos" + (": " + "; ".join(self.avisos) if self.avisos else ".")
        lineas = [f"El archivo no tiene lo necesario para pronosticar ({self.total_errores} problema(s)):"]
        for e in self.errores:
            donde = []
            if e["fila"] is not None:
                donde.append(f"movimiento {e['fila']}")
            if e["campo"]:
                donde.append(f"campo '{e['campo']}'")
            lineas.append(f"  - {', '.join(donde) + ': ' if donde else ''}{e['mensaje']}")
        if self.total_errores > len(self.errores):
            lineas.append(f"  ... y {self.total_errores - len(self.errores)} más")
        return "\n".join(lineas)


def _buscar(mov, interno):
    """Valor del campo con su nombre del formato o con un alias. Devuelve (presente, valor, nombre usado)."""
    nombre, alias, _, _ = CAMPOS[interno]
    for n in (nombre, *alias):
        if n in mov:
            return True, mov[n], n
    return False, None, nombre


def _fecha(valor):
    if not isinstance(valor, str) or not _FECHA.match(valor.strip()):
        return None
    texto = valor.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(texto).date() if len(texto) > 10 else date.fromisoformat(texto)
    except ValueError:
        return None


def _texto(valor):
    if isinstance(valor, bool) or valor is None:
        return None
    if isinstance(valor, (int, float)) and not (isinstance(valor, float) and not math.isfinite(valor)):
        return str(valor)
    return valor if isinstance(valor, str) else None


def leer_json(ruta):
    """Abre el archivo. Devuelve (contenido, None) o (None, ResultadoValidacion con el error)."""
    ruta = Path(ruta)
    r = ResultadoValidacion(valido=True)
    if ruta.suffix.lower() != ".json":
        r.error(f"Se esperaba un archivo .json y llegó '{ruta.name}'. Para convertir un export de Excel usa "
                "src/excel_a_json.py.")
        return None, r
    if not ruta.exists():
        r.error(f"No encontré el archivo '{ruta}'.")
        return None, r
    try:
        return json.loads(ruta.read_text(encoding="utf-8-sig")), None
    except UnicodeDecodeError:
        r.error("El archivo no está en UTF-8.")
    except json.JSONDecodeError as e:
        r.error(f"No es un JSON válido: {e.msg} (línea {e.lineno}, columna {e.colno}).")
    return None, r


def validar_movimientos(contenido, cfg):
    """Valida la estructura, cada movimiento y que la historia alcance para pronosticar."""
    r = ResultadoValidacion(valido=True)

    # ---- estructura
    moneda_archivo = cfg.moneda
    if isinstance(contenido, dict):
        formato = contenido.get("formato")
        if formato is not None and formato != FORMATO:
            r.error(f"'formato' debe ser '{FORMATO}' y llegó '{formato}'.", campo="formato")
        version = str(contenido.get("version", VERSION_SOPORTADA))
        if version.split(".")[0] != VERSION_SOPORTADA:
            r.error(f"Versión {version} no soportada; esta API lee la versión {VERSION_SOPORTADA}.x.", campo="version")
        if "moneda" in contenido:
            if not isinstance(contenido["moneda"], str) or not contenido["moneda"].strip():
                r.error("'moneda' debe ser un código de moneda, por ejemplo 'MXN'.", campo="moneda")
            else:
                moneda_archivo = contenido["moneda"].strip().upper()
        if "movimientos" not in contenido:
            r.error("Falta la lista 'movimientos'.", campo="movimientos")
            return r
        movimientos = contenido["movimientos"]
    elif isinstance(contenido, list):
        movimientos = contenido
    else:
        r.error("El archivo debe ser un objeto con la lista 'movimientos' o directamente una lista de movimientos.")
        return r
    if not isinstance(movimientos, list):
        r.error("'movimientos' debe ser una lista.", campo="movimientos")
        return r
    if not movimientos:
        r.error("La lista 'movimientos' está vacía.", campo="movimientos")
        return r

    # ---- cada movimiento
    filas, presentes = [], Counter()
    sin_concepto = 0
    for i, mov in enumerate(movimientos, start=1):
        if not isinstance(mov, dict):
            r.error("Cada movimiento debe ser un objeto con sus campos.", fila=i)
            continue
        fila, ok = {}, True
        for interno in OBLIGATORIOS:
            esta, valor, nombre = _buscar(mov, interno)
            if not esta or valor is None:
                r.error(f"Falta el dato obligatorio ({CAMPOS[interno][3]}).", fila=i, campo=nombre)
                ok = False
                continue
            if interno == "fecha":
                f = _fecha(valor)
                if f is None:
                    r.error(f"Fecha no válida: {valor!r}. Usa el formato AAAA-MM-DD.", fila=i, campo=nombre)
                    ok = False
                fila["fecha"] = f
            elif interno == "monto":
                if isinstance(valor, bool) or not isinstance(valor, (int, float)) or not math.isfinite(valor):
                    r.error(f"El monto debe ser un número y llegó {valor!r}.", fila=i, campo=nombre)
                    ok = False
                fila["monto"] = valor
            elif interno == "banco":
                t = _texto(valor)
                if t is None or not t.strip():
                    r.error("El banco no puede ir vacío.", fila=i, campo=nombre)
                    ok = False
                fila["banco"] = t
            elif interno == "concepto":
                t = _texto(valor)
                if t is None:
                    r.error(f"El concepto debe ser texto y llegó {valor!r}.", fila=i, campo=nombre)
                    ok = False
                elif not t.strip():
                    sin_concepto += 1
                fila["concepto"] = t
        for interno in (k for k in CAMPOS if k not in OBLIGATORIOS):
            esta, valor, nombre = _buscar(mov, interno)
            if not esta or valor is None:
                continue
            t = _texto(valor)
            if t is None:
                r.error(f"Debe ser texto y llegó {valor!r}.", fila=i, campo=nombre)
                ok = False
                continue
            presentes[interno] += 1
            fila[interno] = t.strip().upper() if interno == "moneda" else t
        if ok:
            filas.append(fila)
    if not r.valido:
        return r

    # ---- conjunto de datos
    crudo = pd.DataFrame(filas)
    n_recibidos = len(crudo)
    for interno in CAMPOS:
        if interno not in crudo.columns:
            crudo[interno] = None
    crudo["moneda"] = crudo["moneda"].fillna(moneda_archivo)
    otras = crudo.loc[crudo["moneda"] != cfg.moneda, "moneda"].value_counts()
    if len(otras):
        r.avisos.append(f"Se excluyen {int(otras.sum())} movimientos en otra moneda ({', '.join(otras.index)}); "
                        f"el pronóstico es en {cfg.moneda}.")
        crudo = crudo[crudo["moneda"] == cfg.moneda]
    if crudo.empty:
        r.error(f"No hay movimientos en {cfg.moneda}.", campo="moneda")
        return r

    crudo["fecha"] = pd.to_datetime(crudo["fecha"]).astype("datetime64[ns]")
    crudo["monto"] = crudo["monto"].astype(float)
    crudo["banco"] = crudo["banco"].astype(str).str.strip().str.upper()
    crudo = crudo.sort_values("fecha", kind="stable").reset_index(drop=True)

    habiles = crudo["fecha"].map(pd.offsets.BDay().rollforward)
    rango = pd.bdate_range(habiles.min(), habiles.max())
    minimo = max(10, cfg.min_contexto + cfg.horizonte)
    if len(rango) < minimo:
        r.error(f"La historia cubre {len(rango)} días hábiles ({habiles.min():%Y-%m-%d} a {habiles.max():%Y-%m-%d}); "
                f"se necesitan al menos {minimo} para pronosticar {cfg.horizonte} días y medir el error.")
    elif len(rango) < DIAS_RECOMENDADOS:
        r.avisos.append(f"La historia cubre {len(rango)} días hábiles: alcanza para pronosticar, pero las métricas "
                        f"de confianza no son concluyentes con menos de {DIAS_RECOMENDADOS}.")

    tipos = crudo["tipo_tx"].fillna("")
    clas = [clasificar(c, t, m, cfg) for c, t, m in zip(crudo["concepto"], tipos, crudo["monto"])]
    operativos = sum(1 for _, tes in clas if not tes)
    if operativos == 0:
        r.error("Todos los movimientos se clasifican como tesorería, así que no hay flujo operativo que pronosticar. "
                "Revisa los conceptos o las reglas de tesorería.", campo="concepto")
    if not r.valido:
        return r

    con_dato = pd.Series(1, index=pd.DatetimeIndex(habiles.unique())).reindex(rango, fill_value=0)
    corridas = (con_dato != con_dato.shift()).cumsum()
    huecos = con_dato[con_dato == 0].groupby(corridas[con_dato == 0]).size()
    if len(huecos) and huecos.max() >= HUECO_AVISO:
        grupo = huecos.idxmax()
        dias_hueco = con_dato[(corridas == grupo)].index
        r.avisos.append(f"No hay movimientos del {dias_hueco[0]:%Y-%m-%d} al {dias_hueco[-1]:%Y-%m-%d} "
                        f"({len(dias_hueco)} días hábiles); esos días cuentan como flujo cero. Revisa si falta un extracto.")
    llave = ["banco", "linea_extracto", "concepto", "monto", "fecha"]
    if crudo["linea_extracto"].isna().all():
        llave.remove("linea_extracto")
        r.avisos.append("Sin 'partida': los duplicados se detectan solo por banco, concepto, monto y fecha.")
    duplicados = int(crudo.duplicated(subset=llave).sum())
    if duplicados:
        r.avisos.append(f"{duplicados} movimientos repetidos (mismo extracto cargado más de una vez); se cuentan una sola vez.")
    if presentes["tipo_tx"] < n_recibidos:
        r.avisos.append(f"{n_recibidos - presentes['tipo_tx']} movimientos sin 'tipo_transaccion': se clasifican solo por concepto.")
    if sin_concepto:
        r.avisos.append(f"{sin_concepto} movimientos con concepto vacío: se clasifican como cobranza o egreso según el signo.")

    r.resumen = {
        "movimientos_recibidos": len(movimientos), "movimientos_validos": len(crudo),
        "desde": f"{crudo['fecha'].min():%Y-%m-%d}", "hasta": f"{crudo['fecha'].max():%Y-%m-%d}",
        "dias_habiles": len(rango), "bancos": {b: int(n) for b, n in crudo["banco"].value_counts().sort_index().items()},
        "movimientos_operativos": operativos, "movimientos_tesoreria": len(clas) - operativos,
        "duplicados": duplicados, "moneda": cfg.moneda,
        "campos_opcionales": {CAMPOS[k][0]: int(presentes[k]) for k in CAMPOS if k not in OBLIGATORIOS},
    }
    r.crudo = crudo[[c for c in CAMPOS]]
    return r


def validar_archivo(ruta, cfg):
    contenido, error = leer_json(ruta)
    if error is not None:
        return error
    return validar_movimientos(contenido, cfg)
