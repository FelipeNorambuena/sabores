"""Dashboard de observabilidad del agente Sabores (Streamlit + Plotly).

Ejecutar:  streamlit run dashboard/app.py
Lee logs/trazas.jsonl y logs/resultados.jsonl, que generan el agente y scripts/ejecutar_escenarios.py.
"""
import os
import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from sabores.observabilidad import metricas as m  # noqa: E402

# Paleta categórica validada con validate_palette.js (modo claro, pares adyacentes). Un color por entidad, en orden fijo.
AZUL, NARANJA, AQUA, AMARILLO, GRIS = "#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#9a9892"
TEXTO, TEXTO_2, GRILLA, SUPERFICIE = "#0b0b0b", "#52514e", "#e6e5e0", "#fcfcfb"
BUENO, ALERTA, CRITICO = "#0ca30c", "#fab219", "#d03b3b"
COLOR_ETIQUETA = {"baseline": AZUL, "optimizado": NARANJA}

# Umbrales definidos por el equipo para un asistente conversacional de restaurante (se justifican en el informe)
UMBRALES = {"seguridad": (1.0, 1.0), "cumplimiento": (0.9, 0.7), "consistencia": (0.8, 0.5),
            "errores": (0.05, 0.2), "p95": (15.0, 45.0)}

st.set_page_config(page_title="Observabilidad · Agente Sabores", page_icon="📊", layout="wide")


def semaforo(valor, bueno, aceptable, mayor_es_mejor=True) -> str:
    """Estado con ícono y texto (nunca solo color)."""
    if pd.isna(valor):
        return "⚪ sin datos"
    ok = valor >= bueno if mayor_es_mejor else valor <= bueno
    regular = valor >= aceptable if mayor_es_mejor else valor <= aceptable
    return "✅ cumple" if ok else ("⚠️ atención" if regular else "❌ crítico")


def estilo(fig, alto=380, titulo=None):
    fig.update_layout(title=dict(text=titulo, font=dict(size=15, color=TEXTO)) if titulo else None, height=alto,
                      paper_bgcolor=SUPERFICIE, plot_bgcolor=SUPERFICIE, font=dict(color=TEXTO_2, size=12),
                      margin=dict(l=10, r=10, t=50 if titulo else 20, b=110),
                      legend=dict(orientation="h", yanchor="top", y=-0.4, x=0), hoverlabel=dict(bgcolor="white"))
    fig.update_xaxes(gridcolor=GRILLA, zeroline=False, linecolor=GRILLA)
    fig.update_yaxes(gridcolor=GRILLA, zeroline=False, linecolor=GRILLA)
    return fig


def color_de(etiqueta: str, orden: list[str]) -> str:
    return COLOR_ETIQUETA.get(etiqueta, [AQUA, AMARILLO][max(len(COLOR_ETIQUETA) - 1, 0) % 2])


# ------------------------------------------------------------------ datos y filtros
st.title("Observabilidad del agente Sabores")
st.caption("EV3 · ISY0101 · métricas, trazas y logs del agente de recomendación de platos")

with st.sidebar:
    st.header("Datos")
    # SABORES_LOGS_DIR permite abrir un segundo dashboard sobre otra carpeta (p. ej. una demo limpia)
    logs_dir = st.text_input("Carpeta de logs", os.getenv("SABORES_LOGS_DIR") or str(Path(__file__).resolve().parents[1] / "logs"))
    if st.button("Recargar"):
        st.rerun()
# Sin caché: cada recarga vuelve a leer logs/, por eso una corrida nueva aparece al pulsar Recargar.
spans, resultados = m.cargar(logs_dir)
if resultados.empty and spans.empty:
    st.warning("No hay logs en esa carpeta. Ejecuta `python scripts/ejecutar_escenarios.py` y recarga.")
    st.stop()

etiquetas_disp = sorted(resultados["etiqueta"].dropna().unique()) if not resultados.empty else []
escenarios_disp = sorted(resultados["escenario"].dropna().unique()) if not resultados.empty else []
with st.sidebar:
    st.header("Filtros")
    sel_etiquetas = st.multiselect("Corrida", etiquetas_disp, default=etiquetas_disp)
    sel_escenarios = st.multiselect("Escenario", escenarios_disp, default=escenarios_disp)
# Todo lo que sigue usa solo las corridas y escenarios marcados en la barra lateral.
res = m.filtrar(resultados, sel_etiquetas, sel_escenarios)
sp = m.filtrar(spans, sel_etiquetas, sel_escenarios)
if res.empty:
    st.info("Los filtros no dejan ninguna ejecución.")
    st.stop()

