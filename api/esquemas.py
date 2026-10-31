"""Contrato de la API: esquemas de entrada y salida (Pydantic). Los montos van en pesos (MXN)."""
from typing import Literal, Optional

from pydantic import BaseModel, Field

NivelAlerta = Literal["ninguna", "vigilancia", "media", "alta"]
NivelConfianza = Literal["alta", "media", "baja"]


class InfoDatos(BaseModel):
    archivo: str
    formato: str = "json"
    huella: str = Field(description="Primeros 16 caracteres del SHA-256 de las series diarias")
    desde: str
    hasta: str
    dias_habiles: int
    movimientos: int
    duplicados: int
    avisos: list[str] = Field(default_factory=list, description="Avisos de la validación del archivo")


class EstadoModelo(BaseModel):
    nombre: str
    familia: str
    backtest: str = Field(description="listo, calculando, pendiente o error")


class Salud(BaseModel):
    estado: Literal["ok", "iniciando"]
    version: str
    datos: InfoDatos
    modelos: list[EstadoModelo]
    modelo_recomendado: str
    serie_principal: str
    horizonte_maximo: int
    umbral_deficit: float
    fechas_pronostico: list[str]
    archivo_salida: Optional[str] = Field(description="Último reporte JSON escrito en el servidor")


class Serie(BaseModel):
    id: str
    descripcion: str
    principal: bool
    ultima_fecha: str
    ultimo_valor: float


class Punto(BaseModel):
    fecha: str = Field(examples=["2026-09-30"])
    valor: float = Field(examples=[2500000.0])


class Historia(BaseModel):
    serie: str
    puntos: list[Punto]


class AlertaDia(BaseModel):
    nivel: NivelAlerta
    activa: bool


class ConfianzaDia(BaseModel):
    mae: float = Field(description="MAE del backtest para este horizonte y serie, en MXN")
    rmae: Optional[float] = Field(description="MAE dividido entre el del pronóstico ingenuo")
    cobertura: float = Field(description="% de valores reales dentro de P10–P90 en el backtest")
    sesgo: float
    n: int


class DiaPronostico(BaseModel):
    fecha: str
    h: int = Field(description="Días hábiles después del último dato")
    p10: float
    p50: float
    p90: float
    prob_deficit: float = Field(ge=0, le=1)
    alerta: AlertaDia
    confianza: Optional[ConfianzaDia]


class DesempenoRegla(BaseModel):
    regla: str
    dias_alertados_pct: float
    precision: Optional[float]
    recall: Optional[float]
    f1: Optional[float]
    eventos_reales: int
    pronosticos: int
    tasa_base_pct: float


class AlertaDeficit(BaseModel):
    activa: bool
    nivel: NivelAlerta
    regla: str
    dias_en_alerta: int
    primera_fecha: Optional[str]
    probabilidad_maxima: float
    peor_p10: float
    colchon_sugerido: float = Field(description="Efectivo que cubre el peor P10 del horizonte, en MXN")
    desempeno: list[DesempenoRegla] = Field(description="Cómo funcionó cada nivel de alerta en el backtest")


class Acumulado(BaseModel):
    p10: float
    p50: float
    p90: float
    prob_deficit: float


class ResumenConfianza(BaseModel):
    disponible: bool
    estado_backtest: str
    nivel: Optional[NivelConfianza] = None
    umbrales_cumplidos: Optional[int] = None
    umbrales_total: Optional[int] = None
    alarmas_monitoreo: Optional[int] = None
    mae_serie: Optional[float] = None
    rmae_serie: Optional[float] = None
    cobertura_serie: Optional[float] = None


class Pronostico(BaseModel):
    serie: str
    descripcion: str
    modelo: str
    familia: str
    origen: str = Field(description="Último día con datos")
    generado: str
    latencia_ms: float = Field(description="Tiempo que tomó calcular el pronóstico de todas las series")
    en_cache: bool
    unidades: str
    cuantiles: list[float]
    umbral_deficit: float
    dias: list[DiaPronostico]
    acumulado: Acumulado
    alerta_deficit: AlertaDeficit
    confianza: ResumenConfianza


class AlertaSerie(BaseModel):
    serie: str
    descripcion: str
    principal: bool
    activa: bool
    nivel: NivelAlerta
    dias_en_alerta: int
    primera_fecha: Optional[str]
    probabilidad_maxima: float
    peor_p10: float
    colchon_sugerido: float
    p50_acumulado: float


class MetricasBasicas(BaseModel):
    mae: float
    rmae: Optional[float]
    sesgo: float
    wql: Optional[float]
    cobertura: float
    f1_direccion: Optional[float]
    n: int


class MetricasHorizonte(MetricasBasicas):
    h: int


class Umbral(BaseModel):
    clave: str
    nombre: str
    valor: Optional[float]
    condicion: str
    unidad: str
    cumple: Optional[bool]


class PuntoMonitoreo(BaseModel):
    fecha: str
    valor: float


