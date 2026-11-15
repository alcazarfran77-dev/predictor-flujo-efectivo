"""Configuración de la supervisión en tiempo real: SLO, presupuestos de error, prioridades, alertas y runbooks.

Todo lo que decide cuándo se avisa, con qué urgencia y qué se hace está aquí, como datos. `observabilidad.py` solo
calcula los indicadores y aplica estas reglas. Para cambiar un objetivo, un umbral o una prioridad basta con editar
este archivo; las pruebas de `tests/test_observabilidad.py` verifican que el catálogo siga completo.

Ver la configuración en consola:
    python -m api.config_alertas
"""

# ---------------------------------------------------------------- objetivos de nivel de servicio (SLO)
OBJ_DISPONIBILIDAD = 0.995       # fracción de peticiones a /api/v1 sin error 5xx
OBJ_LATENCIA = 0.95              # fracción de peticiones que responden en menos de UMBRAL_LATENCIA
UMBRAL_LATENCIA = 1.0            # segundos
MAX_ATRASO_DATOS = 1             # días hábiles de atraso permitidos en los movimientos
OBJ_CALIDAD = 0.90               # fracción de días con el modelo dentro de su límite (calidad y calibración)
DIAS_CALIDAD = 60                # días hábiles que cubre el SLO del modelo
VENTANA_SLO_DIAS = 30            # ventana de los SLO de la API

# Límites del modelo que usan los SLO de calidad y calibración (se evalúan sobre la serie principal).
LIMITE_RMAE = 0.90               # rMAE móvil de 20 días frente al pronóstico ingenuo
DIAS_SEGUIDOS_RMAE = 10          # días seguidos sobre el límite para declarar el modelo degradado
COBERTURA_MIN, COBERTURA_MAX = 65.0, 95.0     # rango aceptado de la cobertura móvil de 40 días, en porcentaje

SLOS = {
    "disponibilidad": {"nombre": "Disponibilidad de la API", "objetivo": OBJ_DISPONIBILIDAD, "ventana": f"{VENTANA_SLO_DIAS} días",
                       "indicador": "Peticiones de la API que no terminan en error 5xx"},
    "latencia": {"nombre": "Latencia de la API", "objetivo": OBJ_LATENCIA, "ventana": f"{VENTANA_SLO_DIAS} días",
                 "indicador": f"Peticiones de la API que responden en menos de {UMBRAL_LATENCIA:g} s"},
    "frescura": {"nombre": "Frescura de los datos", "objetivo": 0.95, "ventana": f"{VENTANA_SLO_DIAS} días",
                 "indicador": f"Días hábiles en que los movimientos llegan hasta el día hábil anterior (atraso máximo {MAX_ATRASO_DATOS})"},
    "calidad": {"nombre": "Calidad del pronóstico", "objetivo": OBJ_CALIDAD, "ventana": f"{DIAS_CALIDAD} días hábiles",
                "indicador": f"Días con rMAE móvil de 20 días en {LIMITE_RMAE:.2f} o menos"},
    "calibracion": {"nombre": "Calibración del intervalo", "objetivo": OBJ_CALIDAD, "ventana": f"{DIAS_CALIDAD} días hábiles",
                    "indicador": f"Días con cobertura móvil de 40 días entre {COBERTURA_MIN:.0f} y {COBERTURA_MAX:.0f} %"},
}

# Rutas que no cuentan para los SLO de la API: son sondas y monitoreo, no tráfico de usuarios.
RUTAS_SIN_SLI = ("/metrics", "/api/v1/observabilidad", "/api/v1/salud")
ESTADOS_SIN_ERROR = (503,)       # 503 con Retry-After (backtest en cálculo) no cuenta como error del servicio

# Política de presupuesto de error: fracción consumida a partir de la cual cambia el estado del SLO.
PRESUPUESTO_EN_RIESGO, PRESUPUESTO_AGOTADO = 0.75, 1.0

# ---------------------------------------------------------------- tasas de consumo (alertas multiventana)
# Una tasa de 1 agota el presupuesto justo al final de la ventana del SLO. Cada regla exige superar el límite en una
# ventana larga (el problema es real) y en una corta (sigue ocurriendo).
VENTANAS = {"5m": 300, "30m": 1800, "1h": 3600, "6h": 21600}       # segundos
CONSUMO_RAPIDO = {"limite": 14.4, "corta": "5m", "larga": "1h"}    # gasta 2 % del presupuesto mensual en 1 hora
CONSUMO_LENTO = {"limite": 6.0, "corta": "30m", "larga": "6h"}     # gasta 5 % del presupuesto mensual en 6 horas
MIN_PETICIONES = 20              # mínimo en la ventana corta para que una tasa de consumo cuente

