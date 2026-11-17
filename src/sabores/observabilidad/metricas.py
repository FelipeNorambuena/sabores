"""Cálculo de métricas de observabilidad a partir de logs/trazas.jsonl y logs/resultados.jsonl.

Es pandas puro (sin Streamlit): lo usan el dashboard y las pruebas. Cada función indica qué indicador cubre.
"""
from __future__ import annotations

import itertools
import json
from pathlib import Path

import numpy as np
import pandas as pd

LIMITE_TOKENS_DIA_GROQ = 200_000   # gpt-oss-120b, plan gratuito (error 429 TPD observado en la corrida base)


# ---------------------------------------------------------------- carga
def _leer_jsonl(ruta: Path) -> list[dict]:
    if not ruta.exists():
        return []
    return [json.loads(x) for x in ruta.read_text(encoding="utf-8").splitlines() if x.strip()]


def cargar(logs_dir: str | Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Devuelve (spans, resultados). Los atributos de cada span se aplanan a columnas `a_*`."""
    logs_dir = Path(logs_dir)
    spans = pd.DataFrame(_leer_jsonl(logs_dir / "trazas.jsonl"))
    if not spans.empty:
        attrs = pd.json_normalize(spans["atributos"].tolist()).add_prefix("a_")
        spans = pd.concat([spans.drop(columns=["atributos"]), attrs], axis=1)
        spans["inicio"] = pd.to_datetime(spans["inicio"])
        for col in ("corrida", "etiqueta", "escenario", "repeticion"):
            if col not in spans:
                spans[col] = None
    resultados = pd.DataFrame(_leer_jsonl(logs_dir / "resultados.jsonl"))
    return spans, resultados


def filtrar(df: pd.DataFrame, etiquetas: list[str] | None = None, escenarios: list[str] | None = None) -> pd.DataFrame:
    if df.empty:
        return df
    if etiquetas:
        df = df[df["etiqueta"].isin(etiquetas)]
    if escenarios:
        df = df[df["escenario"].isin(escenarios)]
    return df


def pct(serie: pd.Series, q: float) -> float:
    return float(np.percentile(serie.dropna(), q)) if len(serie.dropna()) else float("nan")


# ---------------------------------------------------------------- IE1: precisión, consistencia, errores
def ejecuciones_evaluables(res: pd.DataFrame) -> pd.DataFrame:
    """Ejecuciones que terminaron sin error del proveedor/agente (las demás no evalúan al agente)."""
    return res[res["error"].isna()] if not res.empty else res


def precision_seguridad(res: pd.DataFrame) -> dict:
    """% de ejecuciones con restricciones alimentarias sin ningún plato inseguro pedido ni recomendado."""
    ev = ejecuciones_evaluables(res)
    ev = ev[ev["tiene_restricciones"]] if not ev.empty else ev
    n = len(ev)
    violaciones = int(ev["violacion_seguridad"].sum()) if n else 0
    return {"ejecuciones": n, "violaciones": violaciones, "valor": (1 - violaciones / n) if n else float("nan")}


def cumplimiento_checks(res: pd.DataFrame) -> pd.DataFrame:
    """Una fila por (corrida, escenario, repetición, check) con resultado True/False; None = no evaluable."""
    filas = []
    for _, r in res.iterrows():
        for check, ok in (r["checks"] or {}).items():
            if pd.notna(r["error"]):   # la ejecución no terminó: no evalúa al agente (también en logs antiguos)
                ok = None
            filas.append({"etiqueta": r["etiqueta"], "corrida": r["corrida"], "escenario": r["escenario"],
                          "repeticion": r["repeticion"], "check": check, "ok": ok})
    return pd.DataFrame(filas, columns=["etiqueta", "corrida", "escenario", "repeticion", "check", "ok"])


def tasa_cumplimiento(res: pd.DataFrame) -> dict:
    c = cumplimiento_checks(res)
    c = c[c["ok"].notna()] if not c.empty else c
    n = len(c)
    return {"checks": n, "cumplidos": int(c["ok"].sum()) if n else 0, "valor": float(c["ok"].mean()) if n else float("nan")}


def _jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if (a | b) else 1.0


def consistencia(res: pd.DataFrame) -> pd.DataFrame:
    """Por escenario repetido: similitud media entre repeticiones (Jaccard) de los platos recomendados y de las
    herramientas usadas. 1 = siempre lo mismo; 0 = nunca coinciden. Solo con >= 2 repeticiones evaluables."""
    ev = ejecuciones_evaluables(res)
    filas = []
    if ev.empty:
        return pd.DataFrame(columns=["etiqueta", "escenario", "repeticiones", "jaccard_platos", "jaccard_herramientas"])
    for (etiqueta, escenario), g in ev.groupby(["etiqueta", "escenario"]):
        if len(g) < 2:
            continue
        platos = [set(x) for x in g["platos_recomendados"]]
        herr = [set(x) for x in g["secuencia_herramientas"]]
        filas.append({"etiqueta": etiqueta, "escenario": escenario, "repeticiones": len(g),
                      "jaccard_platos": float(np.mean([_jaccard(a, b) for a, b in itertools.combinations(platos, 2)])),
                      "jaccard_herramientas": float(np.mean([_jaccard(a, b)
                                                             for a, b in itertools.combinations(herr, 2)]))})
    return pd.DataFrame(filas)


def frecuencia_errores(res: pd.DataFrame, spans: pd.DataFrame) -> dict:
    """Errores del agente: ejecuciones caídas, turnos con alertas de herramientas y reintentos 429 del proveedor."""
    out = {"ejecuciones": len(res), "ejecuciones_con_error": int(res["error"].notna().sum()) if len(res) else 0}
    out["tasa_ejecuciones_con_error"] = out["ejecuciones_con_error"] / out["ejecuciones"] if out["ejecuciones"] else float("nan")
    turnos = spans[spans["tipo"] == "turno"] if not spans.empty else spans
    out["turnos"] = len(turnos)
    out["turnos_con_alerta"] = int((turnos["a_n_alertas"] > 0).sum()) if len(turnos) else 0
    out["turnos_con_reintentos"] = int((turnos["a_n_reintentos"] > 0).sum()) if len(turnos) and "a_n_reintentos" in turnos else 0
    out["reintentos_totales"] = int(turnos["a_n_reintentos"].sum()) if len(turnos) and "a_n_reintentos" in turnos else 0
    return out


# ---------------------------------------------------------------- IE2: latencia y recursos
def turnos(spans: pd.DataFrame, completos: bool = False) -> pd.DataFrame:
    """Spans de turno. completos=True descarta los que terminaron con error (no son latencias representativas)."""
    t = spans[spans["tipo"] == "turno"].copy() if not spans.empty else spans
    return t[t["estado"] != "error"] if completos and not t.empty else t


def latencia_turnos(spans: pd.DataFrame) -> dict:
    t = turnos(spans, completos=True)
    d = t["duracion_s"] if len(t) else pd.Series(dtype=float)
    return {"turnos": len(t), "p50": pct(d, 50), "p95": pct(d, 95), "max": float(d.max()) if len(d) else float("nan"),
            "media": float(d.mean()) if len(d) else float("nan")}


def desglose_tiempo(spans: pd.DataFrame, por: str = "escenario") -> pd.DataFrame:
    """Segundos medios por turno repartidos entre: modelo, espera del limitador, herramientas, recuperación y otros
    (planificación, memoria, orquestación). Identifica el cuello de botella."""
    t = turnos(spans, completos=True)
    if t.empty:
        return pd.DataFrame()
    filas = []
    for _, turno in t.iterrows():
        s = spans[spans["trace_id"] == turno["trace_id"]]
        llm = s[s["tipo"] == "llm"]
        modelo = float(llm["a_latencia_modelo_s"].sum()) if "a_latencia_modelo_s" in llm else 0.0
        espera = float(llm["a_espera_limitador_s"].sum()) if "a_espera_limitador_s" in llm else 0.0
        herr = float(s[s["tipo"] == "herramienta"]["duracion_s"].sum())
        rec = float(s[s["tipo"] == "recuperacion"]["duracion_s"].sum())
        otros = max(float(turno["duracion_s"]) - modelo - espera - herr - rec, 0.0)
        filas.append({por: turno[por], "etiqueta": turno["etiqueta"], "modelo": modelo, "espera_limitador": espera,
                      "herramientas": herr, "recuperacion": rec, "otros": otros, "total": float(turno["duracion_s"])})
    df = pd.DataFrame(filas)
    return df.groupby([por, "etiqueta"], as_index=False).mean(numeric_only=True)


def tokens_por_turno(spans: pd.DataFrame) -> pd.DataFrame:
    t = turnos(spans)
    if t.empty:
        return t
    t = t.assign(tokens_total=t["a_tokens_entrada"] + t["a_tokens_salida"])
    return t[["etiqueta", "escenario", "repeticion", "a_n_llm", "a_tokens_entrada", "a_tokens_salida",
              "a_tokens_razonamiento", "tokens_total", "duracion_s"]]


def uso_recursos(spans: pd.DataFrame) -> dict:
    tk = tokens_por_turno(spans)
    if tk.empty:
        return {}
    total = float(tk["tokens_total"].sum())
    return {"tokens_total": total, "tokens_por_turno": float(tk["tokens_total"].mean()),
            "llamadas_llm_por_turno": float(tk["a_n_llm"].mean()),
            "pct_razonamiento_en_salida": float(tk["a_tokens_razonamiento"].sum() / tk["a_tokens_salida"].sum())
            if tk["a_tokens_salida"].sum() else float("nan"),
            "pct_cuota_diaria": total / LIMITE_TOKENS_DIA_GROQ}


# ---------------------------------------------------------------- IE3: logs, errores y cuellos de botella
def herramientas_resumen(spans: pd.DataFrame) -> pd.DataFrame:
    h = spans[spans["tipo"] == "herramienta"] if not spans.empty else spans
    if h.empty:
        return pd.DataFrame(columns=["herramienta", "llamadas", "p50_s", "alertas", "tasa_alerta"])
    g = h.groupby("nombre")
    out = pd.DataFrame({"llamadas": g.size(), "p50_s": g["duracion_s"].median(),
                        "alertas": g["estado"].apply(lambda s: int((s != "ok").sum()))}).reset_index()
    out["tasa_alerta"] = out["alertas"] / out["llamadas"]
    return out.rename(columns={"nombre": "herramienta"}).sort_values("llamadas", ascending=False)


def llamadas_llm_lentas(spans: pd.DataFrame, n: int = 10) -> pd.DataFrame:
    """Las llamadas al modelo más lentas con su causa: espera del limitador o reintentos 429 del proveedor."""
    l = spans[spans["tipo"] == "llm"] if not spans.empty else spans
    if l.empty:
        return l
    cols = ["etiqueta", "escenario", "duracion_s", "a_latencia_modelo_s", "a_espera_limitador_s", "a_tokens_entrada",
            "a_reintentos", "a_errores_http"]
    cols = [c for c in cols if c in l.columns]
    return l.sort_values("duracion_s", ascending=False).head(n)[cols]


def causa_dominante(desglose: pd.DataFrame) -> str:
    """Texto con el componente que más tiempo consume (para el hallazgo de cuello de botella)."""
    if desglose.empty:
        return "sin datos"
    comp = desglose[["modelo", "espera_limitador", "herramientas", "recuperacion", "otros"]].mean()
    return f"{comp.idxmax()} ({comp.max() / comp.sum():.0%} del tiempo del turno)"


def arbol_traza(spans: pd.DataFrame, trace_id: str) -> pd.DataFrame:
    """Spans de un turno ordenados como árbol, con profundidad, para el explorador de trazas."""
    s = spans[spans["trace_id"] == trace_id].copy()
    padres = dict(zip(s["span_id"], s["parent_span_id"]))

    def profundidad(sid):
        d = 0
        while pd.notna(padres.get(sid)):
            sid, d = padres[sid], d + 1
            if d > 20:
                break
        return d

    s = s.assign(profundidad=[profundidad(x) for x in s["span_id"]]).sort_values(["inicio", "profundidad"])
    s["fin"] = s["inicio"] + pd.to_timedelta(s["duracion_s"], unit="s")
    return s
