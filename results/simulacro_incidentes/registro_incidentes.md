# Registro de incidentes simulados

Generado el 2026-11-15T01:14:37+00:00 con `herramientas/simulacro_incidentes.py`. Ventanas de las alertas aceleradas 10 veces.

## INC-01. Aumento de latencia de la API

- Inicio: 2026-11-15T01:02:36+00:00  |  Fin: 2026-11-15T01:07:15+00:00
- Causa simulada: Se inyectan 1500 ms de espera a cada petición de la API (POST /api/v1/observabilidad/simulacro). Imita una instancia saturada o una dependencia lenta.
- Runbooks: RB-02, RB-08
- Tiempos (segundos): {"deteccion_s": 89, "mitigacion_s": 93, "recuperacion_s": 33, "alertas_de_consumo_resueltas_s": 134, "duracion_total_s": 279, "primera_alerta_s": 6}

### Cronología

- 01:02:36 (+0 s) Línea base: 45 s de tráfico normal (6 peticiones por segundo).
- 01:03:24 (+48 s) Inicio de la falla: 1500 ms de latencia inyectada (respuesta 200: {'error': 0.0, 'latencia_ms': 1500.0}).
- 01:03:30 (+54 s) Alerta activada: P2 Presupuesto de error agotado: latencia de la API (presupuesto_agotado_latencia), runbook RB-08. Se consumió 271 % del presupuesto de la ventana.
- 01:03:39 (+63 s) Alerta activada: P3 Latencia: consumo sostenido del presupuesto (latencia_consumo_lento), runbook RB-02. El presupuesto de latencia se consume 6.1 veces más rápido de lo permitido.
- 01:04:53 (+137 s) Alerta activada: P2 Latencia: consumo rápido del presupuesto (latencia_consumo_rapido), runbook RB-02. 100.0 % de las peticiones tarda más de 1 s (p95 de 1534 ms).
- 01:04:53 (+137 s) Alerta resuelta: Latencia: consumo sostenido del presupuesto (latencia_consumo_lento).
- 01:04:53 (+137 s) La alerta P2 de latencia está activa: se abre el incidente y se sigue el runbook RB-02.
- 01:04:57 (+141 s) Mitigación aplicada: se retira la latencia inyectada.
- 01:05:02 (+146 s) Alerta activada: P3 Latencia: consumo sostenido del presupuesto (latencia_consumo_lento), runbook RB-02. El presupuesto de latencia se consume 14.3 veces más rápido de lo permitido.
- 01:05:02 (+146 s) Alerta resuelta: Latencia: consumo rápido del presupuesto (latencia_consumo_rapido).
- 01:05:29 (+173 s) Criterio de cierre del RB-02 cumplido: p95 de la ventana corta por debajo de 1 s.
- 01:07:11 (+275 s) Alerta resuelta: Latencia: consumo sostenido del presupuesto (latencia_consumo_lento).

### Ejecución de los runbooks

**01:04:55 · RB-02 · Confirmar.** Abrir el panel operativo y anotar hora, prioridad y magnitud.

```
$ curl http://127.0.0.1:8040/api/v1/observabilidad
{
 "estado": "degradado",
 "p95_ms": {
  "5m": 1534.0,
  "1h": 1529.0
 },
 "peticiones_lentas_pct": {
  "5m": 100.0,
  "1h": 72.4
 },
 "tasa_de_consumo_latencia": {
  "5m": 20.0,
  "30m": 14.5,
  "1h": 14.5,
  "6h": 14.5
 },
 "alertas": [
  "P2 latencia_consumo_rapido",
  "P2 presupuesto_agotado_latencia",
  "P4 retador_mejor"
 ]
}
```

Lectura: El p95 de la ventana corta es 1534 ms contra un umbral de 1 000 ms; 100 % de las peticiones tarda más de 1 s.

**01:04:57 · RB-02 · Confirmar.** Tomar el trace_id de una petición afectada y buscarla en el log.

