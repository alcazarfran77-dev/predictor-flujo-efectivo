"use strict";
// Dashboard del predictor de flujo de efectivo. Consume la API REST en api/v1/ (misma raíz que esta página,
// o la indicada con ?api=https://servidor/). En la versión publicada en GitHub Pages (meta flujo-modo=estatico)
// no hay servidor: cada petición se resuelve con el archivo JSON que generó `python -m api.publicar`.
// No usa librerías externas.
(() => {
  const params = new URLSearchParams(location.search);
  const BASE_API = (() => {
    const b = params.get("api");
    if (!b) return "";
    return b.endsWith("/") ? b : b + "/";
  })();
  const ESTATICO = (document.querySelector('meta[name="flujo-modo"]') || {}).content === "estatico";
  const slug = (m) => m.replace(/[^A-Za-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
  function archivoEstatico(recurso) {
    const [camino, consulta = ""] = recurso.split("?");
    const q = new URLSearchParams(consulta);
    const modelo = slug(q.get("modelo") || "ETS");
    if (camino === "pronostico") return `pronostico/${q.get("serie")}/${modelo}.json`;
    if (camino === "metricas") return `metricas/${q.get("serie")}/${modelo}.json`;
    if (camino === "alertas") return `alertas/${modelo}.json`;
    if (camino === "reporte") return `reporte/${modelo}.json`;
    return `${camino}.json`;          // salud, series, modelos, series/<id>/historia
  }
  const ruta = (p) => BASE_API + "api/v1/" + (ESTATICO ? archivoEstatico(p) : p);
  const TEXTO_CONECTADO = ESTATICO ? "Versión publicada, sin servidor" : "API conectada";
  const NS = "http://www.w3.org/2000/svg";
  const $ = (id) => document.getElementById(id);
  const enc = encodeURIComponent;

  // ------------------------------------------------------------------ formato
  const num2 = new Intl.NumberFormat("es-MX", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const num1 = new Intl.NumberFormat("es-MX", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  const DIAS = ["dom", "lun", "mar", "mié", "jue", "vie", "sáb"];
  const signo = (v) => (v < 0 ? "−" : "");
  const millones = (v, d = 2) => (v == null ? "—" : signo(v) + (d === 1 ? num1 : num2).format(Math.abs(v) / 1e6));
  const pct = (p, d = 0) => (p == null ? "—" : `${(p * 100).toFixed(d)} %`);
  const pctDirecto = (v, d = 0) => (v == null ? "—" : `${v.toFixed(d)} %`);
  const dec = (v, d = 2) => (v == null ? "—" : signo(v) + Math.abs(v).toFixed(d));
  const aFecha = (iso) => new Date(iso + "T00:00:00Z");
  const ddmm = (iso) => { const f = aFecha(iso); return `${String(f.getUTCDate()).padStart(2, "0")}-${String(f.getUTCMonth() + 1).padStart(2, "0")}`; };
  const diaCorto = (iso) => `${DIAS[aFecha(iso).getUTCDay()]} ${ddmm(iso)}`;
  const fechaLarga = (iso) => `${ddmm(iso)}-${aFecha(iso).getUTCFullYear()}`;
  const fechaLocal = (marca) => {       // marca de tiempo ISO -> dd-mm-aaaa en la zona horaria de quien ve la página
    const f = new Date(marca), dos = (n) => String(n).padStart(2, "0");
    return `${dos(f.getDate())}-${dos(f.getMonth() + 1)}-${f.getFullYear()}`;
  };

  function el(tag, attrs = {}, ...hijos) {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null || v === false) continue;
      if (k === "class") n.className = v;
      else if (k === "texto") n.textContent = v;
      else n.setAttribute(k, v);
    }
    for (const h of hijos) if (h != null) n.append(h.nodeType ? h : document.createTextNode(String(h)));
    return n;
  }
  function svg(tag, attrs = {}) {
    const n = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) if (v != null) n.setAttribute(k, v);
    return n;
  }

  // ------------------------------------------------------------------ íconos de estado (siempre con texto)
  const ICONOS = {
    ninguna: '<circle cx="12" cy="12" r="10" fill="var(--bien)"/><path d="M7.5 12.5l3 3 6-6.5" fill="none" stroke="#fff" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/>',
    vigilancia: '<path d="M12 2.5l10 18H2z" fill="var(--aviso)"/><path d="M12 9v5.5" stroke="#14212e" stroke-width="2.4" stroke-linecap="round"/><circle cx="12" cy="17.6" r="1.4" fill="#14212e"/>',
    media: '<path d="M12 2.5l10 18H2z" fill="var(--serio)"/><path d="M12 9v5.5" stroke="#14212e" stroke-width="2.4" stroke-linecap="round"/><circle cx="12" cy="17.6" r="1.4" fill="#14212e"/>',
    alta: '<path d="M8 2h8l6 6v8l-6 6H8l-6-6V8z" fill="var(--critico)"/><path d="M12 7v6.5" stroke="#fff" stroke-width="2.4" stroke-linecap="round"/><circle cx="12" cy="17" r="1.4" fill="#fff"/>',
    no: '<circle cx="12" cy="12" r="10" fill="var(--critico)"/><path d="M8.5 8.5l7 7M15.5 8.5l-7 7" stroke="#fff" stroke-width="2.4" stroke-linecap="round"/>',
    nd: '<circle cx="12" cy="12" r="9" fill="none" stroke="var(--tinta-3)" stroke-width="2"/><path d="M8 12h8" stroke="var(--tinta-3)" stroke-width="2.2" stroke-linecap="round"/>',
  };
  ICONOS.si = ICONOS.ninguna;
  ICONOS.bien = ICONOS.ninguna;
  const icono = (clave) => {
    const s = document.createElementNS(NS, "svg");
    s.setAttribute("viewBox", "0 0 24 24");
    s.setAttribute("aria-hidden", "true");
    s.setAttribute("class", "icono");
    s.innerHTML = ICONOS[clave] || ICONOS.nd;   // contenido fijo de este archivo, nunca datos de la API
    return s;
  };
  const NOMBRE_NIVEL = { ninguna: "Sin alerta", vigilancia: "Vigilancia", media: "Riesgo moderado", alta: "Déficit probable" };
  const NOMBRE_CONFIANZA = { alta: "Alta", media: "Media", baja: "Baja" };
  const ICONO_CONFIANZA = { alta: "ninguna", media: "vigilancia", baja: "alta" };
  const etiquetaNivel = (nivel) => el("span", { class: "nivel" }, icono(nivel), NOMBRE_NIVEL[nivel] || nivel);

  // ------------------------------------------------------------------ comunicación con la API
  const registro = [];
  let totalPeticiones = 0;
  class ErrorAPI extends Error {
    constructor(estado, mensaje, reintentar) { super(mensaje); this.estado = estado; this.reintentar = reintentar; }
  }

  async function pedir(recurso, opciones = {}) {
    const metodo = opciones.method || "GET";
    const t0 = performance.now();
    let r;
    try {
      r = await fetch(ruta(recurso), { headers: { Accept: "application/json", ...(opciones.headers || {}) }, ...opciones });
    } catch (e) {
      anotar({ metodo, recurso: ESTATICO ? archivoEstatico(recurso) : recurso, estado: "sin respuesta", ms: performance.now() - t0, servidor: null });
      throw new ErrorAPI(0, `No hubo respuesta de ${new URL(ruta(recurso), location.href).href}.`);
    }
    const ms = performance.now() - t0;
    const st = r.headers.get("Server-Timing");
    const servidor = st && st.includes("dur=") ? parseFloat(st.split("dur=")[1]) : null;
    anotar({ metodo, recurso: ESTATICO ? archivoEstatico(recurso) : recurso, estado: r.status, ms, servidor });
    const version = r.headers.get("X-Version-API");
    if (version) $("version-api").textContent = `Versión ${version}`;
    let cuerpo = null;
    try { cuerpo = await r.json(); } catch (e) { /* respuesta sin JSON */ }
    if (!r.ok) {
      const det = cuerpo && cuerpo.detail;
      throw new ErrorAPI(r.status, typeof det === "string" ? det : JSON.stringify(det), Number(r.headers.get("Retry-After")) || null);
    }
    return cuerpo;
  }

  function anotar(entrada) {
    totalPeticiones += 1;
    entrada.hora = new Date().toLocaleTimeString("es-MX", { hour12: false });
    registro.unshift(entrada);
    registro.length = Math.min(registro.length, 12);
    const tb = $("tabla-registro").tBodies[0];
    tb.replaceChildren(...registro.map((e) => el("tr", {},
      el("td", { texto: e.hora }), el("td", { texto: e.metodo }), el("td", { texto: "api/v1/" + e.recurso }),
      el("td", { class: "num " + (typeof e.estado === "number" && e.estado < 400 ? "estado-ok" : "estado-error"), texto: String(e.estado) }),
      el("td", { class: "num", texto: `${Math.round(e.ms)} ms` }),
      el("td", { class: "num", texto: e.servidor == null ? "—" : `${Math.round(e.servidor)} ms` }))));
    const u = registro[0];
    $("registro-resumen").textContent =
      `${totalPeticiones} peticiones; la última, ${u.metodo} ${u.recurso.split("?")[0]}, respondió ${u.estado} en ${Math.round(u.ms)} ms`;
  }

  function conexion(estado, texto) {
    $("conexion").dataset.estado = estado;
    $("conexion-texto").textContent = texto;
  }

  function mostrarError(e) {
    conexion("error", "Sin conexión con la API");
    $("error").hidden = false;
    $("error-texto").textContent = e.estado === 0
      ? `${e.message} Revisa que el servidor esté corriendo y vuelve a intentar.`
      : ESTATICO ? `No se pudo leer un archivo de la página (${e.estado}). Vuelve a generar la carpeta docs/ con python -m api.publicar.`
        : `La API respondió ${e.estado}: ${e.message}`;
    $("principal").dataset.estado = "error";
  }

  // ------------------------------------------------------------------ estado y carga
  const estado = { salud: null, series: [], serie: null, modelo: null, pronostico: null, historia: [], metricas: null };
  let reintentoMetricas = null;

  async function iniciar() {
    $("error").hidden = true;
    conexion("pendiente", "Conectando…");
    try {
      const [salud, series] = await Promise.all([pedir("salud"), pedir("series")]);
      estado.salud = salud;
      estado.series = series;
      const ids = series.map((s) => s.id);
      const nombres = salud.modelos.map((m) => m.nombre);
      estado.serie = ids.includes(params.get("serie")) ? params.get("serie") : salud.serie_principal;
      estado.modelo = nombres.includes(params.get("modelo")) ? params.get("modelo") : salud.modelo_recomendado;
      llenarSelectores();
      const d = salud.datos;
      const fecha = (iso) => el("span", { class: "sin-corte", texto: fechaLarga(iso) });
      $("contexto").replaceChildren(
        `Datos de ${d.archivo}, validados: ${d.movimientos.toLocaleString("es-MX")} movimientos del `, fecha(d.desde),
        " al ", fecha(d.hasta), ` (${d.dias_habiles} días hábiles). El pronóstico arranca después del último dato, el `,
        fecha(d.hasta), "." + (d.avisos && d.avisos.length ? ` Avisos de la validación: ${d.avisos.join(" ")}` : "") +
        (ESTATICO && salud.publicado ? ` Versión publicada el ${fechaLocal(salud.publicado)}; se actualiza al volver a generar la página.` : ""));
      if (BASE_API) $("enlace-docs").href = BASE_API + "docs";
      if (ESTATICO) {
        $("enlace-docs").href = BASE_API + "api/openapi.json";
        $("enlace-docs").textContent = "Contrato de la API (OpenAPI, JSON)";
        $("version-api").textContent = `Versión ${salud.version}`;
        $("guardar-json").hidden = true;                 // sin servidor no hay dónde guardar
        enlazarRepositorio();
      }
      actualizarDescarga();
      $("guardar-json").disabled = false;
      if (salud.archivo_salida) $("exportar-estado").textContent = `Último archivo en el servidor: ${rutaCorta(salud.archivo_salida)}`;
      conexion("ok", TEXTO_CONECTADO);
      await cargar(true);
      cargarAlertas();
      cargarModelos();
    } catch (e) {
      mostrarError(e);
    }
  }

  function llenarSelectores() {
    const selS = $("sel-serie");
    selS.replaceChildren(...estado.series.map((s) => el("option", { value: s.id, texto: s.principal ? `${s.id} (principal)` : s.id })));
    selS.value = estado.serie;
    const selM = $("sel-modelo");
    selM.replaceChildren(...estado.salud.modelos.map((m) => el("option", {
      value: m.nombre,
      texto: m.nombre === estado.salud.modelo_recomendado ? `${m.nombre} (recomendado)` : m.nombre,
    })));
    selM.value = estado.modelo;
    selS.disabled = selM.disabled = false;
    describirSerie();
  }

  function describirSerie() {
    const s = estado.series.find((x) => x.id === estado.serie);
    $("descripcion-serie").textContent = s ? s.descripcion : "";
  }

  const rutaCorta = (r) => r.split(/[\\/]/).slice(-2).join("/");   // carpeta y archivo

  function actualizarDescarga() {
    const a = $("descargar-json");
    a.href = ruta(`reporte?modelo=${enc(estado.modelo)}&descargar=true`);
    if (ESTATICO) a.setAttribute("download", `pronostico_${slug(estado.modelo)}_${estado.salud.datos.hasta}.json`);
  }

  function enlazarRepositorio() {
    // En usuario.github.io/repositorio/ el código vive en github.com/usuario/repositorio
    if (!location.hostname.endsWith(".github.io")) return;
    const usuario = location.hostname.split(".")[0];
    const repo = location.pathname.split("/").filter(Boolean)[0];
    const a = $("enlace-repo");
    a.href = repo ? `https://github.com/${usuario}/${repo}` : `https://github.com/${usuario}`;
    a.hidden = false;
  }

  async function guardarJSON() {
    const boton = $("guardar-json"), aviso = $("exportar-estado");
    boton.disabled = true;
    aviso.textContent = "Guardando…";
    try {
      const r = await pedir(`reporte?modelo=${enc(estado.modelo)}`, { method: "POST" });
      aviso.textContent = `Guardado en ${rutaCorta(r.archivo)} (${Math.max(1, Math.round(r.bytes / 1024))} KB, ${r.series} series).`;
    } catch (e) {
      aviso.textContent = `No se pudo guardar: ${e.message}`;
    } finally {
      boton.disabled = false;
    }
  }

  function recordarSeleccion() {
    const p = new URLSearchParams(location.search);
    p.set("serie", estado.serie);
    p.set("modelo", estado.modelo);
    history.replaceState(null, "", `${location.pathname}?${p}`);
  }

  async function cargar(primera = false) {
    const main = $("principal");
    main.dataset.estado = primera ? "cargando" : "recargando";
    const serie = estado.serie, modelo = estado.modelo, h = estado.salud.horizonte_maximo;
    try {
      const [p, hist] = await Promise.all([
        pedir(`pronostico?serie=${enc(serie)}&modelo=${enc(modelo)}&horizonte=${h}`),
        pedir(`series/${enc(serie)}/historia?dias=30`),
      ]);
      if (serie !== estado.serie || modelo !== estado.modelo) return;   // llegó tarde: ya cambió la selección
      estado.pronostico = p;
      estado.historia = hist.puntos;
      pintarAlerta();
      pintarGrafica();
      pintarResumen();
      pintarTabla();
      pintarHistoria();
      await cargarMetricas(serie, modelo);
      if (serie === estado.serie && modelo === estado.modelo) main.dataset.estado = "listo";
      conexion("ok", TEXTO_CONECTADO);
    } catch (e) {
      mostrarError(e);
    }
  }

  async function cargarMetricas(serie, modelo) {
    clearTimeout(reintentoMetricas);
    try {
      const m = await pedir(`metricas?modelo=${enc(modelo)}&serie=${enc(serie)}`);
      if (serie !== estado.serie || modelo !== estado.modelo) return;
      estado.metricas = m;
      pintarConfianza();
      pintarMonitoreo();
      pintarReglas();
    } catch (e) {
      if (e.estado !== 503) throw e;
      estado.metricas = null;
      $("umbrales-nota").textContent = `${e.message}`;
      $("umbrales").replaceChildren();
      $("monitoreo").replaceChildren();
      $("tabla-reglas").tBodies[0].replaceChildren();
      reintentoMetricas = setTimeout(() => cargarMetricas(estado.serie, estado.modelo).catch(mostrarError),
        (e.reintentar || 30) * 1000);
    }
  }

  async function cargarAlertas() {
    const modelo = estado.modelo;
    try {
      const filas = await pedir(`alertas?modelo=${enc(modelo)}`);
      if (modelo === estado.modelo) pintarSeries(filas);
    } catch (e) { mostrarError(e); }
  }

  async function cargarModelos() {
    try { pintarModelos(await pedir("modelos")); } catch (e) { mostrarError(e); }
  }

  // ------------------------------------------------------------------ alerta y resumen
  function diaMasRiesgoso(dias) {
    return dias.reduce((a, b) => (b.prob_deficit > a.prob_deficit ? b : a), dias[0]);
  }

  function pintarAlerta() {
    const p = estado.pronostico, a = p.alerta_deficit, n = p.dias.length;
    const peor = diaMasRiesgoso(p.dias);
    const caja = $("alerta");
    caja.dataset.nivel = a.nivel;
    caja.querySelector(".alerta__icono").replaceChildren(icono(a.nivel));
    const titulos = {
      ninguna: "Sin riesgo de déficit en el horizonte",
      vigilancia: "Vigilancia: el escenario pesimista cae bajo cero",
      media: "Riesgo moderado de déficit",
      alta: "Déficit probable",
    };
    const detalles = {
      ninguna: `El P10 se mantiene arriba de cero los ${n} días; la probabilidad de déficit no pasa de ${pct(a.probabilidad_maxima)}.`,
      vigilancia: `El P10 queda bajo cero en ${a.dias_en_alerta} de ${n} días. La probabilidad más alta de déficit es ${pct(peor.prob_deficit)}, el ${diaCorto(peor.fecha)}.`,
      media: `La probabilidad de déficit llega a ${pct(peor.prob_deficit)} el ${diaCorto(peor.fecha)}, arriba del 25 % que marca el riesgo moderado.`,
      alta: `El P50 del ${diaCorto(peor.fecha)} queda bajo cero: es más probable cerrar ese día en negativo que en positivo.`,
    };
    $("alerta-titulo").textContent = `${titulos[a.nivel]} (${p.serie})`;
    $("alerta-detalle").textContent = detalles[a.nivel];
    $("colchon").textContent = a.colchon_sugerido > 0 ? `${millones(a.colchon_sugerido)} M MXN` : "No hace falta";
  }

  function pintarResumen() {
    const p = estado.pronostico, ac = p.acumulado, dias = p.dias;
    $("acum-etiqueta").textContent = `Flujo esperado del ${diaCorto(dias[0].fecha)} al ${diaCorto(dias[dias.length - 1].fecha)}`;
    const valor = $("acum-p50");
    valor.replaceChildren(document.createTextNode(millones(ac.p50, 1)), el("small", { texto: "M MXN" }));
    $("acum-rango").textContent = `Entre ${millones(ac.p10, 1)} y ${millones(ac.p90, 1)} M (P10 a P90), suponiendo días independientes.`;
    $("acum-prob").textContent = pct(ac.prob_deficit, 1);
    const peor = diaMasRiesgoso(dias);
    $("dia-riesgo").textContent = `${diaCorto(peor.fecha)}, ${pct(peor.prob_deficit)}`;
    const c = p.confianza;
    const dd = $("confianza-nivel");
    if (c.disponible) {
      dd.replaceChildren(icono(ICONO_CONFIANZA[c.nivel]),
        `${NOMBRE_CONFIANZA[c.nivel]}: cumple ${c.umbrales_cumplidos} de ${c.umbrales_total} umbrales`);
      $("mae-serie").textContent = `${millones(c.mae_serie)} M por día (rMAE ${dec(c.rmae_serie)})`;
    } else {
      dd.replaceChildren(icono("nd"), `Backtest ${c.estado_backtest}`);
      $("mae-serie").textContent = "—";
    }
  }

  function pintarTabla() {
    const dias = estado.pronostico.dias;
    $("tabla-dias").tBodies[0].replaceChildren(...dias.map((d) => el("tr", {},
      el("td", { texto: diaCorto(d.fecha) }),
      el("td", { class: "num", texto: millones(d.p10) }),
      el("td", { class: "num", texto: millones(d.p50) }),
      el("td", { class: "num", texto: millones(d.p90) }),
      el("td", { class: "num", texto: pct(d.prob_deficit) }),
      el("td", {}, etiquetaNivel(d.alerta.nivel)),
      el("td", { class: "num", texto: d.confianza ? millones(d.confianza.mae) : "—" }),
      el("td", { class: "num", texto: d.confianza ? pctDirecto(d.confianza.cobertura) : "—" }))));
  }

  function pintarHistoria() {
    $("tabla-historia").tBodies[0].replaceChildren(...[...estado.historia].reverse().map((p) => el("tr", {},
      el("td", { texto: diaCorto(p.fecha) }), el("td", { class: "num", texto: millones(p.valor) }))));
  }

  // ------------------------------------------------------------------ gráfica principal
  function pasoLimpio(rango, objetivo = 5) {
    const bruto = rango / objetivo;
    const mag = Math.pow(10, Math.floor(Math.log10(bruto)));
    const r = bruto / mag;
    return (r <= 1 ? 1 : r <= 2 ? 2 : r <= 2.5 ? 2.5 : r <= 5 ? 5 : 10) * mag;
  }

  function pintarGrafica() {
    const cont = $("grafica");
    const W = Math.max(300, cont.clientWidth || 800);
    const angosta = W < 560;
    const hist = angosta ? estado.historia.slice(-12) : estado.historia, dias = estado.pronostico.dias;
    if (!hist.length || !dias.length) { cont.replaceChildren(); return; }
    $("grafica-nota").textContent = `Últimos ${hist.length} días hábiles observados y los ${dias.length} días pronosticados.`;
    const H = angosta ? 300 : 360;
    const m = { izq: angosta ? 40 : 52, der: angosta ? 62 : 78, arr: 26, aba: 34 };
    const n = hist.length + dias.length;
    const banda = (W - m.izq - m.der) / n;
    const x = (i) => m.izq + (i + 0.5) * banda;

    const valores = [...hist.map((p) => p.valor), ...dias.flatMap((d) => [d.p10, d.p90]), 0].map((v) => v / 1e6);
    let lo = Math.min(...valores), hi = Math.max(...valores);
    const paso = pasoLimpio(hi - lo);
    lo = Math.floor(lo / paso) * paso;
    hi = Math.ceil(hi / paso) * paso;
    const y = (v) => m.arr + (hi - v / 1e6) / (hi - lo) * (H - m.arr - m.aba);
    const y0 = y(0);

    const raiz = svg("svg", { viewBox: `0 0 ${W} ${H}`, tabindex: "0", "aria-label": "Gráfica de flujo real y pronosticado. Usa las flechas para recorrer los días." });
    const defs = svg("defs");
    const idClip = "bajo-cero";
    const clip = svg("clipPath", { id: idClip });
    clip.append(svg("rect", { x: 0, y: y0, width: W, height: Math.max(0, H - m.aba - y0) }));
    defs.append(clip);
    raiz.append(defs);

    // zona de déficit y rejilla
    raiz.append(svg("rect", { x: m.izq, y: y0, width: W - m.izq - m.der, height: Math.max(0, H - m.aba - y0), fill: "var(--zona-deficit)" }));
    for (let v = lo; v <= hi + 1e-9; v += paso) {
      const yy = m.arr + (hi - v) / (hi - lo) * (H - m.arr - m.aba);
      if (Math.abs(v) > 1e-9) raiz.append(svg("line", { x1: m.izq, x2: W - m.der, y1: yy, y2: yy, class: "rejilla" }));
      const t = svg("text", { x: m.izq - 8, y: yy + 4, "text-anchor": "end" });
      t.textContent = Math.abs(v) < 1e-9 ? "0" : signo(v) + num1.format(Math.abs(v)).replace(/,0$/, "").replace(/\.0$/, "");
      raiz.append(t);
    }
    if (lo < 0) {
      const t = svg("text", { x: m.izq + 8, y: H - m.aba - 8, class: "etiqueta-zona" });
      t.textContent = "Déficit";
      raiz.append(t);
    }

    // división entre lo observado y lo pronosticado
    const xDiv = m.izq + hist.length * banda;
    raiz.append(svg("line", { x1: xDiv, x2: xDiv, y1: m.arr - 14, y2: H - m.aba, class: "divisor" }));
    const tDiv = svg("text", { x: xDiv + 6, y: m.arr - 6 });
    tDiv.textContent = angosta ? "Pronóstico" : `Pronóstico desde el ${ddmm(dias[0].fecha)}`;
    const tObs = svg("text", { x: xDiv - 6, y: m.arr - 6, "text-anchor": "end" });
    tObs.textContent = angosta ? "Real" : `Último dato ${ddmm(hist[hist.length - 1].fecha)}`;
    raiz.append(tObs, tDiv);

    // columnas de lo observado (extremo redondeado lejos de la base)
    const ancho = Math.min(24, banda * 0.62), r = Math.min(4, ancho / 2);
    hist.forEach((p, i) => {
      const yv = y(p.valor), x0 = x(i) - ancho / 2, alto = Math.abs(yv - y0);
      if (alto < 0.5) return;
      const rr = Math.min(r, alto);
      const d = p.valor >= 0
        ? `M${x0},${y0} V${yv + rr} Q${x0},${yv} ${x0 + rr},${yv} H${x0 + ancho - rr} Q${x0 + ancho},${yv} ${x0 + ancho},${yv + rr} V${y0} Z`
        : `M${x0},${y0} V${yv - rr} Q${x0},${yv} ${x0 + rr},${yv} H${x0 + ancho - rr} Q${x0 + ancho},${yv} ${x0 + ancho},${yv - rr} V${y0} Z`;
      raiz.append(svg("path", { d, fill: "var(--real)" }));
    });

    // abanico P10–P90 desde el último dato observado
    const iUlt = hist.length - 1, ult = hist[iUlt].valor;
    const puntosArriba = [[x(iUlt), y(ult)], ...dias.map((d, k) => [x(hist.length + k), y(d.p90)])];
    const puntosAbajo = [...dias.map((d, k) => [x(hist.length + k), y(d.p10)])].reverse();
    const poligono = [...puntosArriba, ...puntosAbajo].map((q) => q.join(",")).join(" ");
    raiz.append(svg("polygon", { points: poligono, fill: "var(--banda)" }));
    raiz.append(svg("polygon", { points: poligono, fill: "var(--banda-deficit)", "clip-path": `url(#${idClip})` }));
    const linea = (pts, extra) => svg("polyline", { points: pts.map((q) => q.join(",")).join(" "), fill: "none", ...extra });
    raiz.append(linea([[x(iUlt), y(ult)], ...dias.map((d, k) => [x(hist.length + k), y(d.p90)])],
      { stroke: "var(--serie)", "stroke-width": 1, "stroke-opacity": 0.45 }));
    raiz.append(linea([[x(iUlt), y(ult)], ...dias.map((d, k) => [x(hist.length + k), y(d.p10)])],
      { stroke: "var(--serie)", "stroke-width": 1, "stroke-opacity": 0.45 }));
    raiz.append(linea([[x(iUlt), y(ult)], ...dias.map((d, k) => [x(hist.length + k), y(d.p50)])],
      { stroke: "var(--serie)", "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }));

    // línea de flotación (cero)
    raiz.append(svg("line", { x1: m.izq, x2: W - m.der, y1: y0, y2: y0, class: "eje-cero" }));

    dias.forEach((d, k) => raiz.append(svg("circle", {
      cx: x(hist.length + k), cy: y(d.p50), r: 4.5, fill: "var(--serie)", stroke: "var(--superficie)", "stroke-width": 2,
    })));

    // etiquetas directas al final: P90, P50 y P10 del último día
    const fin = dias[dias.length - 1], xFin = x(n - 1) + 10;
    const etiquetas = [["P90", fin.p90], ["P50", fin.p50], ["P10", fin.p10]].map(([e, v]) => ({ e, v, yy: y(v) + 4 }));
    for (let i = 1; i < etiquetas.length; i++) {
      if (etiquetas[i].yy - etiquetas[i - 1].yy < 15) etiquetas[i].yy = etiquetas[i - 1].yy + 15;
    }
    for (const et of etiquetas) {
      const t = svg("text", { x: xFin, y: et.yy, class: "etiqueta-directa" });
      t.textContent = `${et.e} ${millones(et.v, 1)}`;
      raiz.append(t);
    }

    // eje x
    const fechas = [...hist.map((p) => p.fecha), ...dias.map((d) => d.fecha)];
    const cada = Math.max(1, Math.ceil(44 / banda));
    fechas.forEach((f, i) => {
      if ((n - 1 - i) % cada !== 0) return;
      const t = svg("text", { x: x(i), y: H - m.aba + 18, "text-anchor": "middle" });
      t.textContent = ddmm(f);
      raiz.append(t);
    });

    // capa de lectura: cruz que salta al día más cercano + tooltip
    const cruz = svg("line", { y1: m.arr, y2: H - m.aba, class: "cruz", visibility: "hidden" });
    const zona = svg("rect", { x: m.izq, y: m.arr, width: W - m.izq - m.der, height: H - m.arr - m.aba, fill: "transparent" });
    raiz.append(cruz, zona);
    let actual = null;
    const mostrar = (i) => {
      actual = Math.max(0, Math.min(n - 1, i));
      cruz.setAttribute("x1", x(actual)); cruz.setAttribute("x2", x(actual));
      cruz.setAttribute("visibility", "visible");
      tooltip(actual, x(actual) / W);
    };
    const ocultar = () => { cruz.setAttribute("visibility", "hidden"); $("tooltip").hidden = true; actual = null; };
    zona.addEventListener("pointermove", (ev) => {
      const caja = raiz.getBoundingClientRect();
      const px = (ev.clientX - caja.left) * (W / caja.width);
      mostrar(Math.floor((px - m.izq) / banda));
    });
    zona.addEventListener("pointerleave", ocultar);
    raiz.addEventListener("keydown", (ev) => {
      if (ev.key === "ArrowRight" || ev.key === "ArrowLeft") {
        ev.preventDefault();
        mostrar((actual ?? hist.length - 1) + (ev.key === "ArrowRight" ? 1 : -1));
      } else if (ev.key === "Escape") ocultar();
    });
    raiz.addEventListener("focus", () => mostrar(hist.length));
    raiz.addEventListener("blur", ocultar);

    function tooltip(i, fraccion) {
      const tip = $("tooltip");
      const filas = [];
      if (i < hist.length) {
        const p = hist[i];
        tip.replaceChildren(el("div", { class: "tooltip__fecha", texto: `${diaCorto(p.fecha)}, observado` }),
          el("div", { class: "tooltip__fila" }, el("span", { class: "tooltip__clave tooltip__clave--real" }),
            el("strong", { texto: `${millones(p.valor)} M` }), el("span", { texto: "real" })));
      } else {
        const d = dias[i - hist.length];
        filas.push(el("div", { class: "tooltip__fila" }, el("span", { class: "tooltip__clave" }), el("strong", { texto: `${millones(d.p50)} M` }), el("span", { texto: "P50" })));
        filas.push(el("div", { class: "tooltip__fila" }, el("span", { class: "tooltip__clave tooltip__clave--banda" }),
          el("strong", { texto: `${millones(d.p10)} a ${millones(d.p90)} M` }), el("span", { texto: "P10 a P90" })));
        filas.push(el("div", { class: "tooltip__fila" }, el("strong", { texto: pct(d.prob_deficit) }), el("span", { texto: "probabilidad de déficit" })));
        filas.push(el("div", { class: "tooltip__fila" }, etiquetaNivel(d.alerta.nivel)));
        tip.replaceChildren(el("div", { class: "tooltip__fecha", texto: `${diaCorto(d.fecha)}, pronóstico a ${d.h} día${d.h > 1 ? "s" : ""}` }), ...filas);
      }
      tip.hidden = false;
      const lamina = cont.parentElement.getBoundingClientRect();
      const caja = raiz.getBoundingClientRect();
      const px = caja.left - lamina.left + fraccion * caja.width;
      const ancho = tip.offsetWidth;
      tip.style.left = `${Math.min(Math.max(8, px + (fraccion > 0.6 ? -ancho - 14 : 14)), lamina.width - ancho - 8)}px`;
      tip.style.top = `${caja.top - lamina.top + 40}px`;
    }

    cont.replaceChildren(raiz);
  }

  // ------------------------------------------------------------------ confianza, monitoreo y reglas
  function formatoUmbral(u) {
    if (u.valor == null) return "sin dato";
    switch (u.clave) {
      case "rmae": case "f1_direccion": return dec(u.valor);
      case "wql": return dec(u.valor, 3);
      case "cobertura": return pctDirecto(u.valor);
      case "sesgo_cambio": return `${u.valor > 0 ? "+" : ""}${dec(u.valor)} M/día`;
      case "latencia": return `${dec(u.valor)} s`;
      default: return dec(u.valor);
    }
  }
  function textoCondicion(u) {
    return `Aceptable si ${u.condicion.replace("<=", "es menor o igual a").replace(">=", "es mayor o igual a").replace("entre", "está entre")}${u.unidad ? " " + u.unidad : ""}`;
  }

  function pintarConfianza() {
    const m = estado.metricas;
    const [ini, fin] = m.periodo_backtest;
    $("umbrales-nota").textContent = `Umbrales de aceptación del reporte técnico, medidos en el backtest de todas las series ` +
      `(${m.origenes} orígenes, del ${fechaLarga(ini)} al ${fechaLarga(fin)}). Confianza ${NOMBRE_CONFIANZA[m.nivel_confianza].toLowerCase()}: ` +
      `${m.fallas === 0 ? "cumple todos" : `no cumple ${m.fallas}`} y ${m.alarmas === 0 ? "sin avisos de monitoreo" : `${m.alarmas} aviso(s) de monitoreo`}.`;
    $("umbrales").replaceChildren(...m.umbrales.map((u) => el("li", {},
      icono(u.cumple == null ? "nd" : u.cumple ? "si" : "no"),
      el("span", { texto: u.nombre + (u.clave === "sesgo_cambio" && m.fecha_cambio_temporada ? ` (desde el ${fechaLarga(m.fecha_cambio_temporada)})` : "") }),
      el("span", { class: "valor", texto: formatoUmbral(u) }),
      el("span", { class: "condicion", texto: textoCondicion(u) }))));
  }

  function minigrafica(puntos, opciones) {
    const W = 520, H = 92, m = { izq: 34, der: 8, arr: 8, aba: 18 };
    const vals = puntos.map((p) => p.valor);
    const lo = Math.min(opciones.min, ...vals), hi = Math.max(opciones.max, ...vals);
    const x = (i) => m.izq + i / Math.max(1, puntos.length - 1) * (W - m.izq - m.der);
    const y = (v) => m.arr + (hi - v) / (hi - lo) * (H - m.arr - m.aba);
    const raiz = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", class: "mini", "aria-label": opciones.descripcion });
    if (opciones.rango) {
      raiz.append(svg("rect", { x: m.izq, width: W - m.izq - m.der, y: y(opciones.rango[1]), height: y(opciones.rango[0]) - y(opciones.rango[1]), class: "rango" }));
    }
    if (opciones.limite != null) {
      raiz.append(svg("line", { x1: m.izq, x2: W - m.der, y1: y(opciones.limite), y2: y(opciones.limite), class: "limite" }));
    }
    for (const v of opciones.marcas) {
      const t = svg("text", { x: m.izq - 6, y: y(v) + 4, "text-anchor": "end" });
      t.textContent = opciones.fmt(v);
      raiz.append(t);
    }
    raiz.append(svg("polyline", { points: puntos.map((p, i) => `${x(i)},${y(p.valor)}`).join(" "), class: "linea" }));
    const ti = svg("text", { x: m.izq, y: H - 3 }); ti.textContent = ddmm(puntos[0].fecha);
    const tf = svg("text", { x: W - m.der, y: H - 3, "text-anchor": "end" }); tf.textContent = ddmm(puntos[puntos.length - 1].fecha);
    raiz.append(ti, tf);
    const ultimo = puntos.length - 1;
    raiz.append(svg("circle", { cx: x(ultimo), cy: y(puntos[ultimo].valor), r: 4, fill: "var(--serie)", stroke: "var(--superficie)", "stroke-width": 2 }));
    const cruz = svg("line", { y1: m.arr, y2: H - m.aba, class: "cruz", visibility: "hidden" });
    const zona = svg("rect", { x: m.izq, y: 0, width: W - m.izq - m.der, height: H, fill: "transparent" });
    raiz.append(cruz, zona);
    zona.addEventListener("pointermove", (ev) => {
      const caja = raiz.getBoundingClientRect();
      const px = (ev.clientX - caja.left) * (W / caja.width);
      const i = Math.max(0, Math.min(ultimo, Math.round((px - m.izq) / (W - m.izq - m.der) * ultimo)));
      cruz.setAttribute("x1", x(i)); cruz.setAttribute("x2", x(i)); cruz.setAttribute("visibility", "visible");
      opciones.lectura.textContent = `${opciones.fmt(puntos[i].valor)} el ${ddmm(puntos[i].fecha)}`;
    });
    zona.addEventListener("pointerleave", () => {
      cruz.setAttribute("visibility", "hidden");
      opciones.lectura.textContent = `${opciones.fmt(puntos[ultimo].valor)} al ${ddmm(puntos[ultimo].fecha)}`;
    });
    return raiz;
  }

  function monitor(titulo, valorTexto, alerta, nota, grafica) {
    const lectura = el("span", { class: "monitor__valor", texto: valorTexto });
    const caja = el("div", { class: "monitor" },
      el("div", { class: "monitor__cabeza" }, el("span", { class: "nivel" }, icono(alerta ? "no" : "si"), titulo), lectura),
      el("p", { class: "monitor__nota", texto: nota }));
    if (grafica) caja.append(grafica(lectura));
    return caja;
  }

  function pintarMonitoreo() {
    const mo = estado.metricas.monitoreo;
    const r = mo.rmae_movil, c = mo.cobertura_movil, d = mo.duplicados;
    const ultR = r.serie.length ? r.serie[r.serie.length - 1] : null;
    const ultC = c.serie.length ? c.serie[c.serie.length - 1] : null;
    $("monitoreo").replaceChildren(
      monitor(`rMAE móvil de ${r.ventana_dias} días`, ultR ? `${dec(ultR.valor)} al ${ddmm(ultR.fecha)}` : "—", r.alerta,
        r.alerta ? `Pasó de ${dec(r.limite)} durante ${r.dias_seguidos} días seguidos. ${r.accion}.`
          : `Avisa si pasa de ${dec(r.limite)} durante ${r.dias_seguidos} días hábiles seguidos (línea roja).`,
        r.serie.length > 1 ? (lectura) => minigrafica(r.serie, {
          min: 0.4, max: 1.1, limite: r.limite, marcas: [0.5, r.limite], fmt: (v) => dec(v), lectura,
          descripcion: `rMAE móvil de ${r.ventana_dias} días, último valor ${dec(r.valor)}`,
        }) : null),
      monitor(`Cobertura móvil de ${c.ventana_dias} días`, ultC ? `${pctDirecto(ultC.valor)} al ${ddmm(ultC.fecha)}` : "—", c.alerta,
        c.alerta ? `Salió del rango de ${c.rango[0]} a ${c.rango[1]} %. ${c.accion}.`
          : `Avisa si sale del rango de ${c.rango[0]} a ${c.rango[1]} % (franja verde).`,
        c.serie.length > 1 ? (lectura) => minigrafica(c.serie, {
          min: 55, max: 100, rango: c.rango, marcas: [c.rango[0], c.rango[1]], fmt: (v) => `${Math.round(v)} %`, lectura,
          descripcion: `Cobertura móvil de ${c.ventana_dias} días, último valor ${pctDirecto(c.valor)}`,
        }) : null),
      monitor("Duplicados en el export", String(d.valor), d.alerta,
        d.alerta ? `${d.accion}.` : "Ninguna partida repetida al cargar el Excel.", null),
    );
  }

  function pintarReglas() {
    const reglas = estado.metricas.reglas_deficit;
    if (!reglas.length) return;
    const base = reglas[0];
    $("reglas-nota").textContent = `En el backtest de ${estado.serie}, ${base.eventos_reales} de ${base.pronosticos} pronósticos ` +
      `cayeron en un día con déficit (${pctDirecto(base.tasa_base_pct)}). Una regla que alerta casi todos los días encuentra ` +
      `casi todos los déficits, pero su precisión se queda cerca de esa frecuencia. La primera fila es la regla del reporte.`;
    $("tabla-reglas").tBodies[0].replaceChildren(...reglas.map((r, i) => el("tr", { class: i === 0 ? "destacada" : null },
      el("td", { texto: r.regla }),
      el("td", { class: "num", texto: pctDirecto(r.dias_alertados_pct) }),
      el("td", { class: "num", texto: dec(r.precision) }),
      el("td", { class: "num", texto: dec(r.recall) }),
      el("td", { class: "num", texto: dec(r.f1) }))));
  }

  // ------------------------------------------------------------------ todas las series y modelos
  function pintarSeries(filas) {
    $("tabla-series").tBodies[0].replaceChildren(...filas.map((f) => {
      const boton = el("button", { type: "button", class: "enlace", "aria-label": `Ver ${f.serie}`, texto: f.serie });
      boton.addEventListener("click", () => { $("sel-serie").value = f.serie; $("sel-serie").dispatchEvent(new Event("change")); window.scrollTo({ top: 0, behavior: "smooth" }); });
      return el("tr", { class: f.serie === estado.serie ? "destacada" : null },
        el("td", {}, boton),
        el("td", {}, etiquetaNivel(f.nivel)),
        el("td", { class: "num", texto: `${millones(f.peor_p10)} M` }),
        el("td", { class: "num", texto: pct(f.probabilidad_maxima) }));
    }));
  }

  function pintarModelos(filas) {
    $("tabla-modelos").tBodies[0].replaceChildren(...filas.map((f) => el("tr", { class: f.modelo === estado.modelo ? "destacada" : null },
      el("td", { texto: f.recomendado ? `${f.modelo} (recomendado)` : f.modelo }),
      el("td", { class: "num", texto: f.mae == null ? f.estado_backtest : `${millones(f.mae)} M` }),
      el("td", { class: "num", texto: dec(f.rmae) }),
      el("td", { class: "num", texto: pctDirecto(f.cobertura) }),
      el("td", { class: "num", texto: f.umbrales_total ? `${f.umbrales_cumplidos} de ${f.umbrales_total}` : "—" }),
      el("td", { class: "num", texto: f.latencia_ms == null ? "—" : f.latencia_ms >= 1000 ? `${dec(f.latencia_ms / 1000, 1)} s` : `${Math.round(f.latencia_ms)} ms` }))));
    const rec = filas.find((f) => f.recomendado);
    const reemplazo = filas.filter((f) => f.reemplaza_al_recomendado);
    $("modelos-nota").textContent = reemplazo.length
      ? `${reemplazo.map((f) => f.modelo).join(", ")} reduce el MAE de ${rec.modelo} en 5 % o más y cumple todos los umbrales: conviene evaluarlo como reemplazo.`
      : `Ningún modelo alternativo reduce el MAE de ${rec ? rec.modelo : "el recomendado"} en 5 % o más cumpliendo todos los umbrales, así que sigue como recomendado. MAE en millones de MXN por día; latencia del pronóstico de todas las series.`;
  }

  // ------------------------------------------------------------------ eventos
  $("sel-serie").addEventListener("change", (e) => {
    estado.serie = e.target.value;
    describirSerie();
    recordarSeleccion();
    cargar().then(cargarAlertas);
  });
  $("sel-modelo").addEventListener("change", (e) => {
    estado.modelo = e.target.value;
    recordarSeleccion();
    actualizarDescarga();
    $("exportar-estado").textContent = "";
    cargar().then(() => { cargarAlertas(); cargarModelos(); });
  });
  $("reintentar").addEventListener("click", iniciar);
  $("guardar-json").addEventListener("click", guardarJSON);
  $("filtros").addEventListener("submit", (e) => e.preventDefault());

  let pendiente = null;
  new ResizeObserver(() => {
    clearTimeout(pendiente);
    pendiente = setTimeout(() => { if (estado.pronostico) pintarGrafica(); }, 120);
  }).observe($("grafica"));

  iniciar();
})();