seg, cumpl = m.precision_seguridad(res), m.tasa_cumplimiento(res)
cons, err = m.consistencia(res), m.frecuencia_errores(res, sp)
lat, rec = m.latencia_turnos(sp), m.uso_recursos(sp)
desg = m.desglose_tiempo(sp)

tab_resumen, tab_lat, tab_cal, tab_conv, tab_err, tab_datos = st.tabs(
    ["Resumen", "Latencia y recursos", "Calidad y consistencia", "Conversaciones", "Errores y trazas", "Datos (tabla)"])

# ------------------------------------------------------------------ resumen
with tab_resumen:
    c = st.columns(5)
    # El detalle va en la leyenda, no como "delta": st.metric dibuja una flecha que sugeriría una variación.
    c[0].metric("Precisión de seguridad", "—" if pd.isna(seg["valor"]) else f"{seg['valor']:.0%}")
    c[0].caption(f"{semaforo(seg['valor'], *UMBRALES['seguridad'])} · {seg['violaciones']} violaciones en {seg['ejecuciones']} ejecuciones")
    c[1].metric("Cumplimiento de checks", "—" if pd.isna(cumpl["valor"]) else f"{cumpl['valor']:.0%}")   # sin checks (petición libre): sin dato
    c[1].caption(f"{semaforo(cumpl['valor'], *UMBRALES['cumplimiento'])} · {cumpl['cumplidos']} de {cumpl['checks']} checks")
    cm = cons["jaccard_platos"].mean() if not cons.empty else float("nan")
    c[2].metric("Consistencia (platos)", "—" if pd.isna(cm) else f"{cm:.2f}")
    c[2].caption(f"{semaforo(cm, *UMBRALES['consistencia'])} · similitud entre repeticiones, 0 a 1")
    c[3].metric("Ejecuciones con error", f"{err['tasa_ejecuciones_con_error']:.0%}")
    c[3].caption(f"{semaforo(err['tasa_ejecuciones_con_error'], *UMBRALES['errores'], mayor_es_mejor=False)} · "
                 f"{err['ejecuciones_con_error']} de {err['ejecuciones']}")
    c[4].metric("Latencia p95 por turno", f"{lat['p95']:.0f} s")
    c[4].caption(f"{semaforo(lat['p95'], *UMBRALES['p95'], mayor_es_mejor=False)} · p50 {lat['p50']:.1f} s")

    st.subheader("Hallazgos automáticos")
    h = []
    if not desg.empty:
        h.append(f"**Cuello de botella:** {m.causa_dominante(desg)}.")
    if err["turnos"]:
        h.append(f"**Reintentos del proveedor:** {err['turnos_con_reintentos']} de {err['turnos']} turnos tuvieron al menos un "
                 f"reintento ({err['reintentos_totales']} en total, típicamente 429 por límite de tokens por minuto).")
    if rec:
        h.append(f"**Tokens:** {rec['tokens_por_turno']:,.0f} por turno; {rec['pct_razonamiento_en_salida']:.0%} de la salida es "
                 f"razonamiento interno del modelo. Se usó {rec['pct_cuota_diaria']:.0%} de la cuota diaria "
                 f"({m.LIMITE_TOKENS_DIA_GROQ:,} tokens).")
    if not cons.empty:
        j = cons.iloc[0]
        h.append(f"**Consistencia:** las herramientas usadas coinciden ({j['jaccard_herramientas']:.2f}) pero los platos "
                 f"recomendados casi no ({j['jaccard_platos']:.2f}).")
    for linea in h:
        st.markdown("- " + linea)

    if rec:
        por_corrida = (m.tokens_por_turno(sp).groupby("etiqueta", as_index=False)["tokens_total"].sum())
        fig = go.Figure(go.Bar(x=por_corrida["tokens_total"], y=por_corrida["etiqueta"], orientation="h",
                               marker_color=[color_de(e, etiquetas_disp) for e in por_corrida["etiqueta"]],
                               text=[f"{v:,.0f}" for v in por_corrida["tokens_total"]], textposition="inside",
                               insidetextanchor="end", textfont=dict(color="white", size=14),
                               hovertemplate="%{y}: %{x:,.0f} tokens<extra></extra>"))
        fig.add_vline(x=m.LIMITE_TOKENS_DIA_GROQ, line_dash="dash", line_color=CRITICO,
                      annotation_text="cuota diaria Groq (200.000)", annotation_font_color=CRITICO)
        fig.update_xaxes(title="tokens consumidos en la corrida", range=[0, max(m.LIMITE_TOKENS_DIA_GROQ * 1.25,
                                                                                por_corrida["tokens_total"].max() * 1.2)])
        st.plotly_chart(estilo(fig, 230, "Consumo de tokens frente a la cuota diaria"), use_container_width=True)