# ---------------------------------------------------------------- prioridades por impacto operativo
P1, P2, P3, P4 = "P1", "P2", "P3", "P4"
PRIORIDADES = {
    P1: {"nombre": "Crítica", "atencion": "Inmediata, a cualquier hora", "canal": "Llamada o página a guardia"},
    P2: {"nombre": "Alta", "atencion": "En menos de 4 horas hábiles", "canal": "Mensaje al canal de tesorería y TI"},
    P3: {"nombre": "Media", "atencion": "Siguiente día hábil", "canal": "Ticket"},
    P4: {"nombre": "Informativa", "atencion": "Revisión semanal", "canal": "Tablero"},
}
PRIORIDADES_QUE_NOTIFICAN = (P1, P2, P3)

RUNBOOKS = {
    "RB-01": "API con errores o caída",
    "RB-02": "Latencia alta",
    "RB-03": "Datos atrasados o carga rechazada",
    "RB-04": "Modelo degradado",
    "RB-05": "Intervalo descalibrado",
    "RB-06": "Data drift",
    "RB-07": "Alerta de déficit",
    "RB-08": "Presupuesto de error agotado",
}

# ---------------------------------------------------------------- catálogo de alertas
# Cada alerta tiene nombre, prioridad, runbook, capa y la condición que la activa.
# `prioridad_con_drift` sube la prioridad cuando además hay data drift significativo.
ALERTAS = {
    "api_consumo_rapido": {
        "nombre": "Errores de la API: consumo rápido del presupuesto", "prioridad": P1, "runbook": "RB-01", "capa": "servicio",
        "condicion": "Tasa de consumo de disponibilidad de 14.4 o más en las ventanas de 1 h y 5 min",
        "impacto": "Tesorería no puede consultar el pronóstico ni la alerta de déficit."},
    "api_consumo_lento": {
        "nombre": "Errores de la API: consumo sostenido del presupuesto", "prioridad": P2, "runbook": "RB-01", "capa": "servicio",
        "condicion": "Tasa de consumo de disponibilidad de 6 o más en las ventanas de 6 h y 30 min",
        "impacto": "Parte de las consultas falla; si sigue así, el presupuesto del mes se agota en días."},
    "latencia_consumo_rapido": {
        "nombre": "Latencia: consumo rápido del presupuesto", "prioridad": P2, "runbook": "RB-02", "capa": "servicio",
        "condicion": "Tasa de consumo de latencia de 14.4 o más en las ventanas de 1 h y 5 min",
        "impacto": "El dashboard carga lento y los sistemas que consumen la API pueden agotar su tiempo de espera."},
    "latencia_consumo_lento": {
        "nombre": "Latencia: consumo sostenido del presupuesto", "prioridad": P3, "runbook": "RB-02", "capa": "servicio",
        "condicion": "Tasa de consumo de latencia de 6 o más en las ventanas de 6 h y 30 min",
        "impacto": "Consultas lentas de forma sostenida."},
    "presupuesto_agotado_disponibilidad": {
        "nombre": "Presupuesto de error agotado: disponibilidad de la API", "prioridad": P2, "runbook": "RB-08", "capa": "servicio",
        "condicion": "El SLO de disponibilidad consumió 100 % de su presupuesto",
        "impacto": "Se congelan los cambios que no sean correcciones hasta recuperar el presupuesto."},
    "presupuesto_agotado_latencia": {
        "nombre": "Presupuesto de error agotado: latencia de la API", "prioridad": P2, "runbook": "RB-08", "capa": "servicio",
        "condicion": "El SLO de latencia consumió 100 % de su presupuesto",
        "impacto": "Se congelan los cambios que no sean correcciones hasta recuperar el presupuesto."},
    "datos_atrasados": {
        "nombre": "Datos atrasados", "prioridad": P2, "runbook": "RB-03", "capa": "datos",
        "condicion": f"Más de {MAX_ATRASO_DATOS} día hábil de atraso en los movimientos",
        "impacto": "El pronóstico no incluye los movimientos más recientes y puede ocultar un déficit."},
    "carga_rechazada": {
        "nombre": "Carga de datos rechazada", "prioridad": P2, "runbook": "RB-03", "capa": "datos",
        "condicion": "Un archivo nuevo no pasó la validación",
        "impacto": "Los datos nuevos no entraron; el pronóstico se queda con la historia previa."},
    "datos_duplicados": {
        "nombre": "Movimientos repetidos en la carga", "prioridad": P3, "runbook": "RB-03", "capa": "datos",
        "condicion": "La carga traía movimientos duplicados",
        "impacto": "Si la deduplicación fallara, el flujo se inflaría."},
    "deficit_alta": {
        "nombre": "Alerta de déficit alta", "prioridad": P2, "runbook": "RB-07", "capa": "negocio",
        "condicion": "P50 menor a cero en algún día del horizonte", "umbral": 0.5,
        "impacto": "Riesgo de salida neta de efectivo en los próximos días hábiles."},
    "deficit_media": {
        "nombre": "Alerta de déficit media", "prioridad": P3, "runbook": "RB-07", "capa": "negocio",
        "condicion": "Probabilidad de déficit de 25 % o más en algún día del horizonte", "umbral": 0.25,
        "impacto": "Riesgo de salida neta de efectivo en los próximos días hábiles."},
    "modelo_degradado": {
        "nombre": "Modelo degradado", "prioridad": P3, "prioridad_con_drift": P2, "runbook": "RB-04", "capa": "modelo",
        "condicion": f"rMAE móvil sobre {LIMITE_RMAE:.2f} durante {DIAS_SEGUIDOS_RMAE} días seguidos",
        "impacto": "El pronóstico ya casi no mejora a repetir el último dato."},
    "cobertura_fuera_de_rango": {
        "nombre": "Intervalo descalibrado", "prioridad": P3, "runbook": "RB-05", "capa": "modelo",
        "condicion": f"Cobertura móvil fuera de {COBERTURA_MIN:.0f} a {COBERTURA_MAX:.0f} %",
        "impacto": "El colchón sugerido queda corto o sobrado."},
    "presupuesto_agotado_calidad": {
        "nombre": "Presupuesto de error agotado: calidad del pronóstico", "prioridad": P3, "runbook": "RB-08", "capa": "modelo",
        "condicion": "El SLO de calidad consumió 100 % de su presupuesto",
        "impacto": "El modelo pasó demasiados días fuera de su límite; toca revisarlo antes de cualquier otro cambio."},
    "presupuesto_agotado_calibracion": {
        "nombre": "Presupuesto de error agotado: calibración del intervalo", "prioridad": P3, "runbook": "RB-08", "capa": "modelo",
        "condicion": "El SLO de calibración consumió 100 % de su presupuesto",
        "impacto": "El modelo pasó demasiados días fuera de su límite; toca revisarlo antes de cualquier otro cambio."},
    "drift_significativo": {
        "nombre": "Data drift significativo", "prioridad": P3, "runbook": "RB-06", "capa": "datos",
        "condicion": "PSI de 0.25 o más y prueba KS con p menor a 0.05 en alguna serie; o cambio en la composición",
        "impacto": "El modelo pronostica con una distribución que no ha visto; su error puede subir."},
    "drift_moderado": {
        "nombre": "Data drift moderado", "prioridad": P4, "runbook": "RB-06", "capa": "datos",
        "condicion": "Una sola señal de cambio en alguna serie o en la composición",
        "impacto": "Sin efecto medido en el error; se vigila."},
    "backtest_error": {
        "nombre": "Backtest con error", "prioridad": P3, "runbook": "RB-04", "capa": "modelo",
        "condicion": "No se pudo calcular el backtest de un modelo",
        "impacto": "Esos modelos no tienen métricas de confianza."},
    "backtest_calculando": {
        "nombre": "Backtest en cálculo", "prioridad": P4, "runbook": "RB-04", "capa": "modelo",
        "condicion": "Hay modelos con el backtest pendiente o en cálculo",
        "impacto": "Las métricas de confianza de esos modelos aún no están disponibles."},
    "modelo_conmutado": {
        "nombre": "Modelo de respaldo en uso", "prioridad": P4, "runbook": "RB-04", "capa": "modelo",
        "condicion": "La API recomienda el modelo de respaldo en lugar del original",
        "impacto": "El pronóstico sigue disponible con el modelo de respaldo."},
    "retador_mejor": {
        "nombre": "Un modelo retador mejora al recomendado", "prioridad": P4, "runbook": "RB-04", "capa": "modelo",
        "condicion": "Un modelo evaluado mejora al recomendado de forma significativa",
        "impacto": "Oportunidad de mejora; no afecta la operación."},
}