```
$ curl -i 'http://127.0.0.1:8040/api/v1/pronostico?serie=OPERATIVO_TOTAL'  y  grep 2c1df2180c1a api.log
{
 "traceparent": "00-2c1df2180c1a22390dddd7deb5a73c82-943666d4eb7e45ca-01",
 "Server-Timing": "app;dur=1510.1, espera_inyectada;dur=1500.32",
 "linea_de_log": {
  "ts": "2026-11-15T01:04:56.518+00:00",
  "nivel": "INFO",
  "ruta": "/api/v1/pronostico",
  "estado": 200,
  "duracion_ms": 1510.06,
  "tramos": [
   {
    "nombre": "espera_inyectada",
    "ms": 1500.32
   }
  ],
  "latencia_inyectada_ms": 1500
 }
}
```

Lectura: La petición tardó 1510 ms y la respuesta trae su traza para seguirla en el log.

**01:04:57 · RB-02 · Diagnóstico 1.** Ver si la latencia alta es de todas las rutas o solo de las que calculan.

```
$ curl http://127.0.0.1:8040/metrics | grep flujo_http_duracion_segundos_bucket
{
 "/api/v1/alertas": {
  "peticiones": 127,
  "sobre_1s": 127,
  "sobre_1s_pct": 100.0
 },
 "/api/v1/metricas": {
  "peticiones": 125,
  "sobre_1s": 125,
  "sobre_1s_pct": 100.0
 },
 "/api/v1/modelos": {
  "peticiones": 116,
  "sobre_1s": 116,
  "sobre_1s_pct": 100.0
 },
 "/api/v1/pronostico": {
  "peticiones": 235,
  "sobre_1s": 235,
  "sobre_1s_pct": 100.0
 },
 "/api/v1/series": {
  "peticiones": 125,
  "sobre_1s": 125,
  "sobre_1s_pct": 100.0
 },
 "/api/v1/series/{serie}/historia": {
  "peticiones": 111,
  "sobre_1s": 111,
  "sobre_1s_pct": 100.0
 }
}
```

Lectura: Todas las rutas están lentas, también las que solo leen datos (series, modelos). No es un endpoint de cálculo.

**01:04:57 · RB-02 · Diagnóstico 2.** Revisar los tramos en los logs de las peticiones lentas.

```
$ grep '"duracion_ms"' api.log | python -c 'agrupa los tramos de las peticiones de más de 1 000 ms'
{
 "peticiones_lentas_en_el_log": 839,
 "duracion_promedio_ms": 1511.0,
 "tramos": {
  "espera_inyectada": {
   "peticiones": 839,
   "ms_promedio": 1501.0
  }
 },
 "ejemplo": {
  "ts": "2026-11-15T01:04:57.028+00:00",
  "ruta": "/api/v1/series/{serie}/historia",
  "duracion_ms": 1516.48,
  "tramos": [
   {
    "nombre": "espera_inyectada",
    "ms": 1501.41
   }
  ],
  "trace_id": "9d90106fafaccb2431bf24dd7b823f9d"
 }
}
```

Lectura: El tiempo no se va en el tramo de inferencia: el modelo no se está recalculando. La espera ocurre antes de llegar al cálculo (tramo espera_inyectada), que es lo que se ve cuando el servidor está saturado.

**01:04:57 · RB-02 · Diagnóstico 3.** Revisar si hay un backtest en cálculo, si la instancia se reinició y qué cambió antes del inicio.

```
$ curl http://127.0.0.1:8040/api/v1/salud  y  curl http://127.0.0.1:8040/api/v1/observabilidad/eventos
{
 "salud": {
  "estado": "ok",
  "version": "1.2.0",
  "datos": {
   "archivo": "movimientos_feban_sep2025_sep2026_anon.json",
   "formato": "json",
   "huella": "05b682222f318e4d",
   "desde": "2025-09-01",
   "hasta": "2026-09-30",
   "dias_habiles": 283,
   "movimientos": 4804,
   "duplicados": 0,
   "avisos": []
  },
  "modelos": [
   {
    "nombre": "ETS",
    "familia": "Estadístico",
    "backtest": "listo"
   },
   {
    "nombre": "SARIMAX",
    "familia": "Estadístico",
    "backtest": "listo"
   },
   {
    "nombre": "SARIMAX + temporada",
    "familia": "Estadístico",
    "backtest": "listo"
   },
   {
    "nombre": "Naive (referencia)",
    "familia": "Referencia",
    "backtest": "listo"
   }
  ],
  "modelo_recomendado": "ETS",
  "serie_principal": "OPERATIVO_TOTAL",
  "horizonte_maximo": 5,
  "umbral_deficit": 0.0,
  "fechas_pronostico": [
   "2026-10-01",
   "2026-10-02",
   "2026-10-05",
   "2026-10-06",
   "2026-10-07"
  ],
  "archivo_salida": "results/simulacro_incidentes/salidas_api/pronostico_ETS_2026-09-30.json"
 },
 "minutos_activa": 2.4,
 "cambios_recientes": [
  {
   "momento": "2026-11-15T01:03:24+00:00",
   "tipo": "simulacro",
   "mensaje": "Simulacro: 0 % de errores y 1500 ms de latencia extra.",
   "prioridad": null
  }
 ]
}
```

