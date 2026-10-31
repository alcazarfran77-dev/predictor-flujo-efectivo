# Datos

Los movimientos vienen de un export de la transacción FEBAN de SAP (posproceso de extractos bancarios electrónicos), con dos cuentas, una en BBVA y otra en HSBC, del 01-09-2025 al 30-09-2026. Están en dos formatos con el mismo contenido:

| Archivo | Lo usa |
|---|---|
| `DEPOSITOS_CARGADOS_FEBAN_sep2025_sep2026_anon.xlsx` | El cuaderno y `src/ejecutar_backtest.py` |
| `movimientos_feban_sep2025_sep2026_anon.json` | La API (`api/`), que solo lee JSON y lo valida antes de pronosticar |
| `esquema_movimientos.json` | Esquema formal (JSON Schema) del archivo JSON |

Comprobé que los dos archivos producen exactamente las mismas series diarias; por eso la API reutiliza el backtest de `results/`.

## Contenido

La hoja `Hoja1` tiene 4,804 movimientos en orden cronológico, sin duplicados, con las columnas originales del export:

| Columna | Uso en el proyecto |
|---|---|
| `Value date` | Fecha valor; define el día del flujo |
| `Amount` | Importe en MXN (positivo = entrada, negativo = salida) |
| `House Bk` | Banco (BBVA o HSBC) |
| `MR no` | Número de partida dentro del extracto; forma parte de la llave de deduplicación |
| `Payment Notes` | Concepto del banco; con él se separa la tesorería del flujo operativo |
| `External transaction` | Tipo de transacción (NTRF, NCOL, NCOM, NCMS, etc.) |
| `AC` | Moneda; solo hay MXN |
| `Shrt key` | Clave de carga del extracto |
| `Post. rule` | Regla contable |
| `Customer`, `DocumentNo` | Vacías en este export |

La hoja `Notas_datos` documenta cómo se armó el archivo, los supuestos de generación y un resumen mensual con fórmulas sobre `Hoja1`.

## Origen de los movimientos

Solo tenía seis días hábiles de movimientos reales (del 01 al 08-09-2026, 107 movimientos, filas 4,451 a 4,557 de `Hoja1`). El export original traía cada extracto cargado varias veces, así que conservé la carga más reciente de cada banco y eliminé las copias.

Para tener un año de historia completé el resto con datos ficticios (4,697 movimientos): del 01-09-2025 al 31-08-2026 y del 09 al 30-09-2026. Usé las mismas columnas, bancos, tipos de transacción, reglas contables y formatos de concepto del export real, calibrados con los montos de esa semana. Los únicos efectos estacionales que agregué son mayor ingreso de enero a marzo (1.35 veces el normal) y mayor gasto de septiembre a diciembre (2 veces el normal); el resto de los meses es normal. La tesorería sigue la misma lógica del real: HSBC cierra en cero cada día con barridos y BBVA concentra fondos en HSBC cada dos semanas.

El pipeline trata todo el archivo como historia FEBAN con una sola carga por extracto y no distingue qué filas son reales.

## Anonimización

Los conceptos bancarios del export real traen nombres de clientes y proveedores, cuentas CLABE, números de cuenta y de contrato. Antes de subir el archivo reemplacé esos datos:

- Los nombres de clientes y proveedores pasaron a etiquetas genéricas (`CLIENTE ALFA`, `PAGO CLIENTE BETA`, `CARTERA PROVEEDOR`, etc.).
- Las cuentas CLABE de los conceptos SPEI se cambiaron por números consecutivos ficticios, uno por cada cuenta original.
- La cuenta de concentración, el contrato de terminal, el prefijo de cuenta de los depósitos en efectivo y las cuentas de los pagos de terceros también son ficticios.
- Se quitaron el autor y el último editor de las propiedades del archivo.

No cambié importes, fechas, bancos, tipos de transacción ni las palabras con las que se clasifican los movimientos (`SWEEP`, `GIROFONDO`, `SPEI ENVIADO HSB`, `COMISION`, `CARTERA`, `DEPOSITO`, etc.). Comprobé que la versión anonimizada produce exactamente las mismas series diarias y la misma clasificación que el archivo original.

## Formato JSON

Es un objeto con la lista `movimientos`, un movimiento por línea:

```json
{
  "formato": "flujo-efectivo/movimientos",
  "version": "1.0",
  "moneda": "MXN",
  "movimientos": [
    {"fecha": "2025-09-01", "monto": 4177.04, "banco": "BBVA", "concepto": "SPEI RECIBIDOHSB T20 0142321667  021 SPEI RECIBIDOHSBC        00", "tipo_transaccion": "NTRF", "partida": "00002", "clave_extracto": "00742395", "regla_contable": "PTM1", "moneda": "MXN"}
  ]
}
```

| Campo | Obligatorio | Para qué sirve | Encabezado FEBAN equivalente |
|---|---|---|---|
| `fecha` | Sí | Día del flujo, AAAA-MM-DD | `Value date` |
| `monto` | Sí | Número; positivo = entrada, negativo = salida | `Amount` |
| `banco` | Sí | Arma las series por banco | `House Bk` |
| `concepto` | Sí | Separa la tesorería del flujo operativo | `Payment Notes` |
| `tipo_transaccion` | No | Ayuda a clasificar comisiones y depósitos | `External transaction` |
| `partida` | No | Detecta partidas repetidas por cargas múltiples | `MR no` |
| `clave_extracto` | No | Identifica la carga del extracto | `Shrt key` |
| `regla_contable` | No | Informativo | `Post. rule` |
| `moneda` | No | Solo se pronostican los movimientos en MXN | `AC` |

También se acepta directamente la lista de movimientos sin el objeto que la envuelve, y los encabezados del export FEBAN en lugar de los nombres del formato.

Antes de pronosticar, la API revisa que cada movimiento tenga sus cuatro datos obligatorios con el tipo correcto, que la historia cubra al menos 10 días hábiles y que haya movimientos operativos (si todo es tesorería no hay nada que pronosticar). Si algo falla, no arranca y dice en qué movimiento y en qué campo está cada problema. Además avisa, sin detenerse, de duplicados, huecos de cinco o más días hábiles sin movimientos, movimientos en otra moneda, campos opcionales que faltan y de historias de menos de 60 días hábiles, con las que las métricas de confianza no son concluyentes.

Para revisar un archivo sin levantar la API:

```bash
python -m api.validar data/movimientos_feban_sep2025_sep2026_anon.json
```

## Usar otro export

Un export FEBAN en Excel se convierte al formato JSON, y queda validado, con:

```bash
python src/excel_a_json.py export_feban.xlsx data/movimientos.json
```

El cuaderno y `src/` leen cualquier export FEBAN con estas columnas. Si las columnas cambian de nombre, se ajustan en `COLUMNAS` (celda 4.1 del cuaderno o `src/flujo_efectivo/config.py`), y las reglas para separar la tesorería en `REGLAS_CATEGORIA`, con la lista de cuentas propias de la empresa. Los exports reales sin anonimizar no deben subirse al repositorio; el `.gitignore` excluye cualquier `*FEBAN*.xlsx` o `*feban*.json` que no termine en `_anon`.
