"""Parámetros del experimento. Son los mismos de la celda 4.1 del cuaderno."""
from dataclasses import dataclass, field
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
ARCHIVO_DATOS = RAIZ / "data" / "DEPOSITOS_CARGADOS_FEBAN_sep2025_sep2026_anon.xlsx"
ARCHIVO_JSON = RAIZ / "data" / "movimientos_feban_sep2025_sep2026_anon.json"   # entrada de la API

# Nombre interno -> columna del export FEBAN
COLUMNAS = {
    "fecha": "Value date",
    "monto": "Amount",
    "banco": "House Bk",
    "linea_extracto": "MR no",          # número de partida dentro del extracto
    "concepto": "Payment Notes",
    "tipo_tx": "External transaction",
    "moneda": "AC",
    "clave_extracto": "Shrt key",       # una clave por cada carga del extracto
    "regla_contable": "Post. rule",
}

# (categoría, regex sobre el concepto, tipos de transacción, ¿es tesorería?). Gana la primera que coincide.
# Tesorería = movimientos entre cuentas propias; no son flujo del negocio.
REGLAS_CATEGORIA = [
    ("Tesorería: barrido (cash pooling)",    r"SWEEP",                               [],       True),
    ("Tesorería: giro / traspaso de fondos", r"GIROFONDO|SPEI ENVIADO HSB",          [],       True),
    ("Comisiones e IVA bancario",            r"COMISION|PENA|I\.V\.A|TRANSFENVSPEI", ["NCOM"], False),
    ("Compensación SPEI",                    r"COMPENSACION",                        [],       False),
    ("Cargo por cartera",                    r"CARTERA",                             [],       False),
    ("Depósitos (efectivo / terceros)",      r"DEPOSITO",                            ["NCOL"], False),
    ("Pago cuenta de tercero",               r"PAGO CUENTA DE T",                    [],       False),
]

REPOS_HF = {
    "Chronos-2": "amazon/chronos-2",
    "Chronos-Bolt Small": "amazon/chronos-bolt-small",
}


@dataclass
class Config:
    # Archivo de entrada
    archivo_excel: Path = ARCHIVO_DATOS
    hoja_excel: object = 0
    moneda: str = "MXN"
    columnas: dict = field(default_factory=lambda: dict(COLUMNAS))

    # Limpieza y clasificación
    deduplicar: bool = True
    llave_movimiento: tuple = ("banco", "linea_extracto", "concepto", "monto", "fecha")
    reglas_categoria: list = field(default_factory=lambda: list(REGLAS_CATEGORIA))
    categoria_ingreso_default: str = "Cobranza SPEI / TEF"
    categoria_egreso_default: str = "Otros egresos"
    series_por_banco: bool = True

    # Pronóstico y evaluación
    frecuencia: str = "B"                 # días hábiles
    horizonte: int = 5                    # días hábiles por pronóstico en el backtest
    horizonte_final: int = 5              # días hábiles del pronóstico hacia adelante
    min_contexto: int = 3                 # días de historia antes del primer origen
    max_origenes: int = 280               # orígenes walk-forward (los más recientes)
    cuantiles: tuple = (0.1, 0.5, 0.9)    # P10-P90 = intervalo del 80 %
    estacionalidad: int = 5               # ciclo semanal en días hábiles
    usar_exogenas_sarimax: bool = True    # calendario como X de SARIMAX (solo con >= 30 días)
    sarimax_reestimar_cada: int = 5       # en el backtest re-estima cada N orígenes y filtra entre tanto

    # Covariable temporada (conocida a futuro)
    meses_ingreso_alto: tuple = (1, 2, 3)
    meses_gasto_alto: tuple = (9, 10, 11, 12)
    min_dias_temporada: int = 10
    fecha_corte: str = "2026-09-01"

    # Latencia
    n_repeticiones_latencia: int = 20
    tamanos_lote: tuple = (1, 16, 64)
    limite_escala_s: float = 60

    # Modelos de Hugging Face
    repos_hf: dict = field(default_factory=lambda: dict(REPOS_HF))
    semilla: int = 42

    @property
    def cols_q(self):
        return {q: f"P{int(round(q * 100))}" for q in self.cuantiles}

    @property
    def q_lo(self):
        return self.cols_q[min(self.cuantiles)]

    @property
    def q_hi(self):
        return self.cols_q[max(self.cuantiles)]

    @property
    def etq_cobertura(self):
        return f"Cobertura {self.q_lo}–{self.q_hi} (%)"