Lectura: No hay backtest en cálculo ni reinicio. La bitácora muestra el cambio que coincide con el inicio: la latencia inyectada.

**01:04:57 · RB-02 · Mitigación.** Quitar la causa de la saturación.

```
$ curl -X POST http://127.0.0.1:8040/api/v1/observabilidad/simulacro -H 'X-Token-Admin: ***' -d '{"latencia_ms": 0}'
{
 "estado": 200,
 "respuesta": {
  "error": 0.0,
  "latencia_ms": 0.0
 }
}
```

Lectura: En el simulacro la causa es la falla inyectada y se retira. En producción este paso es subir el plan de la instancia o reiniciarla, como indica el runbook.

**01:05:29 · RB-02 · Cierre.** Verificar que el p95 de la ventana corta volvió a estar por debajo de 1 s.

```
$ curl http://127.0.0.1:8040/api/v1/observabilidad
{
 "p95_ms_5m": 27.0,
 "peticiones_lentas_5m_pct": 0.0,
 "alertas": [
  "P2 presupuesto_agotado_latencia",
  "P3 latencia_consumo_lento",
  "P4 retador_mejor"
 ]
}
```

Lectura: El p95 volvió a 27 ms y la alerta P2 de consumo rápido se resolvió.

**01:07:15 · RB-08 · Diagnóstico.** Identificar cuánto presupuesto de error consumió el incidente.

```
$ curl http://127.0.0.1:8040/api/v1/observabilidad  (bloque slos)
{
 "slo_latencia": {
  "sli_pct": 61.1,
  "objetivo_pct": 95.0,
  "consumido_pct": 778.1,
  "estado": "agotado"
 },
 "antes": {
  "sli_pct": 100.0,
  "objetivo_pct": 95.0,
  "consumido_pct": 0.0,
  "estado": "cumple"
 },
 "alertas_que_siguen_activas": [
  "P2 presupuesto_agotado_latencia",
  "P4 retador_mejor"
 ]
}
```

Lectura: El SLO de latencia quedó en 61.1 % (objetivo 95 %) con 778.1 % del presupuesto consumido. El servicio ya responde normal, pero el presupuesto gastado no vuelve con la mitigación: se recupera con el tiempo.

### Resultado

El servicio se recuperó: el p95 de la ventana corta pasó de 1534 ms durante el incidente a 27 ms después de la mitigación (antes: 24 ms). Las alertas de consumo se resolvieron solas. Queda activa la alerta de presupuesto de latencia agotado, que es la consecuencia esperada: el simulacro concentra en minutos el tráfico lento que en producción se repartiría en 30 días.

### Seguimiento

- Aplicar RB-08: congelar cambios que no sean correcciones hasta recuperar el presupuesto de latencia.
- Las ventanas del simulacro van aceleradas 10 veces; con las ventanas reales la alerta P2 tarda más en activarse porque exige que 72 % de las peticiones de la última hora sean lentas.
- Evaluar una alerta adicional por p95 de 5 minutos para detectar antes una lentitud total.

## INC-02. Modelo degradado por un export defectuoso

- Inicio: 2026-11-15T01:14:47+00:00  |  Fin: 2026-11-15T01:19:30+00:00
- Causa simulada: Se carga un archivo en el que los últimos 17 días hábiles traen el saldo acumulado en lugar de los movimientos del día. El archivo tiene todos sus campos correctos y pasa la validación.
- Runbooks: RB-04, RB-06, RB-03, RB-05, RB-07
- Tiempos (segundos): {"backtest_recalculado_s": 229, "deteccion_s": 229, "modelo_degradado_activada_s": 63, "mitigacion_s": 245, "recuperacion_s": 9, "duracion_total_s": 283, "primera_alerta_s": 1}