# ------------------------------------------------------------------ latencia y recursos
with tab_lat:
    t = m.turnos(sp, completos=True)
    n_caidos = len(m.turnos(sp)) - len(t)
    if n_caidos:
        st.caption(f"{n_caidos} turno(s) terminaron con error del proveedor (p. ej. cuota diaria agotada) y no se incluyen en "
                   "las latencias, porque no representan el tiempo real de respuesta; sus tokens sí cuentan en el consumo.")
    if t.empty:
        st.info("Sin trazas para los filtros elegidos.")
    else:
        t = t.assign(escenario_txt=t["escenario"].astype(str))
        fig = go.Figure()
        for etq in sorted(t["etiqueta"].dropna().unique()):
            d = t[t["etiqueta"] == etq]
            fig.add_trace(go.Scatter(x=d["duracion_s"], y=d["escenario_txt"], mode="markers", name=etq,
                                     marker=dict(size=11, color=color_de(etq, etiquetas_disp), line=dict(width=2, color=SUPERFICIE)),
                                     customdata=d[["repeticion", "a_n_llm", "a_n_reintentos"]],
                                     hovertemplate="%{y} #%{customdata[0]}<br>%{x:.1f} s · %{customdata[1]} llamadas LLM · "
                                                   "%{customdata[2]} reintentos<extra></extra>"))
        fig.update_xaxes(title="duración del turno (segundos)")
        st.plotly_chart(estilo(fig, 400, "Latencia de cada turno por escenario"), use_container_width=True)

        if not desg.empty:
            comp = [("modelo", "Modelo (inferencia + reintentos)", AZUL), ("espera_limitador", "Espera del limitador", NARANJA),
                    ("herramientas", "Herramientas", AQUA), ("recuperacion", "Recuperación (FAISS)", AMARILLO),
                    ("otros", "Otros (planificación, memoria)", GRIS)]
            d = desg.groupby("escenario", as_index=False).mean(numeric_only=True)
            fig = go.Figure()
            for col, nombre, color in comp:
                fig.add_trace(go.Bar(y=d["escenario"], x=d[col], name=nombre, orientation="h", marker_color=color,
                                     marker_line=dict(color=SUPERFICIE, width=2),
                                     text=[f"{v:.0f}s" if v >= 8 else "" for v in d[col]], textposition="inside",
                                     hovertemplate="%{y}<br>" + nombre + ": %{x:.1f} s<extra></extra>"))
            fig.update_layout(barmode="stack")
            fig.update_xaxes(title="segundos medios por turno")
            st.plotly_chart(estilo(fig, 400, "¿Dónde se va el tiempo de un turno?"), use_container_width=True)

        tk = m.tokens_por_turno(sp)
        g = tk.groupby("escenario", as_index=False)[["a_tokens_entrada", "a_tokens_salida", "a_tokens_razonamiento"]].mean()
        fig = go.Figure()
        fig.add_trace(go.Bar(y=g["escenario"], x=g["a_tokens_entrada"], name="Entrada (prompt)", orientation="h",
                             marker_color=AZUL, marker_line=dict(color=SUPERFICIE, width=2)))
        fig.add_trace(go.Bar(y=g["escenario"], x=g["a_tokens_salida"], name="Salida (incluye razonamiento)", orientation="h",
                             marker_color=NARANJA, marker_line=dict(color=SUPERFICIE, width=2)))
        fig.update_layout(barmode="stack")
        fig.update_xaxes(title="tokens medios por turno")
        st.plotly_chart(estilo(fig, 380, "Tokens por turno"), use_container_width=True)

        resumen = t.groupby("escenario").agg(turnos=("duracion_s", "size"), p50_s=("duracion_s", "median"),
                                             p95_s=("duracion_s", lambda s: s.quantile(0.95)),
                                             llamadas_llm=("a_n_llm", "mean"), reintentos=("a_n_reintentos", "sum")).round(1)
        st.dataframe(resumen, use_container_width=True)

