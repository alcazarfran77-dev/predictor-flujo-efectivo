/* Panel operativo: lee /api/v1/observabilidad cada 5 segundos y pinta SLO, alertas, tráfico, modelo y drift. */
(() => {
  "use strict";
  const ESTATICO = (document.querySelector('meta[name="flujo-modo"]') || {}).content === "estatico";
  const RECURSO = ESTATICO ? "api/v1/observabilidad.json" : "api/v1/observabilidad";
  const INTERVALO_MS = 5000;
  const $ = (id) => document.getElementById(id);
  const NS = "http://www.w3.org/2000/svg";

  function el(tag, attrs = {}, ...hijos) {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null || v === false) continue;
      if (k === "class") n.className = v; else if (k === "texto") n.textContent = v; else n.setAttribute(k, v);
    }
    n.append(...hijos.filter((h) => h != null));
    return n;
  }
  function svg(tag, attrs = {}, texto) {
    const n = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) if (v != null) n.setAttribute(k, v);
    if (texto != null) n.textContent = texto;
    return n;
  }
  const num = (v, d = 1) => (v == null ? "—" : Number(v).toLocaleString("es-MX", { minimumFractionDigits: d, maximumFractionDigits: d }));
  const hora = (iso) => new Date(iso).toLocaleTimeString("es-MX", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  const fechaCorta = (iso) => { const [, m, d] = iso.split("-"); return `${d}-${m}`; };
  const duracion = (min) => (min < 1 ? "menos de 1 min" : min < 90 ? `${Math.round(min)} min` : `${num(min / 60, 1)} h`);

  // ------------------------------------------------------------------ gráficas
  const W = 560, H = 190, M = { i: 62, d: 12, a: 14, b: 24 };
  function marco(id, maxY, minY, marcas, formato) {
    const lienzo = svg("svg", { viewBox: `0 0 ${W} ${H}` });
    const y = (v) => M.a + (H - M.a - M.b) * (1 - (v - minY) / (maxY - minY || 1));
    for (const v of marcas) {
      lienzo.append(svg("line", { class: "eje", x1: M.i, x2: W - M.d, y1: y(v), y2: y(v) }));
      lienzo.append(svg("text", { x: M.i - 6, y: y(v) + 4, "text-anchor": "end" }, formato(v)));
    }
    $(id).replaceChildren(lienzo);
    return { lienzo, y };
  }
  const marcasDe = (min, max, n = 4) => Array.from({ length: n + 1 }, (_, i) => min + (max - min) * i / n);
  function techo(v) {
    if (v <= 0) return 1;
    const p = 10 ** Math.floor(Math.log10(v));
    return [1, 2, 2.5, 5, 10].map((m) => m * p).find((m) => m >= v);
  }

  function graficaTrafico(serie) {
    const pts = serie.intervalos, max = techo(Math.max(1, ...pts.map((p) => p.peticiones)));
    const { lienzo, y } = marco("graf-trafico", max, 0, marcasDe(0, max), (v) => num(v, 0));
    const ancho = (W - M.i - M.d) / pts.length;
    pts.forEach((p, i) => {
      const x = M.i + i * ancho + 1;
      if (p.peticiones) lienzo.append(svg("rect", { class: "barra", x, width: Math.max(1, ancho - 2), y: y(p.peticiones), height: y(0) - y(p.peticiones), rx: 1.5 }));
      if (p.errores) lienzo.append(svg("rect", { class: "barra barra--error", x, width: Math.max(1, ancho - 2), y: y(p.errores), height: y(0) - y(p.errores), rx: 1.5 }));
    });
    lienzo.append(svg("text", { x: M.i, y: H - 6 }, hora(pts[0].momento)), svg("text", { x: W - M.d, y: H - 6, "text-anchor": "end" }, hora(pts.at(-1).momento)));
    $("graf-trafico").prepend(el("div", { class: "leyenda-op" }, el("span", {}, el("i"), "Peticiones"), el("span", {}, el("i", { class: "rojo" }), "Errores 5xx")));
  }

  function graficaLinea(id, puntos, { min, max, limite, zona, formato, etiquetaLimite, extremos, divisiones = 4 }) {
    const { lienzo, y } = marco(id, max, min, marcasDe(min, max, divisiones), formato);
    const validos = puntos.map((p, i) => [i, p]).filter(([, p]) => p != null);
    const x = (i) => M.i + (W - M.i - M.d) * (puntos.length > 1 ? i / (puntos.length - 1) : 0.5);
    if (zona) lienzo.append(svg("rect", { class: "zona", x: M.i, width: W - M.i - M.d, y: y(zona[1]), height: y(zona[0]) - y(zona[1]) }));
    if (limite != null) {
      lienzo.append(svg("line", { class: "limite", x1: M.i, x2: W - M.d, y1: y(limite), y2: y(limite) }));
      lienzo.append(svg("text", { x: W - M.d, y: y(limite) - 5, "text-anchor": "end" }, etiquetaLimite));
    }
    let d = "", previo = null;
    for (const [i, v] of validos) {
      const yy = y(Math.min(max, Math.max(min, v)));
      d += `${previo === i - 1 ? "L" : "M"}${x(i).toFixed(1)} ${yy.toFixed(1)}`;
      previo = i;
    }
    if (d) lienzo.append(svg("path", { class: "linea", d }));
    if (validos.length) {
      const [i, v] = validos.at(-1);
      lienzo.append(svg("circle", { class: "punto", cx: x(i), cy: y(Math.min(max, Math.max(min, v))), r: 4 }));
    }
    lienzo.append(svg("text", { x: M.i, y: H - 6 }, extremos[0]), svg("text", { x: W - M.d, y: H - 6, "text-anchor": "end" }, extremos[1]));
  }

  // ------------------------------------------------------------------ secciones
  const TEXTO_ESTADO = { "operando normal": "Operando normal", "con avisos": "Operando con avisos", degradado: "Servicio degradado", incidente: "Incidente en curso" };
  function pintarEstado(r) {
    const caja = $("estado-general");
    caja.dataset.estado = r.estado;
    $("estado-texto").textContent = TEXTO_ESTADO[r.estado] || r.estado;
    const notifican = r.alertas.filter((a) => a.notifica).length;
    $("estado-detalle").textContent = `${notifican ? `${notifican} alerta${notifican > 1 ? "s" : ""} por atender` : "Sin alertas por atender"} · ${ESTATICO ? "instantánea del" : "actualizado"} ${hora(r.generado)}`;
    const acel = r.proceso.ventanas_aceleradas > 1 ? ` Ventanas de alerta aceleradas ×${num(r.proceso.ventanas_aceleradas, 0)} (demostración).` : "";
    const sim = r.proceso.simulacro.error > 0 || r.proceso.simulacro.latencia_ms > 0
      ? ` Simulacro activo: ${num(r.proceso.simulacro.error * 100, 0)} % de errores y ${num(r.proceso.simulacro.latencia_ms, 0)} ms extra.` : "";
    $("contexto").textContent = `Modelo recomendado: ${r.modelo.recomendado}${r.modelo.original ? ` (respaldo de ${r.modelo.original})` : ""}. `
      + `Datos de ${r.datos.archivo} hasta el ${fechaCorta(r.datos.hasta)}-${r.datos.hasta.slice(0, 4)}, ${num(r.datos.movimientos, 0)} movimientos. `
      + `API ${r.proceso.version}, activa desde hace ${duracion(r.proceso.segundos_activo / 60)}.${acel}${sim}`;
    $("pie-version").textContent = `Versión ${r.proceso.version}`;
  }

  function pintarSLO(slos) {
    $("slos").replaceChildren(...slos.map((s) => {
      const conPresupuesto = s.presupuesto_restante_pct != null;
      const valor = s.id === "frescura" ? `${s.valor} día${s.valor === 1 ? "" : "s"}` : s.sli_pct == null ? "—" : `${num(s.sli_pct, s.sli_pct >= 99 && s.sli_pct < 100 ? 2 : 1)} %`;
      const objetivo = s.id === "frescura" ? `Atraso máximo: ${s.limite} día hábil` : `Objetivo: ${num(s.objetivo_pct, s.objetivo_pct % 1 ? 1 : 0)} % en ${s.ventana}`;
      const pie = conPresupuesto
        ? `Presupuesto restante: ${num(s.presupuesto_restante_pct, 0)} % (${num(s.malos, 0)} de ${num(s.presupuesto_eventos, 1)} permitidos)`
        : s.id === "frescura" ? "Atraso de los movimientos frente a la fecha de referencia" : "Sin datos todavía";
      const estado = { cumple: "Cumple", "en riesgo": "En riesgo", agotado: "Presupuesto agotado", incumple: "No cumple", "sin tráfico": "Sin tráfico", "sin datos": "Sin datos" }[s.estado] || s.estado;
      return el("article", { class: "slo", "data-estado": s.estado, title: s.descripcion },
        el("span", { class: "slo__nombre", texto: s.nombre }),
        el("span", { class: "slo__valor", texto: valor }),
        el("span", { class: "slo__objetivo", texto: objetivo }),
        conPresupuesto ? el("div", { class: "slo__barra", role: "img", "aria-label": `Presupuesto restante ${num(s.presupuesto_restante_pct, 0)} %` },
          el("span", { style: `width:${Math.max(2, s.presupuesto_restante_pct)}%` })) : null,
        el("span", { class: "slo__pie", texto: pie }),
        el("span", { class: "slo__estado", texto: estado }));
    }));
  }

  function pintarAlertas(r) {
    const cuerpo = $("tabla-alertas").tBodies[0];
    const p = r.prioridades;
    $("alertas-nota").textContent = `P1: ${p.P1.atencion.toLowerCase()}. P2: ${p.P2.atencion.toLowerCase()}. P3: ${p.P3.atencion.toLowerCase()}. P4: informativa, no notifica.`;
    if (!r.alertas.length) {
      cuerpo.replaceChildren(el("tr", {}, el("td", { colspan: 6, class: "sin-alertas", texto: "No hay alertas activas." })));
      return;
    }
    cuerpo.replaceChildren(...r.alertas.map((a) => el("tr", { class: a.inhibida_por ? "inhibida" : null },
      el("td", {}, el("span", { class: `prioridad prioridad--${a.prioridad}`, texto: a.prioridad })),
      el("td", {}, el("strong", { texto: a.nombre })),
      el("td", {}, document.createTextNode(a.resumen), el("small", { texto: a.impacto })),
      el("td", {}, document.createTextNode(hora(a.desde)), el("small", { texto: `hace ${duracion(a.minutos_activa)}` })),
      el("td", {}, document.createTextNode(a.runbook), el("small", { texto: a.runbook_nombre })),
      el("td", {}, document.createTextNode(a.inhibida_por ? "Inhibida" : a.notifica ? a.canal : "Solo tablero"),
        a.inhibida_por ? el("small", { texto: `por ${a.inhibida_por}` }) : null))));
  }

  function pintarTrafico(r) {
    const v = r.trafico.ventanas, t = r.trafico.total, s = r.trafico.serie;
    graficaTrafico(s);
    $("trafico-nota").textContent = `Por intervalos de ${num(s.segundos_por_intervalo, 0)} s. Desde el arranque: ${num(t.peticiones, 0)} peticiones, ${num(t.errores_5xx, 0)} con error 5xx (${num(t.tasa_error * 100, 2)} %).`;
    const p95 = s.intervalos.map((p) => p.p95_ms);
    const max = techo(Math.max(1200, ...p95.filter((x) => x != null)));
    graficaLinea("graf-latencia", p95, { min: 0, max, limite: 1000, formato: (x) => `${num(x, 0)} ms`, etiquetaLimite: "umbral del SLO: 1 s",
      extremos: [hora(s.intervalos[0].momento), hora(s.intervalos.at(-1).momento)] });
    $("latencia-nota").textContent = `p50 de ${num(v["1h"].p50_ms, 0)} ms y p95 de ${num(v["1h"].p95_ms, 0)} ms en la última hora; ${num(t.lentas, 0)} peticiones tardaron más de 1 s.`;
    const tasa = (x, limite) => el("td", { class: `num${x >= limite ? " tasa-alta" : ""}`, texto: num(x, 1) });
    $("tabla-consumo").tBodies[0].replaceChildren(...r.slos.filter((x) => x.tasas_consumo).map((x) => el("tr", {},
      el("td", { texto: x.nombre }), tasa(x.tasas_consumo["5m"], 14.4), tasa(x.tasas_consumo["30m"], 6), tasa(x.tasas_consumo["1h"], 14.4), tasa(x.tasas_consumo["6h"], 6),
      el("td", { class: "num", texto: num(x.peticiones_por_ventana["1h"], 0) }), el("td", { class: "num", texto: `${num(x.malos, 0)} de ${num(x.eventos, 0)}` }))));
  }

  function pintarModelo(m) {
    const rm = m.serie_rmae, cb = m.serie_cobertura;
    if (rm.length) {
      graficaLinea("graf-rmae", rm.map((p) => p.valor), { min: 0.4, max: Math.max(1.2, 0.4 + Math.ceil((Math.max(...rm.map((p) => p.valor)) - 0.4) / 0.2) * 0.2), limite: m.limite_rmae, formato: (x) => num(x, 2),
        etiquetaLimite: `límite ${num(m.limite_rmae, 2)}`, extremos: [fechaCorta(rm[0].fecha), fechaCorta(rm.at(-1).fecha)] });
      $("rmae-nota").textContent = `${m.recomendado} sobre ${m.serie}: ${num(m.rmae_movil, 2)} en los últimos 20 días. Por debajo de 1 mejora al pronóstico ingenuo; la alarma salta con 10 días seguidos sobre ${num(m.limite_rmae, 2)}.`;
    } else { $("graf-rmae").replaceChildren(el("p", { class: "sin-alertas", texto: "El backtest todavía se está calculando." })); $("rmae-nota").textContent = ""; }
    if (cb.length) {
      graficaLinea("graf-cobertura", cb.map((p) => p.valor), { min: 50, max: 100, divisiones: 5, zona: m.rango_cobertura, formato: (x) => `${num(x, 0)} %`, extremos: [fechaCorta(cb[0].fecha), fechaCorta(cb.at(-1).fecha)] });
      $("cob-nota").textContent = `${num(m.cobertura_movil, 0)} % de los reales cayó dentro del rango P10 a P90 en los últimos 40 días. La franja marca el rango aceptado, de ${m.rango_cobertura[0]} a ${m.rango_cobertura[1]} %; lo ideal es 80 %.`;
    } else { $("graf-cobertura").replaceChildren(el("p", { class: "sin-alertas", texto: "El backtest todavía se está calculando." })); $("cob-nota").textContent = ""; }
  }

  function pintarDrift(d) {
    const c = d.criterio;
    $("drift-nota").textContent = `Últimos ${c.dias_reciente} días hábiles contra los ${c.dias_referencia} anteriores. Significativo: PSI de ${c.psi_alto} o más y prueba KS con p menor a ${c.alfa_ks}. `
      + `Con ventanas tan cortas el PSI solo no basta, así que una sola señal deja el nivel en moderado.`;
    const medidor = (v) => el("span", { class: "medidor" }, el("span", { class: "medidor__pista" }, el("span", { class: v >= c.psi_alto ? "alto" : null, style: `width:${Math.min(100, v / 0.5 * 100)}%` })), document.createTextNode(num(v, 2)));
    const nivel = (n) => el("span", { class: `nivel-drift nivel-drift--${n.replace(" ", "-")}`, texto: n[0].toUpperCase() + n.slice(1) });
    const filas = [
      ...d.series.map((f) => el("tr", {}, el("td", { texto: f.serie }), el("td", {}, f.psi == null ? "—" : medidor(f.psi)),
        el("td", { class: "num", texto: f.p_ks == null ? "—" : f.p_ks < 0.001 ? "< 0.001" : num(f.p_ks, 3) }),
        el("td", { class: "num", texto: f.cambio_media_sd == null ? "—" : `${f.cambio_media_sd >= 0 ? "+" : "−"}${num(Math.abs(f.cambio_media_sd), 2)} desv. est.` }), el("td", {}, nivel(f.nivel)))),
      ...d.composicion.map((f) => el("tr", {}, el("td", { texto: f.indicador }), el("td", {}, medidor(f.psi)),
        el("td", { class: "num", texto: f.p_ks == null ? "—" : num(f.p_ks, 3) }),
        el("td", { class: "num", texto: f.referencia == null ? "—" : `${num(f.referencia, 1)} → ${num(f.reciente, 1)}` }), el("td", {}, nivel(f.nivel)))),
    ];
    $("tabla-drift").tBodies[0].replaceChildren(...filas);
  }

  function pintarAcciones(r) {
    const modo = r.respuesta_automatica.modo;
    $("auto-nota").textContent = { aplicar: "Modo aplicar: las acciones se ejecutan solas y quedan en la bitácora.", recomendar: "Modo recomendar: la API propone la acción y una persona la confirma.", off: "Respuesta automática apagada." }[modo] || modo;
    const fijas = [
      ["Carga de datos inválida", "Se rechaza el archivo y la API conserva los datos anteriores.", "Siempre activa"],
      ["Datos nuevos", "El backtest se recalcula en segundo plano y las métricas responden 503 con Retry-After mientras termina.", "Siempre activa"],
      ["Contenedor sin respuesta", "La plataforma reinicia el contenedor cuando falla la revisión de salud; Docker Compose, cuando el proceso termina.", "Plataforma"],
      ["Despliegue defectuoso", "La plataforma conserva la versión anterior si la nueva no pasa la revisión de salud.", "Plataforma"],
    ];
    const propias = r.respuesta_automatica.acciones.map((a) => el("li", {}, el("strong", { texto: a.ejecutada ? "Ejecutada" : "Recomendada" }), document.createTextNode(a.descripcion)));
    $("acciones").replaceChildren(...propias, ...fijas.map(([t, d, e]) => el("li", {}, el("strong", { texto: t }), document.createTextNode(d), el("small", { texto: e }))));
    $("eventos").replaceChildren(...(r.eventos.length ? r.eventos : [{ momento: r.generado, mensaje: "Sin eventos desde el arranque." }]).map((e) =>
      el("li", {}, el("time", { datetime: e.momento, texto: hora(e.momento) }), document.createTextNode(e.mensaje))));
  }

  async function actualizar() {
    try {
      const resp = await fetch(RECURSO, { cache: "no-store" });
      if (!resp.ok) throw new Error(`La API respondió ${resp.status}`);
      const r = await resp.json();
      pintarEstado(r); pintarSLO(r.slos); pintarAlertas(r); pintarTrafico(r); pintarModelo(r.modelo); pintarDrift(r.drift); pintarAcciones(r);
      document.body.dataset.estado = "listo";
    } catch (e) {
      $("estado-general").dataset.estado = "sin conexión";
      $("estado-texto").textContent = "Sin respuesta de la API";
      $("estado-detalle").textContent = String(e.message || e);
      document.body.dataset.estado = "error";
    }
  }
  if (ESTATICO) { $("enlace-metricas").hidden = true; $("enlace-json").href = RECURSO; }
  actualizar();
  if (!ESTATICO) setInterval(actualizar, INTERVALO_MS);
})();