# ---------------------------------------------------------------- inhibición
# Una causa raíz activa silencia sus consecuencias: siguen visibles en el tablero, pero no notifican ni disparan
# respuestas automáticas.
INHIBICIONES = {
    "api_consumo_rapido": ("latencia_consumo_rapido", "latencia_consumo_lento"),
    "datos_atrasados": ("drift_significativo", "drift_moderado", "modelo_degradado"),
    "backtest_calculando": ("modelo_degradado", "cobertura_fuera_de_rango", "presupuesto_agotado_calidad",
                            "presupuesto_agotado_calibracion"),
}

# ---------------------------------------------------------------- respuesta automática
MODOS_RESPUESTA = ("off", "recomendar", "aplicar")     # variable FLUJO_RESPUESTA_AUTO
HISTERESIS_RMAE = 0.85           # el modelo original vuelve cuando su rMAE móvil baja de aquí
COBERTURA_OBJETIVO = 0.80        # cobertura a la que apunta el factor de recalibración del intervalo
RESPUESTAS = {
    "conservar_datos_anteriores": {"ante": "carga_rechazada", "ejecucion": "siempre",
                                   "accion": "Rechazar el archivo y seguir con los datos anteriores"},
    "recalcular_backtest": {"ante": "datos nuevos válidos", "ejecucion": "siempre",
                            "accion": "Recalcular el backtest en segundo plano; métricas con 503 y Retry-After mientras termina"},
    "conmutar_modelo": {"ante": "modelo_degradado", "ejecucion": "según el modo",
                        "accion": "Recomendar el modelo servido con menor rMAE móvil que cumpla el límite"},
    "restaurar_modelo": {"ante": "modelo_conmutado", "ejecucion": "según el modo",
                         "accion": f"Volver al modelo original cuando su rMAE móvil baje a {HISTERESIS_RMAE:.2f}"},
    "recalibrar_intervalo": {"ante": "cobertura_fuera_de_rango", "ejecucion": "solo propone",
                             "accion": "Calcular el factor que devuelve la cobertura a 80 %"},
}