# ------------------------------------------------------------------ calidad y consistencia
with tab_cal:
    chk = m.cumplimiento_checks(res)
    st.subheader("Checks por escenario")
    if chk.empty:
        st.info("Sin checks.")
    else:
        def _resumen(g):
            evaluables = g["ok"].dropna()
            if evaluables.empty:
                return "⚪ no evaluable"
            ok, n = int(evaluables.sum()), len(evaluables)
            extra = f" ({len(g) - n} no evaluable)" if n < len(g) else ""
            return f"{'✅' if ok == n else '❌'} {ok}/{n}{extra}"

        tabla = (chk.groupby(["escenario", "check"]).apply(_resumen, include_groups=False)
                 .rename("resultado").reset_index())
        st.dataframe(tabla, use_container_width=True, hide_index=True)
        st.caption("✅ cumple en todas las repeticiones · ❌ incumple en alguna · ⚪ no evaluable: la ejecución terminó con "
                   "error del proveedor (cuota) y no cuenta contra el agente")
    st.subheader("Consistencia entre repeticiones")
    if cons.empty:
        st.info("Se necesitan al menos 2 repeticiones evaluables de un mismo escenario.")
    else:
        fig = go.Figure()
        fig.add_trace(go.Bar(y=cons["escenario"], x=cons["jaccard_platos"], name="Platos recomendados", orientation="h",
                             marker_color=AZUL, marker_line=dict(color=SUPERFICIE, width=2),
                             text=[f"{v:.2f}" for v in cons["jaccard_platos"]], textposition="outside"))
        fig.add_trace(go.Bar(y=cons["escenario"], x=cons["jaccard_herramientas"], name="Herramientas usadas", orientation="h",
                             marker_color=NARANJA, marker_line=dict(color=SUPERFICIE, width=2),
                             text=[f"{v:.2f}" for v in cons["jaccard_herramientas"]], textposition="outside"))
        fig.update_xaxes(title="similitud de Jaccard entre repeticiones (1 = idéntico)", range=[0, 1.15])
        st.plotly_chart(estilo(fig, 260), use_container_width=True)
        base = res[res["escenario"].isin(cons["escenario"])][["escenario", "repeticion", "platos_recomendados"]]
        st.dataframe(base.assign(platos_recomendados=base["platos_recomendados"].map(", ".join)), use_container_width=True,
                     hide_index=True)

# ------------------------------------------------------------------ errores y trazas
with tab_err:
    herr = m.herramientas_resumen(sp)
    if not herr.empty:
        fig = go.Figure(go.Bar(y=herr["herramienta"], x=herr["llamadas"], orientation="h", marker_color=AZUL,
                               text=[f"{a} alertas" if a else "" for a in herr["alertas"]], textposition="outside",
                               hovertemplate="%{y}: %{x} llamadas<extra></extra>"))
        fig.update_yaxes(autorange="reversed")
        fig.update_xaxes(title="llamadas (la etiqueta indica resultados ERROR, AGOTADO, RECHAZADO o DERIVADO)")
        st.plotly_chart(estilo(fig, 360, "Uso de herramientas y alertas"), use_container_width=True)
    st.subheader("Llamadas al modelo más lentas")
    lentas = m.llamadas_llm_lentas(sp, 8)
    if not lentas.empty:
        if "a_errores_http" in lentas:
            lentas = lentas.assign(a_errores_http=lentas["a_errores_http"].map(
                lambda e: "; ".join(f"{x['status']} (retry-after {x.get('retry_after_s')}s)" for x in e) if isinstance(e, list) else ""))
        st.dataframe(lentas.round(2), use_container_width=True, hide_index=True)

    st.subheader("Explorador de trazas")
    t = m.turnos(sp)
    if not t.empty:
        t = t.sort_values("inicio")
        opciones = {f"{r['escenario']} #{r['repeticion']} · {r['duracion_s']:.0f}s · {r['inicio']:%H:%M:%S}": r["trace_id"]
                    for _, r in t.iterrows()}
        elegido = st.selectbox("Turno", list(opciones))
        arbol = m.arbol_traza(sp, opciones[elegido])
        arbol = arbol[arbol["tipo"] != "evento"]
        t0 = arbol["inicio"].min()
        arbol = arbol.assign(offset=(arbol["inicio"] - t0).dt.total_seconds())
        colores = {"turno": GRIS, "recuperacion": AMARILLO, "paso": AQUA, "llm": AZUL, "herramienta": NARANJA}
        # Posiciones numéricas: dos spans con el mismo nombre (p. ej. varias llamadas "llm") no deben fundirse en una fila
        etiquetas_y = [f"{'  ' * int(r['profundidad'])}{r['tipo']}: {r['nombre']}" for _, r in arbol.iterrows()]
        pos = list(range(len(arbol)))
        fig = go.Figure(go.Bar(y=pos, x=arbol["duracion_s"], base=arbol["offset"], orientation="h",
                               marker_color=[colores.get(x, GRIS) for x in arbol["tipo"]],
                               marker_line=dict(color=SUPERFICIE, width=1),
                               customdata=[[e.strip(), s] for e, s in zip(etiquetas_y, arbol["estado"])],
                               hovertemplate="%{customdata[0]}<br>%{x:.2f} s · estado %{customdata[1]}<extra></extra>"))
        fig.update_yaxes(autorange="reversed", tickmode="array", tickvals=pos, ticktext=etiquetas_y)
        fig.update_xaxes(title="segundos desde el inicio del turno")
        st.plotly_chart(estilo(fig, max(320, 22 * len(arbol) + 80), "Línea de tiempo del turno (span a span)"),
                        use_container_width=True)
        cols = [c for c in ["tipo", "nombre", "duracion_s", "estado", "a_tokens_entrada", "a_tokens_salida",
                            "a_espera_limitador_s", "a_reintentos", "a_etiqueta", "a_resultado", "error"] if c in arbol]
        st.dataframe(arbol[cols], use_container_width=True, hide_index=True)