### Cronología

- 01:14:47 (+0 s) Línea base: tráfico normal (2 peticiones por segundo) con el archivo original.
- 01:15:09 (+22 s) Inicio de la falla: se carga simulacro_saldos.json. La API lo acepta (respuesta 200) y recalcula el backtest.
- 01:15:10 (+23 s) Alerta activada: P3 Data drift significativo (drift_significativo), runbook RB-06. Los datos de las últimas 4 semanas cambiaron frente a su referencia en: OPERATIVO_TOTAL, OPERATIVO_BBVA, NETO_TOTAL, Reparto del importe por categoría.
- 01:15:10 (+23 s) Alerta activada: P4 Backtest en cálculo (backtest_calculando), runbook RB-04. Se calcula el backtest de: ETS, SARIMAX, SARIMAX + temporada, Naive (referencia).
- 01:15:10 (+23 s) Alerta resuelta: Un modelo retador mejora al recomendado (retador_mejor).
- 01:16:11 (+85 s) Alerta activada: P2 Modelo degradado (modelo_degradado), runbook RB-04. El rMAE móvil de ETS lleva 10 días sobre 0.90 (hoy 0.98) y hay data drift significativo.
- 01:16:11 (+85 s) Alerta activada: P3 Intervalo descalibrado (cobertura_fuera_de_rango), runbook RB-05. La cobertura móvil del rango P10 a P90 es de 58 %, fuera de 65 a 95 %.
- 01:16:11 (+85 s) Alerta activada: P3 Presupuesto de error agotado: calibración del intervalo (presupuesto_agotado_calibracion), runbook RB-08. Solo 87 % de los últimos 60 días cumple; el objetivo es 90 %.
- 01:16:11 (+85 s) Alerta activada: P3 Presupuesto de error agotado: calidad del pronóstico (presupuesto_agotado_calidad), runbook RB-08. Solo 77 % de los últimos 60 días cumple; el objetivo es 90 %.
- 01:18:58 (+251 s) Alerta resuelta: Backtest en cálculo (backtest_calculando).
- 01:18:58 (+251 s) El backtest terminó de recalcularse con los datos nuevos.
- 01:19:01 (+254 s) Las alertas del modelo ya notifican: se abre el incidente y se sigue el runbook RB-04.
- 01:19:13 (+266 s) Alerta activada: P4 Modelo de respaldo en uso (modelo_conmutado), runbook RB-04. La API recomienda SARIMAX en lugar de ETS.
- 01:19:13 (+266 s) Alerta resuelta: Modelo degradado (modelo_degradado).
- 01:19:13 (+266 s) Respuesta automática: la API conmuta de ETS a SARIMAX.
- 01:19:14 (+267 s) Mitigación aplicada: se restaura el archivo original.
- 01:19:22 (+275 s) Alerta activada: P4 Un modelo retador mejora al recomendado (retador_mejor), runbook RB-04. Chronos-2 (cross-learning) tiene 10.3 % menos error que ETS.
- 01:19:22 (+275 s) Alerta resuelta: Intervalo descalibrado (cobertura_fuera_de_rango).
- 01:19:22 (+275 s) Alerta resuelta: Data drift significativo (drift_significativo).
- 01:19:22 (+275 s) Alerta resuelta: Presupuesto de error agotado: calibración del intervalo (presupuesto_agotado_calibracion).
- 01:19:22 (+275 s) Alerta resuelta: Presupuesto de error agotado: calidad del pronóstico (presupuesto_agotado_calidad).
- 01:19:22 (+275 s) Alerta resuelta: Modelo de respaldo en uso (modelo_conmutado).
- 01:19:24 (+277 s) Criterio de cierre cumplido: métricas del modelo en sus valores normales y alertas resueltas.

### Ejecución de los runbooks

**01:19:01 · RB-04 · Confirmar.** Abrir el panel operativo y anotar las alertas activas y su prioridad.

