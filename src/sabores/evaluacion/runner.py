"""Ejecuta los escenarios contra el agente real y deja dos archivos en logs/:

- trazas.jsonl:     un span por línea (lo escribe el Trazador del agente), etiquetado con corrida y escenario.
- resultados.jsonl: una línea por ejecución de escenario, con checks, violaciones de seguridad y platos recomendados.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import time
from datetime import datetime
from pathlib import Path

from ..agente import construir_agente
from ..config import Config
from ..observabilidad import enmascarar
from .escenarios import ESCENARIOS, Contexto, Escenario


def _fabrica_real(cliente: str, sesion: str, cfg: Config):
    return construir_agente(cliente, sesion, cfg)


def _preparar_indice_base(tmp: Path) -> Path:
    """Construye una sola vez el índice FAISS de la carta; cada escenario parte de una copia (ahorra embeddings)."""
    base = tmp / "base"
    construir_agente("base", cfg=Config(data_dir=base, logs_dir=tmp / "logs_base"))
    return base / "faiss"


def ejecutar_corrida(etiqueta: str = "baseline", escenarios: list[Escenario] | None = None,
                     logs_dir: Path | None = None, fabrica=None, repeticiones: int | None = None,
                     verbose: bool = True) -> list[dict]:
    escenarios = escenarios or ESCENARIOS
    logs_dir = Path(logs_dir or Config().logs_dir)
    logs_dir.mkdir(parents=True, exist_ok=True)
    corrida = f"{etiqueta}-{datetime.now():%Y%m%d-%H%M%S}"
    resultados: list[dict] = []
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        tmp = Path(t)
        # El índice de la carta se calcula una sola vez; cada ejecución parte de una copia limpia
        # (aislamiento entre escenarios y menos llamadas de embeddings).
        indice_base = _preparar_indice_base(tmp) if fabrica is None else None
        fabrica = fabrica or _fabrica_real
        for esc in escenarios:
            for rep in range(repeticiones or esc.repeticiones):
                data = tmp / f"{esc.nombre}-{rep}"
                if indice_base is not None:
                    shutil.copytree(indice_base, data / "faiss")
                cliente = f"{esc.nombre}_{rep}"
                ag = fabrica(cliente, cliente, Config(data_dir=data, logs_dir=logs_dir))
                # Etiquetas que se copian a cada span: permiten filtrar por corrida y escenario en el dashboard.
                ag.trazador.etiquetas = {"corrida": corrida, "etiqueta": etiqueta, "escenario": esc.nombre,
                                         "repeticion": rep}
                if esc.setup:
                    esc.setup(ag)
                res = _ejecutar_escenario(ag, esc, cliente, corrida, etiqueta, rep)
                resultados.append(res)
                ag.db.con.close()  # en Windows el directorio temporal no se borra con la base abierta
                # Una línea por ejecución: es la fuente de las métricas de seguridad, checks y consistencia.
                with (logs_dir / "resultados.jsonl").open("a", encoding="utf-8") as f:
                    f.write(json.dumps(res, ensure_ascii=False) + "\n")
                if verbose:
                    fallidos = [k for k, v in res["checks"].items() if not v]
                    print(f"[{esc.nombre} #{rep}] {res['duracion_s']:.0f}s  "
                          f"violacion_seguridad={res['violacion_seguridad']}  "
                          f"checks_fallidos={fallidos or '-'}  error={res['error'] or '-'}", flush=True)
    return resultados


def _ejecutar_escenario(ag, esc: Escenario, cliente: str, corrida: str, etiqueta: str, rep: int) -> dict:
    respuestas, error, herramientas, eventos = [], None, [], []
    t0 = time.perf_counter()
    for mensaje in esc.mensajes:
        try:
            respuestas.append(ag.responder(mensaje))
        except Exception as e:  # una falla del agente es un dato de la evaluación: no debe cortar la corrida
            error = f"{type(e).__name__}: {str(e)[:300]}"
            break
        for ev in ag.traza:
            eventos.append(ev["evento"])
            if ev["evento"] == "paso":
                herramientas += [ll["herramienta"] for ll in ev["llamadas"]]
    # Contexto de evaluación: respuestas, base de datos y herramientas usadas. Los checks y la seguridad se calculan
    # sobre esto con reglas en código, no pidiéndole a un modelo que juzgue la respuesta.
    ctx = Contexto(respuestas, ag.db, cliente, esc.restricciones, herramientas, eventos)
    checks = {}
    for nombre, fn in esc.checks.items():
        try:
            checks[nombre] = None if error else bool(fn(ctx))  # None = no evaluable (el agente no terminó)
        except Exception:
            checks[nombre] = None if error else False
    # Violación de seguridad = pedido o plato mencionado que es_seguro() rechaza para las restricciones reales del escenario.
    pedidos_inseguros = [n for p in ctx.pedidos for n in p["platos"] if ctx.inseguro(n)] if esc.restricciones else []
    menciones_inseguras = ([n for n in ctx.platos_mencionados() if ctx.inseguro(n)]
                           if esc.restricciones and esc.contar_menciones else [])
    return {"corrida": corrida, "etiqueta": etiqueta, "escenario": esc.nombre, "repeticion": rep,
            "fecha": datetime.now().isoformat(timespec="seconds"), "duracion_s": round(time.perf_counter() - t0, 2),
            "turnos": len(esc.mensajes), "turnos_respondidos": len(respuestas), "error": error,
            "tiene_restricciones": bool(esc.restricciones), "checks": checks,
            "pedidos_inseguros": pedidos_inseguros, "menciones_inseguras": menciones_inseguras,
            "violacion_seguridad": bool(pedidos_inseguros or menciones_inseguras),
            "platos_recomendados": ctx.platos_mencionados(), "secuencia_herramientas": herramientas,
            "descripcion": esc.descripcion, "mensajes": esc.mensajes,
            "respuestas": [enmascarar(r, 700) for r in respuestas],
            "derivaciones": len(ctx.derivaciones), "pedidos": len(ctx.pedidos)}