# ------------------------------------------------------------------ conversaciones (evidencia de cada escenario)
with tab_conv:
    st.caption("Cada escenario tal como lo vivió el agente: lo que escribió el cliente, lo que respondió y el veredicto de los "
               "checks. Las respuestas están enmascaradas (email, RUT, teléfono).")
    from sabores.evaluacion.escenarios import ESCENARIOS  # noqa: E402  (solo para recuperar los mensajes de logs antiguos)
    catalogo = {e.nombre: e for e in ESCENARIOS}
    simbolo = {True: "✅", False: "❌", None: "⚪"}
    expandir = st.checkbox("Expandir todas las conversaciones", value=False,
                           help="Úsalo junto con el filtro de escenario de la barra lateral para capturar uno a la vez.")
    for _, r in res.sort_values(["escenario", "repeticion"]).iterrows():
        esc = catalogo.get(r["escenario"])
        mensajes = r.get("mensajes") if isinstance(r.get("mensajes"), list) else (esc.mensajes if esc else [])
        respuestas = r["respuestas"] if isinstance(r.get("respuestas"), list) else []
        desc = r.get("descripcion") if isinstance(r.get("descripcion"), str) else (esc.descripcion if esc else "")
        titulo = f"{r['escenario']} · repetición {r['repeticion'] + 1} · {r['duracion_s']:.0f} s"
        with st.expander(titulo, expanded=expandir):
            if desc:
                st.markdown(f"**Qué se prueba:** {desc}")
            sin_texto = not isinstance(r.get("respuestas"), list)   # corrida anterior a que se guardaran las respuestas
            for i, msg in enumerate(mensajes):
                with st.chat_message("user"):
                    st.write(msg)
                with st.chat_message("assistant"):
                    if sin_texto:
                        st.info("El texto de la respuesta no se registró en esta corrida (se ejecutó antes de que el runner "
                                "guardara las respuestas). Vuelve a ejecutar el escenario para verlo.")
                    elif i < len(respuestas):
                        st.write(respuestas[i])
                    else:
                        st.warning("Sin respuesta: la ejecución terminó antes (error del proveedor).")
            if isinstance(r["error"], str):
                st.error(f"Error del proveedor: {r['error'][:160]}…")
            checks_txt = " · ".join(f"{simbolo.get(v, '⚪')} {k}" for k, v in (r["checks"] or {}).items())
            if r["tiene_restricciones"]:
                checks_txt += " · " + ("❌ violó una restricción alimentaria" if r["violacion_seguridad"]
                                       else "✅ sin violaciones de seguridad")
            st.markdown("**Veredicto:** " + (checks_txt or "sin checks"))
            herr = r["secuencia_herramientas"] if isinstance(r.get("secuencia_herramientas"), list) else []
            st.caption(f"Herramientas usadas ({len(herr)}): " + (", ".join(herr) if herr else "ninguna")
                       + f" · pedidos creados: {r['pedidos']} · derivaciones al personal: {r['derivaciones']}")

# ------------------------------------------------------------------ tablas
with tab_datos:
    st.caption("Vista de tabla de los datos que alimentan los gráficos (accesibilidad y verificación).")
    st.dataframe(res.drop(columns=["checks", "respuestas"], errors="ignore"), use_container_width=True, hide_index=True)
    if "respuestas" in res:
        with st.expander("Respuestas del agente (enmascaradas)"):
            for _, r in res.iterrows():
                st.markdown(f"**{r['escenario']} #{r['repeticion']}**")
                for x in r["respuestas"] if isinstance(r["respuestas"], list) else []:
                    st.write(x)