```
$ curl http://127.0.0.1:8040/api/v1/observabilidad
{
 "estado": "degradado",
 "alertas": [
  {
   "prioridad": "P2",
   "alerta": "modelo_degradado",
   "runbook": "RB-04",
   "resumen": "El rMAE móvil de ETS lleva 10 días sobre 0.90 (hoy 0.98) y hay data drift significativo.",
   "silenciada_por": null
  },
  {
   "prioridad": "P3",
   "alerta": "cobertura_fuera_de_rango",
   "runbook": "RB-05",
   "resumen": "La cobertura móvil del rango P10 a P90 es de 58 %, fuera de 65 a 95 %.",
   "silenciada_por": null
  },
  {
   "prioridad": "P3",
   "alerta": "drift_significativo",
   "runbook": "RB-06",
   "resumen": "Los datos de las últimas 4 semanas cambiaron frente a su referencia en: OPERATIVO_TOTAL, OPERATIVO_BBVA, NETO_TOTAL, Reparto del importe por categoría.",
   "silenciada_por": null
  },
  {
   "prioridad": "P3",
   "alerta": "presupuesto_agotado_calibracion",
   "runbook": "RB-08",
   "resumen": "Solo 87 % de los últimos 60 días cumple; el objetivo es 90 %.",
   "silenciada_por": null
  },
  {
   "prioridad": "P3",
   "alerta": "presupuesto_agotado_calidad",
   "runbook": "RB-08",
   "resumen": "Solo 77 % de los últimos 60 días cumple; el objetivo es 90 %.",
   "silenciada_por": null
  }
 ]
}
```

Lectura: Hay 5 alertas activas; la de mayor prioridad es P2. La de modelo degradado es P2 porque coincide con data drift significativo. Se activó 63 s después de la carga, cuando terminó el backtest de ETS, pero quedó silenciada hasta que terminó el de todos los modelos.

**01:19:01 · RB-04 · Diagnóstico 1.** Ver el rMAE móvil de todos los modelos servidos: si todos subieron, el cambio está en los datos.

```
$ curl http://127.0.0.1:8040/api/v1/observabilidad  (bloque modelo.modelos)
{
 "durante": {
  "ETS": {
   "backtest": "listo",
   "rmae_movil": 0.976,
   "alarma_rmae": true,
   "cobertura_movil": 58.4,
   "alarma_cobertura": true
  },
  "SARIMAX": {
   "backtest": "listo",
   "rmae_movil": 0.879,
   "alarma_rmae": false,
   "cobertura_movil": 57.9,
   "alarma_cobertura": true
  },
  "SARIMAX + temporada": {
   "backtest": "listo",
   "rmae_movil": 0.934,
   "alarma_rmae": true,
   "cobertura_movil": 62.6,
   "alarma_cobertura": true
  },
  "Naive (referencia)": {
   "backtest": "listo",
   "rmae_movil": 1.0,
   "alarma_rmae": true,
   "cobertura_movil": 82.1,
   "alarma_cobertura": false
  }
 },
 "antes": {
  "ETS": {
   "backtest": "listo",
   "rmae_movil": 0.652,
   "alarma_rmae": false,
   "cobertura_movil": 84.2,
   "alarma_cobertura": false
  },
  "SARIMAX": {
   "backtest": "listo",
   "rmae_movil": 0.649,
   "alarma_rmae": false,
   "cobertura_movil": 84.2,
   "alarma_cobertura": false
  },
  "SARIMAX + temporada": {
   "backtest": "listo",
   "rmae_movil": 0.652,
   "alarma_rmae": false,
   "cobertura_movil": 84.2,
   "alarma_cobertura": false
  },
  "Naive (referencia)": {
   "backtest": "listo",
   "rmae_movil": 1.0,
   "alarma_rmae": true,
   "cobertura_movil": 95.3,
   "alarma_cobertura": true
  }
 }
}
```

Lectura: El rMAE móvil subió en 3 de los 3 modelos servidos y la cobertura cayó en 3. Un problema del modelo afectaría a uno solo: el cambio está en los datos.

**01:19:01 · RB-04 · Diagnóstico 2.** Revisar el bloque de data drift.