def validar():
    """Revisa que el catálogo sea coherente. Lanza ValueError con el primer problema que encuentre."""
    for clave, a in ALERTAS.items():
        faltan = {"nombre", "prioridad", "runbook", "capa", "condicion", "impacto"} - set(a)
        if faltan:
            raise ValueError(f"La alerta {clave} no tiene {sorted(faltan)}.")
        if a["prioridad"] not in PRIORIDADES or a.get("prioridad_con_drift", P1) not in PRIORIDADES:
            raise ValueError(f"La alerta {clave} tiene una prioridad desconocida.")
        if a["runbook"] not in RUNBOOKS:
            raise ValueError(f"La alerta {clave} apunta a un runbook que no existe: {a['runbook']}.")
    for causa, efectos in INHIBICIONES.items():
        for clave in (causa, *efectos):
            if clave not in ALERTAS:
                raise ValueError(f"La inhibición usa una alerta que no existe: {clave}.")
    for regla in (CONSUMO_RAPIDO, CONSUMO_LENTO):
        if regla["corta"] not in VENTANAS or regla["larga"] not in VENTANAS or VENTANAS[regla["corta"]] >= VENTANAS[regla["larga"]]:
            raise ValueError("Las ventanas de una regla de consumo no son válidas.")
    for clave, s in SLOS.items():
        if not 0 < s["objetivo"] < 1:
            raise ValueError(f"El objetivo del SLO {clave} debe estar entre 0 y 1.")
    return True


def como_tabla():
    """Catálogo de alertas como lista de filas, ordenado por prioridad."""
    return sorted(({"alerta": k, **{c: v[c] for c in ("nombre", "prioridad", "runbook", "capa", "condicion")}} for k, v in ALERTAS.items()),
                  key=lambda f: (f["prioridad"], f["capa"], f["alerta"]))


if __name__ == "__main__":
    validar()
    print("SLO")
    for clave, s in SLOS.items():
        print(f"  {clave:<15} {s['objetivo'] * 100:>5.1f} % en {s['ventana']:<16} {s['indicador']}")
    print(f"\nTasas de consumo: rápida {CONSUMO_RAPIDO['limite']} ({CONSUMO_RAPIDO['larga']} y {CONSUMO_RAPIDO['corta']}), "
          f"sostenida {CONSUMO_LENTO['limite']} ({CONSUMO_LENTO['larga']} y {CONSUMO_LENTO['corta']}); mínimo {MIN_PETICIONES} peticiones")
    print(f"\nAlertas ({len(ALERTAS)})")
    for f in como_tabla():
        print(f"  {f['prioridad']}  {f['runbook']}  {f['alerta']:<36} {f['condicion']}")
    print("\nInhibiciones")
    for causa, efectos in INHIBICIONES.items():
        print(f"  {causa} silencia: {', '.join(efectos)}")
