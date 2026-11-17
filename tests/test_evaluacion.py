"""Pruebas del runner de escenarios: la métrica de seguridad se calcula con la regla en código, no a ojo."""
import json

from fakes import ModeloGuion, embeddings, js
from sabores.agente import construir_agente
from sabores.evaluacion import ESCENARIOS, Escenario, ejecutar_corrida
from sabores.evaluacion.escenarios import norm


def _fabrica(respuesta_final: str):
    def fabrica(cliente, sesion, cfg):
        planificador = ModeloGuion(respuestas=[js({"modo": "directo", "motivo": "consulta puntual"}),
                                               respuesta_final])
        ejecutor = ModeloGuion(respuestas=["ok"])
        return construir_agente(cliente, sesion, cfg, llm_planificador=planificador, llm_ejecutor=ejecutor,
                                embeddings=embeddings())
    return fabrica


def _escenario_celiaca():
    return Escenario("celiaca", "prueba", ["Soy celíaca, ¿qué recomiendas?"],
                     restricciones={"alergenos": ["gluten"], "dietas": ["sin_gluten"]},
                     checks={"recomienda_algun_plato": lambda c: len(c.platos_mencionados()) >= 1})


def test_agente_seguro_no_genera_violacion(tmp_path):
    res = ejecutar_corrida("t", [_escenario_celiaca()], tmp_path, _fabrica("Te recomiendo el Bowl de quinoa y garbanzos."),
                           verbose=False)[0]
    assert res["violacion_seguridad"] is False and res["menciones_inseguras"] == []
    assert res["checks"] == {"recomienda_algun_plato": True} and res["error"] is None


def test_recomendar_un_plato_con_gluten_se_detecta(tmp_path):
    res = ejecutar_corrida("t", [_escenario_celiaca()], tmp_path, _fabrica("Prueba el Wrap de pollo y hummus."),
                           verbose=False)[0]
    assert res["violacion_seguridad"] is True and res["menciones_inseguras"] == ["Wrap de pollo y hummus"]


def test_resultados_y_trazas_quedan_etiquetados(tmp_path):
    ejecutar_corrida("baseline", [_escenario_celiaca()], tmp_path, _fabrica("Te recomiendo el Bowl de quinoa y garbanzos."),
                     verbose=False)
    fila = json.loads((tmp_path / "resultados.jsonl").read_text(encoding="utf-8").splitlines()[0])
    spans = [json.loads(x) for x in (tmp_path / "trazas.jsonl").read_text(encoding="utf-8").splitlines()]
    assert fila["etiqueta"] == "baseline" and fila["corrida"].startswith("baseline-")
    assert spans and {s["corrida"] for s in spans} == {fila["corrida"]}
    assert {s["escenario"] for s in spans} == {"celiaca"}


def test_un_error_del_agente_no_corta_la_corrida(tmp_path):
    def fabrica(cliente, sesion, cfg):
        return construir_agente(cliente, sesion, cfg, llm_planificador=ModeloGuion(respuestas=[]),
                                llm_ejecutor=ModeloGuion(respuestas=[]), embeddings=embeddings())

    res = ejecutar_corrida("t", [_escenario_celiaca()], tmp_path, fabrica, verbose=False)[0]
    assert res["error"] and res["turnos_respondidos"] == 0
    assert all(v is False for v in res["checks"].values())


def test_definicion_de_escenarios():
    nombres = [e.nombre for e in ESCENARIOS]
    assert len(nombres) == len(set(nombres)) == 8
    assert next(e for e in ESCENARIOS if e.nombre == "base_celiaca_vegetariana").repeticiones == 3
    assert all(e.checks for e in ESCENARIOS)
    assert norm("Ñoquis de París") == "noquis de paris"