```
$ curl http://127.0.0.1:8040/api/v1/observabilidad  (bloque drift)
{
 "nivel": "significativo",
 "series": [
  {
   "serie": "OPERATIVO_TOTAL",
   "psi": 2.8,
   "p_ks": 0.0,
   "cambio_media_sd": 2.02,
   "media_referencia": 2760586.0,
   "media_reciente": 7995516.0,
   "nivel": "significativo"
  },
  {
   "serie": "OPERATIVO_BBVA",
   "psi": 1.78,
   "p_ks": 0.0,
   "cambio_media_sd": 3.99,
   "media_referencia": 1554082.0,
   "media_reciente": 7249884.0,
   "nivel": "significativo"
  },
  {
   "serie": "OPERATIVO_HSBC",
   "psi": 0.03,
   "p_ks": 0.9326,
   "cambio_media_sd": -0.23,
   "media_referencia": 1206504.0,
   "media_reciente": 745632.0,
   "nivel": "sin cambio"
  },
  {
   "serie": "NETO_TOTAL",
   "psi": 2.66,
   "p_ks": 0.0001,
   "cambio_media_sd": 0.92,
   "media_referencia": 110749.0,
   "media_reciente": 4804884.0,
   "nivel": "significativo"
  }
 ],
 "composicion": [
  {
   "indicador": "Movimientos por día",
   "nivel": "sin cambio"
  },
  {
   "indicador": "Reparto del importe por banco",
   "nivel": "moderado"
  },
  {
   "indicador": "Reparto del importe por categoría",
   "nivel": "significativo"
  }
 ]
}
```

Lectura: Data drift significativo. Cambiaron: OPERATIVO_TOTAL, OPERATIVO_BBVA, NETO_TOTAL, Reparto del importe por banco, Reparto del importe por categoría. Sin cambio: OPERATIVO_HSBC. Un cambio real del negocio no se quedaría en un solo banco.

**01:19:07 · RB-04 · Diagnóstico 3.** Registrar la corrida de monitoreo en MLflow para compararla con las anteriores.

```
$ python src/registrar_mlflow.py --solo-monitoreo --api <API> --datos data/simulacro_saldos.json
[
 "Monitoreo: corrida ad88a86a2dd543aaae678856adab0437 en 'flujo-efectivo-monitoreo' (drift significativo).",
 "{\"uri\": \"sqlite:///results/simulacro_incidentes/mlflow_simulacro.db\", \"segundos\": 1.0}"
]
```

Lectura: La corrida queda en el experimento flujo-efectivo-monitoreo con el drift y las alertas del incidente.

**01:19:13 · RB-04 · Mitigación 1.** Si otro modelo servido está por debajo de 0.90, conmutar a ese respaldo (en modo aplicar la API lo hace sola).

```
$ curl http://127.0.0.1:8040/api/v1/observabilidad/eventos  y  curl 'http://127.0.0.1:8040/api/v1/pronostico?serie=OPERATIVO_TOTAL'
{
 "modelo_recomendado": "SARIMAX",
 "modelo_original": "ETS",
 "modelo_que_sirve_el_pronostico": "SARIMAX",
 "bitacora": [
  {
   "momento": "2026-11-15T01:18:59+00:00",
   "tipo": "accion_recomendada",
   "mensaje": "Multiplicar el ancho del rango P10 a P90 de SARIMAX por 1.78 para volver a 80 % de cobertura. Requiere aprobación."
  },
  {
   "momento": "2026-11-15T01:18:58+00:00",
   "tipo": "accion_recomendada",
   "mensaje": "Multiplicar el ancho del rango P10 a P90 de ETS por 1.76 para volver a 80 % de cobertura. Requiere aprobación."
  },
  {
   "momento": "2026-11-15T01:18:58+00:00",
   "tipo": "accion_automatica",
   "mensaje": "Conmutar el modelo recomendado de ETS a SARIMAX (rMAE móvil 0.88) mientras ETS está degradado."
  }
 ],
 "alertas": [
  "P3 cobertura_fuera_de_rango",
  "P3 drift_significativo",
  "P3 presupuesto_agotado_calibracion",
  "P3 presupuesto_agotado_calidad",
  "P4 modelo_conmutado"
 ]
}
```

Lectura: La API conmutó sola de ETS a SARIMAX y el pronóstico ya sale del respaldo. La conmutación contiene el daño pero no arregla la causa: el respaldo pronostica con los mismos datos defectuosos.

**01:19:13 · RB-06 · Diagnóstico 1.** Ver qué series cambiaron y en qué dirección, y distinguir un cambio real de un error del export.