class MonitorRmae(BaseModel):
    valor: Optional[float]
    ventana_dias: int
    limite: float
    dias_seguidos: int
    alerta: bool
    accion: str
    serie: list[PuntoMonitoreo]


class MonitorCobertura(BaseModel):
    valor: Optional[float]
    ventana_dias: int
    rango: list[float]
    alerta: bool
    accion: str
    serie: list[PuntoMonitoreo]


class MonitorDuplicados(BaseModel):
    valor: int
    alerta: bool
    accion: str


class Monitoreo(BaseModel):
    rmae_movil: MonitorRmae
    cobertura_movil: MonitorCobertura
    duplicados: MonitorDuplicados


class Metricas(BaseModel):
    modelo: str
    serie: Optional[str]
    disponible: bool
    origenes: int
    periodo_backtest: list[str]
    todas_las_series: MetricasBasicas
    serie_metricas: MetricasBasicas
    desde_corte: Optional[MetricasBasicas]
    fecha_corte: str
    por_horizonte: list[MetricasHorizonte]
    umbrales: list[Umbral] = Field(description="Umbrales de aceptación del reporte, sobre todas las series")
    fecha_cambio_temporada: Optional[str]
    monitoreo: Monitoreo = Field(description="Disparadores de revisión, sobre la serie consultada")
    reglas_deficit: list[DesempenoRegla]
    nivel_confianza: NivelConfianza
    fallas: int
    alarmas: int


class ComparacionModelo(BaseModel):
    modelo: str
    familia: str
    recomendado: bool
    estado_backtest: str
    latencia_ms: Optional[float] = None
    mae: Optional[float] = None
    rmae: Optional[float] = None
    wql: Optional[float] = None
    cobertura: Optional[float] = None
    f1_direccion: Optional[float] = None
    mae_desde_corte: Optional[float] = None
    umbrales_cumplidos: Optional[int] = None
    umbrales_total: Optional[int] = None
    nivel_confianza: Optional[NivelConfianza] = None
    mae_vs_recomendado: Optional[float] = None
    reemplaza_al_recomendado: Optional[bool] = None


class PeticionPronostico(BaseModel):
    """Serie diaria enviada por otro sistema. Los fines de semana pasan al siguiente día hábil."""
    serie_id: str = Field(default="externa", max_length=64)
    modelo: str = "ETS"
    horizonte: int = Field(default=5, ge=1, le=20)
    historia: list[Punto] = Field(min_length=10, max_length=5000)


# ------------------------------------------------------------------ validación de la entrada
class ErrorValidacion(BaseModel):
    fila: Optional[int] = Field(description="Número de movimiento (desde 1); vacío si el problema es del archivo")
    campo: Optional[str]
    mensaje: str


class Validacion(BaseModel):
    valido: bool
    total_errores: int
    errores: list[ErrorValidacion] = Field(description="Hasta 50 errores con su movimiento y campo")
    avisos: list[str]
    resumen: dict = Field(description="Movimientos, periodo, días hábiles, bancos y campos opcionales presentes")


# ------------------------------------------------------------------ reporte JSON (salida en archivo)
class MonitorResumen(BaseModel):
    valor: Optional[float]
    alerta: bool
    accion: str
    ventana_dias: Optional[int] = None
    limite: Optional[float] = None
    dias_seguidos: Optional[int] = None
    rango: Optional[list[float]] = None


class ConfianzaModelo(BaseModel):
    disponible: bool
    estado_backtest: str
    nivel: Optional[NivelConfianza] = None
    origenes: Optional[int] = None
    periodo_backtest: Optional[list[str]] = None
    metricas: Optional[MetricasBasicas] = None
    desde_corte: Optional[MetricasBasicas] = None
    fecha_corte: Optional[str] = None
    umbrales: Optional[list[Umbral]] = None
    fecha_cambio_temporada: Optional[str] = None
    monitoreo: Optional[dict[str, MonitorResumen]] = None


class SerieReporte(BaseModel):
    serie: str
    descripcion: str
    principal: bool
    origen: str
    dias: list[DiaPronostico]
    acumulado: Acumulado
    alerta_deficit: AlertaDeficit
    confianza: ResumenConfianza


class Reporte(BaseModel):
    formato: Literal["flujo-efectivo/pronostico"]
    version: str
    version_api: str
    generado: str
    pronostico_calculado: str
    datos: InfoDatos
    modelo: str
    familia: str
    recomendado: bool
    latencia_ms: float
    horizonte: int
    cuantiles: list[float]
    unidades: str
    umbral_deficit: float
    niveles_alerta: dict[str, str]
    confianza: ConfianzaModelo
    series: list[SerieReporte]


class ArchivoGenerado(BaseModel):
    archivo: str = Field(description="Ruta del archivo en el servidor, relativa al repositorio")
    nombre: str
    bytes: int
    modelo: str
    generado: str
    series: int
