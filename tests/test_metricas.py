"""Pruebas de las métricas con logs sintéticos de resultado conocido."""
import json

import pytest

from sabores.observabilidad import metricas as m


def _res(escenario, rep, platos, error=None, violacion=False, checks=None, herramientas=("buscar_platos",)):
    return {"corrida": "c1", "etiqueta": "baseline", "escenario": escenario, "repeticion": rep, "error": error,
            "tiene_restricciones": True, "violacion_seguridad": violacion, "checks": checks or {"a": True},
            "platos_recomendados": platos, "secuencia_herramientas": list(herramientas)}


def _escribir(tmp_path, resultados=(), spans=()):
    (tmp_path / "resultados.jsonl").write_text("\n".join(json.dumps(r) for r in resultados), encoding="utf-8")
    (tmp_path / "trazas.jsonl").write_text("\n".join(json.dumps(s) for s in spans), encoding="utf-8")
    return m.cargar(tmp_path)


def _span(trace, sid, padre, tipo, dur, inicio, estado="ok", nombre="x", **atributos):
    return {"session_id": "s", "trace_id": trace, "span_id": sid, "parent_span_id": padre, "cliente": "c",
            "tipo": tipo, "nombre": nombre, "inicio": inicio, "duracion_s": dur, "estado": estado, "error": None,
            "atributos": atributos, "etiqueta": "baseline", "escenario": "esc", "repeticion": 0, "corrida": "c1"}


def test_precision_ignora_ejecuciones_con_error_y_cuenta_violaciones(tmp_path):
    _, res = _escribir(tmp_path, [_res("e", 0, ["A"]), _res("e", 1, ["B"], violacion=True),
                                  _res("e", 2, [], error="429")])
    p = m.precision_seguridad(res)
    assert (p["ejecuciones"], p["violaciones"], p["valor"]) == (2, 1, 0.5)


def test_cumplimiento_excluye_checks_no_evaluables(tmp_path):
    _, res = _escribir(tmp_path, [_res("e", 0, [], checks={"a": True, "b": False}),
                                  _res("e", 1, [], error="x", checks={"a": None, "b": None})])
    t = m.tasa_cumplimiento(res)
    assert (t["checks"], t["cumplidos"], t["valor"]) == (2, 1, 0.5)


def test_consistencia_jaccard_entre_repeticiones(tmp_path):
    _, res = _escribir(tmp_path, [_res("e", 0, ["A", "B"]), _res("e", 1, ["A", "C"]), _res("e", 2, ["A", "B"])])
    c = m.consistencia(res).iloc[0]
    # pares: {A,B}-{A,C}=1/3, {A,B}-{A,B}=1, {A,C}-{A,B}=1/3
    assert c["jaccard_platos"] == pytest.approx((1 / 3 + 1 + 1 / 3) / 3)
    assert c["jaccard_herramientas"] == 1.0


def test_desglose_separa_modelo_espera_herramientas_y_otros(tmp_path):
    spans = [
        _span("t1", "r", None, "turno", 10.0, "2026-01-01T00:00:00.000", n_alertas=1, n_reintentos=2,
              tokens_entrada=100, tokens_salida=50, tokens_razonamiento=25, n_llm=1),
        _span("t1", "l", "r", "llm", 6.0, "2026-01-01T00:00:01.000", latencia_modelo_s=5.0, espera_limitador_s=1.0),
        _span("t1", "h", "r", "herramienta", 2.0, "2026-01-01T00:00:07.000", nombre="buscar_platos"),
    ]
    sp, res = _escribir(tmp_path, spans=spans)
    d = m.desglose_tiempo(sp).iloc[0]
    assert (d["modelo"], d["espera_limitador"], d["herramientas"], d["otros"]) == (5.0, 1.0, 2.0, 2.0)
    assert m.causa_dominante(m.desglose_tiempo(sp)).startswith("modelo")
    u = m.uso_recursos(sp)
    assert u["tokens_total"] == 150 and u["pct_razonamiento_en_salida"] == 0.5
    assert m.frecuencia_errores(res, sp)["reintentos_totales"] == 2
    assert m.latencia_turnos(sp)["p50"] == 10.0


def test_arbol_traza_calcula_profundidad(tmp_path):
    spans = [_span("t1", "r", None, "turno", 5, "2026-01-01T00:00:00.000"),
             _span("t1", "p", "r", "paso", 3, "2026-01-01T00:00:01.000"),
             _span("t1", "l", "p", "llm", 1, "2026-01-01T00:00:02.000")]
    sp, _ = _escribir(tmp_path, spans=spans)
    a = m.arbol_traza(sp, "t1")
    assert list(a["profundidad"]) == [0, 1, 2] and list(a["tipo"]) == ["turno", "paso", "llm"]


def test_sin_logs_no_revienta(tmp_path):
    sp, res = m.cargar(tmp_path)
    assert sp.empty and res.empty
    assert m.precision_seguridad(res)["ejecuciones"] == 0


def test_turnos_con_error_no_cuentan_en_la_latencia(tmp_path):
    spans = [_span("t1", "r1", None, "turno", 10.0, "2026-01-01T00:00:00.000"),
             _span("t2", "r2", None, "turno", 0.5, "2026-01-01T00:01:00.000", estado="error")]
    sp, _ = _escribir(tmp_path, spans=spans)
    assert len(m.turnos(sp)) == 2 and len(m.turnos(sp, completos=True)) == 1
    assert m.latencia_turnos(sp)["p50"] == 10.0


def test_checks_de_ejecuciones_con_error_son_no_evaluables_aunque_el_log_diga_false(tmp_path):
    _, res = _escribir(tmp_path, [_res("e", 0, [], error="429", checks={"a": False}), _res("e", 1, [], checks={"a": True})])
    t = m.tasa_cumplimiento(res)
    assert (t["checks"], t["cumplidos"]) == (1, 1)