```
$ curl 'http://127.0.0.1:8040/api/v1/series/OPERATIVO_TOTAL/historia?dias=137'
{
 "media_referencia_120_dias": 2760586.0,
 "media_reciente_20_dias": 7995516.0,
 "cambio_en_desviaciones": 2.02,
 "parecido_de_cada_dia_con_el_anterior": {
  "120_dias_previos": 0.11,
  "ultimos_17_dias": 0.61
 },
 "flujo_operativo_diario_millones": [
  {
   "fecha": "2026-09-08",
   "millones": 5.06
  },
  {
   "fecha": "2026-09-09",
   "millones": 9.25
  },
  {
   "fecha": "2026-09-10",
   "millones": 11.56
  },
  {
   "fecha": "2026-09-11",
   "millones": 11.7
  },
  {
   "fecha": "2026-09-14",
   "millones": 16.75
  },
  {
   "fecha": "2026-09-15",
   "millones": 16.98
  },
  {
   "fecha": "2026-09-16",
   "millones": 19.69
  },
  {
   "fecha": "2026-09-17",
   "millones": 21.35
  },
  {
   "fecha": "2026-09-18",
   "millones": -4.49
  },
  {
   "fecha": "2026-09-21",
   "millones": -6.63
  },
  {
   "fecha": "2026-09-22",
   "millones": -2.53
  },
  {
   "fecha": "2026-09-23",
   "millones": 1.48
  },
  {
   "fecha": "2026-09-24",
   "millones": 4.86
  },
  {
   "fecha": "2026-09-25",
   "millones": 6.73
  },
  {
   "fecha": "2026-09-28",
   "millones": 7.5
  },
  {
   "fecha": "2026-09-29",
   "millones": 12.55
  },
  {
   "fecha": "2026-09-30",
   "millones": 16.0
  }
 ]
}
```

Lectura: El promedio diario pasó de 2.8 a 8.0 millones. Además, la correlación de cada día con el anterior pasó de 0.11 a 0.61: los últimos 17 días ya no oscilan alrededor del promedio, cada uno queda cerca del anterior. Es la forma de un saldo que se arrastra, no la de un flujo diario.

**01:19:13 · RB-03 · Diagnóstico.** Validar el archivo cargado.

```
$ python -m api.validar data/simulacro_saldos.json
{
 "codigo_de_salida": 0,
 "salida": [
  "Datos válidos.",
  "4,820 movimientos del 2025-09-01 al 2026-09-30 (283 días hábiles); bancos: BBVA 2,335, HSBC 2,485"
 ]
}
```

Lectura: El archivo pasa la validación: los campos son correctos. La validación revisa la estructura, no el nivel de los montos, por eso el export defectuoso entró. La causa es un error de datos y la mitigación es la del RB-03.

**01:19:13 · RB-05 · Diagnóstico.** Ver la cobertura del intervalo y la acción que propone la API.

```
$ curl http://127.0.0.1:8040/api/v1/observabilidad  (bloques modelo y respuesta_automatica)
{
 "cobertura_movil_por_modelo": {
  "ETS": 58.4,
  "SARIMAX": 57.9,
  "SARIMAX + temporada": 62.6,
  "Naive (referencia)": 82.1
 },
 "rango_aceptado": [
  65,
  95
 ],
 "acciones": [
  {
   "id": "conmutar_modelo",
   "descripcion": "Conmutar el modelo recomendado de ETS a SARIMAX (rMAE móvil 0.88) mientras ETS está degradado.",
   "ejecutada": true,
   "modo": "aplicar",
   "de": "ETS",
   "a": "SARIMAX"
  },
  {
   "id": "recalibrar_intervalo",
   "descripcion": "Multiplicar el ancho del rango P10 a P90 de ETS por 1.76 para volver a 80 % de cobertura. Requiere aprobación.",
   "ejecutada": false,
   "modo": "aplicar",
   "factor": 1.755693779412168
  }
 ]
}
```

Lectura: La cobertura de ETS bajó a 58.4 %: el intervalo quedó estrecho para los datos nuevos. La API propone un factor de recalibración pero no lo aplica sin aprobación. No se aprueba: recalibrar taparía un error de datos.

**01:19:13 · RB-07 · Diagnóstico.** Revisar la alerta de déficit y la confianza del modelo antes de avisar a tesorería.

```
$ curl 'http://127.0.0.1:8040/api/v1/alertas'  y  bloque modelo de /api/v1/observabilidad
{
 "antes": {
  "nivel": "vigilancia",
  "colchon_sugerido": 1664240.0
 },
 "durante": {
  "nivel": "ninguna",
  "colchon_sugerido": 0.0
 },
 "nivel_de_confianza": "baja"
}
```

Lectura: La alerta de déficit pasó de vigilancia a ninguna y el colchón sugerido de 1.66 a 0.00 millones: los datos defectuosos también mueven la señal de liquidez. Con confianza baja y datos en duda, el runbook indica que la alerta pesa menos: no se usa para decidir y se avisa a tesorería que el pronóstico del día no es confiable.

**01:19:14 · RB-03 · Mitigación.** Restaurar el último archivo bueno mientras se corrige el export en el origen.

```
$ curl -X POST http://127.0.0.1:8040/api/v1/datos/recargar -H 'X-Token-Admin: ***' -d '{"archivo": "movimientos_feban_sep2025_sep2026_anon.json"}'
{
 "estado": 200,
 "respuesta": {
  "aceptado": true,
  "datos_en_uso": "movimientos_feban_sep2025_sep2026_anon.json",
  "movimientos": 4804,
  "hasta": "2026-09-30",
  "backtest": {
   "ETS": "listo",
   "SARIMAX": "listo",
   "SARIMAX + temporada": "listo",
   "Naive (referencia)": "listo"
  }
 }
}
```

Lectura: La API valida y carga el archivo anterior. El backtest de ese archivo ya estaba calculado, así que las métricas vuelven de inmediato.

**01:19:24 · RB-04 · Cierre.** Verificar que el rMAE móvil, la cobertura y el drift volvieron a sus valores normales.

```
$ curl http://127.0.0.1:8040/api/v1/observabilidad
{
 "modelo_recomendado": "ETS",
 "rmae_movil": 0.652,
 "cobertura_movil": 84.2,
 "drift": "sin cambio",
 "slo_calidad": {
  "sli_pct": 100.0,
  "objetivo_pct": 90.0,
  "consumido_pct": 0.0,
  "estado": "cumple"
 },
 "slo_calibracion": {
  "sli_pct": 100.0,
  "objetivo_pct": 90.0,
  "consumido_pct": 0.0,
  "estado": "cumple"
 },
 "alertas": [
  "P4 retador_mejor"
 ]
}
```

Lectura: ETS vuelve a ser el modelo recomendado, con rMAE móvil 0.652, cobertura 84.2 % y drift sin cambio.

**01:19:29 · RB-04 · Registro.** Registrar en MLflow la corrida de monitoreo posterior a la recuperación.

```
$ python src/registrar_mlflow.py --solo-monitoreo --api <API>
[
 "Monitoreo: corrida 82292a4e8cc34678a1cd3f7c7a2fdf46 en 'flujo-efectivo-monitoreo' (drift sin cambio).",
 "{\"uri\": \"sqlite:///results/simulacro_incidentes/mlflow_simulacro.db\", \"segundos\": 1.1}"
]
```

Lectura: Quedan dos corridas de monitoreo para comparar: durante el incidente y después.

### Resultado

El modelo se recuperó al restaurar el archivo original: el rMAE móvil de ETS pasó de 0.652 antes del incidente a 0.976 durante y volvió a 0.652; la cobertura pasó de 84.2 % a 58.4 % y volvió a 84.2 %. El drift pasó de sin cambio a significativo y volvió a sin cambio.

### Seguimiento

- Agregar a la carga de datos un control de nivel: rechazar o dejar en cuarentena un archivo cuyo drift contra la historia sea significativo, antes de servir pronósticos con él.
- Pedir a tesorería el export corregido y cargarlo con POST /api/v1/datos/recargar.
- La alerta de modelo degradado quedó silenciada mientras se calculaba el backtest de los otros modelos. Conviene silenciarla solo mientras falte el backtest del modelo recomendado.
- El respaldo quedó apenas por debajo del límite de rMAE. Con datos defectuosos todos los modelos se deterioran, así que la conmutación solo gana tiempo.
- El recálculo del backtest tarda varios minutos; durante ese lapso la API sirve pronósticos con los datos nuevos sin métricas de confianza. Conviene conservar los datos anteriores hasta que el backtest termine.
